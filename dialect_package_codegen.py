#!/usr/bin/env python3
"""dialect_package_codegen.py — build-time codegen for an insight-canon dialect package.

Projects ONE `<dialect>.dialect.yaml` declaration into the complete C++ content of an
`insight-canon` semantic package: the manifest, every row kind, the channel and revision
vocabularies, the derived writer projection, the compile-time fences, and the code-tier
hooks REFERENCED BY NAME. The output is an `.inc` textually included by a thin
hand-written module interface unit, which carries the module name, the imports and the
code-tier declarations and no ruleset content at all.

WHY A SEPARATE ENTRY POINT, and not a mode of `intent_library_codegen.py` (DN-17.D19):
  * the grammars are disjoint and must stay CLOSED — one root is `intent:`, the other
    `dialect:`, and a union key set would let an intent key land in a dialect file, which
    dissolves the closed-grammar guarantee that is the whole safety story;
  * the moat rules are opposite — the Intent library's declarations stay PRIVATE and its
    fixtures are synthetic by construction, while dialect declarations are PUBLIC and live
    in insight-canon;
  * THE SORT RULES ARE OPPOSITE, and this is the one that would silently corrupt a
    ruleset. The library codegen iterates `sorted(entries)` — correct there, because its
    entries are a set discovered from the filesystem and sorting is what makes discovery
    order-independent. Here, DECLARED ORDER IS CONTENT: level-lift matching is
    first-match-in-declared-order, and `serialize_manifest` walks every row span in
    declared order, so a reordering silently changes both what the dialect recognizes and
    the composed `semantic_identity`. This tool never sorts a row.

WHAT THIS TOOL CANNOT DO, BY CONSTRUCTION (DN-17.D20). Rows are hashable DATA, never
callables: a declaration only COMBINES the closed core enums, so there is no regex, no
computed prefix, no conditional row, no predicate and no arithmetic here. A dialect
needing a new parse or emit SHAPE is a core grammar-version bump — a new enumerator in
`canon.spi.cppm` — never an algorithm tier in this file. The same rule governs the code
tier: hooks are NAMED, never generated, and a third hook kind is a core SPI change.

THE WRITER PROJECTION IS DERIVED, NEVER DECLARED (DN-17.D15). There is no `emits:` key,
and adding one is a schema error rather than an option: two authorable projections is
exactly the silent divergence the concept exists to forbid. Each declared marker yields
exactly one emit row, in declared order, with `emit = dual(extract)` — and the generator
emits the CALL to core's `dual()`, never a `PayloadEmit` enumerator, so a fifth extractor
added to core cannot be silently mis-derived by a stale table in this file.

DETERMINISM (DN-17.D19). Strict YAML subset -> canonical content hash -> byte-stable
emission, strings end-to-end with no typed conversion, LF-only output, write-if-changed.
The emitted text is Python output and cannot vary with the C++ compiler; the axes that
CAN move it are the interpreter version, the hash seed, the locale, filesystem
enumeration order and line endings, and `--selftest` asserts on those rather than on the
compiler.

ENCODING (DN-17.D24). Everything that enters the content hash is ASCII and is rejected
otherwise, naming the offending byte. `why:` prose does not enter the hash and is UTF-8,
byte-preserved with no normalisation — the warning glyphs an argument uses to make a
reader stop are load-bearing typography. Four sequences are refused inside a `why:`
scalar, each for a named hazard: a trailing backslash (C++ splices lines in phase 2,
BEFORE comment removal, so a `//` line ending in `\\` would swallow the row beneath it),
U+2028/U+2029, U+FEFF, and the bidirectional overrides (a `why:` exists to be read, the
format is public, and a bidi override makes text render in an order different from its
bytes).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from codegen_common import (
    CODEGEN_COMMON_VERSION,
    DeclarationError,
    canonical_hash,
    cpp_escape,
    expect_keys,
    fail,
    parse_subset_yaml,
    read_text,
    reject_non_ascii_field,
    required_scalar,
)

TOOL_VERSION = "2"  # 2: the author-voice marker (`// > `), DN-17.D35
SCHEMA_VERSION = "1"

DIALECT_FILE_SUFFIX = ".dialect.yaml"


# ── the closed core vocabularies, transcribed as TEXT ────────────────────────
#
# Transcription, not typing: a declaration value is carried to the emitted enumerator as a
# string, so no locale- or stdlib-dependent conversion exists anywhere in the path
# (determinism MUST 3). Growing one of these sets is a core grammar-version bump — a new
# enumerator in `canon.spi.cppm` / `canon.api.cppm` — and this file follows it, never
# leads it.

_STRUCTURAL_ROLES = ("None", "GroupBegin", "GroupEnd", "Terminator")
_MARKER_KINDS = ("None", "Job", "Step")
_CHILD_ORDERS = ("Ordered", "Unordered")
_PAYLOAD_EXTRACTS = ("None", "RemainderAfterPrefix", "RemainderToClosingParen",
                     "NumericFieldThenRemainder")
_LOG_LEVELS = ("Trace", "Debug", "Info", "Warn", "Error", "Fatal")
_RUN_OUTCOMES = ("Unknown", "Success", "Failure", "Unstable", "Aborted")

# The gate spelling is `self` / `any`, NEVER a literal dialect name. `all_dialect_gates_owned`
# admits exactly two values per gated row — `kAnyDialect` or the manifest's own name — so a
# free-string gate would re-admit the third, illegal case and lean on a consteval fence to
# catch it. This spelling makes that case UNREPRESENTABLE.
_DIALECT_GATE_SELF = "self"
_DIALECT_GATE_ANY = "any"
_DIALECT_GATES = (_DIALECT_GATE_SELF, _DIALECT_GATE_ANY)

_CHANNEL_GATE_ANY = "any"


# ── the section roster (DN-17.D26) ───────────────────────────────────────────
#
# A section name has THREE independent statuses and they are not one axis: whether it is
# LEGAL AT ALL (a fact about `SemanticPackageManifest`'s row surfaces, stable as dialects
# come and go), whether it is authorable NON-EMPTY in schema v1 (does the schema specify a
# row shape this tool can project?), and whether it is authorable EMPTY — the
# declared-absence seat, which lives at the intersection.

_ROW_SECTIONS = ("roles", "markers", "level_lifts", "outcome_tokens",
                 "outcome_markers", "locations", "value_classes")
_VOCABULARY_SECTIONS = ("channels", "revisions")
_SECTION_NAMES = _ROW_SECTIONS + _VOCABULARY_SECTIONS

# Legal, but EMPTY-only in schema v1: the declared-absence seat and nothing more. The row
# shape arrives with the dialect that needs it, which keeps "a generator capability no
# declaration exercises is dormant plumbing" intact.
_EMPTY_ONLY_SECTIONS = {
    "outcome_markers": "its row shape arrives with the Jenkins dialect",
    "locations": "its row shape arrives with the test_frameworks package",
    "value_classes": ("its row shape is gated on a determinism decision — `scale` is an "
                      "int64 and would be this schema's first numeric field (DN-17.D19)"),
}

# Three keys earn their OWN refusal rather than the generic unknown-key one. `emits:` above
# all: the manifest HAS that member, so it is the name an implementer will type, and
# "unknown key" would be a far worse answer than the reason. A closed grammar's value is
# the quality of its refusals.
_DIALECT_REJECTIONS = {
    "emits": ("derived from `markers:`, never declared; there is no authorable writer "
              "projection. Each marker yields exactly one emit row with "
              "`emit = dual(extract)` (DN-17.D15) — two authorable projections is the "
              "silent divergence the concept exists to forbid."),
    "recoverability": ("RESERVED in schema v1, and rejected rather than ignored "
                       "(DN-17.D18). The block is specified and its fence is expressible, "
                       "but a grammar that ACCEPTS a claim it cannot project lets someone "
                       "author a claim that silently does nothing. It arrives with the "
                       "first real claim, as one additive grammar bump."),
}

_ROOT_KEYS = ("name", "version", "why", "code_tier") + _SECTION_NAMES

# The code tier is a mapping of CLOSED hook kinds — not a section: it carries no list and
# has no empty-section seat. One key per nullable manifest member; an unknown key is a
# generation error, because a third hook kind is a core SPI change (a new nullable member
# with its own signature type), never a tool feature.
_HOOK_KINDS = {
    "echoed_source": "insight::semantic::ProvenanceHook",
    "strategy": "insight::semantic::StrategyFactory",
}
_HOOK_MEMBER = {"echoed_source": "echoed_source", "strategy": "strategy"}


# ── the `why:` fences (DN-17.D24 / DN-17.D27) ────────────────────────────────

_WHY_FORBIDDEN = {
    " ": ("U+2028 LINE SEPARATOR — some tooling treats it as a line terminator, "
               "which would end the generated comment early and expose the row beneath"),
    " ": ("U+2029 PARAGRAPH SEPARATOR — some tooling treats it as a line terminator, "
               "which would end the generated comment early and expose the row beneath"),
    "﻿": "U+FEFF — a byte-order mark has no place inside a scalar",
    "‪": "U+202A — a bidirectional override makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "‫": "U+202B — a bidirectional override makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "‬": "U+202C — a bidirectional override makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "‭": "U+202D — a bidirectional override makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "‮": "U+202E — a bidirectional override makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "⁦": "U+2066 — a bidirectional isolate makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "⁧": "U+2067 — a bidirectional isolate makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "⁨": "U+2068 — a bidirectional isolate makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
    "⁩": "U+2069 — a bidirectional isolate makes text render in an order different "
              "from its bytes (the Trojan-Source class); a `why:` exists to be read",
}


def _validate_why(node: dict, context: str, source: str) -> list[str]:
    """Validate and return the `why:` block of any mapping the schema defines.

    `why:` is an optional key on EVERY mapping — rows, sections, vocabulary entries, the
    code tier, each hook, and the document root. That is one rule rather than four seats,
    and it is what makes an argument attachable at its point of use.
    """
    raw = node.get("why")
    if raw is None:
        return []
    if not isinstance(raw, list):
        fail(source, None,
             f"{context}: `why:` is a SEQUENCE of single-line scalars, one per line of "
             "argument — a bare scalar is not the shape (it is emitted verbatim as "
             "comment lines above what it annotates)")
    if not any(isinstance(line, str) and line.strip() for line in raw):
        fail(source, None,
             f"{context}: `why:` carries no argument — an empty one is noise, and the "
             "key is optional precisely so that nothing has to be written where there is "
             "nothing to say")
    lines: list[str] = []
    for position, line in enumerate(raw):
        where = f"{context}: why[{position}]"
        if not isinstance(line, str):
            fail(source, None, f"{where}: must be a scalar")
        if not line.strip():
            # An interior empty scalar is a PARAGRAPH BREAK — it emits a bare `//`, which
            # is what makes a multi-paragraph argument readable at the row it annotates.
            # It is refused at either end, and doubled, because those emit a stray comment
            # line and nothing else: the emitted shape has to be a function of the
            # argument, not of the author's spacing habits.
            if position in (0, len(raw) - 1):
                fail(source, None,
                     f"{where}: a `why:` may not open or close on an empty line — an "
                     "empty scalar is a paragraph break, and a break at the edge breaks "
                     "nothing")
            if not raw[position - 1].strip():
                fail(source, None,
                     f"{where}: two consecutive empty lines — one paragraph break is a "
                     "break; two is whitespace with an opinion")
            lines.append("")
            continue
        for char, reason in _WHY_FORBIDDEN.items():
            if char in line:
                fail(source, None, f"{where}: {reason}")
        if line.endswith("\\"):
            fail(source, None,
                 f"{where}: a trailing backslash. C++ splices lines in translation phase 2, "
                 "BEFORE comments are removed in phase 3, so a `//` comment ending in a "
                 "backslash swallows the line beneath it — and in a generated file the line "
                 "beneath it is a ROW.")
        if '\\"' in line:
            fail(source, None,
                 f'{where}: a double quote needs no escape here — `\\"` is a leftover from '
                 "when this text lived in a C++ string literal; write the quote. A `why:` "
                 "carries the ARGUMENT as it should READ, never its escaping accidents; "
                 "the quoting layer owns whatever escaping the scalar itself needs.")
        lines.append(line)
    return lines


# ── validation ───────────────────────────────────────────────────────────────


def _ascii_field(value: str, context: str, source: str) -> str:
    reject_non_ascii_field(value, source, context)
    return value


def _enum_value(node: dict, key: str, allowed: tuple[str, ...], context: str,
                source: str) -> str:
    value = required_scalar(node, key, context, source)
    _ascii_field(value, f"{context}: `{key}:`", source)
    if value not in allowed:
        fail(source, None,
             f"{context}: `{key}: {value}` is outside the closed core vocabulary "
             f"{{{', '.join(allowed)}}} — growing it is a canon grammar-version bump, "
             "never a declaration or a tool feature")
    return value


def _dialect_gate(node: dict, context: str, source: str) -> str:
    value = required_scalar(node, "dialect_gate", context, source)
    if value not in _DIALECT_GATES:
        fail(source, None,
             f"{context}: `dialect_gate: {value}` — the gate is `self` (this package's own "
             f"name) or `any` (fires whatever the caller declared), never a literal dialect "
             "name. A row gating to another package's name would reach across a boundary "
             "this package does not own; spelling the gate this way makes that case "
             "unrepresentable rather than merely asserted (DN-17.D13).")
    return value


def _channel_gate(node: dict, channels: list[str], context: str, source: str) -> str:
    value = node.get("channel_gate")
    if value is None:
        fail(source, None, f"{context}: missing required key `channel_gate:`")
    if not isinstance(value, str):
        fail(source, None, f"{context}: `channel_gate:` must be a scalar")
    if value == _CHANNEL_GATE_ANY:
        return value
    if value not in channels:
        declared = ", ".join(channels) if channels else "(this dialect declares none)"
        fail(source, None,
             f"{context}: `channel_gate: {value}` names no channel this document declares "
             f"— the declared vocabulary is: {declared}. This is the generation-time dual "
             "of core's `all_channel_gates_declared`, and an unknown channel is a MISTAKE "
             "where an absent one is a choice: they must not share a code path.")
    return value


def _prefix(node: dict, key: str, context: str, source: str) -> str:
    value = required_scalar(node, key, context, source)
    _ascii_field(value, f"{context}: `{key}:`", source)
    if not value:
        fail(source, None, f"{context}: `{key}:` must be non-empty")
    return value


def _validate_role_row(row: dict, context: str, source: str) -> dict:
    expect_keys(row, ("prefix", "role", "dialect_gate", "why"), context, source,
                _DIALECT_REJECTIONS)
    return {
        "prefix": _prefix(row, "prefix", context, source),
        "role": _enum_value(row, "role", _STRUCTURAL_ROLES, context, source),
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


def _validate_marker_row(row: dict, channels: list[str], context: str, source: str) -> dict:
    expect_keys(row, ("prefix", "kind", "child_order", "dialect_gate", "extract",
                      "channel_gate", "why"), context, source, _DIALECT_REJECTIONS)
    return {
        "prefix": _prefix(row, "prefix", context, source),
        "kind": _enum_value(row, "kind", _MARKER_KINDS, context, source),
        "child_order": _enum_value(row, "child_order", _CHILD_ORDERS, context, source),
        "dialect_gate": _dialect_gate(row, context, source),
        "extract": _enum_value(row, "extract", _PAYLOAD_EXTRACTS, context, source),
        "channel_gate": _channel_gate(row, channels, context, source),
        "why": _validate_why(row, context, source),
    }


def _validate_level_lift_row(row: dict, context: str, source: str) -> dict:
    expect_keys(row, ("prefix", "level", "dialect_gate", "why"), context, source,
                _DIALECT_REJECTIONS)
    return {
        "prefix": _prefix(row, "prefix", context, source),
        "level": _enum_value(row, "level", _LOG_LEVELS, context, source),
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


def _validate_outcome_token_row(row: dict, context: str, source: str) -> dict:
    expect_keys(row, ("token", "outcome", "dialect_gate", "why"), context, source,
                _DIALECT_REJECTIONS)
    return {
        "token": _prefix(row, "token", context, source),
        "outcome": _enum_value(row, "outcome", _RUN_OUTCOMES, context, source),
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


_ROW_VALIDATORS = {
    "roles": _validate_role_row,
    "level_lifts": _validate_level_lift_row,
    "outcome_tokens": _validate_outcome_token_row,
}


def _validate_section(node, name: str, source: str, channels: list[str] | None = None) -> dict:
    context = f"section `{name}:`"
    if not isinstance(node, dict):
        fail(source, None,
             f"{context}: a section is a MAPPING that carries its list — "
             f"`{name}: {{why: [...], rows: [...]}}` — so that an argument has a seat on "
             "the section itself, not only on its rows")
    payload_key = "names" if name in _VOCABULARY_SECTIONS else "rows"
    expect_keys(node, ("why", payload_key), context, source, _DIALECT_REJECTIONS)
    why = _validate_why(node, context, source)
    items = node.get(payload_key)
    if items is None:
        fail(source, None, f"{context}: missing required key `{payload_key}:`")
    if not isinstance(items, list):
        fail(source, None, f"{context}: `{payload_key}:` must be a sequence")

    if not items:
        # A section declared with an EMPTY list and a mandatory `why:` IS the declared
        # absence — "we looked, here is the measurement, there is nothing to declare".
        # Without the argument it is noise, and the seat would become a place to enumerate
        # nothing; writing the argument is the work, which is what makes it self-limiting.
        if not why:
            fail(source, None,
                 f"{context}: an empty section carrying no `why:` is noise — omit the "
                 "section. An empty section WITH an argument is a declared ABSENCE, which "
                 "is a claim: it is how an omission and an exclusion stop looking alike.")
        return {"why": why, payload_key: []}

    if name in _EMPTY_ONLY_SECTIONS:
        fail(source, None,
             f"{context}: `{name}` may be declared EMPTY with an argument in schema v1 — "
             f"{_EMPTY_ONLY_SECTIONS[name]}. Schema v1 specifies no row shape for it, so "
             "there is nothing this tool could project from the rows above.")

    if name in _VOCABULARY_SECTIONS:
        return {"why": why, "names": _validate_vocabulary(items, name, source)}

    if name == "markers":
        rows = [_validate_marker_row(_as_mapping(item, name, position, source),
                                     channels or [],
                                     f"section `{name}:` rows[{position}]", source)
                for position, item in enumerate(items)]
    else:
        validator = _ROW_VALIDATORS[name]
        rows = [validator(_as_mapping(item, name, position, source),
                          f"section `{name}:` rows[{position}]", source)
                for position, item in enumerate(items)]
    return {"why": why, "rows": rows}


def _as_mapping(item, name: str, position: int, source: str) -> dict:
    if not isinstance(item, dict):
        fail(source, None,
             f"section `{name}:` rows[{position}]: a row is a mapping of its declared "
             "fields — every field is required, because a row with a defaulted gate is a "
             "row nobody decided")
    return item


def _validate_vocabulary(items: list, name: str, source: str) -> list[dict]:
    """A vocabulary entry is a bare scalar, or `{name:, why?:}` when it carries an argument.

    The one polymorphism the schema admits, and its bound is stated rather than discovered:
    an entry carrying NO `why:` may be written as a bare scalar. The two forms are
    discriminable by type with no ambiguity, and the bare form is the dominant case —
    forcing `- name: v1` on every dialect forever would be pure tax. Row entries stay
    mappings always; sections stay mappings always.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for position, item in enumerate(items):
        context = f"section `{name}:` names[{position}]"
        if isinstance(item, str):
            entry_name, why = item, []
        elif isinstance(item, dict):
            expect_keys(item, ("name", "why"), context, source, _DIALECT_REJECTIONS)
            entry_name = required_scalar(item, "name", context, source)
            why = _validate_why(item, context, source)
            if not why:
                fail(source, None,
                     f"{context}: the mapping form exists to carry a `why:` — an entry "
                     "with no argument is written as a bare scalar")
        else:
            fail(source, None, f"{context}: must be a scalar or a `{{name:, why:}}` mapping")
        _ascii_field(entry_name, context, source)
        if not entry_name:
            fail(source, None,
                 f"{context}: an empty name IS the any-sentinel on this axis, so it may "
                 "not also name a concrete member")
        if entry_name in seen:
            fail(source, None, f"{context}: duplicate name {entry_name!r}")
        seen.add(entry_name)
        entries.append({"name": entry_name, "why": why})
    return entries


