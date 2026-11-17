#!/usr/bin/env python3
"""The recipe overlay's entries in ``conan.lock``, stamped with a time git holds (ROADMAP N470).

Why this exists
---------------
A lock entry is ``name/version#revision%timestamp``. ConanCenter's timestamps are the server's,
the same on every desk. The overlay's are not: ``overlay/`` is a ``local-recipes-index`` remote,
which EXPORTS a recipe into the home that asks for it, and an export stamps the revision with the
clock of that moment. So two desks, or one desk with a fresh home, lock the same revision of
libpq, libcurl, drogon and autoconf under four different timestamps, and the lock is not
byte-reproducible (seen on N467's lock, malf ``9cdf6eb``: revisions unchanged, timestamps moved 1.4
to 2.3 s).

What the timestamp means to Conan, read in Conan 2.28.1's own source: ``RecipeReference.__eq__``
and ``__hash__`` ignore it, so a locked entry resolves by name, version and revision
(``Lockfile._resolve``); it only ORDERS revisions of one name/version (``__lt__``, timestamp before
revision), newest first. An overlay recipe has ONE revision a remote can serve (a
local-recipes-index "can't provide a specific revision"), so its timestamp orders nothing; it
still has to say when the revision came to be. The time git records for that is the committer
time of the last commit touching ``overlay/recipes/<name>``: the same on every full clone, and
moved only by a change to the recipe.

REFUSED: a shallow clone (its history may not reach the recipe's last commit, so the time would be
the clone's tip), and an overlay name/version locked at more than one revision (no overlay can
serve the second one). A recipe edited and not yet committed keeps its last commit's time: the
revision is the export's, the time stays git's.

    python3 lock_overlay.py stamp LOCK MALF_DIR
    python3 lock_overlay.py selftest
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

OVERLAY = Path("overlay") / "recipes"
ENTRY = re.compile(r'"(?P<name>[^"/#%]+)/(?P<version>[^"#%]+)#(?P<revision>[0-9a-f]+)%(?P<timestamp>[0-9.]+)"')


class Refused(Exception):
    """The lock cannot be stamped reproducibly; the message says why."""


def _git(malf_dir: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(malf_dir), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed in {malf_dir}: {done.stderr.strip()}")
    return done.stdout.strip()


def overlay_times(malf_dir: Path) -> dict[tuple[str, str], str]:
    """`(name, version) -> timestamp` for every version the overlay serves, as Conan spells a
    float: the committer time of the last commit touching the recipe's directory."""
    if _git(malf_dir, "rev-parse", "--is-shallow-repository") != "false":
        raise Refused(f"{malf_dir} is a shallow clone: the last commit touching an overlay recipe may "
                      "lie beyond its history, so its time would be the clone's tip. Lock from a full clone.")
    times: dict[tuple[str, str], str] = {}
    for recipe in sorted(path for path in (malf_dir / OVERLAY).iterdir() if path.is_dir()):
        when = _git(malf_dir, "log", "-1", "--format=%ct", "--", str(OVERLAY / recipe.name))
        if not when:
            raise Refused(f"{OVERLAY / recipe.name} has no commit in {malf_dir}: commit the recipe before locking it")
        config = yaml.safe_load((recipe / "config.yml").read_text(encoding="utf-8")) or {}
        for version in (config.get("versions") or {}):
            times[(recipe.name, str(version))] = repr(float(when))
    return times


def stamp(text: str, times: dict[tuple[str, str], str]) -> str:
    """`text` (a lock) with each overlay entry's timestamp replaced by its git time; every other
    byte as Conan wrote it."""
    revisions: dict[tuple[str, str], set[str]] = {}
    for entry in ENTRY.finditer(text):
        key = (entry["name"], entry["version"])
        if key in times:
            revisions.setdefault(key, set()).add(entry["revision"])
    split = {f"{name}/{version}": sorted(found) for (name, version), found in revisions.items() if len(found) > 1}
    if split:
        raise Refused("an overlay recipe is locked at more than one revision, and a local-recipes-index serves "
                      f"only its current one: {split}")

    def replace(entry: re.Match) -> str:
        when = times.get((entry["name"], entry["version"]))
        if when is None:
            return entry.group(0)
        return f'"{entry["name"]}/{entry["version"]}#{entry["revision"]}%{when}"'

    return ENTRY.sub(replace, text)


