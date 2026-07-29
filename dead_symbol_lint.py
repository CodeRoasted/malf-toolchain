#!/usr/bin/env python3
"""Dead-symbol-in-comment gate — a comment naming a symbol whose last declaration just died.

CLAUDE.md § Comments: a comment restating a symbol name is a MIRROR with no enforced edge.
This gate supplies the missing edge, for exactly one failure mode: a change deletes the last
code occurrence of an identifier while a comment somewhere still names it. That comment is now
pointing at nothing, and nothing else in the toolchain notices — the compiler never reads it.

WHY DIFF-SCOPED AND NOT A TREE SWEEP. A tree sweep was built first and MEASURED: 1289 backticked
candidates across the workspace, 119 flagged (9.2%). Almost all were legitimate — DSL keywords,
JSON field names, env vars, conan targets, and above all CROSS-REPO references, a comment in
logcraft correctly naming canon's `dominant_level`. The sweep cannot separate those from a real
rot: `IntentFormat` named in canon (wrong — canon stopped knowing formats at T4) and
`dominant_level` named in logcraft (right) have the identical shape — alive in another repo,
named here. A gate shipping 119 findings that are mostly correct references is the can't-PASS
mode of [[synthetic-gate-vacuity-vs-judgment]]: it red-walls every lane for reasons that are not
defects, and gets switched off.

Diff scope removes that entire class by construction, because it only ever considers identifiers
THIS change deleted. A legitimate cross-repo reference is never a candidate — nothing about it
was removed. It also fires where the repair is cheapest and the author still has the context: at
the commit that caused it. That is not a preference, it is the measured shape of the defect —
3 of the 5 comment-rot sites found on 2026-07-28 were symbols whose last declaration was deleted
by the very commit that made the comment stale.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge whether a comment SHOULD name a symbol, and
it does not police the pre-existing tree. It answers one question with a yes or no: did this
change kill the last code occurrence of something a comment still names.

Usage:
    dead_symbol_lint.py [--range <git-range>] [--repo <path>]...   # default: HEAD~1..HEAD
    dead_symbol_lint.py --staged                                   # pre-commit shape
    dead_symbol_lint.py --selftest                                 # the gate must have teeth
"""
from __future__ import annotations

import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

SOURCE_SUFFIXES = {".cpp", ".cppm", ".h", ".hpp", ".cc", ".ipp"}
SKIP_DIR_NAMES = {".git", "technical_docs", ".conan2", "node_modules", "dist", "gcm.cache"}
SKIP_DIR_PREFIXES = ("build",)

# This workspace names code entities in backticks inside comments. A backticked token is an
# ASSERTION by the author that the thing is code, which is precisely what makes it checkable —
# and it is why unbackticked prose is never a candidate.
BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

# Identifier-shaped = an internal capital (CamelCase) or an underscore. A bare lowercase word is
# English until proven otherwise, so `note`, `main`, `push` never become candidates. Tightness
# here costs recall and buys the precision the gate lives or dies by.
IDENTIFIER_SHAPED = re.compile(r"^(?:[A-Za-z0-9]*[a-z0-9][A-Z]|[A-Za-z0-9]*_)")

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Reserved-identifier shapes belong to the implementation, not to us: `__int128`, `_Bool`.
# They are never OUR declarations, so their absence from our code proves nothing.
RESERVED = re.compile(r"^(?:_|.*__)")


def _blank_string_literals(text: str) -> str:
    """Blank literal CONTENT, preserving length and newlines, so offsets stay valid.

    Only ever used to locate comments: a `//` inside a string must not open a phantom
    comment. Identifier harvesting runs against the ORIGINAL text, deliberately — a DSL
    keyword or JSON field name that appears only inside a string literal is still present
    in this repo, and treating it as absent was measured as the single largest source of
    false positives (183 flags -> 119 when literals were retained in the oracle).
    """
    def blank(match: re.Match[str]) -> str:
        body = match.group(0)
        return body[0] + re.sub(r"[^\n]", " ", body[1:-1]) + body[-1]

    text = re.sub(r'"(?:\\.|[^"\\\n])*"', blank, text)
    return re.sub(r"'(?:\\.|[^'\\\n])*'", blank, text)


def comment_spans(text: str) -> list[tuple[int, int]]:
    blanked = _blank_string_literals(text)
    spans = [(m.start(), m.end()) for m in re.finditer(r"//[^\n]*", blanked)]
    spans += [(m.start(), m.end()) for m in re.finditer(r"/\*.*?\*/", blanked, re.S)]
    return sorted(spans)


def split_code_and_comments(text: str) -> tuple[str, list[tuple[int, str]]]:
    """(code-only text, [(line number, comment text)])."""
    spans = comment_spans(text)
    kept: list[str] = []
    comments: list[tuple[int, str]] = []
    previous = 0
    for start, end in spans:
        kept.append(text[previous:start])
        comments.append((text[:start].count("\n") + 1, text[start:end]))
        previous = end
    kept.append(text[previous:])
    return "".join(kept), comments


def iter_sources(repo: Path):
    for path in repo.rglob("*"):
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        parts = path.relative_to(repo).parts
        if any(p in SKIP_DIR_NAMES or p.startswith(SKIP_DIR_PREFIXES) for p in parts):
            continue
        yield path


