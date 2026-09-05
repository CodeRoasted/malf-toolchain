#!/usr/bin/env python3
"""Code & Comment as Contract — the comment-grammar phase of `malf format` (DN-90.D5).

Every `//` comment line in an ARMED repo must begin with exactly one of six tags
(`pre:` `post:` `invariant:` `assert:` `note:` `refs:`) and stand on its own line. A CONTRACT
form (the first four) may run to a second line carrying no tag — the Founder's budget ruling of
2026-09-05, taken on the api unit, where one-line contracts were being split into two tagged
lines that were one claim; `note:` and `refs:` stay strictly one line, the note being the form
that must be starved. The only multi-line comment is the framed law block declaring a
`D-LSRC-n`; a handful of TOOL
forms are admitted because a machine reads them (clang-format's `} // namespace x`, clang-tidy's
`/*name=*/` and `/*name*/`, own-line `NOLINT…`, `clang-format off/on`). Everything else is a violation, and
the default disposition of a violating comment is deletion — never a new tag.

WHY THIS READS THE POST-FORMAT TEXT, and it is the reason the phase lives inside `malf format`
rather than beside it. `.clang-format` sets `ReflowComments: true`, and the reflow treats
consecutive `//` lines as one paragraph. Measured 2026-09-05 with clang-format 21.1.8: an
overlong `// pre:` line followed by a `// post:` line came back as two lines where the `post:`
tag sits MID-LINE on the continuation and the second line begins with no tag at all. A gate that
read the file before formatting would pass the `pre:` and never see the `post:` vanish. So in
check mode — where clang-format rewrites nothing — this checker formats each file to stdout
itself and judges that; in write mode the disk already holds the formatted bytes. The selftest
pins exactly that shape: the fixture is CLEAN before formatting and RED after.

ARMING is the repo's own declaration: a top-level `comment_contract: true` in its `packages.yml`
(the Founder, 2026-09-05, DN-90.O2 (d)). A file under an unarmed repo is COUNTED — every form,
every would-be violation — and never failed, so a repo's numbers exist before its migration is
scheduled and the 90 % target has an instrument. Sites are printed for armed files, and for
report-only files when the caller named the paths explicitly (a lane converting one unit wants
its sites; a whole-repo sweep wants the count).

WHAT IT DELIBERATELY DOES NOT DO. It checks the FORM of a `refs:` address, never its RESOLUTION
— that is `scripts/registry_grammar_lint.py`'s (G4, G5, G7, the LSRC arm), and one source per rule
(ADR-6.D14). It does not check WHERE a tag sits (a `pre:` at a declaration versus in a body) —
that is a read rule; parsing C++ for it would buy a second, weaker parser. It does not judge
whether a `note:` deserves to exist — the cold-reader interrogation of DN-90.D6 does.

Usage:
    comment_contract_lint.py [--mode M] [--format-via BIN --style STYLE [--mem-limit-kb N]]
                             [--sites] (--files0 - | FILE...)
    comment_contract_lint.py --selftest [--format-via BIN]
"""
from __future__ import annotations

import argparse
import collections
import functools
import os
import re
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # the same loader pin_coherence.py reads packages.yml with
except ImportError:  # pragma: no cover — the workspace ships pyyaml; the fallback keeps the gate honest
    yaml = None

TAGS = ("pre", "post", "invariant", "assert", "note", "refs")
TAG_HEAD = re.compile(r"(pre|post|invariant|assert|note|refs):(.*)$", re.S)
# A tag token that is NOT at the head of the comment: the reflow-swallow shape. The lookbehind
# keeps quoted mentions (`note:`) and URL-ish text out of it.
TAG_MID = re.compile(r"(?<![\w`'\"/.:-])(pre|post|invariant|assert|note|refs):(\s|$)")

