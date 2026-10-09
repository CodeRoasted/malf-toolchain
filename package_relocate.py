#!/usr/bin/env python3
"""The released packages' relocatability, measured: the one helper behind `malf relocate-verify`.

ROADMAP N366's first half. At the 1.10.6 cut five repositories exported their PRODUCER's absolute
build-folder paths in `IMPORTED_CXX_MODULES_INCLUDE_DIRECTORIES` (PRIVATE include directories not
wrapped in `$<BUILD_INTERFACE:…>`). A consumer on the producer's own disk never notices — the
folder is there and readable — and gcc fails on an UNREADABLE one (`EACCES`), so a consumer under
another account broke (insight-eidos Release 37682345082). An ABSENT folder is worse: gcc skips a
missing include directory silently, so the defect cannot be seen from a clean machine at all. Two
arms, because each sees what the other cannot:

  * SCAN — every `*.cmake` file every released package ships is read for an absolute path
    literal: a path the package config names that `${_IMPORT_PREFIX}` or
    `${CMAKE_CURRENT_LIST_DIR}` does not anchor. It sees the defect whether or not the folder
    still exists.
  * CONSUME — every released package is restored into a FRESH conan home at another absolute path,
    the producer's home is made unreadable, and a synthetic consumer links every target the
    package's config imports, so CMake builds every C++ module the package ships, as an outside
    consumer does. It sees a reference the scan's pattern cannot spell.

    package_relocate.py run <source home> <fresh home> <released tsv> <profile name> [package]

Exit 0 when both arms pass for every package, 1 on a finding, 2 when nothing could be judged. The
producer home's mode is restored on every exit path but SIGKILL; if it was not, `chmod u+rwx
<source home>` restores it, and the run prints that line before it changes anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# An absolute path literal in CMake text: a `/` or a drive letter that opens a token, where a token
# opens at the start of a line, after whitespace, a quote, `;`, `(` or `=`. A path built from a
# variable (`${_IMPORT_PREFIX}/include`) opens with `$`, so it never matches.
# The lone root `"/"` is CMake's own `_IMPORT_PREFIX` guard, so a path needs one component.
ABSOLUTE = re.compile(r"""(?:^|[\s"';(=])((?:/|[A-Za-z]:/)[^\s"';)$/][^\s"';)$]*)""", re.MULTILINE)
IMPORT_STD = re.compile(r'CMAKE_EXPERIMENTAL_CXX_IMPORT_STD\s+"([0-9a-f-]+)"')
# What a home needs before a consumer can resolve anything in it.
HOME_FILES = ("global.conf", "settings_user.yml", "malf-path-map.cmake")
CONSUMER_TU = "consumer.cpp"
CONSUMER_LISTS = """cmake_minimum_required(VERSION 3.30)
set(CMAKE_EXPERIMENTAL_CXX_IMPORT_STD "{import_std}")
project(malf_relocate_consumer CXX)
find_package({name} CONFIG REQUIRED)
get_property(imported DIRECTORY PROPERTY IMPORTED_TARGETS)
if(NOT imported)
    message(FATAL_ERROR "find_package({name}) imported no target: there is nothing to consume")
endif()
message(STATUS "malf relocate-verify: consuming ${{imported}}")
add_library(malf_relocate_consumer OBJECT {tu})
target_compile_features(malf_relocate_consumer PRIVATE cxx_std_23)
set_target_properties(malf_relocate_consumer PROPERTIES CXX_SCAN_FOR_MODULES ON CXX_MODULE_STD ON)
target_link_libraries(malf_relocate_consumer PRIVATE ${{imported}})
"""


def run(argv: list[str], *, home: Path | None = None, cwd: Path | None = None,
        environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """`argv` with CONAN_HOME pointed at `home`; output captured for the finding."""
    env = {**os.environ, **(environment or {})}
    if home is not None:
        env["CONAN_HOME"] = str(home)
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env)


def released_refs(home: Path, released: Path, only: str) -> list[tuple[str, str, Path]]:
    """(name, ref, package folder) for every released package in `home`; exactly one each."""
    rows = []
    for line in released.read_text().splitlines():
        fields = line.split("\t")
        name = fields[0].strip()
        if not name or (only and only not in (name, Path(fields[1]).name if len(fields) > 1 else "")):
            continue
        done = run(["conan", "list", f"{name}/*#*:*#*", "--format=json"], home=home)
        if done.returncode != 0:
            sys.exit(f"package_relocate: `conan list {name}` failed in {home}: {done.stderr.strip()}")
        found = [(ref, f"{ref}#{rrev}:{package_id}#{prev}")
                 for ref, body in json.loads(done.stdout)["Local Cache"].items()
                 for rrev, recipe in body.get("revisions", {}).items()
                 for package_id, package in recipe.get("packages", {}).items()
                 for prev in package.get("revisions", {})]
        if len(found) != 1:
            print(f"package_relocate: {home} must hold exactly one binary of {name}, holds "
                  f"{len(found)} — nothing is judged", file=sys.stderr)
            sys.exit(2)
        ref, full = found[0]
        folder = run(["conan", "cache", "path", full], home=home)
        if folder.returncode != 0:
            sys.exit(f"package_relocate: `conan cache path {full}` failed: {folder.stderr.strip()}")
        rows.append((name, ref, Path(folder.stdout.strip())))
    if not rows:
        print(f"package_relocate: no released package matched '{only}' in {home}", file=sys.stderr)
        sys.exit(2)
    return rows


def code_of(line: str) -> str:
    """`line` without its CMake comment: from the first `#` outside a quoted argument on."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"' and (index == 0 or line[index - 1] != "\\"):
            quoted = not quoted
        elif char == "#" and not quoted:
            return line[:index]
    return line


