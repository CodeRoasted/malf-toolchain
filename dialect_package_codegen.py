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

A MARKER ROW MAY DECLARE ITS PAYLOAD'S VERSION COORDINATE. The optional mapping
`version_coordinate: {introducer, shape}` on a marker row states where that unit's name
holds its version: the introducer byte sequence, and the payload shape it applies to, a
member of a closed core enum. It is data the core applies; no version syntax is parsed
here. A row that declares none omits the key, keeps its content hash and emits the bytes
it emitted before. The coordinate is a reader-side derivation, so it appears on the
recognition row alone and the derived emit row carries no copy of it.

A DIALECT MAY DECLARE A KEY A STREAM'S ACQUIRER SUPPLIES A VALUE UNDER. A row of the
optional `declared_values:` section names the key and the markers behind which the core
masks a digit run equal to the declared value (DN-133.D1): the dialect owns the vocabulary
(`PR-`), the acquirer owns the value (a run's own pull-request number), and the core owns the
mechanism. No value appears here, ever. A declaration with no such section keeps its content
hash and emits the bytes it emitted before.

A MARKER ROW MAY OPEN A UNIT WITHOUT NAMING IT. The optional `role: Opens` states that the
row's line opens a unit of its kind and carries no identity; the unit is named by a
naming row of the same kind that follows. A row that declares no role NAMES its unit, as
every row did before, so "names" has exactly one spelling: the key's absence, which keeps
the row's content hash and its emitted bytes. An opening row declares no `child_order:`,
no `extract:` and no `version_coordinate:` — it carries no payload, so it has no class,
no instance and no version — and its kind must have a naming row in the same section, or
it could never take effect. When an opener takes effect is the core's algorithm, applied
by the consumer that segments; nothing here decides it. The derived emit row carries the
role, so a writer selecting a unit's banner selects the naming row and never the opener.

A ROLE ROW MAY MATCH THE LINE'S WHOLE SHAPE INSTEAD OF ITS PREFIX (ADR-17.D14). A row declares
exactly one of `prefix:` and `shape:`. A shape is literal bytes with `{n}` holes, each a decimal
number, and the core matches it against the whole content after the transport peel and a
trailing-whitespace trim. It is one closed core kind, not a pattern language: no other hole, no
escape, no literal brace, and no shape whose match would depend on how much a hole takes. The
`Progress` role is declared by a shape alone, because the lines it samples share their prefix
with lines that are content. The emitted row carries the shape in the bytes member and adds
`.match = RoleMatchKind::Shape`; a prefix row emits no match kind, so a declaration of prefix
rows alone keeps its content hash and its emitted bytes.

A DIALECT MAY DECLARE ITS PLATFORM'S STOP ANNOUNCEMENTS (ADR-17.D15). A row of the optional
`stop_announcements:` section is a whole line a runner writes into a step it stops, matched as
a role row's `shape:` is, except that a hole is admitted and not required: a runner's stop line
is usually fixed text. The row declares no role and no level, because a stop announcement keeps
both (a line holds one role); it is a second declaration about the line, read by a consumer that
orders a unit's own errors against the stop. A declaration with no such section keeps its
content hash and emits the bytes it emitted before, the manifest member included.

A MARKER ROW MAY CLOSE THE STEPS OF ITS JOB. The optional `role: Closes` states that the
row's line enters the current job's epilogue, a step-level unit the core supplies and names
(ADR-20.D32): the runner's own teardown, which no line names. A closing row is legal only on
`kind: Step`, and like an opening row it declares no `child_order:`, no `extract:` and no
`version_coordinate:`, since the unit it enters carries no class, instance or version of the
dialect's. What the row does to the open unit is the core's algorithm; the derived emit row
carries the role, so a writer never selects it as a banner.

A MARKER ROW MAY READ A KEYED PAYLOAD, AND END AN INNER STEP (DN-162.D4, DN-162.D5). Kind
`InnerStep` is a unit inside a step that recurses; `role: Ends`, legal on that kind alone, ends
the innermost open one. Extractor `KeyedFields` reads `key=value` pairs separated by `;` up to a
line-final `]`, and its row declares `fields:`, which payload key plays which part, as data, so
core holds no platform's keys or literals. Each row shape declares an EXACT set of field roles
(DN-162.O3 item 8): a naming row declares `name:`, `display:`, `segment:`, `ordinal:` and
`anonymous:`, and an Ends row declares `extract: KeyedFields` with `outcome:` and
`propagated_outcome:` (DN-162.O3 item 9), the ended unit's own declared outcome and the one its
containing unit counted. A key role
holds no `=`, `;` or `]`, a literal role no `;`, and two roles never read one key. `fields:` is
required with `KeyedFields` and refused with any other extractor; a `KeyedFields` row declares no
version coordinate, since an inner step's version is its own step banner's. The field roles are
hashed, and they reach both projections after `.role` in the core's member order. A declaration
using none of these keeps its content hash and its emitted bytes.

DECLARATION SCHEMA 2: A PACKAGE MAY RECOGNIZE SEVERAL VENDOR GENERATIONS (DN-162.D1, DN-162.D2).
`revisions: names:` lists the generations the package recognizes, oldest first, and the order is
content. With more than one, every row declares `since: <revision>`, the revision that
INTRODUCED it: a lower bound, so its owning set is that revision and every later one, and a row
a newer generation dropped is unspellable. A row that names none is refused, and so is
`since:` on a one-revision declaration, where it would be one constant copied per row. Emitted
rows carry it as their last member `.revision_since`, the derived emit row copying its marker
row's, and the package asserts core's ownership fence; a one-revision declaration emits neither
and keeps its content hash and every byte schema 1 emitted but the header's version line.
Every revision after the first declares `backward_compatible_with:` its immediate predecessor
and `oracle: revisions/<predecessor>.dialect.yaml`, the predecessor's declaration as it shipped,
retained beside the live one. The oracle is read and never projected: no row, digest or byte of
it reaches the output, and the claim enters this declaration's content hash and nothing the
package's identity sees. Before emission the claim is checked: every oracle row has a live row
equal on every hashed field but `since:`, owned no later than the predecessor (coverage and
meaning); those rows keep the oracle's order (order); a row introduced later shares no literal
prefix with an oracle row of its section on a stream both admit (no shadowing); and a row
claiming the predecessor's ownership is held by the oracle. A failure names the oracle row and
ADR-22.D8's forcing event: such a generation is not a superset and routes to a package of its own.

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

TOOL_VERSION = "11"  # 11: an Ends row reads the propagated outcome too (DN-162.O3 item 9)
SCHEMA_VERSION = "2"

DIALECT_FILE_SUFFIX = ".dialect.yaml"


# ── the closed core vocabularies, transcribed as TEXT ────────────────────────
#
# Transcription, not typing: a declaration value is carried to the emitted enumerator as a
# string, so no locale- or stdlib-dependent conversion exists anywhere in the path
# (determinism MUST 3). Growing one of these sets is a core grammar-version bump — a new
# enumerator in `canon.spi.cppm` / `canon.api.cppm` — and this file follows it, never
# leads it.

_STRUCTURAL_ROLES = ("None", "GroupBegin", "GroupEnd", "Terminator", "Progress")
# The role a row may announce ONLY through a shape: a sample line shares its prefix with lines
# that are content (ADR-17.D14), so a prefix row announcing it would take them too.
_SHAPE_ONLY_ROLES = ("Progress",)
# The one hole a shape admits: a decimal number, `\d+(\.\d+)?`, matched by the core.
_SHAPE_HOLE = "{n}"
_MARKER_KINDS = ("None", "Job", "Step", "InnerStep")
_CHILD_ORDERS = ("Ordered", "Unordered")
_PAYLOAD_EXTRACTS = ("None", "RemainderAfterPrefix", "RemainderToClosingParen",
                     "NumericFieldThenRemainder", "KeyedFields")
# The extractor whose payload is `key=value` pairs up to a line-final `]`: the row says which key
# plays which part, as data, so core holds no platform's keys (DN-162.D4 point 3).
_KEYED_FIELDS = "KeyedFields"
# The field roles a `KeyedFields` row may declare, in the core's member order — a designated
# initializer follows it, so they are emitted in this order and never in the declaration's.
# `name`, `display`, `outcome` and `propagated_outcome` name a payload KEY; `segment`, `ordinal`
# and `anonymous` are literal bytes the core applies to the name's value (DN-162.D5). Each row
# shape declares an EXACT set: a role a shape could omit is a core branch no declaration
# exercises. `outcome` is the ended unit's OWN declared outcome and `propagated_outcome` the one
# its containing unit counted (DN-162.D8): named for what they mean, never for a platform's key.
_FIELD_ROLES = ("name", "display", "segment", "ordinal", "anonymous", "outcome",
                "propagated_outcome")
_FIELD_KEY_ROLES = ("name", "display", "outcome", "propagated_outcome")
_FIELD_LITERAL_ROLES = ("segment", "ordinal", "anonymous")
_NAMING_FIELD_ROLES = ("name", "display", "segment", "ordinal", "anonymous")
_ENDS_FIELD_ROLES = ("outcome", "propagated_outcome")
_ENDS_SHAPE = ("an Ends row declares exactly `extract: KeyedFields` and "
               "`fields: {outcome: …, propagated_outcome: …}`")
_PAYLOAD_SEPARATORS = ("=", ";", "]")
# The payload shapes a marker row's version coordinate may name. A row that declares no
# coordinate omits the key: the core's `None` shape is an ABSENCE here, never an authorable
# value, so "declared none" has exactly one spelling.
_VERSION_PAYLOAD_SHAPES = ("OneToken",)
# The roles a marker row may declare. A row that declares none NAMES its unit: the core's
# `Names` role is an ABSENCE here, never an authorable value, so "names" has exactly one
# spelling.
_MARKER_ROLES = ("Opens", "Closes", "Ends")
_ROLE_ROW_KEYS = ("prefix", "kind", "role", "dialect_gate", "channel_gate", "why")
_LOG_LEVELS = ("Trace", "Debug", "Info", "Warn", "Error", "Fatal")
_RUN_OUTCOMES = ("Unknown", "Success", "Failure", "Unstable", "Aborted", "Cancelled")

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
# come and go), whether it is authorable NON-EMPTY in this schema (does it specify a
# row shape this tool can project?), and whether it is authorable EMPTY — the
# declared-absence seat, which lives at the intersection.

_ROW_SECTIONS = ("roles", "markers", "level_lifts", "outcome_tokens",
                 "outcome_markers", "locations", "value_classes", "declared_values",
                 "stop_announcements")
_VOCABULARY_SECTIONS = ("channels", "revisions")
_SECTION_NAMES = _ROW_SECTIONS + _VOCABULARY_SECTIONS

# Legal, but EMPTY-only in this schema: the declared-absence seat and nothing more. The row
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
    "recoverability": ("RESERVED in this schema, and rejected rather than ignored "
                       "(DN-17.D18). The block is specified and its fence is expressible, "
                       "but a grammar that ACCEPTS a claim it cannot project lets someone "
                       "author a claim that silently does nothing. It arrives with the "
                       "first real claim, as one additive grammar bump."),
}

_ROOT_KEYS = ("name", "version", "why", "code_tier") + _SECTION_NAMES

# A revision's compatibility claim (DN-162.D2): the predecessor it is backward compatible with,
# and the retained declaration that is its oracle. Keys of a `revisions:` entry, never a row's.
_REVISION_CLAIM_KEYS = ("backward_compatible_with", "oracle")

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


# The C0 block and DEL, refused by name. A `why:` is copied VERBATIM into a `//` comment,
# so a control code is not an escaping question here — it is a byte in a source file: NUL
# is a diagnostic on every compile leg, FF/VT/BS make the emitted line render as something
# other than its bytes, and BEL/ESC turn a code review into terminal output. The table
# names the ones an author might plausibly paste; anything else in the range is refused by
# codepoint, because the fence is the RANGE and the names are courtesy.
_WHY_CONTROL_NAMES = {0x00: "NUL", 0x07: "BEL", 0x08: "BS", 0x09: "TAB", 0x0B: "VT",
                      0x0C: "FF", 0x1B: "ESC", 0x7F: "DEL"}


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
        for char in line:
            code = ord(char)
            if code < 0x20 or code == 0x7F:
                named = _WHY_CONTROL_NAMES.get(code)
                fail(source, None,
                     f"{where}: U+{code:04X}"
                     + (f" {named}" if named else "")
                     + " — a control character. The argument is emitted verbatim as a "
                     "`//` comment line, so this is not an escaping question: it is a "
                     "byte in a C++ source file, where a NUL is a diagnostic on every "
                     "compile leg and a form feed or a backspace makes the line render "
                     "as something other than what it says. A `why:` carries prose.")
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


