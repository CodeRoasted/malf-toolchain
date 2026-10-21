#!/usr/bin/env python3
"""A published executable's CONSUMER verdict at step 0: the stored bytes judged as a consumer's host would run them.

N360 plan A2(d), DN-142.D20 (3): the gates a release used to run over its own tag-time rebuild of
`sift` become ONE keyed verdict step over the STORED entry, by its entry digest, so the manifest
admits the public `sift-linux-x64` only when this verdict passed over the very bytes it publishes.
Six rows, each `passed`, `failed` or `not-judged`; the verdict is `pass` only when every row
passed, so a probe that could not run is a fail, never a pass:
  libstdcxx-needed    no DT_NEEDED libstdc++: the standalone posture links it statically, and a
                      consumer distro ships no gcc-16 libstdc++ (the v1.9.4 asset could not start);
  cxx-version-needs   no GLIBCXX_/CXXABI_ version need, which would bind a libstdc++ regardless;
  runpath             no DT_RPATH/DT_RUNPATH: a SAME-PRODUCER reading of the store's published
                      predicate (artefact_store.elf_published_defects), never counted as a second
                      producer of it;
  glibc-floor         the highest GLIBC_ version need is at most the consumer baseline's ceiling
                      (the row names the measured floor);
  consumer-container  in the pinned consumer container, with no network and a read-only root,
                      the executable hashes to the judged entry's digest, `--help` runs and a
                      two-log diff names the one added error line as a change and the shared one
                      as none (CONTENT, not a non-empty report);
  container-glibc     the container's own glibc is the ceiling: the two constants name one consumer
                      baseline, so moving one without the other reds instead of drifting.
Every ELF reading is Python over the bytes (PT_DYNAMIC, the dynamic string table, the version
needs), never `readelf | grep`, so no pipe can turn a hit into a miss.

THE ACT RUNS IN THE BUILD ENVIRONMENT, AND ITS CONSUMER CONTAINER IS THE LAUNCHER'S PROBE (DN-142.D30 (11)
R21): `prepare` runs in the attested image, extracts the entry from the store by digest, judges rows A to D
and writes the work directory, its tar and the probe (the consumer image by digest and its script) into the
act's scratch; malf's launcher then runs the probe (`build_environment.py probe`: pulled by digest and
verified, `--network none`, a read-only root, no mount, the tar on its stdin) and captures its exit and
both streams there; `conclude` runs in the image, judges rows E and F from that capture and stores the
verdict, its `system` member `{image: <the pinned config digest>}` and its `toolchain` the one the seam
measured in that image. No `docker` argv is built here. A bind mount's source would resolve in dockerd's
mount namespace, which on the release runner holds a private /tmp (step 0 run 38296956741: `exit 127:
sh: 1: ./sift: not found`); the stdin stream reaches the container from any namespace, and the
in-container digest check reds a container that ran anything else.

THE CONSUMER BASELINE IS THE PRODUCT'S DECLARATION, read at the commit step 0 recorded from
`<package path>/consumer/` of the package's repository: `baseline.json` (`container`, an image
reference pinned by digest; `glibc_ceiling`; `smoke`: the two log files, the line that must be a
change and the line that must not) and the two logs. Its git tree id and the container reference
are key members, so a new baseline re-keys the verdict.

    consumer_verdict.py prepare <store> <steps.json> <package> <entry> <repository dir> <commit> <package path> <scratch>
        in the build environment: rows A to D, the work directory, its tar and the probe written under <scratch>;
        exit 0 prepared, 1 refused (nothing is written for the launcher to run)
    consumer_verdict.py conclude <store> <scratch> <member.json>
        in the build environment, after the launcher's probe: rows E and F from its capture, the verdict
        stored (output `consumer`, step id `<package>/consumer`) under the `system` and `toolchain` the
        member file names, the system refused unless it is this container's MALF_SYSTEM; exit 0 when it
        passed and agrees with its record, 1 on a fail, a mismatch or a refusal
    consumer_verdict.py selftest
        every row red on a fixture executable built here, the container rows over fixture captures

Exit 0 pass, 1 fail or refused, 2 a usage error.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_manifest  # noqa: E402 — a sibling module, located from this file

NAME = "consumer"
ACT_ARGV = ["consumer_verdict.py", "prepare", "conclude"]
BASELINE_DIR = "consumer"
BASELINE_FILE = "baseline.json"
ROWS = ("libstdcxx-needed", "cxx-version-needs", "runpath", "glibc-floor", "consumer-container",
        "container-glibc")
PASSED, FAILED, NOT_JUDGED = "passed", "failed", "not-judged"
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_NEEDED, DT_STRTAB, DT_RPATH, DT_RUNPATH = 0, 1, 5, 15, 29
DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFE, 0x6FFFFFFF
GLIBC_NEED = re.compile(r"^GLIBC_(\d+(?:\.\d+)*)$")
CONTAINER_REF = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")
# `docker run`'s own exit when the container could not be started: the probe did not run.
DOCKER_UNREACHABLE = 125
# The files of the act's scratch: what `prepare` writes, what the launcher's probe reads and captures,
# and what `conclude` reads back.
WORK, WORK_TAR, PROBE, PREPARED, CAPTURE = "consumer", "work.tar", "probe.json", "prepared.json", "capture.json"
# The line the probe prints between the container's glibc reading and the executable's report.
REPORT_MARK = "consumer-verdict: report"
SYSTEM_ENV = artefact_store.SYSTEM_ENV


class Refused(Exception):
    """The verdict cannot be judged; the message names why, and nothing is stored."""


@dataclass(frozen=True)
class Dynamic:
    needed: tuple[str, ...]
    version_needs: tuple[str, ...]
    runpath: bool


def dynamic(data: bytes) -> Dynamic:
    """The dynamic section of a linked ELF file: its NEEDED names, the version names it needs, and
    whether it carries a runpath. Raises Refused for anything that is not a dynamically linked ELF."""
    if not data.startswith(artefact_store.ELF_MAGIC) or len(data) < 64:
        raise Refused("not an ELF file")
    wide, order = data[4] == 2, "<" if data[5] == 1 else ">"
    word = "Q" if wide else "I"
    if wide:
        phoff = struct.unpack_from(order + "Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from(order + "HH", data, 54)
    else:
        phoff = struct.unpack_from(order + "I", data, 28)[0]
        phentsize, phnum = struct.unpack_from(order + "HH", data, 42)
    loads, dyn = [], None
    for index in range(phnum):
        base = phoff + index * phentsize
        kind = struct.unpack_from(order + "I", data, base)[0]
        if wide:
            offset, vaddr = struct.unpack_from(order + "QQ", data, base + 8)
            filesz = struct.unpack_from(order + "Q", data, base + 32)[0]
        else:
            offset, vaddr = struct.unpack_from(order + "II", data, base + 4)
            filesz = struct.unpack_from(order + "I", data, base + 16)[0]
        if kind == PT_LOAD:
            loads.append((vaddr, offset, filesz))
        elif kind == PT_DYNAMIC:
            dyn = (offset, filesz)
    if dyn is None:
        raise Refused("no PT_DYNAMIC segment: not a dynamically linked executable")

    def at(address: int) -> int:
        for vaddr, offset, filesz in loads:
            if vaddr <= address < vaddr + filesz:
                return offset + address - vaddr
        raise Refused(f"the dynamic section names address {address:#x}, which no PT_LOAD maps")

    entry = 16 if wide else 8
    tags: list[tuple[int, int]] = []
    for cursor in range(dyn[0], dyn[0] + dyn[1] - entry + 1, entry):
        tag, value = struct.unpack_from(order + word + word, data, cursor)
        if tag == DT_NULL:
            break
        tags.append((tag, value))
    values = dict(tags)
    if DT_STRTAB not in values:
        raise Refused("the dynamic section names no string table")
    strtab = at(values[DT_STRTAB])

    def string(offset: int) -> str:
        end = data.index(b"\0", strtab + offset)
        return data[strtab + offset:end].decode("ascii", "replace")

    needed = tuple(string(value) for tag, value in tags if tag == DT_NEEDED)
    names: list[str] = []
    if DT_VERNEED in values:
        cursor = at(values[DT_VERNEED])
        for _file in range(values.get(DT_VERNEEDNUM, 0)):
            _version, count, _name, aux, following = struct.unpack_from(order + "HHIII", data, cursor)
            aux_at = cursor + aux
            for _entry in range(count):
                _hash, _flags, _other, name, aux_next = struct.unpack_from(order + "IHHII", data, aux_at)
                names.append(string(name))
                aux_at += aux_next
            if not following:
                break
            cursor += following
    return Dynamic(needed, tuple(sorted(set(names))), any(tag in (DT_RPATH, DT_RUNPATH) for tag, _ in tags))


def numeric(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def byte_rows(data: bytes, ceiling: str) -> list[list[str]]:
    """Rows A to D over the executable's bytes, each `[row, outcome, detail]`."""
    try:
        seen = dynamic(data)
    except Refused as refused:
        return [[row, NOT_JUDGED, str(refused)] for row in ROWS[:4]]
    stdcxx = [name for name in seen.needed if name.startswith("libstdc++")]
    cxx = [name for name in seen.version_needs if name.startswith(("GLIBCXX_", "CXXABI_"))]
    glibc = sorted((numeric(found.group(1)), found.group(1)) for name in seen.version_needs
                   if (found := GLIBC_NEED.match(name)))
    rows = [["libstdcxx-needed", FAILED if stdcxx else PASSED, ", ".join(stdcxx) or "none"],
            ["cxx-version-needs", FAILED if cxx else PASSED, ", ".join(cxx) or "none"],
            ["runpath", FAILED if seen.runpath else PASSED, "present" if seen.runpath else "none"]]
    if not glibc:
        libc = any(name.startswith("libc.so") for name in seen.needed)
        rows.append(["glibc-floor", NOT_JUDGED if libc else PASSED,
                     "libc NEEDED and no GLIBC_ version need: the parse broke" if libc else "no libc: static"])
    else:
        floor = glibc[-1][1]
        rows.append(["glibc-floor", PASSED if glibc[-1][0] <= numeric(ceiling) else FAILED,
                     f"floor {floor}, ceiling {ceiling}"])
    return rows


