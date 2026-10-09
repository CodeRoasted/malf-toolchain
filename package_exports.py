#!/usr/bin/env python3
"""First-party recipes' exports, measured against a fresh clone: the one helper behind `malf exports-verify`.

DN-142.D4 (b) names the property — a recipe's revision is a function of its commit, never of the
disk it was exported from — and its gate: export every first-party recipe from the desk checkout and
from a fresh clone of the same commit, and compare the two recipe revisions. A recipe revision is
the hash of the export's manifest, so two equal revisions mean byte-equal exports.

    package_exports.py recipes <workspace root>
        every first-party recipe that declares `exports_sources`: each tracked `conanfile.py` of
        each repository the workspace declares, test_package recipes excluded, as
        `<repository>/<path>\t<absolute directory>` lines
    package_exports.py verify <conan home> <recipes tsv> <scratch dir>

The home must be staged as malf stages one (global.conf naming `malf_recipe_exports.py` beside
it). Each repository is cloned once into <scratch dir>, from its own object store, at the commit
its desk checkout has checked out. A package whose TRACKED files are modified on the desk is not
judged: its desk export differs from its commit's by right.

Exit 0 when every recipe's two revisions are equal, 1 when one differs (named with the
files whose bytes differ, or that one side alone exports), 2 when a package could not be judged.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

MANIFEST = "conanmanifest.txt"
NAMED_FILES = 8


def _run(argv: list[str], home: Path | None = None) -> str:
    """`argv`, its stdout; a failure is fatal and names the command."""
    env = {**os.environ, "CONAN_HOME": str(home)} if home else None
    done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        sys.exit(f"package_exports: `{' '.join(argv)}` failed:\n{done.stderr.strip()}")
    return done.stdout


def _export(home: Path, folder: Path) -> str:
    """Export the recipe at `folder` into `home`; its full reference, revision included."""
    return json.loads(_run(["conan", "export", str(folder), "--format=json"], home))["reference"]


def _manifest(home: Path, reference: str) -> dict[str, str]:
    """The export's per-file digests, the creation-time first line excluded."""
    export = Path(_run(["conan", "cache", "path", reference], home).strip())
    lines = (export / MANIFEST).read_text().splitlines()[1:]
    return dict(line.rsplit(": ", 1) for line in lines if line)


def verify(home: Path, released: Path, scratch: Path) -> int:
    rows = [row.split("\t") for row in released.read_text().splitlines() if row.strip()]
    clones: dict[Path, Path] = {}
    equal, differ, unjudged = [], [], []
    for name, folder, *_ in rows:
        desk = Path(folder)
        top = Path(_run(["git", "-C", str(desk), "rev-parse", "--show-toplevel"]).strip())
        modified = _run(["git", "-C", str(desk), "status", "--porcelain", "--untracked-files=no",
                         "--", "."]).strip()
        if modified:
            unjudged.append(name)
            print(f"  UNJUDGED {name}: tracked files modified on the desk —\n"
                  + "\n".join(f"    {line}" for line in modified.splitlines()[:NAMED_FILES]))
            continue
        if top not in clones:
            commit = _run(["git", "-C", str(top), "rev-parse", "HEAD"]).strip()
            clone = scratch / "clones" / top.name
            _run(["git", "clone", "--quiet", "--no-checkout", str(top), str(clone)])
            _run(["git", "-C", str(clone), "checkout", "--quiet", "--detach", commit])
            clones[top] = clone
        fresh = clones[top] / desk.relative_to(top)
        desk_ref, fresh_ref = _export(home, desk), _export(home, fresh)
        if desk_ref == fresh_ref:
            equal.append(name)
            continue
        differ.append(name)
        ours, theirs = _manifest(home, desk_ref), _manifest(home, fresh_ref)
        files = sorted(path for path in ours.keys() | theirs.keys()
                       if ours.get(path) != theirs.get(path))
        only_desk = sum(1 for path in files if path not in theirs)
        print(f"  DIFFER {name}: desk {desk_ref.split('#')[1]} != clone {fresh_ref.split('#')[1]}; "
              f"{len(files)} file(s) differ, {only_desk} exported by the desk alone: "
              f"{', '.join(files[:NAMED_FILES])}{', …' if len(files) > NAMED_FILES else ''}")
    print(f"package_exports: {len(equal)} of {len(rows)} recipe(s) export the same recipe "
          f"revision from the desk and from a fresh clone; {len(differ)} differ, "
          f"{len(unjudged)} not judged")
    if differ:
        return 1
    return 2 if unjudged or not rows else 0


def _declares_exports(conanfile: Path) -> bool:
    """Whether the recipe's class assigns `exports_sources` — the class this gate judges."""
    tree = ast.parse(conanfile.read_text(encoding="utf-8"), filename=str(conanfile))
    return any(isinstance(node, ast.Assign)
               and any(getattr(target, "id", "") == "exports_sources" for target in node.targets)
               for item in tree.body if isinstance(item, ast.ClassDef) for node in item.body)


def recipes(workspace: Path) -> int:
    """Print every first-party recipe declaring `exports_sources`; the repositories are the
    workspace's DECLARED ones (scripts/workspace_layout.py), never whatever sits on the disk."""
    workspace = workspace.resolve()
    sys.path.insert(0, str(workspace / "scripts"))
    import workspace_layout
    found = 0
    for name in workspace_layout.declared_repos(workspace):
        repo = workspace / name
        if not (repo / ".git").exists():
            continue
        listed = _run(["git", "-C", str(repo), "ls-files", "-z", "--", "*conanfile.py"])
        for relative in sorted(path for path in listed.split("\0") if path):
            recipe = repo / relative
            if "test_package" in Path(relative).parts or recipe.name != "conanfile.py":
                continue
            if _declares_exports(recipe):
                folder = Path(relative).parent.as_posix()
                print(f"{name if folder == '.' else f'{name}/{folder}'}\t{recipe.parent}")
                found += 1
    if not found:
        sys.exit(f"package_exports: no recipe declaring exports_sources under {workspace}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "recipes":
        return recipes(Path(argv[1]))
    if len(argv) == 4 and argv[0] == "verify":
        return verify(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