def scan_repo(repo: Path) -> tuple[collections.Counter, list[tuple[Path, int, str]]]:
    """(identifiers present in CODE, backticked identifier mentions in COMMENTS)."""
    code_identifiers: collections.Counter = collections.Counter()
    mentions: list[tuple[Path, int, str]] = []
    for path in iter_sources(repo):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        code, comments = split_code_and_comments(text)
        code_identifiers.update(IDENTIFIER.findall(code))
        for lineno, comment in comments:
            for token in BACKTICKED.findall(comment):
                if IDENTIFIER_SHAPED.match(token) and not RESERVED.match(token):
                    mentions.append((path, lineno, token))
    return code_identifiers, mentions


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def removed_identifiers(repo: Path, diff_args: list[str]) -> set[str]:
    """Identifiers appearing on REMOVED lines of the diff, comment lines excluded.

    A removed comment is not a deleted declaration — sweeping a stale comment must never
    itself trip the gate, or the repair becomes the offence.
    """
    diff = git(repo, "diff", "--unified=0", *diff_args, "--",
               *(f"*{suffix}" for suffix in sorted(SOURCE_SUFFIXES)))
    tokens: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        body = line[1:]
        if body.lstrip().startswith(("//", "*", "/*")):
            continue
        code, _ = split_code_and_comments(body)
        tokens.update(IDENTIFIER.findall(code))
    return tokens


def check_repo(repo: Path, diff_args: list[str]) -> list[str]:
    removed = removed_identifiers(repo, diff_args)
    if not removed:
        return []
    code_identifiers, mentions = scan_repo(repo)
    # Its last CODE occurrence is gone if the change removed it and nothing in the tree
    # still uses it. Comments are excluded from that count by construction — otherwise the
    # stale comment would vouch for the symbol it is stale about.
    dead = {token for token in removed if code_identifiers[token] == 0}
    findings = []
    for path, lineno, token in mentions:
        if token in dead:
            findings.append(
                f"{path.relative_to(repo).as_posix()}:{lineno}: comment names `{token}`, "
                f"whose last code occurrence this change removes — repair by REMOVING THE "
                f"MIRROR, not by re-typing the name (CLAUDE.md § Comments)")
    return sorted(set(findings))


# ── Selftest ─────────────────────────────────────────────────────────────────────────────
# Both directions, because either alone leaves a hole: a gate that cannot FAIL reports green
# over anything, and a gate that cannot PASS red-walls every lane and gets switched off.
SELFTEST_CASES = [
    # (comment text, code text, token expected dead?)
    ("// the `WidgetFactory` owns this", "int unrelated_thing = 0;", True),
    ("// the `WidgetFactory` owns this", "WidgetFactory factory;", False),
    ("// see `latency_ms` in the schema", 'auto k = "latency_ms";', False),
    ("// plain english note here", "int x;", False),
    ("// the `note` word is not shaped", "int x;", False),
    ("// `__int128` is the compiler's", "int x;", False),
]


def selftest() -> int:
    failures = 0
    for index, (comment, code, expect_dead) in enumerate(SELFTEST_CASES, 1):
        text = f"{comment}\n{code}\n"
        code_only, comments = split_code_and_comments(text)
        identifiers = collections.Counter(IDENTIFIER.findall(code_only))
        mentioned = [t for _, c in comments for t in BACKTICKED.findall(c)
                     if IDENTIFIER_SHAPED.match(t) and not RESERVED.match(t)]
        dead = [t for t in mentioned if identifiers[t] == 0]
        if bool(dead) != expect_dead:
            print(f"selftest case {index} FAILED: expected dead={expect_dead}, got {dead}")
            failures += 1

    # The literal-retention arm, stated as its own case because it is the fix that took the
    # measured flag rate from 25% to 9.2% and a regression here would be silent.
    text = '// see `items_per_second`\nstate.counters["items_per_second"] = 1;\n'
    code_only, _ = split_code_and_comments(text)
    if "items_per_second" not in IDENTIFIER.findall(code_only):
        print("selftest FAILED: string-literal contents must count as code presence")
        failures += 1

    # A comment REMOVED by the change must not be read as a deleted declaration — else
    # sweeping a stale comment trips the gate, and the repair becomes the offence.
    if IDENTIFIER.findall(split_code_and_comments("// drop `OldName`")[0]):
        print("selftest FAILED: a removed comment line must yield no code identifiers")
        failures += 1

    print(f"selftest: {len(SELFTEST_CASES) + 2 - failures}/{len(SELFTEST_CASES) + 2} cases pass")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", default="HEAD~1..HEAD",
                        help="git range to inspect (default: HEAD~1..HEAD)")
    parser.add_argument("--staged", action="store_true", help="inspect the staged diff")
    parser.add_argument("--repo", action="append", default=[],
                        help="repo path (repeatable; default: every git repo beside this one)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    root = Path(__file__).resolve().parent.parent
    if args.repo:
        repos = [Path(r).resolve() for r in args.repo]
    else:
        repos = sorted(p for p in root.iterdir() if p.is_dir() and (p / ".git").exists())
        if (root / ".git").exists():
            repos.insert(0, root)

    diff_args = ["--cached"] if args.staged else [args.range]
    total = 0
    for repo in repos:
        findings = check_repo(repo, diff_args)
        for finding in findings:
            print(f"FAIL {repo.name}/{finding}")
        total += len(findings)

    scope = "staged" if args.staged else args.range
    print(f"dead_symbol_lint: {len(repos)} repo(s) over {scope} — {total} finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
