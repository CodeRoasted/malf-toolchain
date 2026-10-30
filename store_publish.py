#!/usr/bin/env python3
"""A release job's whole act on the store: read this repository's CANDIDATE from its own store, verify, stage or push.

DN-142.D33 (3): a release publishes from its repository's candidate and builds nothing (DN-142.D9).
`coderoast-publish.yml` runs this after `attest` proved the tagged commit is the commit the cut's record
names for this repository and handed on that candidate's KEY:
1. the candidate is read from `ghcr.io/<owner>/store-<repository>` at `candidate-<key>` through
   store_transport's ONE trusted reader (`restore`): a public store through its GitHub artifact
   attestations, signed by malf's `coderoast-candidate.yml` at this workflow's own malf commit (the commit
   the tagged tree pins); a private one through its privacy and the job's own token. Every byte is judged
   by its digest;
2. the tag's tree, read from the API, must be the tree the candidate's index was built from;
3. the repository's packages.yml at the commit the index records (read through the API: nothing is
   checked out) DERIVES the Release's rows, the rules `scripts/pharos/publication.py` applies to the whole
   reach (DN-142.D24 (2), DN-142.D25): a `release: true` package's transport `<package>-<version>.tgz`,
   an `assets` site of a `published` executable (the entry's own bytes, with a `.sha256` written from its
   digest, and every verdict the site names, e.g. `consumer`, a passing record of the candidate), an
   `objects:` site (a member of the record that produced it: a tracked step or a verdict that passed; a
   `bench/` member alone is the CUT's, measured on the release seat and named by digest in the record
   attest attested), an `ingest:` site (the candidate's own ingestion of a job of its run: a golden, the
   Windows engine); and every image `images:` declares;
4. EVERY row and image must resolve in the candidate, or nothing is staged and the refusal names each one:
   a row the candidate holds no producer of (a hosted output, a verdict the candidate never ran) is never
   dropped silently, and never fetched from anywhere else;
5. the files are staged with the Release's PUBLICATION DOCUMENT, `<repository>-<version>.publication.json`
   (the candidate key, commit and tree, every row's object digest and record key, every image's manifest
   and executables), and each foreign Release the repository binds (DN-142.D25 (2)) with its own,
   `<destination>-<tag>.publication.json`, its rows projected to {name, kind, object, record, verdicts};
6. after the upload, the Release's asset names must equal the staged files, both ways (check-release).
`image` (`coderoast-publish-image.yml`) reads the same candidate, takes the image's ONE record (built in the
candidate's store job by store_image.py `candidate`), judges its archive (one linux/amd64 image whose
manifest digest is the record's), pushes every blob and the manifest verbatim to ghcr.io/<owner>/<image>,
so the registry's digest is the one the candidate stored, and tags it, never moving a tag that already
names another digest. `deployment` reads the image's identity from the Release's publication document AND
the candidate it names, and refuses unless the two agree.
`rehearse` is step 0's rehearsal of the PREVIOUS publication, from the writer's signed slices
(`slice_stage`): it retires with step 0's store (DN-142.D33 slice (f)), and no release job runs it.
Nothing reaches the output directory or the registry unverified, and nothing here compiles, tests or packs.

    store_publish.py stage <owner/repository> <tag> <candidate key> <cut-record.json> <its sha256> <bench dir> <out dir> (--signer-workflow W --signer-digest D | --private)
        a `bench/<member>` row from <bench dir>/<package>/<member>, the cut's artifact `cut-bench-<repository>`,
        by the digest the cut record attest attested names (DN-142.D33, slice (a)'s ruling (1)), every other row
        from the candidate; <out dir>/release/ (this Release's files) and <out dir>/destinations/<destination>/ with
        <out dir>/destinations/destinations.tsv (`<destination> <tag>` per line). Token: GITHUB_TOKEN (the
        repository's own: its store package and its API)
    store_publish.py cut-tool <owner/repository> <candidate key> <package> <entry> <out file> (--signer-workflow W --signer-digest D | --private)
        a CUT TOOL (DN-142.D33, option (i)): the `run-from-cache` entry of the package's ship-form create in the
        candidate, read through the same trusted reader, extracted to <out file> by its digest and made executable,
        so the cut runs the bytes the candidate bound to its source verdict and builds nothing. Token: GITHUB_TOKEN
        when the store is private (a read:packages token reaches another repository's private store)
    store_publish.py check-release <out dir> <release.json>
        the reading after the publish (DN-142.D24 (3)): the Release's asset names, from
        `gh release view --json assets`, equal the staged files, both ways
    store_publish.py stale-assets <out dir> <release.json>
        before a re-publication over an existing Release (a force-moved tag re-fires it): one line per
        asset name the Release holds that the staged files do not, for the job to remove
    store_publish.py check-destination <out dir>/<destination> <release.json> <releases.json> <tag>
        check-release, and `gh release list --json tagName,isLatest` lists <tag> once, not Latest
    store_publish.py image <owner/repository> <tag> <candidate key> <image> (--signer-workflow W --signer-digest D | --private)
        Token: GITHUB_TOKEN (`packages: write` on the image's package); prints the pushed
        `ghcr.io/<owner>/<image>@sha256:…`
    store_publish.py deployment <publication.json> <owner/repository> <image> <version> (--signer-workflow W --signer-digest D | --private)
        what a deploy runs: `image <manifest hex>`, then one `executable <entry> <sha256> <build-id>` line
        per executable the image carries, once the document and the candidate it names agree
    store_publish.py rehearse <repository> <step-0 record digest> <allowed signers> <candidate> <out dir>
        step 0's rehearsal (ROADMAP N393), over the writer's slices: `slice_stage` and
        `slice_stage_destinations` into <out dir>, ONE draft pre-release named by the candidate, read back
        by check-release, then deleted. Tokens, each from a FILE: MALF_STORE_TOKEN_FILE and
        MALF_STORE_REHEARSAL_TOKEN_FILE
    store_publish.py selftest
        both acts against in-process registries: the candidate pushed by store_transport's own writer, the
        slices seeded by the step-0 writer's functions

Exit 0 staged or pushed, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_image  # noqa: E402 — a sibling module, located from this file
import store_manifest  # noqa: E402 — a sibling module, located from this file
import store_replica  # noqa: E402 — a sibling module, located from this file
import store_transport  # noqa: E402 — a sibling module, located from this file

TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
# A step-0 candidate's name (DN-108.D36): the rehearsal's draft is named by it, never by a release tag.
CANDIDATE = re.compile(r"^candidate/[0-9]+\.[0-9]+\.[0-9]+/[0-9a-f]{16}$")
# The token file holding `contents: write` on each releasing repository, in the writer account's home
# beside MALF_STORE_TOKEN_FILE (ROADMAP N393): it creates and deletes the rehearsal's draft, and nothing else.
REHEARSAL_TOKEN_ENV = "MALF_STORE_REHEARSAL_TOKEN_FILE"
# One page of the Releases listing: a page this full cannot prove a candidate's draft is absent.
RELEASES_PAGE = 100
# The listing trails a create: measured on CodeRoasted/logcraft 2026-10-26, a draft created by an exit-0
# `gh release create` was absent from the listing read 0.7 s later and present at 2.5 s. The rehearsal
# reads the listing up to REHEARSAL_READS times, REHEARSAL_READ_SECONDS apart, before it refuses.
REHEARSAL_READS = 30
REHEARSAL_READ_SECONDS = 2
GH_TIMEOUT_SECONDS = 900
# `gh` as the rehearsal runs it: argv after `gh` -> (exit code, stdout, stderr).
Gh = Callable[[list[str]], "tuple[int, str, str]"]


class Refused(Exception):
    """A check failed; the message names what differed."""


def verified_slice(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
                   allowed: Path) -> tuple[dict, list[dict], bytes, bytes]:
    """(the slice, its assets, its bytes, its signature), once the signature verifies for this
    repository under the writer's key and the slice names the attested record and this
    repository's visibility now."""
    if not store_replica.DIGEST_HEX.match(record_hex):
        raise Refused(f"{record_hex!r} is not a step-0 record digest (64 hex)")
    try:
        slice_bytes, signature = registry.get_slice(record_hex)
    except store_replica.Refused as refused:
        raise Refused(str(refused)) from refused
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "slice.sig").write_bytes(signature)
        try:
            assets = store_manifest.verify(slice_bytes, Path(scratch) / "slice.sig", allowed, repository)
        except store_manifest.Refused as refused:
            raise Refused(str(refused)) from refused
    body = json.loads(slice_bytes)
    if body.get("step0_record") != record_hex:
        raise Refused(f"the slice names step-0 record {body.get('step0_record')}, and attest attested "
                      f"{record_hex}")
    if body.get("visibility") != visibility:
        raise Refused(f"the slice was built for a {body.get('visibility')} {repository}, and the "
                      f"repository is {visibility} now")
    return body, assets, slice_bytes, signature


def fetched(registry: store_replica.Registry, asset: dict, work: Path) -> Path:
    """The asset's object, fetched by its digest and verified, under `work`."""
    path = work / asset["object"]
    if not path.exists():
        try:
            registry.get_layer(asset["object"], path)
        except store_replica.Refused as refused:
            raise Refused(f"{asset['name']}: {refused}") from refused
    return path


def placed(registry: store_replica.Registry, asset: dict, work: Path, out: Path) -> list[str]:
    """One Release row's files written into `out` from its verified object: a transport and an
    object as they are, an entry executable with its `.sha256` written FROM its digest."""
    name = asset["name"]
    data = fetched(registry, asset, work).read_bytes()
    (out / name).write_bytes(data)
    if asset["kind"] != "entry":
        return [name]
    (out / name).chmod(0o755)
    (out / f"{name}.sha256").write_text(f"{asset['object']}  {name}\n")
    return [name, f"{name}.sha256"]


def slice_stage(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
          allowed: Path, out: Path, say: Callable[[str], None]) -> list[str]:
    """Stage every Release asset of this repository's slice into `out`; the staged file names."""
    body, assets, slice_bytes, signature = verified_slice(registry, repository, visibility, record_hex, allowed)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        staged: list[str] = []
        for asset in assets:
            name = asset["name"]
            if asset["kind"] == store_manifest.IMAGE_KIND:
                say(f"store_publish: IMAGE {name} is published by `store_publish.py image`, never as a Release file")
                continue
            if asset["destination"] != repository:
                say(f"store_publish: FOREIGN {name} belongs on {asset['destination']}'s "
                    f"{body['destinations'][asset['destination']]['tag']} Release, never on {repository}'s "
                    f"(DN-142.D25 (2))")
                continue
            if (out / name).exists() or name in staged:
                raise Refused(f"{name} is staged twice")
            staged += placed(registry, asset, work, out)
            say(f"store_publish: STAGED {name} ({asset['kind']}, object {asset['object']})")
        evidence = f"{repository}-{body.get('version')}.slice.json"
        (out / evidence).write_bytes(slice_bytes)
        (out / f"{evidence}.sig").write_bytes(signature)
        staged += [evidence, f"{evidence}.sig"]
    return staged


def slice_stage_destinations(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
                       allowed: Path, out: Path, say: Callable[[str], None]) -> list[tuple[str, str, str, list[str]]]:
    """Stage every FOREIGN Release the owner slice binds under `out/<destination>/`, read from the
    OWNER's store package (DN-142.D25 (2)); (destination, tag, visibility, staged files) each."""
    body, _assets, _slice, _signature = verified_slice(registry, repository, visibility, record_hex, allowed)
    out.mkdir(parents=True, exist_ok=True)
    done = []
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        for destination, binding in sorted((body.get("destinations") or {}).items()):
            held = {}
            for field in ("slice", "signature"):
                held[field] = work / f"{destination}.{field}"
                try:
                    registry.get_layer(binding[field], held[field])
                except store_replica.Refused as refused:
                    raise Refused(f"the destination slice of {destination}: {refused}") from refused
            try:
                rows = store_manifest.verify_destination(held["slice"].read_bytes(), held["signature"], allowed,
                                                         body, destination)
            except store_manifest.Refused as refused:
                raise Refused(f"{destination}: {refused}") from refused
            target = out / destination
            target.mkdir()
            staged: list[str] = []
            for row in rows:
                if row["name"] in staged:
                    raise Refused(f"{destination}: {row['name']} is staged twice")
                staged += placed(registry, row, work, target)
                say(f"store_publish: STAGED {destination}/{row['name']} ({row['kind']}, object {row['object']})")
            evidence = f"{destination}-{binding['tag']}.slice.json"
            (target / evidence).write_bytes(held["slice"].read_bytes())
            (target / f"{evidence}.sig").write_bytes(held["signature"].read_bytes())
            staged += [evidence, f"{evidence}.sig"]
            done.append((destination, binding["tag"], binding["visibility"], staged))
    (out / "destinations.tsv").write_text("".join(f"{destination}\t{tag}\t{seen}\n"
                                                  for destination, tag, seen, _staged in done))
    return done


