#!/usr/bin/env python3
"""The malf files a keyed step executes: its CLOSURE (DN-142.D29), the `malf` member of its definition.

A step's definition used to carry malf's whole tracked tree, so every malf commit re-keyed every step,
even a commit to a workflow no step executes. The member is now the files the step can reach from
two seeds, and the entry names of each directory holding one of them:

  D, the driver   the file the act's argv runs when it runs malf: `malf`, or a tracked malf file
                  (`artefact_store.py ingest`). An argv that runs no malf file has no D.
  A, the act      the tracked malf paths the step's own declared inputs name: a consumer file of its
                  exported source tree naming `${MALF_TOOLCHAIN_DIR}/<path>`, or a symlink of that tree
                  resolving into malf.

ONE GRAMMAR, ANCHORED, NEVER A BARE WORD. File `f` reaches tracked path `p` when
  * `f` names `p` behind a malf-root anchor (`$MALF_DIR/`, `${MALF_TOOLCHAIN_DIR}/`, the `path:` a
    workflow's own step stages or checks malf out at) or behind the file's own directory (a shell
    variable assigned `$(cd "$(dirname "${BASH_SOURCE[0]}")<rel>" && pwd)`, a composite action's
    `${{ github.action_path }}/` and `$GITHUB_ACTION_PATH/`, a variable a step's `env:` binds to it
    or a shell re-binds by `cd`, cmake's `${CMAKE_CURRENT_LIST_DIR}/`, Python's
    `Path(__file__).parent / "<name>"` and
    `Path(__file__).with_name("<name>")`, a conan profile's `{{ profile_dir }}/`), `..` normalized and a
    path leaving malf's root reaching nothing;
  * `f` is Python and imports, at any depth of the file, a module that resolves to `p` in malf's
    root or in `f`'s own directory;
  * `f` is a tracked symlink whose target resolves to `p`.
A reference to a tracked DIRECTORY reaches every tracked file under it. The closure is the fixpoint
of the edges from the seeds. A bare word (`tests` in a message) reaches nothing: matching malf's text
by name would pull 64 workflow files into every key (measured, DN-142.D29 (4)).

AN ANCHORED GRAMMAR CAN MISS A COMPUTED PATH (`"$MALF_DIR/$name"`), and that is by design: the key is
sound because the step's Landlock domain grants exactly its closure, so a read the grammar missed is
denied and the step refused, never stored (DN-142.D29 (4), (5)). Precision is the grammar's job;
soundness is the kernel's.

    malf_closure.py member <malf-dir> [--argv <word>]... [--act-root <dir>]...
        the member over the working tree's tracked files, as canonical JSON
    malf_closure.py selftest
        every arm over a fixture malf tree
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SYMLINK_MODE = "120000"
ROOT = "."
DRIVER = "malf"
# The anchors every malf file and every consumer file may name malf by: the driver's own directory
# and the variable a consumer's build is handed for the same tree.
ROOT_ANCHOR = re.compile(r"\$\{?(?:MALF_DIR|MALF_TOOLCHAIN_DIR)\}?/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
CONSUMER_ANCHOR = re.compile(r"\$\{?MALF_TOOLCHAIN_DIR\}?/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
# The naming file's own directory as a shell spells it, and as a composite action does (its own
# `${{ github.action_path }}` and `$GITHUB_ACTION_PATH`).
SELF_DIR = r'(?:\$\(dirname "(?:\$\{BASH_SOURCE\[0\]\}|\$0)"\)|\$\{\{ github\.action_path \}\}|\$\{?GITHUB_ACTION_PATH\}?)'
ACTION_SELF = re.compile(r"(?:\$\{\{ github\.action_path \}\}|\$\{?GITHUB_ACTION_PATH\}?)/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
# A variable bound to that directory: a step's `env:` entry `NAME: ${{ github.action_path }}`, or a
# shell's `NAME="$(cd "<own directory or a bound variable><tail>" && pwd)"`, with the tail `cd` applies.
YAML_SELF = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*):\s*\$\{\{ github\.action_path \}\}\s*$", re.M)
SHELL_BOUND = r'(?P<name>[A-Za-z_][A-Za-z0-9_]*)="\$\(cd "(?P<anchor>{anchor})(?P<tail>[^"]*)" && pwd\)'
SHELL_SELF = re.compile(SHELL_BOUND.format(anchor=SELF_DIR))
# A workflow's own copy of malf: a step that `uses:` malf's `stage-malf` action, or an `actions/checkout`
# whose `repository:` is malf-toolchain, puts malf's tree at its `with:` mapping's `path:` (`stage-malf`'s
# default `malf`), and the rest of that file spells malf's root by it (`.malf/store_publish.py`).
MALF_CHECKOUT = re.compile(r"^(?P<indent>[ \t]*)(?:-[ \t]+)?(?P<key>repository|uses):\s*['\"]?CodeRoasted/malf-toolchain"
                           r"(?P<rest>/\.github/actions/stage-malf@\S+|)['\"]?\s*$", re.M)
STAGE_DEFAULT = "malf"
CHECKOUT_PATH = re.compile(r"^[ \t]*path:\s*['\"]?(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*?)/?['\"]?\s*$")
# A conan profile's own directory, as conan renders it into the profile's Jinja.
PROFILE_SELF = re.compile(r"\{\{\s*profile_dir\s*\}\}/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
CMAKE_SELF = re.compile(r"\$\{CMAKE_CURRENT_LIST_DIR\}/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
PYTHON_SELF = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?\.parent(?P<segments>(?:\s*/\s*[\"'][^\"'\n]+[\"'])+)")
PYTHON_SIBLING = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?\.with_name\(\s*[\"'](?P<name>[^\"'\n]+)[\"']")
SEGMENT = re.compile(r"[\"']([^\"'\n]+)[\"']")
# A path's tail that is prose, never path: the full stop or comma closing a sentence that named it.
PROSE_TAIL = ".,"


class ClosureError(RuntimeError):
    """A file the grammar must read could not be read as what it claims to be."""


@dataclass
class Tree:
    """malf's tracked files: path -> (mode, blob id), and a reader of each one's bytes."""

    entries: dict[str, tuple[str, str]]
    read: "callable"

    @classmethod
    def worktree(cls, malf_dir: Path) -> "Tree":
        """The tracked files AS THEY ARE ON DISK, through a private index so the shared one is
        never written: a definition is the code that runs."""
        with tempfile.TemporaryDirectory(prefix="malf_closure.") as scratch:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
            _git(malf_dir, env, "read-tree", "HEAD")
            _git(malf_dir, env, "add", "--update", "--", ".")
            listed = _git(malf_dir, env, "ls-files", "--stage", "-z")
        entries = {}
        for record in listed.split("\0"):
            if record:
                meta, path = record.split("\t", 1)
                mode, blob, _stage = meta.split()
                entries[path] = (mode, blob)

        def read(path: str) -> bytes:
            target = malf_dir / path
            if entries[path][0] == SYMLINK_MODE:
                return os.readlink(target).encode()
            return target.read_bytes()

        return cls(entries, read)

    @classmethod
    def at(cls, malf_dir: Path, rev: str) -> "Tree":
        """The tracked files at commit `rev`, read from git's objects."""
        listed = _git(malf_dir, None, "ls-tree", "-r", "-z", rev)
        entries = {}
        for record in listed.split("\0"):
            if record:
                meta, path = record.split("\t", 1)
                mode, _kind, blob = meta.split()
                entries[path] = (mode, blob)

        def read(path: str) -> bytes:
            return subprocess.run(["git", "-C", str(malf_dir), "cat-file", "blob", entries[path][1]],
                                  capture_output=True, check=True).stdout

        return cls(entries, read)

    def under(self, directory: str) -> list[str]:
        """Every tracked path under `directory` (itself, when it is a file)."""
        if directory in self.entries:
            return [directory]
        prefix = directory.rstrip("/") + "/"
        return [path for path in self.entries if path.startswith(prefix)]


