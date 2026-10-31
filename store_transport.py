#!/usr/bin/env python3
"""A repository's own store on ghcr: its candidate pushes every object and record, and anyone restores them by digest.

DN-142.D31, slice S4 (the transport). One package per source repository, `ghcr.io/<owner>/store-<repo>`,
the owner and the repository always DERIVED from the run's own repository (`github.repository`), never
named here, so a fork runs the same computation over its own store (P7). Ghcr is the store and a
runner's disk is only a cache (the Founder's item 6): PUSH writes every object and record of the run's
store to the package whatever the runner; a READ pulls by digest into a content-addressed cache, judging
every byte by its SHA-256, so a stale or foreign cache entry is a miss; the no-op test reads ghcr only.

The tags (DN-142.D31 (3)): `sha256-<hex>` one object (store_replica's canonical object manifest);
`record-<key>` one record, its file's bytes as the one layer;
`candidate-<candidate key>` the index, pushed LAST and only after the run attested it;
`mismatch-<key>-<first 54 hex of the record's manifest digest>` a second record at an existing key whose
compared outputs differ (64 hex would make a 138-character tag, and OCI caps a tag at 128).

THE CHAIN OF TRUST A READER WALKS: the index's manifest is verified by its GitHub artifact attestation
(repository, signer workflow and its digest, `refs/heads/main` or a cut's `refs/tags/candidate/...` whose commit
main holds, judged by GitHub's compare), then its content against the
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
        and the objects it names, then the index's blob; a PUBLIC store's third-party records judged first
        under the public-seat licence rule, the whole push refused on a finding, and malf's recipe notice
        pushed as one object the index names (DN-142.D38); writes <out dir>/index.json and
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
    store_transport.py sources <sources.json> <out.json> <into>
        every SOURCE-ONLY upstream ({owner/repo: commit}, a repository whose recipes are required and which
        ships no package) cloned at its commit into <into>/<repo>; <out.json> holds {owner/repo: {commit,
        tree, root}}. The commit is the trust: git addresses the tree by it. candidate-key and push take the
        same document as --sources, a key member only when non-empty.
    store_transport.py upstream <upstream.json> <out.json> [--sources DIR] [--store DIR] [--signer-workflow W]
        every upstream candidate the document names, read through its own store's trust (a public one's
        attestation, W; a private one's privacy and GITHUB_TOKEN): <out.json> holds {owner/repo: {key,
        commit, tree[, root]}}; --store restores each one's records there, --sources clones each at the
        commit its index records into DIR/<repo>, its tree checked. Refused when an upstream's own upstream
        is not the document's (one candidate of each repository in a graph); exit 3 when one is absent.
    store_transport.py package-check <owner/repo>
        the push's package guard, read before anything is built and writing nothing: exit 0 when the
        package has the repository's visibility and link, or when the token sees no package (the first
        push creates and guards it); 1 refused with the setting to change. Env: GITHUB_TOKEN, and
        MALF_PACKAGE_PEEK_TOKEN for a private package (below).
    store_transport.py index <owner/repo> <key>
        the index at candidate-<key>, printed as canonical JSON on stdout, its content checked against its key
        and no attestation read (the keep-set's upstream closure reads its `upstream`, which the key hashes;
        a keep decision only spares versions): exit 0, 3 ABSENT. Env: GITHUB_TOKEN for a private store.
    store_transport.py prune <owner/repo> <keep-set> [--apply]
        DN-142.D32 (2): every version of store-<repo> the keep-set's closure does not name (each kept index, the
        records it pins by manifest digest, the objects they name, the notices it names; a version younger than
        PRUNE_GRACE_SECONDS before the keep-set's `computed_at` is kept too), printed WOULD-DELETE, or deleted with --apply. Refused,
        deleting nothing, on an unreadable, foreign-schema or empty keep-set and on a kept candidate the store
        does not hold. Env: GITHUB_TOKEN, the repository's own, whose repository holds the Admin role on the
        package (granted by default to the repository whose workflow first published it).
    store_transport.py licence-audit --repository <owner/repo>
        DN-142.D38 (5b), read-only and anonymous: every record of a PUBLIC store a reader reaches (its record-,
        mismatch- and candidate- tags), each third-party-create one judged with its transport by the push's
        public-seat licence predicate; AUDIT-PASS per passing record, the findings under `AUDIT —` with the
        remedy and exit 1, else `PASSED <n> record(s)` and exit 0. `malf store-transport licence-audit` runs it.
    store_transport.py selftest
        every verb against an in-process registry, a fixture API and recorded-shape attestations; no network

The package guard (push, package-check) reads the package's metadata with MALF_PACKAGE_PEEK_TOKEN when it
is set, else GITHUB_TOKEN: a GITHUB_TOKEN's answer for a PRIVATE package carries no `repository` field, so
a private caller passes a classic token holding read:packages alone, used for that read and nothing else.
W is `<owner>/<malf repository>/.github/workflows/coderoast-candidate.yml`; D the malf commit. Reads use
GITHUB_TOKEN when it is set and go anonymous otherwise (a public package). MALF_STORE_REGISTRY and
MALF_STORE_API replace https://ghcr.io and https://api.github.com (a local registry's live proof).
MALF_TRANSPORT_CACHE names a read cache that outlives the job (every hit re-hashed), bounded after each read verb to
MALF_TRANSPORT_CACHE_MAX_MB (default 8192) by least-recent read; unset, every read verb uses a fresh scratch.
Exit 0 done, 1 refused (the reason on stderr), 2 a usage error, 3 absent.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import spdx_licence  # noqa: E402 — a sibling module, located from this file
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
# A cut runs each candidate at its candidate tag (DN-142.D33, DN-108.D36), so a push to main during the cut changes
# nothing it judges; such an attestation is trusted only when its commit is on main's history.
CANDIDATE_REF_PREFIX = "refs/tags/candidate/"
PEEK_ENV = "MALF_PACKAGE_PEEK_TOKEN"
MISMATCH_HEX = 54
ABSENT = 3
THIRD_PARTY_KINDS = ("third-party-create",)
# The output fields a record names objects by (artefact_store's commit: a package's transport, a
# verdict's log, an image step's OCI archive (store_image.py), the objects convention's map).
OBJECT_FIELDS = (artefact_store.TRANSPORT_FIELD, "log", "archive")
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


def candidate_document(repository: str, tree: str, upstream: dict[str, str],
                       sources: dict[str, str] | None = None) -> dict:
    """{repository, tree, upstream[, sources]}: `sources` names the SOURCE-ONLY upstreams by commit (a repository
    whose recipes this one's packages require and which ships no package, e.g. coderoast-hub's logcraft_dsl;
    the Founder, 2026-10-28), a member only when non-empty, so a candidate naming none keeps its key."""
    owner, name = full_name(repository)
    if not (HEX40.match(tree) or HEX64.match(tree)):
        raise Refused(f"{tree!r} is not a git tree id")
    members = {}
    for upstream_repository, key in upstream.items():
        upstream_owner, upstream_name = full_name(upstream_repository)
        if not isinstance(key, str) or not HEX64.match(key):
            raise Refused(f"upstream {upstream_repository}: {key!r} is not a candidate key (64 hex)")
        members[f"{upstream_owner}/{upstream_name}"] = key
    document = {"repository": f"{owner}/{name}", "tree": tree, "upstream": members}
    if sources:
        commits = {}
        for source_repository, commit in sources.items():
            source_owner, source_name = full_name(source_repository)
            if not isinstance(commit, str) or not HEX40.match(commit):
                raise Refused(f"source {source_repository}: {commit!r} is not a commit (40 hex)")
            commits[f"{source_owner}/{source_name}"] = commit
        document["sources"] = commits
    return document


def candidate_key(repository: str, tree: str, upstream: dict[str, str], sources: dict[str, str] | None = None) -> str:
    return sha256(canonical(candidate_document(repository, tree, upstream, sources)))


def _git(checkout: Path, *argv: str) -> str:
    done = subprocess.run(["git", "-C", str(checkout), *argv], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Refused(f"`git {' '.join(argv)}` in {checkout} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def checkout_identity(checkout: Path) -> tuple[str, str]:
    """(commit, tree) of the checkout's HEAD."""
    return _git(checkout, "rev-parse", "HEAD"), _git(checkout, "rev-parse", "HEAD^{tree}")


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


# THE RUNNER'S TRANSPORT CACHE (night lane 2026-10-30-candidate-perf, lever L4): MALF_TRANSPORT_CACHE names a
# directory that outlives the job (CI: the runner's tool cache), so a blob a previous job fetched is read from disk.
# It is safe to share across the jobs of one runner account only because every read re-hashes the entry (`Reader`):
# a planted or stale entry is a miss, never a byte. Unset, each verb reads through a fresh scratch directory.
TRANSPORT_CACHE_ENV = "MALF_TRANSPORT_CACHE"
TRANSPORT_CACHE_MAX_ENV = "MALF_TRANSPORT_CACHE_MAX_MB"
TRANSPORT_CACHE_MAX_MB = 8192


def transport_cache() -> Path:
    """The read cache: MALF_TRANSPORT_CACHE when set (created), else a fresh scratch directory."""
    named = os.environ.get(TRANSPORT_CACHE_ENV, "")
    if not named:
        return Path(tempfile.mkdtemp(prefix="store_transport."))
    cache = Path(named)
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def prune_cache(cache: Path, cap_bytes: int, say: Callable[[str], None]) -> None:
    """Bound the cache to `cap_bytes`: the least recently read entries go first (a hit refreshes its mtime)."""
    entries = sorted(((entry.stat().st_mtime, entry.stat().st_size, entry)
                      for entry in (cache / "blobs" / "sha256").glob("*") if entry.is_file()),
                     key=lambda row: row[0])
    total = sum(size for _mtime, size, _entry in entries)
    evicted = 0
    for _mtime, size, entry in entries:
        if total <= cap_bytes:
            break
        entry.unlink(missing_ok=True)
        total -= size
        evicted += 1
    if evicted:
        say(f"store_transport: CACHE-PRUNED {evicted} entr{'y' if evicted == 1 else 'ies'}, "
            f"{total >> 20} MiB kept under the {cap_bytes >> 20} MiB cap")