# The registry forms a `refs:` may carry — LEXICON.md § Shortcut registry, minus the forms that are
# not source-citable addresses: `MEMN-n` (banned from source, registry_grammar_lint G10), `Gn`
# (a local ordinal, DN-70) and `<Name> · <Index>` (a register row, not an address).
REF_FORM = re.compile(
    r"""^(?:
        ADR-\d+(?:\.[DO]\d+)? | DN-\d+(?:\.[DO]\d+)? | STU-\d+(?:\.[QAO]\d+)? |
        OPS-\d+(?:\.[SO]\d+)? | PRD-\d+(?:\.[PBO]\d+)? | LIM-\d+ | LSRC-\d+ |
        SRC-[A-Z][A-Z0-9-]*[0-9] | F-SRC-[a-z0-9-]+:[^\s,:]+(?::[^\s,]+)? |
        BUG-\d+ | FLAW-\d+ | OQ-\d+ | LEX:[A-Za-z][\w-]* | MEM:[a-z0-9-]+ | BIB:[a-z0-9_]+
    )$""",
    re.X,
)

LAW_RULE_MIN = 20
LAW_OPEN = re.compile(r"^/\*{%d,}$" % LAW_RULE_MIN)
LAW_CLOSE = re.compile(r"^\*{%d,}/$" % LAW_RULE_MIN)
LAW_TITLE = re.compile(r"^D-LSRC-(\d+)\s*[—–:-]\s*\S")
RULER = re.compile(r"^[-=─—_*#~.]{4,}")
NOLINT = re.compile(r"^NOLINT(NEXTLINE|BEGIN|END)?\b")
ARG_COMMENT = re.compile(r"^/\*\w+=\*/$")
# `/*name*/` on an UNNAMED parameter is clang-tidy's other argument form: `readability-named-parameter`
# accepts it as the name. Admitted 2026-09-05 after a census found the stripper had deleted eight of them
# (1 in src, 7 in benchmarks) as block prose, leaving `push(LogRecord&&)` unnamed — a red that check
# would raise, and no gate had run it. A one-line `/*word*/` is only ever this form or an argument
# comment missing its `=`; both have a machine reader, so both are tool forms.
NAMED_PARAM_COMMENT = re.compile(r"^/\*[A-Za-z_]\w*\*/$")

VIOLATION_CLASSES = (
    "bare", "tag-mid-line", "slash3", "spacer", "ruler", "trailing", "trailing-nolint",
    "suppression-without-why", "empty-claim", "refs-prose", "note-run", "block-prose",
    "law-malformed",
)
CONTRACT_TAGS = ("pre", "post", "invariant", "assert")
FORM_CLASSES = TAGS + ("continuation", "law", "tool")

# ── the scanner ───────────────────────────────────────────────────────────────────────────────
# A `//` inside a string literal is not a comment, and this corpus has them: measured on
# logcraft/core, 25 ordinary literals and 2 raw strings carry `//` (URLs, YAML in R"()"). So
# comments are extracted by a state scan over literals, never by a line regex. The regex below
# only finds the next character that can CHANGE state; the loop decides what it was.
INTEREST = re.compile(r'R"([^\s()\\"]{0,16})\(|"|\'|//|/\*|\n')
RAW_PREFIXES = ("", "u8", "u", "U", "L")


@dataclass
class Comment:
    kind: str                 # "line" | "block"
    line: int                 # 1-based line of the opener
    col: int
    lines: list[str]          # raw text including the delimiters, split on newlines
    code_before: str          # source text on the opener's line before the comment
    code_after: str = ""      # source text on the closer's line after a block comment

    @property
    def trailing(self) -> bool:
        return self.code_before.strip() != ""


def _skip_quoted(text: str, pos: int, quote: str) -> int:
    while pos < len(text):
        ch = text[pos]
        if ch == "\\":
            pos += 2
            continue
        if ch == quote:
            return pos + 1
        if ch == "\n":  # unterminated on this line: resume as code, never swallow the file
            return pos
        pos += 1
    return pos


