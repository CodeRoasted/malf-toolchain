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

THE BYTES REACH THE CONTAINER AS A TAR ON ITS STDIN, NEVER A BIND MOUNT. A bind mount's source is
resolved in dockerd's mount namespace, not the caller's: on the release runner the runner and its
rootless dockerd each hold a PRIVATE /tmp (malf runner/isolate-runner-wsl.sh, sandbox_lines), so a
mount of the scratch directory showed the container an empty directory dockerd made in its own
/tmp, and the row read `exit 127: sh: 1: ./sift: not found` (step 0 run 38296956741). The stream
goes through the daemon's API, so the container reads what the caller holds in any namespace, and
the in-container digest check reds a container that ran anything else.

THE CONSUMER BASELINE IS THE PRODUCT'S DECLARATION, read at the commit step 0 recorded from
`<package path>/consumer/` of the package's repository: `baseline.json` (`container`, an image
reference pinned by digest; `glibc_ceiling`; `smoke`: the two log files, the line that must be a
change and the line that must not) and the two logs. Its git tree id and the container reference
are key members, so a new baseline re-keys the verdict.

    consumer_verdict.py judge <store> <steps.json> <package> <entry> <repository dir> <commit> <package path> <scratch>
        stores the verdict record (output `consumer`, step id `<package>/consumer`); exit 0 when it
        passed and agrees with its record, 1 on a fail or a mismatch
    consumer_verdict.py selftest
        every row red on a fixture executable built here, the container rows over a fake runner

Exit 0 pass, 1 fail or refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_manifest  # noqa: E402 — a sibling module, located from this file

NAME = "consumer"
JUDGE_ARGV = ["consumer_verdict.py", "judge"]
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
DOCKER_UNREACHABLE = 125
WORK_MOUNT = "/w"
WORK_HEADROOM = 1 << 20

Runner = Callable[[list[str], bytes], "tuple[int, str, str]"]


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


