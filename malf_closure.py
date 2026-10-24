#!/usr/bin/env python3
"""malf's executed-surface grammar and the Landlock domain of a keyed store act (DN-142.D29).

THE GRAMMAR. The malf files reachable from a seed set, by one anchored static grammar. Its consumer
is `pin_coherence`'s executed-surface derivation (INV-17's currency watcher), which seeds it with
`.github` and `malf` at the reach's pin; it lives here because it describes malf's own spellings and
moves with them. It keys nothing: every key names malf whole, as the git tree of the malf the step
runs (DN-142.D29 (2), `artefact_store.definition_member`).

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
of the edges from the seeds. A bare word (`tests` in a message) reaches nothing. A computed path
(`"$MALF_DIR/$name"`) reaches nothing either, so malf spells each malf file it reads as one literal
anchored path (DN-142.D29 (7)): a file the grammar cannot see goes unwatched, silently.

THE DOMAIN (DN-142.D29 (5), (8)). A keyed act runs in a Landlock domain that DENIES the ground a key
member identifies piece by piece, finer than whole: the workspace root (repositories key through
`sources` and `test_package`, per package), every conan home but the step's own (packages key through
`upstream` and `third_party`, per node), the store root (records enter only as output digests) and
every corpus root (corpora key through `data`, per mount). Inside that ground it RE-GRANTS the step's
share: malf's whole checkout, the step's own repository (its export reads the recipe and derives its
exports from git), its step home, its scratch, each corpus mount its own `data` names, and the
parameter files its command line names. Everything else stays readable: it is the `system` member's
ground, keyed whole. The act WRITES only into locations of its own, a TMPDIR and a HOME under the walk's
scratch: every ancestor of a denied root (`$HOME` among them, which holds the workspace, `~/.conan2` and
the store) is list-only, so a domain whose `/tmp`, TMPDIR or HOME holds denied ground is refused before
the act (DN-142.D30 (9) R8). Where `strace` is on the PATH the act's denials are COUNTED, and a counted one
refuses the act: nothing of it is stored, and the refusal names each path.

    malf_closure.py confine <malf-dir> --tally <file> --workspace <dir> --store <dir>
                            [--conan-home <dir>]... [--corpus-gates <file> --package <name>]
                            [--parameter-file <file>]... --act-root <dir> --home <dir> --scratch <dir>
                            [--unconfined] -- <command> [<argument>...]
        <command> in its step's domain, with a TMPDIR and a HOME of its own, empty, under <scratch>
        (removed when it ends); exit the command's own, DENIED (3) refused for a counted denial, or 2
        refused before it ran (a domain in which /tmp, that TMPDIR or that HOME is an ancestor of a
        denied root or lies in the denied ground among them). --unconfined runs it with no domain, in
        its own locations all the same, its tally `unconfined` (a package declaring itself unconfined)
    malf_closure.py count-denials <job> <verdict>
        the denial counter over its stdin, the tracer's output piped into it by `confine` (the trace never
        lands on disk): <job> holds the domain and its argv, <verdict> receives the count and the paths
    malf_closure.py selftest
        every arm over a fixture malf tree and a fixture workspace
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SYMLINK_MODE = "120000"
ROOT = "."
# The anchors every malf file and every consumer file may name malf by: the driver's own directory
# and the variable a consumer's build is handed for the same tree.
ROOT_ANCHOR = re.compile(r"\$\{?(?:MALF_DIR|MALF_TOOLCHAIN_DIR)\}?/(?P<path>[A-Za-z0-9_.][A-Za-z0-9_./-]*)")
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
    """malf's tracked files at one commit: path -> (mode, blob id), and a reader of each one's bytes."""

    entries: dict[str, tuple[str, str]]
    read: "callable"

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


@dataclass(frozen=True)
class Closure:
    """The tracked files a closure reached: the executed surface `pin_coherence` reads."""

    files: frozenset[str]


def closure(tree: Tree, seeds: set[str], pruned=lambda path: False) -> Closure:
    """The fixpoint of the grammar's edges from `seeds`; a file `pruned` answers True for is neither
    reached nor expanded through. A seed that is not tracked raises."""
    absent = sorted(seed for seed in seeds if seed not in tree.entries)
    if absent:
        raise ClosureError(f"seed(s) not tracked in malf: {', '.join(absent)}")
    reached, pending = set(seeds), sorted(seeds)
    while pending:
        path = pending.pop()
        for target in sorted(path for path in edges(tree, path) - reached if not pruned(path)):
            reached.add(target)
            pending.append(target)
    return Closure(frozenset(reached))


# ── the domain (DN-142.D29 (5), (8)) ────────────────────────────────────────────────────────────────

SANDBOX = "landlock_sandbox.py"
# The exit of an act a counted denial refused: nothing of it is stored (DN-142.D29 (8)).
DENIED = 3
# The exit of an act refused before it ran: its domain could not be built, nothing was judged.
REFUSED_BUILD = 2
# What a tally file says of an act: its denials were counted, and how many; nothing counted them; or it
# ran with no domain, its package declaring itself unconfined (DN-142.D29 (9)).
COUNTED, UNCOUNTED, UNCONFINED = "counted", "uncounted", "unconfined"
# The directory a tool that ignores TMPDIR writes into (DN-142.D30 (9) R8).
SYSTEM_TMP = Path("/tmp")
# A Landlock denial as the tracer prints it: the failed call's path, and EACCES.
TRACED_CALL = re.compile(r"^\d+ +\w+\((?P<args>.*)\) += -1 EACCES")
TRACED_FD = re.compile(r"^(?:-?\d+|AT_FDCWD)<(?P<base>[^>]*)>")
TRACED_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
# The verb the tracer's output command runs: the counter over the piped trace (`count_piped`).
COUNT_VERB = "count-denials"
# The errno every TRACED_CALL names: a line without it holds no denial, and is skipped unparsed.
DENIAL_MARK = "EACCES"


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _outermost(paths: list[Path]) -> tuple[Path, ...]:
    """`paths` without duplicates and without any path lying within another of them, sorted."""
    unique = sorted(set(paths))
    return tuple(path for path in unique if not any(other != path and _within(path, other) for other in unique))


@dataclass(frozen=True)
class Domain:
    """A step's domain: the resolved roots it denies, and the resolved roots inside them it re-grants."""

    denied: tuple[Path, ...]
    granted: tuple[Path, ...]

    def withholds(self, path: Path) -> bool:
        """Whether a read of `path` is denied: it lies under a denied root and under no grant."""
        return any(_within(path, root) for root in self.denied) and not any(_within(path, root) for root in self.granted)