def prune_named_cache(say: Callable[[str], None]) -> None:
    """After a read verb: bound MALF_TRANSPORT_CACHE when it is set (a scratch cache dies with the job)."""
    named = os.environ.get(TRANSPORT_CACHE_ENV, "")
    if named:
        cap = int(os.environ.get(TRANSPORT_CACHE_MAX_ENV, "") or TRANSPORT_CACHE_MAX_MB) << 20
        prune_cache(Path(named), cap, say)


@dataclass
class Reader:
    """Reads by digest, through a runner-local cache: a hit is re-judged by its digest, so a stale or
    foreign entry is a miss, and a fetched byte is judged before the cache keeps it."""

    registry: Registry
    cache: Path
    say: Callable[[str], None]
    fetched: list[str] = field(default_factory=list)

    def _cached(self, hex_digest: str) -> bytes | None:
        if not HEX64.match(hex_digest):
            raise Refused(f"{hex_digest!r} is no sha256 digest, so it names no cache entry")
        path = self.cache / "blobs" / "sha256" / hex_digest
        if path.is_file():
            data = path.read_bytes()
            if sha256(data) == hex_digest:
                os.utime(path)
                return data
            self.say(f"store_transport: CACHE-MISS {hex_digest}: the cached bytes hash to {sha256(data)}")
        return None

    def _keep(self, hex_digest: str, data: bytes) -> None:
        if not HEX64.match(hex_digest):
            raise Refused(f"{hex_digest!r} is no sha256 digest, so it names no cache entry")
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
OnMain = Callable[[str, str], bool]


@dataclass(frozen=True)
class Attested:
    source_commit: str
    signer_digest: str