def scan_comments(text: str) -> list[Comment]:
    comments: list[Comment] = []
    pos, line, line_start = 0, 1, 0
    while True:
        match = INTEREST.search(text, pos)
        if not match:
            break
        tok = match.group(0)
        start = match.start()
        if tok == "\n":
            line, line_start, pos = line + 1, match.end(), match.end()
            continue
        if tok.startswith('R"'):
            back = start
            while back > 0 and (text[back - 1].isalnum() or text[back - 1] == "_"):
                back -= 1
            if text[back:start] in RAW_PREFIXES:
                delim = match.group(1)
                end = text.find(")" + delim + '"', match.end())
                end = len(text) if end < 0 else end + len(delim) + 2
                newlines = text.count("\n", start, end)
                if newlines:
                    line += newlines
                    line_start = text.rfind("\n", start, end) + 1
                pos = end
                continue
            pos = start + 1  # an identifier ending in R, then a plain string: re-scan at the quote
            continue
        if tok == '"':
            pos = _skip_quoted(text, match.end(), '"')
            continue
        if tok == "'":
            prev = text[start - 1] if start > 0 else ""
            nxt = text[match.end()] if match.end() < len(text) else ""
            if (prev.isdigit() or prev in "abcdefABCDEF") and (nxt.isalnum()):
                pos = match.end()  # a digit separator: 1'000'000, 0xFF'FF
                continue
            pos = _skip_quoted(text, match.end(), "'")
            continue
        if tok == "//":
            end = text.find("\n", match.end())
            end = len(text) if end < 0 else end
            comments.append(Comment("line", line, start - line_start + 1,
                                    [text[start:end]], text[line_start:start]))
            pos = end
            continue
        # "/*"
        end = text.find("*/", match.end())
        end = len(text) if end < 0 else end + 2
        raw = text[start:end]
        after_end = text.find("\n", end)
        after_end = len(text) if after_end < 0 else after_end
        comments.append(Comment("block", line, start - line_start + 1, raw.split("\n"),
                                text[line_start:start], text[end:after_end]))
        newlines = raw.count("\n")
        if newlines:
            line += newlines
            line_start = start + raw.rfind("\n") + 1
        pos = end
    return comments


# ── the grammar ───────────────────────────────────────────────────────────────────────────────
@dataclass
class Finding:
    line: int
    col: int
    klass: str          # a VIOLATION_CLASSES member or a FORM_CLASSES member
    text: str
    is_violation: bool


