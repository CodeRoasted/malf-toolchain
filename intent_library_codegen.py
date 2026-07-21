#!/usr/bin/env python3
"""intent_library_codegen.py — C1 build-time codegen for the LogCraft Intent library.

Generates the library's C++ entries (constexpr data + derived index) from declaration
files, resolving each entry's ratification state against the twin-generated manifest.
Design: technical_docs/architecture/logcraft_intent_library.md (§2 schema, §4 fence,
§5 codegen, §6 organization) in the coderoast workspace.

THE BOUNDARY (Topic H hard rule): this TOOL is public; the intent DECLARATIONS it
consumes and the C++ it EMITS stay in the private consumer repo. Everything in this
file — including every --selftest fixture — is SYNTHETIC by construction. Never add a
real library entry here: tools and their fixtures migrate together, and a real
declaration beside the tool walks the moat into the public repo.

THE FOUR TEETH this tool carries (the soundness fence, §4):
  1. The ratification state is REQUIRED in the output — every generated entry carries
     one, resolved here; there is no way to emit an entry without a state.
  2. `Ratified` is NOT hand-writable — the declaration grammar has NO ratification
     key (rejected below, by name, with the fence cited). Ratified records exist only
     in the twin-generated manifest.
  3. Ratification is keyed on the declaration's CONTENT HASH — resolution is a
     comparison (manifest hash == canonical hash of the current declaration), so an
     edit revokes it and a reformat does not.
  4. The showcase surface is fail-closed — the generated ratified index contains
     ONLY hash-matched entries; Authored entries are not in it, by construction.

DETERMINISM MUSTs (§5.3): output is byte-identical across machines, runs and
toolchains. Concretely: entries and every emitted list iterate in sorted or declared
order (never hash order); scalars are STRINGS end-to-end (this parser types nothing,
so no locale- or stdlib-dependent text<->number conversion exists to diverge); input
and output are ASCII-only, LF-only; the tool version enters the generated header but
NOT the declaration hash (MUST 4: a tool upgrade must not mass-revoke the library);
the hash covers canonicalized semantic content, not file bytes (MUST 3: a reformat
must not revoke ratification).

Grammar note: declaration files use a deliberate STRICT SUBSET of YAML — block maps,
block sequences, one-line flow sequences of scalars, quoted/plain scalars, comments.
No anchors, tags, flow maps, multiline scalars, tabs, duplicate keys, non-ASCII.
The subset keeps parsing bit-stable with zero dependencies on every build host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TOOL_VERSION = "1"

INTENT_FILE_SUFFIX = ".intent.yaml"

# ── error reporting ──────────────────────────────────────────────────────────


class DeclarationError(Exception):
    """A declaration, manifest, or grammar violation. Message carries source + line."""


def fail(source: str, line: int | None, message: str) -> None:
    where = f"{source}:{line}" if line is not None else source
    raise DeclarationError(f"{where}: {message}")


# ── the strict YAML-subset parser (string-only scalars) ──────────────────────
#
# Parsed shape: dict[str, node] | list[node] | str.  All scalars are strings —
# `domain: [0, 1]` yields ["0", "1"]. The library schema has no numeric-typed
# declaration value (weights and ranges are BINDINGS, not declarations), and
# string-only scalars remove the entire number-canonicalization hazard class.

_PLAIN_SCALAR = re.compile(r"[A-Za-z0-9_./:>=<+-][A-Za-z0-9_./:>=<+\- ()%]*")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _reject_non_ascii(text: str, source: str) -> None:
    for line_index, line in enumerate(text.split("\n"), start=1):
        for ch in line:
            if ch == "\t":
                continue  # rejected by the indentation pass, with the tab-specific message
            if not (0x20 <= ord(ch) <= 0x7E):
                fail(source, line_index,
                     f"non-ASCII or control byte 0x{ord(ch):02x} — declarations are "
                     "ASCII-only (determinism MUST: no encoding may vary by host)")


def _strip_comment(line: str, source: str, line_no: int) -> str:
    """Remove a trailing comment. Respects quoted spans; rejects stray quotes."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        ch = line[index]
        if quote is not None:
            if ch == "\\" and quote == '"':
                if index + 1 >= len(line):
                    fail(source, line_no, "dangling escape at end of line")
                out.append(ch)
                out.append(line[index + 1])
                index += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
            index += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            index += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        index += 1
    if quote is not None:
        fail(source, line_no, "unterminated quoted scalar")
    return "".join(out).rstrip()


def _parse_scalar(token: str, source: str, line_no: int) -> str:
    token = token.strip()
    if token.startswith('"'):
        if not token.endswith('"') or len(token) < 2:
            fail(source, line_no, f"malformed double-quoted scalar: {token!r}")
        body = token[1:-1]
        result: list[str] = []
        index = 0
        while index < len(body):
            ch = body[index]
            if ch == "\\":
                if index + 1 >= len(body):
                    fail(source, line_no, "dangling escape in quoted scalar")
                escaped = body[index + 1]
                if escaped not in ('"', "\\"):
                    fail(source, line_no,
                         f"unsupported escape \\{escaped} — the subset allows only "
                         '\\" and \\\\ (declarations are single-line printable ASCII)')
                result.append(escaped)
                index += 2
                continue
            if ch == '"':
                fail(source, line_no, f"unescaped quote inside scalar: {token!r}")
            result.append(ch)
            index += 1
        return "".join(result)
    if token.startswith("'"):
        if not token.endswith("'") or len(token) < 2:
            fail(source, line_no, f"malformed single-quoted scalar: {token!r}")
        body = token[1:-1]
        if "'" in body:
            fail(source, line_no, "single-quoted scalar may not contain a quote")
        return body
    if token.startswith("[") or token.startswith("{"):
        fail(source, line_no,
             f"flow collection {token!r} is only legal as a full value of `key: [a, b]` form")
    for forbidden, why in (("&", "anchor"), ("*", "alias"), ("!", "tag"),
                           ("|", "block scalar"), (">", "folded scalar")):
        if token.startswith(forbidden):
            fail(source, line_no,
                 f"YAML {why} {token!r} — outside the declaration subset (keep "
                 "declarations literal: they are hashed content, not templated text)")
    if not token or not _PLAIN_SCALAR.fullmatch(token):
        fail(source, line_no,
             f"plain scalar {token!r} outside the subset — quote it if it is literal text")
    return token


