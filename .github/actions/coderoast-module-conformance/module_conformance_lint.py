#!/usr/bin/env python3
"""Static C++-modules conformance lint for the CodeRoast workspace.

The automated form of the whole-cascade modules audit
(technical_docs/adr/0015-cxx-named-modules-policy.md — the load-bearing rules (MUSTs); granular as-built detail in git history + memory) — the
checks that are UNCONDITIONAL across every repo, package, and module unit.
Judgment-call template items (sizing-rule layer counts, member-vs-consumer
test targets, aggregate adoption) are deliberately NOT linted: those carry
per-package recorded rulings in their CMake headers and a grep cannot
adjudicate them.

What IS linted (exit 1 on any unannotated hit):

  RULE-1  detail stays sealed (§2): no module unit may
          `export import <pkg>.detail[.shard]` — re-exporting a detail shard
          publishes sealed internals through the BMI. The ONLY legitimate
          re-exporters are the white-box test/bench aggregates
          (*.test.cppm / *.bench.cppm — module members, never installed).

  RULE-2  internal stays a plain import (§2): no module
          unit may `export import <pkg>.internal` — the std manifest is
          sealed by NON-re-export; api/facade/detail import it PLAIN.

  RULE-3  no `export using` surface re-homing (a retired re-homing mechanism):
          `export using` in a non-internal, non-aggregate interface unit is
          the banned "re-export a detail symbol over a plain import" shape.
          (`<pkg>.internal` keeps its C-type spellings; aggregates re-export
          whole modules, not names.)

  RULE-4  no `thread_local` in any module unit's code (§2): a
          thread_local in a BMI leaks `__cxa_thread_atexit` machinery into
          every importer (the 1.5.1 gtest wall, f58220e). Definitions belong
          in impl units behind an out-of-line accessor.

  RULE-5  no C-library macro / syscall token in a module unit (§2):
          errno / mmap / shm_open / off_t / O_* / PROT_* / MAP_* … cannot
          ride an import-std BMI; they live in textual impl units (the ipc
          shape: `detail::shm_open_create()` wrapper DECLS are fine — the
          token match is word-bounded, so wrapper names don't trip it).

  RULE-6  the install seal exists (§2): any CMakeLists
          that places a `*.detail*.cppm` in a module file set must also
          declare the PRIVATE `cxx_modules_detail` file set — otherwise the
          sealed shards ship to consumers.

Opt-out: a line — or the line immediately above (`MODULES-ALLOW-NEXTLINE`) —
carrying `MODULES-ALLOW(<reason>)` exempts that hit. Every exemption is
printed so the audit trail stays visible.

Usage:
  python3 scripts/module_conformance_lint.py [ROOT]   # ROOT defaults to '.'
  Exit 0 = conformant, exit 1 = violations (verbose per-violation report).

Checkout-agnostic like pin_coherence.py: it lints whatever module units are
on disk under ROOT, so it works in the superproject, a single repo, or CI.
"""

from __future__ import annotations

import pathlib
import re
import sys

IGNORE_PARTS = {".conan2", ".git", ".venv", "node_modules", "bench_results"}

ALLOW = re.compile(r"MODULES-ALLOW\(([^)]*)\)")
ALLOW_NEXT = "MODULES-ALLOW-NEXTLINE"

# Aggregates are the sanctioned re-exporters (white-box module members).
AGGREGATE = re.compile(r"\.(test|bench)\.cppm$")
INTERNAL_UNIT = re.compile(r"\.?internal\.cppm$")

EXPORT_IMPORT_DETAIL = re.compile(r"^\s*export\s+import\s+[\w.]*\.detail\b")
EXPORT_IMPORT_INTERNAL = re.compile(r"^\s*export\s+import\s+[\w.]*\.internal\s*;")
EXPORT_USING = re.compile(r"^\s*export\s+using\b")
THREAD_LOCAL = re.compile(r"\bthread_local\b")
# RULE-5 token set: the POSIX/C macro + syscall names the cascade actually hit.
C_MACRO = re.compile(
    r"\b(errno|mmap|munmap|shm_open|shm_unlink|ftruncate|fcntl"
    r"|off_t|O_CREAT|O_RDWR|O_EXCL|PROT_READ|PROT_WRITE|MAP_SHARED|MAP_FAILED)\b")

DETAIL_UNIT_REF = re.compile(r"[\w./${}]*\.detail[\w.-]*\.cppm")


def skip(path: pathlib.Path) -> bool:
    # "build" + every profile-named tree (build-gcc15-release, build-clang21-…) —
    # build dirs hold CMake-copied module units that must not be double-linted.
    return any(part in IGNORE_PARTS or part == "build" or part.startswith("build-")
               or part.startswith("linux-")  # repo-root profile-named build trees (legacy)
               for part in path.parts)