def scan(rows: list[tuple[str, str, Path]]) -> list[str]:
    """Every absolute path literal in the CODE of every `*.cmake` file the packages ship; a
    comment names paths as prose (ipc's testing module names `/dev/shm`) and configures nothing."""
    findings = []
    for name, _ref, folder in rows:
        for path in sorted(folder.rglob("*.cmake")):
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
                for match in ABSOLUTE.finditer(code_of(line)):
                    findings.append(f"{name}: {path.relative_to(folder)}:{number} names the "
                                    f"absolute path {match.group(1)}")
    return findings


def seed(source: Path, fresh: Path, profile: str, rows: list[tuple[str, str, Path]]) -> None:
    """The fresh home: the source home's conf and profiles, and the host closure of every released
    package, by save and restore — the route a release asset takes to a consumer."""
    (fresh / "profiles").mkdir(parents=True, exist_ok=True)
    for name in HOME_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, fresh / name)
    for item in (source / "profiles").iterdir():
        shutil.copy2(item, fresh / "profiles" / item.name)
    profile_path = source / "profiles" / profile
    profiles = [f"--profile:host={profile_path}", f"--profile:build={profile_path}"]
    with tempfile.TemporaryDirectory(prefix="package_relocate.") as scratch:
        folder = Path(scratch)

        def conan_to(argv: list[str], home: Path, output: Path | None = None) -> None:
            done = run(argv, home=home)
            if done.returncode != 0:
                sys.exit(f"package_relocate: `{' '.join(argv)}` failed in {home}: "
                         f"{done.stderr.strip()}")
            if output is not None:
                output.write_text(done.stdout)

        # ONE GRAPH PER PACKAGE, the graph its consumer resolves: the released set resolved as
        # one graph has version-range conflicts no single consumer meets (lz4 through libpq).
        lists = []
        for index, (_name, ref, _folder) in enumerate(rows):
            graph, pkglist = folder / f"graph{index}.json", folder / f"pkglist{index}.json"
            conan_to(["conan", "graph", "info", f"--requires={ref}", *profiles, "--format=json"],
                     source, graph)
            conan_to(["conan", "list", f"--graph={graph}", "--graph-binaries=Cache",
                      "--format=json"], source, pkglist)
            lists += ["-l", str(pkglist)]
        merged, archive = folder / "pkglist.json", folder / "closure.tgz"
        conan_to(["conan", "pkglist", "merge", *lists, "--format=json"], source, merged)
        conan_to(["conan", "cache", "save", f"--list={merged}", f"--file={archive}"], source)
        conan_to(["conan", "cache", "restore", str(archive)], fresh)
    print(f"package_relocate: {fresh} holds the host closure of {len(rows)} released package(s) "
          f"from {source}")


def import_std_gate(released: Path) -> str:
    """The one CMAKE_EXPERIMENTAL_CXX_IMPORT_STD value the released packages' sources declare: a
    consumer of a module built against `import std` must enable it with the same CMake."""
    values = {match.group(1)
              for line in released.read_text().splitlines() if "\t" in line
              for lists in [Path(line.split("\t")[1]) / "CMakeLists.txt"] if lists.is_file()
              for match in IMPORT_STD.finditer(lists.read_text())}
    if len(values) != 1:
        print(f"package_relocate: the released sources declare {len(values)} import-std gate "
              f"value(s), not one: {sorted(values)} — nothing is judged", file=sys.stderr)
        sys.exit(2)
    return values.pop()


def build_environment(folder: Path) -> dict[str, str]:
    """The profile's [buildenv] (CC/CXX among it), as `conanbuild.sh` exports it."""
    done = run(["bash", "-c", f"source {folder / 'conanbuild.sh'} >/dev/null && env -0"])
    if done.returncode != 0:
        sys.exit(f"package_relocate: sourcing {folder / 'conanbuild.sh'} failed: {done.stderr}")
    return dict(item.split("=", 1) for item in done.stdout.split("\0") if "=" in item)