def classify(comments: list[Comment]) -> list[Finding]:
    findings: list[Finding] = []
    # Own-line tagged lines by source line, for the two look-behind rules (note-run, NOLINT why).
    tag_at_line: dict[int, str] = {}

    def add(comment: Comment, klass: str, text: str | None = None) -> None:
        findings.append(Finding(comment.line, comment.col, klass,
                                text if text is not None else comment.lines[0].rstrip(),
                                klass in VIOLATION_CLASSES))

    for comment in comments:
        if comment.kind == "block":
            first, last = comment.lines[0].strip(), comment.lines[-1].strip()
            if len(comment.lines) == 1 and (ARG_COMMENT.match(first) or NAMED_PARAM_COMMENT.match(first)):
                add(comment, "tool")
                continue
            if LAW_OPEN.match(first) or first.startswith("/*" + "*" * 4):
                well_framed = (len(comment.lines) >= 3 and LAW_OPEN.match(first)
                               and LAW_CLOSE.match(last) and not comment.trailing
                               and comment.code_after.strip() == ""
                               and LAW_TITLE.match(comment.lines[1].strip()))
                add(comment, "law" if well_framed else "law-malformed",
                    comment.lines[1].strip() if len(comment.lines) > 1 else first)
                continue
            add(comment, "block-prose")
            continue

        body = comment.lines[0][2:]
        if body.startswith("/"):
            add(comment, "slash3")
            continue
        stripped = body.strip()
        if stripped == "":
            add(comment, "spacer")
            continue
        if RULER.match(stripped):
            add(comment, "ruler")
            continue
        nolint = NOLINT.match(stripped)
        if nolint:
            if comment.trailing:
                add(comment, "trailing-nolint")
            elif nolint.group(1) in ("NEXTLINE", "BEGIN"):
                if tag_at_line.get(comment.line - 1) in ("note", "refs"):
                    add(comment, "tool")
                else:
                    add(comment, "suppression-without-why")
            elif nolint.group(1) == "END":
                add(comment, "tool")
            else:
                add(comment, "bare")
            continue
        if stripped in ("clang-format off", "clang-format on"):
            add(comment, "tool")
            continue
        # logcraft's time-source lint (tests/determinism/test_time_source_lint.cpp) reads a
        # `wall-clock:` marker on the STATEMENT of a wall-clock read as the reviewed waiver, and
        # that marker is trailing by construction. A machine reads it, so it is a tool form.
        if stripped.startswith("wall-clock:"):
            add(comment, "tool")
            continue
        # An SPDX licence identifier is read by licence scanners (REUSE, scancode), never by a
        # person: a tool form, kept where the file already carries one. The grammar admits it and
        # says nothing about whether the identifier is RIGHT — that is the licence audit's question.
        if stripped.startswith("SPDX-License-Identifier:"):
            add(comment, "tool")
            continue
        if comment.trailing:
            if comment.code_before.rstrip().endswith("}") and re.match(r"namespace(\s|$)", stripped):
                add(comment, "tool")
            else:
                add(comment, "trailing")
            continue
        head = TAG_HEAD.match(stripped)
        if head:
            tag, claim = head.group(1), head.group(2).strip()
            if not claim:
                add(comment, "empty-claim")
                continue
            if tag == "refs":
                bad = [tok for tok in (t.strip() for t in claim.split(",")) if not REF_FORM.match(tok)]
                if bad:
                    add(comment, "refs-prose", f"{comment.lines[0].rstrip()}   <- not an address: {bad[0]!r}")
                    continue
            if tag == "note" and tag_at_line.get(comment.line - 1) == "note":
                add(comment, "note-run")
                continue
            if TAG_MID.search(claim):
                add(comment, "tag-mid-line")
                continue
            tag_at_line[comment.line] = tag
            add(comment, tag)
            continue
        if TAG_MID.search(stripped):
            add(comment, "tag-mid-line")
            continue
        # The one continuation a contract form may carry: the line directly above is a contract
        # tag (never a note, a refs, or another continuation). The reflow-swallow shape stays
        # caught because the mid-line tag check above runs first.
        if tag_at_line.get(comment.line - 1) in CONTRACT_TAGS:
            tag_at_line[comment.line] = "continuation"
            add(comment, "continuation")
            continue
        add(comment, "bare")
    return findings


# ── arming ────────────────────────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=None)
def repo_and_arming(directory: Path) -> tuple[Path | None, bool]:
    for candidate in (directory, *directory.parents):
        manifest = candidate / "packages.yml"
        if manifest.is_file():
            raw = manifest.read_text(encoding="utf-8", errors="replace")
            if yaml is not None:
                try:
                    data = yaml.safe_load(raw) or {}
                except yaml.YAMLError:
                    return candidate, False
                return candidate, isinstance(data, dict) and data.get("comment_contract") is True
            return candidate, re.search(r"^comment_contract:\s*true\s*$", raw, re.M) is not None
    return None, False


# ── reading a file, pre- or post-format ───────────────────────────────────────────────────────
def read_formatted(path: Path, clang_format: str, style: str, mem_limit_kb: int | None) -> tuple[str | None, str]:
    def limit() -> None:
        if mem_limit_kb:
            cap = mem_limit_kb * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

    proc = subprocess.run([clang_format, f"-style={style}", str(path)], capture_output=True,
                          preexec_fn=limit if mem_limit_kb else None)
    if proc.returncode != 0:
        first = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, f"clang-format rc={proc.returncode}: {first[0] if first else 'no stderr'}"
    return proc.stdout.decode("utf-8", "replace"), ""


# ── the run ───────────────────────────────────────────────────────────────────────────────────
@dataclass
class FileResult:
    path: Path
    repo: Path | None
    armed: bool
    findings: list[Finding] = field(default_factory=list)
    not_checked: str = ""
    comment_lines: int = 0


