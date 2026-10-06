#!/usr/bin/env python3
"""ninja_deps_guard.py <build dir> — delete every compile output ninja holds no dependency record for,
so the build that follows recompiles it instead of linking it stale.

THE DEFECT THIS CLOSES, measured 2026-10-06 (WIP W477). A header edit left an object file
uncompiled, the test binary relinked from it, and `malf test insight-metalog` passed 357/357 against
code the compiler never saw. The object had no record in `.ninja_deps`, and ninja's verdict on such an
object is not reliable in these trees:

  * HOW A RECORD IS LOST. A tree is written in two roles under one key, as the TARGET (tests and
    benches ON) and as a sibling's DEPENDENCY (both OFF). Ninja recompacts `.ninja_deps` whenever
    the log has grown three times its live size, and a recompaction keeps only the records of edges
    the CURRENT manifest has. One recompaction while the tree is in the dependency role drops the
    record of every test and bench object, and the objects stay on disk. Measured on throwaway
    projects with clang-21 and gcc-16.2: a tests-OFF configure, a recompaction, 8 records became 4.
  * WHY NINJA DOES NOT REBUILD IT. Ninja marks an edge dirty when its record is missing, on the
    FIRST visit only (`graph.cc`, `RecomputeNodeDirty`); every later visit resets the edge's
    `deps_missing_` flag to false. A C++ module build loads dyndep files mid-build, and each load
    re-visits every wanted edge downstream of it (`Plan::RefreshDyndepDependents`). After such a
    re-visit nothing marks the edge as record-less, and a restat edge finishing with an unchanged
    output (a modmap, a BMI) lets `Plan::CleanNode` drop it from the plan as clean. Measured on the
    real tree, not on a toy: in insight-metalog's gcc-16.2 tree the tests target relinked 7 times
    between 2026-10-05 18:45 and 2026-10-06 13:52 over an object compiled on 2026-09-14 that had
    no record, while a dry run of the same tree says it would rebuild it.

A MISSING OUTPUT IS DIRTY ON EVERY PATH THROUGH NINJA, `CleanNode`'s included, so deleting the object
is what makes the next build honest whatever order the dyndep loads take. The check reads the tree's
own ninja (`CMAKE_MAKE_PROGRAM`, the program that wrote the log) and runs every tool under `-n`,
which skips the log's write half: this check never recompacts, so it can drop no record itself.

WHAT IT REACHES: the FIRST output of every edge whose rule binds `deps`, the only edges ninja keeps a
record for, which in a CMake tree are the compile edges and the link edges that read a linker depfile.
Ninja writes a record for every output of such an edge but reads only the first one's
(`ImplicitDepLoader::LoadDepsFromLog`), so the first output is the one whose record decides, and the
one judged here. One edge, one entry: a CMake link edge names `<target>[1]_tests.cmake` twice, once
relative and once under `${cmake_ninja_workdir}`, and listing outputs instead of edges deleted that
file and then failed on its second spelling (measured on insight-metalog's gcc-16.2 tree). The edges
are read from the manifest's own `build` lines, ninja's `$` escapes and its top-level variables
resolved. An output that does not exist yet is not
touched; the build makes it. A tree that is not a ninja tree is outside this check and is said to be.

EXIT 0 when the tree is honest after the check (nothing deleted, or every deps-less output deleted),
1 when ninja could not be read or an output could not be deleted, so the caller refuses to build.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RULE_LINE = re.compile(r"^rule\s+(\S+)\s*$")
DEPS_BINDING = re.compile(r"^\s+deps\s*=\s*\S")
INCLUDE_LINE = re.compile(r"^(?:include|subninja)\s+(\S+)\s*$")
VARIABLE_LINE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)\s*=\s?(?P<value>.*)$")
VARIABLE_USE = re.compile(r"\$\{(?P<braced>[A-Za-z0-9_.-]+)\}|\$(?P<bare>[A-Za-z0-9_-]+)")
RECORD_LINE = re.compile(r"^(?P<output>\S.*?): #deps \d+, deps mtime ")
MAKE_PROGRAM = re.compile(r"^CMAKE_MAKE_PROGRAM:[A-Z]+=(?P<path>.+)$", re.M)
NAMED_LIMIT = 10


def deps_rules(manifest: Path, seen: set[Path] | None = None) -> set[str]:
    """Every rule that binds `deps`, read from the manifest and every file it includes."""
    seen = seen if seen is not None else set()
    if manifest in seen or not manifest.is_file():
        return set()
    seen.add(manifest)
    rules: set[str] = set()
    current = None
    for line in manifest.read_text(errors="replace").splitlines():
        rule = RULE_LINE.match(line)
        if rule:
            current = rule.group(1)
            continue
        if not line.startswith((" ", "\t")):
            current = None
            included = INCLUDE_LINE.match(line)
            if included:
                rules |= deps_rules(manifest.parent / included.group(1), seen)
            continue
        if current is not None and DEPS_BINDING.match(line):
            rules.add(current)
    return rules


def ninja_program(build_dir: Path) -> str:
    """The ninja this tree is built with, from its own cache, else the one on PATH."""
    cache = build_dir / "CMakeCache.txt"
    if cache.is_file():
        found = MAKE_PROGRAM.search(cache.read_text(errors="replace"))
        if found and Path(found.group("path")).is_file():
            return found.group("path")
    return "ninja"


def tool(ninja: str, build_dir: Path, *args: str) -> str:
    """One read-only ninja tool run; raises with ninja's own words when it fails."""
    proc = subprocess.run([ninja, "-n", "-C", str(build_dir), "-t", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"`{ninja} -n -C {build_dir} -t {' '.join(args)}` exited "
                           f"{proc.returncode}: {(proc.stderr or proc.stdout).strip()[-400:]}")
    return proc.stdout


def logical_lines(manifest: Path) -> list[str]:
    """The manifest's lines with ninja's `$` + newline continuations joined."""
    lines: list[str] = []
    pending = ""
    for raw in manifest.read_text(errors="replace").splitlines():
        trailing = len(raw) - len(raw.rstrip("$"))
        if trailing % 2 == 1:
            pending += raw[:-1]
            continue
        lines.append(pending + raw)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def first_output(build_line: str, variables: dict[str, str]) -> tuple[str, str] | None:
    """The first output and the rule of one `build` line, its escapes and variables resolved."""
    text = build_line[len("build "):]
    tokens: list[str] = []
    current = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char == "$" and index + 1 < len(text):
            following = text[index + 1]
            if following in " :$":
                current += following
                index += 2
                continue
            used = VARIABLE_USE.match(text, index)
            if used:
                current += variables.get(used.group("braced") or used.group("bare"), "")
                index = used.end()
                continue
        if char == ":":
            if current:
                tokens.append(current)
            rule = text[index + 1:].split()
            return (tokens[0], rule[0]) if tokens and rule else None
        if char in " |":
            if current:
                tokens.append(current)
            current = ""
            index += 1
            continue
        current += char
        index += 1
    return None


def deps_edges(manifest: Path, rules: set[str]) -> list[str]:
    """The first output of every `build` line whose rule binds `deps`, in manifest order."""
    variables: dict[str, str] = {}
    outputs = []
    for line in logical_lines(manifest):
        if line.startswith("build "):
            edge = first_output(line, variables)
            if edge and edge[1] in rules:
                outputs.append(edge[0])
            continue
        binding = VARIABLE_LINE.match(line)
        if binding and not line.startswith(("rule ", "pool ", "default ", "include ", "subninja ")):
            variables[binding.group("name")] = binding.group("value")
    return outputs


def deps_less_outputs(build_dir: Path) -> list[str]:
    """The existing first outputs of `deps` edges that `.ninja_deps` holds no record for, sorted."""
    manifest = build_dir / "build.ninja"
    rules = deps_rules(manifest)
    if not rules:
        return []
    ninja = ninja_program(build_dir)
    recorded = {match.group("output")
                for match in map(RECORD_LINE.match, tool(ninja, build_dir, "deps").splitlines())
                if match}
    return sorted({output for output in deps_edges(manifest, rules)
                   if output not in recorded and (build_dir / output).exists()})


def guard(build_dir: Path, out=sys.stdout, err=sys.stderr) -> int:
    """Delete every deps-less output under <build dir>; the exit code the module documents."""
    if not (build_dir / "build.ninja").is_file():
        print(f"malf: {build_dir} has no build.ninja — not a ninja tree, no dependency records to "
              f"check", file=out)
        return 0
    try:
        missing = deps_less_outputs(build_dir)
    except (OSError, RuntimeError) as failure:
        print(f"malf: FATAL: cannot read ninja's dependency records in {build_dir}: {failure}",
              file=err)
        print("malf: refusing to build — an object with no record can be linked stale while every "
              "test passes, and this tree cannot be checked for one.", file=err)
        return 1
    if not missing:
        return 0
    print(f"malf: {build_dir}: {len(missing)} existing output(s) have no ninja dependency record — "
          f"deleting them so this build recompiles them rather than linking them stale:", file=out)
    for output in missing[:NAMED_LIMIT]:
        print(f"  {output}", file=out)
    if len(missing) > NAMED_LIMIT:
        print(f"  ... and {len(missing) - NAMED_LIMIT} more", file=out)
    failed = []
    for output in missing:
        try:
            (build_dir / output).unlink()
        except OSError as failure:
            failed.append(f"{output}: {failure}")
    if failed:
        print(f"malf: FATAL: {len(failed)} deps-less output(s) could not be deleted, so the build "
              f"would link them stale: {'; '.join(failed[:NAMED_LIMIT])}", file=err)
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ninja_deps_guard.py <build dir>", file=sys.stderr)
        return 2
    return guard(Path(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