@dataclass
class Verifier:
    """What a reader requires of an attestation: the store's own repository, malf's reusable
    candidate workflow (and its commit, when the reader pins one), from `refs/heads/main` or from a cut's
    `refs/tags/candidate/...` whose commit is on main's history (`on_main`)."""

    repository: str
    signer_workflow: str
    signer_digest: str | None
    gh: Gh
    tree_of: TreeOf
    on_main: OnMain

    def signer_repository(self) -> str:
        if not self.signer_workflow.lower().endswith("/" + SIGNER_PATH):
            raise Refused(f"{self.signer_workflow!r} is not <owner>/<malf repository>/{SIGNER_PATH}")
        return self.signer_workflow[:-len(SIGNER_PATH) - 1].lower()

    def argv(self, path: Path) -> list[str]:
        return ["attestation", "verify", str(path), "--repo", self.repository,
                "--signer-workflow", self.signer_workflow, "--format", "json",
                *(["--signer-digest", self.signer_digest] if self.signer_digest else [])]

    def attested(self, manifest: bytes) -> list[Attested]:
        """Every attestation of these manifest bytes that names this repository, this signer
        workflow (at the pinned commit, when one is pinned) and main, or a cut's candidate tag whose commit main
        holds (the ref is judged here, not by `gh`); raises Absent when none does."""
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
            ref, commit = str(certificate.get("sourceRepositoryRef", "")), str(certificate.get("sourceRepositoryDigest", ""))
            if (str(certificate.get("sourceRepositoryURI", "")).lower() == source_uri
                    and (ref == SOURCE_REF or (ref.startswith(CANDIDATE_REF_PREFIX) and HEX40.match(commit)
                                               and self.on_main(self.repository, commit)))
                    and str(certificate.get("buildSignerURI", "")).lower().startswith(signer_uri)
                    and (self.signer_digest is None or signer == self.signer_digest)
                    and sha256(manifest) in subjects
                    and HEX40.match(str(certificate.get("sourceRepositoryDigest", ""))) and HEX40.match(signer)):
                found.append(Attested(certificate["sourceRepositoryDigest"], signer))
        if not found:
            raise Absent(f"no attestation of sha256:{sha256(manifest)} names {self.repository} at {SOURCE_REF} "
                         f"(or a candidate tag on it) "
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
        derived = candidate_key(repository, str(index.get("tree")), index.get("upstream") or {}, index.get("sources") or {})
        if derived != key:
            defects.append(f"its repository, tree and upstream hash to {derived}, not {key}")
    except Refused as refused:
        defects.append(str(refused))
    for entry in index.get("records") or []:
        if not HEX64.match(str(entry.get("key"))) or not HEX64.match(str(entry.get("manifest"))):
            defects.append(f"a record entry {entry!r} is not a key and a manifest digest")
    notices = index.get(NOTICES_FIELD, {})
    if not isinstance(notices, dict) or not all(HEX64.match(str(value)) for value in notices.values()):
        defects.append(f"its {NOTICES_FIELD} {notices!r} are not object digests")
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


# ── the public-seat licence rule (DN-142.D38, the Founder's option B) ──────────────────────────────

# A PUBLIC store may hold a third-party binary only when every licence its recipe declares is on this allowlist
# and its package carries its notice; copyleft is never admitted. An allowlist, never a denylist: an identifier
# outside it is refused, copyleft and an unknown permissive one alike, and a new permissive identifier enters by
# a ruling recorded in DN-142.D38. Derived from what the three public stores held on 2026-10-31.
PERMISSIVE = ("Apache-2.0", "BSD-3-Clause", "MIT")
APACHE = "Apache-2.0"
# Upstream source archives VERIFIED to ship no NOTICE at their root, so their packages owe Apache-2.0 4(d)'s
# notice nothing: (name, version) -> (the archive's sha256 as the recipe's conandata.yml names it, the day its
# extracted tree was read). A HAND LIST that fails CLOSED: a version bump changes the archive digest, and the
# push refuses until the new archive is read and its row added here.
NOTICE_ABSENT = {
    ("benchmark", "1.9.5"): ("9631341c82bac4a288bef951f8b26b41f69021794184ece969f8473977eaa340", "2026-10-31"),
    ("simdjson", "4.6.3"): ("bde0c42f43899c4c5c48be826c09abd22500ed537b89f16b3cced5eec477c263", "2026-10-31"),
}
# The MIT notice of every conan-center-index recipe copy a transport carries (`<n>/e/conanfile.py`); a public
# store holding any third-party record holds these bytes as one object its index names (DN-142.D38 (4)).
RECIPE_NOTICE = Path("overlay") / "LICENSE.conan-center-index"
RECIPE_NOTICE_NAME = "conan-center-index"
NOTICES_FIELD = "notices"
EMPTY_FILE_SHA256 = hashlib.sha256(b"").hexdigest()
RECIPE_MEMBER = re.compile(r"^[^/]+/e/conanfile\.py$")
DATA_MEMBER = re.compile(r"^[^/]+/e/conandata\.yml$")
LICENCE_REMEDY = ("remove the dependency from this public repository's graph, read the new upstream archive for a "
                  "NOTICE, or rule a permissive identifier onto the allowlist (copyleft is never admitted)")


def _archive_digest(data: bytes | None, version: str) -> str:
    """The one source archive digest `conandata.yml` names for `version`, or `unread` when it names none or
    several (a list of archives, a per-platform map): the NOTICE-absent table then matches nothing."""
    import yaml

    try:
        document = yaml.safe_load(data.decode("utf-8")) if data is not None else None
    except (UnicodeDecodeError, yaml.YAMLError):
        return "unread"
    entry = ((document or {}).get("sources") or {}).get(version) if isinstance(document, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and HEX64.match(entry["sha256"]):
        return entry["sha256"]
    return "unread"


def licence_findings(record: dict, transport: bytes) -> list[str]:
    """Every way one `third-party-create` record and its transport break the public-seat licence rule, as
    DN-142.D38 (6) spells each reason; empty when the record may stand in a public store. The licence is read
    from the published bytes (the transport's one recipe), the notice from the record's package entries."""
    import tarfile

    try:
        with tarfile.open(fileobj=io.BytesIO(transport), mode="r:*") as archive:
            members = {member.name: member for member in archive.getmembers() if member.isfile()}
            recipes = sorted(name for name in members if RECIPE_MEMBER.match(name))
            if len(recipes) != 1:
                return [f"licence unreadable: the transport holds {len(recipes)} recipe(s), not 1"]
            folder = recipes[0].split("/", 1)[0]
            recipe = archive.extractfile(members[recipes[0]]).read()
            data_name = f"{folder}/e/conandata.yml"
            conandata = archive.extractfile(members[data_name]).read() if data_name in members else None
    except (tarfile.TarError, OSError, EOFError) as unread:
        return [f"licence unreadable: the transport is no readable archive ({unread})"]
    try:
        declared = spdx_licence.recipe_licences(recipe.decode("utf-8"), recipes[0])
    except (UnicodeDecodeError, SyntaxError, ValueError):
        declared = []
    if len(declared) != 1 or not declared[0].literal or not declared[0].identifiers:
        return ["licence unreadable: the transport's recipe holds no literal license attribute"]
    licence = declared[0]
    findings = []
    outside = [identifier for identifier in licence.identifiers if identifier not in PERMISSIVE]
    if outside:
        findings.append(f"licence {licence.expression} names {', '.join(outside)} outside the permissive "
                        f"allowlist {', '.join(PERMISSIVE)}")
    files = [entry for output in record["outputs"].values() if output.get(artefact_store.TRANSPORT_FIELD)
             for entry in output.get("entries") or []
             if isinstance(entry, list) and len(entry) == 3 and entry[1] == "file"
             and str(entry[0]).startswith("licenses/")]
    if not any(entry[2] != EMPTY_FILE_SHA256 for entry in files):
        findings.append("no licence notice: the package holds no non-empty file under licenses/")
    if APACHE in licence.identifiers and not any(Path(entry[0]).name.startswith("NOTICE") for entry in files):
        name, _slash, version = reference_of(record).split("#")[0].split(":")[0].partition("/")
        source = _archive_digest(conandata, version)
        if NOTICE_ABSENT.get((name, version), ("", ""))[0] != source:
            findings.append(f"{APACHE} without NOTICE: licenses/ holds none and {name}/{version} at source {source} "
                            f"is not a verified NOTICE-absent archive")
    return findings


def reference_of(record: dict) -> str:
    """The conan reference a record names: its step's, else its package output's alias."""
    step = record["inputs"]["step"]
    alias = next((output.get("alias") for output in record["outputs"].values() if output.get("alias")), "")
    return str(step.get("reference") or alias or step.get("package") or "?")


def finding_lines(record: dict, transport: bytes) -> list[str]:
    """`<package> <reference> [<profile>]: <reason>`, one per finding of one record."""
    step = record["inputs"]["step"]
    return [f"{step.get('package')} {reference_of(record)} [{step.get('profile') or 'no profile'}]: {reason}"
            for reason in licence_findings(record, transport)]


def record_transports(record: dict) -> list[str]:
    """The transport objects a record's outputs name."""
    return sorted({output[artefact_store.TRANSPORT_FIELD] for output in record["outputs"].values()
                   if isinstance(output.get(artefact_store.TRANSPORT_FIELD), str)})


def judge_public_push(store: Path, records: list[dict], repository: str) -> int:
    """Every owned `third-party-create` record of a PUBLIC store's push judged before the first blob; the whole
    push refused on any finding (DN-142.D38 (5a)). Returns the number of third-party records judged."""
    owner, name = full_name(repository)
    third_party = [record for record in records if record["inputs"]["step"].get("kind") in THIRD_PARTY_KINDS]
    broken: list[list[str]] = []
    for record in third_party:
        lines = []
        for hex_digest in record_transports(record):
            source = store / "objects" / hex_digest
            if not source.is_file() or artefact_store.file_sha256(source) != hex_digest:
                raise Refused(f"the store does not hold object {hex_digest} whole, and a record names it")
            lines += finding_lines(record, source.read_bytes())
        if lines:
            broken.append(lines)
    if broken:
        raise Refused(f"{owner}/{name}'s store is PUBLIC and {len(broken)} third-party record(s) break ADR-3.D3's "
                      f"public-seat licence rule; nothing was pushed:\n"
                      + "".join(f"  {line}\n" for lines in broken for line in lines) + LICENCE_REMEDY)
    return len(third_party)


def licence_audit(reader: Reader, repository: str, malf_dir: Path, say: Callable[[str], None]) -> int:
    """`licence-audit` (DN-142.D38 (5b)), read-only: every record a reader of the store can reach (each
    `record-` and `mismatch-` tag, each record a `candidate-` index pins) read and checked against its key, and
    each `third-party-create` one judged with its transport by the push's predicate. 1 on any finding, else 0.
    Two facts the predicate does not judge are printed beside it: whether the store holds malf's recipe notice
    (a push adds it, DN-142.D38 (4)), and the `sha256-` objects no enumerated record or notice names."""
    owner, name = full_name(repository)
    tags = sorted(reader.registry.tags())
    pinned: dict[str, str] = {}
    for tag in tags:
        if tag.startswith("record-") or tag.startswith("mismatch-"):
            pinned.setdefault(reader.tagged(tag), tag.split("-")[1])
    notices: set[str] = set()
    for tag in (tag for tag in tags if tag.startswith("candidate-")):
        try:
            index = trusted_index(reader, f"{owner}/{name}", tag.removeprefix("candidate-"), None)
        except Absent as absent:
            raise Refused(f"{tag} cannot be read as an index ({absent}): the records it pins are unenumerated, "
                          f"so no verdict is given") from absent
        notices.update(index.get(NOTICES_FIELD, {}).values())
        for entry in index["records"]:
            pinned.setdefault(entry["manifest"], entry["key"])
    named = set(notices)
    judged = 0
    broken: list[list[str]] = []
    for manifest_hex, key in sorted(pinned.items(), key=lambda item: (item[1], item[0])):
        record = judged_record(reader.layer(manifest_hex, RECORD_MEDIA), key)
        named.update(record_objects(record))
        if record["inputs"]["step"].get("kind") not in THIRD_PARTY_KINDS:
            continue
        judged += 1
        lines = []
        for hex_digest in record_transports(record):
            found = finding_lines(record, reader.blob(hex_digest))
            lines += found
            if not found:
                step = record["inputs"]["step"]
                say(f"store_transport: AUDIT-PASS {step.get('package')} {reference_of(record)} "
                    f"[{step.get('profile') or 'no profile'}] record-{key} transport sha256-{hex_digest}")
        if lines:
            broken.append(lines)
    notice = sha256((malf_dir / RECIPE_NOTICE).read_bytes())
    say(f"store_transport: AUDIT-NOTICE {owner}/{name} "
        + (f"holds malf's recipe notice sha256-{notice}" if object_tag(notice) in tags else
           f"does not hold malf's recipe notice sha256-{notice} (DN-142.D38 (4)): the next public push adds it"))
    unnamed = sorted(tag for tag in tags if tag.startswith("sha256-") and tag.removeprefix("sha256-") not in named)
    say(f"store_transport: AUDIT-UNNAMED {len(unnamed)} sha256- object(s) of {owner}/{name} named by no "
        f"enumerated record nor notice, not judged: {', '.join(unnamed) or 'none'}")
    if broken:
        say(f"store_transport: AUDIT — {owner}/{name}'s store is PUBLIC and {len(broken)} third-party record(s) "
            f"break ADR-3.D3's public-seat licence rule:")
        for lines in broken:
            for line in lines:
                say(f"  {line}")
        say(LICENCE_REMEDY)
        return 1
    say(f"store_transport: AUDIT {owner}/{name}: PASSED {judged} record(s) — every third-party-create record of "
        f"{len(pinned)} enumerated record(s) over {len(tags)} tag(s)")
    return 0


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
    """An object by its digest, under store_replica's canonical object manifest; never rewritten."""
    source = store / "objects" / hex_digest
    if not source.is_file() or artefact_store.file_sha256(source) != hex_digest:
        raise Refused(f"the store does not hold object {hex_digest} whole, and a record names it")
    return put_object_bytes(registry, hex_digest, source.stat().st_size, source.read_bytes, say)


def put_object_bytes(registry: Registry, hex_digest: str, size: int, read: Callable[[], bytes],
                     say: Callable[[str], None]) -> str:
    """The object `read` returns, under its `sha256-<hex>` tag; its bytes read only when the tag is absent."""
    manifest = store_replica.object_manifest(hex_digest, size)
    manifest_hex = sha256(manifest)
    held = registry.manifest_digest(object_tag(hex_digest))
    if held == f"sha256:{manifest_hex}":
        say(f"store_transport: PRESENT sha256-{hex_digest}")
        return manifest_hex
    if held is not None:
        raise Refused(f"{registry.name}:{object_tag(hex_digest)} holds {held}, not the object's manifest "
                      f"sha256:{manifest_hex}: an object's name is never rewritten")
    _put(registry, object_tag(hex_digest), manifest, [read()])
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


def push(registry: Registry, guard: Guard, store: Path, repository: str, checkout: Path,
         upstream: dict[str, str], malf_dir: Path, extra: dict, out: Path, say: Callable[[str], None],
         sources: dict[str, str] | None = None) -> int:
    """Every owned record of `store` and its objects, then the index's blob; the index and the subjects
    to attest written to `out`. 1 when a record at an existing key differs (nothing indexed). A PUBLIC store's
    owned third-party records are judged under the public-seat licence rule before the guard writes anything,
    the whole push refused on a finding, and malf's recipe notice joins it as one object the index names
    (DN-142.D38 (4), (5a)); a private store is not judged."""
    owner, name = full_name(repository)
    commit, tree = checkout_identity(checkout)
    key = candidate_key(repository, tree, upstream, sources)
    malf_commit, malf_tree = _git(malf_dir, "rev-parse", "HEAD"), artefact_store.worktree_tree_id(malf_dir)
    if malf_tree != _git(malf_dir, "rev-parse", "HEAD^{tree}"):
        raise Refused(f"malf at {malf_dir} differs from its commit {malf_commit}: an index names the commit "
                      f"that ran, and this one did not")
    packages = owned_packages(checkout, commit)
    owned: list[tuple[Path, dict]] = []
    upstream_records = 0
    for path in sorted((store / "records").glob("*.json")):
        record = judged_record(path.read_bytes(), path.stem)
        if record_owned(record, name, packages):
            owned.append((path, record))
        else:
            upstream_records += 1
    expected = guard.expected()
    third_party = (judge_public_push(store, [record for _path, record in owned], repository)
                   if expected.visibility == "public" else 0)
    guard.ensure(expected)
    pushed = Pushed()
    for path, _record in owned:
        push_record(registry, store, path, pushed, say)
    if pushed.mismatches:
        for line in pushed.mismatches:
            say(f"store_transport: MISMATCH {line}")
        return 1
    notices = {}
    if third_party:
        notice = malf_dir / RECIPE_NOTICE
        if not notice.is_file():
            raise Refused(f"malf at {malf_dir} holds no {RECIPE_NOTICE}, the notice of every recipe copy a public "
                          f"store's third-party transports carry (DN-142.D38 (4))")
        data = notice.read_bytes()
        notices[RECIPE_NOTICE_NAME] = sha256(data)
        pushed.subjects.append((object_tag(sha256(data)),
                                put_object_bytes(registry, sha256(data), len(data), lambda: data, say)))
    document = candidate_document(repository, tree, upstream, sources)
    index = {"schema": INDEX_SCHEMA, "repository": f"{owner}/{name}", "commit": commit, "tree": tree,
             "candidate": key, "upstream": document["upstream"], **({"sources": document["sources"]} if sources else {}),
             "malf": {"commit": malf_commit, "tree": malf_tree},
             "image": json.loads(ENVIRONMENT_PIN.read_text(encoding="utf-8"))["image"],
             "records": sorted(pushed.entries, key=lambda entry: entry["key"]), "extra": extra,
             **({NOTICES_FIELD: notices} if notices else {})}
    index_bytes = canonical(index)
    registry.push_blob(sha256(index_bytes), index_bytes)
    manifest = one_layer_manifest(INDEX_TYPE, INDEX_MEDIA, index_bytes)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_bytes(index_bytes)
    subjects = sorted(set(pushed.subjects)) + [(candidate_tag(key), sha256(manifest))]
    (out / "subjects.sha256").write_text("".join(f"{digest}  {tag}\n" for tag, digest in subjects),
                                         encoding="utf-8")
    guard.ensure(guard.expected())
    say(f"store_transport: PUSHED candidate {key} of {owner}/{name}: {len(pushed.entries)} record(s), "
        f"{third_party} third-party record(s) judged under the public-seat licence rule, "
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


# ── the prune (DN-142.D32 (2)) ─────────────────────────────────────────────────────────────────────

# THE STORE NEVER DELETES ON ITS OWN READING: the superproject decides the keep-set once every release of a cut
# is green, and each repository deletes, with its own token, every version of its own store that the keep-set's
# CLOSURE does not name. The closure is derived from the kept indexes, never listed: a kept index, every record
# it pins by manifest digest (under its `record-<key>` tag or untagged, an EQUAL-RESULT push), and every object
# such a record names (`sha256-<hex>`). A keep-set naming a candidate this store does not hold is refused whole,
# as is an empty, unparseable or foreign one: a vacuous input must not wipe a store.
KEEP_SET_SCHEMA = "pharos-keep-set/1"
# A candidate run pushes its records before it publishes its index, and a run takes up to six hours (Pharos's
# WAIT_SECONDS for one wave), so a version created within that bound before the keep-set was computed may belong
# to a candidate still running, which no keep-set of that moment could name: it is kept.
PRUNE_GRACE_SECONDS = 6 * 3600
PRUNE_PAGE = 100
STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|\+00:00)$")


@dataclass(frozen=True)
class Version:
    """One version of a container package as the packages API lists it: its id, manifest digest, tags, birth."""

    id: int
    digest: str
    tags: tuple[str, ...]
    created: int


def utc_seconds(text: object) -> int:
    """Seconds since the epoch of a UTC ISO-8601 stamp (`...Z` or `...+00:00`), refusing any other shape."""
    import calendar

    matched = STAMP.match(str(text))
    if matched is None:
        raise Refused(f"{text!r} is not a UTC ISO-8601 stamp")
    return calendar.timegm(tuple(int(part) for part in matched.groups()) + (0, 0, 0))


def read_keep_set(path: Path, repository: str) -> tuple[list[str], int]:
    """(the kept candidate keys of `repository`, sorted; the keep-set's computation time). Refused when the file
    is unreadable, not a `pharos-keep-set/1` document, or names no key of this repository."""
    _owner, name = full_name(repository)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unread:
        raise Refused(f"the keep-set {path} is unreadable ({unread}): it is never read as keep nothing") from unread
    if not isinstance(document, dict) or document.get("schema") != KEEP_SET_SCHEMA:
        raise Refused(f"the keep-set {path} is not a {KEEP_SET_SCHEMA} document")
    computed = utc_seconds(document.get("computed_at"))
    keys = (document.get("repositories") or {}).get(name) if isinstance(document.get("repositories"), dict) else None
    if not isinstance(keys, list) or not keys:
        raise Refused(f"the keep-set {path} names no candidate of {name}: an empty keep-set is never read as keep "
                      f"nothing")
    bad = [key for key in keys if not isinstance(key, str) or not HEX64.match(key)]
    if bad:
        raise Refused(f"the keep-set {path} names {bad!r} for {name}, which are not candidate keys (64 hex)")
    return sorted(set(keys)), computed


def kept_closure(reader: Reader, repository: str, keys: list[str]) -> tuple[set[str], set[str]]:
    """(every manifest digest, every tag) the kept candidates' closure names in this store: each index, each
    record it pins, each object such a record names, each notice object the index names. A kept key whose index is absent or off its shape refuses
    the prune: what it would have kept cannot be derived, so nothing is deleted."""
    digests: set[str] = set()
    tags: set[str] = set()
    for key in keys:
        try:
            manifest_hex = reader.tagged(candidate_tag(key))
            # note: no attestation is read: the index's key derivation is checked, and a keep decision only spares
            index = trusted_index(reader, repository, key, None)
        except Absent as absent:
            raise Refused(f"the keep-set keeps candidate-{key}, and the store answers: {absent}") from absent
        digests.add(manifest_hex)
        tags.add(candidate_tag(key))
        for entry in index["records"]:
            digests.add(entry["manifest"])
            tags.add(record_tag(entry["key"]))
            record = judged_record(reader.layer(entry["manifest"], RECORD_MEDIA), entry["key"])
            tags.update(object_tag(hex_digest) for hex_digest in record_objects(record))
        tags.update(object_tag(hex_digest) for hex_digest in index.get(NOTICES_FIELD, {}).values())
    return digests, tags


def prune_plan(versions: list[Version], digests: set[str], tags: set[str], computed: int
               ) -> tuple[list[Version], list[Version]]:
    """(kept, deleted): a version is kept when the closure names its digest or one of its tags, or when it was
    created within PRUNE_GRACE_SECONDS before the keep-set was computed, or after."""
    kept, deleted = [], []
    for version in sorted(versions, key=lambda each: each.id):
        named = version.digest in digests or bool(tags.intersection(version.tags))
        (kept if named or version.created >= computed - PRUNE_GRACE_SECONDS else deleted).append(version)
    return kept, deleted


def prune(reader: Reader, repository: str, keep_set: Path, versions: Callable[[], list[Version]],
          delete: Callable[[int], int], apply: bool, say: Callable[[str], None]) -> int:
    """`prune`: the plan over the store's versions, printed; with `apply`, each deleted version removed through
    `delete` (its id -> HTTP status), every non-204 answer named and the exit 1. Without `apply`, a dry run."""
    owner, name = full_name(repository)
    keys, computed = read_keep_set(keep_set, repository)
    digests, tags = kept_closure(reader, f"{owner}/{name}", keys)
    listed = versions()
    if not listed:
        raise Refused(f"{package_name(name)} lists no version, and the keep-set keeps {len(keys)} candidate(s) of it")
    kept, deleted = prune_plan(listed, digests, tags, computed)
    verb = "DELETE" if apply else "WOULD-DELETE"
    for version in deleted:
        say(f"store_transport: {verb} {package_name(name)} version {version.id} sha256:{version.digest} "
            f"tags {', '.join(version.tags) or '(untagged)'}")
    failed = []
    if apply:
        for version in deleted:
            status = delete(version.id)
            if status != 204:
                failed.append(f"version {version.id} (sha256:{version.digest}): HTTP {status}")
    say(f"store_transport: PRUNE {owner}/{name} {'applied' if apply else 'dry run'}: {len(keys)} kept candidate(s), "
        f"{len(kept)} version(s) kept, {len(deleted)} {'deleted' if apply else 'to delete'}"
        + (f", {len(failed)} refused by the API" if failed else ""))
    for line in failed:
        say(f"store_transport: DELETE-REFUSED {line}: the repository's token needs the Admin role on the package "
            f"(its settings, Manage Actions access)")
    return 1 if failed else 0


def live_versions(api: str, token: str, owner: str, package: str,
                  get: Callable[[str], "tuple[int, dict]"]) -> Callable[[], list[Version]]:
    """The packages API's version listing of `owner`'s container `package`, every page."""
    def listed() -> list[Version]:
        status, user = get(f"users/{owner}")
        if status != 200:
            raise Refused(f"the API answered HTTP {status} for the owner {owner}")
        scope = "orgs" if user.get("type") == "Organization" else "users"
        found: list[Version] = []
        page = 1
        while True:
            status, body = api_get(api, f"{scope}/{owner}/packages/container/{package}/versions"
                                        f"?per_page={PRUNE_PAGE}&page={page}", token)
            if status != 200 or not isinstance(body, list):
                raise Refused(f"the packages API answered HTTP {status} listing {package}'s versions (page {page})")
            for entry in body:
                digest = str(entry.get("name") or "")
                if not digest.startswith("sha256:") or not HEX64.match(digest[7:]):
                    raise Refused(f"{package} version {entry.get('id')} is named {digest!r}, not a manifest digest")
                tags = ((entry.get("metadata") or {}).get("container") or {}).get("tags") or []
                found.append(Version(int(entry["id"]), digest[7:], tuple(sorted(str(tag) for tag in tags)),
                                     utc_seconds(entry.get("created_at"))))
            if len(body) < PRUNE_PAGE:
                return found
            page += 1
    return listed


def live_delete(api: str, token: str, owner: str, package: str,
                get: Callable[[str], "tuple[int, dict]"]) -> Callable[[int], int]:
    def delete(version_id: int) -> int:
        _status, user = get(f"users/{owner}")
        scope = "orgs" if user.get("type") == "Organization" else "users"
        request = urllib.request.Request(f"{api}/{scope}/{owner}/packages/container/{package}/versions/{version_id}",
                                         method="DELETE")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {token}")
        return store_replica._send(request).status
    return delete


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
    """What the repository's own store package must be (store_replica.expected_package), from the
    repository's visibility as the API answers it."""
    status, seen = get(f"repos/{owner}/{name}")
    if status != 200 or seen.get("visibility") not in store_replica.VISIBILITIES:
        raise Refused(f"the API answered HTTP {status} and visibility {seen.get('visibility')!r} for "
                      f"{owner}/{name}")
    return store_replica.expected_package(owner, name, seen["visibility"])


@dataclass(frozen=True)
class Guard:
    """The writer's guard, in two reads: `expected` is what the package must be, from the repository's visibility
    (`own_expected`), and `ensure` holds the package to it, creating and guarding it on a first push. Split so the
    push judges a public store's licences on the visibility the guard enforces, before the guard writes."""

    expected: Callable[[], store_replica.Expected]
    ensure: Callable[[store_replica.Expected], None]


def package_guard(get: Callable[[str], "tuple[int, dict]"], peek: Callable[[str], "tuple[int, dict]"],
                  registry: Registry, repository: str, say: Callable[[str], None]) -> Guard:
    """The writer's guard for an OWN store: the package has the repository's visibility and is linked to it.
    `peek` reads the package's metadata: a private package's link is absent from a GITHUB_TOKEN's answer
    (measured 2026-10-28, coderoast-security run 40687600161), so a private caller passes a read:packages token."""
    owner, name = full_name(repository)
    return Guard(lambda: own_expected(get, owner, name),
                 lambda expected: store_replica.ensure_guarded(registry, package_view(peek, owner), owner,
                                                               package_name(name), say, expected))


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


def resolve_upstreams(document: dict[str, str], read_index: Callable[[str, str], dict],
                      clone: Callable[[str, str, Path], str] | None, sources: Path | None,
                      say: Callable[[str], None], hidden: Callable[[str], bool] = lambda repository: False
                      ) -> dict[str, dict]:
    """Each upstream candidate the document names, read from its own store (DN-142.D31 (2)): `{owner/repo:
    {key, commit, tree[, root]}}`. With `sources`, each is cloned at the commit its index records into
    `<sources>/<repo>` and its tree checked against the index, unless `hidden` names it private: a private
    upstream is never cloned (another repository's token reads no private repository), and its recipes and
    export sources arrive through its own records' transports instead (the Founder, 2026-10-28, option C).
    raises Refused when an upstream's own upstream
    is absent from the document or named at another key (one graph holds one candidate of each repository),
    or when a clone's tree is not the index's; Absent when an upstream has no candidate at its key."""
    named = {"/".join(full_name(repository)).lower(): key for repository, key in document.items()}
    resolved: dict[str, dict] = {}
    for repository, key in sorted(named.items()):
        if not HEX64.match(str(key)):
            raise Refused(f"the upstream {repository} is named at {key!r}, not a candidate key (64 hex)")
        index = read_index(repository, key)
        for inner, inner_key in sorted((index.get("upstream") or {}).items()):
            inner = "/".join(full_name(inner)).lower()
            if named.get(inner) != inner_key:
                raise Refused(f"the upstream {repository} candidate-{key} was built over {inner} candidate-{inner_key}, "
                              f"and this document names {inner} at {named.get(inner) or 'no key'}: one graph holds "
                              f"one candidate of each repository, so name {inner} at {inner_key}")
        entry = {"key": key, "commit": index["commit"], "tree": index["tree"]}
        if sources is not None and clone is not None and not hidden(repository):
            root = sources / full_name(repository)[1]
            tree = clone(repository, index["commit"], root)
            if tree != index["tree"]:
                raise Refused(f"{repository} at {index['commit']} cloned to tree {tree}, and its candidate-{key} "
                              f"records tree {index['tree']}")
            entry["root"] = str(root)
        say(f"store_transport: UPSTREAM {repository} candidate-{key} at commit {index['commit']}")
        resolved[repository] = entry
    return resolved


def live_clone(server: str, token: str | None) -> Callable[[str, str, Path], str]:
    """`clone(repository, commit, root)`: a shallow checkout of that one commit, its tree id returned; the
    token, for a private repository, reaches git through its environment, never an argument vector."""
    def clone(repository: str, commit: str, root: Path) -> str:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{server}/.extraheader",
                        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
        for argv in (["init", "--quiet"], ["remote", "add", "origin", f"{server}/{repository}.git"],
                     ["fetch", "--quiet", "--depth", "1", "origin", commit],
                     ["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", commit]):
            done = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, env=env, check=False)
            if done.returncode != 0:
                raise Refused(f"cloning {repository} at {commit}: git {argv[0]} exited {done.returncode}: "
                              f"{done.stderr.strip()[-400:]}")
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], capture_output=True,
                              text=True, check=True).stdout.strip()
    return clone


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