def _validate_code_tier(node, package_dir: Path, source: str) -> dict:
    context = "`code_tier:`"
    if not isinstance(node, dict):
        fail(source, None, f"{context}: must be a mapping of hook kinds")
    expect_keys(node, ("why",) + tuple(_HOOK_KINDS), context, source, _DIALECT_REJECTIONS)
    tier: dict[str, object] = {"why": _validate_why(node, context, source)}
    for kind in _HOOK_KINDS:
        entry = node.get(kind)
        if entry is None:
            continue
        entry_context = f"{context} `{kind}:`"
        if not isinstance(entry, dict):
            fail(source, None, f"{entry_context}: must be `{{symbol:, unit:, why?:}}`")
        expect_keys(entry, ("symbol", "unit", "why"), entry_context, source,
                    _DIALECT_REJECTIONS)
        symbol = required_scalar(entry, "symbol", entry_context, source)
        _ascii_field(symbol, entry_context, source)
        if not symbol.replace("_", "a").isalnum() or not symbol[0].isalpha() \
                or symbol != symbol.lower():
            fail(source, None,
                 f"{entry_context}: symbol {symbol!r} outside [a-z_][a-z0-9_]* — the hook "
                 "is NAMED here and DEFINED in C++; this tool does not parse C++ and must "
                 "not, because a C++ front end inside a generator is an algorithm tier")
        unit = required_scalar(entry, "unit", entry_context, source)
        _ascii_field(unit, entry_context, source)
        _check_unit_path(unit, package_dir, entry_context, source)
        tier[kind] = {"symbol": symbol, "unit": unit,
                      "why": _validate_why(entry, entry_context, source)}
    return tier


