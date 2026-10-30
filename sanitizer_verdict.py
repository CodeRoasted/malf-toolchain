#!/usr/bin/env python3
"""sanitizer_verdict — a candidate's sanitizer gates, each judging its own repository's sources in the build
environment, each leaving ONE verdict document its candidate's store job ingests (DN-142.D33, the cut tools'
input roots and the sanitizer gates).

Usage:
    sanitizer_verdict.py asan-suite <repository> <package dir> <verdict.json>
    sanitizer_verdict.py fuzz-replay <repository> <package dir> <binary> <corpus dir> <verdict.json>
    sanitizer_verdict.py valgrind-replay <repository> <driver> <corpus dir> <verdict.json>
    sanitizer_verdict.py selftest

Every act runs through `malf source-act` (the dev leg's container shape, the repository's upstreams exported into
the act's profile home), its output printed live. The verdict document is written in every case, red included, so
a red job's reader has it; exit 0 the verdict `pass`, 1 `fail` or `timeout` (the gate judged and found a defect),
2 `unjudged` (nothing was judged: an act refused, a build failed, a run that cannot say what it measured).

WHERE THIS CAME FROM. Until 2026-10-30 the three gates were the superproject's cut (`pharos build`'s A4:
`canon_asan_suite`, `sanitizer_replay_gate`, `keyed_sanitizers`), compiling in the release seat's workspace. The
Founder's ruling recorded in DN-142.D33 moved them into the repositories' candidates; their predicates are carried
here unchanged:

  asan-suite — `malf test <package dir> --asan` under REPLAY_SANITIZER_ENV, so a UBSan report halts its test.
    Red on an exit other than 0. ctest's summary (`tests failed out of N`) is what says a test ran: a suite that
    reports none, or zero, judged nothing (a green over nothing, DN-108.D5).

  fuzz-replay — `malf build <package dir> --asan` (its upstreams compiled from source at the ASan profile), then
    the bounded replay of the committed seeds, `timeout --kill-after=10 120 <binary> <corpus>`, under
    REPLAY_SANITIZER_ENV. 124 and 137 are a replay past its bound (`timeout`); 126 and 127 `timeout`'s own failure
    to execute (unjudged). A green exit is judged by its POPULATION: the binary's closing
    `parse_fuzzer replay: N input(s) processed` line, the LAST one printed, N at least the committed seeds and
    above zero, else it judged less than its subject (DN-108.D14). A red names the seed the binary named last
    (`parse_fuzzer replay: input <path>`), the one it was on when it stopped — a committed, retrievable input.

  valgrind-replay — the repository's own driver (`fuzz/scripts/valgrind_replay.sh`) at the ship profile, the
    upstream candidates' stored gcc packages RESTORED into its home (`source-act --restore-upstreams`), never
    compiled again; the driver builds the repository's own packages and its non-sanitizer replay binary at that
    profile (the candidate's own ship form does not exist before its store job, which waits on this gate). The
    driver's exit contract: 0 green, 1 a memcheck error or a red replay, 2 nothing judged. A green is judged by
    the driver's `=== memcheck over N corpus inputs ===` line, N the committed seeds and above zero.
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

MALF = Path(__file__).resolve().parent / "malf"
ASAN_PROFILE = "linux-clang21-asan"
SHIP_PROFILE = "linux-gcc16-release"
REPLAY_SANITIZER_ENV = {
    "ASAN_OPTIONS": "abort_on_error=1:halt_on_error=1:detect_leaks=1:print_stats=0",
    "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
}
REPLAY_BOUND_SECONDS = 120
REPLAY_KILL_GRACE_SECONDS = 10
REPLAY_TIMED_OUT = 124
REPLAY_KILLED = 137
REPLAY_UNEXECUTED = (126, 127)
VALGRIND_RED = 1
TAIL_LINES = 200
PASS, FAIL, TIMEOUT, UNJUDGED = "pass", "fail", "timeout", "unjudged"
EXIT_OF = {PASS: 0, FAIL: 1, TIMEOUT: 1, UNJUDGED: 2}
# The lines a verdict reads, kept over the WHOLE stream: a long report after them would push them out of the tail.
WATCHED = {
    "started": re.compile(r"^--- source-act: "),
    "ctest": re.compile(r"tests failed out of (\d+)"),
    "counted": re.compile(r"parse_fuzzer replay: (\d+) input\(s\) processed"),
    "input": re.compile(r"parse_fuzzer replay: input (.+)$"),
    "memcheck": re.compile(r"=== memcheck over (\d+) corpus inputs ==="),
}
WATCHED_KEPT = 50


@dataclass
class Ran:
    """One act's exit, its output's last TAIL_LINES lines, and the last WATCHED_KEPT lines each WATCHED pattern
    matched over the whole stream."""
    code: int
    tail: list[str] = field(default_factory=list)
    found: dict[str, list[str]] = field(default_factory=dict)

    def started(self) -> bool:
        return bool(self.found.get("started"))

    def last(self, name: str) -> re.Match | None:
        lines = self.found.get(name) or []
        return WATCHED[name].search(lines[-1]) if lines else None

    def output_after_start(self) -> list[str]:
        """The tail's lines after `source-act`'s own line: what the command printed."""
        marker = max((index for index, line in enumerate(self.tail) if WATCHED["started"].search(line)), default=-1)
        return self.tail[marker + 1:]


Runner = Callable[[Sequence[str]], Ran]


def run_live(argv: Sequence[str]) -> Ran:
    """`argv` with its merged output printed as it comes, its tail and WATCHED lines kept."""
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    found: dict[str, collections.deque[str]] = {}
    print(f"=== sanitizer_verdict: {' '.join(argv)} ===", flush=True)
    with subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          errors="replace", bufsize=1) as process:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            print(line, flush=True)
            tail.append(line)
            for name, pattern in WATCHED.items():
                if pattern.search(line):
                    found.setdefault(name, collections.deque(maxlen=WATCHED_KEPT)).append(line)
    return Ran(process.returncode, list(tail), {name: list(lines) for name, lines in found.items()})


def act(run: Runner, repository: Path, profile: str, command: Sequence[str], restore: bool = False) -> Ran:
    """One command through `malf source-act` at `profile`."""
    return run([str(MALF), "source-act", "--profile", profile, *(["--restore-upstreams"] if restore else []),
                str(repository), "--", *command])


def sanitized(command: Sequence[str]) -> list[str]:
    """`command` under REPLAY_SANITIZER_ENV, set by `env` inside the container."""
    return ["env", *(f"{name}={value}" for name, value in sorted(REPLAY_SANITIZER_ENV.items())), *command]


def committed_seeds(corpus: Path) -> int:
    """The seed files the corpus holds: a candidate's checkout is its commit's tree, so the files ARE the seeds."""
    return sum(1 for path in corpus.rglob("*") if path.is_file())


def document(gate: str, subject: str, predicate: str, profile: str, verdict: str, reason: str,
             exits: dict, counts: dict, **extra: object) -> dict:
    return {"gate": gate, "subject": subject, "predicate": predicate, "profile": profile,
            "env": REPLAY_SANITIZER_ENV if profile == ASAN_PROFILE else {}, "exits": exits, "counts": counts,
            "verdict": verdict, "reason": reason, **extra}


def asan_suite(run: Runner, repository: Path, package: str) -> dict:
    """Canon core's suite under ASan/UBSan (`malf test <package> --asan`), judged by its exit and ctest's count."""
    def verdict(state: str, reason: str, code: int | None, tests: int | None) -> dict:
        return document("asan-suite", package, "malf test --asan", ASAN_PROFILE, state, reason,
                        {"suite": code}, {"tests": tests})

    suite = act(run, repository, ASAN_PROFILE, sanitized([str(MALF), "test", package, "--asan"]))
    if not suite.started():
        return verdict(UNJUDGED, f"malf source-act refused before the suite ran (exit {suite.code})", None, None)
    counts = [int(match.group(1)) for line in suite.found.get("ctest") or []
              if (match := WATCHED["ctest"].search(line))]
    tests = sum(counts) if counts else None
    if suite.code != 0:
        return verdict(FAIL, f"malf test --asan exited {suite.code} over {tests} test(s); the sanitizer's report, "
                             f"when there is one, is printed above", suite.code, tests)
    if not tests:
        return verdict(UNJUDGED, "the suite exited 0 and ctest reported no test: a green over nothing", 0, tests)
    return verdict(PASS, f"{tests} test(s) ran under ASan/UBSan, 0 failed", 0, tests)


def fuzz_replay(run: Runner, repository: Path, package: str, binary: str, corpus: str) -> dict:
    """The ASan build of `package`, then the bounded replay of the committed seeds under ASan/UBSan."""
    seeds = committed_seeds(repository / corpus) if (repository / corpus).is_dir() else 0
    exits: dict[str, int | None] = {"build": None, "bin-dir": None, "replay": None}
    counts: dict[str, int | None] = {"seeds": seeds, "replayed": None}

    def verdict(state: str, reason: str, **extra: object) -> dict:
        return document("fuzz-replay", package, f"{binary} replay", ASAN_PROFILE, state, reason, exits, counts,
                        bound_seconds=REPLAY_BOUND_SECONDS, **extra)

    if seeds == 0:
        return verdict(UNJUDGED, f"the seed corpus {corpus} holds ZERO committed seeds: a replay over no seed is a "
                                 f"green over nothing (DN-108.D14)")
    built = act(run, repository, ASAN_PROFILE, [str(MALF), "build", package, "--asan"])
    exits["build"] = built.code if built.started() else None
    if not built.started() or built.code != 0:
        return verdict(UNJUDGED, f"the ASan build of {package} failed (exit {built.code}), so no seed was judged")
    located = act(run, repository, ASAN_PROFILE, [str(MALF), "bin-dir", "--asan", package])
    exits["bin-dir"] = located.code if located.started() else None
    printed = [line.strip() for line in located.output_after_start() if line.strip()]
    if not located.started() or located.code != 0 or not printed:
        return verdict(UNJUDGED, f"malf bin-dir --asan {package} named no directory (exit {located.code})")
    harness = str(Path(printed[-1]) / binary)
    replayed = act(run, repository, ASAN_PROFILE, sanitized(
        ["timeout", f"--kill-after={REPLAY_KILL_GRACE_SECONDS}", str(REPLAY_BOUND_SECONDS), harness, corpus]))
    if not replayed.started() or replayed.code in REPLAY_UNEXECUTED:
        exits["replay"] = replayed.code if replayed.started() else None
        return verdict(UNJUDGED, f"the replay did not execute {harness} (exit {replayed.code})")
    exits["replay"] = replayed.code
    seed = replayed.last("input")
    named = seed.group(1).strip() if seed else None
    if replayed.code in (REPLAY_TIMED_OUT, REPLAY_KILLED):
        return verdict(TIMEOUT, f"the replay ran past its {REPLAY_BOUND_SECONDS} s bound (exit {replayed.code})"
                                + (f" on the committed seed {named}" if named else ""), input=named)
    if replayed.code != 0:
        return verdict(FAIL, f"{binary} exited {replayed.code}"
                             + (f" on the committed seed {named}" if named else " before it named any input")
                             + "; the ASan/UBSan report, when there is one, is printed above", input=named)
    closing = replayed.last("counted")
    if closing is None:
        return verdict(UNJUDGED, f"{binary} exited 0 and printed no replay count: a step that cannot say what it "
                                 f"measured has no verdict (DN-108.D5)")
    counts["replayed"] = int(closing.group(1))
    if counts["replayed"] < seeds or counts["replayed"] == 0:
        return verdict(UNJUDGED, f"the replay covered {counts['replayed']} input(s) of the {seeds} committed "
                                 f"seed(s): a green over a subject it never saw")
    return verdict(PASS, f"{binary} replayed {counts['replayed']} committed seed(s) under ASan/UBSan, bounded at "
                         f"{REPLAY_BOUND_SECONDS} s")


def valgrind_replay(run: Runner, repository: Path, driver: str, corpus: str) -> dict:
    """The repository's valgrind driver at the ship profile, over the upstreams' restored gcc packages."""
    seeds = committed_seeds(repository / corpus) if (repository / corpus).is_dir() else 0
    exits: dict[str, int | None] = {"driver": None}
    counts: dict[str, int | None] = {"seeds": seeds, "memchecked": None}

    def verdict(state: str, reason: str) -> dict:
        return document("valgrind-replay", driver, "valgrind memcheck replay", SHIP_PROFILE, state, reason,
                        exits, counts)

    if seeds == 0:
        return verdict(UNJUDGED, f"the seed corpus {corpus} holds ZERO committed seeds: a replay over no seed is a "
                                 f"green over nothing (DN-108.D14)")
    replay = act(run, repository, SHIP_PROFILE, ["env", f"MALF={MALF}", "bash", driver, corpus], restore=True)
    if not replay.started():
        return verdict(UNJUDGED, f"malf source-act refused before the driver ran (exit {replay.code})")
    exits["driver"] = replay.code
    if replay.code == VALGRIND_RED:
        return verdict(FAIL, "valgrind reported an uninitialised read or an invalid access, or the replay was red "
                             "(the driver exited 1); valgrind's report is printed above")
    if replay.code != 0:
        return verdict(UNJUDGED, f"the driver judged nothing (exit {replay.code}: no valgrind, no corpus, or its "
                                 f"build failed)")
    memchecked = replay.last("memcheck")
    counts["memchecked"] = int(memchecked.group(1)) if memchecked else None
    if counts["memchecked"] != seeds:
        return verdict(UNJUDGED, f"the driver memchecked {counts['memchecked']} input(s), not the {seeds} committed "
                                 f"seed(s)")
    return verdict(PASS, f"memcheck over {seeds} committed seed(s): no uninitialised read, no invalid access")


def write(verdict: dict, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"=== sanitizer_verdict: {verdict['gate']} {verdict['verdict'].upper()} — {verdict['reason']} ===",
          flush=True)
    return EXIT_OF[verdict["verdict"]]


def selftest() -> int:
    """Every verdict of the three gates over a scripted runner: what each act printed and exited."""
    failures: list[str] = []
    armed = [0]

    def expect(what: str, got: object, wanted: object) -> None:
        armed[0] += 1
        if got != wanted:
            failures.append(f"{what}: got {got!r}, wanted {wanted!r}")

    def scripted(*acts: tuple[int, list[str]]) -> tuple[Runner, list[list[str]]]:
        calls: list[list[str]] = []
        queue = list(acts)

        def runner(argv: Sequence[str]) -> Ran:
            calls.append(list(argv))
            code, lines = queue.pop(0)
            found: dict[str, list[str]] = {}
            for line in lines:
                for name, pattern in WATCHED.items():
                    if pattern.search(line):
                        found.setdefault(name, []).append(line)
            return Ran(code, lines[-TAIL_LINES:], found)
        return runner, calls

    start = "--- source-act: profile, running command ---"
    with tempfile.TemporaryDirectory(prefix="sanitizer_verdict.selftest.") as scratch:
        repo = Path(scratch) / "repo"
        (repo / "fuzz" / "corpus").mkdir(parents=True)
        for seed in ("a", "b", "c"):
            (repo / "fuzz" / "corpus" / seed).write_text(seed)
        (repo / "empty").mkdir()

        # asan-suite
        runner, calls = scripted((0, [start, "100% tests passed, 0 tests failed out of 861"]))
        green = asan_suite(runner, repo, "core")
        expect("asan-suite: a green suite with tests is a pass, its count kept",
               (green["verdict"], green["counts"], green["exits"]), (PASS, {"tests": 861}, {"suite": 0}))
        expect("asan-suite: the act is `malf test core --asan` under the sanitizer environment, at the ASan profile",
               calls[0][1:4] + calls[0][-3:] + [calls[0][6]],
               ["source-act", "--profile", ASAN_PROFILE, "test", "core", "--asan", "env"])
        runner, _ = scripted((8, [start, "94% tests passed, 48 tests failed out of 861"]))
        expect("asan-suite: RED on an exit other than 0", asan_suite(runner, repo, "core")["verdict"], FAIL)
        runner, _ = scripted((0, [start, "No tests were found!!!"]))
        expect("asan-suite: a 0 exit with no ctest summary is unjudged (a green over nothing)",
               asan_suite(runner, repo, "core")["verdict"], UNJUDGED)
        runner, _ = scripted((0, [start, "100% tests passed, 0 tests failed out of 0"]))
        expect("asan-suite: zero tests counted is unjudged", asan_suite(runner, repo, "core")["verdict"], UNJUDGED)
        runner, _ = scripted((2, ["malf source-act: REFUSED before the command"]))
        expect("asan-suite: a refused act is unjudged, its exit not read as the suite's",
               (lambda verdict: (verdict["verdict"], verdict["exits"]))(asan_suite(runner, repo, "core")),
               (UNJUDGED, {"suite": None}))

        # fuzz-replay
        build_ok, bindir_ok = (0, [start, "built"]), (0, [start, "/repo/fuzz/build-asan"])

        def replay(*acts: tuple[int, list[str]]) -> tuple[dict, list[list[str]]]:
            runner, calls = scripted(*acts)
            return fuzz_replay(runner, repo, "fuzz", "parse_fuzzer", "fuzz/corpus"), calls

        counted = [start, "parse_fuzzer replay: input fuzz/corpus/a", "parse_fuzzer replay: 3 input(s) processed"]
        green, calls = replay(build_ok, bindir_ok, (0, counted))
        expect("fuzz-replay: a full population is a pass", (green["verdict"], green["counts"]),
               (PASS, {"seeds": 3, "replayed": 3}))
        expect("fuzz-replay: the replay is bounded by timeout and runs the binary bin-dir named over the corpus",
               calls[2][-5:], ["timeout", "--kill-after=10", "120", "/repo/fuzz/build-asan/parse_fuzzer",
                               "fuzz/corpus"])
        expect("fuzz-replay: the build is `malf build fuzz --asan`", calls[0][-3:], ["build", "fuzz", "--asan"])
        expect("fuzz-replay: the LAST count wins (a closing 0 is not hidden by an earlier 3)",
               replay(build_ok, bindir_ok, (0, [start, "parse_fuzzer replay: 3 input(s) processed",
                                                "parse_fuzzer replay: 0 input(s) processed"]))[0]["verdict"], UNJUDGED)
        expect("fuzz-replay: fewer inputs than committed seeds is unjudged",
               (lambda verdict: (verdict["verdict"], verdict["counts"]))(
                   replay(build_ok, bindir_ok, (0, [start, "parse_fuzzer replay: 2 input(s) processed"]))[0]),
               (UNJUDGED, {"seeds": 3, "replayed": 2}))
        expect("fuzz-replay: a 0 exit with no replay count is unjudged",
               replay(build_ok, bindir_ok, (0, [start, "INFO: Done 141 runs"]))[0]["verdict"], UNJUDGED)
        red = replay(build_ok, bindir_ok, (1, [start, "parse_fuzzer replay: input fuzz/corpus/a",
                                               "parse_fuzzer replay: input fuzz/corpus/b", "==1==ERROR: AddressSanitizer"]))[0]
        expect("fuzz-replay: a sanitizer abort is a fail naming the seed it was on",
               (red["verdict"], red["input"]), (FAIL, "fuzz/corpus/b"))
        expect("fuzz-replay: exit 124 is a timeout", replay(build_ok, bindir_ok, (124, [start]))[0]["verdict"], TIMEOUT)
        expect("fuzz-replay: exit 137 (killed past the grace) is a timeout",
               replay(build_ok, bindir_ok, (137, [start]))[0]["verdict"], TIMEOUT)
        expect("fuzz-replay: exit 127 (timeout could not execute it) is unjudged",
               replay(build_ok, bindir_ok, (127, [start]))[0]["verdict"], UNJUDGED)
        failed_build, calls = replay((1, [start, "error: cc1plus"]))
        expect("fuzz-replay: a failed ASan build judged no seed, and nothing after it ran",
               (failed_build["verdict"], failed_build["exits"]["build"], len(calls)), (UNJUDGED, 1, 1))
        expect("fuzz-replay: a bin-dir that printed no directory is unjudged",
               replay(build_ok, (0, [start]))[0]["verdict"], UNJUDGED)
        runner, calls = scripted()
        empty = fuzz_replay(runner, repo, "fuzz", "parse_fuzzer", "empty")
        expect("fuzz-replay: ZERO committed seeds is unjudged and runs no act", (empty["verdict"], calls),
               (UNJUDGED, []))

        # valgrind-replay
        def valgrind(code: int, lines: list[str]) -> tuple[dict, list[list[str]]]:
            runner, calls = scripted((code, lines))
            return valgrind_replay(runner, repo, "fuzz/scripts/valgrind_replay.sh", "fuzz/corpus"), calls

        green, calls = valgrind(0, [start, "=== memcheck over 3 corpus inputs ===", "PASS"])
        expect("valgrind-replay: the driver green over every seed is a pass", (green["verdict"], green["counts"]),
               (PASS, {"seeds": 3, "memchecked": 3}))
        expect("valgrind-replay: the driver runs at the ship profile over the restored upstreams, with this malf",
               (calls[0][1:5], calls[0][-5:]),
               (["source-act", "--profile", SHIP_PROFILE, "--restore-upstreams"],
                ["env", f"MALF={MALF}", "bash", "fuzz/scripts/valgrind_replay.sh", "fuzz/corpus"]))
        expect("valgrind-replay: exit 1 is a fail", valgrind(1, [start])[0]["verdict"], FAIL)
        expect("valgrind-replay: exit 2 (nothing judged) is unjudged", valgrind(2, [start])[0]["verdict"], UNJUDGED)
        expect("valgrind-replay: a green over fewer inputs than the seeds is unjudged",
               valgrind(0, [start, "=== memcheck over 2 corpus inputs ==="])[0]["verdict"], UNJUDGED)
        expect("valgrind-replay: a refused act is unjudged", valgrind(2, ["REFUSED"])[0]["exits"], {"driver": None})

        # the document and the exit
        out = Path(scratch) / "out" / "verdict.json"
        code = write(green, out)
        expect("write: a pass exits 0 and the document reads back whole",
               (code, json.loads(out.read_text())), (0, green))
        expect("write: fail and timeout exit 1, unjudged 2",
               [EXIT_OF[state] for state in (FAIL, TIMEOUT, UNJUDGED)], [1, 1, 2])
        expect("the ASan document names the sanitizer environment, the ship one none",
               (asan_suite(scripted((0, [start, "0 tests failed out of 1"]))[0], repo, "core")["env"], green["env"]),
               (REPLAY_SANITIZER_ENV, {}))

    for failure in failures:
        print(f"sanitizer_verdict selftest FAILED: {failure}")
    print(f"sanitizer_verdict selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    if len(argv) == 4 and argv[0] == "asan-suite":
        return write(asan_suite(run_live, Path(argv[1]).resolve(), argv[2]), Path(argv[3]))
    if len(argv) == 6 and argv[0] == "fuzz-replay":
        return write(fuzz_replay(run_live, Path(argv[1]).resolve(), argv[2], argv[3], argv[4]), Path(argv[5]))
    if len(argv) == 5 and argv[0] == "valgrind-replay":
        return write(valgrind_replay(run_live, Path(argv[1]).resolve(), argv[2], argv[3]), Path(argv[4]))
    print(__doc__.split("\n\n", 2)[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
