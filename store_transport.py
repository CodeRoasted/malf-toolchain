#!/usr/bin/env python3
"""A repository's own store on ghcr: its candidate pushes every object and record, and anyone restores them by digest.

DN-142.D31, slice S4 (the transport). One package per source repository, `ghcr.io/<owner>/store-<repo>`,
the owner and the repository always DERIVED from the run's own repository (`github.repository`), never
named here, so a fork runs the same computation over its own store (P7). Ghcr is the store and a
runner's disk is only a cache (the Founder's item 6): PUSH writes every object and record of the run's
store to the package whatever the runner; a READ pulls by digest into a content-addressed cache, judging
every byte by its SHA-256, so a stale or foreign cache entry is a miss; the no-op test reads ghcr only.

The tags (DN-142.D31 (3)): `sha256-<hex>` one object (the manifest step 0's replica writes, byte for
byte, so the two writers share a tag); `record-<key>` one record, its file's bytes as the one layer;
`candidate-<candidate key>` the index, pushed LAST and only after the run attested it;
`mismatch-<key>-<first 54 hex of the record's manifest digest>` a second record at an existing key whose
compared outputs differ (64 hex would make a 138-character tag, and OCI caps a tag at 128).

THE CHAIN OF TRUST A READER WALKS: the index's manifest is verified by its GitHub artifact attestation
(repository, signer workflow and its digest, `refs/heads/main`), then its content against the
certificate (its commit, its tree, malf's commit and tree); the index pins every record by MANIFEST
DIGEST and a record names its objects by content digest, so nothing past the index is read by tag.
A record whose tag already holds other bytes with EQUAL compared outputs (another seat's provenance) is
pushed by digest, untagged, and the index pins the bytes this run produced and attested. A private
repository (no attestation without Enterprise, item 6) is read with `--private`: its trust is the
package's privacy and every read judged by digest.

    store_transport.py candidate-key <owner/repo> <checkout> [<upstream.json>]
        prints the candidate key: SHA-256 of canonical {repository, tree, upstream} (DN-142.D31 (2)),
        the tree `git rev-parse HEAD^{tree}` of <checkout>, upstream a JSON {owner/repo: key} ({} if none)
    store_transport.py push <owner/repo> <checkout> <store dir> <out dir> [--upstream F] [--extra F]
        every OWNED record of <store dir> (this repository's packages, third-party, its tracked steps)
        and the objects it names, then the index's blob; writes <out dir>/index.json and
        <out dir>/subjects.sha256 (`<manifest hex>  <name>`, one line per object, record and the index:
        the attest step's `subject-checksums`). Env: GITHUB_TOKEN (packages: write), required.
    store_transport.py publish-index <owner/repo> <out dir>
        the index manifest under candidate-<key>, LAST; never rewritten. Env: GITHUB_TOKEN.
    store_transport.py candidate-present <owner/repo> <key> (--signer-workflow W [--signer-digest D] | --private)
        ghcr only, never the cache: exit 0 PRESENT (attested, its content checked), 3 ABSENT
    store_transport.py restore <owner/repo> <key> <cache dir> <store dir> (--signer-workflow W [--signer-digest D] | --private)
        the index and its whole closure into <store dir>, through <cache dir>: exit 0, 3 ABSENT
    store_transport.py pull-record <owner/repo> <record key> <cache dir> <store dir> (--signer-workflow W [--signer-digest D] | --private)
        one record by its tag, attested, and its objects: exit 0, 3 ABSENT
    store_transport.py verify <owner/repo> <tag> --signer-workflow W [--signer-digest D]
        the attestation of the manifest at <tag>: exit 0 VERIFIED (prints the source commit and the
        signer digest), 3 ABSENT
    store_transport.py package-check <owner/repo>
        the push's package guard, read before anything is built and writing nothing: exit 0 when the
        package has the repository's visibility and link, or when the token sees no package (the first
        push creates and guards it); 1 refused with the setting to change. Env: GITHUB_TOKEN, and
        MALF_PACKAGE_PEEK_TOKEN for a private package (below).
    store_transport.py owns-store <checkout> <commit>
        exit 0 when the repository at <commit> carries `.github/workflows/candidate.yml` (it owns its
        store, DN-142.D31 (3)), 3 when it does not
    store_transport.py selftest
        every verb against an in-process registry, a fixture API and recorded-shape attestations; no network

The package guard (push, package-check) reads the package's metadata with MALF_PACKAGE_PEEK_TOKEN when it
is set, else GITHUB_TOKEN: a GITHUB_TOKEN's answer for a PRIVATE package carries no `repository` field, so
a private caller passes a classic token holding read:packages alone, used for that read and nothing else.
W is `<owner>/<malf repository>/.github/workflows/coderoast-candidate.yml`; D the malf commit. Reads use
GITHUB_TOKEN when it is set and go anonymous otherwise (a public package). MALF_STORE_REGISTRY and
MALF_STORE_API replace https://ghcr.io and https://api.github.com (a local registry's live proof).
Exit 0 done, 1 refused (the reason on stderr), 2 a usage error, 3 absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_replica  # noqa: E402 — a sibling module, located from this file
from store_replica import Refused, Registry  # noqa: E402

REGISTRY_ENV = "MALF_STORE_REGISTRY"
API_ENV = "MALF_STORE_API"
OWNER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})$")
NAME = re.compile(r"^[a-z0-9_.-]{1,100}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECORD_TYPE = "application/vnd.coderoast.store.record.v1"
RECORD_MEDIA = "application/vnd.coderoast.store.record.v1+json"
INDEX_TYPE = "application/vnd.coderoast.store.candidate.v1"
INDEX_MEDIA = "application/vnd.coderoast.store.candidate.v1+json"
INDEX_SCHEMA = "coderoast.store.candidate/1"
CANDIDATE_WORKFLOW = ".github/workflows/candidate.yml"
SIGNER_PATH = ".github/workflows/coderoast-candidate.yml"
SOURCE_REF = "refs/heads/main"
PEEK_ENV = "MALF_PACKAGE_PEEK_TOKEN"
MISMATCH_HEX = 54
ABSENT = 3
THIRD_PARTY_KINDS = ("third-party-create",)
# The output fields a record names objects by (artefact_store's commit: a package's transport, a
# verdict's log, the objects convention's map).
OBJECT_FIELDS = (artefact_store.TRANSPORT_FIELD, "log")
# Fields two records at one key may differ in and still be one result: what LocalStore.commit never
# compares (the stored bytes' indexes and the objects a step's run produced around its verdict).
UNCOMPARED = {"entries", "build_ids", artefact_store.TRANSPORT_FIELD, "log"}
ENVIRONMENT_PIN = Path(__file__).resolve().parent / "config" / "build-environment.json"


class Absent(Exception):
    """What a reader asked for is not there, or not trusted: the step executes."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def full_name(text: str) -> tuple[str, str]:
    """(owner, repository), lower case, from `owner/repository` as `github.repository` spells it."""
    parts = text.lower().split("/")
    if len(parts) != 2 or not OWNER.match(parts[0]) or not NAME.match(parts[1]) or parts[1] in (".", ".."):
        raise Refused(f"{text!r} is not an owner/repository name")
    return parts[0], parts[1]


def package_name(repository: str) -> str:
    return f"store-{repository}"


def object_tag(hex_digest: str) -> str:
    return f"sha256-{hex_digest}"


def record_tag(key: str) -> str:
    return f"record-{key}"


def candidate_tag(key: str) -> str:
    return f"candidate-{key}"


def mismatch_tag(key: str, manifest_hex: str) -> str:
    return f"mismatch-{key}-{manifest_hex[:MISMATCH_HEX]}"