def live_on_main(get: Callable[[str], "tuple[int, dict]"]) -> OnMain:
    """Is `commit` on the repository's main history? GitHub's compare of the commit to main: `identical` or
    `ahead` (main holds it); anything else, or an API that cannot answer, is no."""
    def on_main(repository: str, commit: str) -> bool:
        status, body = get(f"repos/{repository}/compare/{commit}...main")
        return status == 200 and body.get("status") in ("identical", "ahead")
    return on_main


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


def _repository(root: Path, packages: list[str], candidate: bool, files: dict[str, bytes] | None = None) -> Path:
    checkout = root / "checkout"
    checkout.mkdir()
    for relative, data in (files or {}).items():
        (checkout / relative).parent.mkdir(parents=True, exist_ok=True)
        (checkout / relative).write_bytes(data)
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


FIXTURE_LICENCE = b"Permission is granted, the fixture's licence text.\n"


def _third_party_transport(recipe: str, conandata: str | None, recipes: int = 1) -> bytes:
    """A `conan cache save` shape, uncompressed so its bytes are a function of its members: `recipes` copies of
    the recipe under `<n>/e/`, the first one's conandata.yml beside it, and the package's licence."""
    import tarfile

    members = [(f"pkg{number}/e/conanfile.py", recipe.encode()) for number in range(recipes)]
    members += [("pkg0/e/conandata.yml", conandata.encode())] if conandata is not None else []
    members += [("b/pkg/p/licenses/LICENSE", FIXTURE_LICENCE)]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _put_third_party(store: Path, package: str, version: str, licence: str, *, conandata: str | None = None,
                     recipes: int = 1, licences: list[tuple[str, bytes]] | None = None) -> str:
    """A `third-party-create` record of `package`/`version` whose transport's recipe declares `licence` (Python
    source of the attribute's value) and whose package entries hold `licences` (default: one non-empty LICENSE)."""
    recipe = f"from conan import ConanFile\n\n\nclass Package(ConanFile):\n    name = {package!r}\n    license = {licence}\n"
    transport = _put_object(store, _third_party_transport(recipe, conandata, recipes))
    held = [("licenses/LICENSE", FIXTURE_LICENCE)] if licences is None else licences
    return _put_record(store, {"step": {"kind": "third-party-create", "package": package, "package_id": "1" * 40,
                                        "profile": "linux-gcc16-release", "reference": f"{package}/{version}#rrev"}},
                       {"package": {"alias": f"{package}/{version}#rrev:{'1' * 40}#prev", "content": sha256(recipe.encode()),
                                    "transport": transport,
                                    "entries": [["conaninfo.txt", "file", "a" * 64]]
                                    + [[path, "file", sha256(data)] for path, data in held]}})


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
            malf_dir = _repository(root / "malf", ["malf"], candidate=False,
                                   files={str(RECIPE_NOTICE): b"MIT License\n\nthe fixture's recipe notice\n"})
            notice = sha256((malf_dir / RECIPE_NOTICE).read_bytes())
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
            expect("a candidate naming no source-only upstream keeps its key (the member is absent, not empty)",
                   candidate_key(repository, tree, {}, {}), key)
            expect("a source-only upstream's commit changes the key",
                   candidate_key(repository, tree, {}, {"CodeRoasted/coderoast-hub": "b" * 40}) != key, True)
            arm("RED: a source-only upstream named by no commit is refused",
                lambda: candidate_key(repository, tree, {}, {"o/r": "a" * 64}), "not a commit")
            arm("RED: an upstream member that is not a candidate key is refused",
                lambda: candidate_key(repository, tree, {"o/r": "short"}), "not a candidate key")
            arm("RED: a repository that is not owner/name is refused",
                lambda: candidate_key("coderoast-ipc", tree, {}), "not an owner/repository name")

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
            third = _put_third_party(store, "gtest", "1.17.0", '"BSD-3-Clause"')
            third_transport = record_transports(json.loads((store / "records" / f"{third}.json").read_bytes()))[0]
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
            expect("the subjects are every object, the recipe notice, every record and the index, by manifest digest",
                   (len(subjects), subjects[-1]), (3 + 1 + 3 + 1, f"{index_manifest}  {candidate_tag(key)}"))
            expect("a public store holding a third-party record holds malf's recipe notice, named by the index "
                   "(DN-142.D38 (4))", (index.get(NOTICES_FIELD), object_tag(notice) in fake.tags,
                                        fake.blobs.get(f"sha256:{notice}") == (malf_dir / RECIPE_NOTICE).read_bytes()),
                   ({RECIPE_NOTICE_NAME: notice}, True, True))
            expect("an object's manifest is store_replica's canonical object manifest",
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
            on_main = {("coderoasted/coderoast-ipc", commit)}
            asked: list[list[str]] = []

            def verifier(certificate: dict | None = None, code: int = 0, digest: str | None = None,
                         subject: str | None = None) -> Verifier:
                def gh(argv: list[str]) -> tuple[int, str, str]:
                    asked.append(argv)
                    manifest_hex = subject or sha256(Path(argv[2]).read_bytes())
                    return code, _fixture_attestations(manifest_hex, **{**good_certificate, **(certificate or {})}), \
                        "" if code == 0 else "no matching attestations found"
                return Verifier("coderoasted/coderoast-ipc", signer, digest, gh,
                                lambda repo, sha: trees.get((repo.lower(), sha), "0" * 40),
                                lambda repo, sha: (repo.lower(), sha) in on_main)

            reader = Reader(registry, root / "cache", quiet.append)
            arm("candidate-present: an attested index whose content agrees with its certificate is PRESENT",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(digest=malf_commit)), None)
            expect("the verifier asks gh for the repository, the signer workflow and the malf commit; the ref it judges itself",
                   asked[-1][3:], ["--repo", "coderoasted/coderoast-ipc", "--signer-workflow", signer,
                                   "--format", "json", "--signer-digest", malf_commit])
            arm("a cut's candidate tag whose commit is on main is trusted as main is (DN-142.D33)",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(
                    {"sourceRepositoryRef": "refs/tags/candidate/9.9.9/0123456789abcdef"}, digest=malf_commit)), None)
            on_main.clear()
            arm("RED: a candidate tag whose commit main does not hold is no attestation",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(
                    {"sourceRepositoryRef": "refs/tags/candidate/9.9.9/0123456789abcdef"}, digest=malf_commit)),
                "no attestation", Absent)
            on_main.add(("coderoasted/coderoast-ipc", commit))
            arm("RED: another tag, even on main, is no attestation: only a cut's candidate tag stands for main",
                lambda: trusted_index(reader, "coderoasted/coderoast-ipc", key, verifier(
                    {"sourceRepositoryRef": "refs/tags/v9.9.9"}, digest=malf_commit)), "no attestation", Absent)
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
                   sorted([transport, log, third_transport]))
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
            # -- the runner's transport cache (L4): MALF_TRANSPORT_CACHE outlives a verb, every hit re-hashed, bounded.
            os.environ[TRANSPORT_CACHE_ENV] = str(root / "runner-cache")
            try:
                first = Reader(registry, transport_cache(), quiet.append)
                first.blob(transport)
                second = Reader(registry, transport_cache(), quiet.append)
                second.blob(transport)
                expect("MALF_TRANSPORT_CACHE outlives a verb: a second reader on it reads the blob from disk",
                       (first.fetched, second.fetched), ([transport], []))
            finally:
                del os.environ[TRANSPORT_CACHE_ENV]
            arm("RED: a name that is no sha256 digest names no cache entry, so no path is built from it",
                lambda: Reader(registry, root / "cache6", quiet.append).blob("../../escape"), "is no sha256 digest")
            bounded = root / "bounded" / "blobs" / "sha256"
            bounded.mkdir(parents=True)
            for age, name in enumerate(("a" * 64, "b" * 64, "c" * 64)):
                (bounded / name).write_bytes(b"x" * 1024)
                os.utime(bounded / name, (1_000_000 + age, 1_000_000 + age))
            prune_cache(root / "bounded", 2048, quiet.append)
            expect("the cache is bounded: the least recently read entries go first, down to the cap",
                   sorted(entry.name[0] for entry in bounded.iterdir()), ["b", "c"])
            # -- a dependent candidate's upstreams (DN-142.D31 (2)): read through their own stores, one each.
            def read_ipc(repository: str, wanted: str) -> dict:
                return trusted_index(Reader(registry, root / "cache5", quiet.append), repository, wanted, verifier())
            resolved = arm("upstream: the document's candidate is read through its store, its commit and tree named",
                           lambda: resolve_upstreams({"CodeRoasted/coderoast-ipc": key}, read_ipc,
                                                     lambda repository, at, into: tree, root / "up", quiet.append), None)
            expect("upstream: the commit and the cloned root are the index's",
                   (resolved or {}).get("coderoasted/coderoast-ipc", {}).get("commit"), commit)
            def no_clone(repository: str, at: str, into: Path) -> str:
                raise Refused(f"cloned the private upstream {repository}")
            private_resolved = arm("upstream: a PRIVATE upstream is never cloned (its recipes come through its transports)",
                                   lambda: resolve_upstreams({"CodeRoasted/coderoast-ipc": key}, read_ipc, no_clone,
                                                             root / "up3", quiet.append, lambda repository: True), None)
            expect("upstream: a private upstream's entry names no cloned root",
                   "root" in (private_resolved or {}).get("coderoasted/coderoast-ipc", {"root": 1}), False)
            expect("upstream: a repository this token cannot see (404) is private, a visible one by its visibility",
                   [upstream_is_private(lambda path: (404, {}), "o/hidden"),
                    upstream_is_private(lambda path: (200, {"visibility": "public"}), "o/open"),
                    upstream_is_private(lambda path: (200, {"visibility": "private"}), "o/closed")], [True, False, True])
            arm("RED: any other API answer about an upstream is refused, never guessed",
                lambda: upstream_is_private(lambda path: (500, {}), "o/x"), "HTTP 500")
            arm("RED: an upstream cloned to another tree than its index records is refused",
                lambda: resolve_upstreams({"CodeRoasted/coderoast-ipc": key}, read_ipc,
                                          lambda repository, at, into: "f" * 40, root / "up2", quiet.append), "records tree")
            arm("RED: an upstream built over another candidate of a shared upstream is refused (one candidate per repository)",
                lambda: resolve_upstreams({"CodeRoasted/coderoast-ipc": key},
                                          lambda repository, wanted: {**read_ipc(repository, wanted),
                                                                      "upstream": {"CodeRoasted/insight-canon": "a" * 64}},
                                          None, None, quiet.append), "one graph holds")
            arm("RED: an upstream named at no candidate key is refused",
                lambda: resolve_upstreams({"x/y": "short"}, read_ipc, None, None, quiet.append), "not a candidate key")
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

            # -- the public-seat licence rule (DN-142.D38): a PUBLIC push judges every owned third-party record before
            # the guard writes anything, and one finding refuses the whole push; a private store is not judged.
            def conandata(version: str, digest: str) -> str:
                return f'sources:\n  "{version}":\n    url: "https://example.invalid/v{version}.tar.gz"\n    sha256: "{digest}"\n'

            listed_benchmark = NOTICE_ABSENT[("benchmark", "1.9.5")][0]
            listed_simdjson = NOTICE_ABSENT[("simdjson", "4.6.3")][0]
            breaking = (
                ("a copyleft record", lambda at: _put_third_party(at, "readline", "8.2", '"GPL-3.0-or-later"'),
                 "readline readline/8.2#rrev [linux-gcc16-release]: licence GPL-3.0-or-later names GPL-3.0-or-later "
                 "outside the permissive allowlist Apache-2.0, BSD-3-Clause, MIT"),
                ("a permissive identifier off the allowlist", lambda at: _put_third_party(at, "zlib", "1.3.1", '"Zlib"'),
                 "zlib zlib/1.3.1#rrev [linux-gcc16-release]: licence Zlib names Zlib outside the permissive allowlist"),
                ("a dual licence with one copyleft alternative",
                 lambda at: _put_third_party(at, "dual", "1.0", '"MIT OR LGPL-2.1-only"'),
                 "licence MIT OR LGPL-2.1-only names LGPL-2.1-only outside the permissive allowlist"),
                ("an empty licenses/",
                 lambda at: _put_third_party(at, "fmt", "12.1.0", '"MIT"', licences=[("licenses/LICENSE", b"")]),
                 "fmt fmt/12.1.0#rrev [linux-gcc16-release]: no licence notice: the package holds no non-empty file "
                 "under licenses/"),
                ("an Apache-2.0 record without NOTICE whose version is outside the table",
                 lambda at: _put_third_party(at, "benchmark", "1.9.6", '"Apache-2.0"',
                                             conandata=conandata("1.9.6", "b" * 64)),
                 f"benchmark benchmark/1.9.6#rrev [linux-gcc16-release]: Apache-2.0 without NOTICE: licenses/ holds none "
                 f"and benchmark/1.9.6 at source {'b' * 64} is not a verified NOTICE-absent archive"),
                ("an Apache-2.0 record at a listed version whose archive is another",
                 lambda at: _put_third_party(at, "benchmark", "1.9.5", '"Apache-2.0"',
                                             conandata=conandata("1.9.5", "c" * 64)),
                 f"benchmark/1.9.5 at source {'c' * 64} is not a verified NOTICE-absent archive"),
                ("a transport holding 2 recipes",
                 lambda at: _put_third_party(at, "spdlog", "1.17.0", '"MIT"', recipes=2),
                 "spdlog spdlog/1.17.0#rrev [linux-gcc16-release]: licence unreadable: the transport holds 2 recipe(s), "
                 "not 1"),
                ("a recipe with no literal license attribute",
                 lambda at: _put_third_party(at, "glaze", "7.4.0", "LICENCE_OF_THE_DAY"),
                 "glaze glaze/7.4.0#rrev [linux-gcc16-release]: licence unreadable: the transport's recipe holds no "
                 "literal license attribute"))
            every_breaking = root / "licence-every"
            for number, (label, plant, line) in enumerate(breaking):
                bad = root / f"licence-{number}"
                _put_third_party(bad, "gtest", "1.17.0", '"BSD-3-Clause"')
                plant(bad)
                plant(every_breaking)
                refused_fake, refused_registry = scene()
                arm(f"RED: a public push refuses {label}, naming it",
                    lambda bad=bad, refused_registry=refused_registry, number=number: push(
                        refused_registry, package_guard(get_from(api), get_from(api), refused_registry, repository,
                                                        quiet.append),
                        bad, repository, checkout, {}, malf_dir, {}, root / f"licence-out-{number}", quiet.append),
                    line)
                expect(f"nothing was pushed on {label}: no blob, no tag, not even the package's sentinel",
                       (len(refused_fake.blobs), sorted(refused_fake.tags)), (0, []))
            single = root / "licence-single"
            _put_third_party(single, "readline", "8.2", '"GPL-3.0-or-later"')
            single_fake, single_registry = scene()
            try:
                push(single_registry, package_guard(get_from(api), get_from(api), single_registry, repository,
                                                    quiet.append),
                     single, repository, checkout, {}, malf_dir, {}, root / "licence-single-out", quiet.append)
                refusal_text = "pushed"
            except Refused as refused:
                refusal_text = str(refused)
            expect("the refusal is D38 (6)'s text whole: the head, one line per finding, the remedy",
                   refusal_text,
                   "coderoasted/coderoast-ipc's store is PUBLIC and 1 third-party record(s) break ADR-3.D3's public-seat "
                   "licence rule; nothing was pushed:\n  readline readline/8.2#rrev [linux-gcc16-release]: licence "
                   "GPL-3.0-or-later names GPL-3.0-or-later outside the permissive allowlist Apache-2.0, BSD-3-Clause, "
                   f"MIT\n{LICENCE_REMEDY}")
            private_fake, private_registry = scene()
            arm("a private store holding every refused record takes the push: a private store is not judged",
                lambda: push(private_registry, package_guard(get_from(private), get_from(private), private_registry,
                                                             repository, quiet.append),
                             every_breaking, repository, checkout, {}, malf_dir, {}, root / "licence-private",
                             quiet.append), None)
            private_index = json.loads((root / "licence-private" / "index.json").read_bytes())
            expect("the private push indexes every record and carries no recipe notice",
                   (len(private_index["records"]), NOTICES_FIELD in private_index, object_tag(notice) in private_fake.tags),
                   (len(breaking), False, False))
            passing = root / "licence-pass"
            _put_third_party(passing, "gtest", "1.17.0", '"BSD-3-Clause"')
            _put_third_party(passing, "benchmark", "1.9.5", '"Apache-2.0"', conandata=conandata("1.9.5", listed_benchmark))
            _put_third_party(passing, "simdjson", "4.6.3", '("Apache-2.0", "MIT")',
                             conandata=conandata("4.6.3", listed_simdjson))
            _put_third_party(passing, "noticed", "2.0", '"Apache-2.0 WITH LLVM-exception"',
                             conandata=conandata("2.0", "d" * 64),
                             licences=[("licenses/LICENSE", FIXTURE_LICENCE), ("licenses/NOTICE", b"Noticed, 2026.\n")])
            passing_fake, passing_registry = scene()
            arm("a public push passes BSD-3-Clause, a listed NOTICE-absent Apache-2.0 archive, a dual permissive "
                "licence and an Apache-2.0 package carrying its NOTICE",
                lambda: push(passing_registry, package_guard(get_from(api), get_from(api), passing_registry, repository,
                                                             quiet.append),
                             passing, repository, checkout, {}, malf_dir, {}, root / "licence-pass-out", quiet.append), None)
            publish_index(passing_registry, root / "licence-pass-out", quiet.append)
            anonymous = Registry(passing_registry.base, passing_registry.name)
            audited: list[str] = []
            expect("licence-audit over the passing public store, anonymous: exit 0, every third-party record passed, the "
                   "recipe notice held",
                   (licence_audit(Reader(anonymous, root / "audit-cache", quiet.append), repository, malf_dir,
                                  audited.append),
                    sum(line.startswith("store_transport: AUDIT-PASS ") for line in audited),
                    audited[-1].split(" — ")[0], any(f"holds malf's recipe notice sha256-{notice}" in line
                                                      for line in audited)),
                   (0, 4, "store_transport: AUDIT coderoasted/coderoast-ipc: PASSED 4 record(s)", True))
            audited.clear()
            refused_audit = licence_audit(Reader(Registry(private_registry.base, private_registry.name),
                                                 root / "audit-cache-2", quiet.append),
                                          repository, malf_dir, audited.append)
            expect("licence-audit over a store holding the refused records: exit 1, the head, every finding, the remedy, "
                   "and the missing recipe notice named",
                   (refused_audit, sum(line.startswith("store_transport: AUDIT — ") for line in audited),
                    [line for _label, _plant, line in breaking if not any(line in said for said in audited)],
                    audited[-1], any("does not hold malf's recipe notice" in line for line in audited)),
                   (1, 1, [], LICENCE_REMEDY, True))
            saved = {name: os.environ.get(name) for name in (REGISTRY_ENV, "GITHUB_TOKEN", TRANSPORT_CACHE_ENV)}
            for name in saved:
                os.environ.pop(name, None)
            os.environ[REGISTRY_ENV] = passing_registry.base
            printed = io.StringIO()
            try:
                with contextlib.redirect_stdout(printed):
                    code = main(["licence-audit", "--repository", repository])
            finally:
                for name, value in saved.items():
                    os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
            expect("the licence-audit verb reads the store anonymously and prints PASSED",
                   (code, "PASSED 4 record(s)" in printed.getvalue()), (0, True))

            # -- the prune (DN-142.D32 (2)): a published candidate, then an unindexed record and its object.
            fake, kept_registry = scene()
            push(kept_registry, package_guard(get_from(api), get_from(api), kept_registry, repository, quiet.append),
                 store, repository, checkout, {}, malf_dir, {}, root / "prune-out", quiet.append)
            publish_index(kept_registry, root / "prune-out", quiet.append)
            stale_store = root / "stale-store"
            stale_object = _put_object(stale_store, b"a superseded transport\n")
            stale = _put_record(stale_store, {"step": {"kind": "conan-create", "package": "ipc_core", "form": "old"}},
                                {"package": {"alias": "ipc_core/0", "content": "f" * 64, "transport": stale_object}})
            push_record(kept_registry, stale_store, stale_store / "records" / f"{stale}.json", Pushed(), quiet.append)
            computed = utc_seconds("2026-10-30T12:00:00Z")
            young = {fake.tags[record_tag(stale)][7:]}

            def store_versions() -> list[Version]:
                return [Version(number, digest[7:], tuple(sorted(tag for tag, held in fake.tags.items() if held == digest)),
                                computed - (60 if digest[7:] in young else 7 * 3600))
                        for number, digest in enumerate(sorted(fake.manifests), start=1)
                        if digest[7:] != sha256(store_replica.EMPTY_BYTES)]

            def remove(answer: int) -> Callable[[int], int]:
                def delete(version_id: int) -> int:
                    if answer == 204:
                        digest = sorted(fake.manifests)[version_id - 1]
                        for tag in [tag for tag, held in fake.tags.items() if held == digest]:
                            del fake.tags[tag]
                    return answer
                return delete

            def keep_set(name: str, document: object) -> Path:
                path = root / name
                path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
                return path

            saved = {name: os.environ.get(name) for name in (REGISTRY_ENV, "GITHUB_TOKEN", TRANSPORT_CACHE_ENV)}
            os.environ.update({REGISTRY_ENV: kept_registry.base, "GITHUB_TOKEN": "token",
                               TRANSPORT_CACHE_ENV: str(root / "index-cache")})
            printed = io.StringIO()
            try:
                with contextlib.redirect_stdout(printed):
                    code = main(["index", repository, key])
                    absent = main(["index", repository, "8" * 64])
            finally:
                for name, value in saved.items():
                    os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
            expect("index prints the candidate's index whole, checked against its key, and exits 3 on an absent one",
                   (code, json.loads(printed.getvalue().splitlines()[0])["candidate"], absent), (0, key, ABSENT))
            kept_file = keep_set("keep.json", {"schema": KEEP_SET_SCHEMA, "computed_at": "2026-10-30T12:00:00Z",
                                               "repositories": {"coderoast-ipc": [key]}})
            prune_reader = Reader(kept_registry, root / "prune-cache", quiet.append)
            lines: list[str] = []
            before = dict(fake.tags)
            expect("prune, dry run: exit 0, the store unchanged, the unindexed object WOULD-DELETE and the young "
                   "record kept by the grace bound",
                   (prune(prune_reader, repository, kept_file, store_versions, remove(204), False, lines.append),
                    fake.tags == before, [line.split(" sha256:")[0] for line in lines if "WOULD-DELETE" in line],
                    [line for line in lines if "WOULD-DELETE" in line and object_tag(stale_object) not in line]),
                   (0, True, ["store_transport: WOULD-DELETE store-coderoast-ipc version "
                              f"{sorted(fake.manifests).index(fake.tags[object_tag(stale_object)]) + 1}"], []))
            young.clear()
            lines.clear()
            closure_tags = kept_closure(prune_reader, "coderoasted/coderoast-ipc", [key])[1]
            expect("prune --apply: every version outside the kept closure deleted, the candidate's closure whole",
                   (prune(prune_reader, repository, kept_file, store_versions, remove(204), True, lines.append),
                    sorted(tag for tag in before if tag not in fake.tags),
                    sorted(closure_tags - set(fake.tags)), candidate_tag(key) in fake.tags,
                    sorted(tag.split("-")[0] for tag in closure_tags)),
                   (0, sorted([record_tag(stale), object_tag(stale_object)]), [], True,
                    ["candidate"] + ["record"] * len(json.loads((root / "prune-out" / "index.json").read_bytes())["records"])
                    + ["sha256"] * 3))
            arm("after the prune the kept candidate restores whole from the store",
                lambda: restore(Reader(kept_registry, root / "prune-cache-2", quiet.append), root / "restored",
                                "coderoasted/coderoast-ipc", key, None, quiet.append), None)
            push_record(kept_registry, stale_store, stale_store / "records" / f"{stale}.json", Pushed(), quiet.append)
            expect("RED: a delete the API refuses exits 1 and names the package setting",
                   (prune(prune_reader, repository, kept_file, store_versions, remove(403), True, lines.append),
                    any("DELETE-REFUSED" in line and "Manage Actions access" in line for line in lines)), (1, True))
            for label, document, refusal in (
                    ("an empty keep-set", {"schema": KEEP_SET_SCHEMA, "computed_at": "2026-10-30T12:00:00Z",
                                           "repositories": {"coderoast-ipc": []}}, "names no candidate"),
                    ("a keep-set naming only another repository",
                     {"schema": KEEP_SET_SCHEMA, "computed_at": "2026-10-30T12:00:00Z",
                      "repositories": {"insight-canon": [key]}}, "names no candidate"),
                    ("an unparseable keep-set", "{not json", "unreadable"),
                    ("a keep-set of another schema", {"schema": "x/1", "repositories": {"coderoast-ipc": [key]}},
                     "is not a pharos-keep-set/1"),
                    ("a keep-set with no UTC computation time",
                     {"schema": KEEP_SET_SCHEMA, "computed_at": "yesterday", "repositories": {"coderoast-ipc": [key]}},
                     "UTC ISO-8601"),
                    ("a keep-set keeping a candidate the store does not hold",
                     {"schema": KEEP_SET_SCHEMA, "computed_at": "2026-10-30T12:00:00Z",
                      "repositories": {"coderoast-ipc": [key, "9" * 64]}}, "the store answers")):
                tags_before = dict(fake.tags)
                arm(f"RED: prune refuses {label} and deletes nothing",
                    lambda document=document, label=label: prune(
                        prune_reader, repository, keep_set(f"keep-{len(label)}.json", document), store_versions,
                        remove(204), True, quiet.append), refusal)
                expect(f"{label}: the store is unchanged", fake.tags == tags_before, True)
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
    return Verifier(f"{owner}/{name}", args.signer_workflow, args.signer_digest, live_gh, live_tree_of(get),
                    live_on_main(get))