def check_files(paths: list[Path], format_via: str | None, style: str, mem_limit_kb: int | None) -> list[FileResult]:
    results: list[FileResult] = []
    for path in paths:
        repo, armed = repo_and_arming(path.resolve().parent)
        result = FileResult(path, repo, armed)
        if format_via:
            text, why = read_formatted(path, format_via, style, mem_limit_kb)
            if text is None:
                result.not_checked = why
                results.append(result)
                continue
        else:
            text = path.read_bytes().decode("utf-8", "replace")
        comments = scan_comments(text)
        result.comment_lines = sum(len(c.lines) for c in comments)
        result.findings = classify(comments)
        results.append(result)
    return results


def summarize(results: list[FileResult], mode: str, show_sites: bool, out=sys.stdout) -> int:
    forms = collections.Counter()
    armed_viol = collections.Counter()
    plain_viol = collections.Counter()
    armed_repos: set[str] = set()
    n_armed = n_plain = n_not_checked = 0
    total_comment_lines = 0
    for result in results:
        if result.not_checked:
            n_not_checked += 1
            print(f"{result.path}: NOT CHECKED ({result.not_checked})", file=out)
            continue
        total_comment_lines += result.comment_lines
        if result.armed:
            n_armed += 1
            armed_repos.add(result.repo.name if result.repo else "?")
        else:
            n_plain += 1
        for finding in result.findings:
            if not finding.is_violation:
                forms[finding.klass] += 1
                continue
            (armed_viol if result.armed else plain_viol)[finding.klass] += 1
            if result.armed or show_sites:
                tag = "CCC" if result.armed else "CCC(report-only)"
                print(f"{result.path}:{finding.line}:{finding.col}: {tag} {finding.klass}: {finding.text}", file=out)

    def counts(counter: collections.Counter, keys) -> str:
        return " ".join(f"{k}={counter[k]}" for k in keys if counter[k]) or "none"

    armed_total = sum(armed_viol.values())
    plain_total = sum(plain_viol.values())
    armed_not_checked = sum(1 for r in results if r.not_checked and r.armed)
    rc = 1 if (armed_total or armed_not_checked) else 0
    n_files = len(results)
    if n_files == 0:
        print(f"malf format: CCC SUMMARY · mode={mode} · CHECKED 0 — NOTHING WAS INSPECTED · rc={rc}", file=out)
        return rc
    print(
        f"malf format: CCC SUMMARY · mode={mode} · files {n_files} = armed {n_armed} + report-only {n_plain}"
        f" + NOT CHECKED {n_not_checked} · armed repos: {', '.join(sorted(armed_repos)) or 'none'}"
        f" · comment lines {total_comment_lines} · forms {counts(forms, FORM_CLASSES)}"
        f" · violations in armed files {armed_total} ({counts(armed_viol, VIOLATION_CLASSES)})"
        f" · would-be violations in report-only files {plain_total} ({counts(plain_viol, VIOLATION_CLASSES)})"
        f" · rc={rc}",
        file=out,
    )
    if rc and armed_not_checked:
        print(f"malf format: CCC · {armed_not_checked} armed file(s) were NOT CHECKED — that is lost coverage,"
              " not a clean verdict; read the NOT CHECKED lines above.", file=out)
    return rc


# ── selftest ──────────────────────────────────────────────────────────────────────────────────
# The fixtures carry NO literal law number: `scripts/registry_grammar_lint.py` sweeps every
# source file — this one included — for `D-LSRC-<digits>` declarations and `LSRC-<digits>`
# citations and checks the numbering dense, so a literal `5` here would be a declaration the
# workspace never made (measured 2026-09-05: the first cut of this file reddened that gate's
# density arm — the fixture's number "existed" while two lower ones were declared nowhere — and
# a second cut reddened it again from THIS comment, which quoted the token with its digit). The
# number is spliced in at run time, the same remedy that gate applied to its own fixtures.
LAW_NUMBER_TOKEN = "@N@"
CLEAN_FIXTURE = '''/*****************************************************************************
D-LSRC-@N@ — a law block is the one multi-line comment form
Body prose may be long, and the formatter may re-wrap it; the frame survives.
*****************************************************************************/
#include <string>
namespace demo
{
// pre: `path` names an existing workspace.
// post: on failure, externally observable state is unchanged, and the refusal names the
// first invalid entry of the manifest.
// invariant: state_.size() == index_.size()
// refs: ADR-10.D3, LSRC-@N@, F-SRC-logcraft:core.api-value.cppm:to_string, SRC-SID-3
int open(const std::string& path);

void take(int /*unused*/);

// note: clang-tidy cannot see the forward through the macro.
// NOLINTNEXTLINE(cppcoreguidelines-missing-std-forward)
void process(int& state)
{
    // assert: every entry in `state` has been normalized.
    // refs: DN-90.D2
    // NOLINTBEGIN(readability-magic-numbers)
    const char* url = "http://example.test//x";
    const char* raw = R"yaml(
    # a yaml comment, and a // that is not C++
    key: value
    )yaml";
    const char quote = '"';
    const int big = 1'000'000;
    // NOLINTEND(readability-magic-numbers)
    // clang-format off
    call(/*deterministic=*/true, 1);
    // clang-format on
    const auto started = now(); // wall-clock: real-time-mode path (a lint waiver a machine reads)
    // SPDX-License-Identifier: Apache-2.0
    (void)url; (void)raw; (void)quote; (void)big; (void)state; (void)started;
}
} // namespace demo
'''

