#!/usr/bin/env python3
"""build_provenance.py — an act's configured build graph names only its own environment's trees and
its own conan home (DN-142.D30 (10) R20).

THE DEFECT THIS CLOSES, measured 2026-10-19 (WIP W559, malf `19fa219`). Inside the build environment a
compiling act linked its editable dependencies from the HOST build trees: the sanitizer replay's fuzz
tree named `insight-eidos/sift/build-clang21-asan/libinsight_sift.a`, an archive a host compiler had
written on 2026-10-12, beside module interfaces the image compiled from the current sources. One binary
held two `RankedChange` layouts, and ASan read the ODR violation as a stack-buffer-overflow. The route
was a recipe's `layout()` reading a conf that named the host tree; `19fa219` closed that route, and the
structural arm guarding it checks one argument on one install. A recipe reading another conf, a CMake
cache entry or a profile variable naming a host tree reopens the class with that arm green, so this
reads what the compiler and the linker are about to consume instead.

WHAT IS READ: `build.ninja` (every compile and link line, `-I`, `-isystem`, `-L`, archive and object
paths, `-fmodule-file=` and module-map paths) and `compile_commands.json` (what `malf lint` hands
clang-tidy). Every path in them, an absolute one as written and a relative one resolved against its
base (the tree for `build.ninja`, each entry's `directory` for the database), is judged by two rules:

  * A PATH COMPONENT NAMING A MALF BUILD TREE, `build-<key>` for a key of malf's profiles (`--keys`),
    MUST be `build-<key>.<environment>`, the act's own environment suffix (`--environment`). A bare
    `build-<key>` is a tree a host build wrote; another suffix is another image's.
  * A PATH INTO A CONAN HOME MUST BE INTO THE ACT'S OWN (`--conan-home`). A conan home is a directory
    holding `p/` and `version.txt`; the deepest such ancestor of a path is the home it is into, since
    an image's homes nest one per profile under their base. A path inside the act's own home is that
    home's business, and the build-tree rule does not reach into it.

The graph is read, not the binary: the refusal precedes the bytes, and a linked binary's provenance is
not recoverable in general (an archive member carries no path). A library found by a `-L` search is
caught by its directory.

EXIT 0 when every path is the act's own, 2 when any is not or a named file cannot be read: the verb
refuses before its first compile, the step records the act `unjudged` and stores nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REFUSED = 2
SEPARATORS = re.compile(r"[\s=,;:\"'()|]+")
ATTACHED_OPTIONS = ("-I", "-L", "-B", "-F")
NAMED_LIMIT = 10


class Judge:
    """The two rules over one act: its environment suffix, its conan home and malf's profile keys."""

    def __init__(self, environment: str, conan_home: Path, keys: list[str]) -> None:
        self.environment = environment
        self.own_home = os.path.realpath(conan_home)
        self.tree = re.compile(r"build-(?P<key>" + "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True))
                               + r")(?P<suffix>\..+)?")
        self.homes: dict[str, bool] = {}
        self.offences: dict[str, str] = {}
        self.occurrences = 0

    def is_home(self, directory: str) -> bool:
        if directory not in self.homes:
            self.homes[directory] = os.path.isdir(os.path.join(directory, "p")) and os.path.isfile(
                os.path.join(directory, "version.txt"))
        return self.homes[directory]

    def offence(self, path: str) -> str | None:
        """Why `path` is not the act's own, or None when it is."""
        ancestor = os.path.dirname(path)
        while ancestor not in ("/", ""):
            if self.is_home(ancestor):
                if os.path.realpath(ancestor) == self.own_home:
                    return None
                return f"into the conan home {ancestor}, not the act's own {self.own_home}"
            ancestor = os.path.dirname(ancestor)
        for component in path.split("/"):
            named = self.tree.fullmatch(component)
            if named and named.group("suffix") != f".{self.environment}":
                return (f"the build tree {component} is not this environment's "
                        f"build-{named.group('key')}.{self.environment}")
        return None

    def judge(self, word: str, base: str) -> None:
        for token in SEPARATORS.split(word):
            if token.startswith(ATTACHED_OPTIONS):
                token = token[2:]
            elif token.startswith("-") or token.startswith("$"):
                continue
            token = token.removeprefix("@")
            if "/" not in token and not token.startswith("build-"):
                continue
            path = os.path.normpath(token if token.startswith("/") else os.path.join(base, token))
            why = self.offence(path)
            if why is not None:
                self.occurrences += 1
                self.offences.setdefault(path, why)


def read_ninja(judge: Judge, manifest: Path) -> None:
    text = manifest.read_text(errors="replace").replace("$\n", " ").replace("$:", ":")
    judge.judge(text, str(manifest.parent))


def read_database(judge: Judge, database: Path) -> None:
    entries = json.loads(database.read_text(errors="replace"))
    if not isinstance(entries, list):
        raise ValueError(f"{database} is not a list of compile entries")
    for entry in entries:
        base = str(entry.get("directory") or database.parent)
        words = entry.get("arguments") or [entry.get("command", "")]
        for word in [*words, entry.get("file", ""), entry.get("output", "")]:
            judge.judge(str(word), base)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="build_provenance.py")
    parser.add_argument("--verb", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--conan-home", required=True, type=Path)
    parser.add_argument("--keys", required=True)
    parser.add_argument("files", nargs="+", type=Path)
    given = parser.parse_args(argv)
    judge = Judge(given.environment, given.conan_home, given.keys.split())
    prefix = f"malf {given.verb}: REFUSED (DN-142.D30 (10) R20) —"
    for named in given.files:
        try:
            if named.name == "build.ninja":
                read_ninja(judge, named)
            else:
                read_database(judge, named)
        except (OSError, ValueError) as unread:
            print(f"{prefix} the build graph {named} could not be read, so what this act links and includes "
                  f"is unknown: {unread}", file=sys.stderr)
            return REFUSED
    if not judge.offences:
        return 0
    first, why = next(iter(judge.offences.items()))
    print(f"{prefix} the build graph ({', '.join(map(str, given.files))}) names {len(judge.offences)} path(s) "
          f"outside this act's environment ({judge.occurrences} occurrence(s)); the first: {first} — {why}. "
          f"An act in the image builds over its own trees and its own conan home only, and nothing was compiled",
          file=sys.stderr)
    for path, reason in list(judge.offences.items())[1:NAMED_LIMIT]:
        print(f"  {path} — {reason}", file=sys.stderr)
    if len(judge.offences) > NAMED_LIMIT:
        print(f"  … and {len(judge.offences) - NAMED_LIMIT} more", file=sys.stderr)
    return REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
