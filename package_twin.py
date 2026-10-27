#!/usr/bin/env python3
"""The released packages' path independence, measured: one helper behind `malf twin-verify`.

DN-142.D4 (a) names the property — a package's bytes do not depend on the absolute path of the
conan home or of its build folder, nor on the account that built it — and its gate: create every
released package in two homes at two absolute paths under two user names, and compare. Two runs in
ONE home share every path but the per-create folder hash, so they prove less.

    package_twin.py seed <source home> <owned prefixes> <target home>...
        every package of <source home> OUTSIDE the owned namespaces (third-party, recipes and
        binaries) restored into each <target home>, so a create there rebuilds first-party code
        only; a third-party binary the source lacks is built in each target alike
    package_twin.py seed-recipes <lockfile> <owned prefixes> <target home> <source home>...
        every third-party recipe revision <lockfile> names (outside the owned namespaces) that <target
        home> lacks restored from the first <source home> holding it, recipes alone and no binary, then
        every recipe of <target home> checked against its own conanmanifest.txt (`conan cache
        check-integrity`) and every lock revision it holds against the digest of that manifest: the home
        of a compiling act in the build environment, which builds every third-party binary itself
        (DN-142.D30 (9) R7) and runs this at every act ((10) R17). A recipe whose files differ from its
        manifest, or whose manifest is not its revision, is refused naming it (exit 1); a revision no
        source holds is named and left to the graph that needs it, which fails naming it, since the act
        has no network
    package_twin.py provision <lockfile> <owned prefixes> <home> <source download cache>
        ON THE HOST, before a walk whose container has no network: every third-party recipe revision
        <lockfile> names that <home> lacks downloaded from <home>'s remotes and checked against its
        revision, and every source file a held lock recipe declares (its conandata `sources` entry, a url
        and a sha256) that <source download cache> lacks downloaded and checked against its sha256. Exit 0
        everything held; 1 a revision or a source no remote serves, named; a fresh <home> is left alone
    package_twin.py missing <home> <released tsv> <owned prefixes> <host profile> <build profile> <lockfile>
        every released recipe exported into <home> (a seeded home, before any create), then its graph resolved
        there strictly against <lockfile>, as the creates resolve it: exit 0 when the home holds every third-party binary the graphs need, 3 naming each
        one it lacks (1 is a failed conan command, as for every subcommand)
    package_twin.py digest <home> <released tsv>
        one JSON object per released package: its recipe revision, package id, package revision
        and a content digest over the package folder's files
    package_twin.py compare <digest A> <digest B>
        exit 0 when every released package is identical in both, 1 otherwise; the differing ones
        are named with the files whose bytes differ

A third-party binary the seed lacks is NOT built in each home alike: conan builds it in the home's
per-create folder `p/b/<name><hash>/p`, the hash drawn per home, and a dependent's bytes then carry
that folder through the headers it includes (measured 2026-10-09: libpqxx rebuilt in both homes put
`conan-home/p/b/libpq<hash>/p/include/pqxx/params.hxx` into coderoast_infra_postgres, 18 of 20
identical). So `missing` runs before either create, and twin-verify refuses on its finding.

The content digest is DN-142.D3's, computed by malf/artefact_store.py over the package folder,
the root `conanmanifest.txt` excluded: its first line is a creation timestamp, so it differs
between any two creates whatever the bytes. The package revision is compared beside it, because it is what
a consumer pins and what N358's compare reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

import artefact_store


def conan(home: Path, *argv: str, tmpdir: Path | None = None) -> str:
    """`conan <argv>` in `home`; a failure is fatal and names the command."""
    env = {**os.environ, "CONAN_HOME": str(home)}
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    done = subprocess.run(["conan", *argv], capture_output=True, text=True, env=env)
    if done.returncode != 0:
        sys.exit(f"package_twin: `conan {' '.join(argv)}` failed in {home}:\n{done.stderr.strip()}")
    return done.stdout


def seed(source: Path, prefixes: tuple[str, ...], targets: list[Path]) -> None:
    """Restore every third-party package of `source` into each of `targets`, from ONE save."""
    listed = json.loads(conan(source, "list", "*#*:*#*", "--format=json"))["Local Cache"]
    third = {ref: body for ref, body in listed.items() if not ref.split("/")[0].startswith(prefixes)}
    if not third:
        sys.exit(f"package_twin: {source} holds no third-party package to seed from")
    with tempfile.TemporaryDirectory(prefix="package_twin.") as scratch:
        pkglist = Path(scratch) / "pkglist.json"
        pkglist.write_text(json.dumps({"Local Cache": third}))
        archive = Path(scratch) / "third_party.tgz"
        # note: `conan cache save` stages its manifest at `<tempdir>/pkglist.json`, one fixed name, so two concurrent saves delete each other's (measured 2026-10-09); TMPDIR makes it private
        conan(source, "cache", "save", f"--list={pkglist}", f"--file={archive}", tmpdir=Path(scratch))
        for target in targets:
            conan(target, "cache", "restore", str(archive))
            print(f"package_twin: seeded {len(third)} third-party reference(s) from {source} into "
                  f"{target}")


def lock_recipes(lockfile: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Every `name/version#revision` the lock names in any of its requirement lists, outside the owned
    namespaces, sorted; its timestamps dropped (a recipe revision is the digest of its manifest)."""
    document = json.loads(lockfile.read_text())
    named = {entry.split("%", 1)[0] for key, entries in document.items() if key.endswith("requires")
             and isinstance(entries, list) for entry in entries}
    return sorted(reference for reference in named if not reference.split("/", 1)[0].startswith(prefixes))


