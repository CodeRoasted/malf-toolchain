#!/usr/bin/env python3
"""codegen_common.py — the machinery shared by malf's build-time declaration codegens.

Two tools consume this module and they are DELIBERATELY separate entry points
(DN-17.D19): `intent_library_codegen.py` (the LogCraft Intent library) and
`dialect_package_codegen.py` (the insight-canon dialect semantic packages). What is
shared is mechanism — the strict-YAML subset parser, the canonical content hash, the C++
escaping helpers. What is NOT shared is any schema, any key set, and above all any SORT
rule: the Intent library sorts its entries (a set discovered from the filesystem, where
sorting is what makes discovery order-independent) while a dialect declaration's rows are
CONTENT IN DECLARED ORDER and must never be sorted (level-lift matching is
first-match-in-declared-order). One emitter with two modes is exactly where that gets
copied wrong, which is why this module carries no emitter at all.

The moat rules are opposite too, and that is the second reason the entry points are
split: the Intent library's declarations stay PRIVATE and every fixture beside that tool
is synthetic by construction; the dialect declarations are PUBLIC and live in
insight-canon. This module holds nothing that could carry either rule, so it cannot make
either ambiguous.

DETERMINISM (§5.3, inherited by both tools). The subset is: block maps, block sequences,
one-line flow sequences of scalars, quoted/plain scalars, comments. No anchors, tags,
flow maps, multiline scalars, tabs, duplicate keys. Zero parsing dependencies on any build
host. Scalars are STRINGS end-to-end — this parser types nothing, so no locale- or
stdlib-dependent text<->number conversion exists to diverge.

ENCODING (DN-17.D24 / DN-17.D27). The ASCII constraint exists for HASH STABILITY, so it
binds exactly what enters the hash. `parse_subset_yaml` therefore takes `ascii_only`:
the Intent library passes True and keeps the file-level pre-pass it has always had (its
grammar has no prose key, so every byte in the file is hashed content); the dialect tool
passes False and validates ASCII PER FIELD after parsing, because its `why:` prose is
hash-excluded and is UTF-8. `reject_non_ascii_field` is that per-field check, and it
carries the same byte-naming message the pre-pass does.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CODEGEN_COMMON_VERSION = "1"


# ── error reporting ──────────────────────────────────────────────────────────


class DeclarationError(Exception):
    """A declaration, manifest, or grammar violation. Message carries source + line."""


def fail(source: str, line: int | None, message: str) -> None:
    where = f"{source}:{line}" if line is not None else source
    raise DeclarationError(f"{where}: {message}")


# ── the strict YAML-subset parser (string-only scalars) ──────────────────────
#
# Parsed shape: dict[str, node] | list[node] | str.  All scalars are strings —
# `domain: [0, 1]` yields ["0", "1"].

_PLAIN_SCALAR = re.compile(r"[A-Za-z0-9_./:>=<+-][A-Za-z0-9_./:>=<+\- ()%]*")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ascii_fault(ch: str) -> str:
    return (f"non-ASCII or control byte 0x{ord(ch):02x} — declarations are "
            "ASCII-only (determinism MUST: no encoding may vary by host)")


def reject_non_ascii(text: str, source: str) -> None:
    """The FILE-LEVEL pre-pass: every byte of the document is hashed content."""
    for line_index, line in enumerate(text.split("\n"), start=1):
        for ch in line:
            if ch == "\t":
                continue  # rejected by the indentation pass, with the tab-specific message
            if not (0x20 <= ord(ch) <= 0x7E):
                fail(source, line_index, _ascii_fault(ch))


def reject_non_ascii_field(value: str, source: str, context: str) -> None:
    """The PER-FIELD check (DN-17.D27): applied to everything that enters the hash.

    The file-level pre-pass cannot serve a grammar with a hash-EXCLUDED prose key: it runs
    before parsing, so it cannot tell a `why:` scalar from a row field, and it rejects the
    file before any field exists. Same message, moved to the seat where the distinction is
    knowable.
    """
    for ch in value:
        if not (0x20 <= ord(ch) <= 0x7E):
            fail(source, None, f"{context}: {_ascii_fault(ch)}")


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


def parse_scalar(token: str, source: str, line_no: int) -> str:
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
                         '\\" and \\\\ (declarations are single-line scalars)')
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


def _split_flow_items(inner: str, source: str, line_no: int) -> list[str]:
    """Split a flow body on the commas that are OUTSIDE a quoted scalar.

    A naive `inner.split(",")` is wrong on a subset that already admits quoted scalars: a
    comma inside `"a, b"` is CONTENT, and splitting on it cut a legal scalar in half and
    then reported it as malformed. Nothing in the subset moves — this is the same grammar,
    read correctly.
    """
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(inner):
        ch = inner[index]
        if quote is not None:
            if ch == "\\" and quote == '"':
                if index + 1 >= len(inner):
                    fail(source, line_no, "dangling escape in quoted scalar")
                current.append(ch)
                current.append(inner[index + 1])
                index += 2
                continue
            if ch == quote:
                quote = None
            current.append(ch)
            index += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            index += 1
            continue
        if ch == ",":
            items.append("".join(current))
            current = []
            index += 1
            continue
        current.append(ch)
        index += 1
    if quote is not None:
        fail(source, line_no, "unterminated quoted scalar")
    items.append("".join(current))
    return items


def _parse_flow_sequence(token: str, source: str, line_no: int) -> list[str]:
    body = token.strip()
    assert body.startswith("[")
    if not body.endswith("]"):
        fail(source, line_no, "flow sequence must open and close on one line")
    inner = body[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    for raw in _split_flow_items(inner, source, line_no):
        stripped = raw.strip()
        if stripped[:1] not in ("'", '"') and any(
                bracket in stripped for bracket in "[]{}"):
            fail(source, line_no, "nested flow collections are outside the subset")
        items.append(parse_scalar(raw, source, line_no))
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
                mapping[key] = parse_scalar(rest, source, line.number)
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
        sequence.append(parse_scalar(head, source, line.number))
        pos += 1
    return sequence, pos


def parse_subset_yaml(text: str, source: str, *, ascii_only: bool = True) -> dict:
    if ascii_only:
        reject_non_ascii(text, source)
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


def key_line(text: str, source: str, key: str) -> int | None:
    """The 1-based line on which mapping key `key` is written, or None if it is absent.

    A refusal that names a file but not a line makes the author read the file; a refusal
    that names the line makes the author read the line. The parse discards positions —
    it returns plain dicts — so a caller that must CITE a key re-derives its line here.

    Built on the SAME logical-line pass the parser itself runs (comments stripped, tabs
    refused), so it can never disagree with the parse about what is a key and what is
    scalar content: `message_template: "dialect: x"` partitions at the FIRST colon and
    yields `message_template`, never `dialect`.

    Returns the FIRST match. Uniqueness is the CALLER's premise, not this function's — in
    the intent grammar it holds because `expect_keys` admits `dialect:` at exactly one
    level and a declaration is one entry per file.
    """
    for line in _logical_lines(text, source):
        head, separator, _ = line.text.partition(":")
        if separator and head.strip() == key:
            return line.number
    return None


# ── closed-grammar helpers ───────────────────────────────────────────────────


def expect_keys(node: dict, allowed: tuple[str, ...], context: str, source: str,
                rejected: dict[str, str] | None = None) -> None:
    """Closed key set. `rejected` gives a key its OWN message instead of the generic one.

    A closed grammar's value is the quality of its refusals (DN-17.D26): the keys an
    implementer is most likely to type wrongly are the ones that must teach rather than
    merely refuse.
    """
    for key in node:
        if rejected is not None and key in rejected:
            fail(source, None, f"{context}: key `{key}:` — {rejected[key]}")
        if key not in allowed:
            fail(source, None,
                 f"{context}: unknown key `{key}:` (closed grammar; allowed: "
                 f"{', '.join(allowed)})")


def required(node: dict, key: str, context: str, source: str):
    if key not in node:
        fail(source, None, f"{context}: missing required key `{key}:`")
    return node[key]


def required_scalar(node: dict, key: str, context: str, source: str) -> str:
    value = required(node, key, context, source)
    if not isinstance(value, str):
        fail(source, None, f"{context}: `{key}:` must be a scalar")
    return value


# ── canonical content hash (MUST 3/4) ────────────────────────────────────────


def canonical_hash(payload) -> str:
    """sha256 over canonical JSON of the SEMANTIC content — never over the file bytes.

    `sort_keys` makes mapping order irrelevant, the tight separators remove whitespace,
    and `ensure_ascii` makes any escaping deterministic (belt and braces: even were a
    non-ASCII byte ever to reach a hashed field, its JSON escaping does not vary).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# ── C++ emission helpers ─────────────────────────────────────────────────────


def cpp_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cpp_symbol(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[._]", name))


def read_text(path: Path, *, ascii_only: bool) -> str:
    """Read a declaration with an EXPLICIT codec — never the platform default.

    The file's decoding must not vary by host any more than its hash does (DN-17.D24).
    Newlines are normalised to LF here rather than left to universal-newline mode, so a
    CRLF checkout produces the same parse and the same bytes as an LF one.
    """
    raw = path.read_bytes()
    if ascii_only:
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise DeclarationError(
                f"{path}: non-ASCII bytes — declarations are ASCII-only") from error
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DeclarationError(
                f"{path}: not valid UTF-8 — the declaration codec is DECLARED, never "
                "inherited from the host locale") from error
    return text.replace("\r\n", "\n").replace("\r", "\n")