def _git(where: Path, env: dict | None, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=env)
    if done.returncode != 0:
        raise ClosureError(f"git {' '.join(args)} failed in {where}: {done.stderr.strip()}")
    return done.stdout


def _normal(base: str, relative: str) -> str | None:
    """`relative` joined onto malf-relative `base`, normalized; None when it leaves malf's root."""
    if relative.endswith(tuple(PROSE_TAIL)) and not relative.endswith(".."):
        relative = relative[:-1]
    joined = posixpath.normpath(posixpath.join(base, relative or "."))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def _targets(tree: Tree, path: str) -> set[str]:
    """The tracked files `path` names: the file, or every tracked file under the directory."""
    return set(tree.under(path)) if path is not None else set()


def edges(tree: Tree, path: str) -> set[str]:
    """Every tracked file `path` reaches by one edge of the grammar (module docstring)."""
    mode, _blob = tree.entries[path]
    data = tree.read(path)
    here = posixpath.dirname(path) or ROOT
    if mode == SYMLINK_MODE:
        return _targets(tree, _normal(here, data.decode()))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return set()
    reached: set[str] = set()
    for match in ROOT_ANCHOR.finditer(text):
        reached |= _targets(tree, _normal(ROOT, match["path"]))
    for root in _checkout_roots(text):
        anchor = re.compile(r"(?<![A-Za-z0-9_./-])" + re.escape(root) + r"/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
        for match in anchor.finditer(text):
            reached |= _targets(tree, _normal(ROOT, match["path"]))
    for match in ACTION_SELF.finditer(text):
        reached |= _targets(tree, _normal(here, match["path"]))
    bases = {bound["name"]: here for bound in YAML_SELF.finditer(text)}
    bound_dirs = SELF_DIR if not bases else SELF_DIR[:-1] + "".join(
        r"|\$\{?" + re.escape(name) + r"\}?" for name in sorted(bases)) + ")"
    for bound in re.finditer(SHELL_BOUND.format(anchor=bound_dirs), text):
        origin = bases.get(re.sub(r"[${}]", "", bound["anchor"]), here)
        base = _normal(origin, "." + bound["tail"]) if bound["tail"] else origin
        if base is not None:
            bases[bound["name"]] = base
    for name, base in sorted(bases.items()):
        anchor = re.compile(r"\$\{?" + re.escape(name) + r"\}?/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
        for match in anchor.finditer(text):
            reached |= _targets(tree, _normal(base, match["path"]))
    for match in CMAKE_SELF.finditer(text):
        reached |= _targets(tree, _normal(here, match["path"]))
    for match in PROFILE_SELF.finditer(text):
        reached |= _targets(tree, _normal(here, match["path"]))
    if path.endswith(".py") or text.startswith("#!/usr/bin/env python"):
        for match in PYTHON_SELF.finditer(text):
            reached |= _targets(tree, _normal(here, "/".join(SEGMENT.findall(match["segments"]))))
        for match in PYTHON_SIBLING.finditer(text):
            reached |= _targets(tree, _normal(here, match["name"]))
        reached |= _imports(tree, path, here, text)
    reached.discard(path)
    return reached


def _checkout_roots(text: str) -> set[str]:
    """Where each step of a workflow that copies malf puts it (`MALF_CHECKOUT`): the `path:` of the
    step's own lines — those after its `uses:` or around its `repository:` until a line indented less
    than the step's keys or the next step — else `stage-malf`'s default."""
    roots: set[str] = set()
    lines = text.splitlines()
    for match in MALF_CHECKOUT.finditer(text):
        if match["key"] == "uses" and not match["rest"]:
            continue
        start = text.count("\n", 0, match.start())
        indent = len(match["indent"]) + (2 if match.group(0).lstrip().startswith("-") else 0)
        floor = indent if match["key"] == "uses" else indent - 2
        found_path = None
        for line in lines[start + 1:]:
            depth = len(line) - len(line.lstrip())
            if line.strip() and (depth < floor or (depth <= floor and line.lstrip().startswith("- "))):
                break
            found = CHECKOUT_PATH.match(line)
            if found:
                found_path = found["path"]
        if found_path is None and match["key"] == "repository":
            for line in reversed(lines[:start]):
                depth = len(line) - len(line.lstrip())
                if line.strip() and depth < indent:
                    break
                found = CHECKOUT_PATH.match(line)
                if found:
                    found_path = found["path"]
        roots.add(found_path or STAGE_DEFAULT if match["key"] == "uses" else found_path or "")
    roots.discard("")
    return roots


def _imports(tree: Tree, path: str, here: str, text: str) -> set[str]:
    """The tracked modules `path` imports anywhere in it, function-level imports included, resolved
    in malf's root and in its own directory."""
    try:
        parsed = ast.parse(text, filename=path)
    except SyntaxError as broken:
        raise ClosureError(f"{path} is not Python the grammar can read: {broken}") from broken
    names: list[str] = []
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                names += [f"{base}.{alias.name}" if base else alias.name for alias in node.names]
            else:
                names += [base] + [f"{base}.{alias.name}" for alias in node.names]
    reached: set[str] = set()
    for name in names:
        parts = name.split(".")
        for directory in {ROOT, here}:
            for depth in range(1, len(parts) + 1):
                stem = posixpath.normpath(posixpath.join(directory, *parts[:depth]))
                for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
                    if candidate in tree.entries:
                        reached.add(candidate)
    return reached


def driver_seeds(tree: Tree, argv: list[str]) -> set[str]:
    """D: the file the act's argv runs when it runs malf — `malf`, or a tracked malf file named by
    its path relative to malf or by its bare name — else nothing."""
    if not argv:
        return set()
    program = argv[0]
    if program == DRIVER:
        return {DRIVER}
    return {program} if program in tree.entries else set()


def act_seeds(tree: Tree, malf_dir: Path, roots: list[Path]) -> set[str]:
    """A: the tracked malf paths a file under `roots` (the step's exported source tree) names behind
    `${MALF_TOOLCHAIN_DIR}/`, and the tracked malf files a symlink there resolves into."""
    home = malf_dir.resolve()
    seeds: set[str] = set()
    for root in roots:
        for directory, subdirectories, files in os.walk(root, followlinks=False):
            for name in files + [entry for entry in subdirectories if Path(directory, entry).is_symlink()]:
                candidate = Path(directory, name)
                if candidate.is_symlink():
                    resolved = Path(os.path.normpath(Path(directory, os.readlink(candidate))))
                    if resolved == home or home in resolved.parents:
                        seeds |= _targets(tree, _normal(ROOT, str(resolved.relative_to(home))))
                    continue
                try:
                    text = candidate.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for match in CONSUMER_ANCHOR.finditer(text):
                    seeds |= _targets(tree, _normal(ROOT, match["path"]))
    return seeds


@dataclass
class Closure:
    """The files a step reaches, by path and blob, and the entry names of each directory holding one."""

    files: dict[str, str] = field(default_factory=dict)
    listings: dict[str, list[str]] = field(default_factory=dict)

    def member(self) -> dict[str, object]:
        """`definition.malf` (DN-142.D29 (2)): `{files: [[path, blob]…], listings: {dir: [names]…}}`."""
        return {"files": [[path, blob] for path, blob in sorted(self.files.items())],
                "listings": {directory: names for directory, names in sorted(self.listings.items())}}


def closure(tree: Tree, seeds: set[str], pruned=lambda path: False) -> Closure:
    """The fixpoint of the grammar's edges from `seeds`, and the listings of the directories its
    files lie in; a file `pruned` answers True for is neither reached nor expanded through. A seed that
    is not tracked raises: a seed is read off the key's own inputs."""
    absent = sorted(seed for seed in seeds if seed not in tree.entries)
    if absent:
        raise ClosureError(f"seed(s) not tracked in malf: {', '.join(absent)}")
    reached, pending = set(seeds), sorted(seeds)
    while pending:
        path = pending.pop()
        for target in sorted(path for path in edges(tree, path) - reached if not pruned(path)):
            reached.add(target)
            pending.append(target)
    result = Closure(files={path: tree.entries[path][1] for path in reached})
    for path in reached:
        directory = posixpath.dirname(path) or ROOT
        if directory not in result.listings:
            prefix = "" if directory == ROOT else directory + "/"
            result.listings[directory] = sorted({entry[len(prefix):].split("/", 1)[0]
                                                 for entry in tree.entries if entry.startswith(prefix)})
    return result


def member(malf_dir: Path, argv: list[str], act_roots: list[Path], tree: Tree | None = None) -> dict[str, object]:
    """The step's `definition.malf`: the closure of its driver and its act over malf's working tree."""
    tree = Tree.worktree(malf_dir) if tree is None else tree
    return closure(tree, driver_seeds(tree, argv) | act_seeds(tree, malf_dir, act_roots)).member()


# ── selftest ────────────────────────────────────────────────────────────────────────────────────


def _fixture(base: Path) -> Path:
    """A fixture malf tree, committed, holding one instance of every edge and of every non-edge."""
    malf = base / "malf"
    files = {
        "malf": '#!/usr/bin/env bash\nMALF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                'python3 "$MALF_DIR/store.py" "$@"\ncp -r "$MALF_DIR/profiles/" home\n'
                'conan create --profile "$MALF_DIR/config/writer.profile"\n'
                'echo "tests are run by tests/run.sh"\nfor name in a b; do cat "$MALF_DIR/$name"; done\n',
        "store.py": "import json\n\ndef late():\n    import helper\n    return helper\n",
        "helper.py": "from pathlib import Path\nDATA = Path(__file__).resolve().parent / 'config' / 'data.txt'\n",
        "config/data.txt": "x\n",
        "config/writer.profile": "[buildenv]\nPATH=+(path){{ profile_dir }}/../wrap\nX={{ profile_dir }}/../../outside\n",
        "wrap/malf-cc": "#!/usr/bin/env bash\n",
        "profiles/one": "[settings]\n",
        "profiles/two": "[settings]\n",
        "codegen.py": "import cycle_a\n",
        "cycle_a.py": "import cycle_b\n",
        "cycle_b.py": "import cycle_a\n",
        "tests/run.sh": "echo never reached\n",
        ".github/workflows/ci.yml": "run: ./malf build\n",
        ".github/workflows/publish.yml": "jobs:\n  publish:\n    steps:\n      - name: stage\n"
                                         "        uses: CodeRoasted/malf-toolchain/.github/actions/stage-malf@0123\n"
                                         "        with:\n          path: .malf\n"
                                         "      - run: python3 .malf/store.py stage\n",
        ".github/actions/sbom/action.yml": "runs:\n  steps:\n    - env:\n        ACTION_PATH: ${{ github.action_path }}\n"
                                           "      run: |\n        ROOT=\"$(cd \"$ACTION_PATH/../../..\" && pwd)\"\n"
                                           "        python3 \"$ROOT/codegen.py\"\n"
                                           "    - run: ${{ github.action_path }}/../../../helper.py\n",
        "unreached.py": "print('nobody names me')\n",
    }
    for path, text in files.items():
        (malf / path).parent.mkdir(parents=True, exist_ok=True)
        (malf / path).write_text(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "f", "GIT_AUTHOR_EMAIL": "f@f", "GIT_COMMITTER_NAME": "f",
           "GIT_COMMITTER_EMAIL": "f@f", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(malf), *args], check=True, env=env, capture_output=True)
    consumer = base / "consumer"
    consumer.mkdir()
    (consumer / "CMakeLists.txt").write_text('execute_process(COMMAND python3 ${MALF_TOOLCHAIN_DIR}/codegen.py)\n')
    return malf


def selftest() -> int:
    passed = failed = 0

    def arm(label: str, got: object, want: object) -> None:
        nonlocal passed, failed
        if got == want:
            passed += 1
            print(f"  ok   {label}")
        else:
            failed += 1
            print(f"  FAIL {label}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory(prefix="malf_closure.selftest.") as held:
        base = Path(held)
        malf = _fixture(base)
        tree = Tree.worktree(malf)
        driven = closure(tree, driver_seeds(tree, ["malf", "store-create"]))
        arm("an anchored reference, a function-level import and a directory reference each reach their "
            "target; a Path(__file__) segment chain reaches its file",
            sorted(driven.files), ["config/data.txt", "config/writer.profile", "helper.py", "malf", "profiles/one",
                                   "profiles/two", "store.py", "wrap/malf-cc"])
        arm("a bare word `tests` in a message reaches nothing, nor a computed `$MALF_DIR/$name`, nor a "
            "workflow no file names", [path for path in driven.files if path.startswith((".github", "tests"))], [])
        arm("a conan profile's `{{ profile_dir }}/../wrap` reaches every file under wrap/, and one leaving "
            "malf's root reaches nothing", sorted(edges(tree, "config/writer.profile")), ["wrap/malf-cc"])
        arm("a composite action reaches malf through its own directory: `${{ github.action_path }}/`, and a "
            "variable bound to it in a step's env and re-bound by `cd`",
            sorted(edges(tree, ".github/actions/sbom/action.yml")), ["codegen.py", "helper.py"])
        arm("a workflow reaches malf through the path its own step stages malf at (`.malf/store.py`)",
            sorted(edges(tree, ".github/workflows/publish.yml")), ["store.py"])
        arm("a pruned file is neither reached nor expanded through",
            sorted(closure(tree, {".github/actions/sbom/action.yml"}, lambda path: path == "codegen.py").files),
            [".github/actions/sbom/action.yml", "config/data.txt", "helper.py"])
        act = act_seeds(tree, malf, [base / "consumer"])
        arm("a consumer CMake file naming ${MALF_TOOLCHAIN_DIR}/codegen.py seeds codegen.py into A",
            sorted(act), ["codegen.py"])
        arm("a cycle of imports terminates, each member reached once",
            sorted(closure(tree, act).files), ["codegen.py", "cycle_a.py", "cycle_b.py"])
        arm("each directory holding a closure file is listed by its entry names",
            driven.listings, {".": [".github", "codegen.py", "config", "cycle_a.py", "cycle_b.py", "helper.py",
                                     "malf", "profiles", "store.py", "tests", "unreached.py", "wrap"],
                              "config": ["data.txt", "writer.profile"], "profiles": ["one", "two"],
                              "wrap": ["malf-cc"]})
        arm("an argv that runs no malf file has no driver seed; a tracked malf file as argv[0] is its own seed",
            (driver_seeds(tree, ["pharos", "check"]), driver_seeds(tree, ["store.py", "ingest"])),
            (set(), {"store.py"}))
        arm("the member is the same read at the commit as on the clean working tree",
            closure(Tree.at(malf, "HEAD"), {"malf"}).member(), closure(tree, {"malf"}).member())
        refused = None
        try:
            closure(tree, {"absent.py"})
        except ClosureError as error:
            refused = str(error)
        arm("a seed that is not tracked is refused, named", refused, "seed(s) not tracked in malf: absent.py")
    print(f"malf_closure selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="malf_closure.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    shown = verbs.add_parser("member")
    shown.add_argument("malf_dir", type=Path)
    shown.add_argument("--argv", action="append", default=[])
    shown.add_argument("--act-root", action="append", default=[], type=Path)
    verbs.add_parser("selftest")
    args = parser.parse_args(argv)
    if args.verb == "selftest":
        return selftest()
    print(json.dumps(member(args.malf_dir, args.argv, args.act_root), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