def held_revisions(home: Path) -> set[str]:
    """Every `name/version#revision` recipe `home` holds."""
    listed = json.loads(conan(home, "list", "*#*", "--format=json")).get("Local Cache", {})
    return {f"{ref}#{revision}" for ref, body in listed.items() for revision in (body.get("revisions") or {})}


def manifest_revision(manifest: Path) -> str:
    """The recipe revision conan derives from `manifest` (`FileTreeManifest.summary_hash`): the md5 of its
    `path: md5` lines sorted by path, each closed by a newline; its first line, the export timestamp, is
    no input."""
    sums = dict(line.rpartition(": ")[::2] for line in manifest.read_text(encoding="utf-8").splitlines()[1:]
                if line.strip())
    return hashlib.md5("".join(f"{path}: {digest}\n" for path, digest in sorted(sums.items()))
                       .encode("utf-8")).hexdigest()


def forged_revisions(home: Path, references: list[str]) -> list[str]:
    """Each of `references` whose recipe manifest in `home` does not digest to its own revision. `conan
    cache check-integrity` compares the files with the manifest and never the manifest with the revision,
    so a folder and a manifest edited together pass it."""
    forged = []
    for reference in references:
        folder = Path(conan(home, "cache", "path", reference).strip())
        derived = manifest_revision(folder / "conanmanifest.txt")
        if derived != reference.split("#", 1)[1]:
            forged.append(f"{reference} (its conanmanifest.txt digests to {derived})")
    return forged