def _shape(node: dict, context: str, source: str, *, hole_required: bool) -> str:
    """A whole-line SHAPE: literal bytes with `{n}` holes, each a decimal number.

    The core matches it against the line's whole content after the transport peel and a
    trailing-whitespace trim, greedily and without backtracking, so every refusal below
    names a shape whose match would depend on how much a hole takes: a hole is exactly the
    number the line prints, never a part of one. A role row's shape samples a quantity, so it
    requires a hole; a stop announcement is usually fixed text, so it admits one.
    """
    value = _prefix(node, "shape", context, source)
    if hole_required and _SHAPE_HOLE not in value:
        fail(source, None,
             f"{context}: `shape: {value}` has no hole — a shape carries at least one hole "
             f"`{_SHAPE_HOLE}`, the sampled quantity; a line with none is no sample")
    if value != value.rstrip():
        fail(source, None,
             f"{context}: `shape:` ends in trailing whitespace — the core trims a line's "
             "trailing whitespace before matching, so this shape could never match")
    literals = value.split(_SHAPE_HOLE)
    if any("{" in literal or "}" in literal for literal in literals):
        fail(source, None,
             f"{context}: `shape: {value}` — a hole is spelled `{_SHAPE_HOLE}` and nothing "
             "else, and a brace is never literal: the hole is the one closed core kind")
    if any(not between for between in literals[1:-1]):
        fail(source, None,
             f"{context}: `shape: {value}` puts a hole against a digit or another hole — "
             "the line's number would then be split between them")
    for before, after in zip(literals, literals[1:]):
        if (before and before[-1].isdigit()) or (after and after[0].isdigit()):
            fail(source, None,
                 f"{context}: `shape: {value}` puts a hole against a digit or another hole — "
                 "the line's number would then be split between them")
        if (after.startswith(".") and (len(after) == 1 or after[1].isdigit())) \
                or (before.endswith(".") and len(before) > 1 and before[-2].isdigit()):
            fail(source, None,
                 f"{context}: `shape: {value}` puts a decimal point between a hole and a "
                 "digit or a hole — a hole takes a decimal fraction, so the line's number "
                 "would then be split")
    return value


