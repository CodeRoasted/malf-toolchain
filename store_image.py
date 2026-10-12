#!/usr/bin/env python3
"""An image step at step 0: a container image assembled from stored bytes, built once, stored as one OCI archive.

DN-142.D9 (the Founder, 2026-10-10, N360 plan ruling R-5): an image is a build step whose inputs are
stored bytes, built AT STEP 0 and stored as an object; the tag's publish copies that archive to the
registry by digest and builds nothing. Its inputs here, each identified by bytes or a git object:
  * every executable the plan's image carries, by its ENTRY DIGEST in this run's create record of
    its package, written from the stored transport and verified against that digest (a stripped,
    runpath-free published entry, DN-142.D20 (3), DN-142.D22);
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


def build(store: Path, plan: dict, steps: list[dict], image: str, repository: Path, commit: str,
          cxx: str, run: str, scratch: Path, builder: Builder = docker_build) -> int:
    """Build `image` from this run's stored entries and store it; artefact_store.LocalStore's
    exit: 0 stored (a new run is always a new key)."""
    if not RUN_ID.match(run):
        raise Refused(f"{run!r} is not a run identity")
    repo, declared = _plan_image(plan, image)
    if repository.resolve().name != repo and repository.name != repo:
        raise Refused(f"the image {image} is {repo}'s, and the repository given is {repository.name}")
    by_id = {step["id"]: step for step in steps}
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
        step = by_id.get(f"{package}/package")
        record = local.record(step["key"]) if step else None
        if record is None:
            raise Refused(f"{image}: step 0 stored no {package} package in this run")
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
        executables.append({"package": package, "entry": entry, "digest": held[entry], "record": step["key"]})
    if not executables:
        raise Refused(f"the plan's image {image} carries no executable")
    runtime = {name: _runtime(cxx, name, staged / RUNTIME) for name in sorted(declared.get("runtime") or [])}
    archive = scratch / f"{image}.oci.tar"
    builder(context, dockerfile, archive)
    manifest, entries = judge_archive(archive)
    document = {"step": {"kind": IMAGE_KIND, "subject": image, "repository": repo, "reproducible": False},
                "run": run, "tree": tree, "dockerfile": dockerfile,
                "executables": executables, "runtime": runtime}
    return local.commit(document, {"image": {"manifest": manifest, "reproducible": False, "entries": entries}},
                        {"image": ("archive", lambda: archive)})


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
    plan = {"format": store_manifest.PLAN_FORMAT, "repositories": {"server-repo": {
        "visibility": "private", "assets": [],
        "images": {"server-image": {"dockerfile": "docker/Dockerfile", "runtime": ["libstdc++.so.6"],
                                    "executables": [{"package": "server_pkg", "entry": "bin/server",
                                                     "source": "server-repo"}]}}}}}
    return store, plan, steps, repository, commit, str(cxx)


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

    for failure in failures:
        print(f"store_image selftest FAILED: {failure}")
    print(f"store_image selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
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
