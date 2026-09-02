#!/usr/bin/env python3
"""ADR-3.D9 — the build system's INVENTORY: no CMake project may be invisible to it.

THE DEFECT THIS CLOSES, measured 2026-08-08:

    a signature change in canon
      -> malf test    714/714 GREEN   <- never compiles proof/
      -> cut-verify    18/18  GREEN   <- builds release:true packages only
      -> every gate           GREEN
      -> the tag              RED, all five golden legs

A four-argument signature against a three-argument call: a plain compile error that
every gate reported green. `insight-canon/proof/` declares its own `project()` and
belongs to no conan package, so nothing compiled it until the golden workflow did —
its FIRST compile of a release cycle. The tag was not where the defect entered; it was
the first place anything looked.

So this is not a missing ceremony step. `malf test` was asserting something FALSE, every
day, to every lane (ADR-3.D9) — and a false green repaired at the last gate is still a
false green for the fourteen days before it. The repair belongs to the build system.

── THE TWO HALVES, and they are different memberships (ADR-3.D9) ────────────────────────

  INVENTORY != UNIFORM BUILD. The build system must know a project EXISTS and COMPILES.
  It need not build it the way it builds a package.

  * `lint`  — the derived predicate: a `project()` no package build ever configures is a
              build-system defect. Fails on the difference, so a new orphan reds on the
              commit that adds it.
  * `build` — each inventory entry configures and builds to a LINKED artifact, in ONE
              canonical cell.

`proof/` is NOT made a conan package, deliberately: a package carries one profile and one
build, which is exactly what would flatten its 8-cell determinism matrix. Being in the
inventory and being a package are two different things, and conflating them is what
produced the orphan in the first place.

── THE DECLARED BOUNDARY (ADR-3.D9), written here so nobody expects the wrong thing ─────

  THE DAILY GATE PROVES `proof/` BUILDS. THE TAG PROVES IT IS DETERMINISTIC.

A digest drift still first appears at the cut, BY DESIGN. One cell catches signature
drift, removed symbols, changed types, module-interface drift, and — because it builds to
a linked artifact rather than stopping at compile — link errors too. It cannot catch
codegen-dependent divergence, and it is not trying to: that is the 8-cell matrix's job,
driven by its own script, untouched and unflattened by this file.

That boundary is DECLARED rather than discovered because an undeclared one is
indistinguishable from an oversight, and gets "fixed" by widening the daily gate into an
8-cell run nobody can afford.

── WHY DERIVED AND NEVER ENUMERATED (ADR-3.D9) ──────────────────────────────────────────

Both sides are facts. Declared projects are greppable from `git ls-files` (tracked-ness —
the same allowlist-from-a-fact shape the root CLAUDE.md mandates for workspace sweeps).
Configured ones are derivable from the package roots and their `add_subdirectory` closure.
A hand-kept directory list would rot on the sixth entry; a derived one cannot.

The rule was written asserting `insight-canon/proof/` was the ONLY orphan. Run for the
first time, this predicate found a SECOND of the identical species
(`insight-metalog/scripts/det_harness/`) — on the day the ruling was written. That is the
clause justifying itself, and it is why the population may never be a list in a document.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# A `project()` call at the start of a line (CMake's own convention). Deliberately NOT
# matched inside comments: `strip_comments` runs first, so a `# project(foo)` in prose
# cannot mint a phantom declaration — the same discipline as the one-write-site lint.
PROJECT_RE = re.compile(r"^[ \t]*project[ \t]*\(", re.MULTILINE)
ADD_SUBDIR_RE = re.compile(r"^[ \t]*add_subdirectory[ \t]*\(\s*([^\s)]+)", re.MULTILINE)
# `${VAR}` / `$ENV{VAR}` — a path this reader cannot resolve without evaluating CMake.
UNRESOLVED_RE = re.compile(r"\$(?:ENV)?\{")


def strip_cmake_comments(text: str) -> str:
    """Drop `#` comments, preserving line count so reported line numbers stay true."""
    out = []
    for line in text.split("\n"):
        hash_at = line.find("#")
        out.append(line if hash_at < 0 else line[:hash_at])
    return "\n".join(out)


@dataclass
class Repo:
    root: Path
    name: str
    cmake_lists: list[Path] = field(default_factory=list)


def git_tracked_cmake_lists(repo: Path) -> list[Path]:
    """Every TRACKED CMakeLists.txt. Tracked-ness is the fact this derives from: a new one
    joins automatically on the commit that adds it, and a build artifact never can,
    because build trees are gitignored."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "*CMakeLists.txt"],
        capture_output=True, text=True, check=True,
    )
    return [repo / rel for rel in proc.stdout.split("\0") if rel]