def one_layer_manifest(artifact: str, media: str, data: bytes) -> bytes:
    """An artifact whose one layer is `data`: canonical bytes, so equal data gives one manifest digest."""
    return canonical({
        "schemaVersion": 2, "mediaType": store_replica.OCI_MANIFEST, "artifactType": artifact,
        "config": {"mediaType": store_replica.OCI_EMPTY, "digest": store_replica.EMPTY_DIGEST,
                   "size": len(store_replica.EMPTY_BYTES)},
        "layers": [{"mediaType": media, "digest": f"sha256:{sha256(data)}", "size": len(data)}]})


# ── the candidate key (DN-142.D31 (2)) ─────────────────────────────────────────────────────────────


def candidate_document(repository: str, tree: str, upstream: dict[str, str]) -> dict:
    owner, name = full_name(repository)
    if not (HEX40.match(tree) or HEX64.match(tree)):
        raise Refused(f"{tree!r} is not a git tree id")
    members = {}
    for upstream_repository, key in upstream.items():
        upstream_owner, upstream_name = full_name(upstream_repository)
        if not isinstance(key, str) or not HEX64.match(key):
            raise Refused(f"upstream {upstream_repository}: {key!r} is not a candidate key (64 hex)")
        members[f"{upstream_owner}/{upstream_name}"] = key
    return {"repository": f"{owner}/{name}", "tree": tree, "upstream": members}


def candidate_key(repository: str, tree: str, upstream: dict[str, str]) -> str:
    return sha256(canonical(candidate_document(repository, tree, upstream)))


