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
`<tested package id>.test_package.xml`, beside the first in the package's own directory
(`<results>/<name>/<package id>.xml`): the step's verdict has two populations (DN-142.D13 (2)).

THE THREE CONF SETS OF A `conan create` are defined here and nowhere else (DN-142.D13 (1),
DN-142.D31 (1)). The WRITER (step 0's `malf store-create`) sets no skip conf: it builds and runs
every test and every test_package. The SHIP-BENCH set is the ship form's when `MALF_BENCH` names a bench
document (the cut's bench measurement, DN-142.D33): no skip conf, so the test and bench targets are configured,
and `user.malf:bench_only`, so the build runs no test and only the named benchmarks. The SHIP form (a
candidate's `malf store-create --ship`) sets
`tools.build:skip_test=True` and keeps the test folder: the package's own tests are neither built
nor run (they are judged on clang, by the source verdict), and its test_package is built and run,
because `user.malf:run_test_package` tells `run_test_package` to run in place of `skip_test`. The
CONSUMER and vendor path (a `--build=missing` rebuild, the vendor action, the third-party cache
producer) sets `tools.build:skip_test=True` and `--test-folder=`: no test runs and no test_package
is built. All three keep conan's default graph, test requirements expanded, so each computes the
writer's package id. `tools.graph:skip_test` is never set on a path that resolves a first-party
package: it prunes the test requirements, and a `test_requires` already in the graph merges into
that node and enters the declaring package's id, so pruning it gives metalog, eidos and sift
another id. Every caller reads its set through `create-args <writer|ship|ship-bench|consumer>`.

A TEST LOCATES ITS INPUTS THROUGH A COMPILE DEFINITION, never through the translation unit's own
name (DN-142.D13 (3)). Inside a create malf's path map (`malf-path-map.cmake`, DN-142.D4 (a)) makes
`__FILE__`, `__builtin_FILE()` and `std::source_location::file_name()` the unit's identifier
(`./tests/...`), not a path, so a test reading a file beside itself passes on the desk and reds in
the create. Both runners refuse a test source that spells one of the three before any test runs;
`locators <dir>...` prints the same findings for a tree.

THE CORPUS LEGS (DN-142.D21 (1), (3)). When the conf `user.malf:corpus_gates` names a parameter
document holding an entry for the package, `run_tests` runs each of its legs after the default
selection, in order (`sample`, then `full`): one ctest over the `corpus`-labelled tests of the leg's
named suites, never the bare label, with the leg's mount variables set on that command and nowhere
else, JUnit to `<package id>.corpus-<leg>.xml`. A suite that selects no test, and a selected test
registered without `SKIP_REGULAR_EXPRESSION` (so a GTEST_SKIP would read as a pass), are rows of the
leg's sidecar `<package id>.corpus-<leg>.json`, with the run's exit status. A red leg is recorded,
never raised: the package was judged by its default selection, and the corpus verdict is a record of
its own. The document is written by step 0's Pharos; its paths never enter a key.

THE BENCHMARKS (DN-142.D25 (5) calls D and E). When the conf `user.malf:bench` names a parameter
document whose `packages` list holds the package, `run_tests` runs every benchmark executable the
build produced, after the tests and the corpus legs, each alone under the deadlock watchdog the conf
`user.malf:bench_watchdog` names, at BENCH_REPETITIONS repetitions with aggregates only, its stdin the
null device. Each writes its google-benchmark JSON to `<package id>.bench/<executable>.json`, and the
sidecar `<package id>.bench.json` holds `{repetitions, timeout, executables: [{name, exit}]}`. Nothing
is raised and nothing is written under the build or package folders: the bench is a verdict record of
its own (artefact_store's `bench_record`), and the package a create with the conf stores is the one a
create without it stores. A `user.*` conf is never a package-id input.

WHAT THE DEFAULT RUN ALSO RECORDS. `<package id>.corpus-listed.json`, every `corpus`-labelled test
name of the tree, listed and never run, so the corpus label closes against the registry from the
records alone. It is written once the tree enabled testing and selected at least one test, BEFORE the
selection runs, so it is also the create's PROMISE of a test verdict: the store's outcome expects a
verdict from every step whose results hold it, and a step that promised one and recorded none is never
served (artefact_store `expected_records`, DN-142.D31 (1)).

A SKIP IS A RED UNLESS THE PACKAGE DECLARES IT (DN-142.D23; the Founder, 2026-10-11). After a green
run, every `skipped` or `disabled` row of the default selection or a test_package must be covered by
a row of the package's `tests/declared_skips.json` naming the exact test, outcome and profile; a
test_package row is never covered. A malformed declaration, a profile with no file in
`malf/profiles/`, and, on an unfiltered run, a declared test no row of the run names (stale) are
reds too. Inside a create the profile is `user.malf:profile`, which every writer create carries;
tests that ran under no profile cannot be judged, and raise. The declaration is read from the
package's source folder, which in a create is the keyed export, so it enters the key with it.

NO TEST READS ITS CALLER'S STDIN. Every ctest run of tests this file starts, and `malf test`'s, has
stdin on the null device, because ctest hands its own stdin to each test: step 0 of 1.10.7 hung in a test that
offered an interactive prompt to the terminal its build inherited, which no keystroke could reach.

Staged by malf beside global.conf in every conan home it uses (malf's conf sync, setup-build-env,
setup-proof-linux, setup-proof-msvc); global.conf names it as `user.malf:recipe_tests`, and a
recipe loads it with `runpy.run_path`. A home that lacks it fails the build, never skips the tests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CORPUS_LABEL = "corpus"
CORPUS_GATES_CONF = "user.malf:corpus_gates"
BENCH_CONF = "user.malf:bench"
BENCH_WATCHDOG_CONF = "user.malf:bench_watchdog"
# note: the measurement the release's benchmark step took, moved into the create unchanged
BENCH_REPETITIONS = 5
BENCH_TIMEOUT_SECONDS = 1200
# note: the depth `malf bench` searches a build tree for its executables, `find -maxdepth 3`
BENCH_SEARCH_DEPTH = 3
BENCH_SUFFIX = ".bench"
# DN-142.D23: the declaration file of a package's skips, relative to its source root, and its row.
DECLARED_SKIPS = "tests/declared_skips.json"
SKIP_OUTCOMES = ("skipped", "disabled")
DECLARATION_KEYS = frozenset({"test", "outcome", "profiles", "condition", "why", "refs"})
JUDGED_POPULATIONS = ("build", "test_package")
PROFILE_CONF = "user.malf:profile"
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
# The property gtest_discover_tests sets, without which ctest reads a GTEST_SKIP as a pass.
SKIP_PROPERTY = "SKIP_REGULAR_EXPRESSION"
# DN-142.D31 (1): the conf under which a test_package runs although `tools.build:skip_test` is set.
TEST_PACKAGE_CONF = "user.malf:run_test_package"
# DN-142.D33 slice (a) ruling (1): the cut's bench measurement over a ship-form walk. The tree configures its
# test and bench targets (no `tools.build:skip_test`, whose BUILD_TESTING=OFF configures no bench target) and the
# build runs no test of its own, only the benchmarks `user.malf:bench` names.
BENCH_ONLY_CONF = "user.malf:bench_only"
# DN-142.D13 (1), DN-142.D31 (1): the writer's create sets no skip conf; the ship form builds and runs
# no test of its own and runs its test_package; the consumer's runs no test and builds no test_package;
# each on conan's default graph. Never `tools.graph:skip_test`.
CREATE_ARGS: dict[str, tuple[str, ...]] = {
    "writer": (),
    "ship": ("-c", "tools.build:skip_test=True", "-c", f"{TEST_PACKAGE_CONF}=True"),
    "ship-bench": ("-c", f"{BENCH_ONLY_CONF}=True", "-c", f"{TEST_PACKAGE_CONF}=True"),
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


def shown_tests(arguments: list[str]) -> list[dict]:
    """The tests `ctest <arguments>` selects, from `--show-only=json-v1`: each a dict with its
    `name` and `properties`. Raises RuntimeError when ctest cannot list them."""
    listed = subprocess.run(["ctest", *arguments, "--show-only=json-v1"], capture_output=True,
                            text=True, check=False, stdin=subprocess.DEVNULL)
    if listed.returncode != 0:
        raise RuntimeError(f"ctest {' '.join(arguments)} --show-only=json-v1 exited "
                           f"{listed.returncode}: {listed.stderr.strip()[-300:]}")
    return json.loads(listed.stdout).get("tests", [])


def junit_rows(path: Path, population: str) -> list[list[str]]:
    """`[population, test name, outcome]` per test case of one JUnit file; an outcome is `passed`,
    `failed`, `skipped` or `disabled`. The one reading of a JUnit outcome: artefact_store reads it
    from here."""
    import xml.etree.ElementTree as ElementTree

    rows = []
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        status = case.get("status")
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif status == "disabled":
            outcome = "disabled"
        elif case.find("skipped") is not None or status == "notrun":
            outcome = "skipped"
        else:
            outcome = "passed"
        rows.append([population, case.get("name") or "", outcome])
    return rows


def skip_message(path: Path, name: str) -> str:
    """The skip's own text for test `name` of one JUnit file, empty when it carries none."""
    import xml.etree.ElementTree as ElementTree

    for case in ElementTree.parse(path).getroot().iter("testcase"):
        if case.get("name") == name:
            skipped = case.find("skipped")
            text = (skipped.get("message") if skipped is not None else None) or ""
            output = case.findtext("system-out") or ""
            found = [line.strip() for line in output.splitlines() if "SKIPPED" in line or "Skipped" in line]
            return (text or (found[0] if found else ""))[:300]
    return ""


def read_declarations(source_dir: str | Path) -> list:
    """The rows of `<source dir>/tests/declared_skips.json`, or [] when the package declares none."""
    path = Path(source_dir) / DECLARED_SKIPS
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as unparsed:
        return [{"__unparsed__": f"{path}: {unparsed}"}]
    return document if isinstance(document, list) else [{"__unparsed__": f"{path}: not a JSON list"}]


def skip_findings(rows: list[list[str]], declarations: list, profile: str, unfiltered: bool,
                  profiles_dir: Path = PROFILES_DIR) -> tuple[list[str], list[list[str]], list[str]]:
    """(undeclared, excused, defects) of one run's rows `[population, name, outcome]` against the
    package's declarations (DN-142.D23 (2)).

    post: undeclared names each skipped or disabled row of the build or test_package population no
        declaration covers by exact (test, outcome, profile), a test_package row never covered;
        excused holds the covered rows; defects names each malformed row (a missing or unknown
        key, an empty why, a glob in test, an outcome outside the two, empty profiles), each
        profile with no file in malf's profiles, and, on an unfiltered run, each declared test
        no row of the run names
    """
    defects = []
    valid = []
    for index, row in enumerate(declarations):
        where = f"row {index}"
        if not isinstance(row, dict) or "__unparsed__" in row:
            defects.append(f"{where}: {row.get('__unparsed__') if isinstance(row, dict) else row!r}")
            continue
        keys = set(row)
        wrong = sorted(keys ^ DECLARATION_KEYS)
        if wrong:
            defects.append(f"{where}: missing or unknown key(s) {', '.join(wrong)}")
            continue
        test = row["test"]
        if not isinstance(test, str) or not test or any(char in test for char in "*?["):
            defects.append(f"{where}: test {test!r} is not one exact name (no glob)")
            continue
        if row["outcome"] not in SKIP_OUTCOMES:
            defects.append(f"{where} ({test}): outcome {row['outcome']!r} is neither skipped nor disabled")
            continue
        if not str(row["why"]).strip():
            defects.append(f"{where} ({test}): an empty why")
            continue
        if not isinstance(row["profiles"], list) or not row["profiles"]:
            defects.append(f"{where} ({test}): profiles is empty")
            continue
        unknown = [name for name in row["profiles"] if not (profiles_dir / str(name)).is_file()]
        if unknown:
            defects.append(f"{where} ({test}): no profile file for {', '.join(map(str, unknown))} "
                           f"in {profiles_dir}")
            continue
        valid.append(row)
    covered = {(row["test"], row["outcome"], name) for row in valid for name in row["profiles"]}
    undeclared, excused = [], []
    for population, name, outcome in rows:
        if population not in JUDGED_POPULATIONS or outcome not in SKIP_OUTCOMES:
            continue
        if population == "build" and (name, outcome, profile) in covered:
            excused.append([population, name, outcome])
        else:
            undeclared.append(f"{population} {name} {outcome}"
                              + (" (a test_package skip is never declared)" if population == "test_package" else ""))
    if unfiltered:
        ran = {name for _population, name, _outcome in rows}
        defects += [f"stale: {row['test']} is declared and no row of this run names it"
                    for row in valid if row["test"] not in ran]
    return undeclared, excused, defects


def _judge_run(conanfile, junit: str, population: str, source_dir: str) -> None:
    """Raise when the run's JUnit holds an undeclared skip or the declaration a defect."""
    from conan.errors import ConanException

    profile = conanfile.conf.get(PROFILE_CONF)
    if not profile:
        raise ConanException(f"malf: {conanfile.name or conanfile.tested_reference_str}: tests ran "
                             f"and {PROFILE_CONF} names no profile, so no skip of the run can be "
                             "judged (DN-142.D23)")
    rows = junit_rows(Path(junit), population)
    undeclared, excused, defects = skip_findings(rows, read_declarations(source_dir), profile,
                                                 unfiltered=True)
    for row in excused:
        conanfile.output.info(f"malf: EXCUSED declared skip {row[1]} ({row[2]}) on {profile}")
    if undeclared or defects:
        named = [f"{entry} — {skip_message(Path(junit), entry.split(' ')[1]) or 'no message'}"
                 for entry in undeclared] + defects
        raise ConanException(f"malf: {conanfile.name or conanfile.tested_reference_str}: "
                             f"{len(undeclared)} undeclared skip(s) and {len(defects)} declaration "
                             f"defect(s) on {profile} (DN-142.D23; declare in {DECLARED_SKIPS}):\n  "
                             + "\n  ".join(named))


def corpus_listing(build_dir: str) -> list[str]:
    """Every `corpus`-labelled test name of the tree, sorted; empty when it registers no CTest file."""
    if not (Path(build_dir) / "CTestTestfile.cmake").is_file():
        return []
    return sorted(test["name"] for test in shown_tests(["--test-dir", build_dir, "-L", CORPUS_LABEL]))


def corpus_regex(suites: list[str]) -> str:
    """The `-R` expression selecting exactly the named suites' tests."""
    return "^(" + "|".join(re.escape(suite) for suite in suites) + r")\."


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


def _junit(results: str, package: str, file_name: str) -> str:
    """`<results>/<package>/<file name>`: one directory per package, one file per package id.

    note: a create also builds the upstream binaries its graph lacks (`--build=missing`), each a
    variant another create never stored, and each runs its own tests; keyed by package id, the
    variant's result never overwrites the stored binary's
    """
    directory = os.path.join(results, package)
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, file_name)


def run_tests(conanfile) -> None:
    """Run the default selection over the create's build folder; raise on any red.

    pre: called from `build()`, after `cmake.build()`
    post: under `tools.build:skip_test` nothing ran; otherwise no exported test source spells a
        translation unit's own name, the JUnit file `<user.malf:test_results>/<name>/<package id>.xml`
        holds the run when the tree enabled testing, and a package `user.malf:bench` names ran its
        benchmarks whether or not it enabled testing; or the build failed
    """
    from conan.errors import ConanException

    if conanfile.conf.get(BENCH_ONLY_CONF, check_type=bool):
        conanfile.output.info("malf: the ship form's bench measurement runs no test, only the benchmarks "
                              "user.malf:bench names")
        run_benches(conanfile, str(conanfile.build_folder), _results_directory(conanfile),
                    conanfile.info.package_id())
        return
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
    package_id = conanfile.info.package_id()
    if not (Path(build_dir) / "CTestTestfile.cmake").is_file():
        # note: a package with no test still runs its benchmarks; insight_canon_bench, test-less, stored no bench at step 0 run 38869897758
        conanfile.output.info(f"malf: {conanfile.name} enabled no testing, so no test runs")
        run_benches(conanfile, build_dir, results, package_id)
        return
    try:
        listed = corpus_listing(build_dir)
    except RuntimeError as unlisted:
        raise ConanException(f"malf: {conanfile.name}: {unlisted}") from unlisted
    Path(_junit(results, conanfile.name, f"{package_id}.corpus-listed.json")).write_text(
        json.dumps(listed, indent=1) + "\n")
    junit = _junit(results, conanfile.name, f"{package_id}.xml")
    conanfile.output.info(f"malf: running the test selection of {conanfile.name}, JUnit to {junit}")
    conanfile.run(ctest_command(arguments, junit), env=["conanbuild", "conanrun"])
    _judge_run(conanfile, junit, "build", str(conanfile.source_folder))
    run_corpus_legs(conanfile, build_dir, results, package_id)
    run_benches(conanfile, build_dir, results, package_id)


def bench_executables(build_dir: str) -> list[Path]:
    """Every benchmark executable of a build tree, sorted: a file within BENCH_SEARCH_DEPTH levels
    whose name holds `bench`, executable, and neither a script nor a library (`malf bench`'s
    discovery)."""
    root = Path(build_dir)
    found = []
    for directory, subdirectories, files in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        if depth + 1 >= BENCH_SEARCH_DEPTH:
            subdirectories[:] = []
        for name in files:
            path = Path(directory) / name
            if "bench" in name and not name.endswith((".sh", ".so", ".a")) and os.access(path, os.X_OK):
                found.append(path)
    return sorted(found)


def bench_packages(conanfile) -> list[str]:
    """The packages the `user.malf:bench` document names; [] when the conf is unset."""
    document_path = conanfile.conf.get(BENCH_CONF)
    if not document_path:
        return []
    return list(json.loads(Path(document_path).read_text()).get("packages", []))


def run_benches(conanfile, build_dir: str, results: str, package_id: str) -> None:
    """The benchmarks of a package the `user.malf:bench` document names, each its own run under the
    watchdog; an exit is recorded in the sidecar, never raised (DN-142.D25 (5) call D).

    post: `<package id>.bench.json` holds `{repetitions, timeout, executables: [{name, exit}]}` and
        each run that exited wrote `<package id>.bench/<name>.json`; nothing is written elsewhere
    """
    from conan.errors import ConanException
    from conan.tools.build import cmd_args_to_string

    if conanfile.name not in bench_packages(conanfile):
        return
    watchdog = conanfile.conf.get(BENCH_WATCHDOG_CONF)
    if not watchdog or not Path(watchdog).is_file():
        raise ConanException(f"malf: {conanfile.name}: {BENCH_CONF} names this package and "
                             f"{BENCH_WATCHDOG_CONF} names no watchdog script ({watchdog!r}), so a "
                             "hung benchmark would hold the walk to the runner's limit")
    outputs = Path(_junit(results, conanfile.name, f"{package_id}{BENCH_SUFFIX}"))
    outputs.mkdir(parents=True, exist_ok=True)
    ran = []
    for executable in bench_executables(build_dir):
        out = outputs / f"{executable.name}.json"
        command = cmd_args_to_string([
            "bash", watchdog, str(BENCH_TIMEOUT_SECONDS), "--", str(executable),
            f"--benchmark_out={out}", "--benchmark_out_format=json",
            f"--benchmark_repetitions={BENCH_REPETITIONS}", "--benchmark_report_aggregates_only=true"])
        conanfile.output.info(f"malf: benchmark {executable.name} of {conanfile.name}, JSON to {out}")
        status = conanfile.run(f"{command} < {os.devnull}", env=["conanbuild", "conanrun"],
                               ignore_errors=True)
        ran.append({"name": executable.name, "exit": status})
    Path(_junit(results, conanfile.name, f"{package_id}{BENCH_SUFFIX}.json")).write_text(
        json.dumps({"repetitions": BENCH_REPETITIONS, "timeout": BENCH_TIMEOUT_SECONDS,
                    "executables": ran}, indent=1, sort_keys=True) + "\n")


def run_corpus_legs(conanfile, build_dir: str, results: str, package_id: str) -> None:
    """The legs `user.malf:corpus_gates` names for this package, each its own ctest run; a red leg
    is recorded in its JUnit file and sidecar, never raised (DN-142.D21 (1)).

    post: for each leg, `<package id>.corpus-<leg>.json` holds `{leg, suites, exit, rows}`, `exit`
        the ctest status (None when no suite selected a test) and `rows` the `absent` and
        `unguarded` rows; `<package id>.corpus-<leg>.xml` holds the run when one happened
    """
    from conan.errors import ConanException
    from conan.tools.build import cmd_args_to_string

    document_path = conanfile.conf.get(CORPUS_GATES_CONF)
    if not document_path:
        return
    legs = json.loads(Path(document_path).read_text()).get("packages", {}).get(conanfile.name)
    if not legs:
        return
    if os.name != "posix":
        raise ConanException(f"malf: {conanfile.name}: the corpus legs set their mounts through "
                             "`env`, which this platform lacks; step 0 runs them on Linux only")
    for leg in legs:
        name, suites, environment = leg["leg"], leg["suites"], leg["environment"]
        population = f"corpus-{name}"
        selected = [suite for suite in suites
                    if (selected_count(["--test-dir", build_dir, "-L", CORPUS_LABEL, "-R",
                                        corpus_regex([suite])]) or 0) > 0]
        rows = [[population, f"{suite}.*", "absent"] for suite in suites if suite not in selected]
        status = None
        if selected:
            arguments = ["--test-dir", build_dir, "--output-on-failure", "-L", CORPUS_LABEL,
                         "-R", corpus_regex(selected)]
            try:
                shown = shown_tests(arguments)
            except RuntimeError as unlisted:
                raise ConanException(f"malf: {conanfile.name}: {unlisted}") from unlisted
            rows += [[population, test["name"], "unguarded"] for test in shown
                     if SKIP_PROPERTY not in {item.get("name") for item in test.get("properties", [])}]
            junit = _junit(results, conanfile.name, f"{package_id}.{population}.xml")
            mounts = [f"{variable}={value}" for variable, value in sorted(environment.items())]
            conanfile.output.info(f"malf: running the {population} leg of {conanfile.name} "
                                  f"({', '.join(selected)}), JUnit to {junit}")
            command = (f"{cmd_args_to_string(['env', *mounts])} "
                       f"{ctest_command(arguments, junit)}")
            status = conanfile.run(command, env=["conanbuild", "conanrun"], ignore_errors=True)
        Path(_junit(results, conanfile.name, f"{package_id}.{population}.json")).write_text(
            json.dumps({"leg": name, "suites": suites, "exit": status, "rows": rows},
                       indent=1, sort_keys=True) + "\n")


def ctest_command(arguments: list[str], junit: str) -> str:
    """The shell command of a create's ctest run: its stdin is the null device, never the create's.

    note: `ConanFile.run` takes no stdin, so the redirection rides the command it runs.
    """
    from conan.tools.build import cmd_args_to_string

    return f"{cmd_args_to_string(['ctest', *arguments, '--output-junit', junit])} < {os.devnull}"


def run_test_package(conanfile) -> None:
    """Run a test_package's selection over its build folder; raise on any red.

    pre: called from a test_package recipe's `test()`
    post: under `tools.build:skip_test` without `user.malf:run_test_package` nothing ran; otherwise
        no test_package source spells a
        translation unit's own name, and
        `<user.malf:test_results>/<tested name>/<tested package id>.test_package.xml` holds a run
        of at least one test, or the create failed
    """
    from conan.errors import ConanException
    from conan.tools.build import can_run

    if conanfile.conf.get("tools.build:skip_test", check_type=bool) \
            and not conanfile.conf.get(TEST_PACKAGE_CONF, check_type=bool):
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
    junit = _junit(results, tested,
                   f"{conanfile.dependencies[tested].pref.package_id}.test_package.xml")
    conanfile.output.info(f"malf: running the test_package selection of {tested}, JUnit to {junit}")
    conanfile.run(ctest_command(arguments, junit), env=["conanbuild", "conanrun"])
    _judge_run(conanfile, junit, "test_package", str(conanfile.source_folder))


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
    """`create-args <writer|ship|ship-bench|consumer>`: the conf set of that `conan create`, one per line."""
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


def tree_source_dir(build_dir: str) -> str | None:
    """A build tree's source root, `CMAKE_HOME_DIRECTORY` from its CMakeCache.txt; None if absent."""
    cache = Path(build_dir) / "CMakeCache.txt"
    if not cache.is_file():
        return None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CMAKE_HOME_DIRECTORY:"):
            return line.split("=", 1)[1]
    return None


def _judge_skips(argv: list[str]) -> int:
    """`judge-skips <junit> <source dir> <profile> <filtered>`: `malf test`'s judgment of a desk run;
    exit 1 naming each undeclared skip and defect, 0 otherwise, each excused row printed. An empty
    source dir declares nothing; a JUnit ctest did not write is a run of no test."""
    junit, source_dir, profile, filtered = argv
    if not profile:
        print("malf test: no profile was resolved, so no skip can be judged (DN-142.D23)", file=sys.stderr)
        return 1
    if not Path(junit).is_file():
        print(f"malf test: ctest wrote no JUnit at {junit}: no test ran, so no skip either",
              file=sys.stderr)
        return 0
    rows = junit_rows(Path(junit), "build")
    declarations = read_declarations(source_dir) if source_dir else []
    undeclared, excused, defects = skip_findings(rows, declarations, profile,
                                                 unfiltered=filtered != "true")
    for row in excused:
        print(f"malf test: EXCUSED declared skip {row[1]} ({row[2]}) on {profile}")
    for entry in undeclared:
        print(f"malf test: UNDECLARED skip {entry} — "
              f"{skip_message(Path(junit), entry.split(' ')[1]) or 'no message'} "
              f"(declare it in {source_dir}/{DECLARED_SKIPS}, DN-142.D23)", file=sys.stderr)
    for defect in defects:
        print(f"malf test: declaration defect in {source_dir}/{DECLARED_SKIPS}: {defect}", file=sys.stderr)
    return 1 if undeclared or defects else 0


def _tree_source(argv: list[str]) -> int:
    """`tree-source <build dir>`: the tree's CMAKE_HOME_DIRECTORY, exit 1 when it has none."""
    found = tree_source_dir(argv[0])
    if found is None:
        return 1
    print(found)
    return 0


VERBS = {"desk-args": (6, _desk_args), "create-args": (None, _create_args), "locators": (None, _locators),
         "judge-skips": (4, _judge_skips), "tree-source": (1, _tree_source)}

if __name__ == "__main__":
    verb = VERBS.get(sys.argv[1]) if len(sys.argv) > 1 else None
    if verb is None or (verb[0] is not None and len(sys.argv) - 2 != verb[0]):
        print("usage: malf_recipe_tests.py desk-args <out file> <build_dir> <corpus_only> <regex> "
              "<verbose> <in_sweep>\n       malf_recipe_tests.py create-args <writer|ship|consumer>\n"
              "       malf_recipe_tests.py locators <dir>...\n"
              "       malf_recipe_tests.py judge-skips <junit> <source dir> <profile> <filtered>\n"
              "       malf_recipe_tests.py tree-source <build dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(verb[1](sys.argv[2:]))