def work_stream(work: Path) -> bytes:
    """Every file of `work` as an uncompressed tar, modes kept, owned by root."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(work.iterdir()):
            info = bundle.gettarinfo(str(path), arcname=path.name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            with path.open("rb") as handle:
                bundle.addfile(info, handle)
    return buffer.getvalue()


def probe_script(executable: str, digest: str, smoke: dict) -> str:
    """The consumer container's one script: its glibc's version line, the mark, then the work directory
    unpacked from stdin, the executable hashed against the judged digest, `--help` and the two-log diff."""
    return (f"ldd --version 2>&1 | head -n 1; echo '{REPORT_MARK}'; "
            f"tar -x -f - && echo '{digest}  {executable}' | sha256sum -c --quiet - && "
            f"./{executable} --help >/dev/null && ./{executable} {smoke['baseline']} {smoke['changed']} "
            f"--format json -o /tmp/report.json --transport none >/dev/null && cat /tmp/report.json")


def container_rows(capture: dict, baseline: dict) -> list[list[str]]:
    """Rows E and the consistency arm from the probe's capture, each `[row, outcome, detail]`: a probe
    that could not run (docker's own exit, or no mark printed) is NOT JUDGED in both, never passed."""
    code, out, err = capture["code"], capture["stdout"], capture["stderr"]
    smoke, ceiling = baseline["smoke"], baseline["glibc_ceiling"]
    glibc, marked, report = out.partition(REPORT_MARK + "\n")
    if code == DOCKER_UNREACHABLE or not marked:
        why = f"the probe could not run {capture['image']}: exit {code}: {(err or out).strip()[-200:]}"
        return [["container-glibc", NOT_JUDGED, why], ["consumer-container", NOT_JUDGED, why]]
    first = (glibc.splitlines() or [""])[0].split()
    found = first[-1] if first else ""
    rows = [["container-glibc", PASSED if found == ceiling else FAILED,
             f"the container's glibc is {found or 'unread'}, the ceiling {ceiling}"]]
    if code != 0:
        return [*rows, ["consumer-container", FAILED, f"exit {code}: {(err or report).strip()[-200:]}"]]
    try:
        changes = json.loads(report).get("ranked_changes") or []
    except ValueError:
        return [*rows, ["consumer-container", FAILED, "the report is not JSON"]]
    named = [change.get("summary", "") for change in changes]
    added = [summary for summary in named if smoke["added"] in summary]
    kept = [summary for summary in named if smoke["unchanged"] in summary]
    return [*rows, ["consumer-container", PASSED if len(added) == 1 and not kept and len(named) == 1 else FAILED,
                    f"{len(named)} ranked change(s); `{smoke['added']}` named {len(added)}x, "
                    f"`{smoke['unchanged']}` named {len(kept)}x"]]


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repository), *args], capture_output=True, check=False)