# ── THE RELEASE FROM ITS REPOSITORY'S CANDIDATE (DN-142.D33 (3)) ─────────────────────────────────────


PUBLICATION_FORMAT = "coderoast.publication/1"
DESTINATION_PUBLICATION_FORMAT = "coderoast.destination-publication/1"
PACKAGES_FILE = "packages.yml"
RUN_FROM_CACHE = "run-from-cache"
VERSION_FIELD = "{version}"
TRANSPORT_KIND, ENTRY_KIND, OBJECT_KIND = store_manifest.KINDS
SHIP_KIND, SHIP_FORM = "conan-create", "ship"
ENTRY_SITE_KEYS = {"destination", "name", "verdicts"}
OBJECT_SITE_KEYS = {"destination", "name"}
INGEST_CLASSES = {"executable": ENTRY_KIND, "object": OBJECT_KIND}
# A GitHub API read: path -> (HTTP status, JSON body), as store_transport.api_get answers it.
Get = Callable[[str], "tuple[int, dict]"]


@dataclass
class Candidate:
    """One repository's candidate, restored from its own store through store_transport's trusted read: its
    index and every record the index pins, with the objects they name, under `store`."""

    repository: str
    key: str
    index: dict
    store: Path
    records: dict[str, dict]


BENCH_OUTPUT = "bench"
CUT_RECORD_SCHEMA = "pharos-cut-record/1"


@dataclass
class Measured:
    """The cut's benchmarks of one repository (DN-142.D33, slice (a)'s ruling (1)): measured on the release
    seat from this very candidate, named by digest in the cut's record `attest` attested, the JSONs in
    `folder/<package>/<member>`."""

    key: str
    packages: dict[str, dict]
    folder: Path


def cut_benchmarks(record_bytes: bytes, record_sha256: str, repository: str, folder: Path) -> Measured | None:
    """The record's benchmarks of `repository`, once the record's bytes are the ones `attest` attested; None when
    the record names none of it."""
    if hashlib.sha256(record_bytes).hexdigest() != record_sha256:
        raise Refused(f"the cut record read has sha256 {hashlib.sha256(record_bytes).hexdigest()}, and attest attested "
                      f"{record_sha256}")
    record = json.loads(record_bytes)
    if record.get("schema") != CUT_RECORD_SCHEMA or record.get("cuttable") is not True:
        raise Refused(f"the cut record is schema {record.get('schema')!r} cuttable {record.get('cuttable')!r}")
    measured = (record.get("benchmarks") or {}).get(repository)
    if measured is None:
        return None
    return Measured(str(measured.get("key")), dict(measured.get("packages") or {}), folder)


def read_candidate(reader: store_transport.Reader, store: Path, repository: str, key: str,
                   verifier: store_transport.Verifier | None, say: Callable[[str], None]) -> Candidate:
    """The candidate at candidate-<key> of `repository`, restored whole into `store`: a public store through
    its attestations (`verifier`), a private one through its privacy (None). Absent or untrusted is a refusal:
    a release never publishes what its own store cannot vouch for."""
    if not store_transport.HEX64.match(key):
        raise Refused(f"{key!r} is not a candidate key (64 hex)")
    try:
        index = store_transport.restore(reader, store, repository, key, verifier, say)
    except (store_transport.Absent, store_replica.Refused) as unread:
        raise Refused(f"candidate-{key} of {repository} could not be read as trusted: {unread}") from unread
    records = {entry["key"]: json.loads((store / "records" / f"{entry['key']}.json").read_text(encoding="utf-8"))
               for entry in index["records"]}
    return Candidate(repository, key, index, store, records)


def release_rows(declared: dict, owner: str, version: str) -> tuple[list[dict], dict[str, str], list[str]]:
    """(every Release row the repository's packages.yml declares, {foreign destination: its tag}, findings),
    derived as `scripts/pharos/publication.py` derives one owner's part of the cut's plan (DN-142.D24 (2),
    DN-142.D25): a `release: true` package's transport `<package>-<version>.tgz`, each `assets` site of a
    `published` executable an entry row, each `objects:` site an object row, each `ingest:` site a row of its
    declared class; a site's destination is the owner unless `destinations:` declares the foreign one."""
    findings: list[str] = []
    destinations: dict[str, str] = {}
    for destination, row in sorted((declared.get("destinations") or {}).items()):
        tag = row.get("tag") if isinstance(row, dict) and set(row) == {"tag"} else None
        rendered = tag.replace(VERSION_FIELD, version) if isinstance(tag, str) else ""
        if destination == owner or not store_manifest.TAG_NAME.match(rendered):
            findings.append(f"{PACKAGES_FILE} destinations {destination}: a foreign destination is {{tag: <name>}}, "
                            f"not {row!r}")
            continue
        destinations[destination] = rendered
    rows: list[dict] = []
    used: set[str] = set()

    def sited(at: str, sites: object, keys: set[str], base: dict) -> None:
        if not isinstance(sites, list) or not sites:
            findings.append(f"{at}: its `assets` sites are {sites!r}, not a non-empty list")
            return
        for site in sites:
            if not isinstance(site, dict) or set(site) - keys or not store_manifest.ASSET_NAME.match(str(site.get("name"))):
                findings.append(f"{at}: an assets site is {{{', '.join(sorted(keys))}}} with a file name, not {site!r}")
                continue
            destination = site.get("destination", owner)
            if destination != owner and destination not in destinations:
                findings.append(f"{at}: its destination {destination} is foreign, and {PACKAGES_FILE} declares no "
                                f"`destinations:` row for it (DN-142.D25 (2))")
                continue
            used.add(destination)
            rows.append({**base, "name": site["name"], "destination": destination,
                         "verdicts": sorted(site.get("verdicts") or [])})

    for package, row in sorted((declared.get("packages") or {}).items()):
        row = row or {}
        at = f"{PACKAGES_FILE} {package}"
        # note: a cut tool is stored with the candidate and never released (DN-142.D33), so a row it would derive is refused
        defects = artefact_store.cut_tool_defects(package, row)
        if defects:
            findings += [f"{PACKAGES_FILE} {defect}" for defect in defects]
            continue
        if row.get("release") not in (True, False):
            findings.append(f"{at}: release is {row.get('release')!r}, not true or false")
            continue
        if row["release"] is True:
            if not row.get("version"):
                findings.append(f"{at}: release: true and no version")
                continue
            rows.append({"name": f"{package}-{row['version']}.tgz", "kind": TRANSPORT_KIND, "package": package,
                         "destination": owner, "verdicts": []})
        for entry, kind in sorted((row.get("executables") or {}).items()):
            if kind == RUN_FROM_CACHE:
                continue
            sites = kind.get("published") if isinstance(kind, dict) and set(kind) == {"published"} else None
            if not isinstance(sites, dict) or set(sites) - {"assets", "image"}:
                findings.append(f"{at} {entry}: {kind!r} is neither `{RUN_FROM_CACHE}` nor the site form")
                continue
            if "assets" in sites:
                sited(f"{at} {entry}", sites["assets"], ENTRY_SITE_KEYS,
                      {"kind": ENTRY_KIND, "package": package, "entry": entry})
        for named, value in sorted((row.get("objects") or {}).items()):
            output, _slash, member = str(named).partition("/")
            sites = value.get("published", {}).get("assets") if isinstance(value, dict) and \
                isinstance(value.get("published"), dict) else None
            if not member or not isinstance(value, dict) or set(value) != {"published"}:
                findings.append(f"{at} objects {named}: an object is <output>/<member> {{published: {{assets}}}}, "
                                f"not {value!r}")
                continue
            sited(f"{at} objects {named}", sites, OBJECT_SITE_KEYS,
                  {"kind": OBJECT_KIND, "package": package, "output": output, "member": member})
    for workflow, row in sorted((declared.get("ingest") or {}).items()):
        classes = [klass for klass in INGEST_CLASSES if isinstance(row, dict) and klass in row]
        if len(classes) != 1:
            findings.append(f"{PACKAGES_FILE} ingest {workflow}: its member is not under exactly one class")
            continue
        if isinstance(row.get("published"), dict):
            sited(f"{PACKAGES_FILE} ingest {workflow}", row["published"].get("assets"), OBJECT_SITE_KEYS,
                  {"kind": INGEST_CLASSES[classes[0]], "ingest": workflow, "artifact": row.get("artifact"),
                   "member": row[classes[0]]})
    for destination in sorted(set(destinations) - used):
        findings.append(f"{PACKAGES_FILE} destinations {destination}: no site publishes to it, so the row is stale")
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        seen[(row["destination"], row["name"])] = seen.get((row["destination"], row["name"]), 0) + 1
        if row["kind"] == TRANSPORT_KIND and row["destination"] != owner:
            findings.append(f"{row['name']}: a transport carries its recipe's sources and reaches only its own "
                            f"repository's Release (N360 plan F9)")
    findings += [f"{destination} receives {name} from {count} rows: one (destination, name) pair has one row"
                 for (destination, name), count in sorted(seen.items()) if count > 1]
    return sorted(rows, key=lambda row: (row["destination"], row["name"])), destinations, findings


def _one(found: list[dict], what: str) -> dict:
    if len(found) != 1:
        raise Refused(f"the candidate holds {len(found)} {what}, not one")
    return found[0]


def ship_create(candidate: Candidate, package: str) -> dict:
    """The package's ONE ship-form create record in the candidate, its transport whole in the store."""
    record = _one([body for body in candidate.records.values() if body["inputs"]["step"].get("kind") == SHIP_KIND
                   and body["inputs"]["step"].get("form") == SHIP_FORM
                   and body["inputs"]["step"].get("package") == package], f"ship-form create record(s) of {package}")
    transport = str(record["outputs"]["package"].get(artefact_store.TRANSPORT_FIELD))
    path = candidate.store / "objects" / transport
    if not path.is_file() or store_manifest.sha256_file(path) != transport:
        raise Refused(f"{package}: the transport {transport} its create names is not whole in the restored store")
    return record


def cut_tool(candidate: Candidate, package: str, entry: str, target: Path) -> dict:
    """A CUT TOOL's executable (DN-142.D33, option (i)): the entry `entry` of `package`'s ONE ship-form create in
    the candidate, extracted to `target` by its digest and made executable; {package, entry, digest, record}.
    The create must class the entry `run-from-cache`: a cut tool runs from where the cut put it, with the
    profile's runpath, and is never in the published form. Nothing is built: the bytes are the ones the
    candidate bound to its source verdict."""
    record = ship_create(candidate, package)
    declared = record["inputs"]["step"].get("executables") or {}
    if declared.get(entry) != RUN_FROM_CACHE:
        raise Refused(f"{package}'s ship-form create classes {entry} {declared.get(entry)!r}, not `{RUN_FROM_CACHE}`: a "
                      f"cut tool is an executable the cut runs from its restored bytes (DN-142.D22, DN-142.D33)")
    output = record["outputs"]["package"]
    held = {path: digest for path, _mode, digest in output.get("entries") or []}
    if entry not in held:
        raise Refused(f"{package} holds no entry {entry}")
    try:
        store_manifest.extract(candidate.store / "objects" / output[artefact_store.TRANSPORT_FIELD], entry,
                               held[entry], target)
    except store_manifest.Refused as refused:
        raise Refused(str(refused)) from refused
    target.chmod(0o755)
    return {"package": package, "entry": entry, "digest": held[entry], "record": record["key"]}


def judged_by(candidate: Candidate, package: str, verdict: str, digest: str) -> str:
    """The key of the candidate's `verdict` record over `package` that passed over `digest`."""
    found = []
    for body in candidate.records.values():
        step, output = body["inputs"]["step"], body["outputs"].get(verdict) or {}
        judged = output.get("judged") or body["inputs"].get("judged") or {}
        if step.get("kind") == "verdict" and (step.get("subject") or step.get("package")) == package \
                and output.get("verdict") == "pass" and digest in judged.values():
            found.append(body)
    return _one(found, f"passing `{verdict}` verdict(s) of {package} over {digest}")["key"]


