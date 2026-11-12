#!/usr/bin/env python3
"""compile_db.py — which of two compile-database entries for one file a repository's database keeps.

THE DEFECT THIS CLOSES, measured 2026-11-06 (ROADMAP N422). A repository's database is folded from its
packages' trees, one entry per file, and the folds kept the FIRST tree's entry (or, in a lone build's
fold, the latest). Since insight-eidos `0e098563`, `tamys-corpus` adds `tamys-tools/` as a subdirectory
under `EXCLUDE_FROM_ALL`: its database lists every `tamys-tools` translation unit, and its build compiles
none of them but `tamys_gate_tests`. It folds before `tamys-tools`, so its entries won, and a desk
`malf lint --all-files` of insight-eidos linted `tamys/src/cli` and `tamys/src/crawl` against module
interfaces that tree never builds: false findings (`module 'std' not found`).

THE RULE: an entry from a tree whose build compiles the file outranks one from a tree whose build does
not; between two of one rank, the fold's own precedence decides, as before. "The build compiles it"
is read from the tree's own build graph, never from the database: the file is an input of the tree's
default ninja target (`ninja -t inputs`, under `-n`, with the tree's own `CMAKE_MAKE_PROGRAM`). An
entry's tree is the nearest ancestor of its `directory` holding a `build.ninja`, since a
subdirectory's entries name the subdirectory's binary dir. An entry with no such ancestor has no graph
to read and is taken at its database's word; a graph that exists and cannot be read raises.

The graph is read only for a file two entries claim, so a fold with no such file reads none.
"""

from __future__ import annotations

import os
from pathlib import Path

from ninja_deps_guard import ninja_program, tool


class BuildGraphs:
    """The default-target inputs of every tree an entry names, each read once."""

    def __init__(self) -> None:
        self._trees: dict[str, str | None] = {}
        self._inputs: dict[str, frozenset[str]] = {}

    def _tree(self, directory: str) -> str | None:
        """The nearest ancestor of <directory> holding a build.ninja, else None."""
        if directory not in self._trees:
            here = Path(os.path.realpath(directory)) if directory else None
            found = None
            while here is not None:
                if (here / "build.ninja").is_file():
                    found = str(here)
                    break
                here = None if here.parent == here else here.parent
            self._trees[directory] = found
        return self._trees[directory]

    def _built(self, tree: str) -> frozenset[str]:
        if tree not in self._inputs:
            listed = tool(ninja_program(Path(tree)), Path(tree), "inputs", "-E").splitlines()
            self._inputs[tree] = frozenset(os.path.realpath(os.path.join(tree, line)) for line in listed if line)
        return self._inputs[tree]

    def compiles(self, entry: dict) -> bool:
        """True when the entry's tree's default build compiles the entry's file, or the tree has no graph."""
        directory = str(entry.get("directory") or "")
        tree = self._tree(directory)
        if tree is None:
            return True
        return os.path.realpath(os.path.join(directory, str(entry.get("file") or ""))) in self._built(tree)


def fold(groups: list[list[dict]]) -> list[dict]:
    """One entry per `file` across <groups>, given highest precedence first: the first entry whose tree
    compiles the file, else the first entry. Files keep the order of their first appearance."""
    graphs = BuildGraphs()
    claims: dict[object, list[dict]] = {}
    for entries in groups:
        for entry in entries:
            claims.setdefault(entry.get("file"), []).append(entry)
    return [next((entry for entry in held if graphs.compiles(entry)), held[0]) if len(held) > 1 else held[0]
            for held in claims.values()]
