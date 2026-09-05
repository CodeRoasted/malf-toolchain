#!/usr/bin/env python3
"""malf contract-gen — the DERIVED contract surface of a Code & Comment as Contract repo (ADR-26.O3).

Reads the tagged comment lines and the law blocks of a directory and renders `contract.md`: per file,
per declaration, every tagged line it carries (`pre` / `post` / `invariant` / `assert` / `note`) with
its `refs:`, a `TEST` listed under its name; every law block in full; a `refs` index (address →
citing sites); and from the test tier, `TEST` name → `refs:`, so which contracts have a witness and
which laws have none is one table. The Founder ruled the all-forms surface the default on
2026-09-05 after the measurement (14 of 33 questions from the declarations-only file, 33 of 33 from
the all-forms one); `--declarations-only` renders the narrower shape.

The output is DERIVED and never committed: a committed copy would be a mirror of the source with no
enforced edge, the exact class the grammar removes. It prints to stdout, or to `--out PATH` when a
file is wanted (a scratch path). The parser is the gate's own (`comment_contract_lint.scan_comments`),
so what this renders is exactly what `malf format --check` counts.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comment_contract_lint as ccl  # noqa: E402

# BUILT, never a literal, for the reason `comment_contract_lint`'s own fixtures carry no law
# number: `scripts/registry_grammar_lint.py` sweeps the source tree for `D-LSRC-<n>` declarations
# AND — since 2026-09-05 — for `LSRC-<n>` citations. A literal `LSRC-5` here is a citation this  <!-- registry-lint: allow prose naming the token, not a site claiming it -->
# workspace never made, of a law LogCraft has not minted; measured that day, this file was one of
# the two specimen populations a naive sweep collected, and the only one outside the gate itself.
# The declaration literals below were already built for the declaration half of the same sweep.
_CITE = "LSRC" + "-"

CPP_EXT = {".cpp", ".cppm", ".hpp", ".h", ".cc", ".hh", ".ixx"}
SKIP_DIRS = {".git", ".conan2", ".cache", "node_modules"}
TAG_LINE = re.compile(r"^(pre|post|invariant|assert|note|refs):\s*(.*)$")
CONTINUATION_EXCLUDED = re.compile(r"^(NOLINT|clang-format|wall-clock:|SPDX-License-Identifier:)")
TEST_DECL = re.compile(r"^\s*(TEST|TEST_F|TEST_P|TYPED_TEST)\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)")
LSRC_CITE = re.compile(r"\bLSRC-(\d+)\b")


@dataclass
class Claim:
    tag: str
    text: str
    line: int


@dataclass
class Site:
    file: str
    line: int
    decl: str
    claims: list[Claim] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)

    @property
    def test_name(self) -> str:
        match = TEST_DECL.match(self.decl)
        return f"{match.group(2)}.{match.group(3)}" if match else ""


@dataclass
class Law:
    file: str
    line: int
    number: int
    title: str
    body: list[str]


def _code(line: str) -> str:
    return re.sub(r"\s*//.*$", "", line).rstrip()


def source_files(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
            continue
        for path in sorted(target.rglob("*")):
            if any(part in SKIP_DIRS or part.startswith("build") for part in path.relative_to(target).parts[:-1]):
                continue
            if path.is_file() and path.suffix in CPP_EXT:
                files.append(path)
    return files


def declaration_after(lines: list[str], last_comment_line: int) -> str:
    # The first line carrying code after the run; a run at end of file has no declaration.
    for index in range(last_comment_line, min(len(lines), last_comment_line + 40)):
        code = _code(lines[index]).strip()
        if code and not code.startswith("//"):
            return code
    return ""


def collect(path: Path, rel: str) -> tuple[list[Site], list[Law]]:
    text = path.read_bytes().decode("utf-8", "replace")
    lines = text.split("\n")
    sites: list[Site] = []
    laws: list[Law] = []
    run: list[ccl.Comment] = []

    def flush() -> None:
        if not run:
            return
        claims: list[Claim] = []
        refs: list[str] = []
        for comment in run:
            body = comment.lines[0][2:].strip()
            match = TAG_LINE.match(body)
            if match:
                tag, rest = match.group(1), match.group(2).strip()
                if tag == "refs":
                    refs.extend(address.strip() for address in rest.split(",") if address.strip())
                else:
                    claims.append(Claim(tag, rest, comment.line))
            elif claims and body and not CONTINUATION_EXCLUDED.match(body):
                claims[-1].text = f"{claims[-1].text} {body}".strip()
        run.clear()
        if claims or refs:
            decl = declaration_after(lines, run_last)
            sites.append(Site(rel, run_first, decl, claims, refs))

    run_first = run_last = 0
    for comment in ccl.scan_comments(text):
        if comment.kind == "block":
            first = comment.lines[0].strip()
            if ccl.LAW_OPEN.match(first) and len(comment.lines) >= 3:
                title_match = ccl.LAW_TITLE.match(comment.lines[1].strip())
                if title_match:
                    title_line = comment.lines[1].strip()
                    title = re.sub(r"^D-LSRC-\d+\s*[—–:-]\s*", "", title_line)
                    body = [line.rstrip() for line in comment.lines[2:-1]]
                    laws.append(Law(rel, comment.line, int(title_match.group(1)), title, body))
            flush()
            continue
        if comment.trailing:
            continue
        if run and comment.line == run_last + 1:
            run.append(comment)
            run_last = comment.line
        else:
            flush()
            run.append(comment)
            run_first = run_last = comment.line
    flush()
    return sites, laws


def render(root_label: str, sites: list[Site], laws: list[Law], declarations_only: bool = False) -> str:
    all_forms = not declarations_only
    shown = ("pre", "post", "invariant", "assert", "note") if all_forms else ("pre", "post", "invariant")
    out: list[str] = []
    today = _dt.date.today().isoformat()
    out.append(f"# contract.md — `{root_label}`")
    out.append("")
    out.append(f"DERIVED on {today} by `malf contract-gen` from the tagged comment lines and law blocks; "
               "never committed — regenerate it.")
    out.append("")
    contract_sites = [site for site in sites if any(c.tag in shown for c in site.claims) or site.refs]
    by_file: dict[str, list[Site]] = collections.defaultdict(list)
    for site in contract_sites:
        if all_forms or not site.test_name:
            by_file[site.file].append(site)
    out.append("## Contracts, per file and declaration")
    out.append("")
    if not by_file:
        out.append("_No tagged contract line in the population._")
        out.append("")
    for file in sorted(by_file):
        out.append(f"### `{file}`")
        out.append("")
        for site in by_file[file]:
            head = site.test_name or site.decl or "(end of file)"
            out.append(f"- `{head}` (line {site.line})")
            for claim in site.claims:
                if claim.tag in shown:
                    out.append(f"  - **{claim.tag}:** {claim.text}")
            if site.refs:
                out.append(f"  - **refs:** {', '.join(site.refs)}")
        out.append("")
    out.append("## Laws, in full")
    out.append("")
    if not laws:
        out.append("_No law block in the population._")
        out.append("")
    for law in sorted(laws, key=lambda l: l.number):
        out.append(f"### D-LSRC-{law.number} — {law.title}")
        out.append("")
        out.append(f"`{law.file}` line {law.line}")
        out.append("")
        out.extend(law.body)
        out.append("")
    index: dict[str, list[str]] = collections.defaultdict(list)
    for site in sites:
        for address in site.refs:
            label = site.test_name or site.decl or "(end of file)"
            index[address].append(f"`{site.file}` — `{label}`")
    out.append("## refs index (address → citing sites)")
    out.append("")
    if not index:
        out.append("_No `refs:` line in the population._")
    else:
        out.append("| address | citing sites |")
        out.append("|---|---|")
        for address in sorted(index):
            out.append(f"| `{address}` | {'; '.join(index[address])} |")
    out.append("")
    tests = [site for site in sites if site.test_name]
    out.append("## Test witnesses (TEST → refs)")
    out.append("")
    if not tests:
        out.append("_No TEST in the population._")
    else:
        out.append("| test | witnesses |")
        out.append("|---|---|")
        for site in tests:
            out.append(f"| `{site.test_name}` | {', '.join(f'`{r}`' for r in site.refs) if site.refs else '—'} |")
    out.append("")
    cited = {int(m) for site in sites for address in site.refs for m in LSRC_CITE.findall(address)}
    orphans = [law for law in laws if law.number not in cited]
    out.append("## Laws with no witness in this population")
    out.append("")
    if laws and not orphans:
        out.append("_Every law is cited by at least one `refs:` line here._")
    elif not laws:
        out.append("_No law block in the population._")
    else:
        for law in orphans:
            out.append(f"- `D-LSRC-{law.number}` — {law.title} (`{law.file}` line {law.line})")
    out.append("")
    return "\n".join(out)


def generate(targets: list[Path], root_label: str, declarations_only: bool = False) -> tuple[str, int, int]:
    files = source_files(targets)
    sites: list[Site] = []
    laws: list[Law] = []
    base = targets[0] if len(targets) == 1 and targets[0].is_dir() else Path.cwd()
    for path in files:
        try:
            rel = str(path.resolve().relative_to(base.resolve()))
        except ValueError:
            rel = str(path)
        file_sites, file_laws = collect(path, rel)
        sites.extend(file_sites)
        laws.extend(file_laws)
    return render(root_label, sites, laws, declarations_only), len(files), len(sites)


def selftest() -> int:
    passed = failed = 0

    def check(label: str, condition: bool) -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  ok   {label}")
        else:
            failed += 1
            print(f"  FAIL {label}")

    with tempfile.TemporaryDirectory(prefix="malf_contract_gen_selftest.") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src" / "clean.cpp").write_text(ccl.CLEAN_FIXTURE.replace(ccl.LAW_NUMBER_TOKEN, "5"))
        (root / "tests" / "test_x.cpp").write_text(
            "// refs: " + _CITE + "5, ADR-31.D9\n"
            "// invariant: the witness of the law above.\n"
            "TEST(Subject, PropertyHoldsUnderCondition)\n{\n}\n"
            "// note: a note with no refs.\n"
            "TEST(Subject, UnwitnessedProperty)\n{\n}\n")
        (root / "tests" / "test_y.cpp").write_text(
            "/****************************************************************************************************\n"
            "D-LSRC-" + "7" + " — an orphan law\nNobody cites this one.\n"
            "****************************************************************************************************/\n"
            "int f();\n")
        text, files, sites = generate([root], "selftest")
        check("walks every C++ file under the directory (3)", files == 3)
        check("a declaration carries its pre/post/invariant and refs", "**pre:**" in text and "**post:**" in text and "**invariant:**" in text)
        check("the law block is rendered in full under its number", "### D-LSRC-" + "5" + " —" in text)
        check("the refs index maps an address to its citing sites",
              "| `" + _CITE + "5` |" in text and "`Subject.PropertyHoldsUnderCondition`" in text)
        check("the test table lists a witnessed test with its refs and an unwitnessed one with a dash",
              "| `Subject.PropertyHoldsUnderCondition` | `" + _CITE + "5`, `ADR-31.D9` |" in text
              and "| `Subject.UnwitnessedProperty` | — |" in text)
        check("a law with no LSRC citer is listed as having no witness, a cited one is not",
              "`D-LSRC-" + "7` — an orphan law" in text
              and "`D-LSRC-" + "5` —" not in text.split("## Laws with no witness")[1])
        check("the output declares itself derived and never committed", "never committed" in text)
        narrow, _, _ = generate([root], "selftest", declarations_only=True)
        check("the default renders assert: and note: lines and lists a TEST body under its name",
              "**assert:**" in text and "**note:**" in text and "- `Subject.UnwitnessedProperty`" in text)
        check("--declarations-only leaves assert: and note: out", "**assert:**" not in narrow and "**note:**" not in narrow)
        empty = root / "empty"
        empty.mkdir()
        text, files, sites = generate([empty], "empty")
        check("an empty population says so in every section rather than rendering a blank", files == 0 and "_No tagged contract line" in text)
    print(f"contract_gen selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="malf contract-gen", description=__doc__.split("\n\n")[0])
    parser.add_argument("targets", nargs="*", help="directories or files; a directory is walked for C++ sources")
    parser.add_argument("--out", metavar="PATH", help="write contract.md here instead of stdout (a scratch path, never a tracked one)")
    parser.add_argument("--declarations-only", action="store_true",
                        help="render only pre/post/invariant at declarations, no assert:/note: and no TEST bodies (the narrower shape; all forms is the ruled default)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.targets:
        parser.error("name a directory or a file")
    targets = [Path(t) for t in args.targets]
    missing = [str(t) for t in targets if not t.exists()]
    if missing:
        parser.error(f"no such path: {', '.join(missing)}")
    label = ", ".join(str(t) for t in targets)
    text, files, sites = generate(targets, label, args.declarations_only)
    if args.out:
        Path(args.out).write_text(text)
        print(f"contract-gen: {files} file(s), {sites} tagged site(s) → {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
        print(f"contract-gen: {files} file(s), {sites} tagged site(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