def docker_runner(argv: list[str], stdin: bytes) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, capture_output=True, input=stdin, check=False)
    except FileNotFoundError as missing:
        return DOCKER_UNREACHABLE, "", str(missing)
    return (done.returncode, done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace"))


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


def container_rows(work: Path, executable: str, digest: str, baseline: dict, runner: Runner) -> list[list[str]]:
    """Rows E and the consistency arm, each `[row, outcome, detail]`; a docker that cannot run the
    image is NOT JUDGED, never passed."""
    image, smoke = baseline["container"], baseline["smoke"]
    rows = []
    code, out, err = runner(["docker", "run", "--rm", "--network", "none", image, "ldd", "--version"], b"")
    if code == DOCKER_UNREACHABLE:
        rows.append(["container-glibc", NOT_JUDGED, f"docker could not run {image}: {err.strip()[-200:]}"])
    else:
        first = (out.splitlines() or [""])[0].split()
        found = first[-1] if first else ""
        rows.append(["container-glibc", PASSED if code == 0 and found == baseline["glibc_ceiling"] else FAILED,
                     f"the container's glibc is {found or 'unread'}, the ceiling {baseline['glibc_ceiling']}"])
    stream = work_stream(work)
    script = (f"tar -x -f - && echo '{digest}  {executable}' | sha256sum -c --quiet - && "
              f"./{executable} --help >/dev/null && ./{executable} {smoke['baseline']} {smoke['changed']} "
              f"--format json -o /tmp/report.json --transport none >/dev/null && cat /tmp/report.json")
    code, out, err = runner(["docker", "run", "-i", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                             "--tmpfs", f"{WORK_MOUNT}:rw,exec,size={2 * len(stream) + WORK_HEADROOM}",
                             "-w", WORK_MOUNT, image, "sh", "-c", script], stream)
    if code == DOCKER_UNREACHABLE:
        rows.append(["consumer-container", NOT_JUDGED, f"docker could not run {image}: {err.strip()[-200:]}"])
        return rows
    if code != 0:
        rows.append(["consumer-container", FAILED, f"exit {code}: {(err or out).strip()[-200:]}"])
        return rows
    try:
        changes = json.loads(out).get("ranked_changes") or []
    except ValueError:
        rows.append(["consumer-container", FAILED, "the report is not JSON"])
        return rows
    named = [change.get("summary", "") for change in changes]
    added = [summary for summary in named if smoke["added"] in summary]
    kept = [summary for summary in named if smoke["unchanged"] in summary]
    rows.append(["consumer-container", PASSED if len(added) == 1 and not kept and len(named) == 1 else FAILED,
                 f"{len(named)} ranked change(s); `{smoke['added']}` named {len(added)}x, "
                 f"`{smoke['unchanged']}` named {len(kept)}x"])
    return rows


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


def judge(store: Path, steps: list[dict], package: str, entry: str, repository: Path, commit: str,
          package_path: str, scratch: Path, runner: Runner = docker_runner) -> int:
    """Judge the stored entry and store the verdict; 0 when it passed (and matches its record)."""
    step = {row["id"]: row for row in steps}.get(f"{package}/package")
    record = artefact_store.LocalStore(store).record(step["key"]) if step else None
    if record is None:
        raise Refused(f"step 0 stored no {package} package in this run")
    output = record["outputs"]["package"]
    held = {path: digest for path, _mode, digest in output["entries"]}
    if entry not in held:
        raise Refused(f"{package} holds no entry {entry}")
    work = scratch / "consumer"
    work.mkdir(parents=True)
    executable = Path(entry).name
    try:
        store_manifest.extract(store / "objects" / output["transport"], entry, held[entry], work / executable)
    except store_manifest.Refused as refused:
        raise Refused(str(refused)) from refused
    (work / executable).chmod(0o755)
    baseline, tree = baseline_at(repository, commit, package_path, work)
    rows = byte_rows((work / executable).read_bytes(), baseline["glibc_ceiling"]) + \
        container_rows(work, executable, held[entry], baseline, runner)
    rows.sort(key=lambda row: ROWS.index(row[0]))
    verdict = "pass" if all(row[1] == PASSED for row in rows) else "fail"
    definition = artefact_store.definition_member(Path(__file__).resolve().parent,
                                                  artefact_store.parameter_document(JUDGE_ARGV, None))
    document = {
        "step": {"kind": "verdict", "id": NAME, "subject": package,
                 "predicate": {"id": "malf consumer_verdict.py", "version": artefact_store.key_of(definition["malf"])},
                 "params": {"glibc_ceiling": baseline["glibc_ceiling"], "entry": entry}},
        "definition": definition,
        "judged": {entry: held[entry]},
        "data": {"container": baseline["container"], "baseline": tree},
        "toolchain": {},
        "system": artefact_store.system_member(),
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

        work = root / "work"
        work.mkdir()
        for name, data in (("sift", green), ("base.log", b"ERROR conn refused db-1\n"),
                           ("changed.log", b"ERROR conn refused db-1\nERROR disk full node-7\n")):
            (work / name).write_bytes(data)
        (work / "sift").chmod(0o755)
        digest = hashlib.sha256(green).hexdigest()

        def fake(ldd: tuple[int, str] = (0, "ldd (Ubuntu GLIBC 2.39-0ubuntu8.6) 2.39\n"),
                 smoke: tuple[int, str] = (0, report)) -> Runner:
            """A daemon in a mount namespace of its own (the release runner's private /tmp): a bind
            mount shows the container an empty directory, so /w holds only what the stdin stream
            carries, and the script's digest check runs over those bytes."""
            def run(argv: list[str], stdin: bytes) -> tuple[int, str, str]:
                if "ldd" in argv:
                    return ldd[0], ldd[1], ""
                held: dict[str, bytes] = {}
                if stdin and "-i" in argv:
                    with tarfile.open(fileobj=io.BytesIO(stdin)) as bundle:
                        held = {member.name: bundle.extractfile(member).read()
                                for member in bundle.getmembers() if member.isfile() and member.mode & 0o100}
                if "sift" not in held:
                    return 127, "", "sh: 1: ./sift: not found"
                want = re.search(r"echo '([0-9a-f]{64})  sift'", argv[-1])
                if not want or hashlib.sha256(held["sift"]).hexdigest() != want.group(1):
                    return 1, "", "sha256sum: WARNING: 1 computed checksum did NOT match"
                return smoke[0], smoke[1], "boom" if smoke[0] else ""
            return run

        arm("the pinned container runs the two-log diff naming the added line once and the shared one never, "
            "and its glibc is the ceiling",
            outcomes(container_rows(work, "sift", digest, baseline, fake())),
            {"container-glibc": PASSED, "consumer-container": PASSED})
        arm("CONTROL: over that daemon a bind mount of the work directory finds no executable, the release "
            "runner's red (step 0 run 38296956741: `exit 127: sh: 1: ./sift: not found`)",
            fake()(["docker", "run", "--rm", "-v", f"{work}:{WORK_MOUNT}:ro", "-w", WORK_MOUNT,
                    baseline["container"], "sh", "-c", f"echo '{digest}  sift' | sha256sum -c - && ./sift --help"],
                   b"")[0], 127)
        arm("RED: a container holding other bytes than the judged entry reds row E",
            outcomes(container_rows(work, "sift", "0" * 64, baseline, fake()))["consumer-container"], FAILED)
        arm("RED: an executable exiting 1 in the container reds row E",
            outcomes(container_rows(work, "sift", digest, baseline, fake(smoke=(1, ""))))["consumer-container"],
            FAILED)
        arm("NOT JUDGED: a docker that cannot run the image is never a pass",
            set(outcomes(container_rows(work, "sift", digest, baseline,
                                        fake(ldd=(DOCKER_UNREACHABLE, ""), smoke=(DOCKER_UNREACHABLE, "")))).values()),
            {NOT_JUDGED})
        arm("RED: a report that does not name the added line reds row E (the content, not a non-empty file)",
            outcomes(container_rows(work, "sift", digest, baseline,
                                    fake(smoke=(0, json.dumps({"ranked_changes": [{"summary": "x"}]})))))
            ["consumer-container"], FAILED)
        arm("RED: a report that names the shared line as a change reds row E",
            outcomes(container_rows(work, "sift", digest, baseline, fake(smoke=(0, json.dumps({"ranked_changes": [
                {"summary": "ERROR disk full node-7"}, {"summary": "ERROR conn refused db-1"}]})))))
            ["consumer-container"], FAILED)
        arm("RED: a ceiling the container's glibc is not reds the consistency row",
            outcomes(container_rows(work, "sift", digest, {**baseline, "glibc_ceiling": "2.40"},
                                    fake()))["container-glibc"],
            FAILED)

    for failure in failures:
        print(f"consumer_verdict selftest FAILED: {failure}")
    print(f"consumer_verdict selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 9 and argv[0] == "judge":
            return judge(Path(argv[1]), json.loads(Path(argv[2]).read_text()), argv[3], argv[4], Path(argv[5]),
                         argv[6], argv[7], Path(argv[8]))
    except Refused as refused:
        print(f"consumer_verdict: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