def repository_of(act_root: Path) -> list[Path]:
    """The git checkout holding `act_root` (a create's recipe) and its git directory, wherever that lives
    (a submodule's lies under its superproject's `.git/modules`), else `act_root` itself (a `conan build`
    step's source, materialized outside every checkout)."""
    found = subprocess.run(["git", "-C", str(act_root), "rev-parse", "--path-format=absolute", "--show-toplevel",
                            "--git-common-dir"], capture_output=True, text=True)
    lines = found.stdout.split()
    return [Path(line) for line in lines] if found.returncode == 0 and len(lines) == 2 else [act_root]


def corpus_mounts(document: Path, package: str | None) -> tuple[list[Path], list[Path]]:
    """(every mount the corpus gates document names, the mounts of `package`'s own legs): the
    document's `packages: {<name>: [{leg, suites, environment: {<variable>: <absolute path>}}]}`.
    raises ClosureError on a document that is not that shape or a mount that is not absolute."""
    try:
        packages = json.loads(document.read_text()).get("packages", {})
        named = {name: [Path(value) for leg in legs for value in leg["environment"].values()]
                 for name, legs in packages.items()}
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as unread:
        raise ClosureError(f"the corpus gates document {document} cannot be read as its mounts: {unread!r}") from unread
    relative = sorted(str(path) for paths in named.values() for path in paths if not path.is_absolute())
    if relative:
        raise ClosureError(f"the corpus gates document {document} names mount(s) by a relative path: {', '.join(relative)}")
    return [path for paths in named.values() for path in paths], named.get(package or "", [])


def step_domain(malf_dir: Path, workspace: Path, store: Path, conan_homes: list[Path], home: Path, scratch: Path,
                act_root: Path, corpus_gates: Path | None = None, package: str | None = None,
                parameter_files: list[Path] | tuple = ()) -> Domain:
    """The domain of one keyed act (DN-142.D29 (5)). A denied root that does not exist denies nothing
    and is dropped; a granted root that does not exist is refused (ClosureError), and so is a step
    whose repository is the workspace root itself, since its grant would deny nothing."""
    every_mount, own_mounts = corpus_mounts(corpus_gates, package) if corpus_gates is not None else ([], [])
    own_home = home.resolve()
    denied = [path.resolve() for path in [workspace, store, *conan_homes, *every_mount]
              if path.exists() and path.resolve() != own_home]
    repository, *git_dir = repository_of(act_root)
    if repository.resolve() == workspace.resolve():
        raise ClosureError(f"the act's repository {repository} is the workspace root itself, so its domain would "
                           "deny no sibling repository (DN-142.D29 (5))")
    granted = [malf_dir, repository, *git_dir, home, scratch, *own_mounts, *parameter_files]
    absent = sorted(str(path) for path in granted if not path.exists())
    if absent:
        raise ClosureError(f"the step's domain grants root(s) that do not exist: {', '.join(absent)}")
    return Domain(_outermost(denied), _outermost([path.resolve() for path in granted]))


def domain_argv(domain: Domain) -> list[str]:
    """`landlock_sandbox.py`'s arguments for `domain`, reads alone confined. Every entry beside a denied
    root and beside each of its ancestors is readable whole (a symlink there resolving into the denied
    ground is left ungranted, so a read through it is the denied read it is); each ancestor is
    LISTABLE, which Landlock grants beneath it, so the domain withholds file reads and never entry
    names (a walk of the workspace lists its repositories); each re-grant inside the ground is readable."""
    denied = set(domain.denied)
    ancestors = sorted({ancestor for root in domain.denied for ancestor in root.parents})
    argv = ["--reads-only"]
    for ancestor in ancestors:
        argv += ["--list", str(ancestor)]
        for entry in sorted(os.listdir(ancestor)):
            path = ancestor / entry
            if path in denied or path in ancestors:
                continue
            if path.is_symlink():
                target = path.resolve()
                if any(_within(target, root) or _within(root, target) for root in domain.denied):
                    continue
            argv += ["--read", str(path)]
    for root in domain.granted:
        if any(_within(root, denied_root) for denied_root in domain.denied):
            argv += ["--read", str(root)]
    return argv


def _read_roots(argv: list[str]) -> tuple[Path, ...]:
    """The roots `argv` (a `domain_argv`) grants read beneath, each resolved as Landlock resolves it."""
    return tuple(Path(os.path.realpath(root)) for flag, root in zip(argv, argv[1:]) if flag == "--read")


