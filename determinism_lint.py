#!/usr/bin/env python3
"""determinism_lint — a repository's deterministic-content core names no clock and no std <random>
facility it cannot own. Two gates over ONE subject, each repository judging its own sources.

Usage:
    determinism_lint.py wallclock [--repo DIR]   # the clock gate over DIR (default: the cwd)
    determinism_lint.py random    [--repo DIR]   # the RNG gate over DIR
    determinism_lint.py selftest                 # every control, refusal and finding, over fixtures

Exit 0 is PASS or NOT A SUBJECT (said as such, never printed as PASS), 1 is a finding, 2 is a
REFUSAL: the gate could not judge, and a gate that cannot see its subject never passes.

WHERE THIS CAME FROM. Until 2026-10-30 both gates were superproject Pharos check modules
(`wallclock`, `random_determinism`) holding a hand-written SCOPE over five repositories. The
Founder's ruling recorded in `DN-142.D33` ("each repo should be responsible from it's lint, except
if the check is cross-repo by nature") moved them here: malf's `coderoast-preflight.yml` runs both
over the calling repository, and each repository declares its own scope in its own `packages.yml`.
What stayed cross-repo — the contract's opt-out budget summed over the repositories, and the one
ruled gate exemption — is the superproject's `determinism_allocation` check module. This file
never reads the superproject.

THE TWO GATES (the determinism bible §7 for the clock, §5 M7/M9 for the RNG).

  wallclock — the deterministic-content core performs ZERO hidden wall-clock reads; the clock
    enters only through one explicitly annotated, injected live seam. Banned: `*_clock::now()`
    (system / steady / high_resolution / utc / tai / gps / file / local), `std::time(...)` and a
    bare `time(nullptr|NULL|0|&x)`, `gettimeofday()`, `clock_gettime()`, `timespec_get()`. A STATIC
    gate is required beside the differential-replay oracle: the oracle sees an UNCACHED read that
    diverges between two replays and is structurally blind to a CACHED one (canon once cached the
    wall-clock YEAR in a `thread_local`; same-day replays agree, a cross-year replay would not).

  random — two arms, two reasons. G1 (`ADR-31.D7`): the std DISTRIBUTIONS and the shuffles — their
    engine-bits -> value mapping is implementation-defined, so libstdc++ and libc++ draw different
    values from the same engine state and the cross-stdlib diagonal breaks silently. G2
    (`ADR-31.D9`): the STATEFUL std engines, the engine adaptors, `std::random_device` and
    `std::seed_seq` — a stateful engine's next value is a function of how many draws preceded it,
    so content becomes a function of consumption history; CounterRng addresses a draw by position.
    Permitted, never matched: the portable logcraft::core types and CounterRng (no `std::`).
    NOT MATCHED IS NOT ENDORSED: C++26's `std::philox_engine` is counter-based and deliberately
    unmatched by G2. WHAT NEITHER ARM SEES: a CounterRng seeked with a value derived from
    consumption (`seek(draws_so_far)`) passes both arms and violates `ADR-31.D9` exactly; a green
    here is a statement about NAMES and nothing else.

THE OPT-OUT. `DETERMINISM-ALLOW(<gate>: <reason>)` on the site's own line, or anywhere in the
contiguous comment block directly above it (the walk stops at the first line that is not a pure
comment). It rides inside a `note:` — `// note: DETERMINISM-ALLOW(wallclock: <reason>): <why>` —
because a bare marker is not a form the comment-contract grammar admits (`ADR-26.D5`). THE GATE KEY
IS THE FIRST FIELD AND IT IS NOT DECORATION: each gate matches its own full prefix and neither
string contains the other, so an annotation written for one gate cannot exempt the other, and a
marker with an unknown or missing gate key exempts neither. The same-line test reads the line
with its STRING LITERALS EMPTIED and its comments kept: a marker inside a literal is not a marker,
and the comment-stripped line would exempt nothing at all.

THE BUDGET. An annotated site spends a slot of the contract's budget: ONE wall-clock live seam,
ZERO G1 exceptions, ONE G2 exception (LogCraft's non-deterministic-mode seed). Those caps are this
file's constants (`CONTRACT_CAPS`), never an invocation's. A repository declares the slots it
spends (`opt_outs:`, default 0, at most the cap); more annotated sites than it declares is a
finding (TOO-MANY-SEAMS / TOO-MANY-OPT-OUTS), a declaration above the cap is a refusal, and the
superproject sums the declarations across repositories against the same caps. A slot beyond the
cap is a contract change (Daidalos), never an annotation.

THE DECLARATION — a top-level block of the repository's own `packages.yml`, block style only:

    determinism:
      roots:                       # the deterministic-content core: every C/C++ unit under each
        - core/src                 # root is judged by BOTH gates — one subject, one root list
      exempt:                      # optional: a gate this repository is not judged by, and why
        wallclock: "<a sentence>"
      out_of_scope:                # every declared build target no root reaches, and why
        <target name>: "<a sentence>"
      opt_outs:                    # optional: the budget slots this repository spends
        wallclock: 1               # keys: wallclock, random_g1, random_g2

or, for a repository holding no deterministic-content core, `determinism: {not_a_subject: ...}` as
one indented `not_a_subject: "<a sentence>"` line. A repository whose `packages.yml` carries no
`determinism:` block REFUSES: a gate armed over a repository that never answered the question
would otherwise pass over nothing.

WHAT REFUSES (never a verdict on the tree): a `--repo` that is not a directory; a failed control;
an EMPTY register (a gate or an arm left with no pattern — a register rots by SHRINKING too); a
`packages.yml` that cannot be read, absent included; no `determinism:` block, or one of a shape
this reader does not take; an exemption, an out-of-scope row or a not-a-subject declaration whose
reason is not a sentence; both gates exempted (that is `not_a_subject`); an out-of-scope row naming
a target the repository does not declare, a target a root already reaches, or an `inventory:`
target (a determinism-PROOF harness is covered, never excluded — a clock read or a stateful std
engine inside one is the defect, not the method); an `opt_outs:` key that is not a slot, or a
budget above the contract cap; a scope ROOT the reach holds no file under (an ignored build tree
does not keep a root alive: no commit carries it); a declared build target no root reaches and no
out-of-scope row names; a root list that matched ZERO files; an unreadable index, or a tracked
source the sweep could not read (per key).

THE COVERAGE COMPARISON. A hand-written root list has one armed direction from inside (a root that
stops existing refuses); the other — a target nobody added — is armed only against a fact outside
the list: every BUILD TARGET the same `packages.yml` declares, conan packages under `packages:` and
build targets under `inventory:` alike, must be reached by a root or named out of scope. What it
still cannot reach is a directory belonging to NO declared target at all.

THE SWEEP. The six suffixes `.cpp .hpp .h .cc .cxx .cppm`, over the REACH — tracked plus
untracked-not-ignored, never the raw disk. Excluded by DIRECTORY component, never by substring:
`test`, `tests`, `benchmark`, `benchmarks`, `test_package`, `.git`, `build`, `vendor`,
`third_party`, `_deps`, `build-*`, `build_*`, and file names containing `_test.`. The floor is what
git tracks under the roots minus the same exclusions; a tracked file the sweep did not read is
named and refuses.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

SCRIPT = "malf/determinism_lint.py"
MANIFEST = "packages.yml"
BLOCK = "determinism"
SUFFIXES = (".cpp", ".hpp", ".h", ".cc", ".cxx", ".cppm")
EXCLUDED_DIRS = frozenset(("test", "tests", "benchmark", "benchmarks", "test_package",
                           ".git", "build", "vendor", "third_party", "_deps"))
EXCLUDED_DIR_PREFIXES = ("build-", "build_")
EXCLUDED_FILE_TOKENS = ("_test.",)
GIT_AMBIENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
               "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX")
# A reason is a sentence: the shortest reason the retired modules' rows ever carried was eight words.
MIN_REASON_WORDS = 8

WALLCLOCK, RANDOM = "wallclock", "random"
GATES = (WALLCLOCK, RANDOM)
G1, G2 = "G1", "G2"
# The budget slots: (gate, arm) -> the `opt_outs:` key, and the contract's cap on each.
SLOT_OF = {(WALLCLOCK, WALLCLOCK): "wallclock", (RANDOM, G1): "random_g1", (RANDOM, G2): "random_g2"}
CONTRACT_CAPS = {"wallclock": 1, "random_g1": 0, "random_g2": 1}
DECLARATION_KEYS = ("roots", "exempt", "out_of_scope", "opt_outs", "not_a_subject")

BAN, SEAMS, TOO_MANY = "BAN", "TOO-MANY-SEAMS", "TOO-MANY-OPT-OUTS"
VIOLATION, REFUSAL = "violation", "refusal"


# ── The registers. ─────────────────────────────────────────────────────────────────────────────
Banned = tuple[tuple[str, str, re.Pattern[str]], ...]

# Tightly anchored so `runtime(`, `lifetime(`, `std::timed_mutex` or a bare `now()` never match.
WALLCLOCK_BANNED: Banned = (
    (WALLCLOCK, "*_clock::now",
     re.compile(r"\b(?:system|steady|high_resolution|utc|tai|gps|file|local)_clock"
                r"\s*::\s*now\s*\(")),
    (WALLCLOCK, "std::time(", re.compile(r"\bstd\s*::\s*time\s*\(")),
    (WALLCLOCK, "time(<clock-arg>)", re.compile(r"\btime\s*\(\s*(?:nullptr|NULL|0|&)")),
    (WALLCLOCK, "gettimeofday(", re.compile(r"\bgettimeofday\s*\(")),
    (WALLCLOCK, "clock_gettime(", re.compile(r"\bclock_gettime\s*\(")),
    (WALLCLOCK, "timespec_get(", re.compile(r"\btimespec_get\s*\(")),
)

# Every pattern is anchored to `std::`, so the portable logcraft::core types and CounterRng never
# match. G1: the distribution family by its `_distribution` suffix, the algorithms by a trailing
# `(`. G2: whole words — `\b` keeps `std::mt19937` off `std::mt19937_64` and `std::seed_seq` off
# `seed_seq_len`. `philox_engine` is deliberately absent (the docstring says why).
_DISTRIBUTIONS = (
    "uniform_int|uniform_real|bernoulli|binomial|negative_binomial|geometric|"
    "poisson|exponential|gamma|weibull|extreme_value|normal|lognormal|"
    "chi_squared|cauchy|fisher_f|student_t|discrete|piecewise_constant|"
    "piecewise_linear"
)
_STATEFUL_ENGINES = (
    "mt19937_64|mt19937|minstd_rand0|minstd_rand|ranlux24_base|ranlux24|"
    "ranlux48_base|ranlux48|knuth_b|default_random_engine|"
    "linear_congruential_engine|mersenne_twister_engine|subtract_with_carry_engine|"
    "discard_block_engine|independent_bits_engine|shuffle_order_engine|"
    "random_device|seed_seq"
)
RANDOM_BANNED: Banned = (
    (G1, "std::*_distribution", re.compile(rf"\bstd\s*::\s*(?:{_DISTRIBUTIONS})_distribution\b")),
    (G1, "std::shuffle(", re.compile(r"\bstd\s*::\s*(?:ranges\s*::\s*)?shuffle\s*\(")),
    (G1, "std::sample(", re.compile(r"\bstd\s*::\s*(?:ranges\s*::\s*)?sample\s*\(")),
    (G1, "std::generate_canonical", re.compile(r"\bstd\s*::\s*generate_canonical\b")),
    (G2, "std stateful engine / adaptor / random_device / seed_seq",
     re.compile(rf"\bstd\s*::\s*(?:{_STATEFUL_ENGINES})\b")),
)


@dataclass(frozen=True)
class Gate:
    name: str
    arms: tuple[str, ...]
    banned: Banned
    marker: str
    sibling_marker: str

    def slot(self, arm: str) -> str:
        return SLOT_OF[(self.name, arm)]


GATE_OF = {
    WALLCLOCK: Gate(WALLCLOCK, (WALLCLOCK,), WALLCLOCK_BANNED,
                    "DETERMINISM-ALLOW(wallclock:", "DETERMINISM-ALLOW(random:"),
    RANDOM: Gate(RANDOM, (G1, G2), RANDOM_BANNED,
                 "DETERMINISM-ALLOW(random:", "DETERMINISM-ALLOW(wallclock:"),
}


# ── Three renderings of a line. ────────────────────────────────────────────────────────────────
def blank_string_literals(lines: list[str]) -> list[str]:
    """Each line with its double-quoted string literals emptied and its COMMENTS KEPT — the only
    rendering in which a same-line marker can be told from the same bytes inside a literal.
    Escaped quotes are honoured; a literal spanning lines is not a shape these sources carry."""
    return [re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', line) for line in lines]


def strip_comments(lines: list[str]) -> list[str]:
    """Per-line code with `//` and `/* */` comments removed, line numbers preserved. Naive on `//`
    inside a string literal, on purpose: the banned tokens never appear in one in this workspace,
    and reading a header's prose about its own subject as a violation would make the gate useless."""
    out: list[str] = []
    in_block = False
    for raw in lines:
        buf: list[str] = []
        i, n = 0, len(raw)
        while i < n:
            if in_block:
                end = raw.find("*/", i)
                if end == -1:
                    i = n
                    break
                in_block = False
                i = end + 2
                continue
            two = raw[i: i + 2]
            if two == "/*":
                in_block = True
                i += 2
                continue
            if two == "//":
                break
            buf.append(raw[i])
            i += 1
        out.append("".join(buf))
    return out


