"""The ONE test selection, and the run of it inside a first-party recipe's `build()`.

DN-142.D5 (1) and (5). A package has one ship-leg build, `conan create`, and that build runs the
tests B3 (`malf test` at the workspace root) runs, so the selection is defined once, here, for both
callers: `malf test` reads it through `desk-args`, and every first-party recipe calls
`run_tests(self)` from `build()`, after `cmake.build()`.

The selection: the default population excludes the ctest label `corpus` (`-LE corpus`); `--corpus`
selects that label alone (`-L corpus`). A corpus test needs a private third-party corpus mounted
through an environment variable, so a create BUILDS it and never runs it, as B3 does. An OWED run
(the default population of a tree that enabled testing, or a `--filter` outside a sweep) must
select at least one test: ctest over zero tests prints "No tests were found!!!" and exits 0, so an
owed run is guarded by `ctest <selection> -N` first and then passes `--no-tests=error`.

Inside a create the run is a no-op under `tools.build:skip_test` (the consumer and vendor path,
which also prunes the test requirements), writes its JUnit file into the directory the conf
`user.malf:test_results` names (outside the build folder, which conan may move onto an existing
package revision's), and fails the create on any red.

Staged by malf beside global.conf in every conan home it uses (malf's conf sync, setup-build-env,
setup-proof-linux, setup-proof-msvc); global.conf names it as `user.malf:recipe_tests`, and a
recipe loads it with `runpy.run_path`. A home that lacks it fails the build, never skips the tests.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

CORPUS_LABEL = "corpus"


def selection(build_dir: str, *, corpus_only: bool = False, regex: str = "",
              verbose: bool = False) -> list[str]:
    """The ctest arguments of one run, before the population guard adds `--no-tests=error`."""
    arguments = ["--test-dir", build_dir, "--output-on-failure"]
    if verbose:
        arguments.append("--verbose")
    if regex:
        arguments += ["-R", regex]
    arguments += ["-L" if corpus_only else "-LE", CORPUS_LABEL]
    return arguments


def population_is_owed(build_dir: str, *, corpus_only: bool, regex: str, in_sweep: bool) -> bool:
    """Whether the run promises at least one test.

    note: inside a sweep a --filter is judged by no member, since a name that lives in one member
    is legitimately absent from the others, and the sweep does not sum the members' matches
    """
    if regex:
        return not in_sweep
    return not corpus_only and (Path(build_dir) / "CTestTestfile.cmake").is_file()


def selected_count(arguments: list[str]) -> int | None:
    """How many tests `arguments` select, from `ctest <arguments> -N`; None when ctest says no total."""
    listing = subprocess.run(["ctest", *arguments, "-N"], capture_output=True, text=True, check=False)
    totals = re.findall(r"^Total Tests: ([0-9]+)$", listing.stdout + listing.stderr, re.MULTILINE)
    return int(totals[-1]) if totals else None


def guarded(build_dir: str, arguments: list[str], *, owed: bool) -> tuple[list[str], str | None]:
    """The final arguments, or the reason an owed run selects nothing.

    post: an owed run that selects at least one test carries `--no-tests=error`
    """
    if not owed:
        return arguments, None
    total = selected_count(arguments)
    if total is None or total == 0:
        count = "no" if total is None else str(total)
        return arguments, f"{count} test(s) selected in {build_dir} by: {' '.join(arguments)}"
    return [*arguments, "--no-tests=error"], None


def run_tests(conanfile) -> None:
    """Run the default selection over the create's build folder; raise on any red.

    pre: called from `build()`, after `cmake.build()`
    post: under `tools.build:skip_test` nothing ran; otherwise the JUnit file
        `<user.malf:test_results>/<name>.xml` holds the run, or the build failed
    """
    from conan.errors import ConanException
    from conan.tools.build import cmd_args_to_string

    if conanfile.conf.get("tools.build:skip_test", check_type=bool):
        conanfile.output.info("malf: tools.build:skip_test is set, so no test runs in this build")
        return
    results = conanfile.conf.get("user.malf:test_results")
    if not results:
        raise ConanException("malf: the conf user.malf:test_results names no directory, so this "
                             "build's test results would land nowhere (DN-142.D5)")
    build_dir = str(conanfile.build_folder)
    arguments, empty = guarded(build_dir, selection(build_dir),
                               owed=population_is_owed(build_dir, corpus_only=False, regex="",
                                                       in_sweep=False))
    if empty is not None:
        raise ConanException(f"malf: {conanfile.name}: {empty} — a create that enabled testing "
                             "must run at least one test")
    if not (Path(build_dir) / "CTestTestfile.cmake").is_file():
        conanfile.output.info(f"malf: {conanfile.name} enabled no testing, so no test runs")
        return
    os.makedirs(results, exist_ok=True)
    junit = os.path.join(results, f"{conanfile.name}.xml")
    conanfile.output.info(f"malf: running the test selection of {conanfile.name}, JUnit to {junit}")
    conanfile.run(cmd_args_to_string(["ctest", *arguments, "--output-junit", junit]),
                  env=["conanbuild", "conanrun"])


def _desk_args(argv: list[str]) -> int:
    """`desk-args <out file> <build_dir> <corpus_only> <regex> <verbose> <in_sweep>`: the arguments
    `malf test` passes to ctest, NUL-separated into <out file>; exit 3 with the reason on stderr
    when an owed run selects nothing."""
    out_file, build_dir, corpus_only, regex, verbose, in_sweep = argv
    arguments, empty = guarded(
        build_dir,
        selection(build_dir, corpus_only=corpus_only == "true", regex=regex, verbose=verbose == "true"),
        owed=population_is_owed(build_dir, corpus_only=corpus_only == "true", regex=regex,
                                in_sweep=in_sweep == "true"))
    if empty is not None:
        print(f"malf test: {empty}", file=sys.stderr)
        return 3
    Path(out_file).write_bytes(b"".join(argument.encode() + b"\0" for argument in arguments))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 8 or sys.argv[1] != "desk-args":
        print("usage: malf_recipe_tests.py desk-args <out file> <build_dir> <corpus_only> "
              "<regex> <verbose> <in_sweep>", file=sys.stderr)
        sys.exit(2)
    sys.exit(_desk_args(sys.argv[2:]))