def _parse_flow_sequence(token: str, source: str, line_no: int) -> list[str]:
    body = token.strip()
    assert body.startswith("[")
    if not body.endswith("]"):
        fail(source, line_no, "flow sequence must open and close on one line")
    inner = body[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    depth_guard = inner.split(",")
    for raw in depth_guard:
        if "[" in raw or "]" in raw or "{" in raw or "}" in raw:
            fail(source, line_no, "nested flow collections are outside the subset")
        items.append(_parse_scalar(raw, source, line_no))
    return items


class _Line:
    __slots__ = ("indent", "text", "number")

    def __init__(self, indent: int, text: str, number: int):
        self.indent = indent
        self.text = text
        self.number = number


def _logical_lines(text: str, source: str) -> list[_Line]:
    lines: list[_Line] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        if "\t" in raw:
            fail(source, number, "tab character — indentation is spaces-only in the subset")
        stripped = _strip_comment(raw, source, number)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append(_Line(indent, stripped.strip(), number))
    return lines


def _parse_block(lines: list[_Line], pos: int, indent: int, source: str):
    """Parse the block starting at lines[pos] with exactly `indent`. Returns (node, next_pos)."""
    if pos >= len(lines) or lines[pos].indent != indent:
        fail(source, lines[pos - 1].number if pos > 0 else None, "empty block")
    if lines[pos].text.startswith("- "):
        return _parse_sequence(lines, pos, indent, source)
    return _parse_mapping(lines, pos, indent, source)


def _parse_mapping(lines: list[_Line], pos: int, indent: int, source: str):
    mapping: dict[str, object] = {}
    while pos < len(lines) and lines[pos].indent == indent and not lines[pos].text.startswith("- "):
        line = lines[pos]
        if ":" not in line.text:
            fail(source, line.number, f"expected `key:` or `key: value`, got {line.text!r}")
        key, _, rest = line.text.partition(":")
        key = key.strip()
        if not _KEY.fullmatch(key):
            fail(source, line.number, f"key {key!r} outside the subset ([A-Za-z_][A-Za-z0-9_]*)")
        if key in mapping:
            fail(source, line.number, f"duplicate key {key!r} (K1: keys are unique)")
        rest = rest.strip()
        if rest:
            if rest.startswith("["):
                mapping[key] = _parse_flow_sequence(rest, source, line.number)
            else:
                mapping[key] = _parse_scalar(rest, source, line.number)
            pos += 1
            continue
        # nested block
        pos += 1
        if pos >= len(lines) or lines[pos].indent <= indent:
            fail(source, line.number, f"key {key!r}: has no value and no nested block")
        mapping[key], pos = _parse_block(lines, pos, lines[pos].indent, source)
    if pos < len(lines) and lines[pos].indent == indent and lines[pos].text.startswith("- "):
        fail(source, lines[pos].number, "sequence item at mapping level — mixed block kinds")
    return mapping, pos


def _parse_sequence(lines: list[_Line], pos: int, indent: int, source: str):
    sequence: list[object] = []
    while pos < len(lines) and lines[pos].indent == indent and lines[pos].text.startswith("- "):
        line = lines[pos]
        head = line.text[2:].strip()
        if not head:
            fail(source, line.number, "bare `-` item — the subset requires inline item content")
        if ":" in head and _KEY.fullmatch(head.partition(":")[0].strip()):
            # `- key: value` opens an inline mapping item; continuation keys sit at indent+2.
            item_indent = indent + 2
            synthetic = [_Line(item_indent, head, line.number)]
            probe = pos + 1
            while probe < len(lines) and lines[probe].indent >= item_indent and \
                    not (lines[probe].indent == indent and lines[probe].text.startswith("- ")):
                synthetic.append(lines[probe])
                probe += 1
            item, consumed = _parse_mapping(synthetic, 0, item_indent, source)
            if consumed != len(synthetic):
                fail(source, synthetic[consumed].number,
                     f"indentation outside the subset near {synthetic[consumed].text!r}")
            sequence.append(item)
            pos = probe
            continue
        sequence.append(_parse_scalar(head, source, line.number))
        pos += 1
    return sequence, pos


def parse_subset_yaml(text: str, source: str) -> dict:
    _reject_non_ascii(text, source)
    lines = _logical_lines(text, source)
    if not lines:
        fail(source, None, "empty document")
    if lines[0].indent != 0:
        fail(source, lines[0].number, "top level must start at column 0")
    node, consumed = _parse_block(lines, 0, 0, source)
    if consumed != len(lines):
        fail(source, lines[consumed].number,
             f"content outside the root block near {lines[consumed].text!r}")
    if not isinstance(node, dict):
        fail(source, lines[0].number, "top level must be a mapping")
    return node


# ── declaration schema (§2.2) ────────────────────────────────────────────────

_NAME_PATTERN = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
_DIALECT_PATTERN = re.compile(r"[a-z_][a-z0-9_]*")
_KIND_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]*")
_CONSTRAINT_PATTERN = re.compile(r">= (0|[1-9][0-9]*)")
_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]*)\}")

# Tooth 2, by name: the grammar has NO ratified form. These keys are rejected with the
# fence cited so the error teaches, not just refuses.
_RATIFICATION_KEYS = frozenset({"ratification", "ratified", "soundness", "sound"})

_ROLES = ("Categorical", "Identifier", "Numeric")
_CARDINALITIES = ("bounded", "unbounded")
_PAYLOAD_CONTRACTS = ("None", "Declared")
_UNITS = ("count",)  # closed; grows deliberately with the next real entry that needs one