@dataclass(frozen=True)
class Hit:
    repo: str
    relpath: str
    lineno: int
    arm: str
    pattern: str
    matched: str
    code: str
    allowed: bool

    @property
    def where(self) -> str:
        return f"{self.repo}/{self.relpath}:{self.lineno}"


def allowed_by_annotation(marker: str, idx: int, raw_lines: list[str], code_lines: list[str],
                          marker_lines: list[str] | None = None) -> bool:
    """`marker` on the site's own line (string literals emptied) or anywhere in the contiguous
    comment block directly above it (raw lines: pure comments by construction)."""
    lines = blank_string_literals(raw_lines) if marker_lines is None else marker_lines
    if marker in lines[idx]:
        return True
    i = idx - 1
    while i >= 0:
        pure_comment = code_lines[i].strip() == "" and raw_lines[i].strip() != ""
        if not pure_comment:
            break
        if marker in raw_lines[i]:
            return True
        i -= 1
    return False


def scan_text(gate: Gate, repo: str, relpath: str, text: str,
              banned: Banned | None = None) -> list[Hit]:
    """One hit per line at most: the first banned pattern that matches the code rendering."""
    banned = gate.banned if banned is None else banned
    raw_lines = text.splitlines()
    code_lines = strip_comments(raw_lines)
    marker_lines = blank_string_literals(raw_lines)
    hits: list[Hit] = []
    for idx, code in enumerate(code_lines):
        for arm, name, pattern in banned:
            if (found := pattern.search(code)) is not None:
                allowed = allowed_by_annotation(gate.marker, idx, raw_lines, code_lines, marker_lines)
                matched = re.sub(r"\s+", "", found.group(0))
                hits.append(Hit(repo, relpath, idx + 1, arm, name, matched, code.strip(), allowed))
                break
    return hits


def first_match(snippet: str, banned: Banned) -> tuple[str, str] | None:
    """(arm, label) of the first pattern that matches — the resolution `scan_text` uses."""
    for arm, name, pattern in banned:
        if pattern.search(snippet):
            return arm, name
    return None


# ── The repository's reach and its tracked floor. ─────────────────────────────────────────────
class CannotJudge(Exception):
    """A REFUSAL: (message, the path wanted, the DO lines). Never a pass."""

    def __init__(self, message: str, where: Path | str, do: tuple[str, ...]) -> None:
        super().__init__(message)
        self.where = str(where)
        self.do = do


def git_env(repo: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in GIT_AMBIENT}
    env["GIT_CEILING_DIRECTORIES"] = str(repo.resolve().parent)
    return env


def excluded(rel: str) -> bool:
    """By DIRECTORY component (a name or a `build-`/`build_` prefix) or a token of the FILE name,
    over the repository-relative path — never a substring of the path."""
    parts = rel.split("/")
    for component in parts[:-1]:
        if component in EXCLUDED_DIRS or component.startswith(EXCLUDED_DIR_PREFIXES):
            return True
    return any(token in parts[-1] for token in EXCLUDED_FILE_TOKENS)


def path_components(rel: str) -> tuple[str, ...]:
    return tuple(part for part in rel.strip("/").split("/") if part not in ("", "."))


def reaches(root: str, target_path: str) -> bool:
    """A root touches a target's directory when either contains the other, by COMPONENT."""
    swept, declared = path_components(root), path_components(target_path)
    return swept[:len(declared)] == declared or declared[:len(swept)] == swept


def pathspecs(roots: Iterable[str], suffixes: Iterable[str]) -> tuple[str, ...]:
    specs: list[str] = []
    for root in roots:
        prefix = "" if root in (".", "") else root.strip("/") + "/"
        for suffix in suffixes:
            specs.append(f":(glob){prefix}**/*{suffix}")
    return tuple(specs)


def tracked_sources_do(repo: Path, roots: Iterable[str]) -> str:
    specs = " ".join(f"'{spec}'" for spec in pathspecs(roots, SUFFIXES))
    return f"git -C {repo} ls-files -- {specs}   # the source files git tracks there"


def tracked_sources(repo: Path, roots: Iterable[str]) -> list[str]:
    """Every source `repo` TRACKS under `roots`, minus the exclusions — the floor's fact."""
    roots = tuple(roots)
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--",
                          *pathspecs(roots, SUFFIXES)],
                         capture_output=True, check=False, env=git_env(repo))
    if out.returncode != 0:
        raise CannotJudge(
            f"git ls-files failed in {repo} (exit {out.returncode}): "
            f"{out.stderr.decode(errors='replace').strip() or '<no stderr>'} — the floor is the "
            f"count of source files this repository tracks under {', '.join(roots)}, and without "
            f"an index there is no such count (a checkout without .git, a tarball export)",
            repo, (tracked_sources_do(repo, roots),
                   f"git -C {repo} status   # is this a repository at all?"))
    found = sorted(rel.decode(errors="replace") for rel in out.stdout.split(b"\0") if rel)
    return [rel for rel in found if not excluded(rel)]


def reach_files(repo: Path, specs: tuple[str, ...]) -> list[str]:
    """Tracked plus untracked-not-ignored, present on disk — never the raw disk."""
    done = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard", "--", *specs],
                          capture_output=True, check=False, env=git_env(repo))
    if done.returncode != 0:
        raise CannotJudge(
            f"git cannot list the reach of {repo} (exit {done.returncode}): "
            f"{done.stderr.decode(errors='replace').strip() or '<no stderr>'} — an unread tree "
            f"is never an empty one", repo,
            (f"git -C {repo} ls-files --cached --others --exclude-standard   # what the reach holds",
             f"git -C {repo} status   # is this a repository at all?"))
    names = {name for name in done.stdout.decode("utf-8", "surrogateescape").split("\0")
             if name and not name.endswith("/")}
    return sorted(name for name in names if os.path.lexists(repo / name))


def dead_roots(repo: Path, roots: tuple[str, ...]) -> list[str]:
    """The roots under which the reach holds no file: a root an ignored build tree keeps alive on
    the raw disk is dead, because no commit carries that tree."""
    held = [path_components(rel) for rel in reach_files(repo, pathspecs(roots, ("",)))]
    dead: list[str] = []
    for root in roots:
        swept = path_components(root)
        if not any(len(parts) > len(swept) and parts[:len(swept)] == swept for parts in held):
            dead.append(root)
    return dead


def dead_roots_do(repo: Path, dead: Iterable[str]) -> str:
    return (f"git -C {repo} ls-files --cached --others --exclude-standard -- {' '.join(dead)}"
            f"   # empty: nothing a commit can carry is there")


def walk_sources(repo: Path, roots: tuple[str, ...]) -> list[tuple[Path, str]]:
    found: dict[str, Path] = {}
    specs = pathspecs(roots, SUFFIXES)
    for rel in reach_files(repo, specs) if specs else ():
        path = repo / rel
        if path.is_file() and not excluded(rel):
            found[rel] = path
    return [(found[rel], rel) for rel in sorted(found)]


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ── The manifest: declared build targets and the `determinism:` block. ─────────────────────────
PACKAGES_BLOCK, INVENTORY_BLOCK = "packages", "inventory"
DECLARING_BLOCKS = (PACKAGES_BLOCK, INVENTORY_BLOCK)


@dataclass(frozen=True)
class Declared:
    name: str
    path: str
    block: str


class DeclarationError(ValueError):
    """The manifest's shape is one this reader does not take — a refusal, never a guess."""