def _check_unit_path(unit: str, package_dir: Path, context: str, source: str) -> None:
    """The earliest of the three declared failure sites for a hook that does not exist.

    Earliest first: an absent `unit:` file is a GENERATION error here; a symbol the wrapper
    does not declare is a COMPILE error at the generated `&<symbol>`; a symbol declared but
    never defined, or defined at a different signature, is a LINK error naming the symbol.
    The third is the declared residual, not a hole: this tool does not parse C++.

    The path is normalised relative to the package directory and may be neither absolute
    nor contain `..`, so generation stays host-independent — and its RESULT decides success,
    never output bytes.
    """
    candidate = Path(unit)
    if candidate.is_absolute() or ".." in candidate.parts:
        fail(source, None,
             f"{context}: `unit: {unit}` — the path is relative to the package directory, "
             "with no `..` and no absolute form: generation must stay host-independent")
    if not (package_dir / candidate).is_file():
        fail(source, None,
             f"{context}: `unit: {unit}` names no file under {package_dir} — the code tier "
             "is REFERENCED by name, so the reference must resolve at generation time")


def validate_declaration(document: dict, stem: str, package_dir: Path, source: str) -> dict:
    """Validate one dialect declaration; return the annotated declaration.

    The returned structure carries `why:` for emission; `hashed_content()` strips it, and
    that stripped view is exactly what the content hash covers.
    """
    expect_keys(document, ("dialect",), "document root", source, _DIALECT_REJECTIONS)
    if "dialect" not in document:
        fail(source, None, "document root: missing required key `dialect:`")
    dialect = document["dialect"]
    if not isinstance(dialect, dict):
        fail(source, None, "`dialect:` must be a mapping")
    expect_keys(dialect, _ROOT_KEYS, "dialect", source, _DIALECT_REJECTIONS)

    name = required_scalar(dialect, "name", "dialect", source)
    _ascii_field(name, "dialect: `name:`", source)
    if not name or not name.replace("_", "a").isalnum() or not name[0].isalpha() \
            or name != name.lower():
        fail(source, None,
             f"dialect name {name!r}: must match [a-z][a-z0-9_]* — it is the manifest "
             "name, the value every `self`-gated row carries, and what a caller declares")
    if name != stem:
        fail(source, None,
             f"dialect name {name!r} does not match its file name (expected "
             f"`{stem}{DIALECT_FILE_SUFFIX}` to declare `name: {stem}`) — one dialect, "
             "one file")

    version = required_scalar(dialect, "version", "dialect", source)
    _ascii_field(version, "dialect: `version:`", source)
    if not version:
        fail(source, None, "dialect: `version:` must be non-empty")

    declaration: dict[str, object] = {
        "name": name,
        "version": version,
        "why": _validate_why(dialect, "dialect", source),
    }

    # Channels first: a marker's `channel_gate` is validated against this document's own
    # declared vocabulary, so the vocabulary has to exist before any row is read.
    for section in _VOCABULARY_SECTIONS:
        if section in dialect:
            declaration[section] = _validate_section(dialect[section], section, source)
    channels = [entry["name"] for entry in
                declaration.get("channels", {}).get("names", [])]

    if "revisions" not in declaration:
        fail(source, None,
             "dialect: missing required section `revisions:` — the VENDOR generation these "
             "rows recognize. Core requires a non-empty vocabulary with unique, non-empty "
             "names (`all_revisions_named`): a package that recognizes nothing in "
             "particular is not a state the grammar admits.")
    revisions = declaration["revisions"]["names"]
    if len(revisions) != 1:
        fail(source, None,
             f"section `revisions:`: schema v1 admits exactly ONE revision, got "
             f"{len(revisions)}. The bound is a SCHEMA bound, not a core one (DN-17.D14): "
             "core carries the general span so that the day a vendor ships a second syntax "
             "generation is a data change rather than a redesign, and the v2 subject "
             "re-opens this by bumping the declaration schema version.")

    for section in _ROW_SECTIONS:
        if section in dialect:
            declaration[section] = _validate_section(dialect[section], section, source,
                                                     channels)

    if "code_tier" in dialect:
        declaration["code_tier"] = _validate_code_tier(dialect["code_tier"], package_dir,
                                                       source)

    _check_row_uniqueness(declaration, source)
    return declaration