def _expect_keys(node: dict, allowed: tuple[str, ...], context: str, source: str) -> None:
    for key in node:
        if key in _RATIFICATION_KEYS:
            fail(source, None,
                 f"{context}: key `{key}:` — a hand-authored declaration can only ever be "
                 "Authored (soundness fence, tooth 2). Ratification lives in the "
                 "twin-generated manifest, keyed on this declaration's content hash; "
                 "there is no way to write it here, and that is the design.")
        if key not in allowed:
            fail(source, None,
                 f"{context}: unknown key `{key}:` (closed grammar; allowed: "
                 f"{', '.join(allowed)})")


def _required(node: dict, key: str, context: str, source: str):
    if key not in node:
        fail(source, None, f"{context}: missing required key `{key}:`")
    return node[key]


def _required_scalar(node: dict, key: str, context: str, source: str) -> str:
    value = _required(node, key, context, source)
    if not isinstance(value, str):
        fail(source, None, f"{context}: `{key}:` must be a scalar")
    return value


def validate_declaration(document: dict, entry_name_from_filename: str, source: str) -> dict:
    """Validate one declaration document; return the SEMANTIC declaration (dict).

    The returned dict is the canonical content — exactly what the hash covers.
    """
    _expect_keys(document, ("intent",), "document root", source)
    intent = _required(document, "intent", "document root", source)
    if not isinstance(intent, dict):
        fail(source, None, "`intent:` must be a mapping")
    _expect_keys(intent, ("name", "dialect", "structure", "body"), "intent", source)

    name = _required_scalar(intent, "name", "intent", source)
    if not _NAME_PATTERN.fullmatch(name):
        fail(source, None,
             f"intent name {name!r}: must be dotted lowercase (`<family>.<entry>`, "
             "[a-z0-9_], at least one dot) — the library identity, globally unique")
    if name != entry_name_from_filename:
        fail(source, None,
             f"intent name {name!r} does not match its file name "
             f"(expected `{entry_name_from_filename}{INTENT_FILE_SUFFIX}` to declare "
             f"`name: {entry_name_from_filename}`) — one entry, one file (§6)")

    declaration: dict[str, object] = {"name": name}

    dialect = intent.get("dialect")
    if dialect is not None:
        if not isinstance(dialect, str) or not _DIALECT_PATTERN.fullmatch(dialect):
            fail(source, None, f"`dialect:` must name a canon semantic package ([a-z_]) — got {dialect!r}")
        declaration["dialect"] = dialect

    structure = intent.get("structure")
    body = intent.get("body")
    if structure is None and body is None:
        fail(source, None,
             "a declaration needs a structural half, a body half, or both — the fourth "
             "shape (neither) is illegal (§2.1); an entry that declares nothing is not an intent")

    if structure is not None:
        if not isinstance(structure, dict):
            fail(source, None, "`structure:` must be a mapping")
        if "child_order" in structure:
            fail(source, None,
                 "structure: `child_order:` is CONSUMED from the canon dialect rows through "
                 "`kind`, never re-declared here (SID-1) — remove the key")
        _expect_keys(structure, ("kind", "payload"), "structure", source)
        if dialect is None:
            fail(source, None,
                 "a structural half requires `dialect:` — a structural kind exists only in a "
                 "canon dialect's vocabulary (§3.2); a dialect-less quantum has no rows to pair with")
        kind = _required_scalar(structure, "kind", "structure", source)
        if not _KIND_PATTERN.fullmatch(kind):
            fail(source, None, f"structure kind {kind!r}: not a valid kind token")
        payload = _required_scalar(structure, "payload", "structure", source)
        if payload not in _PAYLOAD_CONTRACTS:
            fail(source, None,
                 f"structure payload {payload!r}: the payload CONTRACT is `None` or `Declared` "
                 "(§2.4) — never a literal value; the binding supplies the value per instance")
        declaration["structure"] = {"kind": kind, "payload": payload}

    if body is not None:
        if not isinstance(body, dict):
            fail(source, None, "`body:` must be a mapping")
        _expect_keys(body, ("message_template", "fields"), "body", source)
        template = _required_scalar(body, "message_template", "body", source)
        if not template:
            fail(source, None, "body: `message_template:` must be non-empty")
        raw_fields = body.get("fields", [])
        if not isinstance(raw_fields, list):
            fail(source, None, "body: `fields:` must be a sequence")
        fields: list[dict] = []
        seen_field_names: set[str] = set()
        for position, raw_field in enumerate(raw_fields):
            if not isinstance(raw_field, dict):
                fail(source, None, f"body fields[{position}]: must be a mapping")
            fields.append(_validate_field(raw_field, position, seen_field_names, source))
        for placeholder in _PLACEHOLDER_PATTERN.findall(template):
            if placeholder not in seen_field_names:
                fail(source, None,
                     f"body: message_template placeholder {{{placeholder}}} names no declared "
                     "field — the template may only bind the declared field contract")
        declaration["body"] = {"message_template": template, "fields": fields}

    return declaration