def strip_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Drop //-comments and /* */ block-comment content from one line.

    Good enough for lint purposes: string literals containing comment markers
    would over-strip, but module interface units don't carry such literals on
    rule-relevant lines (and an over-strip only ever HIDES a token, which the
    live-tree green run + the synthetic self-test keep honest).
    """
    out: list[str] = []
    index = 0
    while index < len(line):
        if in_block:
            end = line.find("*/", index)
            if end == -1:
                return "".join(out), True
            index = end + 2
            in_block = False
            continue
        line_comment = line.find("//", index)
        block_open = line.find("/*", index)
        if line_comment != -1 and (block_open == -1 or line_comment < block_open):
            out.append(line[index:line_comment])
            return "".join(out), False
        if block_open != -1:
            out.append(line[index:block_open])
            index = block_open + 2
            in_block = True
            continue
        out.append(line[index:])
        break
    return "".join(out), in_block


def lint(root: pathlib.Path) -> int:
    violations: list[str] = []
    exemptions: list[str] = []

    module_units = [p for p in sorted(root.rglob("*.cppm")) if not skip(p)]
    if not module_units:
        print(f"module_conformance_lint: no .cppm module units under {root}",
              file=sys.stderr)
        return 1

    for unit in module_units:
        is_aggregate = bool(AGGREGATE.search(unit.name))
        is_internal = bool(INTERNAL_UNIT.search(unit.name))
        in_block = False
        allow_next = False
        for lineno, raw in enumerate(unit.read_text().splitlines(), 1):
            allowed_here = allow_next or ALLOW.search(raw) is not None
            allow_next = ALLOW_NEXT in raw
            code, in_block = strip_comments(raw, in_block)
            if not code.strip():
                continue

            def hit(rule: str, message: str) -> None:
                where = f"{unit.relative_to(root)}:{lineno}"
                if allowed_here:
                    reason = ALLOW.search(raw)
                    exemptions.append(
                        f"[{rule} ALLOWED] {where}: "
                        f"{reason.group(1) if reason else 'see previous line'}")
                else:
                    violations.append(f"[{rule}] {where}: {raw.strip()}")

            if EXPORT_IMPORT_DETAIL.search(code) and not is_aggregate:
                hit("RULE-1 detail re-export",
                    "detail shard re-exported outside a test/bench aggregate")
            if EXPORT_IMPORT_INTERNAL.search(code):
                hit("RULE-2 internal re-export",
                    "internal std-manifest must be imported PLAIN")
            if EXPORT_USING.search(code) and not (is_internal or is_aggregate):
                hit("RULE-3 export-using", "a retired re-homing mechanism")
            if THREAD_LOCAL.search(code):
                hit("RULE-4 thread_local-in-module",
                    "BMI thread_local (§2)")
            if C_MACRO.search(code):
                hit("RULE-5 C-macro-in-module",
                    "POSIX/C macro token in a module unit (§2)")

    # ── RULE-6: install seal — detail units in a file set need the PRIVATE set
    for cmake in sorted(root.rglob("CMakeLists.txt")):
        if skip(cmake):
            continue
        text = cmake.read_text()
        if DETAIL_UNIT_REF.search(text) and "FILE_SET cxx_modules" in text \
                and "cxx_modules_detail" not in text:
            violations.append(
                f"[RULE-6 missing install seal] {cmake.relative_to(root)}: "
                f"detail module unit(s) in a file set but no PRIVATE "
                f"cxx_modules_detail set (§2)")

    unit_count = len(module_units)
    if exemptions:
        print(f"ℹ {len(exemptions)} annotated exemption(s):")
        for entry in exemptions:
            print(f"  • {entry}")
        print()
    if violations:
        print(f"❌ module conformance FAILED — {len(violations)} violation(s) "
              f"across {unit_count} module units:\n")
        for entry in violations:
            print(f"  • {entry}")
        print("\nRules: technical_docs/adr/0015-cxx-named-modules-policy.md "
              "(this script's docstring is the authoritative rule→rationale map).")
        return 1

    print(f"✅ module conformance OK — {unit_count} module units clean "
          f"(detail/internal seals, no export-using, no BMI thread_local, "
          f"no C-macro tokens, install seals present).")
    return 0


def selftest() -> int:
    """Prove the gate has teeth: every rule must fire on a synthetic violation,
    comment-only tokens must NOT fire, and the MODULES-ALLOW opt-out must be
    honored. Guards against the lint silently rotting into a pass-everything
    no-op (the wallclock_lint --selftest pattern)."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="module_lint_selftest.") as tmp:
        root = pathlib.Path(tmp)
        pkg = root / "pkg"
        pkg.mkdir()
        (pkg / "bad.cppm").write_text(
            "export module pkg;\n"
            "export import pkg.detail.engine;\n"
            "export import pkg.internal;\n"
            "export using pkg::detail::Thing;\n"
            "thread_local int counter{0};\n"
            "int fd = shm_open(\"/x\", O_CREAT, 0);\n"
            "// errno in a comment must NOT fire\n")
        (pkg / "allowed.cppm").write_text(
            "export module pkg2;\n"
            "thread_local int justified{0}; // MODULES-ALLOW(proven on both toolchains)\n")
        (pkg / "CMakeLists.txt").write_text(
            "target_sources(x PUBLIC FILE_SET cxx_modules TYPE CXX_MODULES "
            "FILES pkg.detail.engine.cppm)\n")
        # aggregates may re-export detail — must stay clean
        (pkg / "pkg.test.cppm").write_text(
            "export module pkg.test;\nexport import pkg.detail.engine;\n")

        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = lint(root)
        report = buffer.getvalue()

    expected = ["RULE-1", "RULE-2", "RULE-3", "RULE-4", "RULE-5", "RULE-6"]
    failures = [rule for rule in expected if f"[{rule}" not in report]
    if status != 1:
        failures.append(f"exit status {status} != 1")
    if "ALLOWED" not in report:
        failures.append("MODULES-ALLOW exemption not honored")
    if report.count("RULE-4") != 2:  # one violation + one exemption, never the comment
        failures.append("thread_local comment/exemption handling drifted")
    if "pkg.test.cppm" in report:
        failures.append("aggregate detail re-export wrongly flagged")
    if failures:
        print("❌ module_conformance_lint SELFTEST FAILED:")
        for failure in failures:
            print(f"  • {failure}")
        return 1
    print("✅ module_conformance_lint selftest OK — all six rules fire, comments "
          "and aggregates stay clean, MODULES-ALLOW honored.")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    return lint(root)


if __name__ == "__main__":
    raise SystemExit(main())