def benchmarked(candidate: Candidate, row: dict, measured: Measured | None) -> dict:
    """A `bench/<member>` row, resolved in the CUT's benchmarks of this candidate, never in its store: a
    benchmark is measured on the release seat (DN-142.D33, slice (a)'s ruling (1)). The file must hash to the
    digest the attested record names."""
    if measured is None:
        raise Refused("it is a benchmark, and the cut's record names no benchmark of this repository: the cut "
                      "measures it on the release seat in its `bench-<repository>` job")
    if measured.key != candidate.key:
        raise Refused(f"the cut measured candidate-{measured.key}, and this release publishes candidate-{candidate.key}")
    entry = measured.packages.get(row["package"]) or {}
    digest = (entry.get("files") or {}).get(row["member"])
    if entry.get("verdict") != "pass" or not isinstance(digest, str):
        raise Refused(f"the cut's record names no passing benchmark {row['package']}/{row['member']}")
    path = measured.folder / row["package"] / row["member"]
    if not path.is_file() or store_manifest.sha256_file(path) != digest:
        raise Refused(f"the benchmark file {path} does not hash to the record's {digest}")
    return {"object": digest, "record": entry["record"], "verdicts": [entry["record"]], "path": path}


def resolved(candidate: Candidate, row: dict, scratch: Path, measured: Measured | None = None) -> dict:
    """One Release row resolved in the candidate: {object, record, verdicts, path} — the bytes at `path`,
    whole. Raises Refused naming the link the candidate does not hold."""
    if "ingest" in row:
        owner = candidate.repository.split("/", 1)[1]
        klass = {value: key for key, value in INGEST_CLASSES.items()}[row["kind"]]
        record = _one([body for body in candidate.records.values()
                       if body["inputs"]["step"] == {"kind": artefact_store.INGEST_KIND, "repository": owner,
                                                     "artefact": row["artifact"],
                                                     "workflow": f".github/workflows/{row['ingest']}", "class": klass}
                       and row["member"] in ((body["outputs"].get(artefact_store.INGEST_OUTPUT) or {})
                                             .get(artefact_store.OBJECTS_FIELD) or {})],
                      f"ingestion record(s) of {row['ingest']}'s {row['artifact']} holding {row['member']}")
        digest = record["outputs"][artefact_store.INGEST_OUTPUT][artefact_store.OBJECTS_FIELD][row["member"]]
        path = candidate.store / "objects" / str(digest)
        if not path.is_file() or store_manifest.sha256_file(path) != digest:
            raise Refused(f"its object {digest} is not whole in the restored store")
        return {"object": digest, "record": record["key"], "verdicts": [record["key"]], "path": path}
    if row["kind"] == TRANSPORT_KIND:
        record = ship_create(candidate, row["package"])
        digest = record["outputs"]["package"][artefact_store.TRANSPORT_FIELD]
        return {"object": digest, "record": record["key"], "verdicts": [], "path": candidate.store / "objects" / digest}
    if row["kind"] == ENTRY_KIND:
        record = ship_create(candidate, row["package"])
        output = record["outputs"]["package"]
        held = {path: digest for path, _mode, digest in output.get("entries") or []}
        if row["entry"] not in held:
            raise Refused(f"{row['package']} holds no entry {row['entry']}")
        target = scratch / held[row["entry"]]
        try:
            store_manifest.extract(candidate.store / "objects" / output[artefact_store.TRANSPORT_FIELD], row["entry"],
                                   held[row["entry"]], target)
        except store_manifest.Refused as refused:
            raise Refused(str(refused)) from refused
        verdicts = [judged_by(candidate, row["package"], verdict, held[row["entry"]]) for verdict in row["verdicts"]]
        return {"object": held[row["entry"]], "record": record["key"], "verdicts": verdicts, "path": target}
    if row["output"] == BENCH_OUTPUT:
        return benchmarked(candidate, row, measured)
    found = [body for body in candidate.records.values()
             if (body["inputs"]["step"].get("package") or body["inputs"]["step"].get("subject")) == row["package"]
             and row["member"] in ((body["outputs"].get(row["output"]) or {}).get(artefact_store.OBJECTS_FIELD) or {})]
    record = _one(found, f"record(s) of {row['package']}/{row['output']} holding {row['member']}")
    body = record["outputs"][row["output"]]
    verdicts = []
    if record["inputs"]["step"].get("kind") != artefact_store.TRACKED_KIND:
        if body.get("verdict") != "pass":
            raise Refused(f"its producer {row['package']}/{row['output']} judged {body.get('verdict')!r}, not pass")
        verdicts = [record["key"]]
    digest = body[artefact_store.OBJECTS_FIELD][row["member"]]
    path = candidate.store / "objects" / str(digest)
    if not path.is_file() or store_manifest.sha256_file(path) != digest:
        raise Refused(f"its object {digest} is not whole in the restored store")
    return {"object": digest, "record": record["key"], "verdicts": verdicts, "path": path}


def image_rows(candidate: Candidate, declared: dict) -> list[dict]:
    """Every image packages.yml declares, as the candidate's ONE image record of it (store_image.py
    `candidate`): it consumed exactly the executables the `image` sites name, at the digests of the
    candidate's own ship-form creates, and its archive is whole. Each with its executables' build-ids."""
    owner = candidate.repository.split("/", 1)[1]
    sited: dict[str, list[tuple[str, str]]] = {}
    for package, row in sorted((declared.get("packages") or {}).items()):
        for entry, kind in sorted(((row or {}).get("executables") or {}).items()):
            named = kind.get("published", {}).get("image") if isinstance(kind, dict) and \
                isinstance(kind.get("published"), dict) else None
            if named is not None:
                sited.setdefault(named, []).append((package, entry))
    images = []
    for image in sorted(declared.get("images") or {}):
        record = _one([body for body in candidate.records.values()
                       if body["inputs"]["step"].get("kind") == store_image.IMAGE_KIND
                       and body["inputs"]["step"].get("subject") == image
                       and body["inputs"]["step"].get("repository") == owner], f"image record(s) of {image}")
        consumed = {(row["package"], row["entry"]): row for row in record["inputs"].get("executables") or []}
        if set(consumed) != set(sited.get(image, [])):
            raise Refused(f"{image}: its image record consumed {sorted(consumed)}, and {PACKAGES_FILE} sites "
                          f"{sorted(sited.get(image, []))}")
        executables = []
        for (package, entry), row in sorted(consumed.items()):
            create = ship_create(candidate, package)
            output = create["outputs"]["package"]
            held = {path: digest for path, _mode, digest in output.get("entries") or []}
            build_id = (output.get("build_ids") or {}).get(entry)
            if row.get("digest") != held.get(entry) or row.get("record") != create["key"] or not build_id:
                raise Refused(f"{image}: it consumed {package} {entry} at {row.get('digest')} from {row.get('record')}, "
                              f"and the candidate's create {create['key']} holds {held.get(entry)} with build-id "
                              f"{build_id}")
            executables.append({"package": package, "entry": entry, "digest": held[entry], "build_id": build_id})
        output = record["outputs"][store_image.IMAGE_KIND]
        archive = candidate.store / "objects" / str(output.get("archive"))
        if not archive.is_file() or store_manifest.sha256_file(archive) != output.get("archive") \
                or not store_manifest.IMAGE_MANIFEST.match(str(output.get("manifest"))):
            raise Refused(f"{image}: its archive {output.get('archive')} is not whole, or it names no image manifest")
        images.append({"name": image, "manifest": output["manifest"], "archive": output["archive"],
                       "record": record["key"], "executables": executables})
    return images


def publication(candidate: Candidate, declared: dict, version: str, tag: str, scratch: Path,
                measured: Measured | None = None) -> tuple[dict, dict[str, str], list[tuple[dict, Path]]]:
    """(the Release's publication document, {destination: tag}, every row with its staged bytes), once EVERY
    declared row and image resolves in the candidate; else Refused naming each that does not, nothing staged."""
    owner = candidate.repository.split("/", 1)[1]
    rows, destinations, findings = release_rows(declared, owner, version)
    placed: list[tuple[dict, Path]] = []
    for row in rows:
        try:
            found = resolved(candidate, row, scratch, measured)
        except Refused as refused:
            findings.append(f"{row['destination']}/{row['name']}: {refused}")
            continue
        path = found.pop("path")
        placed.append(({key: value for key, value in row.items() if key not in ("output", "member", "ingest", "artifact")}
                       | found, path))
    try:
        images = image_rows(candidate, declared)
    except Refused as refused:
        findings.append(str(refused))
        images = []
    if findings:
        raise Refused(f"the candidate {candidate.key} of {candidate.repository} cannot publish {tag}: "
                      + "; ".join(findings))
    document = {"format": PUBLICATION_FORMAT, "repository": candidate.repository, "version": version, "tag": tag,
                "candidate": candidate.key, "commit": candidate.index.get("commit"),
                "tree": candidate.index.get("tree"), "malf": candidate.index.get("malf"),
                "assets": [row for row, _path in placed], "images": images,
                "destinations": {destination: {"tag": rendered} for destination, rendered in destinations.items()}}
    return document, destinations, placed