def declared_targets(text: str, repo_name: str) -> dict[str, Declared]:
    """Every BUILD TARGET the manifest declares: two-space keys under a top-level `packages:` or
    `inventory:` block, each with its four-space `path:`. A target with no `path:` raises: its
    directory unknown, it cannot be compared with a root, and assuming `.` would cover everything."""
    entries: list[tuple[str, str]] = []
    paths: dict[tuple[str, str], str] = {}
    block = ""
    current = ""
    for raw in text.split("\n"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            stripped = line.strip()
            if stripped.startswith(f"{PACKAGES_BLOCK}:") and stripped != f"{PACKAGES_BLOCK}:":
                raise DeclarationError(
                    f"{repo_name}: `{stripped}` — the flow form of `packages:` is not the shape "
                    f"this reader takes, and reading it as no block would drop every package")
            block = stripped[:-1] if stripped[:-1] in DECLARING_BLOCKS and stripped.endswith(":") \
                else ""
            current = ""
            continue
        if not block:
            continue
        if indent == 2 and line.strip().endswith(":"):
            current = line.strip()[:-1]
            if (block, current) not in paths and (block, current) not in entries:
                entries.append((block, current))
            continue
        if indent == 4 and current and line.strip().startswith("path:"):
            paths[(block, current)] = line.strip()[len("path:"):].strip().strip("'\"") or "."
    missing = [f"{name} (under `{blk}:`)" for blk, name in entries if not paths.get((blk, name))]
    if missing:
        raise DeclarationError(
            f"{repo_name}: {len(missing)} declared build target(s) carry no `path:` — "
            f"{', '.join(missing)}. A target whose directory is unknown cannot be compared with a "
            f"source root, and assuming `.` would mark the whole repository as covered")
    return {name: Declared(name, paths[(blk, name)], blk) for blk, name in entries}


@dataclass(frozen=True)
class Declaration:
    roots: tuple[str, ...] = ()
    exempt: dict[str, str] = field(default_factory=dict)
    out_of_scope: dict[str, str] = field(default_factory=dict)
    opt_outs: dict[str, int] = field(default_factory=dict)
    not_a_subject: str = ""


def _strip_comment(raw: str) -> str:
    """The line without a `#` comment that starts outside a quoted scalar."""
    quote = ""
    for index, char in enumerate(raw):
        if quote:
            if char == quote and raw[index - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or raw[index - 1].isspace()):
            return raw[:index].rstrip()
    return raw.rstrip()


def _scalar(text: str, where: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if text[0] == '"' else inner
    if text[:1] in "\"'[{":
        raise DeclarationError(f"{where}: `{text}` is not a plain or a closed quoted scalar")
    return text


def parse_declaration(text: str) -> Declaration | None:
    """The top-level `determinism:` block, or None when the manifest carries none. PURE; the shape
    is the one the docstring declares, block style only, and anything else raises."""
    lines = text.split("\n")
    start = [i for i, raw in enumerate(lines) if _strip_comment(raw).startswith(f"{BLOCK}:")]
    if not start:
        return None
    if len(start) > 1:
        raise DeclarationError(f"`{BLOCK}:` is declared {len(start)} times")
    head = _strip_comment(lines[start[0]])
    if head != f"{BLOCK}:":
        raise DeclarationError(f"`{head}` — the `{BLOCK}:` block is written in block style, its "
                               f"keys on the indented lines below it")
    values: dict[str, object] = {}
    key = ""
    for number, raw in enumerate(lines[start[0] + 1:], start=start[0] + 2):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        where = f"{MANIFEST}:{number}"
        if indent == 0:
            break
        body = line.strip()
        if indent == 2:
            name, colon, rest = body.partition(":")
            if not colon or name not in DECLARATION_KEYS:
                raise DeclarationError(f"{where}: `{body}` — the `{BLOCK}:` block takes "
                                       f"{', '.join(DECLARATION_KEYS)} and nothing else")
            if name in values:
                raise DeclarationError(f"{where}: `{name}:` is declared twice")
            key = name
            if name == "not_a_subject":
                values[name] = _scalar(rest, where)
            elif rest.strip():
                raise DeclarationError(f"{where}: `{body}` — `{name}:` takes its entries on the "
                                       f"indented lines below it")
            else:
                values[name] = [] if name == "roots" else {}
            continue
        if indent != 4 or key in ("", "not_a_subject"):
            raise DeclarationError(f"{where}: `{body}` sits at an indent the `{BLOCK}:` block does "
                                   f"not take (two spaces for a key, four for its entries)")
        held = values[key]
        if key == "roots":
            if not body.startswith("- "):
                raise DeclarationError(f"{where}: `{body}` — a `roots:` entry is a `- <dir>` line")
            assert isinstance(held, list)
            held.append(_scalar(body[2:], where).strip("/") or ".")
            continue
        entry, colon, rest = body.partition(":")
        entry = entry.strip()
        if not colon or not entry or not rest.strip():
            raise DeclarationError(f"{where}: `{body}` — a `{key}:` entry is `<name>: <value>`")
        assert isinstance(held, dict)
        if entry in held:
            raise DeclarationError(f"{where}: `{entry}` is declared twice under `{key}:`")
        value = _scalar(rest, where)
        if key == "opt_outs":
            if not value.isdigit():
                raise DeclarationError(f"{where}: `{body}` — an `opt_outs:` budget is a whole number")
            held[entry] = int(value)
        else:
            held[entry] = value
    return Declaration(roots=tuple(values.get("roots", ())),  # type: ignore[arg-type]
                       exempt=dict(values.get("exempt", {})),  # type: ignore[arg-type]
                       out_of_scope=dict(values.get("out_of_scope", {})),  # type: ignore[arg-type]
                       opt_outs=dict(values.get("opt_outs", {})),  # type: ignore[arg-type]
                       not_a_subject=str(values.get("not_a_subject", "")))


def is_sentence(reason: str) -> bool:
    return len(reason.split()) >= MIN_REASON_WORDS


def declaration_faults(declaration: Declaration, targets: dict[str, Declared]) -> list[str]:
    """Every way the declaration fails to be one: a reason that is not a sentence, a gate or slot
    this file does not know, a budget above the contract's cap, an out-of-scope row guarding
    nothing (undeclared, already reached, an `inventory:` harness)."""
    faults: list[str] = []
    if declaration.not_a_subject:
        others = [k for k, v in (("roots", declaration.roots), ("exempt", declaration.exempt),
                                 ("out_of_scope", declaration.out_of_scope),
                                 ("opt_outs", declaration.opt_outs)) if v]
        if others:
            faults.append(f"`not_a_subject:` is declared beside {', '.join(others)} — a repository "
                          f"is a subject or it is not")
        if not is_sentence(declaration.not_a_subject):
            faults.append(f"`not_a_subject:` carries `{declaration.not_a_subject}`, not a sentence "
                          f"of at least {MIN_REASON_WORDS} words")
        return faults
    if not declaration.roots:
        faults.append("`roots:` names no directory — declare the deterministic-content core, or "
                      "`not_a_subject:` with its reason")
    for gate, reason in sorted(declaration.exempt.items()):
        if gate not in GATES:
            faults.append(f"`exempt:` names `{gate}`, which is not a gate ({', '.join(GATES)})")
        elif not is_sentence(reason):
            faults.append(f"`exempt: {gate}:` carries `{reason}`, not a sentence of at least "
                          f"{MIN_REASON_WORDS} words")
    if set(GATES) <= set(declaration.exempt):
        faults.append("`exempt:` names both gates — a repository judged by neither is "
                      "`not_a_subject:`, said once with its reason")
    for slot, budget in sorted(declaration.opt_outs.items()):
        if slot not in CONTRACT_CAPS:
            faults.append(f"`opt_outs:` names `{slot}`, which is not a budget slot "
                          f"({', '.join(CONTRACT_CAPS)})")
        elif budget > CONTRACT_CAPS[slot]:
            faults.append(f"`opt_outs: {slot}: {budget}` exceeds the contract's cap of "
                          f"{CONTRACT_CAPS[slot]} — a slot beyond the cap is a contract change "
                          f"(Daidalos, the determinism bible), never a declaration")
        elif budget and any(gate in declaration.exempt for (gate, _), name in SLOT_OF.items()
                            if name == slot):
            faults.append(f"`opt_outs: {slot}` spends a slot of a gate this repository is exempt "
                          f"from")
    for name, reason in sorted(declaration.out_of_scope.items()):
        target = targets.get(name)
        if target is None:
            faults.append(f"`out_of_scope:` names `{name}`, a build target this {MANIFEST} does "
                          f"not declare — a row guarding nothing")
            continue
        if target.block == INVENTORY_BLOCK:
            faults.append(f"`out_of_scope:` names `{name}`, an `inventory:` build target — a "
                          f"determinism-proof harness is COVERED by a root, never excluded: a "
                          f"clock read or a stateful std engine inside one is the defect")
        if any(reaches(root, target.path) for root in declaration.roots):
            faults.append(f"`out_of_scope:` names `{name}` ({target.path}), which a root already "
                          f"reaches — the row is dead")
        if not is_sentence(reason):
            faults.append(f"`out_of_scope: {name}:` carries `{reason}`, not a sentence of at least "
                          f"{MIN_REASON_WORDS} words")
    return faults


def uncovered_targets(targets: dict[str, Declared], declaration: Declaration) -> list[Declared]:
    return sorted((target for target in targets.values()
                   if not any(reaches(root, target.path) for root in declaration.roots)
                   and target.name not in declaration.out_of_scope),
                  key=lambda target: (target.name, target.path))


# ── The gate. ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class Report:
    hits: list[Hit] = field(default_factory=list)
    walked: int = 0
    declaration: Declaration = field(default_factory=Declaration)
    not_a_subject: str = ""


def judge(gate: Gate, repo: Path, banned: Banned | None = None,
          controls: Callable[[], list[str]] | None = None) -> Report:
    """The whole gate, in order: the repository exists, the controls hold, the register is not
    empty, the manifest is read and its declaration is one, the index is read, every root is live,
    every declared target is covered, then the sweep — compared PER KEY with what git tracks, and
    refusing on zero files. Raises `CannotJudge`."""
    banned = gate.banned if banned is None else banned
    if not repo.is_dir():
        raise CannotJudge(f"{repo} is not a directory — there is no repository to judge", repo,
                          (f"ls -d {repo}",))
    if controls is not None:
        failed = controls()
        if failed:
            raise CannotJudge(
                f"CONTROL FAILURE — {len(failed)} control(s) did not hold, so this gate cannot judge "
                f"anything until it is repaired: {'; '.join(failed)}", SCRIPT,
                (f"edit {SCRIPT}: repair the instrument until every control holds",
                 f"python3 {SCRIPT} selftest   # runs the controls verbosely and names the arm"))
    empty_arms = [arm for arm in gate.arms if not any(a == arm for a, _, _ in banned)]
    if empty_arms:
        raise CannotJudge(
            f"EMPTY REGISTER — {gate.name} arm(s) {', '.join(empty_arms)} hold no pattern, so no "
            f"site could be a hit and every unit would pass for a reason that is about the register, "
            f"not the code (a register rots by SHRINKING as well as by mismatching)", SCRIPT,
            (f"edit {SCRIPT}: restore the {gate.name} register's {', '.join(empty_arms)} rows",
             f"grep -n 'BANNED' {Path(__file__).resolve()}"))
    manifest = repo / MANIFEST
    name = repo.resolve().name
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise CannotJudge(
            f"{name}: its {MANIFEST} cannot be read ({error.strerror or error}) — the "
            f"repository's `{BLOCK}:` declaration and its declared build targets live there, so "
            f"this gate cannot say what its subject is", manifest,
            (f"git -C {repo} status --short -- {MANIFEST}   # deleted?", f"cat {manifest}")) from None
    manifest_do = (f"cat {manifest}   # its `{BLOCK}:` block and its declared targets",
                   f"edit {manifest}: write the `{BLOCK}:` block as {SCRIPT}'s docstring declares it")
    try:
        declaration = parse_declaration(text)
        targets = declared_targets(text, name)
    except DeclarationError as error:
        raise CannotJudge(f"{name}: {MANIFEST} cannot be read as this gate's declaration — {error}",
                          manifest, manifest_do) from None
    if declaration is None:
        raise CannotJudge(
            f"{name}: {MANIFEST} carries no `{BLOCK}:` block — the repository has not said whether "
            f"it holds deterministic-content core, and a gate armed over a question never answered "
            f"would pass over nothing", manifest,
            (f"edit {manifest}: add `{BLOCK}:` with its `roots:` (the deterministic-content core), "
             f"or `not_a_subject: \"<why this repository holds none>\"`", f"cat {manifest}"))
    faults = declaration_faults(declaration, targets)
    if faults:
        raise CannotJudge(f"{name}: the `{BLOCK}:` declaration is not one — {'; '.join(faults)}",
                          manifest, manifest_do)
    if declaration.not_a_subject:
        return Report(declaration=declaration,
                      not_a_subject=f"{name} declares no deterministic-content core: "
                                    f"{declaration.not_a_subject}")
    if gate.name in declaration.exempt:
        return Report(declaration=declaration,
                      not_a_subject=f"{name} is exempt from the {gate.name} gate: "
                                    f"{declaration.exempt[gate.name]}")
    roots = declaration.roots
    tracked = tracked_sources(repo, roots)
    dead = dead_roots(repo, roots)
    if dead:
        raise CannotJudge(
            f"scope root(s) {', '.join(f'{name}/{rel}' for rel in dead)} do not exist in the reach "
            f"— no tracked or untracked-not-ignored file under them, and an ignored build tree does "
            f"not count because no commit carries it; directory moved or deleted: update `roots:` "
            f"to the new layout instead of letting coverage narrow silently", repo,
            (dead_roots_do(repo, dead),
             f"edit {manifest}: `{BLOCK}: roots:` — name the directories that exist"))
    uncovered = uncovered_targets(targets, declaration)
    if uncovered:
        listed = ", ".join(f"{target.name} ({target.path}, declared under `{target.block}:`)"
                           for target in uncovered)
        raise CannotJudge(
            f"{name} declares {len(uncovered)} build target(s) that NO scope root reaches and "
            f"`out_of_scope:` does not name: {listed} — the roots are hand-written, so a target "
            f"nobody added is judged by nothing and the pass would be over a narrower subject than "
            f"this gate claims", repo,
            (f"edit {manifest}: `{BLOCK}: roots:` — add the root(s) holding that target's "
             f"deterministic-content units",
             f"edit {manifest}: `{BLOCK}: out_of_scope:` — add `<target>: \"<one-sentence reason>\"` "
             f"if that target is legitimately outside the deterministic-content core",
             f"cat {manifest}   # what the repository declares"))
    rep = Report(declaration=declaration)
    read: list[str] = []
    lost: list[str] = []
    for path, rel in walk_sources(repo, roots):
        text_of = read_text(path)
        if text_of is None:
            lost.append(rel)
            continue
        read.append(rel)
        rep.hits.extend(scan_text(gate, name, rel, text_of, banned))
    unread = sorted((set(tracked) - set(read)) | set(lost))
    if unread:
        raise CannotJudge(
            f"repository {name} LOST source files — {len(unread)} tracked file(s) the sweep could "
            f"not read: {', '.join(unread)} (read {len(read)}; git tracks {len(tracked)}). Every "
            f"rule over a lost file is unjudged, and a sweep one file short prints the same PASS a "
            f"clean tree prints", repo,
            (tracked_sources_do(repo, roots),
             f"ls -l {' '.join(str(repo / rel) for rel in unread)}   # what is on disk",
             f"git -C {repo} status --short -- {' '.join(unread)}   # deleted from the working tree?"))
    if not read:
        raise CannotJudge(
            f"the scope roots of {name} matched ZERO files (roots={list(roots)}) — checkout "
            f"missing or directory moved; refusing to pass vacuously", repo,
            (tracked_sources_do(repo, roots),
             f"edit {manifest}: `{BLOCK}: roots:` — name the directories that hold the "
             f"deterministic-content units"))
    rep.walked = len(read)
    return rep


@dataclass(frozen=True)
class Finding:
    what: str
    where: str
    why: str
    do: tuple[str, ...]
    kind: str = VIOLATION


@dataclass(frozen=True)
class Result:
    walked: int
    findings: tuple[Finding, ...] = ()
    detail: tuple[str, ...] = ()
    not_a_subject: str = ""

    @property
    def refused(self) -> bool:
        return any(f.kind == REFUSAL for f in self.findings)


G1_REMEDY = (
    "G1 REMEDY — std distributions/shuffles are implementation-defined and break the "
    "cross-stdlib determinism diagonal (ADR-31.D7): use the portable logcraft::core types — "
    "UniformIntDist / UniformRealDist / BernoulliDist / NormalDist / DiscreteDist — declared in "
    "logcraft/core/src/foundation/core.detail.foundation-random.cppm and reached by `import "
    "logcraft.core.detail.foundation;`. G1 has NO annotation budget: an exception is a contract "
    "change (Daidalos), not a comment.")
G2_REMEDY = (
    "G2 REMEDY — seed by IDENTITY, draw by POSITION (ADR-31.D9): use CounterRng (declared in "
    "logcraft/core/src/foundation/core.detail.foundation.cppm, reached by `import "
    "logcraft.core.detail.foundation;`), construct it from the entity's IDENTITY (a hash of the "
    "name/seed, never an index or a sibling count) and address each draw with seek(<logical "
    "coordinate>), which rekeys the sub-stream and erases consumption history. ESCAPE HATCH AND "
    "ITS PRICE: `DETERMINISM-ALLOW(random: <reason>)` on the line or in the comment block above "
    "— the `random:` gate key is REQUIRED — capped at ONE across the workspace, a slot already "
    "SPENT by LogCraft's non-deterministic-mode seed (engine_lifecycle.cpp), so a second exception "
    "reds on the COUNT: a contract question for Daidalos, not a comment you write. WHAT THIS ARM "
    "CANNOT SEE: it matches NAMES; a CounterRng seeked with seek(draws_so_far) passes it and "
    "violates ADR-31.D9 exactly — a green here has never covered that.")


def ban_finding(gate: Gate, hit: Hit, repo: Path) -> Finding:
    site = repo / hit.relpath
    if gate.name == WALLCLOCK:
        return Finding(
            what=f"{BAN} {hit.where}: [{hit.pattern}]  {hit.code}", where=hit.where,
            why="the deterministic-content core must read NO wall clock (the determinism bible "
                "§7): a hidden read — cached or not — is the New-Year time bomb the replay oracle "
                "cannot see",
            do=(f"edit {site}:{hit.lineno}: thread an injected event-time source (batch = "
                f"input-derived, live = WallClockFallback) instead of reading the clock here",
                f"sed -n '{hit.lineno}p' {site}   # the read as written",
                f"# if — and only if — this is THE injected live seam: annotate the line "
                f"`// note: DETERMINISM-ALLOW(wallclock: <reason>): <why>` and declare the slot "
                f"(`{BLOCK}: opt_outs: wallclock: 1`) — the `wallclock:` gate key is REQUIRED; the "
                f"RNG gate's `DETERMINISM-ALLOW(random: ...)` does not exempt this gate"))
    if hit.arm == G1:
        why = ("a std distribution's engine-bits -> value mapping is implementation-defined, so the "
               "same engine state draws DIFFERENT values on libstdc++ vs libc++ and the "
               "cross-stdlib determinism diagonal breaks silently (ADR-31.D7)")
        edit = (f"edit {site}:{hit.lineno}: replace {hit.matched} with the portable logcraft::core "
                f"type (UniformIntDist / UniformRealDist / BernoulliDist / NormalDist / "
                f"DiscreteDist) — {G1_REMEDY}")
    else:
        why = ("a stateful std engine's next value is a function of HOW MANY draws preceded it, so "
               "generated content becomes a function of consumption history: insert one draw "
               "upstream and every downstream value moves (ADR-31.D9)")
        edit = (f"edit {site}:{hit.lineno}: replace {hit.matched} with CounterRng seeded by "
                f"identity and addressed by seek(<logical coordinate>) — {G2_REMEDY}")
    return Finding(what=f"{BAN} {hit.arm} {hit.where}: {hit.matched} [{hit.pattern}]  {hit.code}",
                   where=hit.where, why=why,
                   do=(edit, f"sed -n '{hit.lineno}p' {site}   # the site as written"))


def too_many_finding(gate: Gate, arm: str, allowed: list[Hit], repo: Path, budget: int) -> Finding:
    slot = gate.slot(arm)
    roots = " ".join(str(repo / rel) for rel in sorted({h.relpath.rsplit("/", 1)[0] if "/" in
                                                         h.relpath else "." for h in allowed}))
    sites = ", ".join(h.where for h in allowed)
    if gate.name == WALLCLOCK:
        return Finding(
            what=f"{SEAMS}: {len(allowed)} annotated wall-clock reads but only {budget} live "
                 f"seam(s) declared (`{BLOCK}: opt_outs: {slot}`, contract cap "
                 f"{CONTRACT_CAPS[slot]}) — {sites}",
            where=sites,
            why="determinism is the floor: the clock enters the deterministic-content core through "
                "ONE injected live seam, and extra seams need a contract change, not an annotation "
                "(the determinism bible §7)",
            do=(f"grep -rn 'DETERMINISM-ALLOW(wallclock:' {roots}   # every annotated read",
                "# Daidalos: a second live seam is a contract change (the determinism bible §7), "
                "never an annotation — remove the extra annotation and thread the event-time "
                "source, or change the contract"))
    return Finding(
        what=f"{TOO_MANY} {arm}: {len(allowed)} annotated site(s), {budget} declared "
             f"(`{BLOCK}: opt_outs: {slot}`, contract cap {CONTRACT_CAPS[slot]}) — {sites}",
        where=sites,
        why="determinism is the floor — a new exception needs a contract change (Daidalos), not an "
            "annotation; the caps are this script's constants because raising one on a command "
            "line hides the question instead of answering it (ADR-31.D7 for G1, ADR-31.D9 for G2)",
        do=(f"grep -rn 'DETERMINISM-ALLOW(random:' {roots}   # every annotated site",
            f"# Daidalos: a {arm} exception beyond the cap of {CONTRACT_CAPS[slot]} is a contract "
            f"change (the determinism bible §5), never an annotation — remove the extra annotation "
            f"and route through the portable types / CounterRng, or change the contract"))


def refusal(cannot: CannotJudge) -> Finding:
    return Finding(
        what=f"nothing — I could not judge: {cannot}", where=cannot.where,
        why="a vacuous pass is a defect, never a green: a sweep with a failed control, an empty "
            "register, an unread declaration, a dead scope root, a declared target no root "
            "reaches, a root list that matched zero files, an unreadable index or a lost tracked "
            "source prints the same PASS a clean core does",
        do=cannot.do, kind=REFUSAL)


DECLARED_LIMITS = {
    WALLCLOCK: (
        "DECLARED LIMIT — the opt-out is gate-keyed (`DETERMINISM-ALLOW(wallclock:`): the RNG "
        "gate's `DETERMINISM-ALLOW(random:` never exempts here, and a marker with no gate key "
        "exempts neither gate.",
        "DECLARED LIMIT — names, not intent: a liveness timeout that reads the clock is a hit, and "
        "the seam budget sends the question to Daidalos instead of answering it.",
    ),
    RANDOM: (
        "DECLARED LIMIT — names, never value flow: a CounterRng seeked with a value derived from "
        "consumption (seek(draws_so_far)) passes both arms and violates ADR-31.D9 exactly; the "
        "instrument that can see it is the behavioural draw-count intervention, a separate artifact.",
        "DECLARED LIMIT — the opt-out is gate-keyed (`DETERMINISM-ALLOW(random:`): the wall-clock "
        "gate's `DETERMINISM-ALLOW(wallclock:` never exempts here, and a marker with no gate key "
        "exempts neither gate.",
        "DECLARED LIMIT — not matched is not endorsed: std::philox_engine (C++26, counter-based) is "
        "deliberately unmatched by G2; whether it is an acceptable second engine is ADR-31's "
        "question.",
    ),
}
COVERAGE_LIMIT = (
    "DECLARED LIMIT — the roots are compared against EVERY build target this repository's "
    "packages.yml declares — its conan packages under `packages:` and its build targets under "
    "`inventory:` alike: a declared target no root reaches and `out_of_scope:` does not name is a "
    "REFUSAL. What that comparison still cannot reach is a directory belonging to NO declared "
    "target at all, and only a manifest entry or a new root brings it in.")
BUDGET_LIMIT = (
    "DECLARED LIMIT — the budget is judged here per repository (annotated sites <= the declared "
    "`opt_outs:` <= the contract cap); the SUM across repositories is the superproject's "
    "`determinism_allocation` check module, the one part of these gates that is cross-repo by "
    "nature.")


def verdict(gate_name: str, repo: Path, banned: Banned | None = None,
            controls: Callable[[], list[str]] | None = None) -> Result:
    gate = GATE_OF[gate_name]
    try:
        rep = judge(gate, repo, banned, controls)
    except CannotJudge as cannot:
        return Result(walked=0, findings=(refusal(cannot),))
    if rep.not_a_subject:
        return Result(walked=0, not_a_subject=rep.not_a_subject)
    name = repo.resolve().name
    violations = sorted((h for h in rep.hits if not h.allowed), key=lambda h: (h.arm, h.where))
    allowed = sorted((h for h in rep.hits if h.allowed), key=lambda h: (h.arm, h.where))
    budgets = {arm: rep.declaration.opt_outs.get(gate.slot(arm), 0) for arm in gate.arms}
    if gate.name == WALLCLOCK:
        detail = [
            f"wall-clock lint: scanned {rep.walked} deterministic-content TUs in {name}; suffixes "
            f"{' '.join('*' + s for s in SUFFIXES)}; all {len(banned or gate.banned)} banned "
            f"wall-clock read patterns run over all of them.",
            f"Annotated live seam(s) [{len(allowed)} / max {budgets[WALLCLOCK]} declared, contract "
            f"cap {CONTRACT_CAPS['wallclock']}]:",
        ]
        detail.extend(f"  ALLOW  {h.where}: [{h.pattern}]  {h.code}" for h in allowed)
    else:
        detail = [
            f"RNG-determinism lint: scanned {rep.walked} deterministic-content TUs in {name}; "
            f"suffixes {' '.join('*' + s for s in SUFFIXES)}; arms G1 (ADR-31.D7, std "
            f"distributions/shuffles) and G2 (ADR-31.D9, stateful std engines) both run over all "
            f"of them.",
            f"Annotated opt-out(s) [{len(allowed)}]:",
        ]
        for h in allowed:
            count = sum(1 for a in allowed if a.arm == h.arm)
            detail.append(f"  ALLOW  {h.arm} {h.where}: {h.matched} [{count} / max "
                          f"{budgets[h.arm]} declared]  {h.code}")
    detail.extend(f"  OUT OF SCOPE  {target}: {reason}"
                  for target, reason in sorted(rep.declaration.out_of_scope.items()))
    detail.extend(DECLARED_LIMITS[gate.name])
    detail.extend((COVERAGE_LIMIT, BUDGET_LIMIT))
    findings = [ban_finding(gate, h, repo) for h in violations]
    for arm in gate.arms:
        arm_allowed = [h for h in allowed if h.arm == arm]
        if len(arm_allowed) > budgets[arm]:
            findings.append(too_many_finding(gate, arm, arm_allowed, repo, budgets[arm]))
    return Result(walked=rep.walked, findings=tuple(findings), detail=tuple(detail))


# ── Controls — run on EVERY live invocation. ───────────────────────────────────────────────────
WALLCLOCK_BAD = (
    "auto t = std::chrono::system_clock::now();",
    "return steady_clock::now();",
    "const auto x = high_resolution_clock::now( );",
    "const std::time_t now{std::time(nullptr)};",
    "time(&t);",
    "time(0);",
    "gettimeofday(&tv, nullptr);",
    "clock_gettime(CLOCK_MONOTONIC, &ts);",
    "timespec_get(&ts, TIME_UTC);",
)
WALLCLOCK_GOOD = (
    "auto n = now();",
    "// closes the window using now() as end",
    "int dur = runtime(cfg);",
    "auto v = lifetime(obj);",
    "std::timed_mutex m;",
    "schedule_sometime(cb);",
    "window.end_iso = envelope.latest;",
)
RANDOM_BAD: tuple[tuple[str, str], ...] = (
    (G1, "std::uniform_int_distribution<std::size_t> dist(0, n - 1);"),
    (G1, "std::uniform_real_distribution<double> dist(cfg.min, cfg.max);"),
    (G1, "std::bernoulli_distribution error_dist_;"),
    (G1, "auto d = std::normal_distribution<double>(mean, stddev);"),
    (G1, "std::discrete_distribution<int> w(begin, end);"),
    (G1, "std::poisson_distribution<int> p(4.0);"),
    (G1, "std::shuffle(v.begin(), v.end(), rng);"),
    (G1, "std::ranges::shuffle(v, rng);"),
    (G1, "std::sample(in.begin(), in.end(), out, 5, rng);"),
    (G1, "const double u = std::generate_canonical<double, 53>(rng);"),
    (G2, "std::mt19937_64 rng_{seed};"),
    (G2, "std::mt19937 gen(seed);"),
    (G2, "std::minstd_rand lcg(1);"),
    (G2, "std::minstd_rand0 lcg0(1);"),
    (G2, "std::ranlux24 rl24;"),
    (G2, "std::ranlux48_base rl48b;"),
    (G2, "std::knuth_b kb(7);"),
    (G2, "std::default_random_engine eng;"),
    (G2, "std :: random_device rd;"),
    (G2, "std::seed_seq seq{1, 2, 3};"),
    (G2, "std::linear_congruential_engine<std::uint_fast32_t, 16807, 0, 2147483647> e;"),
    (G2, "std::mersenne_twister_engine<std::uint_fast32_t, 32, 624, 397, 31> mte;"),
    (G2, "std::subtract_with_carry_engine<std::uint_fast32_t, 24, 10, 24> swc;"),
    (G2, "std::discard_block_engine<std::ranlux24_base, 223, 23> dbe;"),
    (G2, "std::independent_bits_engine<std::mt19937, 32, std::uint32_t> ibe;"),
    (G2, "std::shuffle_order_engine<std::knuth_b, 256> soe;"),
)
RANDOM_GOOD = (
    "logcraft::core::UniformIntDist<std::size_t> dist(0, n - 1);",
    "logcraft::core::NormalDist<double> dist(mean, stddev);",
    "logcraft::core::DiscreteDist<std::size_t> d(w.begin(), w.end());",
    "BernoulliDist dup_dist_{0.0};",
    "CounterRng rng{identity_seed};",
    "logcraft::core::CounterRng rng{splitmix64(key)};",
    "rng.seek(intra_agent_index);",
    "auto v = sample_rate(cfg);",
    "shuffle_seed_ = next_seed();",
    "int n = distribution_count;",
    "std::size_t seed_seq_len = 4;",
    "std::uint64_t mt19937_reference = 0;",
    "auto device = make_device();",
)
SEAM = "    return std::chrono::system_clock::now();"
SEED = "      seed_base_{cfg.is_deterministic_mode() ? cfg.seed : std::random_device{}()}"


def annotation_cases(gate: Gate) -> tuple[tuple[str, bool, list[str]], ...]:
    """The opt-out's REACH, thirteen boundary cases per gate: where a marker may sit, whose marker
    it must be, and that a marker inside a STRING LITERAL is not one, on either line."""
    own, other = gate.name, (RANDOM if gate.name == WALLCLOCK else WALLCLOCK)
    site = SEAM if gate.name == WALLCLOCK else SEED
    pad = "    " if gate.name == WALLCLOCK else "      "
    same_line_site = ("return std::chrono::system_clock::now();" if gate.name == WALLCLOCK
                      else "std::mt19937 gen(seed);")
    return (
        ("own marker on the site's own line", True,
         [site + f"  // note: DETERMINISM-ALLOW({own}: seam): why"]),
        ("own marker in the comment block above", True,
         [f"{pad}// note: DETERMINISM-ALLOW({own}: seam): why", site]),
        ("own marker inside a multi-line /* */ block directly above", True,
         [f"{pad}/* note: DETERMINISM-ALLOW({own}: seam):", f"{pad}   why */", site]),
        ("a marker one BLANK line above must NOT reach down", False,
         [f"{pad}// note: DETERMINISM-ALLOW({own}: seam): why", "", site]),
        ("a marker beyond a non-comment line must NOT reach down", False,
         [f"{pad}// note: DETERMINISM-ALLOW({own}: seam): why", f"{pad}int x = 0;", site]),
        ("the other gate's marker must NOT exempt", False,
         [f"{pad}// note: DETERMINISM-ALLOW({other}: seam): why", site]),
        ("the pre-2026-09-09 gate-less marker must NOT exempt", False,
         [f"{pad}// note: DETERMINISM-ALLOW(seam): why", site]),
        ("the other gate's marker on the site's OWN line must NOT exempt", False,
         [site + f"  // note: DETERMINISM-ALLOW({other}: seam): why"]),
        ("the gate-less marker on the site's OWN line must NOT exempt", False,
         [site + "  // note: DETERMINISM-ALLOW(seam): why"]),
        ("BOTH gate keys on the site's own line exempt this gate too", True,
         [site + f"  // note: DETERMINISM-ALLOW({other}: seam) DETERMINISM-ALLOW({own}: seam): why"]),
        ("BOTH gate keys in the comment block above exempt this gate too", True,
         [f"{pad}// note: DETERMINISM-ALLOW({own}: seam) DETERMINISM-ALLOW({other}: seam): why",
          site]),
        ("a marker inside a STRING LITERAL on the line above must NOT exempt", False,
         [f'{pad}log("DETERMINISM-ALLOW({own}: seam)");', site]),
        ("a marker inside a STRING LITERAL on the site's OWN line must NOT exempt", False,
         [f'{pad}log("DETERMINISM-ALLOW({own}: seam)"); {same_line_site}']),
    )


def run_controls(gate_name: str, verbose: bool = False) -> tuple[list[str], int]:
    """(failures, arms): every known-bad matches on the arm that OWNS it, every known-good is clean,
    every arm still holds patterns, the two markers are disjoint, the annotation's reach, and the
    coordinate after a block comment."""
    gate = GATE_OF[gate_name]
    failures: list[str] = []
    arms = 0

    def check(label: str, cond: bool) -> None:
        nonlocal arms
        arms += 1
        if not cond:
            failures.append(f"{gate_name}: {label}")
        elif verbose:
            print(f"  control OK  {gate_name}: {label}")

    bad = tuple((WALLCLOCK, line) for line in WALLCLOCK_BAD) if gate_name == WALLCLOCK else RANDOM_BAD
    good = WALLCLOCK_GOOD if gate_name == WALLCLOCK else RANDOM_GOOD
    for expected_arm, line in bad:
        found = first_match(line, gate.banned)
        check(f"known-bad matched on {expected_arm}: {line!r}",
              found is not None and found[0] == expected_arm)
    for line in good:
        check(f"known-good clean: {line!r}", first_match(line, gate.banned) is None)
    for arm in gate.arms:
        check(f"arm {arm} still holds patterns (a register can rot by SHRINKING)",
              any(a == arm for a, _, _ in gate.banned))
    check("the two gates' markers are not in a substring relation",
          gate.marker not in gate.sibling_marker and gate.sibling_marker not in gate.marker)
    for label, expect, lines in annotation_cases(gate):
        got = allowed_by_annotation(gate.marker, len(lines) - 1, lines, strip_comments(lines))
        check(f"annotation scope — {label}", got is expect)
    site = "auto t = steady_clock::now();" if gate_name == WALLCLOCK else "std::mt19937 gen(seed);"
    hits = scan_text(gate, "ctl", "f.cpp", f"int a;\n/* one\n   two */ {site}\n")
    expected = "steady_clock::now(" if gate_name == WALLCLOCK else "std::mt19937"
    check("scan_text reports the site's own line (3) after a two-line block comment, on its arm, "
          "naming the matched spelling, WHERE spelled repo/rel:line",
          [(h.lineno, h.arm, h.matched, h.where) for h in hits]
          == [(3, gate.arms[-1], expected, "ctl/f.cpp:3")])
    return failures, arms


# ── The CLI. ───────────────────────────────────────────────────────────────────────────────────
def render(gate_name: str, repo: Path, result: Result) -> tuple[str, int]:
    lines = [f"determinism_lint {gate_name} — {repo.resolve()}"]
    if result.not_a_subject:
        lines.append(f"NOT A SUBJECT — {result.not_a_subject}. Nothing was judged, and this is not "
                     f"a PASS.")
        return "\n".join(lines), 0
    lines.extend(f"    {line}" for line in result.detail)
    for finding in result.findings:
        lines.append(f"{'REFUSED' if finding.kind == REFUSAL else 'VIOLATION'}: {finding.what}")
        lines.append(f"    where: {finding.where}")
        lines.append(f"    why:   {finding.why}")
        lines.extend(f"    do:    {item}" for item in finding.do)
    if result.refused:
        lines.append(f"REFUSED — the {gate_name} gate could not judge {repo.resolve().name}.")
        return "\n".join(lines), 2
    if result.findings:
        lines.append(f"FAIL — {len(result.findings)} finding(s) over {result.walked} unit(s).")
        return "\n".join(lines), 1
    lines.append(f"PASS — {result.walked} unit(s) judged, no finding.")
    return "\n".join(lines), 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("verb", choices=(WALLCLOCK, RANDOM, "selftest"))
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="the repository to judge (default: the current directory)")
    args = parser.parse_args(argv)
    if args.verb == "selftest":
        return selftest()
    result = verdict(args.verb, args.repo,
                     controls=lambda: run_controls(args.verb)[0])
    text, code = render(args.verb, args.repo, result)
    print(text)
    return code


# ── Self-test ──────────────────────────────────────────────────────────────────────────────────
CLEAN_UNIT = "int clean() { return 0; }\n"
ROOT_PLACEHOLDER = "CMakeLists.txt"
FIXTURE_TARGETS = "packages:\n  fixture_whole:\n    path: .\n    version: 1.0\n"
COVERAGE_TARGETS = ("packages:\n"
                    "  fixture_core:\n    path: core\n    version: 1.0\n"
                    "  fixture_tooling:\n    path: tooling\n    version: 1.0\n"
                    "inventory:\n  fixture_harness:\n    path: harness\n    target: fixture\n")
FIXTURE_ROOTS = ("core/src", "core/api", "semantic/test_frameworks/src", "src", "engine/src")


def block(roots: Iterable[str] = FIXTURE_ROOTS, exempt: dict[str, str] | None = None,
          out_of_scope: dict[str, str] | None = None, opt_outs: dict[str, int] | None = None,
          not_a_subject: str = "") -> str:
    """A `determinism:` block in the declared shape, for a fixture manifest."""
    text = f"{BLOCK}:\n"
    if not_a_subject:
        return text + f'  not_a_subject: "{not_a_subject}"\n'
    text += "  roots:\n" + "".join(f"    - {root}\n" for root in roots)
    for key, held in (("exempt", exempt), ("out_of_scope", out_of_scope), ("opt_outs", opt_outs)):
        if held:
            text += f"  {key}:\n" + "".join(
                f"    {k}: {v}\n" if isinstance(v, int) else f'    {k}: "{v}"\n'
                for k, v in held.items())
    return text


REASON = "a fixture reason long enough to read as one whole sentence"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=git_env(repo))