def baseline_at(repository: Path, commit: str, package_path: str, into: Path) -> tuple[dict, str]:
    """(the baseline, the git tree id of its directory), its files written under `into`."""
    directory = f"{package_path}/{BASELINE_DIR}".lstrip("./") if package_path != "." else BASELINE_DIR
    tree = _git(repository, "rev-parse", f"{commit}:{directory}")
    shown = _git(repository, "show", f"{commit}:{directory}/{BASELINE_FILE}")
    if tree.returncode != 0 or shown.returncode != 0:
        raise Refused(f"{repository.name} declares no consumer baseline at {commit}:{directory}/{BASELINE_FILE}")
    baseline = json.loads(shown.stdout)
    smoke = baseline.get("smoke") or {}
    if not CONTAINER_REF.match(str(baseline.get("container", ""))) or \
            not re.fullmatch(r"\d+(\.\d+)*", str(baseline.get("glibc_ceiling", ""))) or \
            not {"baseline", "changed", "added", "unchanged"} <= set(smoke):
        raise Refused(f"{directory}/{BASELINE_FILE} is not {{container: <ref>@sha256:<digest>, glibc_ceiling, "
                      f"smoke: {{baseline, changed, added, unchanged}}}}")
    for name in (smoke["baseline"], smoke["changed"]):
        if "/" in name:
            raise Refused(f"the smoke log {name!r} is not a file of {directory}")
        log = _git(repository, "show", f"{commit}:{directory}/{name}")
        if log.returncode != 0:
            raise Refused(f"{directory} holds no smoke log {name} at {commit}")
        (into / name).write_bytes(log.stdout)
    return baseline, tree.stdout.decode().strip()