def consume(fresh: Path, profile: str, name: str, ref: str, scratch: Path,
            import_std: str) -> str | None:
    """Install `ref` alone into a synthetic consumer and compile every module it ships.

    TWO BUILDS, because a consumer that imports nothing only SCANS a package's modules — ninja
    builds a BMI for an importer — and the BMI compile is the step that reads every include
    directory the package config exports. The first build scans and names the modules; the second,
    in a fresh tree (a rebuild in the first keeps a dyndep order without the `std` module), imports
    every primary interface, so every module and partition compiles."""
    folder = scratch / name
    folder.mkdir()
    (folder / CONSUMER_TU).write_text("int malf_relocate_consumer() { return 0; }\n")
    (folder / "CMakeLists.txt").write_text(CONSUMER_LISTS.format(name=name, tu=CONSUMER_TU,
                                                                  import_std=import_std))
    profile_path = fresh / "profiles" / profile
    install = ["conan", "install", f"--requires={ref}", f"--profile:host={profile_path}",
               f"--profile:build={profile_path}", "--build=never", "-g", "CMakeToolchain", "-g",
               "CMakeDeps", "-c", "tools.cmake.cmaketoolchain:generator=Ninja", "-of",
               str(folder / "conan")]
    toolchain = f"-DCMAKE_TOOLCHAIN_FILE={folder / 'conan' / 'conan_toolchain.cmake'}"

    def tree(build: str) -> list[tuple[str, list[str]]]:
        return [(f"{build} configure", ["cmake", "-S", str(folder), "-B", str(folder / build),
                                        "-G", "Ninja", toolchain, "-DCMAKE_BUILD_TYPE=Release"]),
                (f"{build} build", ["cmake", "--build", str(folder / build)])]

    environment: dict[str, str] = {}
    for step, argv in [("install", install), *tree("scan"), ("import", []), *tree("build")]:
        if step == "import":
            if not importer(folder / "scan", folder):
                # A package that ships no module is consumed by the scan tree alone: its
                # consumer TU already compiled with every include directory it exports.
                return None
            continue
        done = run(argv, home=fresh, environment=environment)
        if done.returncode != 0:
            tail = "\n      ".join((done.stdout + done.stderr).strip().splitlines()[-12:])
            return f"{name}: the consumer's {step} failed (exit {done.returncode}):\n      {tail}"
        if step == "install":
            environment = build_environment(folder / "conan")
    return None


def importer(build: Path, folder: Path) -> list[str]:
    """Rewrite the consumer TU to import every primary module interface the imported targets
    provide, read off the scan, and return their names — none for a package with no module."""
    names = sorted({provided["logical-name"]
                    for ddi in build.glob("CMakeFiles/*@synth_*.dir/*.ddi")
                    for rule in json.loads(ddi.read_text()).get("rules", [])
                    for provided in rule.get("provides", [])
                    if ":" not in provided["logical-name"]})
    if names:
        (folder / CONSUMER_TU).write_text("".join(f"import {name};\n" for name in names)
                                          + "int malf_relocate_consumer() { return 0; }\n")
    return names


def main(argv: list[str]) -> int:
    if len(argv) not in (5, 6) or argv[0] != "run":
        print(__doc__, file=sys.stderr)
        return 2
    source, fresh, released, profile = Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4]
    only = argv[5] if len(argv) == 6 else ""
    if fresh.resolve() == source.resolve() or fresh.resolve().is_relative_to(source.resolve()):
        print(f"package_relocate: the fresh home {fresh} must lie outside {source}", file=sys.stderr)
        return 2
    rows = released_refs(source, released, only)
    import_std = import_std_gate(released)

    findings = scan(rows)
    print(f"package_relocate: SCAN — {len(findings)} absolute path(s) in the cmake files of "
          f"{len(rows)} released package(s)")

    seed(source, fresh, profile, rows)
    mode = stat.S_IMODE(source.stat().st_mode)
    print(f"package_relocate: {source} is made unreadable for the consume arm; if this run dies "
          f"before restoring it: chmod {mode:o} {source}")
    source.chmod(0)
    try:
        with tempfile.TemporaryDirectory(prefix="malf-relocate-consumer.") as scratch:
            for name, ref, _folder in rows:
                failure = consume(fresh, profile, name, ref, Path(scratch), import_std)
                print(f"  {'FAIL' if failure else 'ok  '} consume {ref}")
                if failure:
                    findings.append(failure)
    finally:
        source.chmod(mode)

    for finding in findings:
        print(f"  FINDING {finding}")
    print(f"package_relocate: {'RED' if findings else 'GREEN'} — {len(findings)} finding(s) over "
          f"{len(rows)} released package(s), consumed from {fresh} with {source} unreadable")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
