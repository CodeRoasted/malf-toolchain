#!/usr/bin/env python3
"""malf platform-gate — which uncovered translation units the BUILD itself gates off this host.

`malf lint --all-files` walks the tree, and a TU absent from the compile database is a coverage
hole. One population is not: a source the build names only inside an `if(WIN32)` branch has no
compile command on Linux because the build declared so. This module answers that from the build's
own CMake text — never from a file name — and CMake itself evaluates each condition in script mode
(`cmake -P`), so no second reading of the predicate exists.

Usage: platform_gate.py <repo_root> <compile_commands.json> <file>...
Prints one line per file: `refused<TAB><file><TAB><reason>` or `missing<TAB><file><TAB><reason>`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

# note: a condition is judged only when every word in it is one of these or an operator; any other
# word (a project option, a quoted string) leaves the file `missing`, the fatal verdict.
PLATFORM_WORDS = frozenset({
    "WIN32", "UNIX", "APPLE", "LINUX",
    "CMAKE_HOST_WIN32", "CMAKE_HOST_UNIX", "CMAKE_HOST_APPLE", "CMAKE_HOST_LINUX",
})
OPERATOR_WORDS = frozenset({"NOT", "AND", "OR", "(", ")"})
SOURCE_DIR_VARS = ("${CMAKE_CURRENT_SOURCE_DIR}", "${CMAKE_CURRENT_LIST_DIR}")
CROSS_TARGET_FLAG = re.compile(r"(^|\s)(--target=|-target\s)")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BRACKET_OPEN = re.compile(r"\[(=*)\[")


@dataclass
class Command:
    name: str
    args: list[str]
    line: int


@dataclass
class Frame:
    conditions: list[list[str]]
    branch: int
    line: int


def _bracket_end(text: str, start: int) -> tuple[int, str] | None:
    match = BRACKET_OPEN.match(text, start)
    if match is None:
        return None
    close = "]" + match.group(1) + "]"
    end = text.find(close, match.end())
    end = len(text) if end < 0 else end
    return end + len(close), text[match.end():end]


def parse_commands(text: str) -> list[Command]:
    commands: list[Command] = []
    pos, line, size = 0, 1, len(text)
    while pos < size:
        char = text[pos]
        if char == "#":
            bracket = _bracket_end(text, pos + 1)
            stop = bracket[0] if bracket else text.find("\n", pos)
            stop = size if stop < 0 else stop
            line += text.count("\n", pos, stop)
            pos = stop
            continue
        ident = IDENTIFIER.match(text, pos)
        if ident is None:
            line += char == "\n"
            pos += 1
            continue
        after = ident.end()
        while after < size and text[after] in " \t":
            after += 1
        if after >= size or text[after] != "(":
            pos = ident.end()
            continue
        start_line = line
        args, pos, line = _parse_args(text, after + 1, line)
        commands.append(Command(ident.group(0).lower(), args, start_line))
    return commands


def _parse_args(text: str, pos: int, line: int) -> tuple[list[str], int, int]:
    args: list[str] = []
    depth, size = 0, len(text)
    while pos < size:
        char = text[pos]
        if char == "\n":
            line += 1
            pos += 1
        elif char in " \t\r":
            pos += 1
        elif char == "#":
            bracket = _bracket_end(text, pos + 1)
            stop = bracket[0] if bracket else text.find("\n", pos)
            stop = size if stop < 0 else stop
            line += text.count("\n", pos, stop)
            pos = stop
        elif char == "(":
            depth += 1
            args.append("(")
            pos += 1
        elif char == ")":
            if depth == 0:
                return args, pos + 1, line
            depth -= 1
            args.append(")")
            pos += 1
        elif char == '"':
            end = pos + 1
            while end < size and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            args.append(text[pos:end + 1])
            line += text.count("\n", pos, end)
            pos = end + 1
        elif (bracket := _bracket_end(text, pos)) is not None:
            args.append(text[pos:bracket[0]])
            line += text.count("\n", pos, bracket[0])
            pos = bracket[0]
        else:
            end = pos
            while end < size and text[end] not in ' \t\r\n()"#':
                end += 2 if text[end] == "\\" else 1
            args.append(text[pos:end])
            pos = end
    return args, pos, line


def mentions(cmakelists: str, targets: set[str]) -> list[tuple[str, int, list[Frame]]]:
    """Every (target, line, enclosing if-frames) a non-conditional command of the file names."""
    base = os.path.dirname(cmakelists)
    with open(cmakelists, encoding="utf-8", errors="replace") as handle:
        commands = parse_commands(handle.read())
    stack: list[Frame] = []
    found: list[tuple[str, int, list[Frame]]] = []
    for command in commands:
        if command.name == "if":
            stack.append(Frame([command.args], 0, command.line))
        elif command.name == "elseif" and stack:
            stack[-1].conditions.append(command.args)
            stack[-1].branch += 1
        elif command.name == "else" and stack:
            stack[-1].branch = len(stack[-1].conditions)
        elif command.name == "endif" and stack:
            stack.pop()
        else:
            for arg in command.args:
                token = arg.strip('"')
                for var in SOURCE_DIR_VARS:
                    token = token.replace(var, base)
                if not token or "${" in token or "$<" in token:
                    continue
                path = os.path.realpath(os.path.join(base, token))
                if path in targets:
                    snapshot = [Frame(list(f.conditions), f.branch, f.line) for f in stack]
                    found.append((path, command.line, snapshot))
    return found


def platform_only(frame: Frame) -> bool:
    judged = frame.conditions[:frame.branch + 1]
    return all(word in PLATFORM_WORDS or word in OPERATOR_WORDS for cond in judged for word in cond)


def branch_label(frame: Frame) -> str:
    if frame.branch == len(frame.conditions):
        return f"the else() of if({' '.join(frame.conditions[0])})"
    keyword = "if" if frame.branch == 0 else "elseif"
    return f"{keyword}({' '.join(frame.conditions[frame.branch])})"


def taken_branch(frame: Frame, workdir: str) -> int:
    """The branch CMake takes on this host: an index into the conditions, or -1 for `else`."""
    judged = frame.conditions[:frame.branch + 1]
    lines = []
    for index, cond in enumerate(judged):
        lines.append(f"{'if' if index == 0 else 'elseif'}({' '.join(cond)})\n  set(_malf_branch {index})\n")
    lines.append("else()\n  set(_malf_branch -1)\nendif()\nmessage(NOTICE \"${_malf_branch}\")\n")
    script = os.path.join(workdir, "probe.cmake")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write("".join(lines))
    result = subprocess.run(["cmake", "-P", script], capture_output=True, text=True, check=True)
    return int(result.stderr.strip().splitlines()[-1])


def verdicts(repo: str, database: str, files: list[str]) -> list[tuple[str, str, str]]:
    targets = {os.path.realpath(f): f for f in files}
    with open(database, encoding="utf-8") as handle:
        entries = json.load(handle)
    if any(CROSS_TARGET_FLAG.search(e.get("command", "") or " ".join(e.get("arguments", [])))
           for e in entries):
        why = "the database cross-compiles (--target), and cmake -P evaluates for the host"
        return [("missing", f, why) for f in files]
    listed = subprocess.run(["git", "-C", repo, "ls-files", "-z", "--", "*CMakeLists.txt", "*.cmake"],
                            capture_output=True, text=True, check=True).stdout
    found: dict[str, list[tuple[str, int, list[Frame]]]] = {path: [] for path in targets}
    for rel in filter(None, listed.split("\0")):
        cmakelists = os.path.join(repo, rel)
        for path, line, frames in mentions(cmakelists, set(targets)):
            found[path].append((os.path.relpath(cmakelists, repo), line, frames))
    out: list[tuple[str, str, str]] = []
    with tempfile.TemporaryDirectory() as workdir:
        for path, original in targets.items():
            if not found[path]:
                out.append(("missing", original, "no CMake file of the repo names it"))
                continue
            reasons = []
            for rel, line, frames in found[path]:
                gate = next((f for f in frames if platform_only(f)
                             and taken_branch(f, workdir) != (f.branch if f.branch < len(f.conditions) else -1)),
                            None)
                if gate is None:
                    reasons = []
                    break
                reasons.append(f"{rel}:{line} sits in {branch_label(gate)} (if at line {gate.line}),"
                               " a branch CMake does not take on this host")
            if reasons:
                out.append(("refused", original, "; ".join(reasons)))
            else:
                out.append(("missing", original, "a CMake file names it outside any platform branch"))
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: platform_gate.py <repo_root> <compile_commands.json> <file>...", file=sys.stderr)
        return 2
    for verdict, path, reason in verdicts(argv[0], argv[1], argv[2:]):
        print(f"{verdict}\t{path}\t{reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