def _check_row_uniqueness(declaration: dict, source: str) -> None:
    """Two rows with the same key inside ONE package are a typo, never a decision.

    Composition already refuses a duplicate key across the composed SET; catching it here
    names the declaration and the row position instead of the composed table.
    """
    keys = {
        "roles": lambda row: (row["prefix"], row["dialect_gate"]),
        "markers": lambda row: (row["prefix"], row["dialect_gate"], row["channel_gate"]),
        "level_lifts": lambda row: (row["prefix"], row["dialect_gate"]),
        "outcome_tokens": lambda row: (row["token"], row["dialect_gate"]),
    }
    for section, key_of in keys.items():
        seen: dict[tuple, int] = {}
        for position, row in enumerate(declaration.get(section, {}).get("rows", [])):
            key = key_of(row)
            if key in seen:
                fail(source, None,
                     f"section `{section}:` rows[{position}]: duplicates rows[{seen[key]}] "
                     f"on {key} — within one package a duplicate row is a typo, and the "
                     "second one could never fire")
            seen[key] = position


# ── the content hash (DN-17.D19 MUST 4 / DN-17.D20) ──────────────────────────


def hashed_content(declaration: dict) -> dict:
    """The declaration's SEMANTIC content: every `why:` stripped, the NAME stripped.

    `why:` is prose, not ruleset content — editing an argument must not revoke a
    ratification, which is the same carve-out the hash already applies to formatting. The
    name stays out because resolution keys on it before any hash is compared, so a rename
    surfaces as a loud stale-record fault rather than as a quiet mismatch.
    """
    def strip(node):
        if isinstance(node, dict):
            return {key: strip(value) for key, value in node.items() if key != "why"}
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    return {key: strip(value) for key, value in declaration.items()
            if key not in ("why", "name")}


def declaration_hash(declaration: dict) -> str:
    return canonical_hash(hashed_content(declaration))


# ── C++ emission ─────────────────────────────────────────────────────────────


def _channel_symbol(name: str) -> str:
    return "kChannel" + "".join(part.capitalize() for part in name.split("_"))


def _gate_expression(gate: str) -> str:
    return "kDialect" if gate == _DIALECT_GATE_SELF else "kAnyDialect"


def _channel_expression(gate: str) -> str:
    return "kAnyChannel" if gate == _CHANNEL_GATE_ANY else _channel_symbol(gate)


def _quoted(value: str) -> str:
    return f'"{cpp_escape(value)}"'


# The AUTHOR-VOICE marker (DN-17.D35). Every line of `why:` prose reaches the emitted file
# behind `// > `; every other comment line in the emitted file is this tool's own words. The
# rule it enforces: a TOOL's explanation and an AUTHOR's argument answer different questions
# and must be distinguishable in the emitted file.
#
# It is not a style preference, and the near-miss that earned it is worth stating. The tool's
# reason for an empty `locations:` is "its row shape arrives with the test_frameworks package"
# — a claim about what the SCHEMA can project. The declaration's reason is "that is the
# test_frameworks package" — a claim about what THIS DIALECT has to say and who owns the
# vocabulary instead. Same package name, nearly the same words, opposite altitudes. A reader
# scanning for "is this absence explained?" finds the noun, finds a sentence, and stops — so
# sharing the identifying token makes adjacency read as coverage. The two land at ONE SITE
# once a section is declared empty, which is exactly where the confusion would be built in.
#
# Mechanical by construction, which is the point: `^\s*// > ` selects the declaration's voice
# and NOTHING else, so a reviewer scoring whether an argument was carried can do it by
# selection rather than by judgement, without knowing which line this tool wrote. `--selftest`
# holds both halves of that equivalence.
_WHY_MARKER = "// > "


