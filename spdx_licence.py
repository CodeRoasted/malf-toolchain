"""The workspace's ONE reader of a declared licence: an SPDX expression split into identifiers, and a conan
recipe's `license` class attribute read from its source.

DN-142.D38 (2): a public store's push judges a third-party transport by the licence its recipe declares, read
from the published bytes, and the superproject's `licence_declaration` check module reads every tracked recipe
the same way. Both import this module, so the workspace keeps one SPDX reader: the check module loads it from
the workspace's malf, because malf cannot import Pharos (a public candidate checks out malf, never the private
superproject).

THE SPLIT. AND, OR and parentheses separate identifiers; the token after WITH is an exception, never a licence.
A recipe's tuple of strings is read as AND, every member a licence the package is under: `expression_of` holds
that rule, and both the recipe reader below and the third-party notices helper (DN-142.D39 (3)), which reads a
dependency's EVALUATED `license` at package time, write an expression through it.

THE RECIPE ATTRIBUTE. Every class-body assignment to `license`: a string or a tuple (or list) of strings is
LITERAL and split; any other value (a name, a call, a formatted string) is kept as its source text with no
identifier, so a reader can refuse it rather than guess.
"""

from __future__ import annotations

import ast
import re
import sys
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


def expression_of(value: object) -> str:
    """The SPDX expression a `license` value states: a string as is, a tuple or list of strings joined by ` AND `.
    raises ValueError on any other value, or an empty one, since no expression can be read from it."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (tuple, list)) and value and all(isinstance(part, str) and part.strip() for part in value):
        return " AND ".join(value)
    raise ValueError(f"a license value is a non-empty string or a tuple or list of them, not {value!r}")


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
            try:
                expression = expression_of([part.value for part in parts]) if literal else ast.unparse(value)
            except ValueError:
                literal, expression = False, ast.unparse(value)
            found.append(RecipeLicence(statement.lineno, expression, literal,
                                       tuple(identifiers_of(expression)) if literal else ()))
    return sorted(found, key=lambda each: each.line)


def selftest() -> int:
    """The reader's arms, offline: `expression_of` on each value shape, and `recipe_licences` splitting as it did
    before the function existed (DN-142.D39's spdx_licence arms)."""
    failures: list[str] = []
    checked = [0]

    def expect(what: str, actual: object, expected: object) -> None:
        checked[0] += 1
        if actual != expected:
            failures.append(f"{what}: expected {expected!r}, actual {actual!r}")

    expect("a string is kept as is", expression_of("Apache-2.0 OR MIT"), "Apache-2.0 OR MIT")
    expect("a tuple is joined by AND", expression_of(("Apache-2.0", "MIT")), "Apache-2.0 AND MIT")
    expect("a list is joined by AND", expression_of(["BSD-3-Clause", "Zlib", "curl"]), "BSD-3-Clause AND Zlib AND curl")
    for refused in ("", (), [], ("MIT", 3), None, 7):
        checked[0] += 1
        try:
            expression_of(refused)
        except ValueError:
            continue
        failures.append(f"expression_of({refused!r}) returned instead of refusing")
    recipe = ("class A:\n    license = ('Apache-2.0', 'MIT')\n"
              "class B:\n    license = 'GPL-2.0-or-later WITH Classpath-exception-2.0 OR (MIT AND Zlib)'\n"
              "class C:\n    license = LICENCE_NAME\n"
              "class D:\n    license = ''\n")
    expect("recipe_licences splits each class attribute as before",
           [(each.line, each.expression, each.literal, each.identifiers) for each in recipe_licences(recipe)],
           [(2, "Apache-2.0 AND MIT", True, ("Apache-2.0", "MIT")),
            (4, "GPL-2.0-or-later WITH Classpath-exception-2.0 OR (MIT AND Zlib)", True,
             ("GPL-2.0-or-later", "MIT", "Zlib")),
            (6, "LICENCE_NAME", False, ()),
            (8, "''", False, ())])
    for failure in failures:
        print(f"spdx_licence selftest: FAIL {failure}", file=sys.stderr)
    print(f"spdx_licence selftest: {checked[0] - len(failures)}/{checked[0]} arms passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] != ["selftest"]:
        print("usage: spdx_licence.py selftest", file=sys.stderr)
        sys.exit(2)
    sys.exit(selftest())