def _git(checkout: Path, *argv: str) -> str:
    done = subprocess.run(["git", "-C", str(checkout), *argv], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Refused(f"`git {' '.join(argv)}` in {checkout} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def checkout_identity(checkout: Path) -> tuple[str, str]:
    """(commit, tree) of the checkout's HEAD."""
    return _git(checkout, "rev-parse", "HEAD"), _git(checkout, "rev-parse", "HEAD^{tree}")


def owns_store(checkout: Path, commit: str) -> bool:
    """The repository owns its store at `commit` when its tree carries its own candidate workflow, the
    one writer of `store-<repo>` (DN-142.D31 (3), (4)); without it, its package is step 0's replica."""
    done = subprocess.run(["git", "-C", str(checkout), "cat-file", "-e", f"{commit}:{CANDIDATE_WORKFLOW}"],
                          capture_output=True, check=False)
    return done.returncode == 0


def read_upstream(path: str | None) -> dict[str, str]:
    if path is None:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Refused(f"{path} is not a JSON object {{owner/repo: candidate key}}")
    return value


# ── a store's records: ownership, objects, and one result ──────────────────────────────────────────


def owned_packages(checkout: Path, commit: str) -> set[str]:
    import yaml

    declared = yaml.safe_load(_git(checkout, "show", f"{commit}:packages.yml")) or {}
    return set((declared.get("packages") or {}).keys())


def record_owned(record: dict, repository: str, packages: set[str]) -> bool:
    """A record this repository's store holds: a step of its own packages, a third-party step it built
    (DN-142.D31 (2): a candidate restores third-party only from its own store), or its own tracked or
    ingested step. An upstream's record lives in that upstream's store and is never pushed here."""
    step = record["inputs"]["step"]
    if step.get("kind") in THIRD_PARTY_KINDS:
        return True
    if "repository" in step:
        return step["repository"] == repository
    return (step.get("package") or step.get("subject")) in packages


def record_objects(record: dict) -> list[str]:
    """Every object digest a record's outputs name."""
    named = set()
    for output in record["outputs"].values():
        for name in OBJECT_FIELDS:
            if isinstance(output.get(name), str):
                named.add(output[name])
        held = output.get(artefact_store.OBJECTS_FIELD)
        if isinstance(held, dict):
            named.update(value for value in held.values() if isinstance(value, str))
    bad = sorted(digest for digest in named if not HEX64.match(digest))
    if bad:
        raise Refused(f"record {record['key']} names objects that are not 64-hex digests: {bad}")
    return sorted(named)


def result_differences(stored: dict, ours: dict) -> list[str]:
    """The compared axes on which two records at one key differ (LocalStore.commit's comparison)."""
    differ = []
    for name in sorted(stored["outputs"].keys() | ours["outputs"].keys()):
        before, after = stored["outputs"].get(name), ours["outputs"].get(name)
        if before is None or after is None:
            differ.append(f"{name}: absent from one record")
            continue
        differ += [f"{name}.{axis}" for axis in sorted((before.keys() | after.keys()) - UNCOMPARED)
                   if before.get(axis) != after.get(axis)]
    return differ


def judged_record(data: bytes, key: str) -> dict:
    """The record in `data`, once its own key and the key of its inputs are `key`."""
    try:
        body = json.loads(data)
        derived = artefact_store.key_of(body["inputs"])
    except (ValueError, KeyError, TypeError, artefact_store.KeyError_) as unread:
        raise Refused(f"record {key}: not a record ({unread})") from unread
    if body.get("key") != key or derived != key:
        raise Refused(f"record {key}: it names key {body.get('key')} and its inputs hash to {derived}")
    return body


# ── the content-addressed cache and the registry reads ─────────────────────────────────────────────


@dataclass
class Reader:
    """Reads by digest, through a runner-local cache: a hit is re-judged by its digest, so a stale or
    foreign entry is a miss, and a fetched byte is judged before the cache keeps it."""

    registry: Registry
    cache: Path
    say: Callable[[str], None]
    fetched: list[str] = field(default_factory=list)

    def _cached(self, hex_digest: str) -> bytes | None:
        path = self.cache / "blobs" / "sha256" / hex_digest
        if path.is_file():
            data = path.read_bytes()
            if sha256(data) == hex_digest:
                return data
            self.say(f"store_transport: CACHE-MISS {hex_digest}: the cached bytes hash to {sha256(data)}")
        return None

    def _keep(self, hex_digest: str, data: bytes) -> None:
        folder = self.cache / "blobs" / "sha256"
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            handle.write(data)
        os.replace(handle.name, folder / hex_digest)

    def blob(self, hex_digest: str) -> bytes:
        data = self._cached(hex_digest)
        if data is None:
            data = self.registry.blob(hex_digest)
            self._keep(hex_digest, data)
            self.fetched.append(hex_digest)
        return data

    def manifest(self, hex_digest: str) -> bytes:
        data = self._cached(hex_digest)
        if data is None:
            answer = self.registry.call("GET", f"manifests/sha256:{hex_digest}", "pull",
                                        {"Accept": store_replica.OCI_MANIFEST})
            if answer.status == 404:
                raise Absent(f"{self.registry.name} holds no manifest sha256:{hex_digest}")
            if answer.status != 200:
                raise Refused(f"GET manifest {self.registry.name}@sha256:{hex_digest}: HTTP {answer.status}")
            data = answer.body
            if sha256(data) != hex_digest:
                raise Refused(f"the manifest served for sha256:{hex_digest} hashes to sha256:{sha256(data)}")
            self._keep(hex_digest, data)
            self.fetched.append(hex_digest)
        return data

    def tagged(self, tag: str) -> str:
        """The manifest digest at a tag, read from the registry, never the cache (a tag can move)."""
        held = self.registry.manifest_digest(tag)
        if held is None:
            raise Absent(f"{self.registry.name}:{tag} does not exist")
        if not held.startswith("sha256:") or not HEX64.match(held[7:]):
            raise Refused(f"{self.registry.name}:{tag} answered digest {held!r}")
        return held[7:]

    def layer(self, manifest_hex: str, media: str) -> bytes:
        body = json.loads(self.manifest(manifest_hex))
        layers = body.get("layers", [])
        if len(layers) != 1 or layers[0].get("mediaType") != media:
            raise Refused(f"manifest sha256:{manifest_hex} holds layers "
                          f"{[layer.get('mediaType') for layer in layers]}, not one {media}")
        digest = str(layers[0].get("digest", ""))
        if not digest.startswith("sha256:") or not HEX64.match(digest[7:]):
            raise Refused(f"manifest sha256:{manifest_hex} names layer {digest!r}")
        return self.blob(digest[7:])


# ── attestations (DN-142.D31 (4)) ──────────────────────────────────────────────────────────────────

# `gh` as the verifier runs it: argv after `gh` -> (exit code, stdout, stderr).
Gh = Callable[[list[str]], "tuple[int, str, str]"]
# A commit's tree: (owner/repo, commit) -> tree id.
TreeOf = Callable[[str, str], str]


@dataclass(frozen=True)
class Attested:
    source_commit: str
    signer_digest: str


@dataclass
class Verifier:
    """What a reader requires of an attestation: the store's own repository, malf's reusable
    candidate workflow (and its commit, when the reader pins one), from `refs/heads/main`."""

    repository: str
    signer_workflow: str
    signer_digest: str | None
    gh: Gh
    tree_of: TreeOf

    def signer_repository(self) -> str:
        if not self.signer_workflow.lower().endswith("/" + SIGNER_PATH):
            raise Refused(f"{self.signer_workflow!r} is not <owner>/<malf repository>/{SIGNER_PATH}")
        return self.signer_workflow[:-len(SIGNER_PATH) - 1].lower()

    def argv(self, path: Path) -> list[str]:
        return ["attestation", "verify", str(path), "--repo", self.repository,
                "--signer-workflow", self.signer_workflow, "--source-ref", SOURCE_REF, "--format", "json",
                *(["--signer-digest", self.signer_digest] if self.signer_digest else [])]

    def attested(self, manifest: bytes) -> list[Attested]:
        """Every attestation of these manifest bytes that names this repository, this signer
        workflow (at the pinned commit, when one is pinned) and main; raises Absent when none does."""
        self.signer_repository()
        with tempfile.TemporaryDirectory(prefix="store_transport.") as scratch:
            path = Path(scratch) / "manifest.json"
            path.write_bytes(manifest)
            code, out, err = self.gh(self.argv(path))
        if code != 0:
            raise Absent(f"gh attestation verify refused sha256:{sha256(manifest)}: "
                         f"{(err or out).strip()[-400:]}")
        try:
            results = json.loads(out)
        except ValueError as unread:
            raise Absent(f"gh attestation verify printed no JSON ({unread})") from unread
        source_uri = f"https://github.com/{self.repository}".lower()
        signer_uri = f"https://github.com/{self.signer_workflow}@".lower()
        found = []
        for result in results if isinstance(results, list) else []:
            verified = result.get("verificationResult") or {}
            certificate = (verified.get("signature") or {}).get("certificate") or {}
            subjects = {(subject.get("digest") or {}).get("sha256")
                        for subject in (verified.get("statement") or {}).get("subject") or []}
            signer = str(certificate.get("buildSignerDigest", ""))
            if (str(certificate.get("sourceRepositoryURI", "")).lower() == source_uri
                    and certificate.get("sourceRepositoryRef") == SOURCE_REF
                    and str(certificate.get("buildSignerURI", "")).lower().startswith(signer_uri)
                    and (self.signer_digest is None or signer == self.signer_digest)
                    and sha256(manifest) in subjects
                    and HEX40.match(str(certificate.get("sourceRepositoryDigest", ""))) and HEX40.match(signer)):
                found.append(Attested(certificate["sourceRepositoryDigest"], signer))
        if not found:
            raise Absent(f"no attestation of sha256:{sha256(manifest)} names {self.repository} at {SOURCE_REF} "
                         f"signed by {self.signer_workflow}"
                         + (f" at {self.signer_digest}" if self.signer_digest else ""))
        return found


def index_defects(index: dict, repository: str, key: str) -> list[str]:
    """What an index read at candidate-<key> of `repository` gets wrong about itself."""
    defects = []
    if index.get("schema") != INDEX_SCHEMA:
        defects.append(f"schema {index.get('schema')!r}, not {INDEX_SCHEMA!r}")
    if index.get("repository") != repository:
        defects.append(f"it names repository {index.get('repository')!r}, read from {repository}")
    if index.get("candidate") != key:
        defects.append(f"it names candidate {index.get('candidate')!r}, read at {key}")
    try:
        derived = candidate_key(repository, str(index.get("tree")), index.get("upstream") or {})
        if derived != key:
            defects.append(f"its repository, tree and upstream hash to {derived}, not {key}")
    except Refused as refused:
        defects.append(str(refused))
    for entry in index.get("records") or []:
        if not HEX64.match(str(entry.get("key"))) or not HEX64.match(str(entry.get("manifest"))):
            defects.append(f"a record entry {entry!r} is not a key and a manifest digest")
    return defects


def trusted_index(reader: Reader, repository: str, key: str, verifier: Verifier | None) -> dict:
    """The index at candidate-<key>, read from ghcr, attested and checked against its certificate."""
    manifest_hex = reader.tagged(candidate_tag(key))
    manifest = reader.manifest(manifest_hex)
    attested = verifier.attested(manifest) if verifier else []
    try:
        index = json.loads(reader.layer(manifest_hex, INDEX_MEDIA))
    except ValueError as unread:
        raise Absent(f"candidate-{key}: the index is not JSON ({unread})") from unread
    defects = index_defects(index, repository, key)
    if verifier and not defects:
        malf = index.get("malf") or {}
        checked = []
        for fact in attested:
            facts = []
            if index.get("commit") != fact.source_commit:
                facts.append(f"the index names commit {index.get('commit')}, the attestation {fact.source_commit}")
            elif verifier.tree_of(repository, fact.source_commit) != index.get("tree"):
                facts.append(f"commit {fact.source_commit}'s tree is not the index's {index.get('tree')}")
            if malf.get("commit") != fact.signer_digest:
                facts.append(f"the index names malf {malf.get('commit')}, the attestation {fact.signer_digest}")
            elif verifier.tree_of(verifier.signer_repository(), fact.signer_digest) != malf.get("tree"):
                facts.append(f"malf {fact.signer_digest}'s tree is not the index's {malf.get('tree')}")
            if not facts:
                break
            checked.append("; ".join(facts))
        else:
            defects += checked
    if defects:
        raise Absent(f"candidate-{key} of {repository}: " + "; ".join(defects))
    return index


def materialize(reader: Reader, store: Path, key: str, manifest_hex: str, say: Callable[[str], None]) -> dict:
    """One record pinned by its manifest digest, and every object it names, into `store`."""
    data = reader.layer(manifest_hex, RECORD_MEDIA)
    record = judged_record(data, key)
    for hex_digest in record_objects(record):
        target = store / "objects" / hex_digest
        if target.is_file() and artefact_store.file_sha256(target) == hex_digest:
            continue
        data_object = reader.blob(hex_digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(data_object)
        os.replace(handle.name, target)
    target = store / "records" / f"{key}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.read_bytes() != data:
        say(f"store_transport: LOCAL {key}: the store already holds another record at this key, kept")
    elif not target.is_file():
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(data)
        os.replace(handle.name, target)
    return record


def restore(reader: Reader, store: Path, repository: str, key: str, verifier: Verifier | None,
            say: Callable[[str], None]) -> dict:
    index = trusted_index(reader, repository, key, verifier)
    for entry in index["records"]:
        materialize(reader, store, entry["key"], entry["manifest"], say)
    say(f"store_transport: RESTORED candidate-{key} of {repository}: {len(index['records'])} record(s), "
        f"{len(reader.fetched)} blob(s) fetched, the rest from the cache")
    return index


def pull_record(reader: Reader, store: Path, key: str, verifier: Verifier | None,
                say: Callable[[str], None]) -> dict:
    manifest_hex = reader.tagged(record_tag(key))
    if verifier:
        attested = verifier.attested(reader.manifest(manifest_hex))
        record = judged_record(reader.layer(manifest_hex, RECORD_MEDIA), key)
        malf = ((record["inputs"].get("definition") or {}).get("malf"))
        if malf is not None and not any(verifier.tree_of(verifier.signer_repository(), fact.signer_digest) == malf
                                        for fact in attested):
            raise Absent(f"record-{key}: its definition names malf tree {malf}, and no attesting malf "
                         f"commit has that tree")
    record = materialize(reader, store, key, manifest_hex, say)
    say(f"store_transport: PULLED record-{key}")
    return record


# ── the push (the candidate's writer act) ──────────────────────────────────────────────────────────


@dataclass
class Pushed:
    subjects: list[tuple[str, str]] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def _put(registry: Registry, reference: str, manifest: bytes, blobs: list[bytes]) -> str:
    for data in [store_replica.EMPTY_BYTES, *blobs]:
        registry.push_blob(sha256(data), data)
    written = registry.put_manifest(reference, manifest)
    if registry.manifest_digest(reference) != written:
        raise Refused(f"{registry.name}:{reference} did not read back as {written}")
    return written[7:]


def push_object(registry: Registry, store: Path, hex_digest: str, say: Callable[[str], None]) -> str:
    """An object by its digest, under the manifest step 0's replica writes; never rewritten."""
    source = store / "objects" / hex_digest
    if not source.is_file() or artefact_store.file_sha256(source) != hex_digest:
        raise Refused(f"the store does not hold object {hex_digest} whole, and a record names it")
    manifest = store_replica.object_manifest(hex_digest, source.stat().st_size)
    manifest_hex = sha256(manifest)
    held = registry.manifest_digest(object_tag(hex_digest))
    if held == f"sha256:{manifest_hex}":
        say(f"store_transport: PRESENT sha256-{hex_digest}")
        return manifest_hex
    if held is not None:
        raise Refused(f"{registry.name}:{object_tag(hex_digest)} holds {held}, not the object's manifest "
                      f"sha256:{manifest_hex}: an object's name is never rewritten")
    _put(registry, object_tag(hex_digest), manifest, [source.read_bytes()])
    say(f"store_transport: PUSHED sha256-{hex_digest}")
    return manifest_hex


def push_record(registry: Registry, store: Path, path: Path, pushed: Pushed, say: Callable[[str], None]) -> None:
    key = path.stem
    data = path.read_bytes()
    record = judged_record(data, key)
    manifest = one_layer_manifest(RECORD_TYPE, RECORD_MEDIA, data)
    manifest_hex = sha256(manifest)
    objects = [(object_tag(hex_digest), push_object(registry, store, hex_digest, say))
               for hex_digest in record_objects(record)]
    held = registry.manifest_digest(record_tag(key))
    if held is None:
        _put(registry, record_tag(key), manifest, [data])
        say(f"store_transport: PUSHED record-{key}")
    elif held == f"sha256:{manifest_hex}":
        say(f"store_transport: PRESENT record-{key}")
    else:
        reader = Reader(registry, store / ".transport-cache", say)
        stored = judged_record(reader.layer(held[7:], RECORD_MEDIA), key)
        differ = result_differences(stored, record)
        if differ:
            _put(registry, mismatch_tag(key, manifest_hex), manifest, [data])
            pushed.mismatches.append(f"record-{key} holds {held} and this run's result differs on "
                                     f"{', '.join(differ)}; this run's record is at {mismatch_tag(key, manifest_hex)}")
            return
        _put(registry, f"sha256:{manifest_hex}", manifest, [data])
        say(f"store_transport: EQUAL-RESULT record-{key}: the tag holds another seat's record with equal "
            f"outputs; this run's bytes pushed by digest sha256:{manifest_hex}")
    pushed.subjects += objects + [(record_tag(key), manifest_hex)]
    step = record["inputs"]["step"]
    pushed.entries.append({"key": key, "kind": step.get("kind"),
                           "package": step.get("package") or step.get("subject") or step.get("repository"),
                           "manifest": manifest_hex})


def push(registry: Registry, guard: Callable[[], None], store: Path, repository: str, checkout: Path,
         upstream: dict[str, str], malf_dir: Path, extra: dict, out: Path, say: Callable[[str], None]) -> int:
    """Every owned record of `store` and its objects, then the index's blob; the index and the subjects
    to attest written to `out`. 1 when a record at an existing key differs (nothing indexed)."""
    owner, name = full_name(repository)
    commit, tree = checkout_identity(checkout)
    key = candidate_key(repository, tree, upstream)
    malf_commit, malf_tree = _git(malf_dir, "rev-parse", "HEAD"), artefact_store.worktree_tree_id(malf_dir)
    if malf_tree != _git(malf_dir, "rev-parse", "HEAD^{tree}"):
        raise Refused(f"malf at {malf_dir} differs from its commit {malf_commit}: an index names the commit "
                      f"that ran, and this one did not")
    packages = owned_packages(checkout, commit)
    guard()
    pushed = Pushed()
    upstream_records = 0
    for path in sorted((store / "records").glob("*.json")):
        record = judged_record(path.read_bytes(), path.stem)
        if not record_owned(record, name, packages):
            upstream_records += 1
            continue
        push_record(registry, store, path, pushed, say)
    if pushed.mismatches:
        for line in pushed.mismatches:
            say(f"store_transport: MISMATCH {line}")
        return 1
    index = {"schema": INDEX_SCHEMA, "repository": f"{owner}/{name}", "commit": commit, "tree": tree,
             "candidate": key, "upstream": candidate_document(repository, tree, upstream)["upstream"],
             "malf": {"commit": malf_commit, "tree": malf_tree},
             "image": json.loads(ENVIRONMENT_PIN.read_text(encoding="utf-8"))["image"],
             "records": sorted(pushed.entries, key=lambda entry: entry["key"]), "extra": extra}
    index_bytes = canonical(index)
    registry.push_blob(sha256(index_bytes), index_bytes)
    manifest = one_layer_manifest(INDEX_TYPE, INDEX_MEDIA, index_bytes)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_bytes(index_bytes)
    subjects = sorted(set(pushed.subjects)) + [(candidate_tag(key), sha256(manifest))]
    (out / "subjects.sha256").write_text("".join(f"{digest}  {tag}\n" for tag, digest in subjects),
                                         encoding="utf-8")
    guard()
    say(f"store_transport: PUSHED candidate {key} of {owner}/{name}: {len(pushed.entries)} record(s), "
        f"{upstream_records} upstream record(s) left to their own store, {len(subjects)} subject(s) to attest "
        f"in {out / 'subjects.sha256'}; the index is published by publish-index AFTER the attestation")
    return 0


def publish_index(registry: Registry, out: Path, say: Callable[[str], None]) -> int:
    index_bytes = (out / "index.json").read_bytes()
    key = json.loads(index_bytes)["candidate"]
    manifest = one_layer_manifest(INDEX_TYPE, INDEX_MEDIA, index_bytes)
    held = registry.manifest_digest(candidate_tag(key))
    if held == f"sha256:{sha256(manifest)}":
        say(f"store_transport: PRESENT candidate-{key}")
        return 0
    if held is not None:
        raise Refused(f"candidate-{key} holds {held}, not this run's index sha256:{sha256(manifest)}: an "
                      f"index is never rewritten")
    _put(registry, candidate_tag(key), manifest, [index_bytes])
    say(f"store_transport: PUBLISHED candidate-{key} (manifest sha256:{sha256(manifest)})")
    return 0


# ── the live readers ───────────────────────────────────────────────────────────────────────────────


def api_get(api: str, path: str, token: str | None) -> tuple[int, dict]:
    request = urllib.request.Request(f"{api}/{path}")
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    answer = store_replica._send(request)
    try:
        return answer.status, json.loads(answer.body or b"{}")
    except ValueError:
        return answer.status, {}


def package_view(get: Callable[[str], "tuple[int, dict]"], owner: str) -> Callable[[str], dict | None]:
    """The GitHub view of an `owner`'s container package, None when the token sees no such package."""
    def view(package: str) -> dict | None:
        status, user = get(f"users/{owner}")
        if status != 200:
            raise Refused(f"the API answered HTTP {status} for the owner {owner}")
        scope = "orgs" if user.get("type") == "Organization" else "users"
        status, seen = get(f"{scope}/{owner}/packages/container/{package}")
        if status == 404:
            return None
        if status != 200:
            raise Refused(f"the package API answered HTTP {status} for {package}; its visibility cannot be "
                          f"verified, so nothing is written")
        return seen
    return view


def own_expected(get: Callable[[str], "tuple[int, dict]"], owner: str, name: str) -> store_replica.Expected:
    """What the repository's own store package must be (store_replica.expected_package, the rule Pharos's
    A1c reads), from the repository's visibility as the API answers it."""
    status, seen = get(f"repos/{owner}/{name}")
    if status != 200 or seen.get("visibility") not in store_replica.VISIBILITIES:
        raise Refused(f"the API answered HTTP {status} and visibility {seen.get('visibility')!r} for "
                      f"{owner}/{name}")
    return store_replica.expected_package(owner, name, seen["visibility"])


def package_guard(get: Callable[[str], "tuple[int, dict]"], peek: Callable[[str], "tuple[int, dict]"],
                  registry: Registry, repository: str, say: Callable[[str], None]) -> Callable[[], None]:
    """The writer's guard for an OWN store: the package has the repository's visibility and is linked to it.
    `peek` reads the package's metadata: a private package's link is absent from a GITHUB_TOKEN's answer
    (measured 2026-10-28, coderoast-security run 40687600161), so a private caller passes a read:packages token."""
    owner, name = full_name(repository)

    def guard() -> None:
        store_replica.ensure_guarded(registry, package_view(peek, owner), owner, package_name(name), say,
                                     own_expected(get, owner, name))
    return guard


def package_check(get: Callable[[str], "tuple[int, dict]"], peek: Callable[[str], "tuple[int, dict]"],
                  repository: str, say: Callable[[str], None]) -> None:
    """The writer's guard read before the candidate builds, writing nothing: a package that exists is judged
    now, and one the token cannot see is left to the push, which creates it and guards it."""
    owner, name = full_name(repository)
    package = package_name(name)
    expected = own_expected(get, owner, name)
    view = package_view(peek, owner)
    if view(package) is None:
        say(f"store_transport: UNSEEN {package} — absent, or private and not linked to {owner}/{name} so its "
            f"token cannot read it; the store job's first push creates it and guards it before any object")
        return
    store_replica.verify_package(view, owner, package, expected)
    say(f"store_transport: GUARDED {package} — {expected.visibility}, linked to {expected.linked}")


def live_gh(argv: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr


def live_tree_of(get: Callable[[str], "tuple[int, dict]"]) -> TreeOf:
    def tree_of(repository: str, commit: str) -> str:
        status, body = get(f"repos/{repository}/git/commits/{commit}")
        if status != 200:
            raise Absent(f"the API answered HTTP {status} for {repository}@{commit}")
        return str((body.get("tree") or {}).get("sha"))
    return tree_of


# ── the selftest: an in-process registry, a fixture API and recorded-shape attestations ────────────

FIXTURE_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "store_transport"


def _fixture_attestations(manifest_hex: str, **certificate: str) -> str:
    """The JSON `gh attestation verify --format json` prints, read from the recorded-shape fixture,
    its subject and certificate fields set for one arm."""
    results = json.loads((FIXTURE_DIR / "attestation-verify.json").read_text(encoding="utf-8"))
    for result in results:
        result["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = manifest_hex
        result["verificationResult"]["signature"]["certificate"].update(certificate)
    return json.dumps(results)


def _repository(root: Path, packages: list[str], candidate: bool) -> Path:
    checkout = root / "checkout"
    checkout.mkdir()
    (checkout / "packages.yml").write_text(
        "packages:\n" + "".join(f"  {name}:\n    path: {name}\n" for name in packages), encoding="utf-8")
    if candidate:
        (checkout / ".github" / "workflows").mkdir(parents=True)
        (checkout / CANDIDATE_WORKFLOW).write_text("on: workflow_dispatch\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    for argv in (["init", "--quiet", "-b", "main"], ["add", "--all"], ["commit", "--quiet", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(checkout), *argv], check=True, env=env, capture_output=True)
    return checkout


def _put_record(store: Path, inputs: dict, outputs: dict) -> str:
    key = artefact_store.key_of(inputs)
    (store / "records").mkdir(parents=True, exist_ok=True)
    (store / "records" / f"{key}.json").write_text(
        json.dumps({"key": key, "inputs": inputs, "outputs": outputs, "produced_by": {"seat": "a"}},
                   sort_keys=True, indent=1), encoding="utf-8")
    return key


def _put_object(store: Path, data: bytes) -> str:
    (store / "objects").mkdir(parents=True, exist_ok=True)
    (store / "objects" / sha256(data)).write_bytes(data)
    return sha256(data)


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, run: Callable[[], object], refusal: str | None, kind: type = Refused) -> object:
        armed[0] += 1
        try:
            value = run()
        except (Refused, Absent) as refused:
            if refusal is None or refusal not in str(refused) or not isinstance(refused, kind):
                failures.append(f"{label}: {type(refused).__name__} {refused!r}, want "
                                f"{'success' if refusal is None else kind.__name__ + ' ' + repr(refusal)}")
            return None
        if refusal is not None:
            failures.append(f"{label}: succeeded, want {kind.__name__} containing {refusal!r}")
        return value

    def expect(label: str, got: object, want: object) -> None:
        armed[0] += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    servers = []
    quiet: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="store_transport.") as scratch:
            root = Path(scratch)
            (root / "malf").mkdir()
            malf_dir = _repository(root / "malf", ["malf"], candidate=False)
            checkout = _repository(root, ["ipc_core", "ipc_consumer"], candidate=True)
            commit, tree = checkout_identity(checkout)
            repository = "CodeRoasted/coderoast-ipc"

            # -- the candidate key: one value from a checkout, case-folded, upstream-sensitive.
            key = candidate_key(repository, tree, {})
            expect("the candidate key is SHA-256 over canonical {repository, tree, upstream}", key,
                   sha256(canonical({"repository": "coderoasted/coderoast-ipc", "tree": tree, "upstream": {}})))
            expect("the owner's case does not change the key (github.repository against a ghcr path)",
                   candidate_key("coderoasted/CODEROAST-IPC", tree, {}), key)
            expect("an upstream candidate key changes the key",
                   candidate_key(repository, tree, {"CodeRoasted/coderoast-ipc": "a" * 64}) != key, True)
            arm("RED: an upstream member that is not a candidate key is refused",
                lambda: candidate_key(repository, tree, {"o/r": "short"}), "not a candidate key")
            arm("RED: a repository that is not owner/name is refused",
                lambda: candidate_key("coderoast-ipc", tree, {}), "not an owner/repository name")
            expect("a repository whose commit carries candidate.yml owns its store", owns_store(checkout, commit), True)
            bare = _repository(root / "bare", ["x"], candidate=False) if (root / "bare").mkdir() is None else None
            expect("a repository whose commit carries no candidate.yml is step 0's replica",
                   owns_store(bare, checkout_identity(bare)[0]), False)

            # -- a run store: two own records with objects, a third-party record, an upstream record.
            store = root / "store"
            transport = _put_object(store, b"a ship-form transport\n" * 32)
            log = _put_object(store, b"a test log\n")
            own_package = _put_record(store, {"step": {"kind": "conan-create", "package": "ipc_core", "form": "ship"}},
                                      {"package": {"alias": "ipc_core/1#r:p#v", "content": "c" * 64,
                                                   "transport": transport, "entries": []}})
            own_verdict = _put_record(store, {"step": {"kind": "source-verdict", "package": "ipc_core"},
                                              "definition": {"malf": "m" * 40}},
                                      {"verdict": {"verdict": "pass", "results": [], "log": log}})
            third = _put_record(store, {"step": {"kind": "third-party-create", "package": "gtest"}},
                                {"package": {"alias": "gtest/1.17.0", "content": "d" * 64, "transport": transport}})
            upstream_key = _put_record(store, {"step": {"kind": "conan-create", "package": "canon_core"}},
                                       {"package": {"alias": "canon", "content": "e" * 64, "transport": transport}})

            def scene(**knobs):
                fake = store_replica.Fake(**knobs)
                server, base = store_replica._fake_server(fake)
                servers.append(server)
                return fake, Registry(base, "coderoasted/store-coderoast-ipc", ("x-access-token", "token"))

            api = {"users/coderoasted": {"type": "Organization"},
                   "repos/coderoasted/coderoast-ipc": {"visibility": "public"},
                   "orgs/coderoasted/packages/container/store-coderoast-ipc": {
                       "visibility": "public", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}}}

            def get_from(answers: dict) -> Callable[[str], "tuple[int, dict]"]:
                return lambda path: (200, answers[path]) if path in answers else (404, {})

            fake, registry = scene()
            out = root / "out"
            guard = package_guard(get_from(api), get_from(api), registry, repository, quiet.append)
            code = arm("a candidate pushes every owned record and its objects, then the index blob",
                       lambda: push(registry, guard, store, repository, checkout, {}, malf_dir, {"preflight": {}},
                                    out, quiet.append), None)
            expect("push exits 0", code, 0)
            index = json.loads((out / "index.json").read_bytes())
            expect("the index names exactly the owned records, never the upstream's",
                   sorted(entry["key"] for entry in index["records"]), sorted([own_package, own_verdict, third]))
            expect("an upstream record is not in the package", record_tag(upstream_key) in fake.tags, False)
            expect("the objects and records are tagged by D31 (3)'s names",
                   {object_tag(transport), object_tag(log), record_tag(own_package), record_tag(third)} <= set(fake.tags),
                   True)
            expect("the index is NOT tagged before publish-index (the attestation comes first)",
                   candidate_tag(key) in fake.tags, False)
            subjects = (out / "subjects.sha256").read_text(encoding="utf-8").splitlines()
            index_manifest = sha256(one_layer_manifest(INDEX_TYPE, INDEX_MEDIA, (out / "index.json").read_bytes()))
            expect("the subjects are every object, every record and the index, by manifest digest",
                   (len(subjects), subjects[-1]), (2 + 3 + 1, f"{index_manifest}  {candidate_tag(key)}"))
            expect("an object's manifest is the one step 0's replica writes (one tag, two writers)",
                   fake.tags[object_tag(transport)],
                   "sha256:" + sha256(store_replica.object_manifest(transport, len(b"a ship-form transport\n" * 32))))
            expect("the index names the commit, tree, key, malf and the image",
                   (index["commit"], index["tree"], index["candidate"], index["malf"]["commit"], index["image"][:7]),
                   (commit, tree, key, _git(malf_dir, "rev-parse", "HEAD"), "sha256:"))

            (malf_dir / "packages.yml").write_text("dirty\n", encoding="utf-8")
            arm("RED: a malf checkout that differs from its commit is refused: an index names the commit that ran",
                lambda: push(registry, guard, store, repository, checkout, {}, malf_dir, {}, root / "dirty",
                             quiet.append), "differs from its commit")
            subprocess.run(["git", "-C", str(malf_dir), "checkout", "--quiet", "--", "packages.yml"], check=True)
            pushes = len(fake.manifests)
            arm("a second push of the same store writes nothing new (PRESENT)",
                lambda: push(registry, guard, store, repository, checkout, {}, malf_dir, {"preflight": {}},
                             root / "out2", quiet.append), None)
            expect("the re-push wrote no manifest", len(fake.manifests), pushes)
            expect("publish-index tags the index", publish_index(registry, out, quiet.append), 0)
            expect("the index tag holds the attested manifest", fake.tags.get(candidate_tag(key)),
                   f"sha256:{index_manifest}")
            expect("a second publish-index is PRESENT", publish_index(registry, out, quiet.append), 0)

            # -- the verifier, over recorded-shape `gh attestation verify --format json` output.
            malf_commit = _git(malf_dir, "rev-parse", "HEAD")
            malf_tree = artefact_store.worktree_tree_id(malf_dir)
            signer = "CodeRoasted/malf-toolchain/" + SIGNER_PATH
            good_certificate = {
                "sourceRepositoryURI": "https://github.com/CodeRoasted/coderoast-ipc",
                "sourceRepositoryDigest": commit, "sourceRepositoryRef": SOURCE_REF,
                "buildSignerURI": f"https://github.com/{signer}@refs/heads/main", "buildSignerDigest": malf_commit}
            trees = {("coderoasted/coderoast-ipc", commit): tree, ("coderoasted/malf-toolchain", malf_commit): malf_tree}
            asked: list[list[str]] = []

            def verifier(certificate: dict | None = None, code: int = 0, digest: str | None = None,
                         subject: str | None = None) -> Verifier:
                def gh(argv: list[str]) -> tuple[int, str, str]:
                    asked.append(argv)
                    manifest_hex = subject or sha256(Path(argv[2]).read_bytes())
                    return code, _fixture_attestations(manifest_hex, **{**good_certificate, **(certificate or {})}), \
                        "" if code == 0 else "no matching attestations found"
                return Verifier("coderoasted/coderoast-ipc", signer, digest, gh,
                                lambda repo, sha: trees.get((repo.lower(), sha), "0" * 40))

            reader = Reader(registry, root / "cache", quiet.append)
            arm("candidate-present: an attested index whose content agrees with its certificate is PRESENT",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(digest=malf_commit)), None)
            expect("the verifier asks gh for the repository, the signer workflow, main and the malf commit",
                   asked[-1][3:], ["--repo", "coderoasted/coderoast-ipc", "--signer-workflow", signer, "--source-ref",
                                   SOURCE_REF, "--format", "json", "--signer-digest", malf_commit])
            for label, knobs, said in (
                    ("gh refusing the verification (no attestation, or a non-main run's)", {"code": 1},
                     "gh attestation verify refused"),
                    ("an attestation from another branch", {"certificate": {"sourceRepositoryRef": "refs/heads/x"}},
                     "no attestation"),
                    ("an attestation signed by another workflow",
                     {"certificate": {"buildSignerURI": "https://github.com/x/y/.github/workflows/z.yml@refs/heads/main"}},
                     "no attestation"),
                    ("an attestation of another repository (another repository's token)",
                     {"certificate": {"sourceRepositoryURI": "https://github.com/CodeRoasted/insight-canon"}},
                     "no attestation"),
                    ("an attestation at another malf commit than the pinned one",
                     {"certificate": {"buildSignerDigest": "f" * 40}, "digest": malf_commit}, "no attestation"),
                    ("an attestation of other bytes", {"subject": "0" * 64}, "no attestation"),
                    ("an index whose commit is not the attested commit",
                     {"certificate": {"sourceRepositoryDigest": "1" * 40}}, "the attestation 1111"),
                    ("an attested malf commit whose tree is not the index's",
                     {"certificate": {"buildSignerDigest": "2" * 40}}, "the attestation 2222")):
                arm(f"RED: candidate-present reads ABSENT on {label}",
                    lambda knobs=knobs: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(**knobs)),
                    said, Absent)
            arm("RED: a candidate key the package does not hold is ABSENT, never a red",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", "9" * 64, verifier()), "does not exist", Absent)

            # -- restore through the cache, every read judged by its digest.
            restored = root / "restored"
            reader = Reader(registry, root / "cache2", quiet.append)
            arm("restore materializes the index's records and their objects",
                lambda: restore(reader, restored, "coderoasted/coderoast-ipc", key, verifier(), quiet.append), None)
            expect("the restored records are byte-equal to the pushed ones",
                   sorted(path.read_bytes() == (store / "records" / path.name).read_bytes()
                          for path in (restored / "records").glob("*.json")), [True, True, True])
            expect("the restored objects are the named ones", sorted(path.name for path in (restored / "objects").iterdir()),
                   sorted([transport, log]))
            fetched_first = len(reader.fetched)
            reader = Reader(registry, root / "cache2", quiet.append)
            restore(reader, root / "restored2", "coderoasted/coderoast-ipc", key, verifier(), quiet.append)
            expect("a second restore on the same runner reads every blob from the cache, the tag from ghcr",
                   (fetched_first > 0, len(reader.fetched)), (True, 0))
            cached = root / "cache2" / "blobs" / "sha256" / transport
            cached.write_bytes(b"a foreign entry")
            reader = Reader(registry, root / "cache2", quiet.append)
            restore(reader, root / "restored3", "coderoasted/coderoast-ipc", key, verifier(), quiet.append)
            expect("a cache entry whose bytes are not its digest is a miss, re-fetched and judged",
                   (reader.fetched, (root / "restored3" / "objects" / transport).read_bytes() == (store / "objects" / transport).read_bytes()),
                   ([transport], True))
            arm("RED: a record whose inputs do not hash to the key it is read at is refused",
                lambda: judged_record(canonical({"key": own_package, "inputs": {"step": {"kind": "forged"}},
                                                 "outputs": {}}), own_package), "inputs hash to")
            fake.corrupt_blobs = True
            arm("RED: a blob served with other bytes than its digest is refused, never cached",
                lambda: Reader(registry, root / "cache3", quiet.append).blob(log), "hashes to")
            fake.corrupt_blobs = False
            arm("pull-record reads one record by its tag, attested",
                lambda: pull_record(Reader(registry, root / "cache4", quiet.append), root / "pulled", own_package,
                                    verifier(), quiet.append), None)
            arm("RED: pull-record of a record whose definition names a malf tree no attesting commit has is ABSENT",
                lambda: pull_record(Reader(registry, root / "cache4", quiet.append), root / "pulled", own_verdict,
                                    verifier(), quiet.append), "no attesting malf commit", Absent)

            # -- immutability and the mismatch tag.
            changed = json.loads((store / "records" / f"{own_package}.json").read_text())
            changed["produced_by"] = {"seat": "b"}
            (store / "records" / f"{own_package}.json").write_text(json.dumps(changed, sort_keys=True, indent=1))
            arm("a record at an existing key with EQUAL outputs from another seat is pushed by digest, the tag kept",
                lambda: push(registry, guard, store, repository, checkout, {}, malf_dir, {}, root / "out3", quiet.append),
                None)
            third_index = json.loads((root / "out3" / "index.json").read_bytes())
            expect("the index pins THIS run's record bytes, and the tag still holds the first writer's",
                   (next(e["manifest"] for e in third_index["records"] if e["key"] == own_package)
                    != fake.tags[record_tag(own_package)][7:],
                    f"sha256:{next(e['manifest'] for e in third_index['records'] if e['key'] == own_package)}"
                    in fake.manifests), (True, True))
            changed["outputs"]["package"]["content"] = "f" * 64
            (store / "records" / f"{own_package}.json").write_text(json.dumps(changed, sort_keys=True, indent=1))
            expect("RED: a record at an existing key whose compared outputs differ exits 1 with a mismatch tag, no index",
                   (push(registry, guard, store, repository, checkout, {}, malf_dir, {}, root / "out4", quiet.append),
                    any(tag.startswith(f"mismatch-{own_package}-") and len(tag) <= 128 for tag in fake.tags),
                    (root / "out4" / "index.json").exists()), (1, True, False))
            (root / "out5").mkdir()
            (root / "out5" / "index.json").write_bytes(canonical({**index, "extra": {"other": 1}}))
            arm("RED: publish-index over a candidate tag holding another index is refused, never rewritten",
                lambda: publish_index(registry, root / "out5", quiet.append), "never rewritten")

            # -- the guard: the one visibility rule, through the API the token reads.
            for label, answers, said in (
                    ("a public repository's store still private (before the Founder's flip)",
                     {"orgs/coderoasted/packages/container/store-coderoast-ipc":
                      {"visibility": "private", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}}},
                     "not 'public'"),
                    ("a public repository's store linked to no repository",
                     {"orgs/coderoasted/packages/container/store-coderoast-ipc": {"visibility": "public"}},
                     "linked to no repository"),
                    ("a repository whose visibility the token cannot read", {"repos/coderoasted/coderoast-ipc": None},
                     "HTTP 404")):
                answered = {**api, **answers}
                answered = {path: body for path, body in answered.items() if body is not None}
                _fake, other = scene()
                arm(f"RED: push refuses {label}, before any object reaches the package",
                    lambda answered=answered, other=other: push(
                        other, package_guard(get_from(answered), get_from(answered), other, repository, quiet.append), store, repository,
                        checkout, {}, malf_dir, {}, root / "out6", quiet.append), said)
                expect(f"nothing was pushed on {label}", [tag for tag in _fake.tags if tag != "store-sentinel"], [])
                arm(f"RED: package-check refuses {label}, before anything is built",
                    lambda answered=answered: package_check(get_from(answered), get_from(answered), repository, quiet.append), said)
            arm("package-check passes a public repository's store, public and linked to it",
                lambda: package_check(get_from(api), get_from(api), repository, quiet.append), None)
            unseen = {path: body for path, body in api.items() if "packages" not in path}
            quiet.clear()
            arm("package-check leaves a package the token cannot see to the push that creates and guards it",
                lambda: package_check(get_from(unseen), get_from(unseen), repository, quiet.append), None)
            expect("package-check says it judged nothing on an unseen package",
                   [line.split(" — ")[0] for line in quiet], ["store_transport: UNSEEN store-coderoast-ipc"])
            private = {**api, "repos/coderoasted/coderoast-ipc": {"visibility": "private"},
                       "orgs/coderoasted/packages/container/store-coderoast-ipc": {
                           "visibility": "private", "repository": {"full_name": "coderoasted/coderoast-ipc"}}}
            unlinked_view = {**private, "orgs/coderoasted/packages/container/store-coderoast-ipc": {"visibility": "private"}}
            arm("RED: package-check refuses a private package whose link the token's answer omits, naming the peek token",
                lambda: package_check(get_from(private), get_from(unlinked_view), repository, quiet.append), PEEK_ENV)
            arm("package-check reads a private package's link through the peek token, the repository through the run's",
                lambda: package_check(get_from(unlinked_view), get_from(private), repository, quiet.append), None)
            _fake, other = scene()
            (store / "records" / f"{own_package}.json").unlink()
            arm("a private repository's store, private and linked to it, takes the push (item 6)",
                lambda: push(other, package_guard(get_from(private), get_from(private), other, repository, quiet.append), store, repository,
                             checkout, {}, malf_dir, {}, root / "out7", quiet.append), None)
            user = {"users/someone": {"type": "User"}, "repos/someone/coderoast-ipc": {"visibility": "public"},
                    "users/someone/packages/container/store-coderoast-ipc": {
                        "visibility": "public", "repository": {"full_name": "someone/coderoast-ipc"}}}
            _fake, forked = scene()
            arm("a fork owned by a user writes its own store, its package read under /users (P7)",
                lambda: push(forked, package_guard(get_from(user), get_from(user), forked, "someone/coderoast-ipc", quiet.append),
                             store, "someone/coderoast-ipc", checkout, {}, malf_dir, {}, root / "out8", quiet.append), None)
            expect("the fork's index names the fork, and its key is the fork's",
                   json.loads((root / "out8" / "index.json").read_bytes())["candidate"],
                   candidate_key("someone/coderoast-ipc", tree, {}))
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as crashed:
        failures.append(f"the selftest stopped: {type(crashed).__name__} {crashed}")
    finally:
        for server in servers:
            server.shutdown()

    for failure in failures:
        print(f"store_transport selftest FAILED: {failure}")
    print(f"store_transport selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


# ── the command line ───────────────────────────────────────────────────────────────────────────────


def _registry(repository: str, token: str | None) -> Registry:
    owner, name = full_name(repository)
    base = os.environ.get(REGISTRY_ENV, store_replica.REGISTRY)
    return Registry(base, f"{owner}/{package_name(name)}",
                    (os.environ.get("GITHUB_ACTOR", "x-access-token"), token) if token else None)


def _verifier(args: argparse.Namespace, get: Callable[[str], "tuple[int, dict]"]) -> Verifier | None:
    owner, name = full_name(args.repository)
    if args.private:
        status, seen = get(f"repos/{owner}/{name}")
        if status != 200 or seen.get("visibility") != "private":
            raise Refused(f"--private reads {owner}/{name} unattested, and it is {seen.get('visibility')!r} "
                          f"(HTTP {status}): a public repository's store is read only through its attestations")
        return None
    if not args.signer_workflow:
        raise Refused("a public store is read through its attestations: name --signer-workflow")
    if args.signer_digest and not HEX40.match(args.signer_digest):
        raise Refused(f"--signer-digest {args.signer_digest!r} is not a commit (40 hex)")
    return Verifier(f"{owner}/{name}", args.signer_workflow, args.signer_digest, live_gh, live_tree_of(get))


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    parser = argparse.ArgumentParser(prog="store_transport.py", add_help=False)
    verbs = parser.add_subparsers(dest="verb", required=True)
    verb = verbs.add_parser("candidate-key")
    verb.add_argument("repository"), verb.add_argument("checkout"), verb.add_argument("upstream", nargs="?")
    verb = verbs.add_parser("push")
    for name in ("repository", "checkout", "store", "out"):
        verb.add_argument(name)
    verb.add_argument("--upstream"), verb.add_argument("--extra")
    verb = verbs.add_parser("publish-index")
    verb.add_argument("repository"), verb.add_argument("out")
    verb = verbs.add_parser("package-check")
    verb.add_argument("repository")
    verb = verbs.add_parser("owns-store")
    verb.add_argument("checkout"), verb.add_argument("commit")
    for name, positional in (("candidate-present", ("repository", "key")),
                             ("restore", ("repository", "key", "cache", "store")),
                             ("pull-record", ("repository", "key", "cache", "store")),
                             ("verify", ("repository", "tag"))):
        verb = verbs.add_parser(name)
        for argument in positional:
            verb.add_argument(argument)
        verb.add_argument("--signer-workflow"), verb.add_argument("--signer-digest")
        verb.add_argument("--private", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(__doc__, file=sys.stderr)
        return 2
    say = print
    token = os.environ.get("GITHUB_TOKEN") or None
    peek_token = os.environ.get(PEEK_ENV) or token
    api = os.environ.get(API_ENV, store_replica.API)

    def get(path: str) -> tuple[int, dict]:
        return api_get(api, path, token)

    def peek(path: str) -> tuple[int, dict]:
        return api_get(api, path, peek_token)

    try:
        if args.verb == "candidate-key":
            print(candidate_key(args.repository, checkout_identity(Path(args.checkout))[1], read_upstream(args.upstream)))
            return 0
        if args.verb == "owns-store":
            return 0 if owns_store(Path(args.checkout), args.commit) else ABSENT
        if args.verb == "package-check":
            package_check(get, peek, args.repository, say)
            return 0
        if args.verb in ("push", "publish-index") and not token:
            raise Refused("GITHUB_TOKEN is unset: a candidate writes its store with its own repository's token")
        registry = _registry(args.repository, token)
        if args.verb == "push":
            extra = json.loads(Path(args.extra).read_text(encoding="utf-8")) if args.extra else {}
            return push(registry, package_guard(get, peek, registry, args.repository, say), Path(args.store),
                        args.repository, Path(args.checkout), read_upstream(args.upstream),
                        Path(__file__).resolve().parent, extra, Path(args.out), say)
        if args.verb == "publish-index":
            return publish_index(registry, Path(args.out), say)
        owner, name = full_name(args.repository)
        if args.verb == "verify":
            if args.private:
                raise Refused("a private store carries no attestation to verify (item 6)")
            reader = Reader(registry, Path(tempfile.mkdtemp(prefix="store_transport.")), say)
            facts = _verifier(args, get).attested(reader.manifest(reader.tagged(args.tag)))
            for fact in facts:
                say(f"store_transport: VERIFIED {owner}/{name}:{args.tag} source {fact.source_commit} "
                    f"signer {fact.signer_digest}")
            return 0
        verifier = _verifier(args, get)
        if args.verb == "candidate-present":
            if not HEX64.match(args.key):
                raise Refused(f"{args.key!r} is not a candidate key (64 hex)")
            reader = Reader(registry, Path(tempfile.mkdtemp(prefix="store_transport.")), say)
            trusted_index(reader, f"{owner}/{name}", args.key, verifier)
            say(f"store_transport: PRESENT candidate-{args.key} of {owner}/{name}")
            return 0
        if not HEX64.match(args.key):
            raise Refused(f"{args.key!r} is not a key (64 hex)")
        reader = Reader(registry, Path(args.cache), say)
        if args.verb == "restore":
            restore(reader, Path(args.store), f"{owner}/{name}", args.key, verifier, say)
        else:
            pull_record(reader, Path(args.store), args.key, verifier, say)
        return 0
    except Absent as absent:
        print(f"store_transport: ABSENT — {absent}")
        return ABSENT
    except Refused as refused:
        print(f"store_transport: REFUSED — {refused}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