@dataclass(frozen=True)
class Denials:
    """What the counter found in one trace: how many reads the domain refused, and each refused path once, in
    the order first refused, split by whether its real location lies in the domain's denied ground; and how
    many lines of trace it read, the population its verdict is over."""

    count: int
    ground: tuple[str, ...]
    beside: tuple[str, ...]
    lines: int


def counted_denials(trace: Iterable[str], domain: Domain, argv: list[str]) -> Denials:
    """Each path the traced act was denied (EACCES) whose real location the domain that ran (`argv`, the
    `domain_argv` it was built from) grants no read of, as the tracer printed it, with whether that location
    lies in the domain's denied ground. A domain built from a listing grants each entry beside a denied root's
    ancestors as it stood when the act started, so an entry created there later (a temporary file in a
    directory holding another conan home) is withheld too, and so is a read through a symlink resolving into
    the denied ground: each is counted, never only the denied ground itself. An EACCES at a location the domain
    grants is the file's own permission, never counted.
    A build's trace grows with its translation units times its include directories, to gigabytes, so it is
    READ AS A STREAM — `trace` is the tracer's lines as they arrive, through a pipe (`_confined`), never a
    file — a record judged only when its line names EACCES at all; the read roots are an index, each denial
    looking up its own ancestors, never a scan of every root; and a path refused again is counted, not held
    again. Memory is bounded by the distinct refused paths, never by the trace."""
    readable = frozenset(_read_roots(argv))
    count = read = 0
    ground: dict[str, None] = {}
    beside: dict[str, None] = {}
    for line in trace:
        read += 1
        if DENIAL_MARK not in line:
            continue
        for record in line.splitlines():
            match = TRACED_CALL.match(record)
            if not match:
                continue
            strings = TRACED_STRING.findall(match["args"])
            if not strings:
                continue
            base = TRACED_FD.match(match["args"])
            path = os.path.normpath(strings[0] if strings[0].startswith("/") or not base
                                    else os.path.join(base["base"], strings[0]))
            real = Path(os.path.realpath(path))
            if readable.isdisjoint((real, *real.parents)):
                count += 1
                (ground if domain.withholds(real) else beside).setdefault(path, None)
    return Denials(count, tuple(ground), tuple(beside), read)


def count_piped(job: Path, verdict: Path) -> int:
    """The `count-denials` verb, the tracer's output command (`_confined`): `counted_denials` over this process's
    stdin, the domain and its argv read from `job`, the verdict written to `verdict` only once the stream ended,
    renamed into place whole, so a reader that died wrote none."""
    held = json.loads(job.read_text())
    domain = Domain(tuple(Path(path) for path in held["denied"]), tuple(Path(path) for path in held["granted"]))
    with io.TextIOWrapper(sys.stdin.buffer, errors="replace") as lines:
        found = counted_denials(lines, domain, held["argv"])
    partial = verdict.with_name(verdict.name + ".partial")
    partial.write_text(json.dumps({"count": found.count, "ground": found.ground, "beside": found.beside,
                                   "lines": found.lines}))
    partial.rename(verdict)
    return 0


@contextlib.contextmanager
def act_locations(scratch: Path):
    """The act's own `TMPDIR` and `HOME` (DN-142.D30 (9) R8, DN-142.D29 (5)): two empty directories of a
    directory of its own under `scratch`, removed when the act ends. LOCATIONS, never keyed: no parameter
    document names them. Yields the environment the act runs with."""
    own = Path(tempfile.mkdtemp(prefix="act.", dir=scratch))
    try:
        (own / "tmp").mkdir()
        (own / "home").mkdir()
        yield {**os.environ, "TMPDIR": str(own / "tmp"), "HOME": str(own / "home")}
    finally:
        shutil.rmtree(own, ignore_errors=True)


def location_refusals(domain: Domain, locations: dict[str, Path]) -> list[str]:
    """One line per location (`/tmp`, the act's `TMPDIR`, its `HOME`) that is an ancestor of a denied root
    or lies inside the denied ground (DN-142.D29 (5)): every ancestor of a denied root is list-only, so an
    entry the act creates there after its domain was built is withheld and counted, refusing the act at
    DENIED after its whole build with a cause that reads like an undeclared input."""
    found = []
    for label, path in locations.items():
        real = Path(os.path.realpath(path))
        below = [str(root) for root in domain.denied if root != real and _within(root, real)]
        if below:
            found.append(f"{label} {path} is an ancestor of the denied root(s) {', '.join(below)}")
        elif domain.withholds(real):
            holding = [str(root) for root in domain.denied if _within(real, root)]
            found.append(f"{label} {path} lies inside the denied ground ({', '.join(holding)})")
    return found


def located(scratch: Path, tally: Path, command: list[str]) -> int:
    """Run `command` with no domain, in its own locations, its tally `unconfined`: the act of a package
    declaring itself unconfined (DN-142.D29 (9)), whose records keep denials_counted false."""
    tally.write_text(f"{UNCONFINED}\n")
    with act_locations(scratch) as environment:
        return subprocess.run(command, env=environment, check=False).returncode


