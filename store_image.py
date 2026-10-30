#!/usr/bin/env python3
"""An image step: a container image assembled from stored bytes, built once, stored as one OCI archive.

DN-142.D9 (the Founder, 2026-10-10, N360 plan ruling R-5): an image is a build step whose inputs are
stored bytes, built once and stored as an object; the tag's publish copies that archive to the
registry by digest and builds nothing. DN-142.D33 (3) moves the build from step 0 into the image's
repository's candidate store job (`candidate`, below); step 0's `build` stays until that path retires
(DN-142.D33 slice (f)). Its inputs, each identified by bytes or a git object:
  * every executable the image carries, by its ENTRY DIGEST in the create record of its package
    (step 0: this run's; a candidate: its ship-form create), written from the stored transport and
    verified against that digest (a stripped, runpath-free published entry, DN-142.D20 (3), DN-142.D22);
  * every runtime library the image declares, resolved by the ship compiler
    (`<cxx> -print-file-name=<name>`, symlinks followed) and identified by its SHA-256;
  * the image's repository tree at the commit step 0 recorded, exported with `git archive` (never
    the disk): its Dockerfile and every file the Dockerfile copies. The base image is the
    Dockerfile's own `FROM … @sha256:` line, so the tree carries it.

THE STAGING CONTRACT, which the Dockerfile copies from: the build context is that export, plus
`.store-image/<entry path>` for each executable and `.store-image/runtime/<name>` for each runtime
library. A tree that already holds `.store-image` is refused: staged bytes are never mixed with
tracked ones.

NON-REPRODUCIBLE, DECLARED (DN-142.D9: 9 of 12 layers differ across two builds, measured): the
record is addressed by its RUN, the run identity being a key member (a declared exception to
DN-142.D2, as an ingestion's run id is, DN-142.D20 (2)), so a second build is a second record and
never a compare, and no step ever reuses one. `--no-cache` keeps every layer a product of this
run's inputs: no layer of an earlier build, whose inputs no record names, enters the archive.

THE ARCHIVE IS JUDGED BEFORE IT IS STORED: one manifest in its index, for linux/amd64, and every
blob the manifest names present and hashing to its digest, so the publish side can push it verbatim
and the registry's digest is the archive's.

    store_image.py build <store> <plan.json> <steps.json> <image> <repository dir> <commit> <cxx> <run> <scratch>
        builds the image the plan declares, stores the archive and its record; the record's line is
        `artefact_store: STORED <image> image key <key> image manifest sha256:<digest>`. Exit 0
        stored, 1 refused (the reason on stderr, nothing stored)
    store_image.py candidate <store> <repository dir> <run> <scratch>
        a repository's CANDIDATE store job (DN-142.D33 (3)): every image the repository's packages.yml at
        HEAD declares under `images:`, built from the ship-form create records of <store> (exactly one per
        package an `image` site names, its entry digest verified) and the runtime libraries the PINNED
        BUILD ENVIRONMENT's ship compiler resolves (the compiler runs in the image, never on the runner,
        whose host carries no gcc-16), then stored as above; a repository declaring no image stores
        nothing and exits 0
    store_image.py selftest
        every refusal and the stored record, with a fake builder (no docker)

Exit 0 done, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_manifest  # noqa: E402 — a sibling module, located from this file

STAGING = ".store-image"
RUNTIME = "runtime"
PLATFORM = "linux/amd64"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUNTIME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
IMAGE_KIND = "image"


class Refused(Exception):
    """A check failed; the message names what differed, and nothing is stored."""


Builder = Callable[[Path, str, Path], None]


def docker_build(context: Path, dockerfile: str, archive: Path) -> None:
    """`docker buildx build` of `context` into an OCI archive, with no cache and no attestation
    manifests, so the archive's index holds the image manifest alone."""
    done = subprocess.run(["docker", "buildx", "build", "--platform", PLATFORM, "--no-cache",
                           "--provenance=false", "--sbom=false",
                           "--output", f"type=oci,dest={archive}",
                           "--file", str(context / dockerfile), str(context)],
                          capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise Refused(f"docker buildx build exited {done.returncode}: "
                      f"{(done.stderr or done.stdout).strip()[-1500:]}")


def _member(bundle: tarfile.TarFile, name: str) -> bytes | None:
    try:
        member = bundle.getmember(name)
    except KeyError:
        return None
    extracted = bundle.extractfile(member) if member.isfile() else None
    return extracted.read() if extracted is not None else None


def judge_archive(archive: Path) -> tuple[str, list[list[str]]]:
    """(the image manifest's digest, the `[blob path, "file", sha256]` entries of every blob it
    names), once the archive is one linux/amd64 image whose every named blob is present and hashes
    to its name."""
    with tarfile.open(archive, "r:*") as bundle:
        index_bytes = _member(bundle, "index.json")
        if index_bytes is None:
            raise Refused(f"{archive.name} holds no index.json: not an OCI image layout")
        index = json.loads(index_bytes)
        manifests = index.get("manifests") or []
        if len(manifests) != 1:
            raise Refused(f"{archive.name}'s index names {len(manifests)} manifests, not one image")
        descriptor = manifests[0]
        platform = descriptor.get("platform") or {}
        if descriptor.get("mediaType") != OCI_MANIFEST or \
                f"{platform.get('os')}/{platform.get('architecture')}" != PLATFORM:
            raise Refused(f"{archive.name}'s one manifest is {descriptor.get('mediaType')} for "
                          f"{platform.get('os')}/{platform.get('architecture')}, not an image manifest "
                          f"for {PLATFORM}")

        def blob(digest: str, what: str) -> bytes:
            matched = DIGEST.match(digest or "")
            if not matched:
                raise Refused(f"{archive.name}: the {what} digest {digest!r} is not sha256:<64 hex>")
            data = _member(bundle, f"blobs/sha256/{matched.group(1)}")
            if data is None:
                raise Refused(f"{archive.name} holds no blob for its {what} {digest}")
            if hashlib.sha256(data).hexdigest() != matched.group(1):
                raise Refused(f"{archive.name}'s {what} blob does not hash to {digest}")
            return data

        manifest = json.loads(blob(descriptor["digest"], "manifest"))
        named = [("manifest", descriptor["digest"]), ("config", manifest.get("config", {}).get("digest", ""))]
        named += [(f"layer {position}", layer.get("digest", ""))
                  for position, layer in enumerate(manifest.get("layers") or [])]
        if len(named) < 3:
            raise Refused(f"{archive.name}'s manifest names no layer")
        entries = []
        for what, digest in named:
            data = blob(digest, what)
            entries.append([f"blobs/sha256/{digest.removeprefix('sha256:')}", "file",
                            hashlib.sha256(data).hexdigest()])
    return descriptor["digest"], sorted({tuple(entry): entry for entry in entries}.values())


def _plan_image(plan: dict, image: str) -> tuple[str, dict]:
    """(the repository that declares `image`, its plan entry)."""
    found = [(repo, body["images"][image]) for repo, body in sorted(plan.get("repositories", {}).items())
             if image in (body.get("images") or {})]
    if len(found) != 1:
        raise Refused(f"the plan declares the image {image!r} in {len(found)} repositories, not one")
    return found[0]


def _export(repository: Path, commit: str, into: Path) -> str:
    """The repository's tree at `commit`, written under `into`; its git tree id."""
    tree = subprocess.run(["git", "-C", str(repository), "rev-parse", f"{commit}^{{tree}}"],
                          capture_output=True, text=True, check=False)
    archived = subprocess.run(["git", "-C", str(repository), "archive", "--format=tar", commit],
                              capture_output=True, check=False)
    if tree.returncode != 0 or archived.returncode != 0:
        raise Refused(f"{repository} cannot export {commit}: "
                      f"{(tree.stderr or archived.stderr.decode('utf-8', 'replace')).strip()[-300:]}")
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as bundle:
        if any(member.name == STAGING or member.name.startswith(f"{STAGING}/") for member in bundle):
            raise Refused(f"{repository} tracks {STAGING}/ at {commit}: staged bytes are never mixed "
                          f"with tracked ones")
        bundle.extractall(into, filter=_inside)
    return tree.stdout.strip()


def _inside(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """tarfile's `data` filter, except that a tracked symlink resolving outside the tree (a
    repository's `.clang-format` link into `../malf/config/`) is left out rather than fatal: no
    build context may follow it, and a Dockerfile that copied it fails its build by name."""
    try:
        return tarfile.data_filter(member, destination)
    except tarfile.LinkOutsideDestinationError:
        return None


def _runtime(cxx: str, name: str, into: Path) -> str:
    """The runtime library `name` the compiler resolves, copied (symlinks followed) into `into`;
    its SHA-256."""
    asked = subprocess.run([cxx, f"-print-file-name={name}"], capture_output=True, text=True, check=False)
    resolved = Path(asked.stdout.strip())
    if asked.returncode != 0 or not resolved.is_absolute() or not resolved.is_file():
        raise Refused(f"{cxx} does not resolve the runtime {name}: it answered {asked.stdout.strip()!r}")
    target = into / name
    shutil.copyfile(resolved.resolve(), target)
    return artefact_store.file_sha256(target)


# A package's create record for an image, as the caller finds it: (its key, the record), or None.
Created = Callable[[str], "tuple[str, dict] | None"]
# A runtime library resolved into a directory: (name, directory) -> its SHA-256.
Resolver = Callable[[str, Path], str]


def build(store: Path, plan: dict, steps: list[dict], image: str, repository: Path, commit: str,
          cxx: str, run: str, scratch: Path, builder: Builder = docker_build) -> int:
    """Build `image` from this step-0 run's stored entries and store it; artefact_store.LocalStore's
    exit: 0 stored (a new run is always a new key)."""
    repo, declared = _plan_image(plan, image)
    if repository.resolve().name != repo and repository.name != repo:
        raise Refused(f"the image {image} is {repo}'s, and the repository given is {repository.name}")
    by_id = {step["id"]: step for step in steps}
    local = artefact_store.LocalStore(store)

    def created(package: str) -> tuple[str, dict] | None:
        step = by_id.get(f"{package}/package")
        record = local.record(step["key"]) if step else None
        return (step["key"], record) if record is not None else None

    return _assemble(store, image, repo, declared, created, "step 0 stored no {package} package in this run",
                     repository, commit, lambda name, into: _runtime(cxx, name, into), run, scratch, builder)


def _assemble(store: Path, image: str, repo: str, declared: dict, created: Created, absent: str,
              repository: Path, commit: str, resolve: Resolver, run: str, scratch: Path, builder: Builder) -> int:
    """The image step itself, whatever found its package records: the context exported at `commit`,
    each executable staged from its create record's transport at its entry digest, each runtime library
    resolved, the archive built, judged and stored under a record addressed by `run`."""
    if not RUN_ID.match(run):
        raise Refused(f"{run!r} is not a run identity")
    local = artefact_store.LocalStore(store)
    context = scratch / "context"
    context.mkdir(parents=True)
    tree = _export(repository, commit, context)
    dockerfile = str(declared["dockerfile"])
    if not (context / dockerfile).is_file():
        raise Refused(f"{repo} at {commit} holds no Dockerfile {dockerfile}")
    staged = context / STAGING
    (staged / RUNTIME).mkdir(parents=True)
    executables = []
    for executable in sorted(declared.get("executables") or [], key=lambda row: (row["package"], row["entry"])):
        package, entry = executable["package"], executable["entry"]
        found = created(package)
        if found is None:
            raise Refused(f"{image}: " + absent.format(package=package))
        key, record = found
        output = record["outputs"]["package"]
        held = {path: digest for path, _mode, digest in output["entries"]}
        if entry not in held:
            raise Refused(f"{image}: {package} holds no entry {entry}")
        target = staged / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            store_manifest.extract(store / "objects" / output["transport"], entry, held[entry], target)
        except store_manifest.Refused as refused:
            raise Refused(f"{image}: {refused}") from refused
        target.chmod(0o755)
        executables.append({"package": package, "entry": entry, "digest": held[entry], "record": key})
    if not executables:
        raise Refused(f"the image {image} carries no executable")
    runtime = {name: resolve(name, staged / RUNTIME) for name in sorted(declared.get("runtime") or [])}
    archive = scratch / f"{image}.oci.tar"
    builder(context, dockerfile, archive)
    manifest, entries = judge_archive(archive)
    document = {"step": {"kind": IMAGE_KIND, "subject": image, "repository": repo, "reproducible": False},
                "run": run, "tree": tree, "dockerfile": dockerfile,
                "executables": executables, "runtime": runtime}
    return local.commit(document, {"image": {"manifest": manifest, "reproducible": False, "entries": entries}},
                        {"image": ("archive", lambda: archive)})


# ── the candidate's image step (DN-142.D33 (3)) ─────────────────────────────────────────────────────

SHIP_PROFILE = Path(__file__).resolve().parent / "profiles" / "linux-gcc16-release"
CXX_LINE = re.compile(r"^CXX=(\S+)$", re.MULTILINE)
PACKAGES_FILE = "packages.yml"


def declared_images(repository: Path, commit: str) -> dict[str, dict]:
    """{image: {dockerfile, runtime, executables: [{package, entry}]}} as the repository's packages.yml at
    `commit` declares them: each `images:` row, carrying every executable whose `published` sites name it
    by `image:`. Raises Refused on an image no site names, a site naming an undeclared image, or a row off
    the shape (the rules `scripts/pharos/publication.py` applies to the whole reach, here over one
    repository, which is all a candidate sees)."""
    import yaml

    shown = subprocess.run(["git", "-C", str(repository), "show", f"{commit}:{PACKAGES_FILE}"],
                           capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        raise Refused(f"{repository} holds no {PACKAGES_FILE} at {commit}: {shown.stderr.strip()}")
    document = yaml.safe_load(shown.stdout) or {}
    images: dict[str, dict] = {}
    for image, row in sorted((document.get("images") or {}).items()):
        if not isinstance(row, dict) or set(row) - {"dockerfile", "runtime"} \
                or not isinstance(row.get("dockerfile"), str) or not isinstance(row.get("runtime", []), list):
            raise Refused(f"{PACKAGES_FILE} images {image}: an image is {{dockerfile, runtime: [<library>...]}}, "
                          f"not {row!r}")
        images[image] = {"dockerfile": row["dockerfile"], "runtime": sorted(row.get("runtime") or []),
                         "executables": []}
    for package, row in sorted((document.get("packages") or {}).items()):
        for entry, kind in sorted(((row or {}).get("executables") or {}).items()):
            sites = kind.get("published") if isinstance(kind, dict) else None
            named = sites.get("image") if isinstance(sites, dict) else None
            if named is None:
                continue
            if named not in images:
                raise Refused(f"{PACKAGES_FILE} {package} {entry}: its image site names {named!r}, which "
                              f"`images:` does not declare")
            images[named]["executables"].append({"package": package, "entry": entry})
    unnamed = sorted(image for image, row in images.items() if not row["executables"])
    if unnamed:
        raise Refused(f"{PACKAGES_FILE} images {', '.join(unnamed)}: no executable's image site names it, so it "
                      f"would publish nothing the candidate stored")
    return images


def ship_created(store: Path) -> Created:
    """The ONE ship-form create record of a package in `store` (a candidate's: DN-142.D31 (1)), or None."""
    records = [json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))]

    def created(package: str) -> tuple[str, dict] | None:
        found = [record for record in records if record["inputs"]["step"].get("kind") == "conan-create"
                 and record["inputs"]["step"].get("form") == "ship"
                 and record["inputs"]["step"].get("package") == package]
        if len(found) > 1:
            raise Refused(f"the store holds {len(found)} ship-form create records of {package}, not one")
        return (found[0]["key"], found[0]) if found else None
    return created


def ship_compiler() -> str:
    """The C++ compiler the ship profile names (`CXX=` in malf's linux-gcc16-release): a path inside the
    pinned build environment, where every ship-form create ran."""
    found = CXX_LINE.search(SHIP_PROFILE.read_text(encoding="utf-8"))
    if found is None:
        raise Refused(f"{SHIP_PROFILE} names no `CXX=` compiler")
    return found.group(1)


def image_runtime(cxx: str) -> Resolver:
    """A runtime library resolved by the ship compiler INSIDE the pinned build environment
    (build_environment.py run, the directory its one mount) and copied out with its symlinks followed."""
    launcher = Path(__file__).resolve().parent / "build_environment.py"

    def resolve(name: str, into: Path) -> str:
        if not RUNTIME_NAME.match(name):
            raise Refused(f"{name!r} is not a runtime library name")
        into = into.resolve()
        done = subprocess.run([sys.executable, str(launcher), "run", "--mount", str(into), "--",
                               "sh", "-c", 'cp -L -- "$("$0" -print-file-name="$1")" "$2"', cxx, name,
                               str(into / name)], cwd=into, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, check=False)
        target = into / name
        if done.returncode != 0 or not target.is_file():
            raise Refused(f"the build environment's {cxx} did not resolve the runtime {name} (exit {done.returncode}): "
                          f"{(done.stderr or done.stdout).strip()[-600:]}")
        return artefact_store.file_sha256(target)
    return resolve


def candidate(store: Path, repository: Path, run: str, scratch: Path, builder: Builder = docker_build,
              resolve: Resolver | None = None, say: Callable[[str], None] = print) -> int:
    """Every image the repository at HEAD declares, built from the candidate store's ship-form creates
    and stored there; 0 when it declares none. `repository`'s directory name is the repository's."""
    commit = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip()
    images = declared_images(repository, commit or "HEAD")
    if not images:
        say(f"store_image: {repository.resolve().name} declares no image at {commit}: nothing to build")
        return 0
    created = ship_created(store)
    resolver = resolve if resolve is not None else image_runtime(ship_compiler())
    worst = 0
    for image, declared in sorted(images.items()):
        worst = max(worst, _assemble(store, image, repository.resolve().name, declared, created,
                                     "the candidate holds no ship-form create of {package}", repository, commit,
                                     resolver, run, scratch / image, builder))
    return worst


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────


def _layout(manifest_extra: dict | None = None, platform: str = PLATFORM, drop_layer: bool = False,
            manifests: int = 1) -> Callable[[Path, str, Path], None]:
    """A fake builder writing a one-layer OCI layout that names what the context staged."""
    def write(context: Path, dockerfile: str, archive: Path) -> None:
        assert (context / dockerfile).is_file()
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as bundle:
            for path in sorted((context / STAGING).rglob("*")):
                if path.is_file():
                    bundle.add(path, arcname=path.relative_to(context).as_posix())
        blobs = {"layer": layer.getvalue(), "config": b'{"architecture":"amd64","os":"linux"}'}
        digests = {name: "sha256:" + hashlib.sha256(data).hexdigest() for name, data in blobs.items()}
        manifest = json.dumps({"schemaVersion": 2, "mediaType": OCI_MANIFEST,
                               "config": {"digest": digests["config"]},
                               "layers": [{"digest": digests["layer"]}], **(manifest_extra or {})}).encode()
        blobs["manifest"] = manifest
        digests["manifest"] = "sha256:" + hashlib.sha256(manifest).hexdigest()
        os_name, architecture = platform.split("/")
        index = {"schemaVersion": 2, "mediaType": OCI_INDEX,
                 "manifests": [{"mediaType": OCI_MANIFEST, "digest": digests["manifest"],
                                "platform": {"os": os_name, "architecture": architecture}}] * manifests}
        with tarfile.open(archive, "w") as bundle:
            for name, data in [("index.json", json.dumps(index).encode()),
                               ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')] + \
                    [(f"blobs/sha256/{digests[name].removeprefix('sha256:')}", blobs[name])
                     for name in blobs if not (drop_layer and name == "layer")]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
    return write


def _fixture(root: Path) -> tuple[Path, dict, list[dict], Path, str, str]:
    """A store holding a server package with one published entry, the plan declaring its image, the
    run's steps, the image's repository at a commit, and a fake compiler resolving one runtime."""
    store = root / "store"
    (store / "objects").mkdir(parents=True)
    (store / "records").mkdir()
    server = b"\x7fELF server bytes"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("b/serve0123/p/bin/server")
        info.size = len(server)
        bundle.addfile(info, io.BytesIO(server))
    transport = hashlib.sha256(buffer.getvalue()).hexdigest()
    (store / "objects" / transport).write_bytes(buffer.getvalue())
    entries = [["bin/server", "executable", hashlib.sha256(server).hexdigest()]]
    document = {"step": {"kind": "conan-create", "package": "server_pkg", "executables": {"bin/server": "published"}}}
    key = artefact_store.key_of(document)
    (store / "records" / f"{key}.json").write_text(json.dumps(
        {"key": key, "inputs": document,
         "outputs": {"package": {"content": artefact_store.key_of(entries), "entries": entries,
                                 "transport": transport}}}))
    steps = [{"id": "server_pkg/package", "key": key, "verdict": None}]
    repository = root / "server-repo"
    repository.mkdir()
    git = ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "docker").mkdir()
    (repository / "docker" / "Dockerfile").write_text("FROM scratch\nCOPY .store-image/bin/server /server\n")
    (repository / "NOTICE").write_text("notice\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    commit = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    runtime = root / "toolchain" / "lib64" / "libstdc++.so.6.0.36"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime bytes")
    (runtime.parent / "libstdc++.so.6").symlink_to(runtime.name)
    cxx = root / "toolchain" / "g++"
    cxx.write_text(f"#!/bin/sh\ncase \"$1\" in -print-file-name=libstdc++.so.6) echo {runtime.parent}/libstdc++.so.6;;"
                   f" *) echo \"$1\" | sed 's/-print-file-name=//';; esac\n")
    cxx.chmod(0o755)
    plan = {"format": store_manifest.PLAN_FORMAT, "visibility": {"server-repo": "private"},
            "repositories": {"server-repo": {"assets": [],
        "images": {"server-image": {"dockerfile": "docker/Dockerfile", "runtime": ["libstdc++.so.6"],
                                    "executables": [{"package": "server_pkg", "entry": "bin/server",
                                                     "source": "server-repo"}]}}}}}
    return store, plan, steps, repository, commit, str(cxx)


