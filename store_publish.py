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
        so the cut runs the bytes the candidate bound to its source verdict and builds nothing. A repository
        GITHUB_TOKEN reads as private, or cannot see, is read through its privacy (an upstream's rule), a public
        one through --signer-workflow. Token: GITHUB_TOKEN, the cut's PACKAGE_PEEK_TOKEN (it reads the
        repositories' visibility and their private stores)
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
    store_publish.py selftest
        every act against an in-process registry: the candidate pushed by store_transport's own writer

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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file
import store_image  # noqa: E402 — a sibling module, located from this file
import store_replica  # noqa: E402 — a sibling module, located from this file
import store_transport  # noqa: E402 — a sibling module, located from this file

TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
class Refused(Exception):
    """A check failed; the message names what differed."""


PUBLICATION_FORMAT = "coderoast.publication/1"
DESTINATION_PUBLICATION_FORMAT = "coderoast.destination-publication/1"
PACKAGES_FILE = "packages.yml"
RUN_FROM_CACHE = "run-from-cache"
VERSION_FIELD = "{version}"
TRANSPORT_KIND, ENTRY_KIND, OBJECT_KIND = "conan-transport", "entry", "object"
ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
TAG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IMAGE_MANIFEST = re.compile(r"^sha256:[0-9a-f]{64}$")
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
        if destination == owner or not TAG_NAME.match(rendered):
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
            if not isinstance(site, dict) or set(site) - keys or not ASSET_NAME.match(str(site.get("name"))):
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
    if not path.is_file() or artefact_store.file_sha256(path) != transport:
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
        artefact_store.extract(candidate.store / "objects" / output[artefact_store.TRANSPORT_FIELD], entry,
                               held[entry], target)
    except artefact_store.Refused as refused:
        raise Refused(str(refused)) from refused
    target.chmod(0o755)
    return {"package": package, "entry": entry, "digest": held[entry], "record": record["key"]}


def cut_tool_trust(get: Get, repository: str, attested: "store_transport.Verifier | None"
                   ) -> "store_transport.Verifier | None":
    """The trust a cut tool's store is read under: an upstream's (`store_transport.upstream_is_private`). The
    cut reads every repository's candidate with one token, PACKAGE_PEEK_TOKEN, which sees the private
    repositories: a repository it reads as private, or cannot see at all, has its store read through its
    privacy, and a public one through its attestations (`attested`, which a public store must have)."""
    if store_transport.upstream_is_private(get, repository):
        return None
    if attested is None:
        raise Refused(f"{repository} is public, and a public store is read only through its attestations: name "
                      f"--signer-workflow")
    return attested


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
    if not path.is_file() or artefact_store.file_sha256(path) != digest:
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
        if not path.is_file() or artefact_store.file_sha256(path) != digest:
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
            artefact_store.extract(candidate.store / "objects" / output[artefact_store.TRANSPORT_FIELD], row["entry"],
                                   held[row["entry"]], target)
        except artefact_store.Refused as refused:
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
    if not path.is_file() or artefact_store.file_sha256(path) != digest:
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
        if not archive.is_file() or artefact_store.file_sha256(archive) != output.get("archive") \
                or not IMAGE_MANIFEST.match(str(output.get("manifest"))):
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
    """The Release holds exactly the staged files: one more is an act nothing staged, one fewer a
    publication that did not happen."""
    attached = {asset["name"] for asset in release.get("assets", [])}
    staged = {path.name for path in out.iterdir() if path.is_file()}
    if attached != staged:
        raise Refused(f"the Release differs from the staged files: attached and never staged "
                      f"{sorted(attached - staged)}, staged and not attached {sorted(staged - attached)}")


def stale_assets(out: Path, release: dict) -> list[str]:
    """The assets a Release already holds that the staged files do not hold, sorted. A force-moved
    tag re-fires its Release workflow over a Release a previous run published (ADR-2.D1: a re-cut moves
    the tag, never deletes it): the publication then uploads the staged files over it, and removes
    exactly these, so the reading after it (check-release) holds the Release to the new staged files."""
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
    guard = store_transport.Guard(lambda: store_replica.expected_package("coderoasted", "pub-repo", "public"),
                                  lambda _expected: None)
    pushed = store_transport.push(registry, guard, store, "CodeRoasted/pub-repo", checkout, {}, malf, {}, out,
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
            arm("RED: one asset more on the Release than the staged files reds the reading",
                lambda: check_release(out / "release", {"assets": listed["assets"] + [{"name": "extra.tgz"}]}),
                "attached and never staged ['extra.tgz']")
            arm("RED: one asset fewer on the Release than the staged files reds the reading",
                lambda: check_release(out / "release", {"assets": listed["assets"][1:]}),
                f"staged and not attached [{want_own[0]!r}]")
            republished = {"assets": [{"name": want_own[0]}, {"name": "pub-repo-benchmark.json"},
                                      {"name": "golden.txt.sha256"}]}
            if stale_assets(out / "release", republished) != ["golden.txt.sha256", "pub-repo-benchmark.json"]:
                failures.append(f"CANDIDATE: a re-fired Release's stale assets read "
                                f"{stale_assets(out / 'release', republished)}, want the two nothing staged")
            if stale_assets(out / "release", listed) != [] or stale_assets(out / "release", {"assets": []}) != []:
                failures.append("CANDIDATE: a Release equal to the staged files, or holding none, reads a stale asset")
            destination = out / "destinations" / "dest-repo"
            hosted = {"assets": [{"name": name} for name in want_destination]}
            releases = [{"tagName": "v2.0.0", "isLatest": True}, {"tagName": "engine-v9.9.9", "isLatest": False}]
            arm("CANDIDATE: the destination Release read back equals its staged files and is not Latest",
                lambda: check_destination(destination, hosted, releases, "engine-v9.9.9"), None)
            arm("RED: one extra asset on the destination Release reds its after-reading",
                lambda: check_destination(destination, {"assets": hosted["assets"] + [{"name": "sift-extra"}]},
                                          releases, "engine-v9.9.9"), "attached and never staged ['sift-extra']")
            arm("RED: a destination Release wearing the Latest badge reds its after-reading",
                lambda: check_destination(destination, hosted, [{"tagName": "engine-v9.9.9", "isLatest": True}],
                                          "engine-v9.9.9"), "never claims its repository's badge")
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
    arm("CUT TOOL: a repository the cut's token reads as private, or cannot see (404), is read through its privacy",
        lambda: [cut_tool_trust(lambda path: (404, {}), "o/hidden", None),
                 cut_tool_trust(lambda path: (200, {"visibility": "private"}), "o/closed", None)], None)
    arm("RED: a visible public repository's cut tool is read only through its attestations",
        lambda: cut_tool_trust(lambda path: (200, {"visibility": "public"}), "o/open", None),
        "read only through its attestations")
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
    ingesting["ingest"]["windows.yml"] = {"artifact": "win", "executable": "win.exe",
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
        except (Refused, store_replica.Refused, artefact_store.Refused) as refused:
            if refusal is None or refusal not in str(refused):
                failures.append(f"{label}: refused with {refused}, want "
                                f"{'success' if refusal is None else repr(refusal)}")
            return None
        if refusal is not None:
            failures.append(f"{label}: succeeded, want a refusal containing {refusal!r}")
        return result

    with tempfile.TemporaryDirectory() as scratch:
        servers: list = []
        try:
            candidate_arms(arm, failures, Path(scratch) / "candidate", servers)
        finally:
            for server in servers:
                server.shutdown()

    for failure in failures:
        print(f"store_publish selftest FAILED: {failure}")
    print(f"store_publish selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


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
        if argv[:1] == ["cut-tool"]:
            args, get, verifier = _trusted(argv[1:], ("repository", "key", "package", "entry", "out"))
            repository = "/".join(store_transport.full_name(args.repository))
            if not args.private:
                verifier = cut_tool_trust(get, repository, verifier)
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