def _validate_role_row(row: dict, context: str, source: str) -> dict:
    """A role row announces its role by a line PREFIX, or by the line's whole SHAPE.

    The validated row carries `prefix` or `shape` and never both, so a declaration of prefix
    rows alone keeps its content hash and its emitted bytes.
    """
    expect_keys(row, ("prefix", "shape", "role", "dialect_gate", "why"), context, source,
                _DIALECT_REJECTIONS)
    if ("prefix" in row) == ("shape" in row):
        fail(source, None,
             f"{context}: a role row declares exactly one of `prefix:` and `shape:` — the "
             "line's opening bytes, or its whole content with `{n}` holes")
    role = _enum_value(row, "role", _STRUCTURAL_ROLES, context, source)
    if "prefix" in row:
        if role in _SHAPE_ONLY_ROLES:
            fail(source, None,
                 f"{context}: `role: {role}` on a prefix row — the role is declared by its "
                 "exact shape, because the lines it samples share their prefix with lines "
                 "that are content (ADR-17.D14)")
        matched = {"prefix": _prefix(row, "prefix", context, source)}
    else:
        matched = {"shape": _shape(row, context, source, hole_required=True)}
    return {
        **matched,
        "role": role,
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


def _validate_version_coordinate(node, context: str, source: str) -> dict:
    """Where the row's payload holds its unit's VERSION: an introducer and a payload shape.

    The dialect states its own version syntax as data; the core applies it and knows no
    platform's reference grammar. Both members are required: a coordinate is declared whole
    or not at all, which is the rule composition enforces on a hand-written package.
    """
    if not isinstance(node, dict):
        fail(source, None,
             f"{context}: `version_coordinate:` must be a mapping of `introducer:` and "
             "`shape:` — a row that declares no version coordinate omits the key")
    expect_keys(node, ("introducer", "shape", "why"), context, source, _DIALECT_REJECTIONS)
    return {
        "introducer": _prefix(node, "introducer", f"{context} version_coordinate", source),
        "shape": _enum_value(node, "shape", _VERSION_PAYLOAD_SHAPES,
                             f"{context} version_coordinate", source),
        "why": _validate_why(node, f"{context} version_coordinate", source),
    }


def _validate_fields(node, *, ends: bool, context: str, source: str) -> dict:
    """A `KeyedFields` row's field roles: which payload key plays which part.

    Ruled by DN-162.D4, DN-162.D5 and DN-162.O3 item 8. Each row shape declares an EXACT set: a
    row that names its unit declares `name`, `display`, `segment`, `ordinal` and `anonymous`, and
    an Ends row declares `outcome` and `propagated_outcome`, the ended unit's own declared outcome
    token and the one its containing unit counted (DN-162.O3 item 9). A key role names
    a payload key, so it holds no separator; a literal role holds no `;`, at which a value ends.
    Two roles never read one key.
    """
    if not isinstance(node, dict):
        fail(source, None,
             f"{context}: `fields:` must be a mapping of field roles to payload keys")
    expect_keys(node, _FIELD_ROLES, f"{context} fields", source, _DIALECT_REJECTIONS)
    fields = {role: _prefix(node, role, f"{context} fields", source)
              for role in _FIELD_ROLES if role in node}
    expected = set(_ENDS_FIELD_ROLES if ends else _NAMING_FIELD_ROLES)
    missing = [role for role in _FIELD_ROLES if role in expected - set(fields)]
    extra = [role for role in _FIELD_ROLES if role in set(fields) - expected]
    if missing or extra:
        gaps = "; it ".join([*(f"lacks `{role}`" for role in missing),
                              *(f"adds `{role}`" for role in extra)])
        if ends:
            fail(source, None,
                 f"{context}: {_ENDS_SHAPE} — it {gaps}. It names nothing, so the two fields it "
                 "reads are the ended unit's own declared outcome token and the one its "
                 "containing unit counted")
        fail(source, None,
             f"{context}: a `KeyedFields` row that names its unit declares exactly `name`, "
             f"`display`, `segment`, `ordinal` and `anonymous` — it {gaps}. A role a row could "
             "omit is a core branch no declaration exercises; the declared outcomes arrive on "
             "the line that ENDS the unit")
    read: dict[str, str] = {}
    for role, value in fields.items():
        if role in _FIELD_LITERAL_ROLES:
            if ";" in value:
                fail(source, None,
                     f"{context}: `{role}: {value}` — a literal role holds no `;`: a value ends "
                     "at the first `;`, so such a literal could never match")
            continue
        if any(separator in value for separator in _PAYLOAD_SEPARATORS):
            fail(source, None,
                 f"{context}: `{role}: {value}` holds a payload separator "
                 f"({' '.join(_PAYLOAD_SEPARATORS)}) — a key is the bytes before a `=`")
        if value in read:
            fail(source, None,
                 f"{context}: `{read[value]}:` and `{role}:` both read key `{value}` — one key "
                 "plays one part")
        read[value] = role
    return fields


def _validate_unit_role_row(row: dict, channels: list[str], context: str, source: str) -> dict:
    """A row that OPENS a unit, CLOSES the steps of its job, or ENDS its innermost inner step.

    An opener's unit is named by the naming row that follows, and a closer's unit is the
    core's supplied epilogue, so everything a payload derives — the class, the instance, the
    version — is never this row's, and a row declaring any of it would be a second, silent
    source of the same fact. An ending row names nothing either; its payload is the ended unit's
    own declared outcome and the one its containing unit counted, read by `KeyedFields` from its
    `outcome:` and `propagated_outcome:` fields (DN-162.D4 point 2, DN-162.O3 item 9).
    """
    role = _enum_value(row, "role", _MARKER_ROLES, context, source)
    forbidden = ("child_order", "version_coordinate") if role == "Ends" else (
        "child_order", "extract", "version_coordinate", "fields")
    for key in forbidden:
        if key in row:
            fail(source, None,
                 f"{context}: `{key}:` on a row declaring `role: {role}` — a row that opens, "
                 "closes or ends a unit carries no identity: an opened unit is named by the "
                 "naming row of its kind that follows, a closed job's epilogue is the core's own "
                 "unit, an ended unit was named when it opened, and none takes its class, "
                 "instance or version from this row")
    allowed = _ROLE_ROW_KEYS + (("extract", "fields") if role == "Ends" else ())
    expect_keys(row, allowed, context, source, _DIALECT_REJECTIONS)
    kind = _enum_value(row, "kind", _MARKER_KINDS, context, source)
    if role == "Opens" and kind == "None":
        fail(source, None,
             f"{context}: `kind: None` on a row declaring `role: Opens` — an opening row "
             "opens a unit of a kind, and `None` is no unit")
    if role == "Closes" and kind != "Step":
        fail(source, None,
             f"{context}: `kind: {kind}` on a row declaring `role: Closes` — a closing row "
             "enters its job's epilogue, a step-level unit, so it is legal only on `kind: Step`")
    if role == "Ends" and kind != "InnerStep":
        fail(source, None,
             f"{context}: `kind: {kind}` on a row declaring `role: Ends` — an ending row ends "
             "the innermost open inner step, so it is legal only on `kind: InnerStep`, as "
             "core's composition refuses it elsewhere")
    validated = {
        "prefix": _prefix(row, "prefix", context, source),
        "kind": kind,
        "role": role,
        "dialect_gate": _dialect_gate(row, context, source),
        "channel_gate": _channel_gate(row, channels, context, source),
        "why": _validate_why(row, context, source),
    }
    if role == "Ends":
        # One shape: the ended unit's two declared outcomes, read by `KeyedFields` (DN-162.O3
        # items 8 and 9).
        if "extract" not in row:
            fail(source, None, f"{context}: {_ENDS_SHAPE} — it reads the ended unit's declared "
                               "outcome tokens, so it declares the extractor that reads them")
        extract = _enum_value(row, "extract", _PAYLOAD_EXTRACTS, context, source)
        if extract != _KEYED_FIELDS:
            fail(source, None, f"{context}: `extract: {extract}` — {_ENDS_SHAPE}")
        validated["extract"] = extract
        _keyed_fields(row, validated, ends=True, context=context, source=source)
    return validated


def _keyed_fields(row: dict, validated: dict, *, ends: bool, context: str, source: str) -> None:
    """`fields:` is required with `extract: KeyedFields` and refused with any other extractor."""
    extract = validated.get("extract", "None")
    if extract == _KEYED_FIELDS:
        if "fields" not in row:
            fail(source, None,
                 f"{context}: `extract: KeyedFields` requires `fields:` — the payload's keys "
                 "are the dialect's, declared as row data, and core holds none")
        validated["fields"] = _validate_fields(row["fields"], ends=ends, context=context,
                                               source=source)
    elif "fields" in row:
        fail(source, None,
             f"{context}: `fields:` with `extract: {extract}` — field roles are read by "
             "`KeyedFields` alone")


def _validate_marker_row(row: dict, channels: list[str], context: str, source: str) -> dict:
    # The key enters the validated row ONLY when declared, so a declaration whose rows all
    # name their units keeps its content hash and its emitted bytes.
    if "role" in row:
        return _validate_unit_role_row(row, channels, context, source)
    expect_keys(row, ("prefix", "kind", "child_order", "dialect_gate", "extract",
                      "channel_gate", "version_coordinate", "fields", "why"), context, source,
                _DIALECT_REJECTIONS)
    validated = {
        "prefix": _prefix(row, "prefix", context, source),
        "kind": _enum_value(row, "kind", _MARKER_KINDS, context, source),
        "child_order": _enum_value(row, "child_order", _CHILD_ORDERS, context, source),
        "dialect_gate": _dialect_gate(row, context, source),
        "extract": _enum_value(row, "extract", _PAYLOAD_EXTRACTS, context, source),
        "channel_gate": _channel_gate(row, channels, context, source),
        "why": _validate_why(row, context, source),
    }
    # The key enters the validated row ONLY when declared, so a declaration that names no
    # version coordinate keeps its content hash and its emitted bytes.
    if "version_coordinate" in row:
        if validated["extract"] == _KEYED_FIELDS:
            fail(source, None,
                 f"{context}: `version_coordinate:` with `extract: KeyedFields` — an inner "
                 "step's version is its own step banner's, read through that row's coordinate "
                 "and never parsed out of its name (DN-162.D5)")
        validated["version_coordinate"] = _validate_version_coordinate(
            row["version_coordinate"], context, source)
    _keyed_fields(row, validated, ends=False, context=context, source=source)
    return validated


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


def _validate_declared_value_row(row: dict, context: str, source: str) -> dict:
    """A key a stream's acquirer supplies a value under, and the markers the core masks behind.

    The key is what a caller types (`--changed-context pull_request=<n>`), so it is spelled like
    a dialect name. A marker ending in a digit is refused here as composition refuses it: the
    digit run after it would not be the run the marker introduces.
    """
    expect_keys(row, ("key", "markers", "dialect_gate", "why"), context, source,
                _DIALECT_REJECTIONS)
    key = required_scalar(row, "key", context, source)
    _ascii_field(key, f"{context}: `key:`", source)
    if not key or not key.replace("_", "a").isalnum() or not key[0].isalpha() \
            or key != key.lower():
        fail(source, None,
             f"{context}: `key: {key}` must match [a-z][a-z0-9_]* — it is what a caller "
             "declares a value under")
    markers = row.get("markers")
    if not isinstance(markers, list) or not markers:
        fail(source, None,
             f"{context}: `markers:` must be a non-empty sequence — a key with no marker "
             "could never mask anything")
    seen: set[str] = set()
    for position, marker in enumerate(markers):
        marker_context = f"{context} markers[{position}]"
        if not isinstance(marker, str) or not marker:
            fail(source, None, f"{marker_context}: a marker is a non-empty scalar")
        _ascii_field(marker, marker_context, source)
        if marker[-1].isdigit():
            fail(source, None,
                 f"{marker_context}: {marker!r} ends in a digit — the digit run after a "
                 "marker would then not be the run the marker introduces")
        if marker in seen:
            fail(source, None, f"{marker_context}: duplicate marker {marker!r}")
        seen.add(marker)
    return {
        "key": key,
        "markers": list(markers),
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


def _validate_stop_announcement_row(row: dict, context: str, source: str) -> dict:
    """A whole line a runner writes into a step it stops, matched as a whole-line shape.

    It declares no role and no level: the line keeps both, and the row only lets a consumer
    order a unit's own errors against the stop (ADR-17.D15).
    """
    expect_keys(row, ("shape", "dialect_gate", "why"), context, source, _DIALECT_REJECTIONS)
    return {
        "shape": _shape(row, context, source, hole_required=False),
        "dialect_gate": _dialect_gate(row, context, source),
        "why": _validate_why(row, context, source),
    }


_ROW_VALIDATORS = {
    "stop_announcements": _validate_stop_announcement_row,
    "declared_values": _validate_declared_value_row,
    "roles": _validate_role_row,
    "level_lifts": _validate_level_lift_row,
    "outcome_tokens": _validate_outcome_token_row,
}


def _row_since(item: dict, revisions: list[str], context: str,
               source: str) -> tuple[dict, str | None]:
    """Split a row's `since:` from the fields its section's validator reads (DN-162.D1).

    Required on every row of a multi-revision declaration and refused on a singleton one, so
    "owned by every revision" has exactly one spelling per package shape: the key's absence at
    cardinality one, and the first revision's name above it. The value is a lower bound in the
    span's declared order, never a set, so a row a newer generation dropped is unspellable.
    """
    if "since" not in item:
        if len(revisions) > 1:
            fail(source, None,
                 f"{context}: declares no `since:` — a declaration naming {len(revisions)} "
                 "revisions states, on every row, the revision that INTRODUCED it "
                 f"(one of {', '.join(revisions)}; DN-162.D1). A row with a defaulted owner is "
                 "a row nobody decided.")
        return item, None
    if len(revisions) == 1:
        fail(source, None,
             f"{context}: `since:` on a singleton span — with one revision it would be one "
             "constant copied onto every row, the per-row carriage ADR-17.D9 rejected. A row of "
             "a one-revision declaration names no revision; `since:` arrives with the second.")
    since = item["since"]
    if not isinstance(since, str):
        fail(source, None, f"{context}: `since:` must be a scalar")
    _ascii_field(since, f"{context}: `since:`", source)
    if since not in revisions:
        fail(source, None,
             f"{context}: `since: {since}` names no revision the span declares "
             f"({', '.join(revisions)})")
    return {key: value for key, value in item.items() if key != "since"}, since


def _validate_rows(items: list, name: str, source: str, revisions: list[str],
                   validate) -> list[dict]:
    """Each row through its section's validator, carrying its `since:` when it declares one."""
    rows: list[dict] = []
    for position, item in enumerate(items):
        context = f"section `{name}:` rows[{position}]"
        fields, since = _row_since(_as_mapping(item, name, position, source), revisions, context,
                                   source)
        row = validate(fields, context)
        if since is not None:
            row["since"] = since
        rows.append(row)
    return rows


def _validate_section(node, name: str, source: str, channels: list[str] | None = None,
                      revisions: list[str] | None = None) -> dict:
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
             f"{context}: `{name}` may be declared EMPTY with an argument in schema "
             f"v{SCHEMA_VERSION} — {_EMPTY_ONLY_SECTIONS[name]}. The schema specifies no row "
             "shape for it, so there is nothing this tool could project from the rows above.")

    if name in _VOCABULARY_SECTIONS:
        return {"why": why, "names": _validate_vocabulary(items, name, source)}

    if name == "markers":
        rows = _validate_rows(items, name, source, revisions or [],
                              lambda row, row_context: _validate_marker_row(
                                  row, channels or [], row_context, source))
        named = {row["kind"] for row in rows if "role" not in row}
        for position, row in enumerate(rows):
            if row.get("role") in ("Opens", "Ends") and row["kind"] not in named:
                fail(source, None,
                     f"section `{name}:` rows[{position}]: a row declaring `role: "
                     f"{row['role']}` of kind `{row['kind']}`, and no row of this section names "
                     "a unit of that kind — an opener takes effect only when a naming row of its "
                     "kind follows it, and an ending row ends a unit one named, so this row "
                     "could never take effect")
    else:
        validator = _ROW_VALIDATORS[name]
        rows = _validate_rows(items, name, source, revisions or [],
                              lambda row, row_context: validator(row, row_context, source))
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
        claim: dict[str, str] = {}
        if isinstance(item, str):
            entry_name, why = item, []
        elif isinstance(item, dict):
            if name != "revisions":
                for key in _REVISION_CLAIM_KEYS:
                    if key in item:
                        fail(source, None,
                             f"{context}: key `{key}:` is legal on `revisions:` alone — the "
                             "compatibility claim is about a vendor generation, and a channel "
                             "has no predecessor (DN-162.D2)")
            expect_keys(item, ("name", "why") + _REVISION_CLAIM_KEYS, context, source,
                        _DIALECT_REJECTIONS)
            entry_name = required_scalar(item, "name", context, source)
            why = _validate_why(item, context, source)
            for key in _REVISION_CLAIM_KEYS:
                if key in item:
                    claim[key] = _ascii_field(required_scalar(item, key, context, source),
                                              f"{context}: `{key}:`", source)
            if not why and not claim:
                fail(source, None,
                     f"{context}: the mapping form exists to carry a `why:` or a revision's "
                     "compatibility claim — an entry with neither is written as a bare scalar")
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
        entries.append({"name": entry_name, **claim, "why": why})
    return entries


def _check_revision_claims(entries: list[dict], source: str) -> None:
    """The compatibility claim's shape, by position in the span (DN-162.D2 Q2, Q3, Q5).

    Required on every revision after the first and refused on the first, so it is unspellable
    at cardinality one. It names only the IMMEDIATELY preceding revision — older ones follow by
    chaining, each step checked once — and its oracle is that revision's retained declaration
    at the one path the retention check watches.
    """
    for position, entry in enumerate(entries):
        context = f"section `revisions:` names[{position}] (`{entry['name']}`)"
        present = [key for key in _REVISION_CLAIM_KEYS if key in entry]
        if position == 0:
            if present:
                fail(source, None,
                     f"{context}: `{present[0]}:` on the first revision — nothing precedes it, "
                     "so there is nothing it could be compatible with")
            continue
        predecessor = entries[position - 1]["name"]
        for key in _REVISION_CLAIM_KEYS:
            if key not in entry:
                fail(source, None,
                     f"{context}: a revision after the first requires `{key}:` — it declares "
                     f"`backward_compatible_with: {predecessor}` and names the retained "
                     f"declaration `oracle: revisions/{predecessor}{DIALECT_FILE_SUFFIX}` the "
                     "claim is checked against (DN-162.D2)")
        claimed = entry["backward_compatible_with"]
        if claimed == "false":
            fail(source, None,
                 f"{context}: `backward_compatible_with: false` — `false` is not spellable. One "
                 "union package holds only generations that are supersets of their "
                 "predecessor; a generation that is not one is ADR-22.D8's forcing event and "
                 "routes to arm (a), a separate package (DN-162.D2 Q3)")
        if claimed != predecessor:
            fail(source, None,
                 f"{context}: `backward_compatible_with: {claimed}` — the claim names the "
                 f"IMMEDIATELY preceding revision, `{predecessor}`: compatibility with an older "
                 "one follows by chaining, and each step is checked once (DN-162.D2 Q2)")
        expected = f"revisions/{predecessor}{DIALECT_FILE_SUFFIX}"
        if entry["oracle"] != expected:
            fail(source, None,
                 f"{context}: `oracle: {entry['oracle']}` — the oracle of `{entry['name']}` is "
                 f"`{expected}`, the retained declaration of its predecessor at the one path "
                 "the retention check holds append-only (DN-162.D2, DN-162.D3)")


def _validate_code_tier(node, package_dir: Path, source: str, *, resolve_units: bool) -> dict:
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
        if resolve_units:
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


def validate_declaration(document: dict, stem: str, package_dir: Path, source: str, *,
                         as_oracle: bool = False) -> dict:
    """Validate one dialect declaration; return the annotated declaration.

    The returned structure carries `why:` for emission; `hashed_content()` strips it, and
    that stripped view is exactly what the content hash covers.

    `as_oracle` reads a RETAINED declaration as its successor's oracle (DN-162.D2): it is never
    projected, so its code-tier units are not resolved, and its own compatibility claim is not
    re-checked — that step was checked once, when it was the live declaration, and the
    retention check holds its bytes to the ones that shipped (DN-162.D3).
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
    entries = declaration["revisions"]["names"]
    if not entries:
        fail(source, None,
             "section `revisions:`: names no revision — core requires a non-empty vocabulary "
             "(`all_revisions_named`), so an empty span is not a declared absence")
    _check_revision_claims(entries, source)
    revisions = [entry["name"] for entry in entries]

    for section in _ROW_SECTIONS:
        if section in dialect:
            declaration[section] = _validate_section(dialect[section], section, source,
                                                     channels, revisions)

    if "code_tier" in dialect:
        declaration["code_tier"] = _validate_code_tier(dialect["code_tier"], package_dir,
                                                       source, resolve_units=not as_oracle)

    _check_row_uniqueness(declaration, source)
    if not as_oracle:
        for position in range(1, len(entries)):
            _check_against_oracle(declaration, position, package_dir, source)
    return declaration


def _check_row_uniqueness(declaration: dict, source: str) -> None:
    """Two rows with the same key inside ONE package are a typo, never a decision.

    Composition already refuses a duplicate key across the composed SET; catching it here
    names the declaration and the row position instead of the composed table.
    """
    keys = {
        "roles": lambda row: ("prefix" in row, row.get("prefix", row.get("shape")),
                              row["dialect_gate"]),
        "markers": lambda row: (row["prefix"], row["dialect_gate"], row["channel_gate"]),
        "level_lifts": lambda row: (row["prefix"], row["dialect_gate"]),
        "outcome_tokens": lambda row: (row["token"], row["dialect_gate"]),
        "declared_values": lambda row: (row["key"],),
        "stop_announcements": lambda row: (row["shape"], row["dialect_gate"]),
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


# ── the compatibility claim, checked against its oracle (DN-162.D2) ───────────────────
#
# Four mechanical properties, each judged section by section against the retained declaration
# of the predecessor: COVERAGE and MEANING (every oracle row has a live row equal to it on every
# hashed field but `since:`, owned no later than the predecessor), ORDER (those rows keep the
# oracle's relative order, since first-match and `serialize_manifest` walk declared order), and
# NO SHADOWING (a row introduced after the predecessor cannot take a line an oracle row of its
# section can take). Every refusal names the oracle row and the forcing event it is.

# The bytes a row is matched on: two rows sharing them and differing elsewhere are the
# MEANING drift ADR-22.D8 names, rather than a removed row and an unrelated added one.
_ROW_BYTES = {
    "roles": lambda row: row.get("prefix", row.get("shape")),
    "markers": lambda row: row["prefix"],
    "level_lifts": lambda row: row["prefix"],
    "outcome_tokens": lambda row: row["token"],
    "declared_values": lambda row: row["key"],
    "stop_announcements": lambda row: row["shape"],
}

# The literal bytes a row's match starts with: a prefix, or a shape's bytes before its first
# hole. `declared_values` is absent: its rows match no line, they name a key, and a new key
# cannot take an old key's value because the key is unique in the package.
_ROW_LITERAL = {
    "roles": lambda row: row["prefix"] if "prefix" in row else row["shape"].split(_SHAPE_HOLE)[0],
    "markers": lambda row: row["prefix"],
    "level_lifts": lambda row: row["prefix"],
    "outcome_tokens": lambda row: row["token"],
    "stop_announcements": lambda row: row["shape"].split(_SHAPE_HOLE)[0],
}


def _strip_why(node):
    if isinstance(node, dict):
        return {key: _strip_why(value) for key, value in node.items() if key != "why"}
    if isinstance(node, list):
        return [_strip_why(item) for item in node]
    return node


def _comparable(row: dict) -> dict:
    """A row's hashed fields with its owner removed: what the compatibility claim compares."""
    return {key: value for key, value in _strip_why(row).items() if key != "since"}


def _share_a_stream(row: dict, other: dict) -> bool:
    """Whether some stream admits both rows. The dialect gate never separates two rows of one
    package — `self` and `any` both fire on a stream declared as this dialect — so only a
    channel gate can: two concrete, different channels share no stream."""
    left, right = row.get("channel_gate"), other.get("channel_gate")
    return left is None or right is None or _CHANNEL_GATE_ANY in (left, right) or left == right


def _load_oracle(declaration: dict, position: int, package_dir: Path, source: str) -> dict:
    """The predecessor's retained declaration, read as an oracle and never projected."""
    entry = declaration["revisions"]["names"][position]
    context = f"section `revisions:` names[{position}] (`{entry['name']}`)"
    oracle_path = package_dir / entry["oracle"]
    if not oracle_path.is_file():
        fail(source, None,
             f"{context}: `oracle: {entry['oracle']}` names no file under {package_dir} — the "
             "oracle is the predecessor's declaration as it shipped, retained beside the live one")
    oracle_source = str(oracle_path)
    document = parse_subset_yaml(read_text(oracle_path, ascii_only=False), oracle_source,
                                 ascii_only=False)
    root = document.get("dialect")
    oracle_name = root.get("name") if isinstance(root, dict) else None
    if oracle_name != declaration["name"]:
        fail(source, None,
             f"{context}: the oracle declares `name: {oracle_name}`, this declaration "
             f"`name: {declaration['name']}` — an oracle is the retained declaration of THIS "
             "package's predecessor revision")
    oracle = validate_declaration(document, declaration["name"], package_dir, oracle_source,
                                  as_oracle=True)
    live_span = [item["name"] for item in declaration["revisions"]["names"]]
    oracle_span = [item["name"] for item in oracle["revisions"]["names"]]
    if oracle_span != live_span[:position]:
        fail(source, None,
             f"{context}: the oracle's span is [{', '.join(oracle_span)}], and the oracle of "
             f"`{entry['name']}` declares exactly the span before it, "
             f"[{', '.join(live_span[:position])}]")
    return oracle


def _check_against_oracle(declaration: dict, position: int, package_dir: Path,
                          source: str) -> None:
    """The step from revision `position - 1` to `position`, checked against its oracle."""
    oracle = _load_oracle(declaration, position, package_dir, source)
    span = [item["name"] for item in declaration["revisions"]["names"]]
    claimant, predecessor = span[position], span[position - 1]
    oracle_name = declaration["revisions"]["names"][position]["oracle"]

    def refuse(section: str, oracle_position: int, oracle_row: dict, what: str) -> None:
        fail(source, None,
             f"revision `{claimant}` claims `backward_compatible_with: {predecessor}`, and "
             f"oracle {oracle_name} section `{section}:` rows[{oracle_position}] "
             f"({_ROW_BYTES[section](oracle_row)!r}) {what}. That is ADR-22.D8's forcing "
             "event: a generation that is not a superset of its predecessor cannot share one "
             "union package with it, and routes to arm (a), a package of its own (DN-162.D2).")

    for section in _ROW_SECTIONS:
        oracle_rows = oracle.get(section, {}).get("rows", [])
        live_rows = declaration.get(section, {}).get("rows", [])
        live_comparable = [_comparable(row) for row in live_rows]
        held: set[int] = set()
        previous: tuple[int, int] | None = None
        for oracle_position, oracle_row in enumerate(oracle_rows):
            wanted = _comparable(oracle_row)
            if wanted not in live_comparable:
                same_bytes = [live for live, row in enumerate(live_rows)
                              if _ROW_BYTES[section](row) == _ROW_BYTES[section](oracle_row)]
                if same_bytes:
                    live = same_bytes[0]
                    fields = sorted(key for key in wanted.keys() | live_comparable[live].keys()
                                    if wanted.get(key) != live_comparable[live].get(key))
                    refuse(section, oracle_position, oracle_row,
                           f"is read differently by live rows[{live}], which changes its "
                           f"{', '.join(f'`{key}:`' for key in fields)} — the same bytes called "
                           "something else is the MEANING drift, not a compatible addition")
                refuse(section, oracle_position, oracle_row,
                       "has no equal row in the live declaration — a newer generation keeps "
                       "every row its predecessor recognized (coverage)")
            live = live_comparable.index(wanted)
            owner = live_rows[live]["since"]
            if span.index(owner) > position - 1:
                refuse(section, oracle_position, oracle_row,
                       f"is held by live rows[{live}] declaring `since: {owner}`, later than "
                       f"`{predecessor}`, whose retained declaration already holds it")
            if previous is not None and live < previous[0]:
                refuse(section, oracle_position, oracle_row,
                       f"is held by live rows[{live}], ahead of live rows[{previous[0]}] which "
                       f"holds oracle rows[{previous[1]}] — rows match first in declared order, "
                       "so a reorder is a meaning change")
            previous = (live, oracle_position)
            held.add(live)
        for live, row in enumerate(live_rows):
            if live in held:
                continue
            if span.index(row["since"]) <= position - 1:
                fail(source, None,
                     f"section `{section}:` rows[{live}] declares `since: {row['since']}`, and "
                     f"oracle {oracle_name}, the retained `{predecessor}` declaration holds no "
                     "equal row — `since:` names the revision that INTRODUCED a row "
                     "(DN-162.D1), and a row the oracle lacks was introduced after it")
            literal_of = _ROW_LITERAL.get(section)
            if literal_of is None:
                continue
            literal = literal_of(row)
            for oracle_position, oracle_row in enumerate(oracle_rows):
                oracle_literal = literal_of(oracle_row)
                if _share_a_stream(row, oracle_row) and (
                        literal.startswith(oracle_literal) or oracle_literal.startswith(literal)):
                    refuse(section, oracle_position, oracle_row,
                           f"can be shadowed by live rows[{live}] ({literal!r}, `since: "
                           f"{row['since']}`): one row's literal bytes are a prefix of the "
                           "other's on a stream both admit, so the newer row can take lines "
                           "the older one reads (judged on literal bytes, conservatively)")


# ── the content hash (DN-17.D19 MUST 4 / DN-17.D20) ──────────────────────────


def hashed_content(declaration: dict) -> dict:
    """The declaration's SEMANTIC content: every `why:` stripped, the NAME stripped.

    `why:` is prose, not ruleset content — editing an argument must not revoke a
    ratification, which is the same carve-out the hash already applies to formatting. The
    name stays out because resolution keys on it before any hash is compared, so a rename
    surfaces as a loud stale-record fault rather than as a quiet mismatch.
    """
    return {key: _strip_why(value) for key, value in declaration.items()
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


def _field_roles(fields: list[tuple[str, str]], row: dict) -> list[tuple[str, str]]:
    """The row's fields, then its `KeyedFields` field roles when it declares them.

    After `.role` and before `.revision_since`, the struct's last member (DN-162.O3 items 2 and
    7). The roles come in the core's member order because `_validate_fields` builds them in it.
    """
    if "fields" not in row:
        return fields
    roles = ", ".join(f".{role} = {_quoted(key)}" for role, key in row["fields"].items())
    return [*fields, ("fields", f"{{{roles}}}")]


def _owned(fields: list[tuple[str, str]], row: dict) -> list[tuple[str, str]]:
    """The row's fields, then the revision that introduced it when it declares one.

    LAST, because a core row struct gains `revision_since` as its last member, and only a
    multi-revision declaration's rows carry it (DN-162.D1): a one-revision package emits the
    bytes schema v1 emitted, and builds against a core without the member.
    """
    if "since" not in row:
        return fields
    return [*fields, ("revision_since", _quoted(row["since"]))]


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
    _emit_declared_value_rows(out, declaration)
    _emit_stop_announcement_rows(out, declaration)
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
        # A shape row carries its bytes in the same member and adds its match kind; a prefix
        # row emits none, the core's default, so its bytes are the ones it emitted before.
        fields = [
            ("prefix", _quoted(row.get("prefix", row.get("shape")))),
            ("role", f"insight::StructuralRole::{row['role']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ]
        if "shape" in row:
            fields.append(("match", "RoleMatchKind::Shape"))
        _emit_row(out, _owned(fields, row), row["why"])
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
        if "role" in row:
            # An opening or closing row carries no payload: its order and extractor are the
            # core's defaults, inert on a row that names nothing, and its role is the one fact
            # it adds.
            _emit_row(out, _owned(_field_roles([
                ("prefix", _quoted(row["prefix"])),
                ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
                ("dialect_gate", _gate_expression(row["dialect_gate"])),
                ("extract", f"PayloadExtract::{row.get('extract', 'None')}"),
                ("channel_gate", _channel_expression(row["channel_gate"])),
                ("role", f"MarkerRole::{row['role']}"),
            ], row), row), row["why"])
            continue
        fields = [
            ("prefix", _quoted(row["prefix"])),
            ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
            ("child_order", f"insight::tokenization::ChildOrder::{row['child_order']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
            ("extract", f"PayloadExtract::{row['extract']}"),
            ("channel_gate", _channel_expression(row["channel_gate"])),
        ]
        why = list(row["why"])
        # A reader-side derivation with no writer dual: the emit rows below carry the payload
        # verbatim, so the coordinate appears on the recognition row alone.
        coordinate = row.get("version_coordinate")
        if coordinate is not None:
            fields.append(("version",
                           f"{{.introducer = {_quoted(coordinate['introducer'])}, "
                           f".shape = VersionPayloadShape::{coordinate['shape']}}}"))
            why += coordinate["why"]
        _emit_row(out, _owned(_field_roles(fields, row), row), why)
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
        # The emit row copies its marker row's owner: the writer projection is derived, so
        # it never declares one of its own (DN-162.D1 point 6).
        if "role" in row:
            _emit_row(out, _owned(_field_roles([
                ("prefix", _quoted(row["prefix"])),
                ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
                ("dialect_gate", _gate_expression(row["dialect_gate"])),
                ("emit", f"insight::semantic::dual(PayloadExtract::{row.get('extract', 'None')})"),
                ("channel_gate", _channel_expression(row["channel_gate"])),
                ("role", f"MarkerRole::{row['role']}"),
            ], row), row), [])
            continue
        _emit_row(out, _owned(_field_roles([
            ("prefix", _quoted(row["prefix"])),
            ("kind", f"insight::tokenization::IntentMarkerKind::{row['kind']}"),
            ("child_order", f"insight::tokenization::ChildOrder::{row['child_order']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
            ("emit", f"insight::semantic::dual(PayloadExtract::{row['extract']})"),
            ("channel_gate", _channel_expression(row["channel_gate"])),
        ], row), row), [])
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
        _emit_row(out, _owned([
            ("prefix", _quoted(row["prefix"])),
            ("level", f"insight::LogLevel::{row['level']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row), row["why"])
    out.append("}};")
    out.append("")


def _emit_outcome_token_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("outcome_tokens")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    _emit_array(out, "OutcomeTokenRow", "kOutcomeTokens", len(section["rows"]))
    for row in section["rows"]:
        _emit_row(out, _owned([
            ("token", _quoted(row["token"])),
            ("outcome", f"insight::RunOutcome::{row['outcome']}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row), row["why"])
    out.append("}};")
    out.append("")


def _emit_declared_value_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("declared_values")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    for position, row in enumerate(section["rows"]):
        values = ", ".join(_quoted(marker) for marker in row["markers"])
        out.append(f"inline constexpr std::array<std::string_view, {len(row['markers'])}> "
                   f"kDeclaredValueMarkers{position}{{{{{values}}}}};")
    _emit_array(out, "DeclaredValueRow", "kDeclaredValues", len(section["rows"]))
    for position, row in enumerate(section["rows"]):
        _emit_row(out, _owned([
            ("key", _quoted(row["key"])),
            ("markers", f"kDeclaredValueMarkers{position}"),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row), row["why"])
    out.append("}};")
    name = declaration["name"]
    out.append("static_assert(std::ranges::all_of(kDeclaredValues, "
               "insight::semantic::declared_value_row_well_formed),")
    out.append(f'              "{name}: a declared value names no key or no marker, or a '
               'marker is empty or ends in a "')
    out.append('              "digit -- composition would refuse the package");')
    out.append("")


def _emit_stop_announcement_rows(out: list[str], declaration: dict) -> None:
    section = declaration.get("stop_announcements")
    if section is None or not section["rows"]:
        return
    _emit_why(out, section["why"])
    _emit_array(out, "StopAnnouncementRow", "kStopAnnouncements", len(section["rows"]))
    for row in section["rows"]:
        _emit_row(out, _owned([
            ("shape", _quoted(row["shape"])),
            ("dialect_gate", _gate_expression(row["dialect_gate"])),
        ], row), row["why"])
    out.append("}};")
    name = declaration["name"]
    out.append("static_assert(std::ranges::all_of(kStopAnnouncements, "
               "insight::semantic::stop_announcement_row_well_formed),")
    out.append(f'              "{name}: a stop announcement is not a well-formed whole-line '
               'shape -- "')
    out.append('              "composition would refuse the package");')
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
        ("declared_values",
         "kDeclaredValues" if declaration.get("declared_values", {}).get("rows") else "{}"),
    ]
    # The member is named only where rows are declared, so a declaration with none emits the
    # bytes it emitted before the section existed and builds against a core without the member.
    if declaration.get("stop_announcements", {}).get("rows"):
        members.append(("stop_announcements", "kStopAnnouncements"))
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
    # The ownership fence, only where rows carry an owner: a one-revision package emits the
    # bytes schema v1 emitted (DN-162.D1 points 5 and 7).
    if len(declaration["revisions"]["names"]) > 1:
        out.append("static_assert(insight::semantic::all_row_revisions_declared(kManifest),")
        out.append(f'              "{name}: a row\'s revision_since is empty or names no '
                   'revision of kDialectRevisions -- "')
        out.append('              "in a multi-revision package every row names the revision that '
                   'introduced it");')
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


def _sinced(text: str, revision: str = "v1") -> str:
    """Every row of `text` declaring `since: <revision>` — the fixture's rows, not its names."""
    out: list[str] = []
    for line in text.split("\n"):
        out.append(line)
        if line.startswith(("      - prefix:", "      - token:", "      - key:", "      - shape:")):
            out.append(f"        since: {revision}")
    return "\n".join(out)


# The synthetic dialect's second generation (DN-162.D1, DN-162.D2): the v1 rows keep their
# bytes, their meaning and their order and declare `since: v1`; v2 adds one level lift and one
# marker, each on bytes no v1 row of its section can take. Its oracle is `_SYNTHETIC` itself,
# retained at `revisions/v1.dialect.yaml` — the live declaration v1 was.
_V2_ENTRY = ("      - name: v2\n        backward_compatible_with: v1\n"
             "        oracle: revisions/v1.dialect.yaml\n"
             "        why: [\"The second generation adds rows and changes none.\"]\n")
_V2_LIFT = ('      - prefix: "##[notice]"\n        level: Info\n        dialect_gate: self\n'
            "        since: v2\n")
_V2_MARKER = ('      - prefix: "Inner "\n        kind: Step\n        child_order: Ordered\n'
              "        dialect_gate: self\n        extract: RemainderAfterPrefix\n"
              "        channel_gate: marked\n        since: v2\n")
_V1_LIFTS = ('      - prefix: "##[error]"\n        since: v1\n        level: Error\n'
             "        dialect_gate: self\n"
             '      - prefix: "##[warning]"\n        since: v1\n        level: Warn\n'
             "        dialect_gate: self\n")
_V1_LIFTS_SWAPPED = ('      - prefix: "##[warning]"\n        since: v1\n        level: Warn\n'
                     "        dialect_gate: self\n"
                     '      - prefix: "##[error]"\n        since: v1\n        level: Error\n'
                     "        dialect_gate: self\n")
_TWO_REVISIONS = (_sinced(_SYNTHETIC)
                  .replace("    names: [v1]\n", "    names:\n      - v1\n" + _V2_ENTRY, 1)
                  .replace("  level_lifts:\n", _V2_MARKER + "  level_lifts:\n", 1)
                  .replace("  outcome_tokens:\n", _V2_LIFT + "  outcome_tokens:\n", 1))
_THREE_REVISIONS = (_TWO_REVISIONS
                    .replace(_V2_ENTRY, _V2_ENTRY
                             + "      - name: v3\n        backward_compatible_with: v2\n"
                               "        oracle: revisions/v2.dialect.yaml\n", 1)
                    .replace("  outcome_tokens:\n",
                             '      - prefix: "##[debug]"\n        level: Debug\n'
                             "        dialect_gate: self\n        since: v3\n"
                             "  outcome_tokens:\n", 1))


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


_V1_ORACLE = {"revisions/v1.dialect.yaml": _SYNTHETIC}

# An inner step's two lines (DN-162.D4): a start line that NAMES an `InnerStep` from keyed fields,
# and an end line that ENDS the innermost open one, carrying the ended unit's own declared outcome
# and the outcome its containing unit counted (DN-162.O3 item 9).
# What every malformed Ends row's refusal says (DN-162.O3 item 9), spelled once for the arms.
_ENDS_SHAPE_NEEDLE = ("an Ends row declares exactly `extract: KeyedFields` and "
                      "`fields: {outcome: …, propagated_outcome: …}`")
_INNER_START = ('      - prefix: "##[start-action "\n        kind: InnerStep\n'
                "        child_order: Ordered\n        dialect_gate: self\n"
                "        extract: KeyedFields\n        fields:\n          name: id\n"
                '          display: display\n          segment: "."\n          ordinal: "_"\n'
                '          anonymous: "__run"\n        channel_gate: marked\n'
                '        why: ["The id names the step; the display names an anonymous one."]\n')
_INNER_END = ('      - prefix: "##[end-action "\n        kind: InnerStep\n        role: Ends\n'
              "        dialect_gate: self\n        extract: KeyedFields\n        fields:\n"
              "          outcome: outcome\n          propagated_outcome: conclusion\n"
              "        channel_gate: marked\n")
_WITH_INNER = _SYNTHETIC.replace("  level_lifts:\n", _INNER_START + _INNER_END + "  level_lifts:\n",
                                 1)
_INNER_TWO = _sinced(_WITH_INNER).replace("    names: [v1]\n",
                                          "    names:\n      - v1\n" + _V2_ENTRY, 1)
_INNER_ORACLE = {"revisions/v1.dialect.yaml": _WITH_INNER}


def _package_path(directory: Path, live: str, oracles: dict[str, str] | None) -> Path:
    """A package directory holding the live declaration and its retained oracles."""
    for relative, text in (_V1_ORACLE if oracles is None else oracles).items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return _write_fixture(directory, live)


def _parse_package(live: str, oracles: dict[str, str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return load_declaration(_package_path(Path(tmp), live, oracles))


def _render_package(live: str, oracles: dict[str, str] | None = None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = _package_path(Path(tmp), live, oracles)
        return emit_inc(load_declaration(path), module_suffix="_gen", source_name=path.name)


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

    # ── every class of the core's RunOutcome is admitted, the sixth included (ADR-17.D5) ──
    # A row naming `Cancelled` is admitted AND reaches the emitted row as the core's
    # enumerator: the closed list is the only thing between a declaration and the enum.
    _case("outcome: a row naming Cancelled is admitted and emitted as the core enumerator",
          failures, lambda: _assert(
              "insight::RunOutcome::Cancelled" in render_fixture(text=_SYNTHETIC.replace(
                  "        outcome: Unknown", "        outcome: Cancelled")),
              "a Cancelled outcome row did not reach the emitted rows"))

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

    # ── every declared span reaches the emitted file (row presence) ──────────
    # A span whose rows are dropped still COMPILES: `std::array<Row, N>` with an empty
    # initializer value-initialises, so the package recognises nothing while every other
    # arm here stays green. Measured 2026-08-26 by mutation on this tool: with no arm,
    # dropping the rows of `kRoles`, of `kOutcomeTokens`, the channel symbol list, or the
    # revision list each exited 0. `kLevelLifts` (order arm) and `kMarkers`/`kEmitMarkers`
    # (derive arm) are already named above; these are the spans that had no witness.
    for _symbol, _expected in (("kRoles", ['"##[group]"', '"##[endgroup]"']),
                               ("kOutcomeTokens", ['"success"', '"skipped"'])):
        _case(f"presence: {_symbol} carries every declared row", failures,
              lambda symbol=_symbol, expected=_expected: _assert(
                  _row_prefixes(rendered, symbol) == expected,
                  f"{symbol} does not carry the declared rows — an empty span value-"
                  f"initialises and compiles, and the dialect then recognises nothing: "
                  f"{_row_prefixes(rendered, symbol)} != {expected}"))
    _case("presence: one channel symbol per declared channel, and kChannels lists them",
          failures, lambda: _assert(
              'export inline constexpr std::string_view kChannelPlain{"plain"};' in rendered
              and 'export inline constexpr std::string_view kChannelMarked{"marked"};'
              in rendered
              and "std::array<std::string_view, 2> kChannels{{kChannelPlain, "
                  "kChannelMarked}};" in rendered,
              "the channel vocabulary is what every `channel_gate` resolves against, so an "
              "empty kChannels makes every gated row unreachable while still compiling"))
    _case("presence: kDialectRevisions carries the declared revision", failures,
          lambda: _assert(
              'std::array<std::string_view, 1> kDialectRevisions{{"v1"}};' in rendered,
              "an empty revision list compiles and names no vendor generation, so the "
              "package claims to recognise a dialect it can no longer identify"))

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

    # ── the module-suffix fence (ADR-17.D10) ──────────────────────────────────
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
    _expect_rejection("refuse: `payload_excludes:` is not a marker row key", failures,
                      "unknown key",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        channel_gate: any\n",
                          "        channel_gate: any\n        payload_excludes: [x]\n", 1)))
    # ── the marker row's version coordinate ─────────────────────────────────────────────
    _with_coordinate = _SYNTHETIC.replace(
        "        channel_gate: plain\n",
        "        channel_gate: plain\n        version_coordinate:\n"
        "          introducer: \"@\"\n          shape: OneToken\n"
        "          why: [\"The bytes after the last introducer are the unit's version.\"]\n", 1)
    _case("version coordinate: a declared one reaches its recognition row and no other row",
          failures, lambda: _assert(
              render_fixture(text=_with_coordinate).count(
                  '.version = {.introducer = "@", .shape = VersionPayloadShape::OneToken}') == 1
              and "// > The bytes after the last introducer are the unit's version."
              in render_fixture(text=_with_coordinate),
              "the declared coordinate is not on exactly one emitted row with its argument"))
    _case("version coordinate: declaring none emits none and leaves the content hash alone",
          failures, lambda: _assert(
              ".introducer = " not in rendered
              and declaration_hash(_parse_fixture(_SYNTHETIC)) == base_hash
              and declaration_hash(_parse_fixture(_with_coordinate)) != base_hash,
              "an undeclared coordinate was emitted, or a declared one did not move the hash"))
    _expect_rejection("refuse: a version coordinate with a shape outside the closed set",
                      failures, "outside the closed core vocabulary",
                      lambda: _parse_fixture(_with_coordinate.replace(
                          "shape: OneToken", "shape: AnyShape")))
    _expect_rejection("refuse: a version coordinate with an empty introducer", failures,
                      "must be non-empty",
                      lambda: _parse_fixture(_with_coordinate.replace(
                          'introducer: "@"', 'introducer: ""')))
    _expect_rejection("refuse: a version coordinate with no shape", failures, "shape",
                      lambda: _parse_fixture(_with_coordinate.replace(
                          "          shape: OneToken\n", "")))
    _expect_rejection("refuse: a version coordinate that is not a mapping", failures,
                      "must be a mapping",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        channel_gate: plain\n",
                          "        channel_gate: plain\n        version_coordinate: \"@\"\n",
                          1)))
    # ── the marker row's role: a row that opens a unit without naming it ────────────────
    _opener = ('      - prefix: "Opened at "\n        kind: Job\n        role: Opens\n'
               '        dialect_gate: self\n        channel_gate: any\n'
               '        why: ["The line that opens a job before a later line names it."]\n')
    _with_opener = _SYNTHETIC.replace('      - prefix: "Job: "\n', _opener + '      - prefix: "Job: "\n', 1)
    _case("role: an opening row is emitted with its role on both projections, and no other row "
          "carries one", failures, lambda: _assert(
              render_fixture(text=_with_opener).count(".role = MarkerRole::Opens") == 2
              and render_fixture(text=_with_opener).count("MarkerRole::") == 2
              and "// > The line that opens a job before a later line names it."
              in render_fixture(text=_with_opener),
              "the opening row's role is not on exactly its recognition and its emit row"))
    _case("role: the opening row carries no payload — extract None, emit dual(None), no order",
          failures, lambda: _assert(
              '{.prefix = "Opened at ",\n     .kind = insight::tokenization::IntentMarkerKind::Job,'
              '\n     .dialect_gate = kDialect,\n     .extract = PayloadExtract::None,'
              in render_fixture(text=_with_opener)
              and '.emit = insight::semantic::dual(PayloadExtract::None),'
              in render_fixture(text=_with_opener),
              "the opening row was emitted with a payload field it may not carry"))
    _case("role: declaring none emits none and leaves the content hash alone", failures,
          lambda: _assert(
              "MarkerRole::" not in rendered
              and declaration_hash(_parse_fixture(_with_opener)) != base_hash,
              "an undeclared role was emitted, or a declared opener did not move the hash"))
    _expect_rejection("refuse: a role outside the closed set — `Names` is the key's absence",
                      failures, "outside the closed core vocabulary",
                      lambda: _parse_fixture(_with_opener.replace("role: Opens", "role: Names")))
    _expect_rejection("refuse: an opening row declaring an extractor", failures,
                      "`extract:` on a row declaring `role: Opens`",
                      lambda: _parse_fixture(_with_opener.replace(
                          "        role: Opens\n",
                          "        role: Opens\n        extract: RemainderAfterPrefix\n", 1)))
    _expect_rejection("refuse: an opening row declaring a child order", failures,
                      "`child_order:` on a row declaring `role: Opens`",
                      lambda: _parse_fixture(_with_opener.replace(
                          "        role: Opens\n",
                          "        role: Opens\n        child_order: Unordered\n", 1)))
    _expect_rejection("refuse: an opening row declaring a version coordinate", failures,
                      "`version_coordinate:` on a row declaring `role: Opens`",
                      lambda: _parse_fixture(_with_opener.replace(
                          "        role: Opens\n",
                          "        role: Opens\n        version_coordinate:\n"
                          "          introducer: \"@\"\n          shape: OneToken\n", 1)))
    _expect_rejection("refuse: an opening row of kind None", failures,
                      "`kind: None` on a row declaring `role: Opens`",
                      lambda: _parse_fixture(_with_opener.replace(
                          '"Opened at "\n        kind: Job', '"Opened at "\n        kind: None', 1)))
    _expect_rejection("refuse: an opening row whose kind no row of the section names", failures,
                      "could never take effect",
                      lambda: _parse_fixture(_with_opener.replace(
                          '"Opened at "\n        kind: Job', '"Opened at "\n        kind: Step', 1)
                          .replace('      - prefix: "Run "\n        kind: Step\n',
                                   '      - prefix: "Run "\n        kind: Job\n', 1)))
    # ── the closing role: a row that enters its job's supplied epilogue ──────────────────
    _closer = ('      - prefix: "Teardown."\n        kind: Step\n        role: Closes\n'
               '        dialect_gate: self\n        channel_gate: any\n'
               '        why: ["The runner\'s teardown line names no step."]\n')
    _with_closer = _SYNTHETIC.replace('      - prefix: "Job: "\n', _closer + '      - prefix: "Job: "\n', 1)
    _case("role: a closing row is emitted with its role on both projections, and no other row "
          "carries one", failures, lambda: _assert(
              render_fixture(text=_with_closer).count(".role = MarkerRole::Closes") == 2
              and render_fixture(text=_with_closer).count("MarkerRole::") == 2
              and '{.prefix = "Teardown.",\n     .kind = insight::tokenization::IntentMarkerKind::Step,'
              '\n     .dialect_gate = kDialect,\n     .extract = PayloadExtract::None,'
              in render_fixture(text=_with_closer)
              and declaration_hash(_parse_fixture(_with_closer)) != base_hash,
              "the closing row's role is not on exactly its recognition and its emit row, it "
              "carries a payload field, or it did not move the hash"))
    _expect_rejection("refuse: a closing row of kind Job — it enters a step-level unit", failures,
                      "`kind: Job` on a row declaring `role: Closes`",
                      lambda: _parse_fixture(_with_closer.replace(
                          '"Teardown."\n        kind: Step', '"Teardown."\n        kind: Job', 1)))
    _expect_rejection("refuse: a closing row of kind None", failures,
                      "`kind: None` on a row declaring `role: Closes`",
                      lambda: _parse_fixture(_with_closer.replace(
                          '"Teardown."\n        kind: Step', '"Teardown."\n        kind: None', 1)))
    _expect_rejection("refuse: a closing row declaring an extractor", failures,
                      "`extract:` on a row declaring `role: Closes`",
                      lambda: _parse_fixture(_with_closer.replace(
                          "        role: Closes\n",
                          "        role: Closes\n        extract: RemainderAfterPrefix\n", 1)))
    # ── an inner step: kind InnerStep, role Ends, extractor KeyedFields (DN-162.O3 item 7) ─
    def _inner() -> str:
        return render_fixture(text=_WITH_INNER)

    _start_fields = ('.fields = {.name = "id", .display = "display", .segment = ".", '
                     '.ordinal = "_", .anonymous = "__run"}')
    _case("inner: a KeyedFields naming row reaches both projections with its field roles",
          failures, lambda: _assert(
              _inner().count(_start_fields) == 2
              and _inner().count("IntentMarkerKind::InnerStep") == 4
              and '.extract = PayloadExtract::KeyedFields,' in _array_body(_inner(), "kMarkers")
              and ".emit = insight::semantic::dual(PayloadExtract::KeyedFields)," in _inner()
              and "// > The id names the step; the display names an anonymous one." in _inner(),
              "the start row's kind, extractor or field roles did not reach its recognition row "
              "and its emit row"))
    _ends_fields = '.fields = {.outcome = "outcome", .propagated_outcome = "conclusion"}'
    _case("inner: an Ends row reaches both projections with its role, extractor and two outcome "
          "fields", failures, lambda: _assert(
              _inner().count(".role = MarkerRole::Ends,") == 2
              and _inner().count(_ends_fields) == 2
              and _inner().count(".emit = insight::semantic::dual(PayloadExtract::KeyedFields),")
              == 2,
              "the Ends row is not on both projections with `MarkerRole::Ends` and both its outcome "
              "fields, `outcome` then `propagated_outcome`"))
    _case("inner: an Ends row's two outcome fields are emitted in the core's member order",
          failures, lambda: _assert(
              render_fixture(text=_WITH_INNER.replace(
                  "          outcome: outcome\n          propagated_outcome: conclusion\n",
                  "          propagated_outcome: conclusion\n          outcome: outcome\n",
                  1)).count(_ends_fields) == 2,
              "a designated initializer follows the struct's member order, so `outcome` precedes "
              "`propagated_outcome` whatever the declaration's order"))
    _case("inner: the field roles are emitted in the core's member order, whatever the YAML order",
          failures, lambda: _assert(
              render_fixture(text=_WITH_INNER.replace(
                  "          name: id\n          display: display\n",
                  "          display: display\n          name: id\n", 1)).count(_start_fields) == 2,
              "a designated initializer follows the struct's member order, so the field roles "
              "must be emitted in the closed role order and never in the declaration's"))
    _case("inner: the field roles are hashed content", failures, lambda: _assert(
        declaration_hash(_parse_fixture(_WITH_INNER)) != declaration_hash(_parse_fixture(
            _WITH_INNER.replace("          display: display\n", "          display: title\n", 1))),
        "a field-role edit did not move the content hash"))
    _case("inner: a declaration without them emits none of them", failures, lambda: _assert(
        "InnerStep" not in rendered and "KeyedFields" not in rendered
        and ".fields = " not in rendered and "MarkerRole::Ends" not in rendered,
        "the new kind, role, extractor or field roles reached a declaration that declares none"))
    _case("inner: in a two-revision package the field roles precede `.revision_since`", failures,
          lambda: _assert(
              _start_fields + ',\n     .revision_since = "v1"},'
              in _render_package(_INNER_TWO, _INNER_ORACLE),
              "`.revision_since` is the last member of the row struct (DN-162.O3 item 2), so every "
              "member item 7 adds is emitted before it"))
    _expect_rejection("mutation: an oracle row's field role changed is the MEANING drift", failures,
                      "changes its `fields:`",
                      lambda: _parse_package(_INNER_TWO.replace(
                          "          display: display\n", "          display: title\n", 1),
                          _INNER_ORACLE))
    for _label, _text, _needle in (
            ("`role: Ends` off kind InnerStep",
             _WITH_INNER.replace("        kind: InnerStep\n        role: Ends\n",
                                 "        kind: Step\n        role: Ends\n", 1),
             "`kind: Step` on a row declaring `role: Ends`"),
            ("`extract: KeyedFields` without `fields:`",
             _WITH_INNER.replace("        fields:\n          outcome: outcome\n"
                                 "          propagated_outcome: conclusion\n", "", 1),
             "`extract: KeyedFields` requires `fields:`"),
            ("`fields:` with another extractor",
             _WITH_INNER.replace("        extract: KeyedFields\n        fields:\n"
                                 "          name: id\n",
                                 "        extract: RemainderAfterPrefix\n        fields:\n"
                                 "          name: id\n", 1),
             "`fields:` with `extract: RemainderAfterPrefix`"),
            ("a field role outside the closed set",
             _WITH_INNER.replace("          ordinal: \"_\"\n", "          version: v\n", 1),
             "unknown key `version:`"),
            *((f"a KeyedFields naming row without `{_role}:`",
               _WITH_INNER.replace(_line, "", 1),
               f"declares exactly `name`, `display`, `segment`, `ordinal` and `anonymous` — "
               f"it lacks `{_role}`")
              for _role, _line in (("name", "          name: id\n"),
                                   ("display", "          display: display\n"),
                                   ("segment", '          segment: "."\n'),
                                   ("ordinal", '          ordinal: "_"\n'),
                                   ("anonymous", '          anonymous: "__run"\n'))),
            ("`outcome:` on a naming row",
             _WITH_INNER.replace("          name: id\n", "          name: id\n"
                                 "          outcome: outcome\n", 1),
             "declares exactly `name`, `display`, `segment`, `ordinal` and `anonymous` — "
             "it adds `outcome`"),
            ("`propagated_outcome:` on a naming row",
             _WITH_INNER.replace("          name: id\n", "          name: id\n"
                                 "          propagated_outcome: conclusion\n", 1),
             "declares exactly `name`, `display`, `segment`, `ordinal` and `anonymous` — "
             "it adds `propagated_outcome`"),
            ("an Ends row reading `outcome:` alone",
             _WITH_INNER.replace("          propagated_outcome: conclusion\n", "", 1),
             _ENDS_SHAPE_NEEDLE + " — it lacks `propagated_outcome`"),
            ("an Ends row reading `propagated_outcome:` alone",
             _WITH_INNER.replace("          outcome: outcome\n", "", 1),
             _ENDS_SHAPE_NEEDLE + " — it lacks `outcome`"),
            ("an Ends row reading a field other than its two",
             _WITH_INNER.replace("          outcome: outcome\n", "          name: id\n", 1),
             _ENDS_SHAPE_NEEDLE + " — it lacks `outcome`; it adds `name`"),
            ("an Ends row reading both outcome roles from one key",
             _WITH_INNER.replace("          propagated_outcome: conclusion\n",
                                 "          propagated_outcome: outcome\n", 1),
             "`outcome:` and `propagated_outcome:` both read key `outcome`"),
            ("an Ends row with no extractor",
             _WITH_INNER.replace(
                 "        extract: KeyedFields\n        fields:\n          outcome: outcome\n"
                 "          propagated_outcome: conclusion\n", "", 1),
             _ENDS_SHAPE_NEEDLE),
            ("a literal field role holding a `;`",
             _WITH_INNER.replace('          segment: "."\n', '          segment: ".;"\n', 1),
             "a literal role holds no `;`"),
            ("two field roles reading one key",
             _WITH_INNER.replace("          display: display\n", "          display: id\n", 1),
             "read key `id`"),
            ("a field key carrying a payload separator",
             _WITH_INNER.replace("          name: id\n", "          name: \"i;d\"\n", 1),
             "a payload separator"),
            ("an empty literal field role",
             _WITH_INNER.replace("          ordinal: \"_\"\n", "          ordinal: \"\"\n", 1),
             "must be non-empty"),
            ("an Ends row declaring a child order",
             _WITH_INNER.replace("        role: Ends\n", "        role: Ends\n"
                                 "        child_order: Ordered\n", 1),
             "`child_order:` on a row declaring `role: Ends`"),
            ("an Ends row with an extractor other than KeyedFields",
             _WITH_INNER.replace("        role: Ends\n        dialect_gate: self\n"
                                 "        extract: KeyedFields\n        fields:\n"
                                 "          outcome: outcome\n"
                                 "          propagated_outcome: conclusion\n",
                                 "        role: Ends\n        dialect_gate: self\n"
                                 "        extract: RemainderAfterPrefix\n", 1),
             _ENDS_SHAPE_NEEDLE),
            ("a KeyedFields row declaring a version coordinate",
             _WITH_INNER.replace('          anonymous: "__run"\n',
                                 '          anonymous: "__run"\n        version_coordinate:\n'
                                 '          introducer: "@"\n          shape: OneToken\n', 1),
             "`version_coordinate:` with `extract: KeyedFields`"),
            ("an Ends row whose kind no row of the section names",
             _WITH_INNER.replace(_INNER_START, "", 1), "could never take effect")):
        _expect_rejection(f"refuse inner: {_label}", failures, _needle,
                          lambda text=_text: _parse_fixture(text))

    # ── a declared value: a key a stream's acquirer supplies a value under ───────────────
    _with_value = _SYNTHETIC.replace(
        "  outcome_markers:\n",
        "  declared_values:\n    why: [\"The acquirer knows the number; the dialect knows the "
        "marker.\"]\n    rows:\n      - key: pull_request\n        markers: [\"PR-\", \"pull-\"]\n"
        "        dialect_gate: self\n        why: [\"Both spellings name the run's own request.\"]\n"
        "  outcome_markers:\n", 1)
    _case("declared value: a declared row reaches the manifest with its markers and argument",
          failures, lambda: _assert(
              'kDeclaredValueMarkers0{{"PR-", "pull-"}};' in render_fixture(text=_with_value)
              and '{.key = "pull_request",\n     .markers = kDeclaredValueMarkers0,\n'
                  '     .dialect_gate = kDialect},' in render_fixture(text=_with_value)
              and ".declared_values = kDeclaredValues," in render_fixture(text=_with_value)
              and "// > Both spellings name the run's own request."
              in render_fixture(text=_with_value),
              "the declared value did not reach kDeclaredValues and the manifest whole"))
    _case("declared value: declaring none emits none and leaves the content hash alone",
          failures, lambda: _assert(
              "kDeclaredValues" not in rendered and ".declared_values = {}," in rendered
              and declaration_hash(_parse_fixture(_SYNTHETIC)) == base_hash
              and declaration_hash(_parse_fixture(_with_value)) != base_hash,
              "an undeclared value was emitted, or a declared one did not move the hash"))
    _case("declared value: marker order is content and never sorted", failures,
          lambda: _assert(
              'kDeclaredValueMarkers0{{"pull-", "PR-"}};' in render_fixture(
                  text=_with_value.replace('[\"PR-\", \"pull-\"]', '[\"pull-\", \"PR-\"]')),
              "the markers were reordered on the way to the emitted array"))
    _expect_rejection("refuse: a declared-value marker ending in a digit", failures,
                      "ends in a digit",
                      lambda: _parse_fixture(_with_value.replace('\"PR-\"', '\"PR2\"')))
    _expect_rejection("refuse: a declared value with no marker", failures,
                      "must be a non-empty sequence",
                      lambda: _parse_fixture(_with_value.replace(
                          'markers: [\"PR-\", \"pull-\"]', "markers: []")))
    _expect_rejection("refuse: a declared-value key outside the caller's spelling", failures,
                      "must match [a-z][a-z0-9_]*",
                      lambda: _parse_fixture(_with_value.replace(
                          "key: pull_request", "key: PullRequest")))
    _expect_rejection("refuse: a duplicate declared-value marker", failures,
                      "duplicate marker",
                      lambda: _parse_fixture(_with_value.replace('\"pull-\"', '\"PR-\"')))
    _expect_rejection("refuse: two declared values under one key", failures,
                      "duplicates rows[0]",
                      lambda: _parse_fixture(_with_value.replace(
                          "        why: [\"Both spellings name the run's own request.\"]\n",
                          "        why: [\"Both spellings name the run's own request.\"]\n"
                          "      - key: pull_request\n        markers: [\"MR-\"]\n"
                          "        dialect_gate: self\n", 1)))
    # ── a stop announcement: a whole line a runner writes into a step it stops ───────────
    _stop_row = ('      - shape: "##[error]The operation was canceled."\n'
                 '        dialect_gate: self\n'
                 '        why: ["The runner writes it into the step it stops."]\n')
    _with_stop = _SYNTHETIC.replace(
        "  outcome_markers:\n",
        "  stop_announcements:\n    why: [\"Stops order a unit's errors; they keep their role.\"]\n"
        "    rows:\n" + _stop_row + "  outcome_markers:\n", 1)
    _case("stop: a declared row reaches kStopAnnouncements and the manifest, with its argument",
          failures, lambda: _assert(
              '    {.shape = "##[error]The operation was canceled.",\n'
              "     .dialect_gate = kDialect},"
              in render_fixture(text=_with_stop)
              and ".stop_announcements = kStopAnnouncements," in render_fixture(text=_with_stop)
              and "stop_announcement_row_well_formed" in render_fixture(text=_with_stop)
              and "// > The runner writes it into the step it stops."
              in render_fixture(text=_with_stop),
              "the stop announcement did not reach kStopAnnouncements and the manifest whole"))
    _case("stop: declaring none emits no member and leaves the content hash alone", failures,
          lambda: _assert(
              "stop_announcements" not in rendered and "StopAnnouncement" not in rendered
              and declaration_hash(_parse_fixture(_SYNTHETIC)) == base_hash
              and declaration_hash(_parse_fixture(_with_stop)) != base_hash,
              "an undeclared section reached the output, or a declared one did not move the "
              "hash"))
    _case("stop: a shape with a hole is admitted, and one without is too", failures,
          lambda: _assert(
              '{.shape = "##[error]Step {n} canceled.",' in render_fixture(
                  text=_with_stop.replace("The operation was canceled.", "Step {n} canceled.",
                                          1)),
              "a stop shape carrying a hole was not emitted"))
    _case("stop: an empty section with its argument is a declared absence, no member", failures,
          lambda: _assert(
              "DECLARED ABSENCE -- the manifest's `.stop_announcements` below is empty"
              in render_fixture(text=_with_stop.replace("    rows:\n" + _stop_row,
                                                        "    rows: []\n", 1))
              and ".stop_announcements =" not in render_fixture(
                  text=_with_stop.replace("    rows:\n" + _stop_row, "    rows: []\n", 1)),
              "an empty stop section did not read as a declared absence"))
    _expect_rejection("refuse: a stop announcement declaring a role", failures,
                      "unknown key `role:`",
                      lambda: _parse_fixture(_with_stop.replace(
                          "        dialect_gate: self\n        why: [\"The runner",
                          "        role: Terminator\n        dialect_gate: self\n"
                          "        why: [\"The runner", 1)))
    _expect_rejection("refuse: a stop announcement by prefix", failures,
                      "unknown key `prefix:`",
                      lambda: _parse_fixture(_with_stop.replace(
                          '      - shape: "##[error]The operation was canceled."\n',
                          '      - prefix: "##[error]The operation"\n', 1)))
    _expect_rejection("refuse: a stop shape ending in whitespace", failures,
                      "trailing whitespace",
                      lambda: _parse_fixture(_with_stop.replace(
                          "The operation was canceled.\"", "The operation was canceled. \"",
                          1)))
    _expect_rejection("refuse: a stop shape with a stray brace", failures, "spelled `{n}`",
                      lambda: _parse_fixture(_with_stop.replace(
                          "The operation was canceled.", "The {operation} was canceled.", 1)))
    _expect_rejection("refuse: two stop rows on one shape and gate", failures,
                      "could never fire",
                      lambda: _parse_fixture(_with_stop.replace(
                          _stop_row, _stop_row + _stop_row.replace(
                              '        why: ["The runner writes it into the step it stops."]\n',
                              ""), 1)))
    # ── a role row matching a whole-line SHAPE (ADR-17.D14) ─────────────────────────────
    _shape = "Received {n} of {n} ({n}%), {n} MBs/sec"
    _shape_row = (f'      - shape: "{_shape}"\n        role: Progress\n'
                  '        dialect_gate: self\n'
                  '        why: ["A transfer gauge sampled on a timer, never an event."]\n')
    _with_shape = _SYNTHETIC.replace("  markers:\n", _shape_row + "  markers:\n", 1)
    _case("shape: a declared shape row reaches kRoles byte-exact, with the Shape match kind "
          "and its argument", failures, lambda: _assert(
              f'    {{.prefix = "{_shape}",\n'
              "     .role = insight::StructuralRole::Progress,\n"
              "     .dialect_gate = kDialect,\n"
              "     .match = RoleMatchKind::Shape},"
              in render_fixture(text=_with_shape)
              and "// > A transfer gauge sampled on a timer, never an event."
              in render_fixture(text=_with_shape)
              and _row_prefixes(render_fixture(text=_with_shape), "kRoles")
              == ['"##[group]"', '"##[endgroup]"', f'"{_shape}"'],
              "the shape row is not the third kRoles row with its bytes, its role and "
              "RoleMatchKind::Shape"))
    _case("shape: a prefix row carries no match kind, and declaring no shape leaves the "
          "content hash alone", failures, lambda: _assert(
              "RoleMatchKind" not in rendered
              and render_fixture(text=_with_shape).count("RoleMatchKind::") == 1
              and declaration_hash(_parse_fixture(_SYNTHETIC)) == base_hash
              and declaration_hash(_parse_fixture(_with_shape)) != base_hash,
              "a prefix row was emitted with a match kind (its bytes would move), or a "
              "declared shape did not move the hash"))
    _case("shape: the shape and the prefix spelling of the same bytes are distinct content",
          failures, lambda: _assert(
              declaration_hash(_parse_fixture(_with_shape)) != declaration_hash(
                  _parse_fixture(_with_shape.replace(
                      f'      - shape: "{_shape}"\n        role: Progress\n',
                      f'      - prefix: "{_shape}"\n        role: GroupBegin\n', 1))),
              "a shape row and a prefix row over the same bytes hash alike — the match kind "
              "is recognition content and must enter the hash"))
    for _edge_shape in ("Progress: resolved {n}, reused {n}, downloaded {n}, added {n}",
                        "{n} files left"):
        _case(f"shape: a hole may open or close the shape ({_edge_shape!r})", failures,
              lambda edge=_edge_shape: _assert(
                  f'{{.prefix = "{edge}",' in render_fixture(text=_with_shape.replace(
                      f'shape: "{_shape}"', f'shape: "{edge}"', 1)),
                  "a shape opening or closing on a hole was not emitted"))
    _expect_rejection("refuse: `role: Progress` on a prefix row", failures,
                      "declared by its exact shape",
                      lambda: _parse_fixture(_with_shape.replace(
                          f'      - shape: "{_shape}"\n', '      - prefix: "Received "\n', 1)))
    _expect_rejection("refuse: a role row declaring both `prefix:` and `shape:`", failures,
                      "exactly one of `prefix:` and `shape:`",
                      lambda: _parse_fixture(_with_shape.replace(
                          f'      - shape: "{_shape}"\n',
                          f'      - shape: "{_shape}"\n        prefix: "Received "\n', 1)))
    _expect_rejection("refuse: a role row declaring neither `prefix:` nor `shape:`", failures,
                      "exactly one of `prefix:` and `shape:`",
                      lambda: _parse_fixture(_with_shape.replace(
                          f'      - shape: "{_shape}"\n        role: Progress\n',
                          "      - role: Progress\n", 1)))
    for _label, _bad, _needle in (
            ("with no hole", "Received bytes", "at least one hole"),
            ("with a hole spelled other than {n}", "Received {x} of {n}", "spelled `{n}`"),
            ("with a stray brace", "Received {n} of {n} }", "spelled `{n}`"),
            ("with two adjacent holes", "Received {n}{n} bytes", "a digit or another hole"),
            ("with a digit against a hole", "Received {n}0 bytes", "a digit or another hole"),
            ("with a digit before a hole", "Received 1{n} bytes", "a digit or another hole"),
            ("with a hole followed by a decimal point and a digit", "Received {n}.5 bytes",
             "decimal point"),
            ("with a hole followed by a decimal point and a hole", "Received {n}.{n} bytes",
             "decimal point"),
            ("with a decimal point and a digit before a hole", "Received 1.{n} bytes",
             "decimal point"),
            ("ending in whitespace", "Received {n} bytes ", "trailing whitespace"),
            ("empty", "", "must be non-empty")):
        _expect_rejection(f"refuse: a shape {_label}", failures, _needle,
                          lambda bad=_bad: _parse_fixture(_with_shape.replace(
                              f'shape: "{_shape}"', f'shape: "{bad}"', 1)))
    _expect_rejection("refuse: a shape carrying a non-ASCII byte", failures,
                      "non-ASCII or control byte",
                      lambda: _parse_fixture(_with_shape.replace(
                          "MBs/sec", "MB s/sec", 1)))
    _expect_rejection("refuse: two shape rows on one shape and gate", failures,
                      "could never fire",
                      lambda: _parse_fixture(_with_shape.replace(
                          _shape_row, _shape_row + _shape_row.replace(
                              '        why: ["A transfer gauge sampled on a timer, never an '
                              'event."]\n', ""), 1)))
    _case("shape: a prefix row and a shape row over the same bytes are not duplicates",
          failures, lambda: _assert(
              _parse_fixture(_with_shape.replace(
                  _shape_row, _shape_row + f'      - prefix: "{_shape}"\n'
                  "        role: GroupBegin\n        dialect_gate: self\n", 1)) is not None,
              "unreachable"))
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
    _expect_rejection("refuse: a run outcome outside the closed core vocabulary", failures,
                      "outside the closed core vocabulary",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          "        outcome: Unknown", "        outcome: Timeout")))
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

    # ── schema v2: several ORDERED revisions, each row's `since:` (DN-162.D1) ───────────
    # Rendered inside each arm, so a refusal is that arm's failure rather than the run's.
    def _two() -> str:
        return _render_package(_TWO_REVISIONS)

    _case("v2: a two-revision span generates, every row carrying the revision that introduced it",
          failures, lambda: _assert(
              'std::array<std::string_view, 2> kDialectRevisions{{"v1", "v2"}};' in _two()
              and _two().count('.revision_since = "v1"') == 10
              and _two().count('.revision_since = "v2"') == 3
              and _row_prefixes(_two(), "kMarkers")
              == ['"Job: "', '"Run "', '"Inner "']
              and _row_prefixes(_two(), "kLevelLifts")
              == ['"##[error]"', '"##[warning]"', '"##[notice]"'],
              "the span, or a row's `.revision_since`, did not reach the emitted rows (want 10 "
              "rows naming v1: 2 roles, 2 markers, 2 emit rows, 2 lifts, 2 tokens; and 3 naming "
              "v2: a lift, a marker, its emit row): "
              + repr([line.strip() for line in _two().split("\n")
                      if ".revision_since = " in line])))
    _case("v2: the derived emit row copies its marker row's revision (DN-162.D1 point 6)",
          failures, lambda: _assert(
              '.revision_since = "v2"' in _array_body(_two(), "kEmitMarkers")
              and _array_body(_two(), "kEmitMarkers").count(".revision_since = ") == 3,
              "an emit row does not carry its marker row's `revision_since` — the writer "
              "projection is derived, so it copies the owner rather than declaring one"))
    _case("v2: the ownership fence is emitted, and only for more than one revision", failures,
          lambda: _assert(
              _two().count("insight::semantic::all_row_revisions_declared(kManifest)")
              == 1
              and "all_row_revisions_declared" not in rendered
              and "revision_since" not in rendered,
              "the fence or a `.revision_since` member is missing from the two-revision package, "
              "or reached a one-revision package, whose bytes must be the ones schema v1 emitted"))
    _case("v2: the header names declaration schema 2", failures, lambda: _assert(
        "declaration schema: 2" in rendered and "declaration schema: 2" in _two(),
        "the generated header does not name schema 2"))
    _case("v2: the compatibility claim is declaration content and reaches no emitted code",
          failures, lambda: _assert(
              "backward_compatible_with" in repr(hashed_content(_parse_package(_TWO_REVISIONS)))
              and not any("backward_compatible_with" in line or "revisions/v1" in line
                          for line in _two().split("\n")
                          if not line.lstrip().startswith("//")),
              "the claim is missing from the declaration's content, or reached an emitted C++ "
              "line, which the package's semantic_identity would then see (DN-162.D2 Q4)"))
    _case("v2: a new row on a channel the oracle row does not share does not shadow it", failures,
          lambda: _assert(
              _parse_package(_TWO_REVISIONS.replace(
                  _V2_MARKER, _V2_MARKER.replace('"Inner "', '"Run"'), 1)) is not None,
              "unreachable"))
    _case("v2: a three-revision chain checks each step against its own oracle", failures,
          lambda: _assert(
              _render_package(_THREE_REVISIONS, {
                  "revisions/v1.dialect.yaml": _SYNTHETIC,
                  "revisions/v2.dialect.yaml": _TWO_REVISIONS}).count(
                  '.revision_since = "v3"') == 1,
              "the v3 row did not reach the emitted rows"))
    # Refusals of the span and the per-row revision, each by its message.
    for _label, _text, _needle in (
            ("a row without `since:` in a multi-revision declaration",
             _TWO_REVISIONS.replace("        since: v1\n", "", 1), "declares no `since:`"),
            ("`since:` naming a revision the span does not declare",
             _TWO_REVISIONS.replace("        since: v1\n", "        since: v9\n", 1),
             "names no revision the span declares"),
            ("`since:` on a singleton span", _sinced(_SYNTHETIC), "`since:` on a singleton span"),
            ("a compatibility claim on the first revision",
             _TWO_REVISIONS.replace("      - v1\n", "      - name: v1\n"
                                    "        backward_compatible_with: v0\n"
                                    "        oracle: revisions/v0.dialect.yaml\n", 1),
             "nothing precedes it"),
            ("a second revision with no claim",
             _TWO_REVISIONS.replace(_V2_ENTRY, "      - v2\n", 1),
             "requires `backward_compatible_with:`"),
            ("a claim with no oracle",
             _TWO_REVISIONS.replace("        oracle: revisions/v1.dialect.yaml\n", "", 1),
             "requires `oracle:`"),
            ("a claim naming a revision other than the immediate predecessor",
             _THREE_REVISIONS.replace("backward_compatible_with: v2",
                                      "backward_compatible_with: v1", 1),
             "the IMMEDIATELY preceding revision"),
            ("a claim of `false`",
             _TWO_REVISIONS.replace("backward_compatible_with: v1",
                                    "backward_compatible_with: false", 1),
             "`false` is not spellable"),
            ("an oracle outside `revisions/<predecessor>.dialect.yaml`",
             _TWO_REVISIONS.replace("oracle: revisions/v1.dialect.yaml", "oracle: v1.yaml", 1),
             "the oracle of `v2` is `revisions/v1.dialect.yaml`"),
            ("a claim on a channel name",
             _TWO_REVISIONS.replace("      - name: marked\n",
                                    "      - name: marked\n"
                                    "        backward_compatible_with: plain\n", 1),
             "legal on `revisions:` alone")):
        _expect_rejection(f"refuse v2: {_label}", failures, _needle,
                          lambda text=_text: _parse_package(text))
    _expect_rejection("refuse v2: an oracle path naming no file", failures,
                      "names no file", lambda: _parse_package(_TWO_REVISIONS, {}))
    _expect_rejection("refuse v2: an oracle declaring another name", failures,
                      "the oracle declares `name: other`",
                      lambda: _parse_package(_TWO_REVISIONS, {
                          "revisions/v1.dialect.yaml":
                          _SYNTHETIC.replace("  name: synthetic", "  name: other", 1)}))
    _expect_rejection("refuse v2: an oracle whose span is not the live span's prefix", failures,
                      "the oracle's span is [v0]",
                      lambda: _parse_package(_TWO_REVISIONS, {
                          "revisions/v1.dialect.yaml":
                          _SYNTHETIC.replace("    names: [v1]", "    names: [v0]", 1)}))
    # The mutation arms (DN-162.O2 arm 3): each drift of the live file from its oracle is
    # refused, and the refusal NAMES the oracle row and the forcing event it is.
    _oracle = "oracle revisions/v1.dialect.yaml section"
    for _label, _text, _needle in (
            ("an oracle row's role changed",
             _TWO_REVISIONS.replace("        role: GroupEnd\n", "        role: Terminator\n", 1),
             f"{_oracle} `roles:` rows[1]"),
            ("an oracle row's level changed",
             _TWO_REVISIONS.replace("        level: Warn\n", "        level: Error\n", 1),
             f"{_oracle} `level_lifts:` rows[1]"),
            ("an oracle row's extractor changed",
             _TWO_REVISIONS.replace("        extract: RemainderAfterPrefix\n",
                                    "        extract: RemainderToClosingParen\n", 1),
             f"{_oracle} `markers:` rows[0]"),
            ("an oracle row's channel gate changed",
             _TWO_REVISIONS.replace("        channel_gate: plain\n", "        channel_gate: any\n",
                                    1), f"{_oracle} `markers:` rows[1]"),
            ("an oracle row deleted",
             _TWO_REVISIONS.replace("      - token: skipped\n        since: v1\n"
                                    "        outcome: Unknown\n        dialect_gate: self\n",
                                    "", 1),
             f"{_oracle} `outcome_tokens:` rows[1]"),
            ("two oracle rows of a first-match section swapped",
             _TWO_REVISIONS.replace(_V1_LIFTS, _V1_LIFTS_SWAPPED, 1),
             f"{_oracle} `level_lifts:` rows[1]"),
            ("a new row whose prefix is a prefix of an oracle row's",
             _TWO_REVISIONS.replace('"##[notice]"', '"##[warn"', 1),
             f"{_oracle} `level_lifts:` rows[1]"),
            ("a new row whose prefix extends an oracle row's",
             _TWO_REVISIONS.replace('"##[notice]"', '"##[error]x"', 1),
             f"{_oracle} `level_lifts:` rows[0]"),
            ("a new marker shadowing an oracle marker on a common channel",
             _TWO_REVISIONS.replace(_V2_MARKER, _V2_MARKER.replace('"Inner "', '"Run"', 1)
                                    .replace("channel_gate: marked", "channel_gate: any", 1), 1),
             f"{_oracle} `markers:` rows[1]"),
            ("an oracle row marked `since: v2`",
             _TWO_REVISIONS.replace('"##[error]"\n        since: v1\n', '"##[error]"\n'
                                    "        since: v2\n", 1),
             f"{_oracle} `level_lifts:` rows[0]")):
        _expect_rejection(f"mutation: {_label}", failures, _needle,
                          lambda text=_text: _parse_package(text))
        _expect_rejection(f"mutation: {_label} — the refusal names the forcing event", failures,
                          "ADR-22.D8", lambda text=_text: _parse_package(text))
    # A changed row is refused as the MEANING drift, naming the field, and not as a deleted row
    # beside an unrelated new one: the bytes are the same, the reading is not.
    for _label, _text, _field in (
            ("role", _TWO_REVISIONS.replace("        role: GroupEnd\n",
                                            "        role: Terminator\n", 1), "`role:`"),
            ("level", _TWO_REVISIONS.replace("        level: Warn\n", "        level: Error\n", 1),
             "`level:`"),
            ("extractor", _TWO_REVISIONS.replace("        extract: RemainderAfterPrefix\n",
                                                 "        extract: RemainderToClosingParen\n", 1),
             "`extract:`"),
            ("channel gate", _TWO_REVISIONS.replace("        channel_gate: plain\n",
                                                    "        channel_gate: any\n", 1),
             "`channel_gate:`")):
        _expect_rejection(f"mutation: an oracle row's {_label} changed is the MEANING drift",
                          failures, f"changes its {_field} — the same bytes called something else",
                          lambda text=_text: _parse_package(text))
    _expect_rejection("refuse v2: a row claiming `since: v1` that the v1 oracle does not hold",
                      failures, "the retained `v1` declaration holds no equal row",
                      lambda: _parse_package(_TWO_REVISIONS.replace(
                          _V2_LIFT, _V2_LIFT.replace("since: v2", "since: v1", 1), 1)))

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
    # The C0 block and DEL, one witness per refused class: the named ones the table
    # spells out, and one codepoint it does NOT name, because the fence is the RANGE.
    for _label, _char, _needle in (("NUL", "\x00", "U+0000 NUL"),
                                   ("BEL", "\x07", "U+0007 BEL"),
                                   ("FF", "\x0c", "U+000C FF"),
                                   ("DEL", "\x7f", "U+007F DEL"),
                                   ("an unnamed C0 code", "\x01", "U+0001")):
        _expect_rejection(f"encoding: {_label} inside a `why:` scalar", failures, _needle,
                          lambda c=_char: _parse_fixture(_SYNTHETIC.replace(
                              "A byte predicate, not a grammar.",
                              f"A byte predicate{c}, not a grammar.")))
    # FF and VT are Python whitespace, so a scalar made only of one would reach the
    # paragraph-break branch and be silently read as a blank line. The control scan runs
    # BEFORE that branch precisely so this is a refusal and not a shrug.
    _expect_rejection("encoding: a `why:` scalar that is ONLY a control character",
                      failures, "U+000C FF",
                      lambda: _parse_fixture(_SYNTHETIC.replace(
                          '      why: ["A byte predicate, not a grammar."]',
                          '      why: ["A byte predicate.", "\x0c", "Nothing more."]')))
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


def _array_body(rendered: str, array_symbol: str) -> str:
    """The emitted initializer text of ONE array, from its symbol to its closing brace."""
    body = rendered[rendered.index(f"{array_symbol}{{{{"):]
    return body[: body.index("\n}};")]


def _row_prefixes(rendered: str, array_symbol: str) -> list[str]:
    """The `.prefix` (or `.token`) initializers of ONE emitted array, in emitted order."""
    body = _array_body(rendered, array_symbol)
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