CANDIDATE_PACKAGES = """\
images:
  server-image:
    dockerfile: docker/Dockerfile
    runtime: [libstdc++.so.6]
packages:
  server_pkg:
    release: false
    executables:
      bin/server: {published: {image: server-image}}
"""


def _candidate_fixture(root: Path, packages: str, ship: bool = True) -> tuple[Path, Path]:
    """`_fixture`'s store and repository, the create record turned into a candidate's ship-form create and
    the repository committing `packages` as its packages.yml (its directory named `server-repo`)."""
    store, _plan, _steps, repository, _commit, _cxx = _fixture(root)
    record_path = next((store / "records").iterdir())
    body = json.loads(record_path.read_text())
    if ship:
        body["inputs"]["step"] = {"kind": "conan-create", "package": "server_pkg", "profile": "linux-gcc16-release",
                                  "form": "ship"}
    key = artefact_store.key_of(body["inputs"])
    body["key"] = key
    record_path.unlink()
    (store / "records" / f"{key}.json").write_text(json.dumps(body))
    (repository / PACKAGES_FILE).write_text(packages)
    git = ["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "packages"], check=True)
    return store, repository


def _fake_runtime(name: str, into: Path) -> str:
    (into / name).write_bytes(b"image runtime bytes")
    return artefact_store.file_sha256(into / name)


def candidate_arms(arm: Callable, failures: list[str], root: Path) -> None:
    """The candidate verb's arms (DN-142.D33 (3)): built from the one ship-form create, stored with the
    resolved runtime; every declaration and record defect refused before anything is stored."""
    quiet: Callable[[str], None] = lambda _line: None  # noqa: E731 — a sink for the verb's lines
    store, repository = _candidate_fixture(root / "a", CANDIDATE_PACKAGES)
    arm("CANDIDATE: the image is built from the candidate's ship-form create and the build environment's runtime",
        lambda: candidate(store, repository, "run-c1", root / "s1", _layout(), _fake_runtime, quiet), None)
    images = [json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))
              if json.loads(path.read_text())["inputs"]["step"]["kind"] == IMAGE_KIND]
    ship = [json.loads(path.read_text())["key"] for path in sorted((store / "records").glob("*.json"))
            if json.loads(path.read_text())["inputs"]["step"].get("form") == "ship"]
    if len(images) != 1 or images[0]["inputs"]["executables"][0]["record"] != ship[0] or \
            images[0]["inputs"]["step"]["repository"] != "server-repo" or \
            images[0]["inputs"]["runtime"] != {"libstdc++.so.6": hashlib.sha256(b"image runtime bytes").hexdigest()}:
        failures.append(f"CANDIDATE: the stored image records {[body['inputs'] for body in images]}, want one naming "
                        f"the ship-form create {ship}, the repository server-repo and the resolved runtime")
    bare, bare_repository = _candidate_fixture(root / "b", "packages:\n  server_pkg: {release: true}\n")
    if arm("CANDIDATE: a repository declaring no image stores nothing and exits 0",
           lambda: candidate(bare, bare_repository, "run-c2", root / "s2", _layout(), _fake_runtime, quiet),
           None) != 0 or len(list((bare / "records").glob("*.json"))) != 1:
        failures.append("CANDIDATE: a repository declaring no image stored a record or did not exit 0")
    unnamed, unnamed_repository = _candidate_fixture(
        root / "c", CANDIDATE_PACKAGES.replace("{published: {image: server-image}}", "run-from-cache"))
    arm("CANDIDATE: an image no executable's site names is refused",
        lambda: candidate(unnamed, unnamed_repository, "run-c3", root / "s3", _layout(), _fake_runtime, quiet),
        "no executable's image site names it")
    stray, stray_repository = _candidate_fixture(
        root / "d", CANDIDATE_PACKAGES.replace("{image: server-image}", "{image: other-image}"))
    arm("CANDIDATE: a site naming an undeclared image is refused",
        lambda: candidate(stray, stray_repository, "run-c4", root / "s4", _layout(), _fake_runtime, quiet),
        "which `images:` does not declare")
    writer, writer_repository = _candidate_fixture(root / "e", CANDIDATE_PACKAGES, ship=False)
    arm("CANDIDATE: a store with no ship-form create of the package is refused",
        lambda: candidate(writer, writer_repository, "run-c5", root / "s5", _layout(), _fake_runtime, quiet),
        "the candidate holds no ship-form create of server_pkg")
    twice, twice_repository = _candidate_fixture(root / "f", CANDIDATE_PACKAGES)
    held = next((twice / "records").iterdir())
    second = json.loads(held.read_text())
    second["inputs"]["step"]["profile"] = "another-profile"
    second["key"] = artefact_store.key_of(second["inputs"])
    (twice / "records" / f"{second['key']}.json").write_text(json.dumps(second))
    arm("CANDIDATE: two ship-form creates of one package are refused, never one of them chosen",
        lambda: candidate(twice, twice_repository, "run-c6", root / "s6", _layout(), _fake_runtime, quiet),
        "2 ship-form create records of server_pkg")
    if len([path for path in (twice / "records").glob("*.json")
            if json.loads(path.read_text())["inputs"]["step"]["kind"] == IMAGE_KIND]):
        failures.append("CANDIDATE: a refused candidate image stored a record")
    if CXX_LINE.search(SHIP_PROFILE.read_text(encoding="utf-8")) is None:
        failures.append(f"CANDIDATE: {SHIP_PROFILE} names no CXX= the build environment's runtime is resolved by")


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, run: Callable[[], object], refusal: str | None) -> object:
        armed[0] += 1
        try:
            result = run()
        except Refused as refused:
            if refusal is None or refusal not in str(refused):
                failures.append(f"{label}: refused with {refused}, want "
                                f"{'success' if refusal is None else repr(refusal)}")
            return None
        if refusal is not None:
            failures.append(f"{label}: succeeded, want a refusal containing {refusal!r}")
        return result

    def records(store: Path) -> list[dict]:
        return [json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))
                if json.loads(path.read_text())["inputs"]["step"]["kind"] == IMAGE_KIND]

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        store, plan, steps, repository, commit, cxx = _fixture(root / "a")
        arm("the image is built from the stored entry and the resolved runtime, and stored",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "run-1",
                          root / "s1", _layout()), None)
        stored = records(store)
        if len(stored) != 1:
            failures.append(f"{len(stored)} image record(s) stored, want 1")
        else:
            body = stored[0]
            archive = store / "objects" / body["outputs"]["image"]["archive"]
            with tarfile.open(archive) as bundle:
                index = json.loads(bundle.extractfile("index.json").read())
                digest = index["manifests"][0]["digest"].removeprefix("sha256:")
                manifest = json.loads(bundle.extractfile(f"blobs/sha256/{digest}").read())
                layer = bundle.extractfile(f"blobs/sha256/{manifest['layers'][0]['digest'].removeprefix('sha256:')}")
                with tarfile.open(fileobj=io.BytesIO(layer.read())) as staged:
                    files = {member.name: staged.extractfile(member).read() for member in staged if member.isfile()}
            want_files = {f"{STAGING}/bin/server": b"\x7fELF server bytes",
                          f"{STAGING}/{RUNTIME}/libstdc++.so.6": b"runtime bytes"}
            if files != want_files:
                failures.append(f"the image's context staged {sorted(files)}, want {sorted(want_files)} with their bytes")
            document = body["inputs"]
            if document["executables"] != [{"package": "server_pkg", "entry": "bin/server",
                                             "digest": hashlib.sha256(b"\x7fELF server bytes").hexdigest(),
                                             "record": steps[0]["key"]}] or \
                    document["runtime"] != {"libstdc++.so.6": hashlib.sha256(b"runtime bytes").hexdigest()} or \
                    document["run"] != "run-1" or body["outputs"]["image"]["manifest"] != f"sha256:{digest}":
                failures.append(f"the image record does not name its inputs and manifest: {json.dumps(body)[:400]}")
        arm("a second build of the same inputs is a second record, addressed by its run, never a compare",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "run-2",
                          root / "s2", _layout()), None)
        if len(records(store)) != 2:
            failures.append(f"two runs stored {len(records(store))} image record(s), want 2")

        arm("an image no repository of the plan declares is refused",
            lambda: build(store, plan, steps, "no-image", repository, commit, cxx, "run-3", root / "s3", _layout()),
            "in 0 repositories")
        arm("an image whose package step 0 did not store in this run is refused",
            lambda: build(store, plan, [], "server-image", repository, commit, cxx, "run-4", root / "s4", _layout()),
            "stored no server_pkg package in this run")
        bad = json.loads(json.dumps(plan))
        bad["repositories"]["server-repo"]["images"]["server-image"]["executables"][0]["entry"] = "bin/other"
        arm("an executable the stored package does not hold is refused",
            lambda: build(store, bad, steps, "server-image", repository, commit, cxx, "run-5", root / "s5", _layout()),
            "holds no entry bin/other")
        arm("an archive whose index names two manifests is refused",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "run-6", root / "s6",
                          _layout(manifests=2)), "names 2 manifests")
        arm("an archive for another platform is refused",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "run-7", root / "s7",
                          _layout(platform="linux/arm64")), "not an image manifest for linux/amd64")
        arm("an archive missing a blob its manifest names is refused",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "run-8", root / "s8",
                          _layout(drop_layer=True)), "holds no blob for its layer 0")
        arm("a run identity that is not one is refused",
            lambda: build(store, plan, steps, "server-image", repository, commit, cxx, "/tmp/x", root / "s9",
                          _layout()), "is not a run identity")
        if len(records(store)) != 2:
            failures.append(f"a refused build stored a record: {len(records(store))} image record(s), want 2")

        tampered_store, tampered_plan, tampered_steps, tampered_repo, tampered_commit, tampered_cxx = _fixture(root / "b")
        transport = next((tampered_store / "objects").iterdir())
        data = bytearray(transport.read_bytes())
        transport.write_bytes(bytes(data))
        record_path = next((tampered_store / "records").iterdir())
        body = json.loads(record_path.read_text())
        body["outputs"]["package"]["entries"][0][2] = "e" * 64
        record_path.write_text(json.dumps(body))
        arm("an entry whose stored bytes differ from the record's digest is refused",
            lambda: build(tampered_store, tampered_plan, tampered_steps, "server-image", tampered_repo,
                          tampered_commit, tampered_cxx, "run-10", root / "s10", _layout()), "hashes to")

        staged_store, staged_plan, staged_steps, staged_repo, _commit, staged_cxx = _fixture(root / "c")
        (staged_repo / STAGING).mkdir()
        (staged_repo / STAGING / "planted").write_text("x\n")
        subprocess.run(["git", "-C", str(staged_repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", "add", "."], check=True)
        subprocess.run(["git", "-C", str(staged_repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "plant"], check=True)
        arm("a tree that tracks the staging directory is refused",
            lambda: build(staged_store, staged_plan, staged_steps, "server-image", staged_repo, "HEAD",
                          staged_cxx, "run-11", root / "s11", _layout()), "tracks .store-image/")

        candidate_arms(arm, failures, root / "candidate")

    for failure in failures:
        print(f"store_image selftest FAILED: {failure}")
    print(f"store_image selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 5 and argv[0] == "candidate":
            return candidate(Path(argv[1]), Path(argv[2]), argv[3], Path(argv[4]))
        if len(argv) == 10 and argv[0] == "build":
            return build(Path(argv[1]), json.loads(Path(argv[2]).read_text()), json.loads(Path(argv[3]).read_text()),
                         argv[4], Path(argv[5]), argv[6], argv[7], argv[8], Path(argv[9]))
    except Refused as refused:
        print(f"store_image: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