def upstream_recipes(store: Path, index: dict) -> dict[str, str]:
    """{package: transport path} of an upstream candidate's ship-form creates, read from `store`, where its
    records were restored: each transport carries the package's recipe export and export sources."""
    recipes: dict[str, str] = {}
    for entry in index["records"]:
        record = json.loads((store / "records" / f"{entry['key']}.json").read_text(encoding="utf-8"))
        step, output = record["inputs"]["step"], (record.get("outputs") or {}).get("package") or {}
        if step.get("kind") == "conan-create" and step.get("form") == "ship" and output.get(artefact_store.TRANSPORT_FIELD):
            recipes[step["package"]] = str(store / "objects" / output[artefact_store.TRANSPORT_FIELD])
    if not recipes:
        raise Refused(f"candidate-{index.get('candidate')} of {index.get('repository')} names no ship-form create "
                      f"with a transport: a private upstream's recipes have nowhere else to come from")
    return recipes


def sources_verb(args: argparse.Namespace, token: str | None, say: Callable[[str], None]) -> int:
    """`sources`: every source-only upstream of the document cloned at its commit into <into>/<repo>; <out>
    holds {owner/repo: {commit, tree, root}}. A commit names its tree, so git's own addressing is the trust."""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    clone = live_clone(server, token)
    resolved: dict[str, dict] = {}
    for repository, commit in sorted(read_upstream(args.sources).items()):
        full = "/".join(full_name(repository)).lower()
        if not isinstance(commit, str) or not HEX40.match(commit):
            raise Refused(f"source {repository}: {commit!r} is not a commit (40 hex)")
        root = Path(args.into) / full_name(repository)[1]
        resolved[full] = {"commit": commit, "tree": clone(full, commit, root), "root": str(root)}
        say(f"store_transport: SOURCE {full} at commit {commit}")
    Path(args.out).write_text(json.dumps(resolved, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return 0


def upstream_is_private(get: Callable[[str], "tuple[int, dict]"], repository: str) -> bool:
    """Whether an upstream is private, from this job's token: a public repository is visible to every token, and
    another repository's GITHUB_TOKEN gets 404 for a private one (measured 2026-10-29, insight-eidos's candidate
    over logcraft), so an unseen repository is private. One that does not exist is no trusted upstream either:
    its store read is then absent or refused, never trusted."""
    status, seen = get(f"repos/{repository}")
    if status == 404:
        return True
    if status != 200 or seen.get("visibility") not in store_replica.VISIBILITIES:
        raise Refused(f"the API answered HTTP {status} and visibility {seen.get('visibility')!r} for the upstream "
                      f"{repository}")
    return seen["visibility"] == "private"


def upstream_verb(args: argparse.Namespace, get: Callable[[str], "tuple[int, dict]"], token: str | None,
                  say: Callable[[str], None]) -> int:
    """`upstream`: every upstream candidate of the document, through its own store's trust (a public one's
    attestation, a private one's privacy and the run's token), written to <out> as `{owner/repo: {key, commit,
    tree[, root]}}`; with --store its records restored there, with --sources its source cloned there."""
    cache = transport_cache()

    def private(repository: str) -> bool:
        return upstream_is_private(get, repository)

    def read_index(repository: str, key: str) -> dict:
        hidden = private(repository)
        if not hidden and not args.signer_workflow:
            raise Refused(f"the public upstream {repository} is read through its attestations: name --signer-workflow")
        verifier = None if hidden else Verifier(repository, args.signer_workflow, None, live_gh, live_tree_of(get),
                                                live_on_main(get))
        reader = Reader(_registry(repository, token), cache, say)
        if args.store:
            return restore(reader, Path(args.store), repository, key, verifier, say)
        return trusted_index(reader, repository, key, verifier)

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    seen: dict[str, bool] = {}

    def hidden(repository: str) -> bool:
        if repository not in seen:
            seen[repository] = private(repository)
        return seen[repository]

    resolved = resolve_upstreams(read_upstream(args.upstream), read_index, live_clone(server, token),
                                 Path(args.sources) if args.sources else None, say, hidden)
    for repository, entry in resolved.items():
        # note: only a job that builds (--sources) needs a private upstream's recipes; a verification (present) needs none
        if hidden(repository) and args.sources:
            if not args.store:
                raise Refused(f"the private upstream {repository} reaches its recipes through its records' transports, "
                              f"so its records are restored: name --store")
            entry["recipes"] = upstream_recipes(Path(args.store), read_index(repository, entry["key"]))
    Path(args.out).write_text(json.dumps(resolved, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    prune_named_cache(say)
    return 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    parser = argparse.ArgumentParser(prog="store_transport.py", add_help=False)
    verbs = parser.add_subparsers(dest="verb", required=True)
    verb = verbs.add_parser("candidate-key")
    verb.add_argument("repository"), verb.add_argument("checkout"), verb.add_argument("upstream", nargs="?")
    verb.add_argument("--sources")
    verb = verbs.add_parser("push")
    for name in ("repository", "checkout", "store", "out"):
        verb.add_argument(name)
    verb.add_argument("--upstream"), verb.add_argument("--extra"), verb.add_argument("--sources")
    verb = verbs.add_parser("publish-index")
    verb.add_argument("repository"), verb.add_argument("out")
    verb = verbs.add_parser("sources")
    verb.add_argument("sources"), verb.add_argument("out"), verb.add_argument("into")
    verb = verbs.add_parser("upstream")
    verb.add_argument("upstream"), verb.add_argument("out")
    verb.add_argument("--sources"), verb.add_argument("--store"), verb.add_argument("--signer-workflow")
    verb = verbs.add_parser("package-check")
    verb.add_argument("repository")
    verb = verbs.add_parser("index")
    verb.add_argument("repository"), verb.add_argument("key")
    verb = verbs.add_parser("licence-audit")
    verb.add_argument("--repository", required=True)
    verb = verbs.add_parser("prune")
    verb.add_argument("repository"), verb.add_argument("keep_set"), verb.add_argument("--apply", action="store_true")
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
            print(candidate_key(args.repository, checkout_identity(Path(args.checkout))[1], read_upstream(args.upstream),
                                read_upstream(args.sources)))
            return 0
        if args.verb == "upstream":
            return upstream_verb(args, get, token, say)
        if args.verb == "sources":
            return sources_verb(args, token, say)
        if args.verb == "package-check":
            package_check(get, peek, args.repository, say)
            return 0
        if args.verb == "licence-audit":
            # note: anonymous whatever GITHUB_TOKEN holds: the audit reads what the public reads
            anonymous = _registry(args.repository, None)
            if os.environ.get(TRANSPORT_CACHE_ENV):
                code = licence_audit(Reader(anonymous, transport_cache(), say), args.repository,
                                     Path(__file__).resolve().parent, say)
                prune_named_cache(say)
                return code
            with tempfile.TemporaryDirectory(prefix="store_transport.audit.") as scratch:
                return licence_audit(Reader(anonymous, Path(scratch), lambda _line: None), args.repository,
                                     Path(__file__).resolve().parent, say)
        if args.verb in ("push", "publish-index", "prune") and not token:
            raise Refused("GITHUB_TOKEN is unset: a repository writes and prunes its store with its own token")
        registry = _registry(args.repository, token)
        if args.verb == "index":
            if not HEX64.match(args.key):
                raise Refused(f"{args.key!r} is not a candidate key (64 hex)")
            owner, name = full_name(args.repository)
            # note: no attestation is read: index_defects binds the printed upstream to the key by its hash
            print(canonical(trusted_index(Reader(registry, transport_cache(), lambda _line: None), f"{owner}/{name}",
                                          args.key, None)).decode("utf-8"))
            prune_named_cache(lambda _line: None)
            return 0
        if args.verb == "prune":
            owner, name = full_name(args.repository)
            return prune(Reader(registry, transport_cache(), say), args.repository, Path(args.keep_set),
                         live_versions(api, token, owner, package_name(name), get),
                         live_delete(api, token, owner, package_name(name), get), args.apply, say)
        if args.verb == "push":
            extra = json.loads(Path(args.extra).read_text(encoding="utf-8")) if args.extra else {}
            return push(registry, package_guard(get, peek, registry, args.repository, say), Path(args.store),
                        args.repository, Path(args.checkout), read_upstream(args.upstream),
                        Path(__file__).resolve().parent, extra, Path(args.out), say, read_upstream(args.sources))
        if args.verb == "publish-index":
            return publish_index(registry, Path(args.out), say)
        owner, name = full_name(args.repository)
        if args.verb == "verify":
            if args.private:
                raise Refused("a private store carries no attestation to verify (item 6)")
            reader = Reader(registry, transport_cache(), say)
            facts = _verifier(args, get).attested(reader.manifest(reader.tagged(args.tag)))
            for fact in facts:
                say(f"store_transport: VERIFIED {owner}/{name}:{args.tag} source {fact.source_commit} "
                    f"signer {fact.signer_digest}")
            return 0
        verifier = _verifier(args, get)
        if args.verb == "candidate-present":
            if not HEX64.match(args.key):
                raise Refused(f"{args.key!r} is not a candidate key (64 hex)")
            reader = Reader(registry, transport_cache(), say)
            trusted_index(reader, f"{owner}/{name}", args.key, verifier)
            say(f"store_transport: PRESENT candidate-{args.key} of {owner}/{name}")
            prune_named_cache(say)
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
