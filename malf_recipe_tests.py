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

Inside a create the run is a no-op under `tools.build:skip_test`, writes its JUnit file into the
directory the conf `user.malf:test_results` names (outside the build folder, which conan may move
onto an existing package revision's), and fails the create on any red. A test_package's `test()`
runs the same selection through `run_test_package(self)` and writes a second result file,
`<tested name>.test_package.xml`, beside the first: the step's verdict has two populations
(DN-142.D13 (2)).

THE TWO CONF SETS OF A `conan create` are defined here and nowhere else (DN-142.D13 (1)). The
WRITER (the store's one writer, `malf cut-verify`, `malf store-create`) sets no skip conf: it
builds and runs every test and every test_package. The CONSUMER and vendor path (a
`--build=missing` rebuild, the vendor action, the third-party cache producer) sets
`tools.build:skip_test=True` and `--test-folder=`: no test runs and no test_package is built, while
the graph stays conan's default graph, test requirements expanded, so it computes the writer's
package id. `tools.graph:skip_test` is never set on a path that resolves a first-party package: it
prunes the test requirements, and a `test_requires` already in the graph merges into that node and
enters the declaring package's id, so pruning it gives metalog, eidos and sift another id. Every
caller reads its set through `create-args <writer|consumer>`.

A TEST LOCATES ITS INPUTS THROUGH A COMPILE DEFINITION, never through the translation unit's own
name (DN-142.D13 (3)). Inside a create malf's path map (`malf-path-map.cmake`, DN-142.D4 (a)) makes
`__FILE__`, `__builtin_FILE()` and `std::source_location::file_name()` the unit's identifier
(`./tests/...`), not a path, so a test reading a file beside itself passes on the desk and reds in
the create. Both runners refuse a test source that spells one of the three before any test runs;
`locators <dir>...` prints the same findings for a tree.

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
# DN-142.D13 (1): the writer's create sets no skip conf; the consumer's runs no test and builds no
# test_package, on conan's default graph. Never `tools.graph:skip_test`.
CREATE_ARGS: dict[str, tuple[str, ...]] = {
    "writer": (),
    "consumer": ("-c", "tools.build:skip_test=True", "--test-folder="),
}
# DN-142.D13 (3): the directories whose C++ sources are test sources, and the three spellings of a
# translation unit's own name that a create's path map turns into an identifier.
TEST_DIRECTORIES = frozenset({"tests", "tests_support", "test_package"})
CXX_SUFFIXES = frozenset({".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".cppm", ".ixx", ".inl"})
SKIPPED_DIRECTORIES = frozenset({".git", "__pycache__"})
LOCATOR_SPELLINGS = (
    ("__FILE__", re.compile(r"\b__FILE__\b")),
    ("__builtin_FILE()", re.compile(r"\b__builtin_FILE\s*\(")),
    ("std::source_location::file_name()", re.compile(r"\.\s*file_name\s*\(\s*\)")),
)
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


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


def _is_test_source(relative: Path, *, every_source: bool) -> bool:
    if relative.suffix not in CXX_SUFFIXES:
        return False
    return every_source or any(part in TEST_DIRECTORIES for part in relative.parts[:-1])


def locator_findings(root: str, *, every_source: bool = False) -> list[str]:
    """Each test source under `root` that spells a translation unit's own name, one
    `<path>:<line>: <spelling>` per site, path relative to `root`, sorted.

    note: comments are stripped before matching, so a comment naming a spelling is not a site; a
    `file_name()` call counts only in a file that names `source_location`
    """
    base = Path(root)
    findings: list[str] = []
    for directory, subdirectories, files in os.walk(base):
        subdirectories[:] = sorted(name for name in subdirectories
                                   if name not in SKIPPED_DIRECTORIES and not name.startswith("build"))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(base)
            if not _is_test_source(relative, every_source=every_source):
                continue
            text = COMMENT.sub(lambda hit: "\n" * hit.group(0).count("\n"),
                               path.read_text(encoding="utf-8", errors="replace"))
            for spelling, pattern in LOCATOR_SPELLINGS:
                if spelling.startswith("std::source_location") and "source_location" not in text:
                    continue
                for hit in pattern.finditer(text):
                    line = text.count("\n", 0, hit.start()) + 1
                    findings.append(f"{relative.as_posix()}:{line}: {spelling}")
    return sorted(findings)


def _refuse_locators(conanfile, root: str, *, every_source: bool) -> None:
    from conan.errors import ConanException

    findings = locator_findings(root, every_source=every_source)
    if findings:
        raise ConanException(
            f"malf: {conanfile.name or conanfile.tested_reference_str}: {len(findings)} test source "
            "site(s) locate a file through the translation unit's own name, which a create's path "
            "map makes an identifier, never a path (DN-142.D13 (3)); locate the input through a "
            "compile definition built from CMAKE_CURRENT_SOURCE_DIR instead:\n  "
            + "\n  ".join(findings))


def _results_directory(conanfile) -> str:
    from conan.errors import ConanException

    results = conanfile.conf.get("user.malf:test_results")
    if not results:
        raise ConanException("malf: the conf user.malf:test_results names no directory, so this "
                             "build's test results would land nowhere (DN-142.D5)")
    return results


def _junit(results: str, file_name: str) -> str:
    os.makedirs(results, exist_ok=True)
    return os.path.join(results, file_name)


def run_tests(conanfile) -> None:
    """Run the default selection over the create's build folder; raise on any red.

    pre: called from `build()`, after `cmake.build()`
    post: under `tools.build:skip_test` nothing ran; otherwise no exported test source spells a
        translation unit's own name, and the JUnit file `<user.malf:test_results>/<name>.xml`
        holds the run, or the build failed
    """
    from conan.errors import ConanException
    from conan.tools.build import cmd_args_to_string

    if conanfile.conf.get("tools.build:skip_test", check_type=bool):
        conanfile.output.info("malf: tools.build:skip_test is set, so no test runs in this build")
        return
    results = _results_directory(conanfile)
    _refuse_locators(conanfile, str(conanfile.source_folder), every_source=False)
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
    junit = _junit(results, f"{conanfile.name}.xml")
    conanfile.output.info(f"malf: running the test selection of {conanfile.name}, JUnit to {junit}")
    conanfile.run(cmd_args_to_string(["ctest", *arguments, "--output-junit", junit]),
                  env=["conanbuild", "conanrun"])


def run_test_package(conanfile) -> None:
    """Run a test_package's selection over its build folder; raise on any red.

    pre: called from a test_package recipe's `test()`
    post: under `tools.build:skip_test` nothing ran; otherwise no test_package source spells a
        translation unit's own name, and `<user.malf:test_results>/<tested name>.test_package.xml`
        holds a run of at least one test, or the create failed
    """
    from conan.errors import ConanException
    from conan.tools.build import can_run, cmd_args_to_string

    if conanfile.conf.get("tools.build:skip_test", check_type=bool):
        conanfile.output.info("malf: tools.build:skip_test is set, so no test_package test runs")
        return
    tested = str(conanfile.tested_reference_str).split("/", 1)[0]
    if not can_run(conanfile):
        raise ConanException(f"malf: the test_package of {tested} cannot run on this build "
                             "machine, and a writer's create never reports a test it did not run "
                             "(DN-142.D13 (2))")
    results = _results_directory(conanfile)
    _refuse_locators(conanfile, str(conanfile.source_folder), every_source=True)
    build_dir = str(conanfile.build_folder)
    arguments, empty = guarded(build_dir, selection(build_dir), owed=True)
    if empty is not None:
        raise ConanException(f"malf: the test_package of {tested}: {empty} — a test_package "
                             "must run at least one test")
    junit = _junit(results, f"{tested}.test_package.xml")
    conanfile.output.info(f"malf: running the test_package selection of {tested}, JUnit to {junit}")
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


def _create_args(argv: list[str]) -> int:
    """`create-args <writer|consumer>`: the conf set of that `conan create`, one argument per line."""
    if len(argv) != 1 or argv[0] not in CREATE_ARGS:
        print(f"usage: malf_recipe_tests.py create-args <{'|'.join(sorted(CREATE_ARGS))}>",
              file=sys.stderr)
        return 2
    for argument in CREATE_ARGS[argv[0]]:
        print(argument)
    return 0


def _locators(argv: list[str]) -> int:
    """`locators <dir>...`: every test source site under each <dir>, `<dir>/<path>:<line>:
    <spelling>`; exit 1 when there is one, 2 on a <dir> that is not a directory."""
    if not argv:
        print("usage: malf_recipe_tests.py locators <dir>...", file=sys.stderr)
        return 2
    missing = [directory for directory in argv if not Path(directory).is_dir()]
    if missing:
        print(f"malf_recipe_tests.py locators: not a directory: {' '.join(missing)}", file=sys.stderr)
        return 2
    findings = [f"{directory.rstrip('/')}/{finding}" for directory in argv
                for finding in locator_findings(directory)]
    for finding in findings:
        print(finding)
    print(f"locators: {len(findings)} test source site(s) spell a translation unit's own name "
          f"under {len(argv)} director{'y' if len(argv) == 1 else 'ies'}", file=sys.stderr)
    return 1 if findings else 0


VERBS = {"desk-args": (6, _desk_args), "create-args": (None, _create_args), "locators": (None, _locators)}

if __name__ == "__main__":
    verb = VERBS.get(sys.argv[1]) if len(sys.argv) > 1 else None
    if verb is None or (verb[0] is not None and len(sys.argv) - 2 != verb[0]):
        print("usage: malf_recipe_tests.py desk-args <out file> <build_dir> <corpus_only> <regex> "
              "<verbose> <in_sweep>\n       malf_recipe_tests.py create-args <writer|consumer>\n"
              "       malf_recipe_tests.py locators <dir>...", file=sys.stderr)
        sys.exit(2)
    sys.exit(verb[1](sys.argv[2:]))