def _every_why_line(declaration: dict) -> list[str]:
    """Every `why:` line in a declaration, at every depth — the author's whole voice.

    Walks the parsed declaration rather than a hand-kept list of seats, so a seat added to the
    schema is covered the day it exists: the selftest cases below assert a SET EQUIVALENCE, and
    an enumeration that could go short would silently weaken one half of it.
    """
    lines: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "why" and isinstance(value, list):
                    lines.extend(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(declaration)
    return lines


def _emit_why(out: list[str], why: list[str], indent: str = "") -> None:
    for line in why:
        out.append(f"{indent}{_WHY_MARKER}{line}" if line
                   else f"{indent}{_WHY_MARKER.rstrip()}")


def _emit_row(out: list[str], fields: list[tuple[str, str]], why: list[str]) -> None:
    _emit_why(out, why, "    ")
    opening = f"    {{.{fields[0][0]} = {fields[0][1]},"
    out.append(opening)
    pad = "     "
    for name, value in fields[1:-1]:
        out.append(f"{pad}.{name} = {value},")
    last_name, last_value = fields[-1]
    out.append(f"{pad}.{last_name} = {last_value}}},")


def _emit_array(out: list[str], cpp_type: str, symbol: str, count: int) -> None:
    out.append(f"inline constexpr std::array<{cpp_type}, {count}> {symbol}{{{{")


def emit_inc(declaration: dict, *, module_suffix: str, source_name: str) -> str:
    name = declaration["name"]
    namespace = f"insight::semantic::{name}{module_suffix}"
    digest = declaration_hash(declaration)
    out: list[str] = []

    out.append("// GENERATED by dialect_package_codegen.py -- DO NOT EDIT.")
    out.append(f"// tool version: {TOOL_VERSION}, codegen_common: {CODEGEN_COMMON_VERSION}, "
               f"declaration schema: {SCHEMA_VERSION}")
    out.append("// (all three enter this header and none of them enters the declaration "
               "hash -- a tool")
    out.append("// upgrade must not mass-revoke, so a stale version string here is "
               "cosmetic.)")
    out.append(f"// declaration: {source_name}")
    out.append(f"// declaration hash: {digest}")
    out.append("// (sha256 over the canonical JSON of the semantic content: the name is "
               "excluded, and")
    out.append("// every `why:` is excluded -- prose is not ruleset content, so editing an "
               "argument")
    out.append("// must not move the identity of what the package recognizes.)")
    out.append(f"// module suffix: {module_suffix if module_suffix else '(none)'}")
    out.append("// (a GENERATION-TIME parameter, never declaration content: it may reach "
               "the module")
    out.append("// name and the namespace and NOTHING else -- never the manifest name, "
               "never a row.")
    out.append("// `--selftest` proves it by generating this declaration both ways and "
               "comparing.)")
    out.append("// BUILT, never committed: a pure function of (the declaration, this tool).")
    out.append("// TWO VOICES, and they are distinguishable on sight and by `grep`: every "
               "comment line")
    out.append("// beginning `// > ` is the DECLARATION's own argument, carried verbatim from "
               "a `why:`;")
    out.append("// every other comment line is this TOOL explaining what it generated. They "
               "answer")
    out.append("// different questions, so they are never merged into one block.")
    out.append("// clang-format off")
    out.append("")
    out.append(f"namespace {namespace}")
    out.append("{")
    out.append("")

    _emit_why(out, declaration["why"])
    if declaration["why"]:
        out.append("")

    _emit_code_tier_pins(out, declaration)

    out.append("// The dialect NAME every `self`-gated row below carries, and the name a "
               "caller declares.")
    out.append("// Exported so a caller can name it without spelling a literal.")
    out.append(f"export inline constexpr std::string_view kDialect{{{_quoted(name)}}};")
    out.append("")

    _emit_role_rows(out, declaration)
    _emit_channels(out, declaration)
    _emit_revisions(out, declaration)
    _emit_marker_rows(out, declaration)
    _emit_level_lift_rows(out, declaration)
    _emit_outcome_token_rows(out, declaration)
    _emit_declared_absences(out, declaration)
    _emit_manifest(out, declaration)

    out.append(f"}} // namespace {namespace}")
    out.append("// clang-format on")
    out.append("")
    return "\n".join(out)


def _emit_code_tier_pins(out: list[str], declaration: dict) -> None:
    tier = declaration.get("code_tier")
    if tier is None:
        return
    _emit_why(out, tier["why"])
    for kind, hook_type in _HOOK_KINDS.items():
        entry = tier.get(kind)
        if entry is None:
            continue
        _emit_why(out, entry["why"])
        if entry["why"]:
            out.append("//")
        out.append(f"// The `{kind}` code tier is REFERENCED, never generated: "
                   f"{entry['symbol']} is declared by this")
        out.append(f"// module's wrapper and defined in {entry['unit']}. The pin below "
                   "makes a signature drift a")
        out.append("// compile error here rather than a link error at the composing target.")
        out.append(f"static_assert(std::is_same_v<decltype(&{entry['symbol']}), "
                   f"{hook_type}>,")
        out.append(f'              "{declaration["name"]}: the `{kind}` hook '
                   f"{entry['symbol']} does not match the SPI \"")
        out.append(f'              "signature {hook_type} -- the code tier is '
                   'signature-constrained by the provider "')
        out.append('              "contract, not by convention");')
    out.append("")


def _emit_role_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("roles")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    _emit_array(out, "StructuralRoleRow", "kRoles", len(section["rows"]))
    for row in section["rows"]:
        _emit_row(out, [
            ("prefix", _quoted(row["prefix"])),
            ("role", f"insight::StructuralRole::{row['role']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row["why"])
    out.append("}};")
    out.append("")


def _emit_channels(out: list[str], declaration: dict) -> None:
    section = declaration.get("channels")
    if section is None or not section["names"]:
        return
    _emit_why(out, section["why"])
    for entry in section["names"]:
        if entry["why"]:
            out.append("//")
        _emit_why(out, entry["why"])
        out.append(f"export inline constexpr std::string_view "
                   f"{_channel_symbol(entry['name'])}{{{_quoted(entry['name'])}}};")
    symbols = ", ".join(_channel_symbol(entry["name"]) for entry in section["names"])
    out.append(f"export inline constexpr std::array<std::string_view, "
               f"{len(section['names'])}> kChannels{{{{{symbols}}}}};")
    out.append("")


def _emit_revisions(out: list[str], declaration: dict) -> None:
    section = declaration["revisions"]
    _emit_why(out, section["why"])
    for entry in section["names"]:
        _emit_why(out, entry["why"])
    values = ", ".join(_quoted(entry["name"]) for entry in section["names"])
    out.append(f"export inline constexpr std::array<std::string_view, "
               f"{len(section['names'])}> kDialectRevisions{{{{{values}}}}};")
    out.append("")


def _emit_marker_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("markers")
    if section is None or not section["rows"]:
        return
    rows = section["rows"]
    _emit_why(out, section["why"])
    _emit_array(out, "IntentMarkerRow", "kMarkers", len(rows))
    for row in rows:
        _emit_row(out, [
            ("prefix", _quoted(row["prefix"])),
            ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
            ("child_order", f"insight::tokenization::ChildOrder::{row['child_order']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
            ("extract", f"PayloadExtract::{row['extract']}"),
            ("channel_gate", _channel_expression(row["channel_gate"])),
        ], row["why"])
    out.append("}};")
    out.append("")

    # The writer projection: DERIVED, total over the declared markers, in declared order.
    # The `emit` field is the CALL to core's `dual()`, never a PayloadEmit enumerator — a
    # mapping table on this side would be the same concept minted at two sites, and a fifth
    # extractor added to core without touching this file would silently mis-derive.
    out.append("// The generation-template rows -- the WRITER dual, DERIVED from the "
               "markers above and")
    out.append("// never declared: one emit row per recognition row, same prefix, same "
               "kind, same gates,")
    out.append("// `emit = dual(extract)`. `dual` is core's, so the two projections cannot "
               "drift apart:")
    out.append("// a new extractor in canon changes what this call returns without this "
               "tool knowing it")
    out.append("// exists. `all_intents_paired`, the DialectIntent concept and the "
               "one-Medium-per-pair")
    out.append("// clause then hold BY CONSTRUCTION rather than by assertion.")
    _emit_array(out, "IntentEmitRow", "kEmitMarkers", len(rows))
    for row in rows:
        _emit_row(out, [
            ("prefix", _quoted(row["prefix"])),
            ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
            ("child_order", f"insight::tokenization::ChildOrder::{row['child_order']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
            ("emit", f"insight::semantic::dual(PayloadExtract::{row['extract']})"),
            ("channel_gate", _channel_expression(row["channel_gate"])),
        ], [])
    out.append("}};")
    out.append("")
    out.append("export struct Dialect")
    out.append("{")
    out.append("    static constexpr std::span<const IntentMarkerRow> markers{kMarkers};")
    out.append("    static constexpr std::span<const IntentEmitRow> emit_markers"
               "{kEmitMarkers};")
    out.append("};")
    name = declaration["name"]
    out.append("static_assert(")
    out.append("    insight::semantic::DialectIntent<Dialect>,")
    out.append(f'    "{name}: a recognition marker has no paired generation row (reader '
               'without a writer), or "')
    out.append('    "a reader/writer pair straddles two IntentChannels -- the projections '
               'must name the "')
    out.append('    "same Medium");')
    if declaration.get("channels", {}).get("names"):
        out.append("static_assert(insight::semantic::all_channels_named(kChannels),")
        out.append(f'              "{name}: a declared IntentChannel name is empty -- the '
                   'empty name IS kAnyChannel, "')
        out.append('              "so it may not also name a concrete channel");')
        out.append("static_assert(insight::semantic::all_channel_gates_declared(kMarkers, "
                   "kEmitMarkers, kChannels),")
        out.append(f'              "{name}: a row gates to an IntentChannel this package '
                   'never declared -- the "')
        out.append('              "declared vocabulary is kChannels");')
    out.append("")


def _emit_level_lift_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("level_lifts")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    _emit_array(out, "LevelLiftRow", "kLevelLifts", len(section["rows"]))
    for row in section["rows"]:
        _emit_row(out, [
            ("prefix", _quoted(row["prefix"])),
            ("level", f"insight::LogLevel::{row['level']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row["why"])
    out.append("}};")
    out.append("")


def _emit_outcome_token_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("outcome_tokens")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    _emit_array(out, "OutcomeTokenRow", "kOutcomeTokens", len(section["rows"]))
    for row in section["rows"]:
        _emit_row(out, [
            ("token", _quoted(row["token"])),
            ("outcome", f"insight::RunOutcome::{row['outcome']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row["why"])
    out.append("}};")
    out.append("")


def _emit_declared_absences(out: list[str], declaration: dict) -> None:
    """An empty section's argument is emitted where the manifest member is left empty.

    A declared absence has to be READABLE at the surface it is about, or it is an absence
    argued one document away — which is the failure the seat exists to prevent.
    """
    for section_name in _ROW_SECTIONS:
        section = declaration.get(section_name)
        if section is None or section.get("rows"):
            continue
        out.append(f"// DECLARED ABSENCE -- the manifest's `.{section_name}` below is empty. What "
                   "follows in the")
        out.append("// declaration's own voice is why THIS DIALECT has nothing to declare here "
                   "and who owns")
        out.append("// the vocabulary instead -- a different question from why the SCHEMA "
                   "specifies no row")
        out.append(f"// shape for `{section_name}:`, which is this tool's business and is "
                   "answered in its")
        out.append("// refusal message, not here.")
        _emit_why(out, section["why"])
        out.append("")


def _emit_manifest(out: list[str], declaration: dict) -> None:
    name = declaration["name"]
    tier = declaration.get("code_tier", {})
    members: list[tuple[str, str]] = [
        ("name", _quoted(name)),
        ("version", _quoted(declaration["version"])),
        ("roles", "kRoles" if declaration.get("roles", {}).get("rows") else "{}"),
        ("markers", "kMarkers" if declaration.get("markers", {}).get("rows") else "{}"),
        ("emits", "kEmitMarkers" if declaration.get("markers", {}).get("rows") else "{}"),
        ("level_lifts",
         "kLevelLifts" if declaration.get("level_lifts", {}).get("rows") else "{}"),
        ("locations", "{}"),
        ("value_classes", "{}"),
        ("outcome_tokens",
         "kOutcomeTokens" if declaration.get("outcome_tokens", {}).get("rows") else "{}"),
        ("outcome_markers", "{}"),
        ("channels", "kChannels" if declaration.get("channels", {}).get("names") else "{}"),
        ("dialect_revisions", "kDialectRevisions"),
    ]
    for kind in _HOOK_KINDS:
        entry = tier.get(kind)
        if entry is not None:
            members.append((_HOOK_MEMBER[kind], f"&{entry['symbol']}"))

    out.append("export inline constexpr SemanticPackageManifest kManifest{")
    for member, value in members:
        out.append(f"    .{member} = {value},")
    out.append("};")
    out.append("")
    out.append("static_assert(insight::semantic::all_dialect_gates_owned(kManifest),")
    out.append(f'              "{name}: a row\'s dialect_gate is neither kAnyDialect nor '
               'this package\'s own name");')
    out.append("static_assert(kManifest.name == kDialect,")
    out.append(f'              "{name}: kDialect and the manifest name must be the same '
               'string -- kDialect is what "')
    out.append('              "a caller declares and what every gated row carries");')
    out.append("static_assert(insight::semantic::all_revisions_named(kDialectRevisions),")
    out.append(f'              "{name}: the declared dialect-revision vocabulary must be '
               'non-empty, with unique, "')
    out.append('              "non-empty names -- the coordinate is what a reader compares '
               'generations on, "')
    out.append('              "so an unnamed or repeated one is not a declaration");')
    out.append("")


# ── generation driver ────────────────────────────────────────────────────────


def load_declaration(declaration_path: Path) -> dict:
    source = str(declaration_path)
    if not declaration_path.name.endswith(DIALECT_FILE_SUFFIX):
        raise DeclarationError(
            f"{source}: a dialect declaration is named `<dialect>{DIALECT_FILE_SUFFIX}` "
            "— one dialect, one file, and the stem IS the declared name")
    stem = declaration_path.name[: -len(DIALECT_FILE_SUFFIX)]
    text = read_text(declaration_path, ascii_only=False)
    document = parse_subset_yaml(text, source, ascii_only=False)
    return validate_declaration(document, stem, declaration_path.parent, source)


def generate(declaration_path: Path, out_path: Path, module_suffix: str) -> int:
    declaration = load_declaration(declaration_path)
    rendered = emit_inc(declaration, module_suffix=module_suffix,
                        source_name=declaration_path.name)
    payload = rendered.encode("utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.read_bytes() == payload:
        return 0  # unchanged — do not touch the file (no rebuild churn)
    with open(out_path, "wb") as handle:
        handle.write(payload)
    return 0


# ── selftest ─────────────────────────────────────────────────────────────────
#
# Every fixture here is SYNTHETIC. The real declarations live in insight-canon beside the
# packages they generate, and a copy of one beside the tool would rot into a second
# picture of the same dialect.

_SYNTHETIC = """\
dialect:
  name: synthetic
  version: "1.0.0"
  why: ["The dialect-level argument — an em dash lives here, and it is preserved."]
  revisions:
    why: ["The vendor generation these rows recognize."]
    names: [v1]
  channels:
    why: ["Two materializations, one of which is ours."]
    names:
      - plain
      - name: marked
        why: ["This name is a state ABSENCE CANNOT DECIDE."]
  code_tier:
    echoed_source:
      symbol: is_echoed_source
      unit: hook.cpp
      why: ["A byte predicate, not a grammar."]
  roles:
    why: ["The ungated reading is deliberate."]
    rows:
      - prefix: "##[group]"
        role: GroupBegin
        dialect_gate: any
      - prefix: "##[endgroup]"
        role: GroupEnd
        dialect_gate: any
  markers:
    why: ["Dialect-gated: these prefixes would misfire elsewhere."]
    rows:
      - prefix: "Job: "
        kind: Job
        child_order: Unordered
        dialect_gate: self
        extract: RemainderAfterPrefix
        channel_gate: any
        why: ["The job banner is identical in both channels."]
      - prefix: "Run "
        kind: Step
        child_order: Ordered
        dialect_gate: self
        extract: RemainderAfterPrefix
        channel_gate: plain
        why: ["In the marked channel this prefix is ordinary prose."]
  level_lifts:
    why: ["First match in DECLARED order wins, so row order is CONTENT here."]
    rows:
      - prefix: "##[error]"
        level: Error
        dialect_gate: self
      - prefix: "##[warning]"
        level: Warn
        dialect_gate: self
  outcome_tokens:
    why: ["Tokens carrying no pass/fail verdict map to Unknown -- honest, never a guess."]
    rows:
      - token: success
        outcome: Success
        dialect_gate: self
      - token: skipped
        outcome: Unknown
        dialect_gate: self
  outcome_markers:
    why: ["This dialect emits no single run-verdict console line, so there is nothing to declare."]
    rows: []
"""


def _write_fixture(directory: Path, text: str = _SYNTHETIC,
                   stem: str = "synthetic") -> Path:
    (directory / "hook.cpp").write_text("", encoding="utf-8")
    path = directory / f"{stem}{DIALECT_FILE_SUFFIX}"
    path.write_bytes(text.encode("utf-8"))
    return path


def render_fixture(module_suffix: str = "_gen", text: str = _SYNTHETIC) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_fixture(Path(tmp), text)
        declaration = load_declaration(path)
        return emit_inc(declaration, module_suffix=module_suffix,
                        source_name=path.name)


def fixture_digest() -> str:
    return hashlib.sha256(render_fixture().encode("utf-8")).hexdigest()


def _parse_fixture(text: str, stem: str = "synthetic") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return load_declaration(_write_fixture(Path(tmp), text, stem))


def _assert(condition: bool, message: str) -> None:
    assert condition, message


def _case(label: str, failures: list[str], check) -> None:
    try:
        check()
    except AssertionError as error:
        failures.append(f"{label}: {error}")
    except DeclarationError as error:
        failures.append(f"{label}: unexpected rejection: {error}")


def _expect_rejection(label: str, failures: list[str], needle: str, thunk) -> None:
    """Every malformed fixture is refused BY NAME: the assertion is on the MESSAGE.

    A nonzero exit says only that something was refused. What has to hold is that the tool
    refuses the right thing for the right stated reason — a closed grammar's value is the
    quality of its refusals, and a test that accepts any rejection cannot see a fence
    firing for the wrong one.
    """
    try:
        thunk()
    except DeclarationError as error:
        if needle not in str(error):
            failures.append(f"{label}: rejected, but message lacks {needle!r}: {error}")
        return
    failures.append(f"{label}: accepted a declaration the fence must refuse")


def _subprocess_digest(env_overrides: dict[str, str]) -> str:
    """Re-run the fixture render in a child interpreter under a changed environment."""
    env = dict(os.environ)
    env.update(env_overrides)
    here = str(Path(__file__).resolve().parent)
    code = (f"import sys; sys.path.insert(0, {here!r}); "
            "import dialect_package_codegen as tool; print(tool.fixture_digest())")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env=env, check=True)
    return result.stdout.strip()


def selftest() -> int:
    failures: list[str] = []
    base = _parse_fixture(_SYNTHETIC)
    base_hash = declaration_hash(base)
    rendered = render_fixture()

    # ── the emission contract ────────────────────────────────────────────────
    _case("emit: byte-stable across renders", failures, lambda: _assert(
        render_fixture() == rendered, "two renders of one declaration differ"))
    _case("emit: LF-only, and no trailing whitespace", failures, lambda: _assert(
        "\r" not in rendered and not any(line != line.rstrip()
                                         for line in rendered.split("\n")),
        "emitted a CR or a trailing space — output line endings are LF-only"))
    _case("emit: the row fields are ASCII (only `why:` prose may be UTF-8)", failures,
          lambda: _assert(
              all(ch.isascii() for line in rendered.split("\n")
                  if not line.lstrip().startswith("//") for ch in line),
              "a non-ASCII byte reached emitted CODE — the carve-out is prose only"))
    _case("emit: the argument survives, verbatim and un-transliterated", failures,
          lambda: _assert(
              "// > The dialect-level argument — an em dash lives here, and it is "
              "preserved." in rendered,
              "a `why:` line was lost or transliterated on the way to the comment"))

    # ── the TWO VOICES are distinguishable in the EMITTED file (DN-17.D35) ────────────────
    # The property is an EQUIVALENCE and both halves are asserted, because either one alone
    # is satisfiable by a wrong emitter. "Every author line is marked" alone passes if the
    # tool marks its own lines too; "no tool line is marked" alone passes if nothing is
    # marked at all. Together they say: `^\s*// > ` selects the declaration's voice, exactly.
    _marked = {line.lstrip()[len(_WHY_MARKER):] for line in rendered.split("\n")
               if line.lstrip().startswith(_WHY_MARKER)}
    _declared_prose = {line for line in _every_why_line(_parse_fixture(_SYNTHETIC)) if line}
    _case("emit: every declaration `why:` line reaches the file behind the author marker",
          failures, lambda: _assert(
              _declared_prose <= _marked,
              "a `why:` line was emitted unmarked -- it now reads as this tool's own words: "
              + repr(sorted(_declared_prose - _marked)[:3])))
    _case("emit: no line this tool wrote carries the author marker", failures,
          lambda: _assert(
              _marked <= _declared_prose,
              "a tool-written comment carries `// > ` -- the marker no longer selects the "
              "declaration's voice, so a reviewer scoring a carried argument would score "
              "this tool's prose as the author's: "
              + repr(sorted(_marked - _declared_prose)[:3])))

    # ── declared order is CONTENT, and this is the one that must never be `sorted` ──
    # Reversing the level lifts must move the emitted bytes AND the content hash. If this
    # arm ever goes green with the rows unmoved, the emitter has grown a sort.
    reversed_lifts = _SYNTHETIC.replace(
        '      - prefix: "##[error]"\n        level: Error\n        dialect_gate: self\n'
        '      - prefix: "##[warning]"\n        level: Warn\n        dialect_gate: self\n',
        '      - prefix: "##[warning]"\n        level: Warn\n        dialect_gate: self\n'
        '      - prefix: "##[error]"\n        level: Error\n        dialect_gate: self\n')
    # The comparison is on the ROW REGION, deliberately, and not on the whole rendered
    # file: the header carries the declaration hash, which moves on any row edit, so a
    # whole-file `!=` goes green over an emitter that sorts. That is precisely the shape
    # this arm exists to catch, so it must not be the shape the arm itself has.
    _case("order: the emitted rows follow the DECLARED order, never a sort", failures,
          lambda: _assert(
              _row_prefixes(rendered, "kLevelLifts") == ['"##[error]"', '"##[warning]"']
              and _row_prefixes(render_fixture(text=reversed_lifts), "kLevelLifts")
              == ['"##[warning]"', '"##[error]"'],
              "row order is CONTENT — first-match-in-declared-order decides which level "
              "lift fires, so a sort here silently changes what the dialect recognizes: "
              f"{_row_prefixes(render_fixture(text=reversed_lifts), 'kLevelLifts')}"))
    _case("order: reversing rows moves the content hash", failures, lambda: _assert(
        reversed_lifts != _SYNTHETIC
        and declaration_hash(_parse_fixture(reversed_lifts)) != base_hash,
        "`serialize_manifest` walks every row span in declared order, so a reordering "
        "moves the composed semantic_identity and must move this hash with it"))

    # ── the derived writer projection (DN-17.D15) ────────────────────────────
    _case("derive: one emit row per marker, in the same order, same prefixes", failures,
          lambda: _assert(
              _row_prefixes(rendered, "kEmitMarkers")
              == _row_prefixes(rendered, "kMarkers") == ['"Job: "', '"Run "'],
              "the derivation is TOTAL over the declared markers and preserves their "
              "order — a writer row that does not pair is a reader without a writer"))
    _case("derive: the emit shape is a CALL to core's dual(), never an enumerator",
          failures, lambda: _assert(
              rendered.count(".emit = insight::semantic::dual(PayloadExtract::"
                             "RemainderAfterPrefix)") == 2
              and "PayloadEmit::" not in rendered,
              "a PayloadEmit enumerator in the emitted text means this tool carries the "
              "extractor->emitter table, which is the same concept minted at two sites: "
              "a fifth extractor added to canon would then mis-derive in silence"))

    # ── the content hash (MUST 3/4) ──────────────────────────────────────────
    reformatted = _SYNTHETIC.replace("  version: \"1.0.0\"",
                                     "  # a comment\n  version:   '1.0.0'")
    _case("hash: a reformat does not move it", failures, lambda: _assert(
        declaration_hash(_parse_fixture(reformatted)) == base_hash,
        "a reformat moved the content hash — authors would avoid touching files"))
    why_edited = _SYNTHETIC.replace("A byte predicate, not a grammar.",
                                    "A byte predicate. Nothing more.")
    _case("hash: editing an argument does not move it", failures, lambda: _assert(
        declaration_hash(_parse_fixture(why_edited)) == base_hash
        and render_fixture(text=why_edited) != rendered,
        "`why:` is prose, not ruleset content: it must move the emitted comment and "
        "leave the identity of what the package recognizes alone"))
    row_edited = _SYNTHETIC.replace("        level: Warn", "        level: Info")
    _case("hash: editing a row DOES move it", failures, lambda: _assert(
        declaration_hash(_parse_fixture(row_edited)) != base_hash,
        "a row edit must move the content hash"))
    renamed = _SYNTHETIC.replace("  name: synthetic", "  name: other")
    _case("hash: the NAME stays out of it", failures, lambda: _assert(
        declaration_hash(_parse_fixture(renamed, stem="other")) == base_hash,
        "resolution keys on the name before any hash is compared, so a rename must "
        "surface as a loud stale-record fault rather than a quiet mismatch"))
    # A tool upgrade must not mass-revoke: the version is HEADER provenance, and the only
    # honest way to assert that is to move it and watch the digest hold still.
    global TOOL_VERSION  # noqa: PLW0603 — restored below; the arm IS the mutation
    original_version, TOOL_VERSION = TOOL_VERSION, f"{TOOL_VERSION}-selftest"
    try:
        bumped = render_fixture()
    finally:
        TOOL_VERSION = original_version
    _case("hash: a tool-version bump moves the header and NOT the digest", failures,
          lambda: _assert(
              f"tool version: {original_version}," in rendered
              and f"tool version: {original_version}-selftest," in bumped
              and f"// declaration hash: {base_hash}" in rendered
              and f"// declaration hash: {base_hash}" in bumped,
              "the tool version belongs in the header and never in hashed content — a "
              "tool upgrade must not mass-revoke what the declarations claim"))

    # ── the module-suffix fence (DN-17.D21) ──────────────────────────────────
    # The suffix may reach the module name and the namespace and NOTHING else. This is the
    # one place that is cheap to prove, and it is what makes "the bytes proven equivalent
    # are the bytes that ship" true: the declaration never varies between the two.
    plain = render_fixture(module_suffix="")
    suffixed = render_fixture(module_suffix="_gen")
    plain_lines, suffixed_lines = plain.split("\n"), suffixed.split("\n")
    parameter_lines = {"namespace insight::semantic::synthetic",
                       "namespace insight::semantic::synthetic_gen",
                       "} // namespace insight::semantic::synthetic",
                       "} // namespace insight::semantic::synthetic_gen",
                       "// module suffix: (none)", "// module suffix: _gen"}
    differing = [(a, b) for a, b in zip(plain_lines, suffixed_lines) if a != b]
    _case("suffix: it reaches the namespace and nothing else", failures, lambda: _assert(
        len(plain_lines) == len(suffixed_lines)
        and differing
        and all(a in parameter_lines and b in parameter_lines for a, b in differing),
        f"the module suffix leaked out of the namespace: {differing}"))
    _case("suffix: the manifest name and every row are byte-identical", failures,
          lambda: _assert(
              '.name = "synthetic",' in suffixed and "synthetic_gen" not in
              "\n".join(line for line in suffixed_lines
                        if line not in parameter_lines),
              "the suffix reached the declared level — `name:` must stay verbatim, so the "
              "swap is a build-invocation change rather than a declaration edit"))

    # ── determinism across the axes that CAN move the bytes ──────────────────
    # Not the C++ compiler: this is Python output, and no C++ toolchain is in the path
    # that produces it. What can move it is the interpreter's environment, so those are
    # the arms. PYTHONHASHSEED appears nowhere in malf today while byte-identical emission
    # is already a declared MUST, which is exactly the kind of gap that stays invisible
    # until it bites.
    try:
        seed_digests = {seed: _subprocess_digest({"PYTHONHASHSEED": seed})
                        for seed in ("0", "1", "12345", "random")}
        locale_digests = {
            "C": _subprocess_digest({"LC_ALL": "C", "LANG": "C"}),
            "C.UTF-8": _subprocess_digest({"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}),
            "utf8-mode": _subprocess_digest({"PYTHONUTF8": "1"}),
            "no-utf8-mode": _subprocess_digest({"PYTHONUTF8": "0"}),
        }
        in_process = fixture_digest()
        _case("determinism: PYTHONHASHSEED does not move the bytes", failures,
              lambda: _assert(
                  set(seed_digests.values()) == {in_process},
                  f"hash-seed randomisation moved the output: {seed_digests}"))
        _case("determinism: the locale does not move the bytes", failures, lambda: _assert(
            set(locale_digests.values()) == {in_process},
            f"the host locale moved the output: {locale_digests}"))
    except (subprocess.CalledProcessError, OSError) as error:
        failures.append(f"determinism: the environment arms could not run: {error}")

    crlf = _SYNTHETIC.replace("\n", "\r\n")
    _case("determinism: CRLF input renders the same bytes", failures, lambda: _assert(
        render_fixture(text=crlf) == rendered,
        "a CRLF checkout must produce the same bytes as an LF one"))
    permuted = _reorder_sections(_SYNTHETIC)
    _case("determinism: section order in the document does not move the bytes", failures,
          lambda: _assert(
              permuted != _SYNTHETIC and render_fixture(text=permuted) == rendered
              and declaration_hash(_parse_fixture(permuted)) == base_hash,
              "sections are emitted in the SCHEMA's order, so the document's key order "
              "is not content — unlike row order inside a section, which is"))

    # ── the refusals, each asserted on its message ───────────────────────────
    _expect_rejection("refuse: `emits:` gets its own message", failures,
                      "derived from `markers:`",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  roles:", "  emits:\n    rows: []\n  roles:")))
    _expect_rejection("refuse: `recoverability:` is RESERVED", failures, "RESERVED",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  roles:", "  recoverability:\n    rows: []\n  roles:")))
    _expect_rejection("refuse: an unknown key", failures, "unknown key",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  roles:", "  vibes: [good]\n  roles:")))
    _expect_rejection("refuse: `payload_excludes:` is not a v1 marker row key", failures,
                      "unknown key",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        channel_gate: any\n",
                          "        channel_gate: any\n        payload_excludes: [x]\n", 1)))
    _expect_rejection("refuse: an EMPTY-only section declared non-empty", failures,
                      "may be declared EMPTY with an argument",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  outcome_markers:\n    why: [\"This dialect emits no single "
                          "run-verdict console line, so there is nothing to declare.\"]\n"
                          "    rows: []\n",
                          "  outcome_markers:\n    why: [\"x\"]\n    rows:\n"
                          "      - prefix: \"Finished: \"\n")))
    _expect_rejection("refuse: an empty section with no argument", failures,
                      "an empty section carrying no `why:` is noise",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "    why: [\"This dialect emits no single run-verdict console "
                          "line, so there is nothing to declare.\"]\n    rows: []\n",
                          "    rows: []\n")))
    _expect_rejection("refuse: more than one revision in schema v1", failures,
                      "schema v1 admits exactly ONE revision",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "    names: [v1]", "    names: [v1, v2]")))
    _expect_rejection("refuse: a literal dialect name as a gate", failures,
                      "never a literal dialect name",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        dialect_gate: self\n",
                          "        dialect_gate: synthetic\n", 1)))
    _expect_rejection("refuse: a channel gate naming an undeclared channel", failures,
                      "names no channel this document declares",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        channel_gate: plain", "        channel_gate: stripped")))
    _expect_rejection("refuse: an enum value outside the closed core vocabulary", failures,
                      "outside the closed core vocabulary",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        level: Warn", "        level: Screaming")))
    _expect_rejection("refuse: a name that disagrees with the file name", failures,
                      "one dialect, one file",
                      lambda: _parse_fixture(_SYNTHETIC, stem="elsewhere"))
    _expect_rejection("refuse: an unknown code-tier hook kind", failures, "unknown key",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  code_tier:\n", "  code_tier:\n    tokenizer:\n"
                          "      symbol: x\n      unit: hook.cpp\n")))
    _expect_rejection("refuse: a code-tier unit that does not exist", failures,
                      "names no file under",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "      unit: hook.cpp", "      unit: absent.cpp")))
    _expect_rejection("refuse: a code-tier unit escaping the package directory", failures,
                      "host-independent",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "      unit: hook.cpp", "      unit: ../hook.cpp")))
    _expect_rejection("refuse: a duplicate row inside one package", failures,
                      "could never fire",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '      - prefix: "##[warning]"\n        level: Warn\n',
                          '      - prefix: "##[error]"\n        level: Warn\n')))
    _expect_rejection("refuse: a section written as a bare list", failures,
                      "a section is a MAPPING that carries its list",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  level_lifts:\n    why: [\"First match in DECLARED order wins, "
                          "so row order is CONTENT here.\"]\n    rows:\n",
                          "  level_lifts:\n")))
    _expect_rejection("refuse: `why:` written as a bare scalar", failures,
                      "`why:` is a SEQUENCE",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '    why: ["The vendor generation these rows recognize."]',
                          '    why: "The vendor generation these rows recognize."')))

    # ── the encoding fences (DN-17.D24 / DN-17.D27) ──────────────────────────
    _expect_rejection("encoding: a non-ASCII byte in a ROW field", failures,
                      "non-ASCII or control byte",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '      - prefix: "Run "', '      - prefix: "Run — "')))
    _expect_rejection("encoding: a non-ASCII PLAIN scalar is not the shape", failures,
                      "quote it if it is literal text",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '  why: ["The dialect-level argument — an em dash lives '
                          'here, and it is preserved."]',
                          "  why: [The dialect-level argument — unquoted]")))
    for label, char in (("U+2028", " "), ("U+FEFF", "﻿"),
                        ("a bidi override", "‮")):
        _expect_rejection(f"encoding: {label} inside a `why:` scalar", failures, "why[0]",
                          lambda c=char: _parse_fixture(_SYNTHETIC.replace(
                              "A byte predicate, not a grammar.",
                              f"A byte predicate{c}, not a grammar.")))
    _expect_rejection("encoding: a trailing backslash inside a `why:` scalar", failures,
                      "swallows the line beneath it",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '      why: ["A byte predicate, not a grammar."]',
                          "      why: ['A byte predicate, not a grammar. \\\\']")))
    _expect_rejection("encoding: a C++-era escaped quote inside a `why:` scalar", failures,
                      "leftover from",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '      why: ["A byte predicate, not a grammar."]',
                          "      why: ['Narrowing them to \\\\\"synthetic\\\\\" would be "
                          "a change.']")))
    _case("encoding: a `why:` carrying non-ASCII is ACCEPTED when quoted", failures,
          lambda: _assert(
              "⚠" in render_fixture(text=_SYNTHETIC.replace(
                  "A byte predicate, not a grammar.",
                  "⚠ A byte predicate, not a grammar.")),
              "the warning glyphs are load-bearing typography and must survive verbatim"))

    # ── the subset fences inherited from codegen_common ──────────────────────
    _expect_rejection("subset: tabs", failures, "tab",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "  version:", "\tversion:")))
    _expect_rejection("subset: duplicate keys", failures, "duplicate",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '  version: "1.0.0"', '  version: "1.0.0"\n  version: "2.0.0"')))
    _expect_rejection("subset: anchors", failures, "anchor",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '  version: "1.0.0"', "  version: &v 1.0.0")))

    if failures:
        for failure in failures:
            print(f"selftest FAIL — {failure}", file=sys.stderr)
        return 1
    print("selftest OK — declared order is content and never sorted, the suffix reaches "
          "the namespace and nothing else, the hash is reformat-stable and prose-blind, "
          "emission is byte-stable under hash seed and locale, and every malformed "
          "declaration is refused by name")
    return 0