def _validate_field(field: dict, position: int, seen_names: set[str], source: str) -> dict:
    context = f"body fields[{position}]"
    _expect_keys(field, ("name", "role", "domain", "cardinality", "unit", "constraint"),
                 context, source)
    name = _required_scalar(field, "name", context, source)
    if not _FIELD_NAME_PATTERN.fullmatch(name):
        fail(source, None, f"{context}: field name {name!r} outside [a-z_][a-z0-9_]*")
    if name in seen_names:
        fail(source, None, f"{context}: duplicate field name {name!r}")
    seen_names.add(name)
    role = _required_scalar(field, "role", context, source)
    if role not in _ROLES:
        fail(source, None, f"{context}: role {role!r} is not one of {', '.join(_ROLES)}")

    result: dict[str, object] = {"name": name, "role": role}

    if role == "Categorical":
        domain = _required(field, "domain", context, source)
        if not isinstance(domain, list) or not domain:
            fail(source, None,
                 f"{context}: Categorical requires `domain: [v1, v2, ...]` — the closed "
                 "vocabulary IS the structure (§2.3); an open categorical is an Identifier")
        if len(set(domain)) != len(domain):
            fail(source, None, f"{context}: domain values must be unique")
        for forbidden in ("cardinality", "unit", "constraint"):
            if forbidden in field:
                fail(source, None, f"{context}: `{forbidden}:` is not a Categorical key")
        result["domain"] = list(domain)
    elif role == "Identifier":
        domain = _required(field, "domain", context, source)
        if domain != "open":
            fail(source, None,
                 f"{context}: Identifier requires `domain: open` — a closed identifier "
                 "vocabulary is a Categorical by another name; declare it as one")
        # §2.3 witness ruling (2026-07-21): an open domain is a DECLARED blind spot in the
        # fence, and it must also declare its cardinality class, so the twin can still
        # classify a cardinality mismatch as structural residual. No default — silence
        # is impossible here for the same reason it is impossible for the ratification
        # state (tooth-1 posture applied to the schema).
        cardinality = _required_scalar(field, "cardinality", context, source)
        if cardinality not in _CARDINALITIES:
            fail(source, None,
                 f"{context}: cardinality {cardinality!r} is not one of "
                 f"{', '.join(_CARDINALITIES)} (declared class of the open domain)")
        for forbidden in ("unit", "constraint"):
            if forbidden in field:
                fail(source, None, f"{context}: `{forbidden}:` is not an Identifier key")
        result["domain"] = "open"
        result["cardinality"] = cardinality
    else:  # Numeric
        if "domain" in field:
            fail(source, None,
                 f"{context}: Numeric declares `unit:` (+ optional `constraint:`), never a "
                 "`domain:` — its range is DYNAMICS and lives in the binding (§2.3)")
        if "cardinality" in field:
            fail(source, None, f"{context}: `cardinality:` is not a Numeric key")
        unit = _required_scalar(field, "unit", context, source)
        if unit not in _UNITS:
            fail(source, None,
                 f"{context}: unit {unit!r} outside the closed set {{{', '.join(_UNITS)}}} "
                 "— grow the set deliberately with the entry that needs it, not by default")
        result["unit"] = unit
        constraint = field.get("constraint")
        if constraint is not None:
            if not isinstance(constraint, str) or not _CONSTRAINT_PATTERN.fullmatch(constraint):
                fail(source, None,
                     f"{context}: constraint {constraint!r} outside the closed grammar "
                     "(`>= <non-negative-int>`)")
            result["constraint"] = constraint

    return result


# ── canonicalize + hash (§4.3 / §5.3 MUST 3) ─────────────────────────────────