def selftest() -> int:
    checks: list[tuple[str, bool]] = []
    lock = ('{\n    "version": "0.5",\n    "requires": [\n'
            '        "zlib/1.4#f51a%1793979742.583",\n'
            '        "libpq/17.11#4926%1794666219.8129056",\n'
            '        "libpq/16.0#aaaa%1700000000.5"\n    ],\n'
            '    "build_requires": [\n        "autoconf/2.71#090b%1794666351.8049767"\n    ]\n}\n')
    times = {("libpq", "17.11"): "1791749761.0", ("autoconf", "2.71"): "1791749761.0"}
    stamped = stamp(lock, times)
    checks.append(("an overlay entry takes its git time, in every section",
                   '"libpq/17.11#4926%1791749761.0"' in stamped and '"autoconf/2.71#090b%1791749761.0"' in stamped))
    checks.append(("a ConanCenter entry, and an overlay name at a version the overlay does not serve, keep "
                   "Conan's bytes", '"zlib/1.4#f51a%1793979742.583"' in stamped
                   and '"libpq/16.0#aaaa%1700000000.5"' in stamped))
    moved = lock.replace("%1794666219.8129056", "%1794666221.2").replace("%1794666351.8049767", "%1794666353.1")
    checks.append(("two exports' locks, stamped, are byte-identical", stamp(moved, times) == stamped))
    try:
        stamp(lock.replace('"zlib', '"libpq/17.11#beef%1.0",\n        "zlib'), times)
        checks.append(("an overlay name/version at two revisions is refused", False))
    except Refused as refused:
        checks.append(("an overlay name/version at two revisions is refused", "libpq/17.11" in str(refused)))

    with tempfile.TemporaryDirectory(prefix="lock_overlay.selftest.") as held:
        malf = Path(held) / "malf"
        recipe = malf / OVERLAY / "libpq"
        recipe.mkdir(parents=True)
        (recipe / "config.yml").write_text('versions:\n  "17.11":\n    folder: all\n', encoding="utf-8")
        env = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "-C", str(malf), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(malf), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(malf), *env, "commit", "-q", "-m", "r", "--date", "@1791749761"], check=True,
                       env={"GIT_COMMITTER_DATE": "@1791749761", "PATH": "/usr/bin:/bin"})
        checks.append(("the time is the committer time of the recipe's last commit, as Conan spells a float",
                       overlay_times(malf) == {("libpq", "17.11"): "1791749761.0"}))
        shallow = Path(held) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{malf}", str(shallow)], check=True)
        try:
            overlay_times(shallow)
            checks.append(("a shallow clone is refused", False))
        except Refused as refused:
            checks.append(("a shallow clone is refused", "shallow clone" in str(refused)))

    failed = [name for name, held in checks if not held]
    for name, held in checks:
        print(f"  {'ok  ' if held else 'FAIL'}  {name}")
    print(f"lock_overlay selftest: {len(checks) - len(failed)}/{len(checks)}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    if len(argv) == 3 and argv[0] == "stamp":
        lock, malf_dir = Path(argv[1]), Path(argv[2])
        try:
            lock.write_text(stamp(lock.read_text(encoding="utf-8"), overlay_times(malf_dir)), encoding="utf-8")
        except Refused as refused:
            print(f"lock_overlay: {refused}", file=sys.stderr)
            return 1
        return 0
    print(__doc__.rsplit("\n\n", 1)[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