def confine(domain: Domain, scratch: Path, tally: Path, command: list[str], tracer: str | None = None) -> int:
    """Run `command` in `domain` and in its own locations under `scratch` (act_locations), counting its
    denials where a tracer is given: a counted denial REFUSES the act (exit DENIED, the paths named,
    nothing for its records to store), an uncounted act runs as it would, and a domain in which `/tmp`,
    the act's TMPDIR or its HOME holds or lies in denied ground is refused before the act runs (exit
    REFUSED_BUILD, each named). `tally` receives `counted <n>` or `uncounted`, what the act's records keep
    as `denials_counted` (DN-142.D29 (8)). The counter judges the very argv the domain was built from."""
    with act_locations(scratch) as environment:
        refusals = location_refusals(domain, {str(SYSTEM_TMP): SYSTEM_TMP, "TMPDIR": Path(environment["TMPDIR"]),
                                              "HOME": Path(environment["HOME"])})
        if refusals:
            tally.write_text(f"{UNCOUNTED}\n")
            print("malf_closure: REFUSED before the act ran, nothing judged — an act writes its temporary files and "
                  "its HOME where no denied root hangs (DN-142.D29 (5), DN-142.D30 (9) R8); move the denied root out, "
                  "never the act's writes in: " + "; ".join(refusals), file=sys.stderr)
            return REFUSED_BUILD
        return _confined(domain, tally, command, tracer, environment)


def _confined(domain: Domain, tally: Path, command: list[str], tracer: str | None, environment: dict) -> int:
    argv = domain_argv(domain)
    sandbox = [sys.executable, "-I", str(Path(__file__).resolve().parent / SANDBOX), *argv, "--", *command]
    if tracer is None:
        tally.write_text(f"{UNCOUNTED}\n")
        return subprocess.run(sandbox, env=environment, check=False).returncode
    # THE TRACE NEVER LANDS ON DISK: the tracer writes it into a pipe to the counter (`-o |<command>`), a child
    # of the tracer forked before the act starts, its write end close-on-exec, so the act holds neither end.
    # A gcc create's trace was a file in this process's temporary directory, ~4 GiB for coderoast_server
    # (inferred from the 9.1 GB OOM of step 0 run 40003748415), and it filled the release runner's disk.
    # Only the job (the domain and its argv) and the verdict touch the disk, each a few hundred bytes.
    with tempfile.TemporaryDirectory(prefix="malf_closure.count.") as count_dir:
        job, verdict = Path(count_dir) / "job.json", Path(count_dir) / "verdict.json"
        job.write_text(json.dumps({"denied": [str(path) for path in domain.denied],
                                   "granted": [str(path) for path in domain.granted], "argv": argv}))
        counter = shlex.join([sys.executable, "-I", str(Path(__file__).resolve()), COUNT_VERB, str(job),
                              str(verdict)])
        ran = subprocess.run([tracer, "-f", "-qq", "--seccomp-bpf", "-y", "-e", "trace=%file,execve",
                              "-e", "status=failed", "-s", "4096", "-o", f"|{counter}", *sandbox],
                             env=environment, check=False)
        try:
            held = json.loads(verdict.read_text())
            denied = Denials(held["count"], tuple(held["ground"]), tuple(held["beside"]), held["lines"])
        except (OSError, ValueError, KeyError, TypeError) as unread:
            tally.write_text(f"{UNCOUNTED}\n")
            print(f"malf_closure: REFUSED — the denial counter returned no verdict ({unread!r}), so the act's "
                  f"denials are unjudged and nothing of it is stored (DN-142.D29 (8))", file=sys.stderr)
            return DENIED
    tally.write_text(f"{COUNTED} {denied.count}\n")
    if denied.count:
        print(f"malf_closure: REFUSED — the step's domain refused {denied.count} read(s), and an act a domain refused "
              f"is never stored (DN-142.D29 (8))", file=sys.stderr)
        if denied.ground:
            print(f"malf_closure:   {len(denied.ground)} path(s) of ground the step's key does not name (a sibling "
                  f"repository, another conan home, the store or a corpus not its own); declare the read in the key if "
                  f"it is a real input, or remove it, never widen the grant by hand: " + ", ".join(denied.ground),
                  file=sys.stderr)
        if denied.beside:
            print(f"malf_closure:   {len(denied.beside)} path(s) outside that ground the domain still withholds: an "
                  f"entry created after the act started in a directory that holds a denied root (the domain grants "
                  f"that directory's entries as listed at its start), or a symlink resolving into the denied ground: "
                  + ", ".join(denied.beside), file=sys.stderr)
        return DENIED
    return ran.returncode


def denials_counted(tally: Path | None) -> bool:
    """Whether a tally says its act ran in its domain with its denials counted at zero; an absent or
    unreadable tally, or one that counted nothing, is False, and nothing defaults it to True."""
    if tally is None:
        return False
    try:
        words = tally.read_text().split()
    except OSError:
        return False
    return words == [COUNTED, "0"]


# ── selftest────────────────────────────────────────────────────────────────────────────────────

GIT_FIXTURE_ENV = {"GIT_AUTHOR_NAME": "f", "GIT_AUTHOR_EMAIL": "f@f", "GIT_COMMITTER_NAME": "f",
                   "GIT_COMMITTER_EMAIL": "f@f", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def _committed(where: Path, files: dict[str, str]) -> Path:
    """A git repository at `where` holding `files`, committed."""
    for path, text in files.items():
        (where / path).parent.mkdir(parents=True, exist_ok=True)
        (where / path).write_text(text)
    env = {**os.environ, **GIT_FIXTURE_ENV}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(where), *args], check=True, env=env, capture_output=True)
    return where


