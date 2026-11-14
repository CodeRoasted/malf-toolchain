#!/usr/bin/env python3
"""One name at more than one version in ``conan.lock``: the one reader of that fact.

Why this exists
---------------
``ADR-4.D4``: one name = ONE version in a linked closure. A lockfile may legally carry a name at
two versions (two independent roots); a binary may not, and one image holding two ABIs of one
library errors nowhere and logs nothing. ``malf lock`` reports the lockfile's split as a NOTE, the
finding instrument the ADR keeps. On 2026-11-14 the 1.10.13 bump's ``lock --update`` resolved
clickhouse-cpp's lz4 range and libcurl's zlib range above exact pins that still held the older
versions, so the lock carried lz4 and zlib twice each; the NOTE printed, and nothing refused it.

This module is where the split is COMPUTED, once, for both readers:

* ``summary LOCK`` is ``malf lock``'s report: the counts and, on a split, the NOTE. It never fails:
  the lockfile is legal (ADR-4.D4).
* ``refuse LOCK`` is the bump's verdict (``pharos bump`` S2, the cut-head rebaseline whose lock a
  release is built from): any THIRD-PARTY library pinned at more than one version in the host
  context (``requires``, the link closure) exits 1 and names each one. A release built from that
  lock is the binary ADR-4.D4 forbids, unless a reader proves which roots never link together, and
  nothing in the lock says so.

The two readers differ only in WHICH entries they hand ``splits`` and what they do with the answer,
so the NOTE and the refusal can never disagree about what a split is.

An entry is ``name/version#revision%timestamp``; third-party entries carry a recipe revision
(``#``), first-party ones float without one. ``build_requires`` are tools, outside the link closure:
the NOTE reads them, the refusal does not.

    python3 lock_versions.py summary conan.lock
    python3 lock_versions.py refuse conan.lock
    python3 lock_versions.py selftest
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable


def splits(entries: Iterable[str]) -> dict[str, list[str]]:
    """Each name the entries carry at more than one version, with its `name/version` spellings."""
    seen: dict[str, set[str]] = {}
    for entry in entries:
        seen.setdefault(entry.split("/")[0], set()).add(entry.split("#")[0])
    return {name: sorted(versions) for name, versions in sorted(seen.items()) if len(versions) > 1}


def third_party_host_splits(lock: dict) -> dict[str, list[str]]:
    """The splits the bump refuses: third-party (`#`-pinned) entries of the host context."""
    return splits(entry for entry in lock.get("requires", []) if "#" in entry)


def summary(lock: dict) -> list[str]:
    requires = lock.get("requires", [])
    pinned = [entry for entry in requires if "#" in entry]
    lines = [
        f"    requires        {len(requires):>3}  ({len(pinned)} third-party pinned, "
        f"{len(requires) - len(pinned)} first-party floating)",
        f"    build_requires  {len(lock.get('build_requires', [])):>3}  (all third-party pinned)",
    ]
    multi = splits(requires + lock.get("build_requires", []))
    if multi:
        lines.append("    NOTE — pinned at MULTIPLE versions (two roots disagree; a link-time skew "
                     "hazard, not a lockfile error — `pharos bump` refuses it, ADR-4.D4):")
        lines += [f"      {name}: {', '.join(versions)}" for name, versions in multi.items()]
    return lines


def refusal(lock: dict, where: str) -> list[str]:
    """The refusal's lines, or [] when no third-party library is split."""
    multi = third_party_host_splits(lock)
    if not multi:
        return []
    lines = [f"lock_versions: {where} pins {len(multi)} third-party librar"
             f"{'y' if len(multi) == 1 else 'ies'} at more than one version (ADR-4.D4: one name = "
             f"ONE version in a linked closure):"]
    lines += [f"  {name}: {', '.join(versions)}" for name, versions in multi.items()]
    lines.append("  unify UP, onto the version the newer consumer already resolved, with "
                 "`override=True` in the recipe where the conflict is decided, then `malf lock "
                 "--update` again")
    return lines


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def selftest() -> int:
    one = {"requires": ["zlib/1.4#f51a%1", "lz4/1.11.1#aa%2", "insight_canon/1.10.13"],
           "build_requires": ["cmake/3.31.0#c0%3"]}
    # The 2026-11-14 shape: a range resolved above an exact pin, the name twice in `requires`.
    split = dict(one, requires=one["requires"] + ["zlib/1.3.2#9e%4", "lz4/1.10.0#bb%5"])
    # A tool at two versions (build context) and a first-party float: not a library in the link
    # closure — the NOTE reads them, the refusal does not.
    tools = dict(one, build_requires=["cmake/3.31.0#c0%3", "cmake/3.30.1#c1%6"],
                 requires=one["requires"] + ["insight_canon/1.10.12"])
    checks = [
        ("one version each: no refusal", refusal(one, "x") == []),
        ("one version each: no NOTE", not any("NOTE" in line for line in summary(one))),
        ("the lz4/zlib split: refused", refusal(split, "x")[1:3] == [
            "  lz4: lz4/1.10.0, lz4/1.11.1", "  zlib: zlib/1.3.2, zlib/1.4"]),
        ("the lz4/zlib split: the NOTE names the same pair",
         [line for line in summary(split) if line.startswith("      ")] == [
             "      lz4: lz4/1.10.0, lz4/1.11.1", "      zlib: zlib/1.3.2, zlib/1.4"]),
        ("a split tool or first-party float: noted, not refused",
         refusal(tools, "x") == [] and any("NOTE" in line for line in summary(tools))),
    ]
    failed = [name for name, held in checks if not held]
    for name, held in checks:
        print(f"  {'ok  ' if held else 'FAIL'}  {name}")
    print(f"lock_versions selftest: {len(checks) - len(failed)}/{len(checks)}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    if len(argv) == 2 and argv[0] == "summary":
        print("\n".join(summary(_read(argv[1]))))
        return 0
    if len(argv) == 2 and argv[0] == "refuse":
        lines = refusal(_read(argv[1]), argv[1])
        if lines:
            print("\n".join(lines), file=sys.stderr)
            return 1
        print(f"lock_versions: {argv[1]} pins every third-party library at one version")
        return 0
    print("usage: lock_versions.py summary LOCK | refuse LOCK | selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