SWALLOW_FIXTURE = '''namespace demo
{
// pre: `path` identifies an existing workspace whose manifest has been validated by the loader and whose ratification is green.
// post: failure leaves externally observable state unchanged.
int open(int path);
} // namespace demo
'''

VIOLATION_FIXTURES: dict[str, str] = {
    "bare": "int x;\n// this is a comment\n",
    "bare (third line of a contract — one continuation is the budget)":
        "int x;\n// pre: one\n// two\n// three\n",
    "bare (a note has no continuation)": "int x;\n// note: one line is the whole note\n// and this is bare\n",
    "bare (a refs has no continuation)": "int x;\n// refs: ADR-10.D3\n// and this is bare\n",
    "tag-mid-line": "int x;\n// whose ratification is green. post: failure leaves state unchanged.\n",
    "slash3": "int x;\n/// a doxygen-looking line\n",
    "spacer": "int x;\n//\n",
    "ruler": "int x;\n// ========================\n",
    "trailing": "int x; // note: trailing forms are not forms\n",
    "trailing-nolint": "int x; // NOLINT(some-check)\n",
    "suppression-without-why": "int x;\n// NOLINTNEXTLINE(some-check)\nint y;\n",
    "empty-claim": "int x;\n// pre:\n",
    "refs-prose": "int x;\n// refs: ADR-10.D3, because the loader said so\n",
    "note-run": "int x;\n// note: one line is the whole budget\n// note: and a second line is a doc paragraph\n",
    "block-prose": "int x;\n/* a block of prose\n   over two lines */\n",
    "law-malformed": "int x;\n/**************************\nnot a D-LSRC title line\n**************************/\n",
}