def seed_recipes(lockfile: Path, prefixes: tuple[str, ...], target: Path, sources: list[Path]) -> int:
    """Restore into `target` every third-party recipe revision of `lockfile` it lacks from the first of
    `sources` holding it, recipes alone, then check every recipe of `target` against its manifest and every
    lock revision it holds against that manifest's digest."""
    wanted = lock_recipes(lockfile, prefixes)
    if not wanted:
        print(f"package_twin: REFUSED — {lockfile} names no third-party recipe to seed", file=sys.stderr)
        return 1
    present = held_revisions(target)
    held = {source: held_revisions(source) for source in sources}
    chosen: dict[Path, list[str]] = {}
    absent = []
    for reference in wanted:
        if reference in present:
            continue
        source = next((source for source in sources if reference in held[source]), None)
        if source is None:
            absent.append(reference)
        else:
            chosen.setdefault(source, []).append(reference)
    if absent:
        # note: the lock spans every profile, the MSVC leg's build tools included (msys2, nasm, ...), so a
        # revision no source holds is named here and refused only by the graph that needs it, which then
        # fails naming it: the act has no network to fetch it from.
        print(f"package_twin: {len(absent)} recipe revision(s) of {lockfile.name} held by none of "
              f"{', '.join(map(str, sources))}, not restored: {', '.join(absent)}")
    with tempfile.TemporaryDirectory(prefix="package_twin.recipes.") as scratch:
        for index, (source, references) in enumerate(sorted(chosen.items())):
            pkglist = Path(scratch) / f"pkglist-{index}.json"
            pkglist.write_text(json.dumps({"Local Cache": {
                reference.split("#", 1)[0]: {"revisions": {reference.split("#", 1)[1]: {}}}
                for reference in references}}))
            archive = Path(scratch) / f"recipes-{index}.tgz"
            conan(source, "cache", "save", f"--list={pkglist}", "--no-source", f"--file={archive}",
                  tmpdir=Path(scratch))
            conan(target, "cache", "restore", str(archive))
            print(f"package_twin: restored {len(references)} recipe revision(s) of {lockfile.name} from "
                  f"{source} into {target}, no binary")
    env = {**os.environ, "CONAN_HOME": str(target)}
    checked = subprocess.run(["conan", "cache", "check-integrity", "*#*"], capture_output=True, text=True, env=env)
    if checked.returncode != 0:
        print(f"package_twin: REFUSED — a recipe restored into {target} differs from its own conanmanifest.txt, "
              f"so it is not the revision the lock pins:\n{(checked.stderr or checked.stdout).strip()}",
              file=sys.stderr)
        return 1
    holding = sorted(set(wanted) & held_revisions(target))
    forged = forged_revisions(target, holding)
    if forged:
        print(f"package_twin: REFUSED — a recipe of {target} carries a conanmanifest.txt that is not its "
              f"revision, so it is not the revision the lock pins: {', '.join(forged)}", file=sys.stderr)
        return 1
    print(f"package_twin: {len(holding)} lock recipe revision(s) of {target} match their conanmanifest.txt, "
          f"each manifest digesting to its revision; {sum(map(len, chosen.values()))} restored by this run")
    return 0


SOURCE_TIMEOUT_S = 120
SOURCE_CHUNK_BYTES = 1 << 20


def enabled_remotes(home: Path) -> list[str]:
    """The names of `home`'s enabled remotes, in its resolution order."""
    return [remote["name"] for remote in json.loads(conan(home, "remote", "list", "--format=json"))
            if remote.get("enabled", True)]


def fetch_recipe(home: Path, reference: str, remotes: list[str]) -> str | None:
    """Download the recipe `reference` (`name/version#revision`) into `home` from the first of `remotes`
    holding it, recipe alone. post: the remote it came from, or None when none holds it."""
    env = {**os.environ, "CONAN_HOME": str(home)}
    for remote in remotes:
        done = subprocess.run(["conan", "download", reference, "--only-recipe", "-r", remote],
                              capture_output=True, text=True, env=env)
        if done.returncode == 0:
            return remote
    return None


# The platform every keyed act runs on: the pinned build environment (config/build-environment/Dockerfile) is a
# Linux x86_64 image, and conan's `os` and `arch` settings name it so in every profile malf ships.
ENVIRONMENT_OS, ENVIRONMENT_ARCH = "Linux", "x86_64"


def source_entries(conandata: dict, version: str) -> list[tuple[list[str], str]] | None:
    """The `(urls, sha256)` downloads `conandata` declares for `version`: [] when it declares none. An entry
    keyed `{<os>: {<arch>: {url, sha256}}}` (cmake's binary tool-require) yields the build environment's
    member, [] when it keys none for that os, since no act in the image fetches another platform's file.
    None for any other shape (a git source, a recipe keyed by arch alone): one this provisioning does not
    choose a member of."""
    entry = (conandata.get("sources") or {}).get(version)
    if entry is None:
        return []
    if isinstance(entry, dict) and "url" not in entry and entry and all(
            isinstance(arches, dict) and arches and all(isinstance(item, dict) and "url" in item and "sha256" in item
                                                        for item in arches.values())
            for arches in entry.values()):
        member = (entry.get(ENVIRONMENT_OS) or {}).get(ENVIRONMENT_ARCH)
        entry = [] if member is None else member
        if entry == []:
            return []
    listed = entry if isinstance(entry, list) else [entry]
    if not all(isinstance(item, dict) and "url" in item and "sha256" in item for item in listed):
        return None
    return [([item["url"]] if isinstance(item["url"], str) else list(item["url"]), item["sha256"])
            for item in listed]


