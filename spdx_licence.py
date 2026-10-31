"""The workspace's ONE reader of a declared licence: an SPDX expression split into identifiers, and a conan
recipe's `license` class attribute read from its source.

DN-142.D38 (2): a public store's push judges a third-party transport by the licence its recipe declares, read
from the published bytes, and the superproject's `licence_declaration` check module reads every tracked recipe
the same way. Both import this module, so the workspace keeps one SPDX reader: the check module loads it from
the workspace's malf, because malf cannot import Pharos (a public candidate checks out malf, never the private
superproject).

THE SPLIT. AND, OR and parentheses separate identifiers; the token after WITH is an exception, never a licence.
A recipe's tuple of strings is read as AND, every member a licence the package is under.

THE RECIPE ATTRIBUTE. Every class-body assignment to `license`: a string or a tuple (or list) of strings is
LITERAL and split; any other value (a name, a call, a formatted string) is kept as its source text with no
identifier, so a reader can refuse it rather than guess.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

IDENTIFIER = re.compile(r"[A-Za-z0-9.+\-]+")
OPERATORS = frozenset({"AND", "OR"})
EXCEPTION_OPERATOR = "WITH"
RECIPE_ATTRIBUTE = "license"


@dataclass(frozen=True)
class RecipeLicence:
    """One `license = …` assignment in a recipe class body: its line, its expression (a tuple joined by AND,
    or the source text of a non-literal value), whether it is literal, and the identifiers it names."""

    line: int
    expression: str
    literal: bool
    identifiers: tuple[str, ...]


def identifiers_of(expression: str) -> list[str]:
    """The licence identifiers an SPDX expression names; the token after WITH is dropped."""
    found: list[str] = []
    skip_next = False
    for token in IDENTIFIER.findall(expression):
        if skip_next:
            skip_next = False
            continue
        upper = token.upper()
        if upper == EXCEPTION_OPERATOR:
            skip_next = True
        elif upper not in OPERATORS:
            found.append(token)
    return found


def recipe_licences(source: str, filename: str = "<recipe>") -> list[RecipeLicence]:
    """Every `license` class attribute of the recipe `source`, in source order. Raises SyntaxError when the
    source does not parse; `filename` is the name the error quotes."""
    found: list[RecipeLicence] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if not (isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == RECIPE_ATTRIBUTE for target in statement.targets)):
                continue
            value = statement.value
            parts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
            literal = all(isinstance(part, ast.Constant) and isinstance(part.value, str) for part in parts)
            expression = " AND ".join(part.value for part in parts) if literal else ast.unparse(value)
            found.append(RecipeLicence(statement.lineno, expression, literal,
                                       tuple(identifiers_of(expression)) if literal else ()))
    return sorted(found, key=lambda each: each.line)