def selftest(format_via: str | None) -> int:
    passed = failed = 0

    def check(label: str, expected, actual) -> None:
        nonlocal passed, failed
        if expected == actual:
            passed += 1
            print(f"  ok   {label}")
        else:
            failed += 1
            print(f"  FAIL {label}\n       expected: {expected!r}\n       actual:   {actual!r}")

    with tempfile.TemporaryDirectory(prefix="malf_ccc_selftest.") as tmp:
        root = Path(tmp)
        armed = root / "armed"
        plain = root / "plain"
        (armed / "src").mkdir(parents=True)
        (plain / "src").mkdir(parents=True)
        (armed / "packages.yml").write_text("comment_contract: true\npackages: {}\n")
        (plain / "packages.yml").write_text("packages: {}\n")

        clean = armed / "src" / "clean.cpp"
        clean.write_text(CLEAN_FIXTURE.replace(LAW_NUMBER_TOKEN, "5"))
        [res] = check_files([clean], None, "", None)
        viol = [f for f in res.findings if f.is_violation]
        check("clean fixture: zero violations", [], [(f.klass, f.text) for f in viol])
        forms = collections.Counter(f.klass for f in res.findings if not f.is_violation)
        check("clean fixture: every form counted once where expected (the post: carries its one continuation)",
              {"pre": 1, "post": 1, "invariant": 1, "assert": 1, "note": 1, "refs": 2, "continuation": 1, "law": 1},
              {k: forms[k] for k in TAGS + ("continuation", "law") if forms[k]})
        check("clean fixture: tool forms (namespace, arg comment, named parameter, NOLINTNEXTLINE, BEGIN, END, off, on, wall-clock waiver, SPDX)",
              10, forms["tool"])
        check("scanner: `//` inside a literal, a raw string, a char literal and a digit separator are not comments",
              True, all("http" not in f.text and "yaml" not in f.text for f in res.findings))

        for label, source in VIOLATION_FIXTURES.items():
            klass = label.split(" ")[0]
            fixture = armed / "src" / f"{len(label)}_{klass}.cpp"
            fixture.write_text(source)
            [res] = check_files([fixture], None, "", None)
            got = sorted({f.klass for f in res.findings if f.is_violation})
            check(f"violation class fires: {label}", [klass], got)

        twin = plain / "src" / "bare.cpp"
        twin.write_text(VIOLATION_FIXTURES["bare"])
        import io
        buf = io.StringIO()
        rc = summarize(check_files([twin], None, "", None), "check-sweep", False, out=buf)
        check("unarmed repo: a violation is counted, never failed (rc 0)", 0, rc)
        check("unarmed repo: the summary names it report-only with its would-be count",
              True, "report-only 1" in buf.getvalue() and "would-be violations in report-only files 1" in buf.getvalue())
        armed_bare = armed / "src" / "bare_armed.cpp"
        armed_bare.write_text(VIOLATION_FIXTURES["bare"])
        buf = io.StringIO()
        rc = summarize(check_files([armed_bare], None, "", None), "check-sweep", False, out=buf)
        check("armed repo: the same violation fails (rc 1)", 1, rc)
        buf = io.StringIO()
        rc = summarize([], "check-sweep", False, out=buf)
        check("zero files is announced as NOTHING WAS INSPECTED, never as a pass",
              True, "NOTHING WAS INSPECTED" in buf.getvalue())

        swallow = armed / "src" / "swallow.cpp"
        swallow.write_text(SWALLOW_FIXTURE)
        [res] = check_files([swallow], None, "", None)
        check("reflow-swallow fixture is CLEAN before formatting (the blindness a pre-format read has)",
              [], [f.klass for f in res.findings if f.is_violation])
        if format_via:
            style_file = root / ".clang-format"
            style_file.write_text("BasedOnStyle: LLVM\nColumnLimit: 100\nReflowComments: true\n")
            [res] = check_files([swallow], format_via, f"file:{style_file}", None)
            check("reflow-swallow fixture is RED after formatting: the swallowed post: is caught",
                  True, res.not_checked == "" and any(f.klass == "tag-mid-line" for f in res.findings))
            [res] = check_files([clean], format_via, f"file:{style_file}", None)
            check("clean fixture stays clean THROUGH clang-format (the law frame and every tag survive the reflow)",
                  [], [(f.klass, f.text) for f in res.findings if f.is_violation] if not res.not_checked else [res.not_checked])
        else:
            print("  SKIP the post-format leg: no --format-via given (pass the clang-format binary to run it)")

    print(f"comment_contract_lint selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="standalone")
    parser.add_argument("--format-via", metavar="CLANG_FORMAT")
    parser.add_argument("--style", default="file")
    parser.add_argument("--mem-limit-kb", type=int)
    parser.add_argument("--sites", action="store_true", help="print sites for report-only files too")
    parser.add_argument("--files0", metavar="-", help="read NUL-separated paths from stdin ('-')")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(args.format_via)

    paths = [Path(p) for p in args.files]
    if args.files0 == "-":
        paths += [Path(p) for p in sys.stdin.buffer.read().decode("utf-8", "replace").split("\0") if p]
    if not paths:
        print("comment_contract_lint: no files given (use --files0 - or FILE...)", file=sys.stderr)
        return 2
    show_sites = args.sites or args.mode.endswith("-paths")
    results = check_files(paths, args.format_via, args.style, args.mem_limit_kb)
    return summarize(results, args.mode, show_sites)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
