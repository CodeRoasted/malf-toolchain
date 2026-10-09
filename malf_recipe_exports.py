"""A first-party recipe exports its git-TRACKED files: the one helper every recipe's `export_sources()` calls.

DN-142.D4 (b). `exports_sources` globs read the DISK, so a desk export swept whatever its trees
held: measured 2026-10-08, insight-eidos's `"detection/*", "explain/*", "engine/*"` exported 1 253
gitignored files (`build-clang21-asan/` trees, `bench_results/`), and its recipe revision was a
function of the desk, never of the commit. A recipe keeps its `exports_sources` as the declared
allowlist (ADR-3.D5) and calls `narrow_to_tracked(self)` from `export_sources()`, which conan runs
after it copies those globs and before it hashes the export: every copied file git does not track
is removed. The export is then the tracked files under the declared roots, so the helper can
narrow the allowlist and never widen it.

Staged by malf beside global.conf in every conan home it uses (malf's conf sync, setup-build-env,
setup-proof-linux, setup-proof-msvc); global.conf names it as `user.malf:recipe_exports`, and a
recipe loads it with `runpy.run_path`. A home that lacks it fails the export, never exports the
disk silently.

Outside a git checkout (a source archive) there is no tracked set to narrow to: the export keeps
the declared globs as found on disk and says so in a warning, because its revision is then the
disk's.

A test whose input lives OUTSIDE the recipe folder (`DN-142.D5` (4), `DN-142.D13` (3), (4): the
infra packages' shared `../tests_support/`, the canon proof recipe's `../scripts/`) has it exported
by `export_tracked(self, source, destination)`, called after `narrow_to_tracked`: the files git
tracks under `source`, a path relative to the recipe folder that may leave it, are copied into the
export at `destination`. A `source` holding no tracked file fails the export: an input the tests
read cannot be exported empty.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conan.errors import ConanException


def _tracked(folder: str) -> set[str] | None:
    """The paths git tracks under `folder`, relative to it; None when `folder` is in no checkout."""
    if shutil.which("git") is None:
        return None
    probe = subprocess.run(["git", "-C", folder, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None
    listed = subprocess.run(["git", "-C", folder, "ls-files", "-z", "--cached"],
                            capture_output=True, check=False)
    if listed.returncode != 0:
        raise ConanException(f"malf: `git -C {folder} ls-files` failed in a checkout, so the "
                             f"tracked set is unknown: {listed.stderr.decode(errors='replace')}")
    return {path for path in listed.stdout.decode().split("\0") if path}


def narrow_to_tracked(conanfile) -> None:
    """Remove from the export every file conan copied that git does not track.

    pre: called from `export_sources()`, after conan copied the recipe's `exports_sources` globs
    post: the export holds only tracked paths, with no directory left empty by the removal
    """
    destination = Path(conanfile.export_sources_folder)
    tracked = _tracked(conanfile.recipe_folder)
    if tracked is None:
        conanfile.output.warning(
            f"malf: {conanfile.recipe_folder} is not in a git checkout (or git is not installed): "
            "the export keeps its exports_sources globs as found on disk, so its recipe revision "
            "depends on the disk, not on a commit (DN-142.D4)")
        return
    kept = dropped = 0
    for root, directories, files in os.walk(destination, topdown=False):
        here = Path(root)
        entries = files + [name for name in directories if (here / name).is_symlink()]
        for name in entries:
            path = here / name
            if path.relative_to(destination).as_posix() in tracked:
                kept += 1
            else:
                path.unlink()
                dropped += 1
        if here != destination and not any(here.iterdir()):
            here.rmdir()
    conanfile.output.info(f"malf: exports narrowed to the tracked files — {kept} kept, "
                          f"{dropped} untracked or ignored dropped")


def export_tracked(conanfile, source: str, destination: str) -> None:
    """Copy into the export, at `destination`, every file git tracks under `source`.

    pre: called from `export_sources()`, after `narrow_to_tracked`; `source` is a file or a
        directory, relative to the recipe folder, and may leave it (`../tests_support`)
    post: `<export>/<destination>` holds exactly the tracked files under `source`, or the export
        failed
    """
    origin = (Path(conanfile.recipe_folder) / source).resolve()
    target = Path(conanfile.export_sources_folder) / destination
    if origin.is_file():
        folder, names = origin.parent, [origin.name]
    elif origin.is_dir():
        folder, names = origin, None
    else:
        raise ConanException(f"malf: {conanfile.name}: the export names {source}, and "
                             f"{origin} does not exist")
    tracked = _tracked(str(folder))
    if tracked is None:
        conanfile.output.warning(
            f"malf: {folder} is not in a git checkout (or git is not installed): {source} is "
            "exported as found on disk, so the recipe revision depends on the disk (DN-142.D4)")
        tracked = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    chosen = sorted(tracked if names is None else tracked & set(names))
    if not chosen:
        raise ConanException(f"malf: {conanfile.name}: the export names {source}, and git tracks "
                             f"no file there")
    for relative in chosen:
        copied = (target / relative) if names is None else target
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / relative, copied)
    conanfile.output.info(f"malf: {len(chosen)} tracked file(s) under {source} exported "
                          f"to {destination}")