def _fixture(base: Path) -> Path:
    """A fixture malf tree, committed, holding one instance of every edge and of every non-edge."""
    return _committed(base / "malf", {
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
        ".gitattributes": "* text=auto eol=lf\n",
    })


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

    # The fixture lives under the user's cache, never /tmp: each ancestor of a denied root has its entries
    # granted one by one, and /tmp's come and go, so an entry deleted between the listing and the domain
    # refuses the act and one created after it is unreadable inside (measured on this arm's first run).
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="malf_closure.selftest.", dir=cache) as held:
        base = Path(held)
        workspace = base / "workspace"
        malf = _fixture(workspace)
        tree = Tree.at(malf, "HEAD")
        driven = closure(tree, {"malf"})
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
        arm("a cycle of imports terminates, each member reached once",
            sorted(closure(tree, {"codegen.py"}).files), ["codegen.py", "cycle_a.py", "cycle_b.py"])
        refused = None
        try:
            closure(tree, {"absent.py"})
        except ClosureError as error:
            refused = str(error)
        arm("a seed that is not tracked is refused, named", refused, "seed(s) not tracked in malf: absent.py")

        repository = _committed(workspace / "repository", {"recipe/conanfile.py": "own\n"})
        sibling = _committed(workspace / "sibling", {"secret.txt": "a sibling's source\n"})
        shared_home = workspace / ".conan2" / "shared"
        shared_home.mkdir(parents=True)
        (shared_home / "package.txt").write_text("a shared home's package\n")
        user_home = base / "user-conan"
        user_home.mkdir()
        (user_home / "package.txt").write_text("another home's package\n")
        store = base / "store"
        (store / "records").mkdir(parents=True)
        (store / "records" / "k.json").write_text("{}\n")
        corpora = base / "corpora"
        for name in ("own", "other"):
            (corpora / name).mkdir(parents=True)
            (corpora / name / "a.log").write_text(f"{name} corpus\n")
        scratch = base / "scratch"
        step_home = scratch / "home.step"
        step_home.mkdir(parents=True)
        (step_home / "profile").write_text("[settings]\n")
        parameters = workspace / "out" / "bench.json"
        parameters.parent.mkdir()
        parameters.write_text("{}\n")
        gates = scratch / "corpus_gates.json"
        gates.write_text(json.dumps({"packages": {
            "pkg": [{"leg": "sample", "suites": ["S"], "environment": {"OWN": str(corpora / "own")}}],
            "other": [{"leg": "sample", "suites": ["T"], "environment": {"OTHER": str(corpora / "other")}}]}}))
        domain = step_domain(malf, workspace, store, [workspace / ".conan2", user_home], step_home, scratch,
                             repository / "recipe", gates, "pkg", [parameters])
        arm("the domain denies the workspace, every conan home outside the step's own, the store and every corpus "
            "mount; it re-grants malf whole, the step's repository, its home, its scratch, its own mount and its "
            "parameter file", (domain.denied, domain.granted),
            (_outermost([path.resolve() for path in (workspace, user_home, store, corpora / "own", corpora / "other")]),
             _outermost([path.resolve() for path in (malf, repository, step_home, scratch, corpora / "own", parameters)])))
        tally = base / "tally"
        tracer = shutil.which("strace")
        arm("the seat carries the tracer the denial counter runs (DN-142.D29 (8))", tracer is not None, True)

        def act(script: str, counter: str | None = tracer) -> tuple[int, list[str], str]:
            with contextlib.redirect_stderr(io.StringIO()) as said:
                code = confine(domain, scratch, tally, ["sh", "-c", script], tracer=counter)
            return code, tally.read_text().split(), said.getvalue()

        code, counted, said = act(f"cat {sibling / 'secret.txt'} >/dev/null 2>&1")
        arm("RED: an act reading a sibling repository of the workspace is denied, counted and REFUSED, the path named",
            (code, counted, str(sibling / "secret.txt") in said), (DENIED, [COUNTED, "1"], True))
        code, counted, said = act(f"cat {shared_home / 'package.txt'} >/dev/null 2>&1; "
                                  f"cat {user_home / 'package.txt'} >/dev/null 2>&1")
        arm("RED: an act reading a file of a conan home other than its own is refused, each path named",
            (code, counted, str(shared_home / "package.txt") in said, str(user_home / "package.txt") in said),
            (DENIED, [COUNTED, "2"], True, True))
        probe = ("import sys\ntry:\n    open(sys.argv[1]).read()\nexcept OSError:\n    pass\n")
        with contextlib.redirect_stderr(io.StringIO()) as said:
            code = confine(domain, scratch, tally, [sys.executable, "-I", "-c", probe, str(sibling / "secret.txt")], tracer=tracer)
        arm("RED: the same read behind `try/except OSError` is refused all the same",
            (code, tally.read_text().split()), (DENIED, [COUNTED, "1"]))
        code, counted, said = act(f"cat {store / 'records' / 'k.json'} >/dev/null 2>&1; "
                                  f"cat {corpora / 'other' / 'a.log'} >/dev/null 2>&1")
        arm("RED: the store and a corpus mount the step's own legs do not name are denied, each counted",
            (code, counted), (DENIED, [COUNTED, "2"]))
        code, counted, _said = act(f"cat {repository / 'recipe' / 'conanfile.py'} {malf / 'unreached.py'} "
                                   f"{malf / '.github' / 'workflows' / 'ci.yml'} {step_home / 'profile'} "
                                   f"{corpora / 'own' / 'a.log'} {parameters} /etc/hostname >/dev/null "
                                   f"&& git -C {repository} rev-parse HEAD >/dev/null")
        arm("an act reading its own repository and its git, any malf file, its home, its own corpus mount, its "
            "parameter file and a system path runs, counted at zero: its records keep denials_counted true",
            (code, counted, denials_counted(tally)), (0, [COUNTED, "0"], True))
        submodule = workspace / "submodule"
        (workspace / ".git-modules").mkdir()
        subprocess.run(["git", "init", "-q", f"--separate-git-dir={workspace / '.git-modules' / 'submodule'}", str(submodule)],
                       check=True, env={**os.environ, **GIT_FIXTURE_ENV})
        _committed(submodule, {"recipe/conanfile.py": "a submodule's own\n"})
        sub_domain = step_domain(malf, workspace, store, [], step_home, scratch, submodule / "recipe")
        with contextlib.redirect_stderr(io.StringIO()):
            code = confine(sub_domain, scratch, tally, ["sh", "-c", f"git -C {submodule} rev-parse HEAD >/dev/null && "
                                                           f"cat {submodule / 'recipe' / 'conanfile.py'} >/dev/null"],
                           tracer=tracer)
        arm("a repository whose git directory lives elsewhere in the workspace (a submodule's, under the "
            "superproject's .git/modules) reads its git: the directory is part of its grant",
            (code, tally.read_text().split()), (0, [COUNTED, "0"]))
        code, counted, _said = act(f"ls {workspace} | grep -qx sibling && ls {sibling} | grep -qx secret.txt")
        arm("a listing of the workspace and of a sibling names their entries and is counted at zero: the domain "
            "withholds file reads, never entry names", (code, counted), (0, [COUNTED, "0"]))
        temporary = ("import sys, tempfile\ntry:\n    tempfile.mkstemp(dir=sys.argv[1])\nexcept OSError:\n"
                     "    sys.exit('Cannot create temporary file in ' + sys.argv[1])\n")
        with contextlib.redirect_stderr(io.StringIO()) as said:
            code = confine(domain, scratch, tally, [sys.executable, "-I", "-c", temporary, str(base)], tracer=tracer)
        arm("RED: a file the act creates beside a denied root's ancestor after its domain was built (a temporary file "
            "in the directory holding another conan home) is refused a read by the domain, so it is counted and the act "
            "REFUSED, the directory named: denials_counted is never true over a refusal nobody counted",
            (code, tally.read_text().split(), str(base) in said.getvalue(), denials_counted(tally)),
            (DENIED, [COUNTED, "1"], True, False))
        (base / "through-link").symlink_to(sibling)
        code, counted, said = act(f"cat {base / 'through-link' / 'secret.txt'} >/dev/null 2>&1")
        arm("RED: a read through a symlink beside a denied root, resolving into the denied ground, is counted and "
            "REFUSED under the path the act named", (code, counted, str(base / "through-link") in said),
            (DENIED, [COUNTED, "1"], True))
        code, counted, _said = act(f"cat {sibling / 'secret.txt'} 2>/dev/null", counter=None)
        arm("with no tracer the domain still denies, the act fails as it would, and its tally is uncounted: "
            "denials_counted false", (code != 0, counted, denials_counted(tally)), (True, [UNCOUNTED], False))
        (base / "into-workspace").symlink_to(sibling)
        arm("a symlink beside a denied root resolving into the denied ground is left ungranted",
            str(base / "into-workspace") in domain_argv(domain), False)
        refusals = []
        for label, call in (("absent", lambda: step_domain(malf, workspace, store, [], step_home, scratch,
                                                            repository / "recipe", None, None, [base / "absent.json"])),
                            ("workspace", lambda: step_domain(malf, repository, store, [], step_home, scratch,
                                                               repository / "recipe"))):
            try:
                call()
                refusals.append(f"{label}: no refusal")
            except ClosureError as error:
                refusals.append(f"{label}: {str(error).split(':', 1)[0]}")
        arm("RED: a granted root that does not exist is refused, and so is a step whose repository is the "
            "workspace root itself", refusals,
            ["absent: the step's domain grants root(s) that do not exist",
             f"workspace: the act's repository {repository} is the workspace root itself, so its domain would deny "
             "no sibling repository (DN-142.D29 (5))"])
        def cli(script: str, *extra: str) -> subprocess.CompletedProcess:
            """The `confine` verb over this fixture's step, as malf's driver calls it."""
            return subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "confine", str(malf),
                                   "--tally", str(tally), "--workspace", str(workspace), "--store", str(store),
                                   "--act-root", str(repository / "recipe"), "--home", str(step_home),
                                   "--scratch", str(scratch), *extra, "--", "sh", "-c", script],
                                  capture_output=True, text=True, check=False)

        under_tmp = Path(tempfile.mkdtemp(prefix="malf_closure.selftest.home.", dir="/tmp"))
        try:
            ran = base / "ran-beside-a-tmp-home"
            refused_build = cli(f"touch {ran}", "--conan-home", str(under_tmp))
            arm("RED: a domain whose conan home lies under /tmp is refused at build, exit 2, /tmp and the home named, "
                "and the act never runs (DN-142.D30 (9) R8)",
                (refused_build.returncode, "/tmp " in refused_build.stderr, str(under_tmp) in refused_build.stderr,
                 ran.exists()), (REFUSED_BUILD, True, True, False))
        finally:
            shutil.rmtree(under_tmp)
        located = cli('[ -z "$(ls -A "$HOME")" ] && [ -z "$(ls -A "$TMPDIR")" ] && [ "$HOME" != "$TMPDIR" ] '
                      f'&& case "$HOME:$TMPDIR" in {scratch}/*:{scratch}/*) ;; *) exit 9 ;; esac '
                      '&& mkdir -p "$HOME/.cache" && echo t > "$TMPDIR/x" && echo h > "$HOME/.cache/x" '
                      '&& cat "$TMPDIR/x" "$HOME/.cache/x" >/dev/null')
        arm("RED: an act runs with a TMPDIR and a HOME of its own, empty, inside the walk's scratch, and creates then "
            "reads $TMPDIR/x and $HOME/.cache/x counted at zero (DN-142.D30 (9) R8)",
            (located.returncode, tally.read_text().split()), (0, [COUNTED, "0"]))
        arm("an act's locations are removed when it ends: the scratch holds no act directory after it",
            sorted(path.name for path in scratch.glob("act.*")), [])
        arm("a TMPDIR or HOME that is an ancestor of a denied root, or lies inside the denied ground, is named; the "
            "act's own locations are not",
            [line.split(" ", 1)[0] for line in location_refusals(domain, {"TMPDIR": base, "HOME": sibling,
                                                                          "own": scratch})],
            ["TMPDIR", "HOME"])
        argv = domain_argv(domain)
        fresh = base / "created-after-the-listing"
        traced = base / "golden.trace"
        traced.write_text(
            f'7 openat(AT_FDCWD</>, "{sibling / "secret.txt"}", O_RDONLY) = -1 EACCES (Permission denied)\n'
            f'7 openat(AT_FDCWD</>, "{sibling / "secret.txt"}", O_RDONLY) = -1 EACCES (Permission denied)\n'
            f'7 openat(3<{sibling}>, "relative.txt", O_RDONLY) = -1 EACCES (Permission denied)\n'
            f'7 openat(AT_FDCWD</>, "{malf / "unreached.py"}", O_RDONLY) = -1 EACCES (Permission denied)\n'
            f'7 openat(AT_FDCWD</>, "{sibling / "absent.txt"}", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
            f'7 newfstatat(AT_FDCWD</>, "{fresh}", 0x7ffc, 0) = -1 EACCES (Permission denied)\n'
            f'7 +++ a record\r8 openat(AT_FDCWD</>, "{store / "records" / "k.json"}", O_RDONLY) = -1 EACCES (Permission denied)\n'
            f'7 chdir() = -1 EACCES (Permission denied)\n')
        with traced.open(errors="replace") as lines:
            found = counted_denials(lines, domain, argv)
        golden = (5, (str(sibling / "secret.txt"), str(sibling / "relative.txt"), str(store / "records" / "k.json")),
                  (str(fresh),))
        arm("the counter's verdict over a trace of every record shape: a repeated denial counts twice and is named "
            "once, a relative path joins its descriptor's directory, an EACCES at a granted location (the file's own "
            "permission), an ENOENT and a call naming no path count nothing, an entry created after the listing is "
            "denied beside the ground (counted, named apart), and a carriage return splits two records",
            (found.count, found.ground, found.beside), golden)
        # THE SAME BYTES THROUGH THE PIPE the tracer writes into (`count-denials` reading its stdin, the verdict
        # file it renames into place): the framing a pipe delivers is the file's, so the verdict is identical.
        job, verdict = base / "golden.job.json", base / "golden.verdict.json"
        job.write_text(json.dumps({"denied": [str(path) for path in domain.denied],
                                   "granted": [str(path) for path in domain.granted], "argv": argv}))
        piped = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), COUNT_VERB, str(job),
                                str(verdict)], input=traced.read_bytes(), capture_output=True, check=False)
        held = json.loads(verdict.read_text()) if verdict.is_file() else {}
        arm(f"the golden trace piped into `count-denials` gives the same verdict as read from its file, over the same "
            f"{found.lines} lines",
            (piped.returncode, held.get("count"), tuple(held.get("ground", ())), tuple(held.get("beside", ())),
             held.get("lines")), (0, *golden, found.lines))
        # THE TRACE NEVER LANDS ON DISK. An act emitting a large trace (failed stats, each a traced record) measures,
        # while it still runs, every byte under the confining process's temporary directory: the trace went to a
        # file there before (the release runner's disk filled twice), and now only the job and the verdict do.
        confiner_tmp = base / "confiner-tmp"
        confiner_tmp.mkdir()
        measured_at = scratch / "measured-bytes"
        records = 40000
        emitter = ("import os, sys\n"
                   f"for index in range({records}):\n"
                   "    try:\n"
                   f"        os.stat('/malf-closure-absent/{'x' * 160}/' + str(index))\n"
                   "    except OSError:\n"
                   "        pass\n"
                   "total = sum(os.path.getsize(os.path.join(where, name)) for where, _dirs, names in os.walk(sys.argv[1])"
                   " for name in names)\n"
                   "open(sys.argv[2], 'w').write(str(total))\n")
        saved_tmp, tempfile.tempdir = tempfile.tempdir, str(confiner_tmp)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                code = confine(domain, scratch, tally, [sys.executable, "-I", "-c", emitter, str(confiner_tmp),
                                                        str(measured_at)], tracer=tracer)
        finally:
            tempfile.tempdir = saved_tmp
        on_disk = int(measured_at.read_text()) if measured_at.is_file() else -1
        arm(f"RED: an act tracing {records} failed calls (about 12 MiB of trace) finds under 64 KiB "
            f"under the confining process's temporary directory while it runs, and is counted at zero (got {on_disk} "
            f"bytes; the trace written to a file there measured its whole size)",
            (code, tally.read_text().split(), 0 <= on_disk < 64 * 1024), (0, [COUNTED, "0"], True))
        arm("the confining process's temporary directory is empty once the act ends",
            sorted(path.name for path in confiner_tmp.iterdir()), [])
        # A COUNTER THAT RETURNS NO VERDICT REFUSES: the act's denials are unjudged, never counted at zero.
        saved_verb = globals()["COUNT_VERB"]
        globals()["COUNT_VERB"] = "no-such-verb"
        try:
            with contextlib.redirect_stderr(io.StringIO()) as said:
                code = confine(domain, scratch, tally, ["true"], tracer=tracer)
        finally:
            globals()["COUNT_VERB"] = saved_verb
        arm("RED: a denial counter that writes no verdict REFUSES the act, its tally uncounted, never counted at zero",
            (code, tally.read_text().split(), "the denial counter returned no verdict" in said.getvalue()),
            (DENIED, [UNCOUNTED], True))
        # A build's trace grows with its translation units times its include directories (measured: 134 MiB over
        # the preprocessing of coderoast_server's 120 units, ~4 GiB inferred for the gcc create an OOM kill ended).
        trace_mib, growth_mib = 64, 16
        record = f'7 openat(AT_FDCWD</>, "{sibling}/include/never/{"x" * 160}.hpp", O_RDONLY) = -1 ENOENT (No such file)\n'
        with (base / "large.trace").open("w") as large:
            for _ in range(trace_mib * 1024 * 1024 // len(record)):
                large.write(record)
        measured = subprocess.run(
            [sys.executable, "-I", "-c",
             "import resource, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport malf_closure\n"
             "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
             "malf_closure.counted_denials(Path(sys.argv[2]).open(errors='replace'), "
             "malf_closure.Domain((Path(sys.argv[3]),), ()), [])\n"
             "print((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) // 1024)\n",
             str(Path(__file__).resolve().parent), str(base / "large.trace"), str(sibling)],
            capture_output=True, text=True, check=False)
        grown = int(measured.stdout) if measured.returncode == 0 else f"exit {measured.returncode}: {measured.stderr}"
        arm(f"RED: the counter streams its trace: reading a {trace_mib} MiB trace grows its peak RSS by under "
            f"{growth_mib} MiB (got {grown} MiB; the trace read whole held twice its size)",
            isinstance(grown, int) and grown < growth_mib, True)
        (base / "large.trace").unlink()
        grants, denials = 500, 200
        crowded = ["--reads-only", *(item for index in range(grants) for item in ("--read", f"/absent-grant-{index}"))]
        (base / "denials.trace").write_text("".join(
            f'7 openat(AT_FDCWD</>, "{sibling}/header-{index}.hpp", O_RDONLY) = -1 EACCES (Permission denied)\n'
            for index in range(denials)))
        calls = 0
        contained = globals()["_within"]

        def counting(path: Path, root: Path) -> bool:
            nonlocal calls
            calls += 1
            return contained(path, root)

        globals()["_within"] = counting
        try:
            with (base / "denials.trace").open(errors="replace") as lines:
                crowded_found = counted_denials(lines, domain, crowded)
        finally:
            globals()["_within"] = contained
        ceiling = denials * (len(domain.denied) + len(domain.granted))
        arm(f"RED: the counter indexes the read roots, never scans them per denial: {denials} denials under {grants} "
            f"grants call the containment test at most {ceiling} times (denials x the domain's own roots; got {calls})",
            (crowded_found.count, calls <= ceiling), (denials, True))
        (base / "unconfined-tally").write_text("unconfined\n")
        arm("an absent or unreadable tally reads false, never true; so does a declared-unconfined act's",
            (denials_counted(None), denials_counted(base / "no-tally"), denials_counted(base / "unconfined-tally")),
            (False, False, False))
    print(f"malf_closure selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["confine"]:
        if "--" not in argv:
            print("malf_closure.py confine: name the command after --", file=sys.stderr)
            return 2
        split = argv.index("--")
        options = argparse.ArgumentParser(prog="malf_closure.py confine")
        options.add_argument("malf_dir", type=Path)
        options.add_argument("--tally", type=Path, required=True)
        options.add_argument("--workspace", type=Path, required=True)
        options.add_argument("--store", type=Path, required=True)
        options.add_argument("--conan-home", action="append", default=[], type=Path)
        options.add_argument("--corpus-gates", type=Path)
        options.add_argument("--package")
        options.add_argument("--parameter-file", action="append", default=[], type=Path)
        options.add_argument("--act-root", type=Path, required=True)
        options.add_argument("--home", type=Path, required=True)
        options.add_argument("--scratch", type=Path, required=True)
        options.add_argument("--unconfined", action="store_true")
        given = options.parse_args(argv[1:split])
        if not argv[split + 1:]:
            print("malf_closure.py confine: the command after -- is empty", file=sys.stderr)
            return 2
        if given.unconfined:
            return located(given.scratch, given.tally, argv[split + 1:])
        try:
            domain = step_domain(given.malf_dir, given.workspace, given.store, given.conan_home, given.home,
                                 given.scratch, given.act_root, given.corpus_gates, given.package, given.parameter_file)
        except ClosureError as unread:
            print(f"malf_closure.py confine: REFUSED — {unread}", file=sys.stderr)
            return 2
        return confine(domain, given.scratch, given.tally, argv[split + 1:], shutil.which("strace"))
    parser = argparse.ArgumentParser(prog="malf_closure.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("selftest")
    counting = verbs.add_parser(COUNT_VERB)
    counting.add_argument("job", type=Path)
    counting.add_argument("verdict", type=Path)
    given = parser.parse_args(argv)
    if given.verb == COUNT_VERB:
        return count_piped(given.job, given.verdict)
    return selftest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