def canonical_hash(declaration: dict) -> str:
    """sha256 over the canonical JSON of the SEMANTIC declaration.

    Key order is sorted; list order is preserved (field order and domain order are
    semantic — emission order and positional weights bind to them). The tool version
    is deliberately NOT part of the input (MUST 4).
    """
    canonical = json.dumps(declaration, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# ── manifest resolution (§4.2/§4.3 — teeth 2 and 3) ──────────────────────────

_MANIFEST_RECORD_KEYS = ("name", "declaration_hash", "corpus", "residual", "floor",
                         "study", "date")


def parse_manifest(text: str, source: str) -> list[dict]:
    document = parse_subset_yaml(text, source)
    if set(document) != {"ratification"}:
        fail(source, None, "manifest root must be exactly `ratification:`")
    ratification = document["ratification"]
    if not isinstance(ratification, dict) or set(ratification) != {"entries"}:
        fail(source, None, "manifest must be `ratification:` -> `entries:`")
    entries = ratification["entries"]
    if not isinstance(entries, list):
        fail(source, None, "manifest `entries:` must be a sequence")
    records: list[dict] = []
    seen: set[str] = set()
    for position, record in enumerate(entries):
        context = f"manifest entries[{position}]"
        if not isinstance(record, dict):
            fail(source, None, f"{context}: must be a mapping")
        for key in record:
            if key not in _MANIFEST_RECORD_KEYS:
                fail(source, None, f"{context}: unknown key `{key}:`")
        for key in _MANIFEST_RECORD_KEYS:
            if key not in record or not isinstance(record[key], str) or not record[key]:
                # A partial record is an instrument fault, not a warning: the manifest
                # is measured evidence, and evidence with missing fields is no evidence.
                fail(source, None, f"{context}: missing or empty `{key}:` (instrument fault)")
        if record["name"] in seen:
            fail(source, None, f"{context}: duplicate record for {record['name']!r}")
        seen.add(record["name"])
        records.append(dict(record))
    return records


def resolve_ratification(entries: dict[str, dict], records: list[dict],
                         manifest_source: str, notices: list[str]) -> dict[str, dict | None]:
    """Per entry name: the matching Ratified record, or None (Authored).

    The name/hash partition implements both §4.3 bullets at once:
      * record name matches a current entry, hash differs  -> the entry was EDITED;
        it silently reverts to Authored (by design — an edited declaration is a new,
        unmeasured claim). A notice says so; it is not an error.
      * record name matches NO current entry -> a claim about something that no
        longer exists: an INSTRUMENT FAULT. The register must fail loudly rather
        than quietly assert a dead judgment.
    """
    resolution: dict[str, dict | None] = {name: None for name in entries}
    for record in records:
        name = record["name"]
        if name not in entries:
            fail(manifest_source, None,
                 f"Ratified record for {name!r} matches no current declaration — a stale "
                 "record is an instrument fault (§4.3); regenerate the manifest from a "
                 "measured twin run")
        current_hash = canonical_hash(entries[name])
        if record["declaration_hash"] == current_hash:
            resolution[name] = record
        else:
            notices.append(
                f"notice: {name} was Ratified against {record['declaration_hash'][:12]}... "
                f"but its declaration hash is now {current_hash[:12]}... -- the edit revoked "
                "ratification; the entry is Authored until the twin re-measures it")
    return resolution


# ── discovery (§6 — the index is derived, never hand-maintained) ─────────────


def discover_entries(library_dir: Path) -> dict[str, dict]:
    """Scan the flat library directory; every *.intent.yaml IS an entry (fail-closed)."""
    entries: dict[str, dict] = {}
    for path in sorted(library_dir.iterdir()):
        if not path.name.endswith(INTENT_FILE_SUFFIX):
            continue
        entry_name = path.name[: -len(INTENT_FILE_SUFFIX)]
        text = path.read_bytes().decode("ascii", errors="strict") \
            if _is_ascii(path) else _fail_encoding(path)
        document = parse_subset_yaml(text, str(path))
        if "intent" not in document:
            fail(str(path), None,
                 "a *.intent.yaml in the library directory must declare an `intent:` root "
                 "— strays are rejected, not skipped (the index is derived by content)")
        declaration = validate_declaration(document, entry_name, str(path))
        name = declaration["name"]
        if name in entries:
            fail(str(path), None, f"duplicate library identity {name!r} (K1)")
        entries[name] = declaration
    return entries


def _is_ascii(path: Path) -> bool:
    try:
        path.read_bytes().decode("ascii")
        return True
    except UnicodeDecodeError:
        return False


def _fail_encoding(path: Path) -> str:
    raise DeclarationError(f"{path}: non-ASCII bytes — declarations are ASCII-only")


# ── C++ emission (deterministic; the shape the api partition defines) ────────


def _cpp_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _cpp_symbol(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[._]", name))


def _emit_field(entry_symbol: str, index: int, field: dict, out: list[str]) -> str:
    """Emit one field's backing array (if any); return the FieldContractView initializer."""
    name = field["name"]
    role = field["role"]
    if role == "Categorical":
        array_name = f"k{entry_symbol}Field{index}Domain"
        values = ", ".join(f'"{_cpp_escape(v)}"sv' for v in field["domain"])
        out.append(f"inline constexpr std::array<std::string_view, {len(field['domain'])}> "
                   f"{array_name}{{{values}}};")
        domain = f"FieldDomain{{ClosedDomain{{std::span<const std::string_view>{{{array_name}}}}}}}"
    elif role == "Identifier":
        cardinality = "Bounded" if field["cardinality"] == "bounded" else "Unbounded"
        domain = f"FieldDomain{{OpenDomain{{CardinalityClass::{cardinality}}}}}"
    else:  # Numeric
        constraint = field.get("constraint")
        if constraint is None:
            minimum = "std::nullopt"
        else:
            minimum = f"std::int64_t{{{constraint.removeprefix('>= ')}}}"
        domain = (f'FieldDomain{{NumericContract{{"{_cpp_escape(field["unit"])}"sv, '
                  f"{minimum}}}}}")
    return f'FieldContractView{{"{_cpp_escape(name)}"sv, {domain}}}'


def _emit_view_index(out: list[str], index_name: str, initializers: list[str],
                     lead_comment: str | None) -> None:
    if lead_comment is not None:
        out.append(lead_comment)
        out.append("// a hand-maintained list here would be the rot pattern the design refuses.")
    if not initializers:
        out.append(f"inline constexpr std::span<const IntentLibraryEntryView> {index_name}{{}};")
        return
    storage_name = f"{index_name}Storage"
    out.append(f"inline constexpr std::array<IntentLibraryEntryView, {len(initializers)}> "
               f"{storage_name}{{")
    for initializer in initializers:
        out.append(f"    {initializer},")
    out.append("};")
    out.append(f"inline constexpr std::span<const IntentLibraryEntryView> "
               f"{index_name}{{{storage_name}}};")


def emit_cpp(entries: dict[str, dict], resolution: dict[str, dict | None],
             source_names: list[str]) -> str:
    out: list[str] = []
    out.append("// GENERATED by intent_library_codegen.py -- DO NOT EDIT.")
    out.append(f"// tool version: {TOOL_VERSION} (enters this header, never the declaration hash -- sec 5.3 MUST 4)")
    out.append("// BUILT, never committed (sec 5.2): a pure function of (the declaration set, this tool).")
    out.append(f"// declarations: {', '.join(source_names) if source_names else '(none)'}")
    out.append("// clang-format off")
    out.append("")
    out.append("namespace logcraft::core::intent_library_gen")
    out.append("{")
    out.append("")
    out.append("using namespace std::string_view_literals;")
    out.append("")
    out.append("// The canon-kind vocabulary pin: if canon grows a kind, -Werror=switch breaks THIS")
    out.append("// function on the next build, forcing the mirror (IntentKind + to_canon_kind) and the")
    out.append("// library to grow in the same commit. The mirror cannot drift silently.")
    out.append("[[nodiscard]] consteval bool canon_kind_is_pinned(insight::tokenization::IntentMarkerKind kind)")
    out.append("{")
    out.append("    switch (kind)")
    out.append("    {")
    out.append("    case insight::tokenization::IntentMarkerKind::None:")
    out.append("    case insight::tokenization::IntentMarkerKind::Job:")
    out.append("    case insight::tokenization::IntentMarkerKind::Step:")
    out.append("        return true;")
    out.append("    }")
    out.append("    return false;")
    out.append("}")
    out.append("static_assert(canon_kind_is_pinned(insight::tokenization::IntentMarkerKind::None) &&")
    out.append("              canon_kind_is_pinned(insight::tokenization::IntentMarkerKind::Job) &&")
    out.append("              canon_kind_is_pinned(insight::tokenization::IntentMarkerKind::Step));")
    out.append("")
    out.append("// sec 3.2 -- the kind comes FROM the package: a declared kind must be one the")
    out.append("// dialect's own reader rows declare. Consumed from canon's data; nothing mirrored.")
    out.append("[[nodiscard]] consteval bool kind_declared_by(")
    out.append("    insight::tokenization::IntentMarkerKind kind,")
    out.append("    std::span<const insight::semantic::IntentMarkerRow> markers)")
    out.append("{")
    out.append("    for (const insight::semantic::IntentMarkerRow& row : markers)")
    out.append("    {")
    out.append("        if (row.kind == kind)")
    out.append("            return true;")
    out.append("    }")
    out.append("    return false;")
    out.append("}")
    out.append("")
    out.append("// sec 2.4 -- the payload CONTRACT must agree with the dialect's emit rows:")
    out.append("// `Declared` needs a payload-bearing emit row for the kind, `None` a payload-less one.")
    out.append("[[nodiscard]] consteval bool payload_contract_served(")
    out.append("    insight::tokenization::IntentMarkerKind kind, PayloadContract contract,")
    out.append("    std::span<const insight::semantic::IntentEmitRow> emits)")
    out.append("{")
    out.append("    for (const insight::semantic::IntentEmitRow& row : emits)")
    out.append("    {")
    out.append("        if (row.kind != kind)")
    out.append("            continue;")
    out.append("        const bool payload_bearing{row.emit != insight::semantic::PayloadEmit::None};")
    out.append("        if (payload_bearing == (contract == PayloadContract::Declared))")
    out.append("            return true;")
    out.append("    }")
    out.append("    return false;")
    out.append("}")
    out.append("")

    view_initializers: list[str] = []
    ratified_names: list[str] = []
    for name in sorted(entries):
        declaration = entries[name]
        symbol = _cpp_symbol(name)
        record = resolution[name]
        out.append(f"// ---- {name} ----")
        field_views: list[str] = []
        body = declaration.get("body")
        if body is not None:
            for index, field in enumerate(body["fields"]):
                field_views.append(_emit_field(symbol, index, field, out))
            if field_views:
                out.append(f"inline constexpr std::array<FieldContractView, {len(field_views)}> "
                           f"k{symbol}Fields{{")
                for view in field_views:
                    out.append(f"    {view},")
                out.append("};")
        out.append(f"struct {symbol}Entry")
        out.append("{")
        out.append(f'    static constexpr IntentIdentity identity{{"{name}"sv,')
        out.append(f'                                             "{canonical_hash(declaration)}"sv}};')
        dialect = declaration.get("dialect")
        structure = declaration.get("structure")
        if structure is not None:
            out.append(f"    static constexpr std::optional<StructuralHalfView> structure{{StructuralHalfView{{")
            out.append(f"        IntentKind::{structure['kind']}, PayloadContract::{structure['payload']}}}}};")
        else:
            out.append("    static constexpr std::optional<StructuralHalfView> structure{std::nullopt};")
        if body is not None:
            fields_span = (f"std::span<const FieldContractView>{{k{symbol}Fields}}"
                           if field_views else "std::span<const FieldContractView>{}")
            out.append(f"    static constexpr std::optional<BodyContractView> body{{BodyContractView{{")
            out.append(f'        "{_cpp_escape(body["message_template"])}"sv, {fields_span}}}}};')
        else:
            out.append("    static constexpr std::optional<BodyContractView> body{std::nullopt};")
        if record is None:
            out.append("    static constexpr Ratification ratification{Ratification::authored()};")
        else:
            ratified_names.append(name)
            out.append("    static constexpr Ratification ratification{Ratification::ratified(RatificationEvidence{")
            out.append(f'        "{_cpp_escape(record["corpus"])}"sv, "{_cpp_escape(record["residual"])}"sv,')
            out.append(f'        "{_cpp_escape(record["floor"])}"sv, "{_cpp_escape(record["study"])}"sv,')
            out.append(f'        "{_cpp_escape(record["date"])}"sv}})}};')
        if dialect is not None and structure is not None:
            out.append(f"    using dialect = insight::semantic::{dialect}::Dialect;")
        out.append("};")
        out.append(f"static_assert(StructuralContract<{symbol}Entry>);")
        if dialect is not None and structure is not None:
            out.append(f"static_assert(insight::semantic::DialectIntent<{symbol}Entry::dialect>,")
            out.append(f'              "{name}: dialect package must pair reader-to-writer");')
            out.append(f"static_assert(kind_declared_by(insight::tokenization::IntentMarkerKind::{structure['kind']},")
            out.append(f"                               {symbol}Entry::dialect::markers),")
            out.append(f'              "{name}: kind is not declared by the {dialect} dialect");')
            out.append(f"static_assert(payload_contract_served(insight::tokenization::IntentMarkerKind::{structure['kind']},")
            out.append(f"                                      PayloadContract::{structure['payload']},")
            out.append(f"                                      {symbol}Entry::dialect::emit_markers),")
            out.append(f'              "{name}: payload contract has no serving emit row");')
        dialect_view = f'"{_cpp_escape(dialect)}"sv' if dialect is not None else '""sv'
        view_initializers.append(
            f"IntentLibraryEntryView{{{symbol}Entry::identity, {dialect_view}, "
            f"{symbol}Entry::structure, {symbol}Entry::body, {symbol}Entry::ratification}}")
        out.append("")

    # Index emission note: the indices are SPANS over conditionally-emitted storage arrays.
    # An empty index must not become std::array<T, 0> — MSVC's array<T, 0> declares a real T
    # element, and IntentLibraryEntryView is (deliberately, tooth 1) not default-constructible.
    _emit_view_index(out, "kEntries", view_initializers,
                     "// The derived index (sec 6): discovered by content, sorted by name --")
    out.append("")
    out.append("// Tooth 4 -- the showcase surface is fail-closed BY CONSTRUCTION: this index is")
    out.append("// generated from hash-matched records only. An Authored entry is not in it; there")
    out.append("// is nothing to filter and nothing to forget to filter.")
    ratified_views = [view_initializers[sorted(entries).index(name)] for name in ratified_names]
    _emit_view_index(out, "kRatifiedEntries", ratified_views, None)
    out.append("")
    out.append("} // namespace logcraft::core::intent_library_gen")
    out.append("// clang-format on")
    out.append("")
    return "\n".join(out)


# ── generation driver ────────────────────────────────────────────────────────


def generate(library_dir: Path, manifest_path: Path | None, out_path: Path) -> int:
    entries = discover_entries(library_dir)
    notices: list[str] = []
    records: list[dict] = []
    if manifest_path is not None and manifest_path.exists():
        records = parse_manifest(
            manifest_path.read_bytes().decode("ascii"), str(manifest_path))
    resolution = resolve_ratification(entries, records, str(manifest_path), notices)
    source_names = [f"{name}{INTENT_FILE_SUFFIX}" for name in sorted(entries)]
    rendered = emit_cpp(entries, resolution, source_names)
    for notice in notices:
        print(notice, file=sys.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.read_bytes() == rendered.encode("ascii"):
        return 0  # unchanged — do not touch the file (no rebuild churn)
    with open(out_path, "w", encoding="ascii", newline="\n") as handle:
        handle.write(rendered)
    return 0


# ── selftest (synthetic fixtures ONLY — the moat rule) ───────────────────────

_SYNTHETIC_ENTRY = """\
intent:
  name: synthetic.demo
  dialect: github
  structure:
    kind: Step
    payload: Declared
  body:
    message_template: "{verb} {object}"
    fields:
      - name: verb
        role: Categorical
        domain: [alpha, beta]
      - name: object
        role: Identifier
        domain: open
        cardinality: unbounded
      - name: tally
        role: Numeric
        unit: count
        constraint: ">= 0"
"""

_SYNTHETIC_GENERIC = """\
intent:
  name: synthetic.generic
  body:
    message_template: "plain {token}"
    fields:
      - name: token
        role: Categorical
        domain: [one, two]
"""


def _selftest_case(label: str, failures: list[str], check) -> None:
    try:
        check()
    except AssertionError as error:
        failures.append(f"{label}: {error}")
    except DeclarationError as error:
        failures.append(f"{label}: unexpected rejection: {error}")


def _expect_rejection(label: str, failures: list[str], needle: str, thunk) -> None:
    try:
        thunk()
    except DeclarationError as error:
        if needle not in str(error):
            failures.append(f"{label}: rejected, but message lacks {needle!r}: {error}")
        return
    failures.append(f"{label}: accepted a declaration the fence must refuse")


def _parse_entry(text: str, name: str = "synthetic.demo") -> dict:
    return validate_declaration(parse_subset_yaml(text, "<selftest>"), name, "<selftest>")


def selftest() -> int:
    failures: list[str] = []

    base = _parse_entry(_SYNTHETIC_ENTRY)
    base_hash = canonical_hash(base)

    # ── tooth 3: canonicalize-then-hash (MUST 3) ──
    reformatted = _SYNTHETIC_ENTRY.replace("  name: synthetic.demo",
                                           "  # a comment\n  name:   'synthetic.demo'")
    reordered = _SYNTHETIC_ENTRY.replace(
        "  name: synthetic.demo\n  dialect: github\n",
        "  dialect: github\n  name: synthetic.demo\n")
    _selftest_case("hash: reformat invariant", failures, lambda: _assert(
        canonical_hash(_parse_entry(reformatted)) == base_hash,
        "a reformat moved the hash — authors would avoid touching files (MUST 3)"))
    _selftest_case("hash: key-order invariant", failures, lambda: _assert(
        canonical_hash(_parse_entry(reordered)) == base_hash,
        "mapping key order moved the hash"))
    edited = _SYNTHETIC_ENTRY.replace("domain: [alpha, beta]", "domain: [alpha, gamma]")
    _selftest_case("hash: edit revokes", failures, lambda: _assert(
        canonical_hash(_parse_entry(edited)) != base_hash,
        "a semantic edit did NOT move the hash — an edit would inherit soundness"))
    field_order = _SYNTHETIC_ENTRY.replace(
        "domain: [alpha, beta]", "domain: [beta, alpha]")
    _selftest_case("hash: domain order is semantic", failures, lambda: _assert(
        canonical_hash(_parse_entry(field_order)) != base_hash,
        "domain order is positional-binding-semantic and must enter the hash"))

    # ── tooth 2: the grammar has no ratified form ──
    for key in sorted(_RATIFICATION_KEYS):
        _expect_rejection(f"tooth 2: `{key}:` rejected", failures, "tooth 2",
                          lambda k=key: _parse_entry(
                              _SYNTHETIC_ENTRY.replace("  dialect: github",
                                                       f"  dialect: github\n  {k}: yes")))

    # ── shape legality (§2.1) ──
    _expect_rejection("shape: fourth shape illegal", failures, "fourth",
                      lambda: _parse_entry("intent:\n  name: synthetic.demo\n"))
    _expect_rejection("shape: structure requires dialect", failures, "dialect",
                      lambda: _parse_entry(
                          "intent:\n  name: synthetic.demo\n  structure:\n"
                          "    kind: Step\n    payload: None\n"))
    _expect_rejection("shape: child_order is consumed, not declared", failures, "child_order",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "    kind: Step", "    kind: Step\n    child_order: Ordered")))
    _expect_rejection("shape: payload contract is not a value", failures, "CONTRACT",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "payload: Declared", "payload: yarn_install")))

    # ── §2.3 witness ruling: open domain declares its cardinality class ──
    _expect_rejection("open domain: cardinality required", failures, "cardinality",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "        cardinality: unbounded\n", "")))
    _expect_rejection("closed identifier is a categorical", failures, "Categorical",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "domain: open", "domain: [a, b]")))
    _expect_rejection("numeric: no domain key", failures, "DYNAMICS",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "unit: count", "unit: count\n        domain: [x]")))
    _expect_rejection("numeric: closed unit set", failures, "closed set",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "unit: count", "unit: parsecs")))
    _expect_rejection("numeric: closed constraint grammar", failures, "closed grammar",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          '">= 0"', '"!= 7"')))
    _expect_rejection("template placeholders must be declared", failures, "placeholder",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY.replace(
                          "{verb} {object}", "{verb} {ghost}")))
    _expect_rejection("name/filename coupling", failures, "one file",
                      lambda: _parse_entry(_SYNTHETIC_ENTRY, name="synthetic.other"))

    # ── the subset parser's fences ──
    _expect_rejection("parser: non-ASCII", failures, "ASCII",
                      lambda: parse_subset_yaml("intent:\n  name: café\n", "<selftest>"))
    _expect_rejection("parser: tabs", failures, "tab",
                      lambda: parse_subset_yaml("intent:\n\tname: x\n", "<selftest>"))
    _expect_rejection("parser: anchors", failures, "anchor",
                      lambda: parse_subset_yaml("intent:\n  name: &a x\n", "<selftest>"))
    _expect_rejection("parser: duplicate keys", failures, "duplicate",
                      lambda: parse_subset_yaml("intent:\n  name: a\n  name: b\n", "<selftest>"))

    # ── manifest resolution (teeth 2+3 at the register) ──
    manifest_text = (
        "ratification:\n"
        "  entries:\n"
        f"    - name: synthetic.demo\n"
        f"      declaration_hash: {base_hash}\n"
        "      corpus: synthetic-corpus-A\n"
        '      residual: "0.010"\n'
        '      floor: "0.050"\n'
        "      study: synthetic-study\n"
        "      date: 2026-01-01\n")
    records = parse_manifest(manifest_text, "<manifest>")
    notices: list[str] = []
    resolution = resolve_ratification({"synthetic.demo": base}, records, "<manifest>", notices)
    _selftest_case("manifest: hash match ratifies", failures, lambda: _assert(
        resolution["synthetic.demo"] is not None and not notices,
        f"expected Ratified with no notice, got {resolution} / {notices}"))

    edited_entry = _parse_entry(edited)
    notices = []
    resolution = resolve_ratification({"synthetic.demo": edited_entry}, records,
                                      "<manifest>", notices)
    _selftest_case("manifest: edit reverts to Authored (tooth 3)", failures, lambda: _assert(
        resolution["synthetic.demo"] is None and len(notices) == 1,
        f"an edited declaration must silently revert to Authored: {resolution} / {notices}"))

    _expect_rejection("manifest: stale record is an instrument fault", failures,
                      "instrument fault",
                      lambda: resolve_ratification({"synthetic.other": base}, records,
                                                   "<manifest>", []))
    _expect_rejection("manifest: partial record is an instrument fault", failures,
                      "instrument fault",
                      lambda: parse_manifest(manifest_text.replace(
                          "      study: synthetic-study\n", ""), "<manifest>"))
    _expect_rejection("manifest: no unknown keys", failures, "unknown key",
                      lambda: parse_manifest(manifest_text.replace(
                          "      date: 2026-01-01\n",
                          "      date: 2026-01-01\n      vibe: good\n"), "<manifest>"))

    # ── emission determinism + tooth 4 at the emitter ──
    generic = _parse_entry(_SYNTHETIC_GENERIC, name="synthetic.generic")
    both = {"synthetic.demo": base, "synthetic.generic": generic}
    resolution_full = resolve_ratification(both, records, "<manifest>", [])
    rendered_a = emit_cpp(both, resolution_full, sorted(both))
    rendered_b = emit_cpp(dict(reversed(list(both.items()))), resolution_full, sorted(both))
    _selftest_case("emit: byte-deterministic + input-order independent", failures,
                   lambda: _assert(rendered_a == rendered_b,
                                   "emission depends on input order"))
    _selftest_case("emit: ASCII only", failures, lambda: _assert(
        all(0x20 <= ord(c) <= 0x7E or c == "\n" for c in rendered_a),
        "emitted bytes outside printable ASCII + LF"))
    _selftest_case("emit: ratified index excludes Authored (tooth 4)", failures, lambda: _assert(
        "std::array<IntentLibraryEntryView, 1> kRatifiedEntriesStorage" in rendered_a
        and rendered_a.count("SyntheticGenericEntry::identity") == 1,
        "the generated ratified index must contain exactly the hash-matched entry"))
    empty_rendered = emit_cpp(both, {name: None for name in both}, sorted(both))
    _selftest_case("emit: empty ratified index is a bare span (MSVC array<T,0> hazard)",
                   failures, lambda: _assert(
                       "std::span<const IntentLibraryEntryView> kRatifiedEntries{};"
                       in empty_rendered and "kRatifiedEntriesStorage" not in empty_rendered,
                       "an empty index must emit no storage array — MSVC's std::array<T,0> "
                       "declares a real element of the non-default-constructible view type"))
    _selftest_case("emit: tool version outside the hash (MUST 4)", failures, lambda: _assert(
        f"tool version: {TOOL_VERSION}" in rendered_a
        and base_hash in rendered_a
        and TOOL_VERSION not in json.dumps(base, sort_keys=True),
        "tool version must appear in the header and never in hashed content"))

    if failures:
        for failure in failures:
            print(f"selftest FAIL — {failure}", file=sys.stderr)
        return 1
    print("selftest OK — the fence holds: teeth 1-4 rejections fire, the hash is "
          "canonical (reformat-stable, edit-sensitive), the manifest partitions "
          "edit-vs-stale, emission is byte-deterministic ASCII")
    return 0


def _assert(condition: bool, message: str) -> None:
    assert condition, message


# ── entry point ──────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="C1 build-time codegen for the LogCraft Intent library")
    parser.add_argument("--library-dir", type=Path,
                        help="flat directory of <name>.intent.yaml declarations")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="twin-generated ratification manifest (absent = all Authored)")
    parser.add_argument("--out", type=Path, help="generated C++ include to write")
    parser.add_argument("--selftest", action="store_true",
                        help="run the synthetic-fixture selftest and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.library_dir is None or args.out is None:
        parser.error("--library-dir and --out are required (or use --selftest)")
    try:
        return generate(args.library_dir, args.manifest, args.out)
    except DeclarationError as error:
        print(f"intent_library_codegen: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