def _row_prefixes(rendered: str, array_symbol: str) -> list[str]:
    """The `.prefix` (or `.token`) initializers of ONE emitted array, in emitted order."""
    body = rendered[rendered.index(f"{array_symbol}{{{{"):]
    body = body[: body.index("\n}};")]
    return [line.split(" = ", 1)[1].rstrip(",") for line in body.split("\n")
            if ".prefix = " in line or ".token = " in line]


def _reorder_sections(text: str) -> str:
    """Move `revisions:` to the end of the document — the sections keep their contents."""
    marker = "  revisions:\n"
    start = text.index(marker)
    end = text.index("  channels:\n")
    return text[:start] + text[end:] + text[start:end]


# ── entry point ──────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="build-time codegen for an insight-canon dialect semantic package")
    parser.add_argument("--declaration", type=Path,
                        help="the <dialect>.dialect.yaml to project")
    parser.add_argument("--out", type=Path, help="generated C++ include to write")
    parser.add_argument("--module-suffix", default="",
                        help="appended to the C++ namespace (and, in the wrapper, to the "
                             "module name) — a GENERATION parameter, never declaration "
                             "content")
    parser.add_argument("--selftest", action="store_true",
                        help="run the synthetic-fixture selftest and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.declaration is None or args.out is None:
        parser.error("--declaration and --out are required (or use --selftest)")
    try:
        return generate(args.declaration, args.out, args.module_suffix)
    except DeclarationError as error:
        print(f"dialect_package_codegen: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