def _write(out: Path, name: str, data: bytes, entry: bool) -> list[str]:
    if (out / name).exists():
        raise Refused(f"{name} is staged twice")
    (out / name).write_bytes(data)
    if not entry:
        return [name]
    (out / name).chmod(0o755)
    (out / f"{name}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    return [name, f"{name}.sha256"]


def stage(candidate: Candidate, declared: dict, version: str, tag: str, out: Path,
          say: Callable[[str], None], measured: Measured | None = None
          ) -> tuple[list[str], list[tuple[str, str, list[str]]]]:
    """Every file of this repository's Release staged under `out/release`, and of each foreign Release it
    binds under `out/destinations/<destination>` with `out/destinations/destinations.tsv`
    (`<destination> <tag>` per line): each row's bytes from the candidate, an entry with its `.sha256`, and the
    publication document beside them, `<repository>-<version>.publication.json` (a destination's,
    `<destination>-<tag>.publication.json`, its rows projected). (own staged names, [(destination, tag, names)])."""
    if not TAG.match(tag) or tag != f"v{version}":
        raise Refused(f"{tag!r} is not the release tag v{version}")
    with tempfile.TemporaryDirectory(prefix="store_publish.") as scratch:
        document, destinations, placed = publication(candidate, declared, version, tag, Path(scratch), measured)
        owner = candidate.repository.split("/", 1)[1]
        release = out / "release"
        release.mkdir(parents=True)
        staged: list[str] = []
        for row, path in placed:
            if row["destination"] == owner:
                staged += _write(release, row["name"], path.read_bytes(), row["kind"] == ENTRY_KIND)
                say(f"store_publish: STAGED {row['name']} ({row['kind']}, object {row['object']}, record {row['record']})")
        done = []
        (out / "destinations").mkdir()
        for destination, rendered in sorted(destinations.items()):
            target = out / "destinations" / destination
            target.mkdir()
            names: list[str] = []
            for row, path in placed:
                if row["destination"] == destination:
                    names += _write(target, row["name"], path.read_bytes(), row["kind"] == ENTRY_KIND)
                    say(f"store_publish: STAGED {destination}/{row['name']} ({row['kind']}, object {row['object']})")
            projected = canonical({"format": DESTINATION_PUBLICATION_FORMAT, "owner": candidate.repository,
                                   "destination": destination, "tag": rendered, "version": version,
                                   "candidate": candidate.key,
                                   "assets": [{key: row[key] for key in ("name", "kind", "object", "record", "verdicts")}
                                              for row, _path in placed if row["destination"] == destination]})
            names += _write(target, f"{destination}-{rendered}.publication.json", projected, False)
            document["destinations"][destination]["publication"] = hashlib.sha256(projected).hexdigest()
            done.append((destination, rendered, names))
        (out / "destinations" / "destinations.tsv").write_text(
            "".join(f"{destination}\t{rendered}\n" for destination, rendered, _names in done))
        staged += _write(release, f"{owner}-{version}.publication.json", canonical(document), False)
    return staged, done


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"


def publish_image(candidate: Candidate, declared: dict, target: store_replica.Registry, image: str, tag: str,
                  say: Callable[[str], None]) -> str:
    """The candidate's image `image` pushed verbatim to `target` and tagged `tag`: every blob and the manifest
    exactly as the candidate stored them, so the registry's digest is the stored one; a tag already naming
    another digest is never moved. Its manifest digest."""
    if not TAG.match(tag):
        raise Refused(f"{tag!r} is not a release tag vMAJOR.MINOR.PATCH")
    found = [row for row in image_rows(candidate, declared) if row["name"] == image]
    if len(found) != 1:
        raise Refused(f"{candidate.repository}'s {PACKAGES_FILE} declares {len(found)} image(s) named {image}, not one")
    row = found[0]
    archive = candidate.store / "objects" / row["archive"]
    try:
        manifest, _entries = store_image.judge_archive(archive)
    except store_image.Refused as refused:
        raise Refused(f"{image}: {refused}") from refused
    if manifest != row["manifest"]:
        raise Refused(f"{image}: the archive's manifest is {manifest}, and its record names {row['manifest']}")
    held = target.manifest_digest(tag)
    if held is not None and held != manifest:
        raise Refused(f"{target.name}:{tag} already names {held}: a release tag is never moved")
    with tarfile.open(archive, "r:*") as bundle:
        blobs = {member.name.rsplit("/", 1)[-1]: bundle.extractfile(member).read()
                 for member in bundle.getmembers() if member.isfile() and member.name.startswith("blobs/sha256/")}
    body = blobs[manifest.removeprefix("sha256:")]
    named = json.loads(body)
    for digest in [named["config"]["digest"], *(layer["digest"] for layer in named["layers"])]:
        try:
            target.push_blob(digest.removeprefix("sha256:"), blobs[digest.removeprefix("sha256:")])
        except store_replica.Refused as refused:
            raise Refused(f"{image}: {refused}") from refused
    if held is None:
        try:
            target.put_manifest(tag, body)
        except store_replica.Refused as refused:
            raise Refused(f"{image}: {refused}") from refused
    if target.manifest_digest(tag) != manifest:
        raise Refused(f"{target.name}:{tag} did not read back as {manifest}")
    say(f"store_publish: IMAGE {target.name}@{manifest} tagged {tag} (record {row['record']})")
    return manifest


def deployment(document_bytes: bytes, candidate: Candidate, declared: dict, image: str, version: str) -> dict:
    """What a deploy of `image` at `version` runs: the image's manifest digest and each executable it carries
    with its digest and GNU build-id, read from the Release's publication document AND the candidate it names,
    restored from the store: two producers of one identity, which must agree (DN-142.D15)."""
    document = json.loads(document_bytes)
    if document.get("format") != PUBLICATION_FORMAT or document.get("repository") != candidate.repository \
            or document.get("version") != version or document.get("candidate") != candidate.key:
        raise Refused(f"the publication document names {document.get('format')} {document.get('repository')} "
                      f"{document.get('version')} candidate {document.get('candidate')}, and the deploy reads "
                      f"{candidate.repository} {version} candidate {candidate.key}")
    published = [row for row in document.get("images") or [] if row.get("name") == image]
    stored = [row for row in image_rows(candidate, declared) if row["name"] == image]
    if len(published) != 1 or len(stored) != 1:
        raise Refused(f"the publication document lists {len(published)} and the candidate {len(stored)} image(s) "
                      f"named {image}, not one each")
    if published[0] != stored[0]:
        raise Refused(f"{image}: the publication document says {published[0]}, and the candidate holds {stored[0]}")
    return {"manifest": stored[0]["manifest"], "executables": stored[0]["executables"]}


def declared_at(get: Get, repository: str, commit: str) -> dict:
    """packages.yml at `commit`, read through the API (a release job checks nothing out)."""
    import base64

    import yaml

    status, body = get(f"repos/{repository}/contents/{PACKAGES_FILE}?ref={commit}")
    if status != 200 or body.get("encoding") != "base64":
        raise Refused(f"the API answered HTTP {status} for {repository}'s {PACKAGES_FILE} at {commit}")
    return yaml.safe_load(base64.b64decode(body["content"])) or {}


def tagged_tree(get: Get, repository: str, tag: str) -> tuple[str, str]:
    """(commit, tree) the release tag names, read through the API."""
    status, body = get(f"repos/{repository}/commits/{tag}")
    tree = ((body.get("commit") or {}).get("tree") or {}).get("sha")
    if status != 200 or not store_transport.HEX40.match(str(body.get("sha"))) or not store_transport.HEX40.match(str(tree)):
        raise Refused(f"the API answered HTTP {status} for {repository}@{tag}, naming no commit and tree")
    return body["sha"], tree


def released_candidate(reader: store_transport.Reader, store: Path, repository: str, key: str, tag: str,
                       verifier: store_transport.Verifier | None, get: Get,
                       say: Callable[[str], None]) -> tuple[Candidate, dict]:
    """The candidate a release tag publishes from, once its index's tree is the tag's tree (the key binds a
    tree, and the cut's record its commit), and the packages.yml of the commit the index records."""
    candidate = read_candidate(reader, store, repository, key, verifier, say)
    commit, tree = tagged_tree(get, repository, tag)
    if candidate.index.get("tree") != tree:
        raise Refused(f"{tag} names {commit}, tree {tree}, and candidate-{key} was built from tree "
                      f"{candidate.index.get('tree')} (commit {candidate.index.get('commit')}): a release publishes the "
                      f"candidate of its own tree")
    return candidate, declared_at(get, repository, str(candidate.index.get("commit")))


def check_release(out: Path, release: dict) -> None:
    """The Release holds exactly the staged files: one more is an act no slice listed, one fewer a
    publication that did not happen."""
    attached = {asset["name"] for asset in release.get("assets", [])}
    staged = {path.name for path in out.iterdir() if path.is_file()}
    if attached != staged:
        raise Refused(f"the Release differs from the slice: attached and never staged "
                      f"{sorted(attached - staged)}, staged and not attached {sorted(staged - attached)}")


def stale_assets(out: Path, release: dict) -> list[str]:
    """The assets a Release already holds that the staged slice does not list, sorted. A force-moved
    tag re-fires its Release workflow over a Release a previous run published (ADR-2.D1: a re-cut moves
    the tag, never deletes it): the publication then uploads the staged files over it, and removes
    exactly these, so the reading after it (check-release) holds the Release to the new slice."""
    staged = {path.name for path in out.iterdir() if path.is_file()}
    return sorted({asset["name"] for asset in release.get("assets", [])} - staged)


def check_destination(out: Path, release: dict, releases: list[dict], tag: str) -> None:
    """The destination Release holds exactly its staged files, and it never wears the Latest badge:
    a foreign Release is an asset host, and its repository's own line owns the badge. `releases` is
    `gh release list --json tagName,isLatest` (`gh release view` carries no isLatest)."""
    check_release(out, release)
    listed = [row.get("isLatest") for row in releases if row.get("tagName") == tag]
    if listed != [False]:
        raise Refused(f"the destination Release {tag} is listed with isLatest {listed}, not once and False: a "
                      f"foreign Release is created --latest=false and never claims its repository's badge")


def _gh_json(gh: Gh, argv: list[str]) -> object:
    code, out, err = gh(argv)
    if code != 0:
        raise Refused(f"gh {' '.join(argv)} exited {code}: {(err or out).strip()[-400:]}")
    try:
        return json.loads(out)
    except ValueError as unread:
        raise Refused(f"gh {' '.join(argv)} printed no JSON: {unread}") from unread


def _named(gh: Gh, repo_path: str, candidate: str) -> list[dict]:
    """Every Release of the repository whose tag name is the candidate's, drafts included (the
    listing shows drafts to a token that can push, which `contents: write` is)."""
    listed = _gh_json(gh, ["api", f"{repo_path}/releases?per_page={RELEASES_PAGE}"])
    if not isinstance(listed, list):
        raise Refused(f"{repo_path}/releases is not a list")
    if len(listed) >= RELEASES_PAGE:
        raise Refused(f"{repo_path} lists {len(listed)} Releases or more on one page: the absence of a draft "
                      f"named {candidate} cannot be read from it")
    return [row for row in listed if row.get("tag_name") == candidate]


def _delete(gh: Gh, repo_path: str, release_id: object, sleep: Callable[[float], None]) -> None:
    """The draft deleted, then read by its id until the forge answers 404: the listing trails a delete
    as it trails a create, so absence is read where it is authoritative. A DELETE answered 404 is a
    draft already gone, which the reading confirms."""
    code, out, err = gh(["api", "-X", "DELETE", f"{repo_path}/releases/{release_id}"])
    if code != 0 and "HTTP 404" not in err:
        raise Refused(f"the draft {release_id} of {repo_path} could not be deleted (exit {code}): "
                      f"{(err or out).strip()[-400:]}")
    for _read in range(REHEARSAL_READS):
        code, out, err = gh(["api", f"{repo_path}/releases/{release_id}"])
        if code != 0:
            if "HTTP 404" in err:
                return
            raise Refused(f"the draft {release_id} of {repo_path} could not be read after its deletion "
                          f"(exit {code}): {(err or out).strip()[-400:]}")
        sleep(REHEARSAL_READ_SECONDS)
    raise Refused(f"the draft {release_id} of {repo_path} is still present after its deletion, read "
                  f"{REHEARSAL_READS} times over {REHEARSAL_READS * REHEARSAL_READ_SECONDS} s")


def _created(gh: Gh, repo_path: str, candidate: str, url: str, sleep: Callable[[float], None],
             gone: set[object]) -> dict:
    """The one draft the create made, found by the URL `gh release create` printed: the listing is read
    until it shows that draft, at most REHEARSAL_READS times. Any other draft of the candidate's name
    (a deleted leftover excepted) is refused at once; a draft never listed is refused, never passed."""
    for _read in range(REHEARSAL_READS):
        named = [row for row in _named(gh, repo_path, candidate) if row.get("draft") and row.get("id") not in gone]
        foreign = [row.get("id") for row in named if row.get("html_url") != url]
        if foreign or len(named) > 1:
            raise Refused(f"{len(named)} draft(s) named {candidate} after one create, not 1: {foreign or named} "
                          f"not the one the create printed ({url})")
        if named:
            return named[0]
        sleep(REHEARSAL_READ_SECONDS)
    raise Refused(f"0 draft(s) named {candidate} after one create, not 1: the draft the create printed ({url}) "
                  f"was never listed, read {REHEARSAL_READS} times over "
                  f"{REHEARSAL_READS * REHEARSAL_READ_SECONDS} s")


def rehearse(registry: store_replica.Registry, repository: str, record_hex: str, allowed: Path, candidate: str,
             out: Path, gh: Gh, say: Callable[[str], None], sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """THE PUBLISH REHEARSED AT THE CANDIDATE (ROADMAP N393, RUL-152): the repository's visibility read
    from GitHub (the second producer `stage` compares the slice against), its slice staged exactly as
    the tag's `stage` stages it, every destination slice it binds verified and staged, then ONE draft
    pre-release named by the candidate created with those files by `gh release create`, found by the
    URL it printed, read back by `check-release`, and deleted. A draft creates no tag and is visible
    only to accounts that can push. A leftover draft of an earlier rehearsal is deleted first; a
    PUBLISHED Release of that name is refused and never touched. The staged names, once the draft held
    exactly them and is gone."""
    if not CANDIDATE.match(candidate):
        raise Refused(f"{candidate!r} is not a candidate name (candidate/<V>/<16 hex>): a rehearsal never "
                      f"names a release tag")
    repo_path = f"repos/{store_replica.ORG}/{repository}"
    seen = _gh_json(gh, ["api", repo_path])
    visibility = seen.get("visibility") if isinstance(seen, dict) else None
    release_dir = out / "release"
    staged = slice_stage(registry, repository, str(visibility), record_hex, allowed, release_dir, say)
    slice_stage_destinations(registry, repository, str(visibility), record_hex, allowed, out / "destinations", say)
    found = _named(gh, repo_path, candidate)
    published = [row.get("id") for row in found if not row.get("draft")]
    if published:
        raise Refused(f"{repository} has a published Release named {candidate} (id {published}): a rehearsal "
                      f"never touches a published Release")
    gone: set[object] = set()
    for row in found:
        say(f"store_publish: deleting the leftover rehearsal draft {row.get('id')} of {repository}")
        _delete(gh, repo_path, row.get("id"), sleep)
        gone.add(row.get("id"))
    failure: Refused | None = None
    created: dict | None = None
    url = ""
    try:
        notes = (f"A step-0 rehearsal of the store publish for {candidate} (step-0 record sha256 {record_hex}): "
                 f"a draft, never published, deleted by the run that created it (ROADMAP N393).")
        code, out_text, err = gh(["release", "create", "--repo", f"{store_replica.ORG}/{repository}", "--draft",
                                  "--prerelease", "--title", f"{candidate} (rehearsal)", "--notes", notes,
                                  candidate, *[str(release_dir / name) for name in staged]])
        url = next((line.strip() for line in reversed(out_text.splitlines()) if line.strip()), "")
        if code != 0:
            raise Refused(f"gh release create exited {code}: {(err or out_text).strip()[-400:]}")
        if not url.startswith("https://"):
            raise Refused(f"gh release create exited 0 and printed no Release URL: {out_text.strip()[-400:]!r}")
        created = _created(gh, repo_path, candidate, url, sleep, gone)
        release = _gh_json(gh, ["api", f"{repo_path}/releases/{created.get('id')}"])
        check_release(release_dir, release if isinstance(release, dict) else {})
    except Refused as refused:
        failure = refused
    try:
        doomed = {row.get("id") for row in _named(gh, repo_path, candidate) if row.get("draft")} - gone
        if created is not None:
            doomed.add(created.get("id"))
        for release_id in sorted(doomed, key=str):
            _delete(gh, repo_path, release_id, sleep)
    except Refused as cleanup:
        raise Refused(f"{failure}; and the cleanup failed: {cleanup}" if failure else str(cleanup)) from cleanup
    if failure is not None:
        if created is None and url and not doomed:
            raise Refused(f"{failure}; the draft at {url} was not listed for the cleanup either, so the next "
                          f"rehearsal deletes it as a leftover") from failure
        raise failure
    say(f"store_publish: REHEARSED {repository} at {candidate}: a draft pre-release held exactly the "
        f"{len(staged)} staged asset(s), read back by check-release, then deleted")
    return staged


def live_gh(token: str) -> Gh:
    """`gh` with the rehearsal token as GH_TOKEN, never prompting."""
    def run(argv: list[str]) -> tuple[int, str, str]:
        done = subprocess.run(["gh", *argv], env={**os.environ, "GH_TOKEN": token, "GH_PROMPT_DISABLED": "1"},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=GH_TIMEOUT_SECONDS, check=False)
        return done.returncode, done.stdout, done.stderr
    return run


class _FakeForge:
    """The selftest's GitHub: one repository's Releases, answering the `gh` calls `rehearse` makes.
    `drop` loses one named asset at upload; `keep_on_delete` answers a DELETE without deleting; `lag`
    hides a created draft from that many listings after its create (the listing GitHub serves trails a
    create, step 0 run 40305270614); ids are never reused, as GitHub's are not; `twin` adds a second
    draft of the same name the create never made."""

    def __init__(self, full_name: str, visibility: str = "public", seeded: list[dict] | None = None,
                 drop: str | None = None, keep_on_delete: bool = False, lag: int = 0, twin: bool = False) -> None:
        self.full_name, self.visibility, self.drop, self.keep_on_delete = full_name, visibility, drop, keep_on_delete
        self.lag, self.twin = lag, twin
        self.releases = [{**row, "id": index + 1, "html_url": self._url(index + 1)}
                         for index, row in enumerate(seeded or [])]
        self.hidden: dict[int, int] = {}
        self.minted = len(self.releases)
        self.calls: list[list[str]] = []

    def _url(self, release_id: int) -> str:
        return f"https://github.com/{self.full_name}/releases/tag/untagged-{release_id:020x}"

    def _listing(self) -> list[dict]:
        shown = [row for row in self.releases if not self.hidden.get(row["id"])]
        self.hidden = {key: reads - 1 for key, reads in self.hidden.items() if reads > 1}
        return shown[:RELEASES_PAGE]

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        path = f"repos/{self.full_name}"
        if argv == ["api", path]:
            return 0, json.dumps({"visibility": self.visibility}), ""
        if argv == ["api", f"{path}/releases?per_page={RELEASES_PAGE}"]:
            return 0, json.dumps(self._listing()), ""
        if len(argv) == 2 and argv[0] == "api" and argv[1].startswith(f"{path}/releases/"):
            wanted = int(argv[1].rsplit("/", 1)[1])
            hit = [row for row in self.releases if row["id"] == wanted]
            return (0, json.dumps(hit[0]), "") if hit else (1, "", "gh: Not Found (HTTP 404)")
        if argv[:3] == ["api", "-X", "DELETE"]:
            wanted = int(argv[3].rsplit("/", 1)[1])
            if not any(row["id"] == wanted for row in self.releases):
                return 1, "", "gh: Not Found (HTTP 404)"
            if not self.keep_on_delete:
                self.releases = [row for row in self.releases if row["id"] != wanted]
            return 0, "", ""
        if argv[:2] == ["release", "create"]:
            valued = {"--repo", "--title", "--notes"}
            positional, index = [], 2
            while index < len(argv):
                if argv[index] in valued:
                    index += 2
                    continue
                if not argv[index].startswith("--"):
                    positional.append(argv[index])
                index += 1
            names = [Path(path).name for path in positional[1:] if Path(path).name != self.drop]
            made = []
            for _copy in range(2 if self.twin else 1):
                self.minted += 1
                release_id = self.minted
                self.releases.append({"id": release_id, "html_url": self._url(release_id),
                                      "tag_name": positional[0], "draft": "--draft" in argv,
                                      "prerelease": "--prerelease" in argv,
                                      "assets": [{"name": name} for name in names]})
                made.append(release_id)
                if self.lag:
                    self.hidden[release_id] = self.lag
            return 0, f"{self._url(made[0])}\n", ""
        return 1, "", f"the fake forge has no answer for {argv}"


CANDIDATE_PACKAGES = """\
destinations:
  dest-repo: {tag: "engine-v{version}"}
images:
  srv-image: {dockerfile: docker/Dockerfile, runtime: [libstdc++.so.6]}
packages:
  lib_a:
    release: true
    version: 9.9.9
    objects:
      bench/lib_a_bench.json: {published: {assets: [{name: lib-a-benchmark.json}]}}
      perf/lib_a_perf.json: {published: {assets: [{name: lib-a-perf.json}]}}
  tools:
    release: true
    version: 9.9.9
    path: tools
    executables:
      bin/tool: {published: {assets: [{destination: dest-repo, name: tool-linux-x64}]}}
    objects:
      tracked/sbom.json: {published: {assets: [{name: sbom.json}]}}
  server:
    release: false
    executables:
      bin/server: {published: {image: srv-image}}
  proof:
    release: false
    cut_tool: {why: the cut's render runs it}
    executables:
      bin/proof: run-from-cache
ingest:
  golden.yaml:
    artifact: determinism-golden
    object: determinism-golden.txt
    commits: [pub-repo]
    published: {assets: [{name: pub.golden.txt}]}
"""


def _transport(entries: dict[str, bytes]) -> bytes:
    """A `conan cache save` archive holding each entry at `b/<folder>/p/<entry>`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for entry, data in sorted(entries.items()):
            info = tarfile.TarInfo(f"b/pkg0123/p/{entry}")
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _candidate_scene(root: Path, packages: str = CANDIDATE_PACKAGES, perf_verdict: str = "pass",
                     image: bool = True) -> dict:
    """A repository `pub-repo` at a commit, its candidate store (three ship-form creates, a tracked step, a perf
    verdict, the golden's ingestion and the image store_image's candidate verb builds) pushed to an in-process
    registry and its index published, the cut's benchmark of it, and the API answers a release job reads."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def repository(path: Path, files: dict[str, str]) -> Path:
        path.mkdir(parents=True)
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        for argv in (["init", "--quiet", "-b", "main"], ["add", "--all"], ["commit", "--quiet", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(path), *argv], check=True, env=env, capture_output=True)
        return path

    checkout = repository(root / "pub-repo", {"packages.yml": packages, "tools/sbom.json": '{"bom": 1}\n',
                                              "docker/Dockerfile": "FROM scratch\n"})
    malf = repository(root / "malf", {"README": "malf\n"})
    store = root / "store"
    put_object = store_transport._put_object
    put_record = store_transport._put_record
    tool, server = b"\x7fELF tool bytes", b"\x7fELF server bytes"
    keys = {}
    for package, entries, build_ids in (("lib_a", {}, {}), ("tools", {"bin/tool": tool}, {}),
                                        ("server", {"bin/server": server}, {"bin/server": "b" * 40})):
        transport = put_object(store, _transport(entries))
        listed = [[entry, "executable", hashlib.sha256(data).hexdigest()] for entry, data in sorted(entries.items())]
        keys[package] = put_record(store, {"step": {"kind": "conan-create", "package": package, "form": "ship",
                                                    "profile": "linux-gcc16-release"}},
                                   {"package": {"content": "c" * 64, "entries": listed, "transport": transport,
                                                "build_ids": build_ids}})
    proof = b"\x7fELF proof bytes"
    keys["proof"] = put_record(store, {"step": {"kind": "conan-create", "package": "proof", "form": "ship",
                                                "profile": "linux-gcc16-release",
                                                "executables": {"bin/proof": RUN_FROM_CACHE}}},
                               {"package": {"content": "d" * 64, "transport": put_object(store, _transport({"bin/proof": proof})),
                                            "entries": [["bin/proof", "executable", hashlib.sha256(proof).hexdigest()]],
                                            "build_ids": {}}})
    sbom = put_object(store, b'{"bom": 1}\n')
    keys["tracked"] = put_record(store, {"step": {"kind": "tracked", "repository": "pub-repo", "package": "tools"}},
                                 {"tracked": {"objects": {"sbom.json": sbom}}})
    perf = put_object(store, b'{"perf": [1]}\n')
    keys["perf"] = put_record(store, {"step": {"kind": "verdict", "id": "lib_a/perf", "subject": "lib_a"}},
                              {"perf": {"verdict": perf_verdict, "findings": [], "objects": {"lib_a_perf.json": perf}}})
    golden = put_object(store, b"agreed golden digest\n")
    keys["golden"] = put_record(store, {"step": {"kind": "ingest", "repository": "pub-repo",
                                                 "artefact": "determinism-golden",
                                                 "workflow": ".github/workflows/golden.yaml", "class": "object"},
                                        "run": {"id": 41, "attempt": 1}},
                                {"ingest": {"objects": {"determinism-golden.txt": golden}, "class": "object"}})
    if image:
        store_image.candidate(store, checkout, "run-1", root / "image-scratch", store_image._layout(),
                              store_image._fake_runtime, lambda _line: None)
    fake = store_replica.Fake()
    server_handle, base = store_replica._fake_server(fake)
    registry = store_replica.Registry(base, "coderoasted/store-pub-repo", ("x-access-token", "token"))
    out = root / "push"
    pushed = store_transport.push(registry, lambda: None, store, "CodeRoasted/pub-repo", checkout, {}, malf, {}, out,
                                  lambda _line: None)
    store_transport.publish_index(registry, out, lambda _line: None)
    index = json.loads((out / "index.json").read_bytes())
    commit, tree = store_transport.checkout_identity(checkout)
    answers = {"repos/coderoasted/pub-repo/commits/v9.9.9": {"sha": commit, "commit": {"tree": {"sha": tree}}},
               f"repos/coderoasted/pub-repo/contents/packages.yml?ref={commit}": {
                   "encoding": "base64", "content": base64.b64encode(packages.encode()).decode()}}
    bench = root / "cut-bench"
    (bench / "lib_a").mkdir(parents=True)
    (bench / "lib_a" / "lib_a_bench.json").write_bytes(b'{"benchmarks": [1]}\n')
    measured = Measured(index["candidate"], {"lib_a": {"record": "9" * 64, "verdict": "pass", "files": {
        "lib_a_bench.json": hashlib.sha256(b'{"benchmarks": [1]}\n').hexdigest()}}}, bench)
    return {"server": server_handle, "registry": registry, "fake": fake, "key": index["candidate"], "pushed": pushed,
            "answers": answers, "tool": tool, "proof": proof, "server_bytes": server, "keys": keys, "sbom": sbom, "perf": perf,
            "measured": measured}


def candidate_arms(arm: Callable, failures: list[str], root: Path, servers: list) -> None:
    """The release from its candidate (DN-142.D33 (3)): every declared row resolved in the candidate restored
    from the repository's own store, staged with its publication document; a row the candidate does not hold,
    a tree that is not the tag's, an unpassed producer and a moved image tag refused before anything lands."""
    quiet: Callable[[str], None] = lambda _line: None  # noqa: E731 — a sink for the lines

    def opened(scene: dict, label: str, answers: dict | None = None, key: str | None = None
               ) -> tuple[Candidate, dict]:
        reader = store_transport.Reader(scene["registry"], root / f"cache-{label}", quiet)
        held = answers if answers is not None else scene["answers"]
        get = lambda path: (200, held[path]) if path in held else (404, {})  # noqa: E731 — the fixture API
        return released_candidate(reader, root / f"restored-{label}", "coderoasted/pub-repo", key or scene["key"],
                                  "v9.9.9", None, get, quiet)

    scene = _candidate_scene(root / "green")
    servers.append(scene["server"])
    if scene["pushed"] != 0:
        failures.append(f"CANDIDATE: the fixture's push exited {scene['pushed']}")
        return
    found = arm("CANDIDATE: the release's candidate is read from its own store, its tree the tag's",
                lambda: opened(scene, "a"), None)
    if found is None:
        return
    candidate, declared = found
    out = root / "staged"
    measured = scene["measured"]
    result = arm("CANDIDATE: every declared row resolves — the benchmark in the cut's, the rest in the candidate — "
                 "and is staged with the publication document",
                 lambda: stage(candidate, declared, "9.9.9", "v9.9.9", out, quiet, measured), None)
    want_own = ["lib-a-benchmark.json", "lib-a-perf.json", "lib_a-9.9.9.tgz", "pub.golden.txt", "sbom.json",
                "tools-9.9.9.tgz", "pub-repo-9.9.9.publication.json"]
    want_destination = ["tool-linux-x64", "tool-linux-x64.sha256", "dest-repo-engine-v9.9.9.publication.json"]
    if result is not None:
        staged, done = result
        if staged != want_own or done != [("dest-repo", "engine-v9.9.9", want_destination)]:
            failures.append(f"CANDIDATE: staged {staged} and {done}, want {want_own} and dest-repo {want_destination}")
        elif (out / "destinations" / "dest-repo" / "tool-linux-x64").read_bytes() != scene["tool"] or \
                not (out / "destinations" / "dest-repo" / "tool-linux-x64.sha256").read_text().startswith(
                    hashlib.sha256(scene["tool"]).hexdigest()) or \
                (out / "release" / "sbom.json").read_bytes() != b'{"bom": 1}\n' or \
                (out / "destinations" / "destinations.tsv").read_text() != "dest-repo\tengine-v9.9.9\n":
            failures.append("CANDIDATE: a staged file's bytes, its .sha256 or the destinations list differ from the candidate")
        else:
            document = json.loads((out / "release" / "pub-repo-9.9.9.publication.json").read_bytes())
            rows = {row["name"]: row for row in document["assets"]}
            if document["candidate"] != scene["key"] or rows["sbom.json"]["record"] != scene["keys"]["tracked"] or \
                    rows["lib-a-perf.json"]["verdicts"] != [scene["keys"]["perf"]] or \
                    rows["lib-a-benchmark.json"]["verdicts"] != ["9" * 64] or \
                    rows["pub.golden.txt"]["record"] != scene["keys"]["golden"] or \
                    rows["tools-9.9.9.tgz"]["record"] != scene["keys"]["tools"] or \
                    [image["name"] for image in document["images"]] != ["srv-image"] or \
                    document["destinations"]["dest-repo"]["publication"] != hashlib.sha256(
                        (out / "destinations" / "dest-repo" / "dest-repo-engine-v9.9.9.publication.json").read_bytes()
                    ).hexdigest():
                failures.append(f"CANDIDATE: the publication document does not bind the candidate's records: {document}")
            listed = {"assets": [{"name": name} for name in want_own]}
            arm("CANDIDATE: the Release read after the publish equals the staged files",
                lambda: check_release(out / "release", listed), None)
    extracted = root / "cut-tool" / "proof"
    extracted.parent.mkdir()
    tool_found = arm("CUT TOOL: a cut tool's run-from-cache entry is extracted from the candidate by its digest, "
                     "executable, and derives no Release row",
                     lambda: cut_tool(candidate, "proof", "bin/proof", extracted), None)
    if tool_found is not None and (extracted.read_bytes() != scene["proof"] or not os.access(extracted, os.X_OK)
                                   or tool_found["record"] != scene["keys"]["proof"]):
        failures.append(f"CUT TOOL: extracted {tool_found} and bytes that are not the candidate's, or not executable")
    arm("RED: a published executable is no cut tool, so the cut never runs it from the candidate",
        lambda: cut_tool(candidate, "tools", "bin/tool", root / "cut-tool" / "tool"), "not `run-from-cache`")
    arm("RED: a package the candidate holds no ship-form create of hands out no cut tool",
        lambda: cut_tool(candidate, "absent", "bin/proof", root / "cut-tool" / "absent"),
        "0 ship-form create record(s) of absent")
    released_tool = json.loads(json.dumps(declared))
    released_tool["packages"]["proof"]["release"] = True
    released_tool["packages"]["proof"]["version"] = "9.9.9"
    arm("RED: a cut tool declared `release: true` derives no row: a cut tool is never released",
        lambda: stage(candidate, released_tool, "9.9.9", "v9.9.9", root / "released-tool", quiet, measured),
        "proof: a cut tool is never released")
    arm("RED: a tag that is not v<version> stages nothing",
        lambda: stage(candidate, declared, "9.9.9", "v9.9.8", root / "wrong-tag", quiet, measured),
        "is not the release tag v9.9.9")
    ingesting = json.loads(json.dumps(declared))
    ingesting["ingest"]["windows.yml"] = {"artifact": "win", "executable": "win.exe", "commits": ["pub-repo"],
                                          "published": {"assets": [{"name": "win.exe"}]}}
    arm("RED: a declared hosted output the candidate holds no ingestion of is refused by name, and nothing is staged",
        lambda: stage(candidate, ingesting, "9.9.9", "v9.9.9", root / "ingest", quiet, measured),
        "pub-repo/win.exe: the candidate holds 0 ingestion record(s) of windows.yml's win")
    arm("RED: a benchmark row with no cut benchmark of the repository is refused, never taken from the candidate",
        lambda: stage(candidate, declared, "9.9.9", "v9.9.9", root / "unmeasured", quiet),
        "pub-repo/lib-a-benchmark.json: it is a benchmark, and the cut's record names no benchmark")
    arm("RED: the cut's benchmark of another candidate is refused",
        lambda: stage(candidate, declared, "9.9.9", "v9.9.9", root / "other-key", quiet,
                      Measured("e" * 64, measured.packages, measured.folder)), f"the cut measured candidate-{'e' * 64}")
    tampered_bench = root / "tampered-bench" / "lib_a"
    tampered_bench.mkdir(parents=True)
    (tampered_bench / "lib_a_bench.json").write_bytes(b"other numbers\n")
    arm("RED: a benchmark file that does not hash to the digest the attested record names is refused",
        lambda: stage(candidate, declared, "9.9.9", "v9.9.9", root / "tampered", quiet,
                      Measured(measured.key, measured.packages, tampered_bench.parent)), "does not hash to the record's")
    cut_record = json.dumps({"schema": CUT_RECORD_SCHEMA, "cuttable": True, "benchmarks": {"pub-repo": {
        "key": measured.key, "packages": measured.packages}}}).encode()
    arm("CANDIDATE: the cut record's benchmarks of the repository are read once its bytes are the attested ones",
        lambda: cut_benchmarks(cut_record, hashlib.sha256(cut_record).hexdigest(), "pub-repo", measured.folder).packages,
        None)
    arm("RED: a cut record whose bytes are not the ones attest attested is refused",
        lambda: cut_benchmarks(cut_record, "0" * 64, "pub-repo", measured.folder), "attest attested")
    if (root / "ingest").exists():
        failures.append("RED: a refused publication staged files")
    judged = json.loads(json.dumps(declared))
    judged["packages"]["tools"]["executables"]["bin/tool"]["published"]["assets"][0]["verdicts"] = ["consumer"]
    arm("RED: an entry whose declared verdict the candidate holds no pass of is refused",
        lambda: stage(candidate, judged, "9.9.9", "v9.9.9", root / "verdict", quiet, measured),
        "0 passing `consumer` verdict(s) of tools")
    foreign = json.loads(json.dumps(declared))
    foreign["packages"]["lib_a"]["objects"]["perf/lib_a_perf.json"]["published"]["assets"][0]["destination"] = "elsewhere"
    arm("RED: a site naming a foreign destination no `destinations:` row declares is refused",
        lambda: stage(candidate, foreign, "9.9.9", "v9.9.9", root / "foreign", quiet, measured),
        "declares no `destinations:` row")
    moved = {**scene["answers"], "repos/coderoasted/pub-repo/commits/v9.9.9": {
        "sha": "1" * 40, "commit": {"tree": {"sha": "2" * 40}}}}
    arm("RED: a tag whose tree is not the candidate's tree publishes nothing from it",
        lambda: opened(scene, "moved", moved), "a release publishes the candidate of its own tree")
    arm("RED: a candidate key the store does not hold is refused, never substituted",
        lambda: opened(scene, "absent", key="f" * 64), "could not be read as trusted")

    red = _candidate_scene(root / "red-perf", perf_verdict="fail")
    servers.append(red["server"])
    red_found = arm("CANDIDATE: a candidate whose bench verdict failed is still read", lambda: opened(red, "red"), None)
    if red_found is not None:
        arm("RED: an object whose producing verdict did not pass is refused",
            lambda: stage(*red_found, "9.9.9", "v9.9.9", root / "red-staged", quiet,
                          Measured(red["key"], measured.packages, measured.folder)), "judged 'fail', not pass")
    bare = _candidate_scene(root / "no-image", image=False)
    servers.append(bare["server"])
    bare_found = arm("CANDIDATE: a candidate stored before its image step is read", lambda: opened(bare, "bare"), None)
    if bare_found is not None:
        arm("RED: a declared image the candidate holds no record of is refused",
            lambda: stage(*bare_found, "9.9.9", "v9.9.9", root / "bare-staged", quiet,
                          Measured(bare["key"], measured.packages, measured.folder)), "0 image record(s) of srv-image")

    target_fake = store_replica.Fake()
    target_server, target_base = store_replica._fake_server(target_fake)
    servers.append(target_server)
    target = store_replica.Registry(target_base, "coderoasted/srv-image", ("user", "token"))
    stored_manifest = next(row["manifest"] for row in image_rows(candidate, declared))
    pushed = arm("CANDIDATE: the candidate's image is pushed verbatim and tagged: the registry's digest is the stored one",
                 lambda: publish_image(candidate, declared, target, "srv-image", "v9.9.9", quiet), None)
    if pushed != stored_manifest or target_fake.tags.get("v9.9.9") != stored_manifest:
        failures.append(f"CANDIDATE: pushed {pushed}, tagged {target_fake.tags.get('v9.9.9')}, want {stored_manifest}")
    arm("CANDIDATE: publishing the same image under the same tag again is idempotent",
        lambda: publish_image(candidate, declared, target, "srv-image", "v9.9.9", quiet), None)
    other = b'{"schemaVersion":2,"note":"another image"}'
    target_fake.manifests["sha256:" + hashlib.sha256(other).hexdigest()] = other
    target_fake.tags["v9.9.8"] = "sha256:" + hashlib.sha256(other).hexdigest()
    arm("RED: a release tag already naming another image is never moved",
        lambda: publish_image(candidate, declared, target, "srv-image", "v9.9.8", quiet), "a release tag is never moved")
    arm("RED: an image packages.yml does not declare is refused",
        lambda: publish_image(candidate, declared, target, "other-image", "v9.9.9", quiet), "0 image(s) named other-image")
    document = (out / "release" / "pub-repo-9.9.9.publication.json").read_bytes() if result is not None else b"{}"
    deployed = arm("CANDIDATE: a deploy reads the image's digest and its binary's identity from the publication "
                   "document and the candidate, which agree",
                   lambda: deployment(document, candidate, declared, "srv-image", "9.9.9"), None)
    if deployed is not None and (deployed["manifest"] != stored_manifest or [
            (row["entry"], row["digest"], row["build_id"]) for row in deployed["executables"]] != [
            ("bin/server", hashlib.sha256(scene["server_bytes"]).hexdigest(), "b" * 40)]):
        failures.append(f"CANDIDATE: the deployment read {deployed}")
    tampered = json.loads(document or b"{}")
    for row in tampered.get("images") or []:
        row["manifest"] = "sha256:" + "0" * 64
    arm("RED: a publication document whose image digest the candidate does not hold deploys nothing",
        lambda: deployment(json.dumps(tampered).encode(), candidate, declared, "srv-image", "9.9.9"),
        "the publication document says")
    arm("RED: a deploy of another version than the document's is refused",
        lambda: deployment(document, candidate, declared, "srv-image", "9.9.8"), "the deploy reads")


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, run: Callable[[], object], refusal: str | None) -> object:
        armed[0] += 1
        try:
            result = run()
        except (Refused, store_replica.Refused, store_manifest.Refused) as refused:
            if refusal is None or refusal not in str(refused):
                failures.append(f"{label}: refused with {refused}, want "
                                f"{'success' if refusal is None else repr(refusal)}")
            return None
        if refusal is not None:
            failures.append(f"{label}: succeeded, want a refusal containing {refusal!r}")
        return result

    quiet: Callable[[str], None] = lambda _line: None  # noqa: E731 — a sink for the writer's lines
    idle: Callable[[float], None] = lambda _seconds: None  # noqa: E731 — the fake forge needs no wall-clock wait
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        store, record, plan, _transports = store_manifest._fixture(root / "fixture")
        context = root / "image-context"
        (context / store_image.STAGING / "bin").mkdir(parents=True)
        (context / "Dockerfile").write_text("FROM scratch\n")
        (context / store_image.STAGING / "bin" / "server").write_bytes(b"\x7fELF server bytes")
        archive = root / "image.oci.tar"
        store_image._layout()(context, "Dockerfile", archive)
        image_manifest, _entries = store_image.judge_archive(archive)
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (store / "objects" / archive_digest).write_bytes(archive.read_bytes())
        image_key = next(step["key"] for step in json.loads(record)["steps"] if step["id"] == "srv-image/image")
        image_record = store / "records" / f"{image_key}.json"
        rewritten = json.loads(image_record.read_text())
        rewritten["outputs"]["image"].update(archive=archive_digest, manifest=image_manifest)
        image_record.write_text(json.dumps(rewritten))
        keys = root / "keys"
        keys.mkdir()
        for name in ("writer", "other"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(keys / name)],
                           check=True, stdin=subprocess.DEVNULL)
        allowed = keys / "allowed_signers"
        allowed.write_text(f'{store_manifest.WRITER_IDENTITY} namespaces="{store_manifest.NAMESPACE}" '
                           f"{(keys / 'writer.pub').read_text().strip()}\n")

        def destination_signed(label: str, body: bytes) -> bytes:
            """The seal's act on a destination slice: signed by the writer, both kept as objects."""
            path = root / f"destination-{label}.json"
            path.write_bytes(body)
            signature = store_manifest.sign(path, keys / "writer").read_bytes()
            for held in (body, signature):
                store_manifest.hold(store, hashlib.sha256(held).hexdigest(), held)
            return signature

        manifest, slices, destination_slices = store_manifest.build(store, record, plan, destination_signed)
        owned = json.loads(json.dumps(plan))
        owned["repositories"]["priv-repo"].pop("destinations")
        for row in owned["repositories"]["priv-repo"]["assets"]:
            row["destination"] = "priv-repo"
        _owned_manifest, owned_slices, _none = store_manifest.build(store, record, owned)
        record_hex = hashlib.sha256(record).hexdigest()

        signings = [0]

        def signed(body: bytes, key: str) -> bytes:
            signings[0] += 1
            path = root / f"to-sign-{signings[0]}-{key}"
            path.write_bytes(body)
            return store_manifest.sign(path, keys / key).read_bytes()

        servers = []

        def registry_with(slice_bytes: bytes, signature: bytes, record_tag: str = record_hex,
                          **knobs) -> tuple[store_replica.Fake, store_replica.Registry]:
            fake = store_replica.Fake(**knobs)
            server, base = store_replica._fake_server(fake)
            servers.append(server)
            registry = store_replica.Registry(base, "coderoasted/store-pub-repo", ("user", "token"))
            view = store_replica.github_guard(base, "coderoasted", "api-token")
            body = json.loads(slice_bytes)
            objects = sorted({asset["object"] for asset in body["assets"]} |
                             {binding[field] for binding in (body.get("destinations") or {}).values()
                              for field in ("slice", "signature")})
            store_replica.replicate(store, registry, view, "coderoasted", "store-pub-repo", objects, quiet)
            store_replica.replicate_slice(registry, view, "coderoasted", "store-pub-repo", record_tag,
                                          slice_bytes, signature, quiet)
            return fake, registry

        try:
            pub = slices["pub-repo"]
            fake, registry = registry_with(pub, signed(pub, "writer"))
            out = root / "out"
            staged = arm("the slice of the attested record stages every asset, verified",
                         lambda: slice_stage(registry, "pub-repo", "public", record_hex, allowed, out, quiet), None)
            want = ["pub_lib-9.9.9.tgz", "pub-repo-9.9.9.slice.json", "pub-repo-9.9.9.slice.json.sig"]
            if staged != want:
                failures.append(f"staged {staged}, want {want}: the destination stages only the rows it owns")
            owned_private = owned_slices["priv-repo"]
            _fake, owned_registry = registry_with(owned_private, signed(owned_private, "writer"))
            owned_out = root / "owned"
            staged_owned = arm("an entry destined to its owner's own Release is staged with its .sha256",
                               lambda: slice_stage(owned_registry, "priv-repo", "private", record_hex, allowed,
                                             owned_out, quiet), None)
            want_owned = ["bench.json", "golden.txt", "priv_tools-9.9.9.tgz", "sbom.json", "tool-linux-x64",
                          "tool-linux-x64.sha256", "win.exe", "win.exe.sha256", "priv-repo-9.9.9.slice.json",
                          "priv-repo-9.9.9.slice.json.sig"]
            if staged_owned != want_owned:
                failures.append(f"staged {staged_owned}, want {want_owned}")
            elif (owned_out / "tool-linux-x64").read_bytes() != b"\x7fELF tool bytes" or \
                    not (owned_out / "tool-linux-x64.sha256").read_text().startswith(
                        hashlib.sha256(b"\x7fELF tool bytes").hexdigest()):
                failures.append("the staged entry or its .sha256 differs from the slice")
            elif (owned_out / "win.exe").read_bytes() != b"MZ windows bytes" or \
                    not (owned_out / "win.exe.sha256").read_text().startswith(
                        hashlib.sha256(b"MZ windows bytes").hexdigest()):
                failures.append("an ingested entry is staged with its own bytes and the .sha256 written from "
                                "its digest (DN-142.D25 (4) A)")
            elif (owned_out / "sbom.json").read_bytes() != b'{"bom": 1}\n' or \
                    (owned_out / "golden.txt").read_bytes() != b"agreed golden digest\n" or \
                    (owned_out / "golden.txt.sha256").exists():
                failures.append("a staged object's bytes differ from the store's, or an object gained a sidecar")
            twice = root / "signed-twice"
            twice.write_bytes(pub)
            store_manifest.sign(twice, keys / "writer")
            arm("a signature is written once: signing over an existing one is refused, never prompted",
                lambda: store_manifest.sign(twice, keys / "writer"), "already exists")
            arm("a slice built for a public repository is refused where the repository is private now",
                lambda: slice_stage(registry, "pub-repo", "private", record_hex, allowed, root / "o8", quiet),
                "the repository is private now")
            if staged == want:
                listed = {"assets": [{"name": name} for name in want]}
                arm("the Release read after the publish equals the slice", lambda: check_release(out, listed), None)
                arm("one asset more on the Release than the slice lists reds the reading",
                    lambda: check_release(out, {"assets": listed["assets"] + [{"name": "extra.tgz"}]}),
                    "attached and never staged ['extra.tgz']")
                arm("one asset fewer on the Release than the slice lists reds the reading",
                    lambda: check_release(out, {"assets": listed["assets"][1:]}),
                    "staged and not attached ['pub_lib-9.9.9.tgz']")
                republished = {"assets": [{"name": "pub_lib-9.9.9.tgz"}, {"name": "pub-repo-benchmark.json"},
                                          {"name": "golden.txt.sha256"}]}
                if stale_assets(out, republished) != ["golden.txt.sha256", "pub-repo-benchmark.json"]:
                    failures.append(f"a re-fired Release's stale assets read {stale_assets(out, republished)}, want "
                                    f"the two the slice no longer lists")
                if stale_assets(out, listed) != []:
                    failures.append(f"a Release equal to the slice reads stale assets {stale_assets(out, listed)}")
                if stale_assets(out, {"assets": []}) != []:
                    failures.append("a Release with no asset reads a stale asset")
            arm("another slice for the same record is refused by the writer, never rewritten",
                lambda: store_replica.replicate_slice(
                    registry, store_replica.github_guard(registry.base, "coderoasted", "t"), "coderoasted",
                    "store-pub-repo", record_hex, pub + b" ", signed(pub + b" ", "writer"), quiet),
                "never rewritten")
            arm("a record attest did not attest has no slice to stage",
                lambda: slice_stage(registry, "pub-repo", "public", "c" * 64, allowed, root / "o2", quiet), "HTTP 404")
            arm("the slice is verified for the repository that asks, not any other",
                lambda: slice_stage(registry, "priv-repo", "private", record_hex, allowed, root / "o3", quiet),
                "not 'priv-repo''s")

            private = slices["priv-repo"]
            _fake, registry = registry_with(private, signed(private, "writer"))
            said: list[str] = []
            staged_private = arm("an image asset and a row destined to a foreign Release are never staged on "
                                 "the owner's Release",
                                 lambda: slice_stage(registry, "priv-repo", "private", record_hex, allowed, root / "o9",
                                               said.append), None)
            if staged_private != ["bench.json", "golden.txt", "priv_tools-9.9.9.tgz", "sbom.json",
                                  "priv-repo-9.9.9.slice.json", "priv-repo-9.9.9.slice.json.sig"] or not all(any(
                    line.startswith(f"store_publish: FOREIGN {name} belongs on pub-repo's engine-v9.9.9")
                    for line in said) for name in ("tool-linux-x64", "win.exe")):
                failures.append(f"the owner staged {staged_private}, saying {said}")
            elsewhere = root / "destinations"
            placed_rows = arm("the owner stages each destination Release it binds from its OWN store package: the "
                              "projected rows, verified, with the destination slice and its signature",
                              lambda: slice_stage_destinations(registry, "priv-repo", "private", record_hex, allowed,
                                                         elsewhere, quiet), None)
            want_destination = ["tool-linux-x64", "tool-linux-x64.sha256", "win.exe", "win.exe.sha256",
                                "pub-repo-engine-v9.9.9.slice.json", "pub-repo-engine-v9.9.9.slice.json.sig"]
            if placed_rows != [("pub-repo", "engine-v9.9.9", "public", want_destination)] or \
                    (elsewhere / "destinations.tsv").read_text() != "pub-repo\tengine-v9.9.9\tpublic\n" or \
                    (elsewhere / "pub-repo" / "pub-repo-engine-v9.9.9.slice.json").read_bytes() != \
                    destination_slices[("priv-repo", "pub-repo")][0]:
                failures.append(f"the destination staging is {placed_rows}")
            else:
                listed = {"assets": [{"name": name} for name in want_destination]}
                releases = [{"tagName": "v2.0.0", "isLatest": True}, {"tagName": "engine-v9.9.9", "isLatest": False}]
                arm("the destination Release read back equals its slice and is not Latest",
                    lambda: check_destination(elsewhere / "pub-repo", listed, releases, "engine-v9.9.9"), None)
                arm("RED: one extra asset on the destination Release reds its after-reading",
                    lambda: check_destination(elsewhere / "pub-repo", {"assets": listed["assets"] + [
                        {"name": "sift-extra"}]}, releases, "engine-v9.9.9"), "attached and never staged ['sift-extra']")
                arm("RED: a destination Release wearing the Latest badge reds its after-reading",
                    lambda: check_destination(elsewhere / "pub-repo", listed,
                                              [{"tagName": "engine-v9.9.9", "isLatest": True}], "engine-v9.9.9"),
                    "never claims its repository's badge")
            corrupt, corrupt_registry = registry_with(private, signed(private, "writer"))
            bound = json.loads(private)["destinations"]["pub-repo"]["slice"]
            corrupt.blobs[f"sha256:{bound}"] = corrupt.blobs[f"sha256:{bound}"].replace(b"tool-linux-x64", b"tool-linux-x65")
            arm("RED: a destination slice whose served bytes differ from the digest the owner slice binds is "
                "refused before anything is staged",
                lambda: slice_stage_destinations(corrupt_registry, "priv-repo", "private", record_hex, allowed,
                                           root / "corrupt", quiet), "the destination slice of pub-repo")
            if (root / "corrupt" / "pub-repo").exists():
                failures.append("a refused destination staging left a directory behind")
            candidate = "candidate/9.9.9/0123456789abcdef"
            for name, (label, forge_knobs, run_with, refusal) in enumerate((
                    ("the rehearsal stages the slice into ONE draft pre-release named by the candidate, reads it "
                     "back equal to the slice, then deletes it", {}, candidate, None),
                    ("a leftover rehearsal draft named by the candidate is deleted before the new one",
                     {"seeded": [{"tag_name": candidate, "draft": True, "assets": []}]}, candidate, None),
                    ("RED: a draft missing one staged asset reds the reading, and the draft is still deleted",
                     {"drop": "pub_lib-9.9.9.tgz"}, candidate, "staged and not attached ['pub_lib-9.9.9.tgz']"),
                    ("RED: a PUBLISHED Release named by the candidate is refused and never touched",
                     {"seeded": [{"tag_name": candidate, "draft": False, "assets": []}]}, candidate,
                     "a published Release named"),
                    ("a draft the listing shows only on its third read after the create still rehearses: it "
                     "is judged by the URL the create printed, not by the first listing (step 0 run "
                     "40305270614)", {"lag": 2}, candidate, None),
                    ("RED: a draft never listed within the bounded wait is refused, never passed",
                     {"lag": REHEARSAL_READS + 1}, candidate, "was never listed"),
                    ("RED: a second draft named by the candidate that the create did not make is refused",
                     {"twin": True}, candidate, "not the one the create printed"),
                    ("RED: a deletion the forge did not perform reds the rehearsal",
                     {"keep_on_delete": True}, candidate, "still present after its deletion"),
                    ("RED: a name that is no candidate's is refused before any call", {}, "v9.9.9",
                     "is not a candidate name"),
                    ("RED: a repository whose visibility differs from the slice's is refused before any draft",
                     {"visibility": "private"}, candidate, "the repository is private now"),
                    ("RED: one page full of Releases cannot prove the candidate's absence",
                     {"seeded": [{"tag_name": f"v0.0.{index}", "draft": False, "assets": []}
                                 for index in range(RELEASES_PAGE)]}, candidate, "Releases or more"))):
                forge = _FakeForge(f"{store_replica.ORG}/pub-repo", **forge_knobs)
                _fake, rehearsed_registry = registry_with(pub, signed(pub, "writer"))
                done = arm(label, lambda: rehearse(rehearsed_registry, "pub-repo", record_hex, allowed, run_with,
                                                   root / f"rehearsal-{name}", forge, quiet, idle), refusal)
                creates = [call for call in forge.calls if call[:2] == ["release", "create"]]
                drafts = [row for row in forge.releases if row["draft"]]
                if refusal is None and (done != want or len(creates) != 1 or drafts or
                                        not {"--draft", "--prerelease"} <= set(creates[0]) or
                                        "--verify-tag" in creates[0] or creates[0][-len(want) - 1] != candidate):
                    failures.append(f"{label}: returned {done}, created {creates}, drafts left {drafts}")
                if refusal is not None and drafts and not forge_knobs.get("keep_on_delete") and \
                        not forge_knobs.get("lag"):
                    failures.append(f"{label}: a refused rehearsal left the draft(s) {drafts}")
                if refusal is not None and forge_knobs.get("lag"):
                    forge.lag = 0
                    arm(f"{label}: the next rehearsal deletes the draft that was never listed as a leftover",
                        lambda: rehearse(rehearsed_registry, "pub-repo", record_hex, allowed, run_with,
                                         root / f"rehearsal-{name}-next", forge, quiet, idle), None)
                    if [row for row in forge.releases if row["draft"]]:
                        failures.append(f"{label}: the next rehearsal left the draft(s) {forge.releases}")
                if run_with != candidate and forge.calls:
                    failures.append(f"{label}: a refused name still called the forge {forge.calls}")
                if forge_knobs.get("visibility") and creates:
                    failures.append(f"{label}: a refused visibility still created {creates}")
                if any(not row["draft"] and row["tag_name"] == candidate for row in forge_knobs.get("seeded", [])) \
                        and (creates or [{key: value for key, value in row.items() if key not in ("id", "html_url")}
                                         for row in forge.releases if row["tag_name"] == candidate] !=
                             forge_knobs["seeded"]):
                    failures.append(f"{label}: the published Release was touched: {forge.releases}, {creates}")

            private_forge = _FakeForge(f"{store_replica.ORG}/priv-repo", visibility="private")
            _fake, private_registry = registry_with(private, signed(private, "writer"))
            rehearsed_private = arm("the rehearsal of an owner binding a destination also verifies and stages that "
                                    "destination's slice, and drafts nothing on the destination",
                                    lambda: rehearse(private_registry, "priv-repo", record_hex, allowed, candidate,
                                                     root / "rehearsal-private", private_forge, quiet, idle), None)
            listed_tsv = root / "rehearsal-private" / "destinations" / "destinations.tsv"
            if rehearsed_private is not None and (
                    not listed_tsv.is_file() or listed_tsv.read_text() != "pub-repo\tengine-v9.9.9\tpublic\n"
                    or any("pub-repo" in " ".join(call) for call in private_forge.calls)):
                failures.append(f"the private owner's rehearsal staged destinations "
                                f"{listed_tsv.read_text() if listed_tsv.is_file() else 'none'} and called "
                                f"{private_forge.calls}")

            _fake, registry = registry_with(pub, signed(pub, "other"))
            arm("a slice signed by any key but the writer's stages nothing",
                lambda: slice_stage(registry, "pub-repo", "public", record_hex, allowed, root / "o4", quiet), "does not verify")
            if (root / "o4").exists() and any((root / "o4").iterdir()):
                failures.append("a refused stage left files behind")

            _fake, registry = registry_with(pub, signed(pub, "writer"), record_tag="d" * 64)
            arm("a genuine slice served under another record's tag is refused: it names its own record",
                lambda: slice_stage(registry, "pub-repo", "public", "d" * 64, allowed, root / "o5", quiet),
                "attest attested")

            fake, registry = registry_with(pub, signed(pub, "writer"))
            fake.corrupt_blobs = True
            arm("an object whose served bytes differ from its digest is refused",
                lambda: slice_stage(registry, "pub-repo", "public", record_hex, allowed, root / "o6", quiet), "hashes to")

            fake, registry = registry_with(pub, signed(pub, "writer"))
            fake.tags[store_replica.slice_tag(record_hex)] = fake.tags[store_replica.SENTINEL_TAG]
            arm("a slice tag that holds no slice and signature is refused",
                lambda: slice_stage(registry, "pub-repo", "public", record_hex, allowed, root / "o7", quiet),
                "not a slice and its signature")
            candidate_arms(arm, failures, root / "candidate", servers)
        finally:
            for server in servers:
                server.shutdown()

    for failure in failures:
        print(f"store_publish selftest FAILED: {failure}")
    print(f"store_publish selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def _token_file(env: str, label: str) -> str:
    """The token in the file `env` names, for `label`; never an argument, never the environment's value."""
    path = os.environ.get(env, "")
    if not path or not Path(path).is_file() or not Path(path).read_text().strip():
        raise Refused(f"{env} names no non-empty file: {label} reads its token from the writer account's "
                      f"token file")
    return Path(path).read_text().strip()

def _trusted(argv: list[str], positional: tuple[str, ...]) -> tuple[argparse.Namespace, Get,
                                                                      store_transport.Verifier | None]:
    """The arguments of a verb that reads a candidate, the API reader, and the store's trust:
    `--private` (the repository is private now, its store read through its privacy and the job's token) or
    the attestation signer, exactly as store_transport's readers take them."""
    parser = argparse.ArgumentParser(prog="store_publish.py", add_help=False)
    for name in positional:
        parser.add_argument(name)
    parser.add_argument("--signer-workflow"), parser.add_argument("--signer-digest")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or None
    api = os.environ.get(store_transport.API_ENV, store_replica.API)

    def get(path: str) -> tuple[int, dict]:
        return store_transport.api_get(api, path, token)
    try:
        return args, get, store_transport._verifier(args, get)
    except store_transport.Refused as refused:
        raise Refused(str(refused)) from refused


def _candidate(args: argparse.Namespace, get: Get, verifier: store_transport.Verifier | None, store: Path,
               tag: str) -> tuple[Candidate, dict]:
    token = os.environ.get("GITHUB_TOKEN") or None
    repository = "/".join(store_transport.full_name(args.repository))
    reader = store_transport.Reader(store_transport._registry(repository, token), store / ".cache", print)
    return released_candidate(reader, store, repository, args.key, tag, verifier, get, print)


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 3 and argv[0] == "check-release":
            check_release(Path(argv[1]), json.loads(Path(argv[2]).read_text()))
            print(f"store_publish: the Release equals the staged files ({argv[1]})")
            return 0
        if len(argv) == 3 and argv[0] == "stale-assets":
            for name in stale_assets(Path(argv[1]), json.loads(Path(argv[2]).read_text())):
                print(name)
            return 0
        if len(argv) == 5 and argv[0] == "check-destination":
            check_destination(Path(argv[1]), json.loads(Path(argv[2]).read_text()),
                              json.loads(Path(argv[3]).read_text()), argv[4])
            print(f"store_publish: the destination Release equals its staged files and is not Latest ({argv[1]})")
            return 0
        if len(argv) == 6 and argv[0] == "rehearse":
            writer = _token_file(store_replica.TOKEN_FILE_ENV, "the store package read")
            forge = _token_file(REHEARSAL_TOKEN_ENV, "the rehearsal's draft")
            registry = store_replica.Registry(store_replica.REGISTRY,
                                              f"{store_replica.ORG}/{store_replica.package_of(argv[1])}",
                                              (os.environ.get(store_replica.REGISTRY_USER_ENV,
                                                              store_replica.REGISTRY_USER_DEFAULT), writer))
            rehearse(registry, argv[1], argv[2], Path(argv[3]), argv[4], Path(argv[5]), live_gh(forge), print)
            return 0
        if argv[:1] == ["cut-tool"]:
            args, _get, verifier = _trusted(argv[1:], ("repository", "key", "package", "entry", "out"))
            repository = "/".join(store_transport.full_name(args.repository))
            with tempfile.TemporaryDirectory(prefix="store_publish.") as scratch:
                reader = store_transport.Reader(store_transport._registry(repository, os.environ.get("GITHUB_TOKEN") or None),
                                                Path(scratch) / ".cache", print)
                candidate = read_candidate(reader, Path(scratch), repository, args.key, verifier, print)
                found = cut_tool(candidate, args.package, args.entry, Path(args.out))
            print(f"store_publish: CUT-TOOL {repository} candidate-{args.key} {found['package']} {found['entry']} "
                  f"sha256 {found['digest']} record {found['record']} -> {args.out}")
            return 0
        if argv[:1] in (["stage"], ["image"], ["deployment"]):
            if not os.environ.get("GITHUB_TOKEN"):
                raise Refused("GITHUB_TOKEN is unset: a release reads its repository's own store with its own token")
            with tempfile.TemporaryDirectory(prefix="store_publish.") as scratch:
                if argv[0] == "stage":
                    args, get, verifier = _trusted(argv[1:], ("repository", "tag", "key", "record", "record_sha256",
                                                              "bench", "out"))
                    candidate, declared = _candidate(args, get, verifier, Path(scratch), args.tag)
                    measured = cut_benchmarks(Path(args.record).read_bytes(), args.record_sha256,
                                              candidate.repository.split("/", 1)[1], Path(args.bench))
                    staged, done = stage(candidate, declared, args.tag.removeprefix("v"), args.tag, Path(args.out), print,
                                         measured)
                    for name in staged:
                        print(f"store_publish: ASSET {name}")
                    for destination, rendered, names in done:
                        print(f"store_publish: DESTINATION {destination} {rendered}: {' '.join(names)}")
                    return 0
                if argv[0] == "image":
                    args, get, verifier = _trusted(argv[1:], ("repository", "tag", "key", "image"))
                    candidate, declared = _candidate(args, get, verifier, Path(scratch), args.tag)
                    owner, _name = store_transport.full_name(candidate.repository)
                    token = os.environ["GITHUB_TOKEN"]
                    target = store_replica.Registry(os.environ.get(store_transport.REGISTRY_ENV, store_replica.REGISTRY),
                                                    f"{owner}/{args.image}",
                                                    (os.environ.get("GITHUB_ACTOR", "x-access-token"), token))
                    manifest = publish_image(candidate, declared, target, args.image, args.tag, print)
                    print(f"store_publish: PUBLISHED ghcr.io/{owner}/{args.image}@{manifest}")
                    return 0
                args, get, verifier = _trusted(argv[1:], ("document", "repository", "image", "version"))
                document = json.loads(Path(args.document).read_bytes())
                args.key = str(document.get("candidate"))
                candidate, declared = _candidate(args, get, verifier, Path(scratch), f"v{args.version}")
                found = deployment(Path(args.document).read_bytes(), candidate, declared, args.image, args.version)
                print(f"image {found['manifest'].removeprefix('sha256:')}")
                for executable in found["executables"]:
                    print(f"executable {executable['entry']} {executable['digest']} {executable['build_id']}")
                return 0
    except (Refused, store_replica.Refused, store_transport.Refused) as refused:
        print(f"store_publish: REFUSED — {refused}", file=sys.stderr)
        return 1
    except SystemExit:
        pass
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