def fetch_source(cache: Path, urls: list[str], sha256: str) -> str | None:
    """Download the file `sha256` names into conan's source download cache `cache` (`core.sources:
    download_cache`, read by conan at `<cache>/s/<sha256>`) from the first of `urls` whose bytes digest to
    it, written beside and renamed in, so a reader never sees a partial file. post: None when stored, else
    every url's failure."""
    target = cache / "s" / sha256
    target.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    for url in urls:
        staged = target.parent / f"{sha256}.provisioning-{os.getpid()}"
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=SOURCE_TIMEOUT_S) as answer, staged.open("wb") as out:
                while chunk := answer.read(SOURCE_CHUNK_BYTES):
                    digest.update(chunk)
                    out.write(chunk)
        except (OSError, ValueError) as error:
            staged.unlink(missing_ok=True)
            failures.append(f"{url}: {error}")
            continue
        if digest.hexdigest() != sha256:
            staged.unlink(missing_ok=True)
            failures.append(f"{url}: its bytes digest to {digest.hexdigest()}")
            continue
        os.replace(staged, target)
        return None
    return "; ".join(failures)


def provision(lockfile: Path, prefixes: tuple[str, ...], home: Path, cache: Path) -> int:
    """Make `home` hold every third-party recipe revision `lockfile` names and `cache` every source file those
    recipes declare, ON THE HOST, before the walk's container starts: the walk and every act after it have
    no network (DN-142.D30), so a revision or a source absent here fails a third-party step, naming a remote
    it cannot reach (measured 2026-10-22, step 0 run 39774938501: boost/1.83.0, drogon's requirement, held
    by no home, red B2 and B4). A recipe comes from the home's own remotes in order and is checked against
    its revision; a source is checked against the sha256 its recipe declares, the identity conan reads it
    by. Nothing is fetched when everything is held, so a provisioned seat needs no network at all."""
    if not (home / "settings.yml").is_file():
        print(f"package_twin: nothing provisioned — {home} is not a conan home yet, and the walk that "
              f"creates it seeds it")
        return 0
    wanted = lock_recipes(lockfile, prefixes)
    absent = [reference for reference in wanted if reference not in held_revisions(home)]
    remotes = enabled_remotes(home) if absent else []
    fetched, unfetched = [], []
    for reference in absent:
        remote = fetch_recipe(home, reference, remotes)
        (fetched if remote else unfetched).append(f"{reference} ({remote})" if remote else reference)
    forged = forged_revisions(home, [entry.split(" ", 1)[0] for entry in fetched])
    sources, unsourced, by_platform = [], [], []
    for reference in sorted(set(wanted) & held_revisions(home)):
        folder = Path(conan(home, "cache", "path", reference).strip())
        conandata = folder / "conandata.yml"
        declared = source_entries(yaml.safe_load(conandata.read_text()) or {},
                                  reference.split("/", 1)[1].split("#", 1)[0]) if conandata.is_file() else []
        if declared is None:
            by_platform.append(reference)
            continue
        for urls, sha256 in declared:
            if (cache / "s" / sha256).is_file():
                continue
            failure = fetch_source(cache, urls, sha256)
            (unsourced if failure else sources).append(f"{reference} {sha256}" + (f" ({failure})" if failure else ""))
    print(f"package_twin: provisioned {home} from {lockfile.name}: {len(fetched)} recipe revision(s) fetched"
          f"{': ' + ', '.join(fetched) if fetched else ''}; {len(sources)} source file(s) fetched into {cache}"
          f"{': ' + ', '.join(sources) if sources else ''}")
    if by_platform:
        print(f"package_twin: not provisioned, their sources keyed by platform: {', '.join(by_platform)}")
    refusals = ([f"{len(unfetched)} recipe revision(s) held by none of the remotes {', '.join(remotes)}: "
                 f"{', '.join(unfetched)}"] if unfetched else []) \
        + ([f"a fetched recipe whose conanmanifest.txt is not its revision: {', '.join(forged)}"] if forged else []) \
        + ([f"{len(unsourced)} source file(s) not fetched: {', '.join(unsourced)}"] if unsourced else [])
    if refusals:
        print(f"package_twin: REFUSED — {home} cannot hold what the lock pins, and the walk has no network to "
              f"fetch it: {'; '.join(refusals)}", file=sys.stderr)
        return 1
    return 0