def _plant(base: Path, name: str, files: dict[str, str], dirs: Iterable[str] = (),
           init: bool = True) -> Path:
    """A fixture checkout: `files` written, every directory of `dirs` holding one non-source file
    (a root is live when the reach holds a file under it; git records no empty directory)."""
    repo = base / name
    for rel in dirs:
        (repo / rel).mkdir(parents=True, exist_ok=True)
        (repo / rel / ROOT_PLACEHOLDER).write_text("# a root with no source\n", encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    repo.mkdir(parents=True, exist_ok=True)
    if init:
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
    return repo


def selftest() -> int:
    failures: list[str] = []
    arms = 0
    for gate_name in GATES:
        ctl_failures, ctl_arms = run_controls(gate_name, verbose=True)
        failures.extend(ctl_failures)
        arms += ctl_arms

    def check(label: str, got, expected=True) -> None:
        nonlocal arms
        arms += 1
        if got != expected:
            failures.append(f"{label}: {got!r} != {expected!r}")

    def paste_run(line: str) -> int:
        try:
            done = subprocess.run(["bash", "-c", line], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=20)
        except subprocess.TimeoutExpired:
            return -1
        return done.returncode

    blank = Finding(what="", where="", why="", do=("", ""), kind="none")

    def first(result: Result) -> Finding:
        return result.findings[0] if result.findings else blank

    def refused(result: Result, fragment: str, label: str) -> Finding:
        finding = first(result)
        check(f"refusal: {label} is a REFUSAL that walked nothing, carries the fragment "
              f"{fragment!r}, a WHERE and a DO",
              result.walked == 0 and len(result.findings) == 1 and finding.kind == REFUSAL
              and fragment in finding.what and bool(finding.where) and bool(finding.do)
              and all(d.strip() for d in finding.do))
        return finding

    # -- THE DECLARATION PARSER -------------------------------------------------------------------
    parsed = parse_declaration(FIXTURE_TARGETS + block(exempt={WALLCLOCK: REASON},
                                                       out_of_scope={"fixture_tooling": REASON},
                                                       opt_outs={"random_g2": 1}))
    check("parse: the declared shape reads back every key — roots in order, the exemption, the "
          "out-of-scope row, the budget as a number",
          (parsed.roots, parsed.exempt, parsed.out_of_scope, parsed.opt_outs) if parsed else None,
          (FIXTURE_ROOTS, {WALLCLOCK: REASON}, {"fixture_tooling": REASON}, {"random_g2": 1}))
    check("parse: a manifest without the block is None, never an empty declaration",
          parse_declaration(FIXTURE_TARGETS), None)
    check("parse: a `#` comment after a value and a `#` INSIDE a quoted reason are told apart",
          (parse_declaration(f'{BLOCK}:\n  not_a_subject: "a # is part of this reason"  # c\n')
           or Declaration()).not_a_subject, "a # is part of this reason")
    for label, text in (
            ("an unknown key", f"{BLOCK}:\n  roots:\n    - src\n  scope:\n    - x\n"),
            ("the flow form of the block", f"{BLOCK}: {{roots: [src]}}\n"),
            ("a flow-form root list", f"{BLOCK}:\n  roots: [src]\n"),
            ("a root entry that is not a `- ` line", f"{BLOCK}:\n  roots:\n    src\n"),
            ("a budget that is not a number", f"{BLOCK}:\n  roots:\n    - s\n  opt_outs:\n"
                                              f"    wallclock: one\n"),
            ("a key declared twice", f"{BLOCK}:\n  roots:\n    - a\n  roots:\n    - b\n"),
            ("an entry at an indent the block does not take", f"{BLOCK}:\n  roots:\n      - a\n"),
            ("the block declared twice", f"{BLOCK}:\n  roots:\n    - a\n{BLOCK}:\n  roots:\n"
                                         f"    - b\n")):
        try:
            parse_declaration(text)
        except DeclarationError:
            check(f"parse: {label} raises", True)
        else:
            check(f"parse: {label} raises", False)
    check("is_sentence: the reason floor is eight words",
          (is_sentence("one two three four five six seven eight"),
           is_sentence("one two three four five six seven")), (True, False))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # -- THE WALLCLOCK GATE OVER ONE CHECKOUT ---------------------------------------------------
        ban_unit = ("int a;\n/* a block comment\n   spanning lines */\n"
                    "auto t = std::chrono::system_clock::now();\n")
        seam_unit = ("    // note: DETERMINISM-ALLOW(wallclock: the injected live seam): why\n"
                     + SEAM + "\n")
        sibling_unit = "    // note: DETERMINISM-ALLOW(random: seam): why\n" + SEAM + "\n"
        test_unit = "auto t = steady_clock::now();\n"
        repo = _plant(base, "fixture-repo", {
            "core/src/b.cpp": ban_unit,
            "semantic/test_frameworks/src/test_frameworks.cppm": CLEAN_UNIT,
            "core/src/tests/t.cpp": test_unit,
            "core/src/x_test.cpp": test_unit,
            "src/c.cpp": seam_unit,
            "engine/src/d.cpp": sibling_unit,
            "core/api/a.hpp": CLEAN_UNIT,
            MANIFEST: FIXTURE_TARGETS + block(opt_outs={"wallclock": 1}),
        }, FIXTURE_ROOTS)
        result = verdict(WALLCLOCK, repo)
        check("wallclock: the fixture yields exactly two BAN findings — the read after the block "
              "comment and the read under the RNG gate's marker — with the declared seam allowed; "
              "tests/ and _test. excluded, the test_frameworks dialect unit COUNTED",
              (sorted(f.what.split(" ", 1)[0] for f in result.findings), result.walked),
              ([BAN, BAN], 5))
        by_where = {f.where: f for f in result.findings}
        ban = by_where.get("fixture-repo/core/src/b.cpp:4", blank)
        check("wallclock BAN: WHERE is the read's own line (4), the message names the pattern and "
              "the code, the DO edits the absolute path at that line, sed's it, and the annotation "
              "remedy is a `#` line naming the gate key and the slot",
              len(ban.do) == 3
              and ban.what == f"{BAN} fixture-repo/core/src/b.cpp:4: [*_clock::now]  auto t = "
                              f"std::chrono::system_clock::now();"
              and ban.do[0].startswith(f"edit {repo / 'core/src/b.cpp'}:4: thread an injected")
              and ban.do[1] == f"sed -n '4p' {repo / 'core/src/b.cpp'}   # the read as written"
              and ban.do[2].startswith("# if — and only if — this is THE injected live seam")
              and "wallclock:" in ban.do[2] and "opt_outs" in ban.do[2])
        check("wallclock BAN: the read under the RNG gate's marker is a finding at its own line",
              "fixture-repo/engine/src/d.cpp:2" in by_where)
        check("every finding is a VIOLATION with all four lines",
              all(f.kind == VIOLATION and f.what and f.where and f.why and f.do
                  for f in result.findings))
        check("DO paste-run: the BAN sed remedy exits 0 as typed",
              paste_run(ban.do[1]) if len(ban.do) == 3 else -1, 0)
        check("DO paste-run: the BAN annotation remedy is a comment line — exit 0 as typed",
              paste_run(ban.do[2]) if len(ban.do) == 3 else -1, 0)
        check("wallclock detail: the population line, the allowed seam against the declared budget, "
              "the declared limits",
              f"wall-clock lint: scanned 5 deterministic-content TUs in fixture-repo; suffixes "
              f"*.cpp *.hpp *.h *.cc *.cxx *.cppm; all 6 banned wall-clock read patterns run over "
              f"all of them." in result.detail
              and "Annotated live seam(s) [1 / max 1 declared, contract cap 1]:" in result.detail
              and f"  ALLOW  fixture-repo/src/c.cpp:2: [*_clock::now]  {SEAM.strip()}"
              in result.detail
              and all(line in result.detail for line in DECLARED_LIMITS[WALLCLOCK])
              and COVERAGE_LIMIT in result.detail and BUDGET_LIMIT in result.detail)
        check("render: findings exit 1 and say FAIL", render(WALLCLOCK, repo, result)[1], 1)
        # A second annotated read against the declared budget of one.
        (repo / "core/api/e.hpp").write_text(seam_unit, encoding="utf-8")
        _git(repo, "add", "-A")
        many = verdict(WALLCLOCK, repo)
        seams = [f for f in many.findings if f.what.startswith(SEAMS)]
        check("wallclock: two annotated reads against a declared budget of one is ONE "
              "TOO-MANY-SEAMS finding beside the two BANs, WHERE both seams, the DO a grep then a "
              "`# Daidalos:` line",
              len(seams) == 1 and len(many.findings) == 3
              and seams[0].where == "fixture-repo/core/api/e.hpp:2, fixture-repo/src/c.cpp:2"
              and seams[0].do[0].startswith("grep -rn 'DETERMINISM-ALLOW(wallclock:' ")
              and seams[0].do[1].startswith("# Daidalos: a second live seam is a contract change"))
        check("DO paste-run: the TOO-MANY-SEAMS grep remedy exits 0 as typed (it finds the seams)",
              paste_run(seams[0].do[0]) if seams else -1, 0)
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"wallclock": 2}),
                                     encoding="utf-8")
        refused(verdict(WALLCLOCK, repo), "exceeds the contract's cap of 1",
                "a budget declared ABOVE the contract cap (a cap is the contract's, never the "
                "repository's)")
        (repo / "core/api/e.hpp").unlink()
        _git(repo, "add", "-A")
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(), encoding="utf-8")
        undeclared = verdict(WALLCLOCK, repo)
        check("wallclock: an annotated seam with NO declared budget is a TOO-MANY-SEAMS finding — "
              "the annotation alone buys nothing",
              sorted(f.what.split(" ", 1)[0].rstrip(":") for f in undeclared.findings),
              sorted([BAN, BAN, SEAMS]))
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"wallclock": 1}),
                                     encoding="utf-8")
        (repo / "core/src/b.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        (repo / "engine/src/d.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        clean = verdict(WALLCLOCK, repo)
        check("wallclock: a clean fixture carries no finding and walks the same units",
              (clean.findings, clean.walked), ((), 5))
        check("render: a clean run exits 0 and says PASS with its count",
              (render(WALLCLOCK, repo, clean)[1],
               render(WALLCLOCK, repo, clean)[0].endswith("PASS — 5 unit(s) judged, no finding.")),
              (0, True))

        # -- THE RANDOM GATE OVER THE SAME CHECKOUT ------------------------------------------------
        g1_unit = ("int a;\n/* a block comment\n   spanning lines */\n"
                   "std::uniform_int_distribution<int> d(0, 9);\n")
        seed_unit = ("      // note: DETERMINISM-ALLOW(random: the non-deterministic-mode seed): "
                     "why\n" + SEED + "\n")
        rng_sibling = "      // note: DETERMINISM-ALLOW(wallclock: seed): why\n" + SEED + "\n"
        (repo / "core/src/b.cpp").write_text(g1_unit, encoding="utf-8")
        (repo / "src/c.cpp").write_text(seed_unit, encoding="utf-8")
        (repo / "engine/src/d.cpp").write_text(rng_sibling, encoding="utf-8")
        (repo / "core/src/tests/t.cpp").write_text("std::shuffle(v.begin(), v.end(), rng);\n",
                                                  encoding="utf-8")
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"random_g2": 1}),
                                     encoding="utf-8")
        _git(repo, "add", "-A")
        rng = verdict(RANDOM, repo)
        rng_where = {f.where: f for f in rng.findings}
        g1 = rng_where.get("fixture-repo/core/src/b.cpp:4", blank)
        g2 = rng_where.get("fixture-repo/engine/src/d.cpp:2", blank)
        check("random: two BANs — G1 after the block comment, G2 under the wall-clock gate's "
              "marker — the declared G2 seed allowed, tests/ excluded",
              (sorted(f.what.split(" ", 2)[1] for f in rng.findings), rng.walked),
              ([G1, G2], 5))
        check("random BAN G1: WHERE its own line, the DO routes to the portable types with the G1 "
              "remedy, and sed's the site",
              g1.what.startswith(f"{BAN} G1 fixture-repo/core/src/b.cpp:4: "
                                 f"std::uniform_int_distribution")
              and g1.do[0].startswith(f"edit {repo / 'core/src/b.cpp'}:4: replace "
                                      f"std::uniform_int_distribution")
              and G1_REMEDY in g1.do[0]
              and g1.do[1] == f"sed -n '4p' {repo / 'core/src/b.cpp'}   # the site as written")
        check("random BAN G2: names the engine and carries the G2 remedy, its blind spot included",
              "std::random_device" in g2.what and G2_REMEDY in g2.do[0]
              and "seek(draws_so_far)" in g2.do[0])
        check("DO paste-run: the G1 and G2 sed remedies exit 0 as typed",
              (paste_run(g1.do[1]), paste_run(g2.do[1])), (0, 0))
        check("random detail: the population line and the allowed seed against its declared slot",
              f"RNG-determinism lint: scanned 5 deterministic-content TUs in fixture-repo; suffixes "
              f"*.cpp *.hpp *.h *.cc *.cxx *.cppm; arms G1 (ADR-31.D7, std distributions/shuffles) "
              f"and G2 (ADR-31.D9, stateful std engines) both run over all of them." in rng.detail
              and f"  ALLOW  G2 fixture-repo/src/c.cpp:2: std::random_device [1 / max 1 declared]  "
              f"{SEED.strip()}" in rng.detail
              and all(line in rng.detail for line in DECLARED_LIMITS[RANDOM]))
        (repo / "core/api/e.hpp").write_text(seed_unit, encoding="utf-8")
        (repo / "core/api/f.hpp").write_text(
            "// note: DETERMINISM-ALLOW(random: x): why\nstd::shuffle(a, b, r);\n", encoding="utf-8")
        _git(repo, "add", "-A")
        over = verdict(RANDOM, repo)
        too = {f.what.split(" ", 2)[1].rstrip(":"): f for f in over.findings
               if f.what.startswith(TOO_MANY)}
        check("random: two annotated G2 seeds against a slot of one AND one annotated G1 site "
              "against a cap of zero are TWO TOO-MANY-OPT-OUTS findings, one per arm",
              sorted(too), [G1, G2])
        check("random TOO-MANY G2: WHERE both seeds, the DO a grep then a `# Daidalos:` line",
              too.get(G2, blank).where == "fixture-repo/core/api/e.hpp:2, fixture-repo/src/c.cpp:2"
              and too.get(G2, blank).do[0].startswith("grep -rn 'DETERMINISM-ALLOW(random:' ")
              and too.get(G2, blank).do[1].startswith("# Daidalos: a G2 exception beyond the cap"))
        check("DO paste-run: the TOO-MANY grep remedy exits 0 as typed",
              paste_run(too[G2].do[0]) if G2 in too else -1, 0)
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"random_g1": 1}),
                                     encoding="utf-8")
        refused(verdict(RANDOM, repo), "exceeds the contract's cap of 0",
                "a G1 budget declared at all (its cap is zero)")
        for rel in ("core/api/e.hpp", "core/api/f.hpp"):
            (repo / rel).unlink()
        (repo / "core/src/b.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        (repo / "engine/src/d.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"random_g2": 1}),
                                     encoding="utf-8")
        _git(repo, "add", "-A")
        check("random: a clean fixture carries no finding",
              (verdict(RANDOM, repo).findings, verdict(RANDOM, repo).walked), ((), 5))

        # -- NOT A SUBJECT, said as such -----------------------------------------------------------
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(not_a_subject=REASON),
                                     encoding="utf-8")
        nas = verdict(WALLCLOCK, repo)
        text, code = render(WALLCLOCK, repo, nas)
        check("not_a_subject: no finding, nothing walked, exit 0, and the verdict line says NOT A "
              "SUBJECT with the reason — never PASS",
              (nas.findings, nas.walked, code, "NOT A SUBJECT" in text, REASON in text,
               any(line.startswith("PASS") for line in text.splitlines())),
              ((), 0, 0, True, True, False))
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(exempt={WALLCLOCK: REASON},
                                                             opt_outs={"random_g2": 1}),
                                     encoding="utf-8")
        check("exempt: the exempted gate is NOT A SUBJECT and the other gate still judges",
              (bool(verdict(WALLCLOCK, repo).not_a_subject), verdict(RANDOM, repo).walked), (True, 5))
        for label, manifest, fragment in (
                ("both gates exempted", block(exempt={WALLCLOCK: REASON, RANDOM: REASON}),
                 "names both gates"),
                ("an exemption whose reason is not a sentence", block(exempt={WALLCLOCK: "logcraft"}),
                 "not a sentence"),
                ("an exemption naming no gate", block(exempt={"clock": REASON}), "is not a gate"),
                ("a not_a_subject reason that is not a sentence", block(not_a_subject="none"),
                 "not a sentence"),
                ("an empty root list", block(roots=()), "names no directory"),
                ("an unknown budget slot", block(opt_outs={"random": 1}), "is not a budget slot"),
                ("a budget spent on an exempt gate",
                 block(exempt={WALLCLOCK: REASON}, opt_outs={"wallclock": 1}),
                 "a gate this repository is exempt from")):
            (repo / MANIFEST).write_text(FIXTURE_TARGETS + manifest, encoding="utf-8")
            refused(verdict(RANDOM, repo), fragment, f"declaration — {label}")
        (repo / MANIFEST).write_text(FIXTURE_TARGETS, encoding="utf-8")
        missing_block = refused(verdict(WALLCLOCK, repo), "carries no `determinism:` block",
                                "a manifest with no `determinism:` block")
        check("refusal: the missing block's WHERE is the manifest and its DO says both answers",
              missing_block.where == str(repo / MANIFEST)
              and "not_a_subject" in missing_block.do[0] and "roots:" in missing_block.do[0])
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"wallclock": 1}),
                                     encoding="utf-8")

        # -- REFUSALS ------------------------------------------------------------------------------
        refused(verdict(WALLCLOCK, repo, controls=lambda: ["arm X"]), "CONTROL FAILURE",
                "a failing control suite")
        empty = refused(verdict(WALLCLOCK, repo, banned=()), "EMPTY REGISTER",
                        "an empty wall-clock register")
        check("refusal: the empty register's WHERE is this script and its grep DO paste-runs",
              (empty.where, paste_run(empty.do[1]) if len(empty.do) == 2 else -1), (SCRIPT, 0))
        g2_only = tuple(row for row in RANDOM_BANNED if row[0] == G2)
        refused(verdict(RANDOM, repo, banned=g2_only), "arm(s) G1 hold no pattern",
                "a random register whose G1 arm SHRANK to nothing")
        nowhere = refused(verdict(WALLCLOCK, base / "no-such-repo", controls=lambda: ["arm X"]),
                          "is not a directory", "a --repo that is not a directory")
        check("refusal: ...and it is asked BEFORE the controls, never reported as CONTROL FAILURE",
              "CONTROL FAILURE" not in nowhere.what)
        shutil.rmtree(repo / "engine" / "src")
        dead = refused(verdict(WALLCLOCK, repo), "do not exist", "a scope root deleted from disk")
        check("refusal: the dead root is named `<repo>/<root>`, WHERE is the repository, the DO "
              "lists the reach under it and edits `roots:`",
              "fixture-repo/engine/src" in dead.what and dead.where == str(repo)
              and dead.do[0] == dead_roots_do(repo, ["engine/src"]) and "roots:" in dead.do[1])
        (repo / "engine/src/build-x").mkdir(parents=True)
        (repo / "engine/src/build-x/out.o").write_text("object\n", encoding="utf-8")
        (repo / ".gitignore").write_text("build-*/\n", encoding="utf-8")
        refused(verdict(WALLCLOCK, repo), "do not exist",
                "a scope root whose ONLY content is an IGNORED build tree (no commit carries it)")
        shutil.rmtree(repo / "engine/src/build-x")
        (repo / ".gitignore").unlink()
        (repo / "engine/src" / ROOT_PLACEHOLDER).write_text("# a root\n", encoding="utf-8")
        (repo / "engine/src/d.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        (repo / MANIFEST).unlink()
        gone = refused(verdict(WALLCLOCK, repo), "cannot be read", "a DELETED packages.yml")
        check("refusal: the deleted manifest is named where it was",
              gone.where == str(repo / MANIFEST))
        (repo / MANIFEST).write_text(FIXTURE_TARGETS + block(opt_outs={"wallclock": 1}),
                                     encoding="utf-8")
        (repo / "core/api/a.hpp").unlink()
        lost = refused(verdict(WALLCLOCK, repo), "LOST source files",
                       "a tracked source deleted from disk (per key)")
        check("refusal: the per-key loss names the file and the counts, and its ls-files DO "
              "paste-runs at exit 0",
              "core/api/a.hpp" in lost.what and "(read 4; git tracks 5)" in lost.what
              and paste_run(lost.do[0]) == 0)
        empty_repo = _plant(base, "empty-repo", {MANIFEST: FIXTURE_TARGETS + block(roots=("src",))},
                            ("src",))
        zero = refused(verdict(WALLCLOCK, empty_repo), "matched ZERO files",
                       "a root list with every root live and no unit")
        check("refusal: the ZERO case names the repository and the roots, its DO the ls-files line",
              "empty-repo" in zero.what and zero.do[0] == tracked_sources_do(empty_repo, ("src",)))
        export = _plant(base, "export", {MANIFEST: FIXTURE_TARGETS + block(roots=("src",)),
                                         "src/a.cpp": CLEAN_UNIT}, init=False)
        unreadable = refused(verdict(WALLCLOCK, export), "git ls-files failed",
                             "a checkout without .git (an unreadable index)")
        check("refusal: the unreadable-index DO is the ls-files line for that repository",
              unreadable.do[0] == tracked_sources_do(export, ("src",)))

        # -- COVERAGE: the roots against what the repository DECLARES -----------------------------
        cover = _plant(base, "cover", {"core/src/a.cpp": CLEAN_UNIT,
                                       MANIFEST: COVERAGE_TARGETS + block(roots=("core/src",))},
                       ("core/src", "harness"))
        gap = refused(verdict(WALLCLOCK, cover), "NO scope root reaches",
                      "a declared package and an `inventory:` target no root reaches")
        check("COVERAGE: the refusal names each target with its path AND its block, never the "
              "covered package, WHERE the repository, the DO the two edits then the cat",
              ("fixture_tooling (tooling, declared under `packages:`)" in gap.what,
               "fixture_harness (harness, declared under `inventory:`)" in gap.what,
               "fixture_core" in gap.what, gap.where == str(cover), len(gap.do),
               "roots:" in gap.do[0], "out_of_scope:" in gap.do[1]),
              (True, True, False, True, 3, True, True))
        (cover / MANIFEST).write_text(COVERAGE_TARGETS + block(
            roots=("core/src",), out_of_scope={"fixture_tooling": REASON}), encoding="utf-8")
        inventory_gap = refused(verdict(WALLCLOCK, cover), "declares 1 build target(s)",
                                "an uncovered `inventory:` target once the package is excluded")
        check("COVERAGE: ...naming the inventory target and its block — the comparison does not "
              "stop at `packages:`",
              "fixture_harness (harness, declared under `inventory:`)" in inventory_gap.what)
        (cover / MANIFEST).write_text(COVERAGE_TARGETS + block(
            roots=("core/src",), out_of_scope={"fixture_tooling": REASON,
                                               "fixture_harness": REASON}), encoding="utf-8")
        refused(verdict(WALLCLOCK, cover), "an `inventory:` build target",
                "an `inventory:` harness EXCLUDED rather than covered")
        (cover / MANIFEST).write_text(COVERAGE_TARGETS + block(
            roots=("core/src", "harness"), out_of_scope={"fixture_tooling": REASON}),
            encoding="utf-8")
        by_root = verdict(WALLCLOCK, cover)
        check("COVERAGE: naming the inventory target's directory as a root covers it, and a root "
              "holding no source is not a refusal while another root holds one",
              (by_root.findings, by_root.walked), ((), 1))
        for label, manifest, fragment in (
                ("an out-of-scope row naming an undeclared target",
                 block(roots=("core/src", "harness"), out_of_scope={"fixture_tooling": REASON,
                                                                    "ghost": REASON}),
                 "does not declare"),
                ("an out-of-scope row a root already reaches",
                 block(roots=("core/src", "harness", "tooling"),
                       out_of_scope={"fixture_tooling": REASON}), "already reaches"),
                ("an out-of-scope reason that is not a sentence",
                 block(roots=("core/src", "harness"), out_of_scope={"fixture_tooling": "tools"}),
                 "not a sentence")):
            (cover / MANIFEST).write_text(COVERAGE_TARGETS + manifest, encoding="utf-8")
            refused(verdict(WALLCLOCK, cover), fragment, f"COVERAGE — {label}")
        (cover / MANIFEST).write_text("packages:\n  ghost_pkg:\n    x: 1\n" + block(roots=("core/src",)),
                                      encoding="utf-8")
        no_path = refused(verdict(WALLCLOCK, cover), "carry no `path:`",
                          "a declared target with no `path:`")
        check("COVERAGE: ...naming the package and the manifest",
              ("ghost_pkg" in no_path.what, no_path.where), (True, str(cover / MANIFEST)))

        # -- THE FLOOR ------------------------------------------------------------------------------
        floor_repo = _plant(base, "own", {"src/a.cpp": CLEAN_UNIT, "api/b.cppm": CLEAN_UNIT,
                                          "src/tests/t.cpp": CLEAN_UNIT,
                                          "src/x_test.cpp": CLEAN_UNIT,
                                          "src/test_frameworks/tf.cppm": CLEAN_UNIT,
                                          "src/build_gen/g.cpp": CLEAN_UNIT,
                                          "src/build_identity.cpp": CLEAN_UNIT,
                                          "tools/c.cpp": CLEAN_UNIT, "src/d.ipp": CLEAN_UNIT})
        (floor_repo / "src" / "loose.cpp").write_text(CLEAN_UNIT, encoding="utf-8")
        check("FLOOR: over roots src and api the tracked set counts the nested .cpp, the .cppm, the "
              "test_frameworks/ unit and src/build_identity.cpp (a DIRECTORY named test or prefixed "
              "build_ is excluded, never a FILE named alike) — not tests/, build_gen/, _test., the "
              "untracked file, tools/ (outside the roots) nor the .ipp",
              tracked_sources(floor_repo, ("src", "api")),
              ["api/b.cppm", "src/a.cpp", "src/build_identity.cpp", "src/test_frameworks/tf.cppm"])
        check("FLOOR: the reach walk reads the tracked set plus the untracked-not-ignored file",
              [rel for _, rel in walk_sources(floor_repo, ("src", "api"))],
              ["api/b.cppm", "src/a.cpp", "src/build_identity.cpp", "src/loose.cpp",
               "src/test_frameworks/tf.cppm"])
    check("REGISTER: both live registers are non-empty, every entry a compiled pattern with a name",
          all(bool(g.banned) and all(isinstance(n, str) and n and hasattr(p, "search")
                                     for _, n, p in g.banned) for g in GATE_OF.values()))
    check("CAPS: every budget slot has a contract cap and every gate arm a slot",
          set(SLOT_OF.values()) == set(CONTRACT_CAPS)
          and all((g.name, arm) in SLOT_OF for g in GATE_OF.values() for arm in g.arms))

    for failure in failures:
        print(f"SELFTEST FAIL: {failure}")
    print(f"determinism_lint selftest: {arms - len(failures)}/{arms} arms green, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