def prepare(store: Path, steps: list[dict], package: str, entry: str, repository: Path, commit: str,
            package_path: str, scratch: Path) -> int:
    """In the build environment: the stored entry extracted by digest, rows A to D judged, and the work
    directory, its tar, the probe and the prepared state written under `scratch`. raises Refused."""
    step = {row["id"]: row for row in steps}.get(f"{package}/package")
    record = artefact_store.LocalStore(store).record(step["key"]) if step else None
    if record is None:
        raise Refused(f"step 0 stored no {package} package in this run")
    output = record["outputs"]["package"]
    held = {path: digest for path, _mode, digest in output["entries"]}
    if entry not in held:
        raise Refused(f"{package} holds no entry {entry}")
    work = scratch / WORK
    work.mkdir(parents=True)
    executable = Path(entry).name
    try:
        store_manifest.extract(store / "objects" / output["transport"], entry, held[entry], work / executable)
    except store_manifest.Refused as refused:
        raise Refused(str(refused)) from refused
    (work / executable).chmod(0o755)
    baseline, tree = baseline_at(repository, commit, package_path, work)
    rows = byte_rows((work / executable).read_bytes(), baseline["glibc_ceiling"])
    (scratch / WORK_TAR).write_bytes(work_stream(work))
    (scratch / PROBE).write_text(json.dumps({"image": baseline["container"], "argv": [
        "sh", "-c", probe_script(executable, held[entry], baseline["smoke"])]}, sort_keys=True))
    (scratch / PREPARED).write_text(json.dumps({"package": package, "entry": entry, "digest": held[entry],
                                                "baseline": baseline, "tree": tree, "rows": rows}, sort_keys=True))
    print(f"consumer_verdict: PREPARED {package} {entry} — the probe {baseline['container']} is the launcher's")
    return 0