def missing(home: Path, released: Path, prefixes: tuple[str, ...], host: str, build: str,
            lockfile: Path) -> int:
    """Name every third-party binary the released graphs need that `home` does not hold."""
    rows = [row.split("\t") for row in released.read_text().splitlines() if row.strip()]
    for _name, folder, *_rest in rows:
        conan(home, "export", folder)
    lacking: set[str] = set()
    for _name, folder, *_rest in rows:
        graph = json.loads(conan(home, "graph", "info", folder, f"--profile:host={host}",
                                 f"--profile:build={build}", f"--lockfile={lockfile}",
                                 "--format=json"))["graph"]["nodes"]
        lacking.update(f"{node['ref']}:{node['package_id']} ({node['context']})"
                       for node in graph.values()
                       if node.get("binary") == "Missing"
                       and not node["ref"].split("/")[0].startswith(prefixes))
    for entry in sorted(lacking):
        print(f"  MISSING {entry}")
    print(f"package_twin: {home} lacks {len(lacking)} third-party binary(ies) the released graphs need")
    return 3 if lacking else 0


def _folder_digest(folder: Path) -> tuple[str, dict[str, str]]:
    """DN-142.D3's content digest (artefact_store owns it), and each file's mode and digest."""
    whole, entries = artefact_store.tree_digest(folder)
    return whole, {path: f"{mode}:{sha}" for path, mode, sha in entries}


def digest(home: Path, released: Path) -> None:
    """One JSON line per released package: rrev, package id, prev, content digest, file digests."""
    for row in released.read_text().splitlines():
        name = row.split("\t")[0].strip()
        if not name:
            continue
        listed = json.loads(conan(home, "list", f"{name}/*#*:*#*", "--format=json"))["Local Cache"]
        found = [(ref, rrev, package_id, prev)
                 for ref, body in listed.items()
                 for rrev, recipe in body.get("revisions", {}).items()
                 for package_id, package in recipe.get("packages", {}).items()
                 for prev in package.get("revisions", {})]
        if len(found) != 1:
            sys.exit(f"package_twin: {home} must hold exactly one binary of {name}, holds "
                     f"{len(found)}: {found}")
        ref, rrev, package_id, prev = found[0]
        folder = Path(conan(home, "cache", "path", f"{ref}#{rrev}:{package_id}#{prev}").strip())
        whole, files = _folder_digest(folder)
        print(json.dumps({"name": name, "ref": ref, "rrev": rrev, "package_id": package_id,
                          "prev": prev, "content": whole, "files": files}, sort_keys=True))


def compare(first: Path, second: Path) -> int:
    """Exit 0 when every package is identical in both digests, 1 otherwise, naming the diffs."""
    left = {row["name"]: row for row in map(json.loads, first.read_text().splitlines())}
    right = {row["name"]: row for row in map(json.loads, second.read_text().splitlines())}
    if left.keys() != right.keys():
        print(f"package_twin: the two digests name different packages: "
              f"{sorted(left.keys() ^ right.keys())}")
        return 1
    differ = []
    for name in left:
        one, two = left[name], right[name]
        axes = [axis for axis in ("rrev", "package_id", "prev", "content") if one[axis] != two[axis]]
        if axes:
            files = sorted(rel for rel in one["files"].keys() | two["files"].keys()
                           if one["files"].get(rel) != two["files"].get(rel))
            differ.append(name)
            print(f"  DIFFER {name}: {', '.join(axes)}; files: {', '.join(files) or 'none'}")
    print(f"package_twin: {len(left) - len(differ)} of {len(left)} released package(s) identical "
          f"across the two homes")
    return 1 if differ else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "seed":
        seed(Path(argv[1]), tuple(argv[2].split()), [Path(target) for target in argv[3:]])
        return 0
    if len(argv) >= 5 and argv[0] == "seed-recipes":
        return seed_recipes(Path(argv[1]), tuple(argv[2].split()), Path(argv[3]), [Path(home) for home in argv[4:]])
    if len(argv) == 5 and argv[0] == "provision":
        return provision(Path(argv[1]), tuple(argv[2].split()), Path(argv[3]), Path(argv[4]))
    if len(argv) == 7 and argv[0] == "missing":
        return missing(Path(argv[1]), Path(argv[2]), tuple(argv[3].split()), argv[4], argv[5],
                       Path(argv[6]))
    if len(argv) == 3 and argv[0] == "digest":
        digest(Path(argv[1]), Path(argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        return compare(Path(argv[1]), Path(argv[2]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