def discover_repos(workspace: Path) -> list[Repo]:
    """Repo discovery is derived from .git presence, over BOTH legitimate workspace shapes:
    the workspace-of-repos (the dev tree: children carry .git; the superproject root also
    carries one and contributes its own tracked files) and the single-repo checkout (CI:
    MALF_WORKSPACE_ROOT is the tag checkout, whose .git sits at the root and whose children
    carry none). The first release run of this lint saw only the first shape and
    returned ZERO repos on the second — the non-vacuity arm then failed the publish job on
    a scan that had reached nothing (coderoast-ipc v1.9.3, run 31634074680). The arm was
    right; discovery was blind. A repo found both ways is counted once."""
    repos = []
    if (workspace / ".git").exists():
        repos.append(Repo(root=workspace, name=workspace.name,
                          cmake_lists=git_tracked_cmake_lists(workspace)))
    for entry in sorted(workspace.iterdir()):
        if not entry.is_dir() or not (entry / ".git").exists():
            continue
        repos.append(Repo(root=entry, name=entry.name,
                          cmake_lists=git_tracked_cmake_lists(entry)))
    return repos


def load_inventory(repo: Path) -> dict[str, dict]:
    """The repo's declared inventory entries, from its own packages.yml.

    Parsed WITHOUT PyYAML: malf's runtime must not grow a dependency to answer a build
    question, and the block shape here is fixed and shallow. A malformed entry raises
    rather than degrading to "no inventory" — a silently empty inventory would re-open
    exactly the false green this file exists to close.
    """
    manifest = repo / "packages.yml"
    if not manifest.exists():
        return {}
    entries: dict[str, dict] = {}
    current: str | None = None
    in_inventory = False
    for raw in manifest.read_text().split("\n"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_inventory = line.strip() == "inventory:"
            current = None
            continue
        if not in_inventory:
            continue
        stripped = line.strip()
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            entries[current] = {"defines": {}}
        elif current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if indent == 6:
                entries[current]["defines"][key] = value
            elif key != "defines":
                entries[current][key] = value
    for name, entry in entries.items():
        for required in ("path", "toolchain_from", "target"):
            if required not in entry:
                raise SystemExit(
                    f"malf inventory: {manifest} entry '{name}' is missing '{required}'. "
                    "An inventory entry must say WHERE it lives, WHICH package's conan "
                    "deps provide its toolchain, and WHAT linked artifact proves it built."
                )
    return entries


def configured_dirs(repo: Repo, inventory: dict[str, dict]) -> tuple[set[Path], list[str]]:
    """The set of directories some build actually configures, plus the paths this reader
    could not resolve.

    Three ways a directory is covered, all facts:
      * it holds a `conanfile.py`   — malf's sweep discovers and builds it;
      * it is reached by a literal `add_subdirectory()` from a covered root, transitively;
      * it is a declared inventory entry — which is what this whole file exists to give
        `proof/`.

    ⚠ AN UNRESOLVABLE `add_subdirectory(${VAR}/x)` IS REPORTED, NEVER ASSUMED EITHER WAY,
    and the direction of that choice is the safety property. Resolving a path can only ADD
    coverage, so declining to resolve one can only ever produce a FALSE ORPHAN — loud, and
    a human classifies it. The opposite choice (assume it covers something) would produce
    a false GREEN, which is the exact failure class this file closes. Today every
    variable-bearing call sits inside the two harnesses, which are inventory entries
    already, so nothing is lost by it.
    """
    covered: set[Path] = set()
    unresolved: list[str] = []
    roots: list[Path] = [p.parent for p in repo.cmake_lists if (p.parent / "conanfile.py").exists()]
    for entry in inventory.values():
        roots.append(repo.root / entry["path"])
    pending = list(roots)
    covered.update(roots)
    while pending:
        current = pending.pop()
        lists_file = current / "CMakeLists.txt"
        if not lists_file.exists():
            continue
        text = strip_cmake_comments(lists_file.read_text(errors="replace"))
        for match in ADD_SUBDIR_RE.finditer(text):
            arg = match.group(1)
            if UNRESOLVED_RE.search(arg):
                line_no = text[: match.start()].count("\n") + 1
                unresolved.append(f"{lists_file}:{line_no}: add_subdirectory({arg})")
                continue
            child = (current / arg).resolve()
            if child not in covered:
                covered.add(child)
                pending.append(child)
    return covered, unresolved


def run_lint(workspace: Path, extra_orphan: str | None) -> int:
    repos = discover_repos(workspace)
    all_orphans: list[tuple[str, Path]] = []
    all_unresolved: list[str] = []
    declared_total = 0
    for repo in repos:
        inventory = load_inventory(repo.root)
        covered, unresolved = configured_dirs(repo, inventory)
        all_unresolved.extend(unresolved)
        for lists_file in repo.cmake_lists:
            text = strip_cmake_comments(lists_file.read_text(errors="replace"))
            if not PROJECT_RE.search(text):
                continue
            declared_total += 1
            if lists_file.parent.resolve() not in {c.resolve() for c in covered}:
                all_orphans.append((repo.name, lists_file))

    print(f"build inventory lint (ADR-3.D9) — {len(repos)} repos, "
          f"{declared_total} declared CMake project(s)")
    # NON-VACUITY: a scan that found no projects reached nothing and must not read as clean.
    if declared_total == 0:
        print("::error::the inventory lint found ZERO declared projects — it reached no "
              "CMakeLists.txt at all. A vacuous scan is a silent green, so this FAILS.",
              file=sys.stderr)
        return 2
    if all_unresolved:
        print("  unresolved add_subdirectory paths (reported, never assumed covered):")
        for item in all_unresolved:
            print(f"    {item}")
    if not all_orphans:
        print("PASS: every declared CMake project is configured by a package build or is "
              "a declared inventory entry.")
        return 0
    print(f"::error::{len(all_orphans)} ORPHAN CMake project(s) — declared but never "
          f"configured by any build:", file=sys.stderr)
    for repo_name, lists_file in all_orphans:
        print(f"    {repo_name}: {lists_file}", file=sys.stderr)
    print(
        "\nAn orphan project is a FALSE GREEN (ADR-3.D9): `malf test` reports success while\n"
        "a translation unit in the repo does not compile, and the first thing that compiles\n"
        "it is the tag. Give it an `inventory:` entry in its repo's packages.yml — path,\n"
        "toolchain_from, target — so one canonical cell configures and links it every day.\n"
        "Do NOT make it a conan package to silence this: a package carries one profile and\n"
        "one build, which would flatten a determinism matrix if the project has one.",
        file=sys.stderr)
    return 1


def substitute(value: str, workspace: Path, repo: Path) -> str:
    return value.replace("${workspace}", str(workspace)).replace("${repo}", str(repo))


# ── ADR-3.D10: a ${workspace}-crossing cell is WORKSPACE-GRAIN ────────────────────────────
#
# A cell whose defines dereference `${workspace}` beyond the repo root holds a compile
# property of the workspace-of-repos SHAPE, not of the repo alone — a repo-shaped checkout
# cannot evaluate it, any more than one cell can evaluate the 8-cell matrix. Measured on
# the v1.9.3 metalog publish (run 31891808449): the single-repo tag checkout substituted
# `${workspace}/insight-canon` to a nonexistent directory and the cell failed a configure
# it was never designed to run, while the same harness was green at its designed site.
#
# Per shape, when a dereferenced root is ABSENT:
#   * workspace-of-repos (workspace root != repo root): loud FAIL — there it means a
#     partial checkout or a typo'd root, and a skip would silently disarm the daily
#     anti-false-green, the exact class ADR-3.D9 closed.
#   * single-repo (workspace root == repo root): a loud, COUNTED, DECLARED skip — the
#     cell is out of the shape's jurisdiction and its property is held by the workspace
#     shape's release-train gate (ADR-3.D10's coverage MUST). If the substituted path
#     exists (a job that stages the sibling inside the checkout), the cell builds: the
#     skip covers only the genuinely unsatisfiable.
#
# The trigger is DERIVED from the defines this file already parses. A declared
# `requires:`/`siblings:` manifest key was considered and REFUSED (ADR-3.D10): the fact
# already lives in the defines, and a second declaration can drift from it — a drifted
# declaration is a lie with a straight face.
#
# ONE WRITE SITE for the skip line's needle. The tests assert both its PRESENCE
# (single-repo shape) and its ABSENCE (workspace shape), and an absence assertion keyed
# on a retyped string goes vacuous on the first rewording
# (MEM:synthetic-gate-vacuity-vs-judgment) — tests import this constant, never respell it.
WORKSPACE_GRAIN_SKIP_NEEDLE = "ADR-3.D10 SKIP (workspace-grain)"


def workspace_grain_absences(entry: dict, workspace: Path, repo: Path) -> list[tuple[str, Path]]:
    """The entry's defines whose RAW value dereferences `${workspace}` and whose
    substituted path does not exist — (define key, substituted path) pairs.
    Derived from the manifest text, never declared (ADR-3.D10)."""
    absent: list[tuple[str, Path]] = []
    for key, raw in entry.get("defines", {}).items():
        if "${workspace}" not in raw:
            continue
        resolved = Path(substitute(raw, workspace, repo))
        if not resolved.exists():
            absent.append((key, resolved))
    return absent


def run_build(workspace: Path, repo_root: Path, build_key: str, profile: str,
              build_type: str) -> int:
    inventory = load_inventory(repo_root)
    if not inventory:
        return 0
    skipped = 0
    for name, entry in sorted(inventory.items()):
        source = repo_root / entry["path"]
        # The ADR-3.D10 shape gate, before any configure. The FAIL arm is checked by shape
        # FIRST so the skip is structurally unreachable in the workspace shape — not
        # merely unreached.
        absences = workspace_grain_absences(entry, workspace, repo_root)
        if absences:
            missing = ", ".join(f"{key} -> {path}" for key, path in absences)
            if workspace != repo_root:
                print(f"malf inventory: FAIL — cell {name} ({repo_root.name}/"
                      f"{entry['path']}): {missing} does not exist. In the "
                      "workspace-of-repos shape an absent ${workspace}-dereferenced "
                      "sibling is a partial checkout or a typo'd root, never a skip "
                      "(ADR-3.D10).", file=sys.stderr)
                return 1
            skipped += 1
            print(f"{WORKSPACE_GRAIN_SKIP_NEEDLE}: cell {name} ({repo_root.name}/"
                  f"{entry['path']}) — {missing} is unsatisfiable in this single-repo "
                  "checkout. The cell is workspace-grain: its compile property is held "
                  "by the workspace-of-repos shape's release-train gate (ADR-3.D10), "
                  "not by this shape.")
            continue
        # PERSISTENT, profile-keyed build tree — deliberately NOT the mktemp the determinism
        # scripts use. Their clean room is load-bearing for a DIGEST; this cell's job is to
        # answer "does it still compile", and an incremental tree is what makes that
        # affordable to run every day. Same directory shape as a package's build-<key>, so
        # `malf clean build` already reaches it.
        build_dir = source / f"build-inventory-{build_key}"
        toolchain_pkg = repo_root / entry["toolchain_from"]
        print(f"── malf inventory: {repo_root.name}/{entry['path']} "
              f"(target {entry['target']}, {profile}) ──")
        conan_out = build_dir / "conan"
        install = subprocess.run(
            ["conan", "install", str(toolchain_pkg),
             f"--profile:host={profile}", f"--profile:build={profile}",
             "--build=missing", "-of", str(conan_out)],
            capture_output=True, text=True)
        if install.returncode != 0:
            print(f"malf inventory: conan install FAILED for {name}", file=sys.stderr)
            # BOTH streams, generously. A dep's real cmake.configure error sits well above
            # conan's final ConanException summary, and a short tail of stdout alone hid the
            # cause on the gcc-15.3 CI drift — the same trap determinism_bitidentity.sh
            # records at its own install site.
            print("\n".join((install.stdout + install.stderr).split("\n")[-40:]),
                  file=sys.stderr)
            return 1
        toolchains = list(conan_out.rglob("conan_toolchain.cmake"))
        if not toolchains:
            print(f"malf inventory: no conan_toolchain.cmake under {conan_out}", file=sys.stderr)
            return 1
        # THE CELL'S BUILD TYPE IS THE PROFILE'S, passed in by malf, and this line used to read a
        # literal "Release". The two halves of one cell then disagreed whenever the profile was not
        # a Release one: the `conan install` above passes NO `-s build_type`, so the dependencies
        # resolved at the profile's build type while the cmake configure was handed Release. Under
        # linux-clang21-asan (build_type Debug) that produced a Release cell inside a directory
        # named build-inventory-clang21-asan, which is the `N120` mislabel one level down.
        configure = ["cmake", "-S", str(source), "-B", str(build_dir), "-G", "Ninja",
                     f"-DCMAKE_BUILD_TYPE={build_type}",
                     f"-DCMAKE_TOOLCHAIN_FILE={toolchains[0]}"]
        for key, value in entry.get("defines", {}).items():
            configure.append(f"-D{key}={substitute(value, workspace, repo_root)}")
        # The cell's compiler comes from conan's OWN generated buildenv (`CC`/`CXX` out of the
        # profile's [buildenv]), sourced rather than mapped. The determinism scripts carry a
        # hardcoded profile->binary table because they drive a fixed matrix; an inventory entry
        # must follow whatever profile malf was invoked with, so a table here would be a second
        # source of truth that silently disagrees the day a profile moves.
        buildenv = conan_out / "conanbuild.sh"
        for stage, cmd in (("configure", configure),
                           ("build", ["cmake", "--build", str(build_dir),
                                      "--target", entry["target"]])):
            script = " ".join(shlex.quote(part) for part in cmd)
            if buildenv.exists():
                script = f". {shlex.quote(str(buildenv))} >/dev/null && {script}"
            proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"malf inventory: {stage} FAILED for {name} "
                      f"({repo_root.name}/{entry['path']})", file=sys.stderr)
                tail = (proc.stdout + proc.stderr).split("\n")[-40:]
                print("\n".join(tail), file=sys.stderr)
                return 1
        # A LINKED ARTIFACT, not merely a successful build command (ADR-3.D9). Compile alone
        # misses a symbol declared and never defined; the binary existing is what proves the
        # link happened.
        produced = [p for p in build_dir.rglob(entry["target"])
                    if p.is_file() and os.access(p, os.X_OK)]
        if not produced:
            print(f"malf inventory: {name} built but produced no linked `{entry['target']}` "
                  f"artifact under {build_dir}", file=sys.stderr)
            return 1
        print(f"   linked: {produced[0]}")
    if skipped:
        # COUNTED as well as declared: a skip that is only a per-cell line can scroll
        # away; the count is the one-glance fact a CI log reader checks against the
        # inventory size.
        print(f"malf inventory: {skipped} workspace-grain cell(s) skipped in the "
              "single-repo shape (declared above, ADR-3.D10).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="build inventory (ADR-3.D9): lint + one-cell build")
    parser.add_argument("mode", choices=["lint", "build"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--repo", help="repo root (build mode)")
    parser.add_argument("--build-key", default="default")
    parser.add_argument("--profile", default="")
    # NO DEFAULT, and required in `build` mode only (checked below, beside --repo — `lint` mode
    # reads inventory declarations and configures nothing). A default here would be a second
    # declaration of the build type, and the one thing this argument exists to remove is a second
    # declaration of the build type.
    parser.add_argument("--build-type",
                        help="the active profile's declared build_type (malf passes it)")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    # A bad path is a MISTAKE and must say so, not raise. A traceback here reads as "the tool is
    # broken" rather than "you pointed it at nothing", and the whole value of this check is that
    # its failures are legible the moment they fire.
    if not workspace.is_dir():
        print(f"malf inventory: --workspace '{workspace}' is not a directory.", file=sys.stderr)
        return 2
    workspace = workspace.resolve()
    if args.mode == "lint":
        return run_lint(workspace, None)
    if not args.repo:
        parser.error("build mode needs --repo")
    if not args.build_type:
        parser.error("build mode needs --build-type (the active profile's declared build_type)")
    return run_build(workspace, Path(args.repo).resolve(), args.build_key, args.profile,
                     args.build_type)


if __name__ == "__main__":
    sys.exit(main())