def _read(path: Path, what: str) -> dict:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unread:
        raise Refused(f"{what} {path} could not be read: {unread}") from unread
    if not isinstance(held, dict):
        raise Refused(f"{what} {path} is not a JSON object")
    return held


def act_member(member: dict, environ: dict) -> tuple[dict, dict]:
    """(the `system`, the `toolchain`) the member file names: the system this container's own
    (`artefact_store.system_member`: MALF_SYSTEM, the pinned config digest, `{image: <pin>}`), the
    toolchain a non-empty measurement. raises Refused."""
    system, toolchain = member.get("system"), member.get("toolchain")
    try:
        own = artefact_store.system_member(environ)
    except artefact_store.Refused as refused:
        raise Refused(str(refused)) from refused
    if system != own:
        raise Refused(f"the member names the system {system!r}, and this container runs {own!r}")
    if not isinstance(toolchain, dict) or not toolchain:
        raise Refused(f"the member names the toolchain {toolchain!r}, not a measurement of the image "
                      f"(DN-142.D30 (10) R15)")
    return system, toolchain


def conclude(store: Path, scratch: Path, member: dict, environ: dict) -> int:
    """In the build environment, after the probe: rows E and F from its capture, the verdict stored under
    the member's system and toolchain; 0 when it passed (and matches its record). raises Refused."""
    system, toolchain = act_member(member, environ)
    prepared, capture = _read(scratch / PREPARED, "the prepared state"), _read(scratch / CAPTURE, "the probe's capture")
    baseline = prepared["baseline"]
    if capture.get("image") != baseline["container"]:
        raise Refused(f"the probe ran {capture.get('image')!r}, and the baseline names {baseline['container']}")
    rows = prepared["rows"] + container_rows(capture, baseline)
    rows.sort(key=lambda row: ROWS.index(row[0]))
    verdict = "pass" if all(row[1] == PASSED for row in rows) else "fail"
    definition = artefact_store.definition_member(Path(__file__).resolve().parent,
                                                  artefact_store.parameter_document(ACT_ARGV, None))
    entry, package = prepared["entry"], prepared["package"]
    document = {
        "step": {"kind": "verdict", "id": NAME, "subject": package,
                 "predicate": {"id": "malf consumer_verdict.py", "version": definition["malf"]},
                 "params": {"glibc_ceiling": baseline["glibc_ceiling"], "entry": entry}},
        "definition": definition,
        "judged": {entry: prepared["digest"]},
        "data": {"container": baseline["container"], "baseline": prepared["tree"]},
        "toolchain": toolchain,
        "system": system,
        "context": {},
    }
    findings = [f"{row[0]}: {row[1]}" for row in rows if row[1] != PASSED]
    stored = artefact_store.judged_verdict(store, document, {"verdict": verdict, "findings": findings,
                                                             "entries": rows, "record": {}}, {}, name=NAME)
    for row in rows:
        print(f"consumer_verdict: {row[1].upper():<10} {row[0]} — {row[2]}")
    return 0 if stored == 0 and verdict == "pass" else 1


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────


def _built(root: Path, name: str, source: str, compiler: str, *flags: str) -> bytes:
    suffix = ".cpp" if compiler == "c++" else ".c"
    (root / f"{name}{suffix}").write_text(source)
    subprocess.run([compiler, "-O1", "-o", str(root / name), str(root / f"{name}{suffix}"), *flags], check=True)
    return (root / name).read_bytes()


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, got: object, want: object) -> None:
        armed[0] += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def outcomes(rows: list[list[str]]) -> dict[str, str]:
        return {row[0]: row[1] for row in rows}

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        c_source = '#include <stdio.h>\nint main(void) { puts("x"); return 0; }\n'
        green = _built(root, "green", c_source, "cc")
        arm("a C executable linking libc alone passes every byte row, its floor named",
            (outcomes(byte_rows(green, "2.39")), byte_rows(green, "2.39")[3][2].startswith("floor 2.")),
            ({"libstdcxx-needed": PASSED, "cxx-version-needs": PASSED, "runpath": PASSED,
              "glibc-floor": PASSED}, True))
        runpath = _built(root, "runpath", c_source, "cc", "-Wl,-rpath,/opt/gcc-16.2/lib64",
                         "-Wl,--enable-new-dtags")
        arm("RED: a RUNPATH reds the runpath row (store-d16's sift, N360 plan F1)",
            outcomes(byte_rows(runpath, "2.39"))["runpath"], FAILED)
        rpath = _built(root, "rpath", c_source, "cc", "-Wl,-rpath,/opt/x", "-Wl,--disable-new-dtags")
        arm("RED: a DT_RPATH reds the runpath row too", outcomes(byte_rows(rpath, "2.39"))["runpath"], FAILED)
        cxx = _built(root, "cxx", '#include <iostream>\nint main() { std::cout << "x\\n"; }\n', "c++")
        arm("RED: a C++ executable linking libstdc++ dynamically reds rows A and B",
            [outcomes(byte_rows(cxx, "2.39"))[row] for row in ("libstdcxx-needed", "cxx-version-needs")],
            [FAILED, FAILED])
        patched = green.replace(b"GLIBC_2.34", b"GLIBC_2.99")
        arm("RED: a version need of GLIBC_2.99 reds the floor row naming 2.99",
            (patched != green, outcomes(byte_rows(patched, "2.39"))["glibc-floor"],
             "2.99" in byte_rows(patched, "2.39")[3][2]), (True, FAILED, True))
        arm("RED: the same floor against a ceiling it exceeds by a minor only reds",
            outcomes(byte_rows(green, "2.33"))["glibc-floor"], FAILED)
        arm("NOT JUDGED: bytes that are not an ELF file are never a pass",
            set(outcomes(byte_rows(b"#!/bin/sh\n", "2.39")).values()), {NOT_JUDGED})

        baseline = {"container": "ubuntu:24.04@sha256:" + "5" * 64, "glibc_ceiling": "2.39",
                    "smoke": {"baseline": "base.log", "changed": "changed.log",
                              "added": "ERROR disk full node-7", "unchanged": "ERROR conn refused db-1"}}
        report = json.dumps({"ranked_changes": [{"summary": 'New error pattern: "ERROR disk full node-7"'}]})
        glibc = "ldd (Ubuntu GLIBC 2.39-0ubuntu8.6) 2.39\n"

        def probed(code: int = 0, out: str = report, glibc_line: str = glibc, err: str = "",
                   marked: bool = True) -> dict:
            """The launcher's capture of the probe: its glibc line, the mark, then the executable's report."""
            return {"image": baseline["container"], "code": code, "stderr": err,
                    "stdout": glibc_line + (REPORT_MARK + "\n" if marked else "") + out}

        arm("the probe's capture names the added line once and the shared one never, and its glibc is the ceiling",
            outcomes(container_rows(probed(), baseline)), {"container-glibc": PASSED, "consumer-container": PASSED})
        script = probe_script("sift", "a" * 64, baseline["smoke"])
        arm("the probe's script prints the glibc line and the mark, then hashes the unpacked executable against "
            "the judged digest before running it",
            (script.index("ldd --version") < script.index(REPORT_MARK) < script.index("tar -x -f -")
             < script.index(f"echo '{'a' * 64}  sift' | sha256sum -c") < script.index("./sift --help")), True)
        arm("RED: a container holding other bytes than the judged entry reds row E (the script's digest check exits 1)",
            outcomes(container_rows(probed(1, "", err="sha256sum: WARNING: 1 computed checksum did NOT match"),
                                    baseline))["consumer-container"], FAILED)
        arm("RED: an executable exiting 1 in the container reds row E",
            outcomes(container_rows(probed(1, ""), baseline))["consumer-container"], FAILED)
        arm("NOT JUDGED: a probe that could not run (docker's own exit 125) is never a pass, in either row",
            set(outcomes(container_rows(probed(DOCKER_UNREACHABLE, "", "", "no such image", marked=False),
                                        baseline)).values()), {NOT_JUDGED})
        arm("NOT JUDGED: a probe whose script never reached the mark (no shell, exit 127) is never a pass",
            set(outcomes(container_rows(probed(127, "", "", "exec: sh: not found", marked=False),
                                        baseline)).values()), {NOT_JUDGED})
        arm("RED: a report that does not name the added line reds row E (the content, not a non-empty file)",
            outcomes(container_rows(probed(out=json.dumps({"ranked_changes": [{"summary": "x"}]})), baseline))
            ["consumer-container"], FAILED)
        arm("RED: a report that names the shared line as a change reds row E",
            outcomes(container_rows(probed(out=json.dumps({"ranked_changes": [
                {"summary": "ERROR disk full node-7"}, {"summary": "ERROR conn refused db-1"}]})), baseline))
            ["consumer-container"], FAILED)
        arm("RED: a ceiling the container's glibc is not reds the consistency row",
            outcomes(container_rows(probed(), {**baseline, "glibc_ceiling": "2.40"}))["container-glibc"], FAILED)

        pin = json.loads(artefact_store.ENVIRONMENT_PIN.read_text(encoding="utf-8"))["image"]
        measured = {"python3": {"version": "3.12.3", "executable": "1" * 64}}
        member = {"system": {"image": pin}, "toolchain": measured}
        act = root / "act"
        act.mkdir()
        (act / PREPARED).write_text(json.dumps({
            "package": "tools", "entry": "bin/sift", "digest": "a" * 64, "baseline": baseline, "tree": "b" * 40,
            "rows": byte_rows(green, "2.39")}))
        (act / CAPTURE).write_text(json.dumps(probed()))
        store = root / "store"
        concluded = conclude(store, act, member, {SYSTEM_ENV: pin})
        records = [json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))]
        arm("RED-FIRST (DN-142.D30 (11) R21): the consumer verdict's record attests {image: <pin>} and the toolchain "
            "the seam measured in that image",
            (concluded, [(record["inputs"]["system"], record["inputs"]["toolchain"],
                          record["outputs"][NAME]["verdict"]) for record in records]),
            (0, [({"image": pin}, measured, "pass")]))

        def refused(call) -> str:
            try:
                call()
            except Refused as refusal:
                return str(refusal)
            return "no refusal"

        arm("RED: a conclude run outside the build environment (no MALF_SYSTEM) is refused a system member",
            refused(lambda: act_member(member, {})).startswith("no system member outside the build environment: "
                                                              "MALF_SYSTEM is ''"), True)
        arm("RED: a container whose MALF_SYSTEM is another image than the pin is refused",
            "the act ran in another image" in refused(lambda: act_member(member, {SYSTEM_ENV: "sha256:" + "8" * 64})),
            True)
        arm("RED: a member naming another image than the container's own is refused",
            refused(lambda: act_member({**member, "system": {"image": "host"}}, {SYSTEM_ENV: pin})).startswith(
                "the member names the system {'image': 'host'}"), True)
        arm("RED: a member carrying no measured toolchain is refused",
            refused(lambda: act_member({**member, "toolchain": {}}, {SYSTEM_ENV: pin})).startswith(
                "the member names the toolchain {}"), True)
        (act / CAPTURE).write_text(json.dumps({**probed(), "image": "ubuntu:22.04@sha256:" + "4" * 64}))
        arm("RED: a capture of another image than the baseline's is refused, nothing stored",
            (refused(lambda: conclude(root / "other-store", act, member, {SYSTEM_ENV: pin})).startswith(
                "the probe ran 'ubuntu:22.04@sha256:"), (root / "other-store" / "records").is_dir()), (True, False))
        judging = [node for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                   if isinstance(node, ast.FunctionDef) and node.name != "selftest"]
        built = sorted({node.value for function in judging for node in ast.walk(function)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and Path(node.value.split(" ")[0]).name == "docker"})
        arm("RED: no `docker` argv is built here: the consumer container is the launcher's probe (DN-142.D30 (11) R21)",
            built, [])

    for failure in failures:
        print(f"consumer_verdict selftest FAILED: {failure}")
    print(f"consumer_verdict selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 9 and argv[0] == "prepare":
            return prepare(Path(argv[1]), json.loads(Path(argv[2]).read_text()), argv[3], argv[4], Path(argv[5]),
                           argv[6], argv[7], Path(argv[8]))
        if len(argv) == 4 and argv[0] == "conclude":
            return conclude(Path(argv[1]), Path(argv[2]), _read(Path(argv[3]), "the act's member"), dict(os.environ))
    except Refused as refused:
        print(f"consumer_verdict: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
