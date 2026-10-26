#!/usr/bin/env python3
"""A release job's whole act on the store: fetch this repository's signed slice and every object it names, verify, stage or push.

DN-142.D9: a tag publishes; it builds nothing. `coderoast-publish.yml` runs this after `attest`
proved the tagged commit is the one the step-0 record judged, and hands it that record's digest:
1. the slice `slice-<record digest>` is read from ghcr.io/coderoasted/store-<repository> (both
   layers verified against their digests);
2. its signature verifies under the writer's key for this repository (store_manifest.verify), and
   the slice names the very record `attest` attested — a slice of another step-0 run is refused,
   as is one whose visibility (read from GitHub at step 0) differs from this repository's own now
   (DN-142.D24: two producers of the one fact that decides whether sources may cross);
3. each object it lists is fetched by its content digest and verified;
4. a `conan-transport` asset is staged as is, an `entry` asset (the entry's own bytes, its object
   named by its entry digest, store_manifest.verify) with its `.sha256` file written FROM that
   digest, an `object` asset as is and with no sidecar (the signed slice is its evidence); an
   `oci-image` asset is never a Release file: `image` publishes it; a row whose DESTINATION is
   another repository's Release (DN-142.D25 (2)) is never staged on this one;
5. the slice and its signature are staged beside them, so the Release carries its own evidence;
6. after the upload, the Release's asset names must equal the staged files, both ways (check-release).
`stage-destinations` is the same act for every FOREIGN Release the slice binds (DN-142.D25 (2)):
the destination slice and its signature are fetched by the digests the verified owner slice names,
verified (store_manifest.verify_destination: the writer's signature, the owner slice's record,
manifest, version and tag, its rows the projection of the owner's rows for that destination), each
object fetched and verified, and the files staged under `<out dir>/<destination>/` with the
destination slice and its signature; `<out dir>/destinations.tsv` lists `<destination> <tag>
<visibility>` for the workflow, which mints and reads back the annotated tag, creates the Release
`--verify-tag --latest=false` and attaches them with the DESTINATION credential only, then reads the
Release back (check-destination: its assets equal the staged files both ways, and it is not
`Latest`) and the destination's visibility (equal to the one step 0 read).
`image` (`coderoast-publish-image.yml`, N360 plan C1) reads the same verified slice, fetches the
image's OCI archive by its object digest, judges it (store_image.judge_archive: one linux/amd64
image whose manifest digest is the slice's), pushes every blob and the manifest verbatim to
ghcr.io/coderoasted/<image>, so the registry's digest is the one step 0 stored, and tags it with
the release tag, never moving a tag that already names another digest.
Nothing reaches the output directory or the registry unverified, and nothing here compiles, tests
or packs.

    store_publish.py stage <repository> <visibility> <step-0 record digest> <allowed signers> <out dir>
        Token: GITHUB_TOKEN (the repository's own, `packages: read`); <visibility> is the job's own
        reading of its repository (`github.event.repository.visibility`)
    store_publish.py check-release <out dir> <release.json>
        the reading after the publish (DN-142.D24 (3)): the Release's asset names, from
        `gh release view --json assets`, equal the staged files, both ways
    store_publish.py stale-assets <out dir> <release.json>
        before a re-publication over an existing Release (a force-moved tag re-fires it): one line per
        asset name the Release holds that the staged files do not, for the job to remove
    store_publish.py stage-destinations <repository> <visibility> <step-0 record digest> <allowed signers> <out dir>
        Token: GITHUB_TOKEN (the OWNER's, `packages: read`): every destination Release the owner
        slice binds, staged and listed in <out dir>/destinations.tsv
    store_publish.py check-destination <out dir>/<destination> <release.json> <releases.json> <tag>
        check-release, and `gh release list --json tagName,isLatest` lists <tag> once, not Latest
    store_publish.py image <repository> <visibility> <step-0 record digest> <allowed signers> <image> <tag>
        Token: GITHUB_TOKEN (`packages: write` on the image's package, which the repository's own
        store package grants it Read beside); prints the pushed `ghcr.io/coderoasted/<image>@sha256:…`
    store_publish.py deployment <slice> <signature> <allowed signers> <repository> <image> <version>
        what a deploy runs, from the Release's own signed slice: `image <manifest hex>`, then one
        `executable <entry> <sha256> <build-id>` line per executable the image carries
    store_publish.py rehearse <repository> <step-0 record digest> <allowed signers> <candidate> <out dir>
        step 0's rehearsal of this act before any tag (ROADMAP N393): the repository's visibility read
        from GitHub, `stage` and `stage-destinations` into <out dir>, ONE draft pre-release named by the
        candidate created with the staged files, read back by check-release, then deleted. Tokens, each
        from a FILE: MALF_STORE_TOKEN_FILE (the writer's, reads the store package) and
        MALF_STORE_REHEARSAL_TOKEN_FILE (`contents: write` on the releasing repositories, for the draft)
    store_publish.py selftest
        the whole act against an in-process registry seeded by the writer's own functions

Exit 0 staged or pushed, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_image  # noqa: E402 — a sibling module, located from this file
import store_manifest  # noqa: E402 — a sibling module, located from this file
import store_replica  # noqa: E402 — a sibling module, located from this file

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


def stage(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
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


def stage_destinations(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
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


def publish_image(registry: store_replica.Registry, target: store_replica.Registry, repository: str,
                  visibility: str, record_hex: str, allowed: Path, image: str, tag: str,
                  say: Callable[[str], None]) -> str:
    """Push the slice's image `image` verbatim to `target` and tag it `tag`; its manifest digest."""
    if not TAG.match(tag):
        raise Refused(f"{tag!r} is not a release tag vMAJOR.MINOR.PATCH")
    _body, assets, _slice, _signature = verified_slice(registry, repository, visibility, record_hex, allowed)
    found = [asset for asset in assets if asset["kind"] == store_manifest.IMAGE_KIND and asset["name"] == image]
    if len(found) != 1:
        raise Refused(f"the slice of {repository} holds {len(found)} image(s) named {image}, not one")
    asset = found[0]
    with tempfile.TemporaryDirectory() as scratch:
        archive = fetched(registry, asset, Path(scratch))
        try:
            manifest, _entries = store_image.judge_archive(archive)
        except store_image.Refused as refused:
            raise Refused(f"{image}: {refused}") from refused
        if manifest != asset["manifest"]:
            raise Refused(f"{image}: the archive's manifest is {manifest}, and the slice names {asset['manifest']}")
        held = target.manifest_digest(tag)
        if held is not None and held != manifest:
            raise Refused(f"{target.name}:{tag} already names {held}: a release tag is never moved")
        with tarfile.open(archive, "r:*") as bundle:
            blobs = {member.name.rsplit("/", 1)[-1]: bundle.extractfile(member).read()
                     for member in bundle.getmembers() if member.isfile() and member.name.startswith("blobs/sha256/")}
        body = blobs[manifest.removeprefix("sha256:")]
        named = json.loads(body)
        for digest in [named["config"]["digest"], *(layer["digest"] for layer in named["layers"])]:
            hex_digest = digest.removeprefix("sha256:")
            try:
                target.push_blob(hex_digest, blobs[hex_digest])
            except store_replica.Refused as refused:
                raise Refused(f"{image}: {refused}") from refused
        if held is None:
            try:
                target.put_manifest(tag, body)
            except store_replica.Refused as refused:
                raise Refused(f"{image}: {refused}") from refused
        if target.manifest_digest(tag) != manifest:
            raise Refused(f"{target.name}:{tag} did not read back as {manifest}")
    say(f"store_publish: IMAGE {target.name}@{manifest} tagged {tag}")
    return manifest


def deployment(slice_bytes: bytes, signature: Path, allowed: Path, repository: str, image: str,
               version: str) -> dict:
    """What a deploy of `image` at `version` runs, read from the Release's own signed slice: the
    image's manifest digest and each executable it carries with its file digest and GNU build-id.
    Refused unless the writer signed the slice for this repository and version (DN-142.D15)."""
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "slice.sig").write_bytes(signature.read_bytes())
        try:
            assets = store_manifest.verify(slice_bytes, Path(scratch) / "slice.sig", allowed, repository)
        except store_manifest.Refused as refused:
            raise Refused(str(refused)) from refused
    body = json.loads(slice_bytes)
    if body.get("version") != version:
        raise Refused(f"the Release's slice is for version {body.get('version')!r}, and the deploy names {version!r}")
    found = [asset for asset in assets if asset["kind"] == store_manifest.IMAGE_KIND and asset["name"] == image]
    if len(found) != 1 or not found[0].get("executables"):
        raise Refused(f"the slice of {repository} {version} holds {len(found)} image(s) named {image} with its "
                      f"executables, not one")
    return {"manifest": found[0]["manifest"], "executables": found[0]["executables"]}


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
    staged = stage(registry, repository, str(visibility), record_hex, allowed, release_dir, say)
    stage_destinations(registry, repository, str(visibility), record_hex, allowed, out / "destinations", say)
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
                         lambda: stage(registry, "pub-repo", "public", record_hex, allowed, out, quiet), None)
            want = ["pub_lib-9.9.9.tgz", "pub-repo-9.9.9.slice.json", "pub-repo-9.9.9.slice.json.sig"]
            if staged != want:
                failures.append(f"staged {staged}, want {want}: the destination stages only the rows it owns")
            owned_private = owned_slices["priv-repo"]
            _fake, owned_registry = registry_with(owned_private, signed(owned_private, "writer"))
            owned_out = root / "owned"
            staged_owned = arm("an entry destined to its owner's own Release is staged with its .sha256",
                               lambda: stage(owned_registry, "priv-repo", "private", record_hex, allowed,
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
                lambda: stage(registry, "pub-repo", "private", record_hex, allowed, root / "o8", quiet),
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
                lambda: stage(registry, "pub-repo", "public", "c" * 64, allowed, root / "o2", quiet), "HTTP 404")
            arm("the slice is verified for the repository that asks, not any other",
                lambda: stage(registry, "priv-repo", "private", record_hex, allowed, root / "o3", quiet),
                "not 'priv-repo''s")

            private = slices["priv-repo"]
            _fake, registry = registry_with(private, signed(private, "writer"))
            said: list[str] = []
            staged_private = arm("an image asset and a row destined to a foreign Release are never staged on "
                                 "the owner's Release",
                                 lambda: stage(registry, "priv-repo", "private", record_hex, allowed, root / "o9",
                                               said.append), None)
            if staged_private != ["bench.json", "golden.txt", "priv_tools-9.9.9.tgz", "sbom.json",
                                  "priv-repo-9.9.9.slice.json", "priv-repo-9.9.9.slice.json.sig"] or not all(any(
                    line.startswith(f"store_publish: FOREIGN {name} belongs on pub-repo's engine-v9.9.9")
                    for line in said) for name in ("tool-linux-x64", "win.exe")):
                failures.append(f"the owner staged {staged_private}, saying {said}")
            elsewhere = root / "destinations"
            placed_rows = arm("the owner stages each destination Release it binds from its OWN store package: the "
                              "projected rows, verified, with the destination slice and its signature",
                              lambda: stage_destinations(registry, "priv-repo", "private", record_hex, allowed,
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
                lambda: stage_destinations(corrupt_registry, "priv-repo", "private", record_hex, allowed,
                                           root / "corrupt", quiet), "the destination slice of pub-repo")
            if (root / "corrupt" / "pub-repo").exists():
                failures.append("a refused destination staging left a directory behind")
            target_fake = store_replica.Fake()
            target_server, target_base = store_replica._fake_server(target_fake)
            servers.append(target_server)
            target = store_replica.Registry(target_base, "coderoasted/srv-image", ("user", "token"))
            pushed = arm("the slice's image is pushed verbatim and tagged: the registry's digest is the one "
                         "step 0 stored",
                         lambda: publish_image(registry, target, "priv-repo", "private", record_hex, allowed,
                                               "srv-image", "v9.9.9", quiet), None)
            if pushed != image_manifest or target_fake.tags.get("v9.9.9") != image_manifest:
                failures.append(f"the pushed image is {pushed}, tagged {target_fake.tags.get('v9.9.9')}, "
                                f"want {image_manifest}")
            arm("publishing the same image under the same tag again is idempotent",
                lambda: publish_image(registry, target, "priv-repo", "private", record_hex, allowed,
                                      "srv-image", "v9.9.9", quiet), None)
            other = b'{"schemaVersion":2,"note":"another image"}'
            target_fake.manifests["sha256:" + hashlib.sha256(other).hexdigest()] = other
            target_fake.tags["v9.9.8"] = "sha256:" + hashlib.sha256(other).hexdigest()
            arm("a release tag already naming another image is never moved",
                lambda: publish_image(registry, target, "priv-repo", "private", record_hex, allowed,
                                      "srv-image", "v9.9.8", quiet), "a release tag is never moved")
            arm("an image the slice does not hold is refused",
                lambda: publish_image(registry, target, "priv-repo", "private", record_hex, allowed,
                                      "other-image", "v9.9.9", quiet), "holds 0 image(s) named other-image")
            arm("a tag that is not a release tag is refused",
                lambda: publish_image(registry, target, "priv-repo", "private", record_hex, allowed,
                                      "srv-image", "latest", quiet), "is not a release tag")
            arm("an image of a slice signed by another key is refused before any push",
                lambda: publish_image(registry_with(private, signed(private, "other"))[1], target, "priv-repo",
                                      "private", record_hex, allowed, "srv-image", "v9.9.7", quiet),
                "does not verify")
            if "v9.9.7" in target_fake.tags:
                failures.append("a refused image publication tagged the registry anyway")
            private_path = root / "priv-repo-slice.json"
            private_path.write_bytes(private)
            private_signature = root / "priv-repo-slice.json.sig"
            private_signature.write_bytes(signed(private, "writer"))
            found = arm("a deploy reads the image's digest and its executables' identity from the Release's "
                        "signed slice",
                        lambda: deployment(private, private_signature, allowed, "priv-repo", "srv-image", "9.9.9"),
                        None)
            if found is not None and (found["manifest"] != image_manifest or
                                      [row["entry"] for row in found["executables"]] != ["bin/server"]):
                failures.append(f"the deployment read {found}")
            arm("a deploy of another version than the slice's is refused",
                lambda: deployment(private, private_signature, allowed, "priv-repo", "srv-image", "9.9.8"),
                "the deploy names '9.9.8'")
            other_signature = root / "priv-repo-slice.other.sig"
            other_signature.write_bytes(signed(private, "other"))
            arm("a slice the writer did not sign deploys nothing",
                lambda: deployment(private, other_signature, allowed, "priv-repo", "srv-image", "9.9.9"),
                "does not verify")

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
                lambda: stage(registry, "pub-repo", "public", record_hex, allowed, root / "o4", quiet), "does not verify")
            if (root / "o4").exists() and any((root / "o4").iterdir()):
                failures.append("a refused stage left files behind")

            _fake, registry = registry_with(pub, signed(pub, "writer"), record_tag="d" * 64)
            arm("a genuine slice served under another record's tag is refused: it names its own record",
                lambda: stage(registry, "pub-repo", "public", "d" * 64, allowed, root / "o5", quiet),
                "attest attested")

            fake, registry = registry_with(pub, signed(pub, "writer"))
            fake.corrupt_blobs = True
            arm("an object whose served bytes differ from its digest is refused",
                lambda: stage(registry, "pub-repo", "public", record_hex, allowed, root / "o6", quiet), "hashes to")

            fake, registry = registry_with(pub, signed(pub, "writer"))
            fake.tags[store_replica.slice_tag(record_hex)] = fake.tags[store_replica.SENTINEL_TAG]
            arm("a slice tag that holds no slice and signature is refused",
                lambda: stage(registry, "pub-repo", "public", record_hex, allowed, root / "o7", quiet),
                "not a slice and its signature")
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

def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 3 and argv[0] == "check-release":
            check_release(Path(argv[1]), json.loads(Path(argv[2]).read_text()))
            print(f"store_publish: the Release equals the slice ({argv[1]})")
            return 0
        if len(argv) == 3 and argv[0] == "stale-assets":
            for name in stale_assets(Path(argv[1]), json.loads(Path(argv[2]).read_text())):
                print(name)
            return 0
        if len(argv) == 5 and argv[0] == "check-destination":
            check_destination(Path(argv[1]), json.loads(Path(argv[2]).read_text()),
                              json.loads(Path(argv[3]).read_text()), argv[4])
            print(f"store_publish: the destination Release equals its slice and is not Latest ({argv[1]})")
            return 0
        if len(argv) == 7 and argv[0] == "deployment":
            found = deployment(Path(argv[1]).read_bytes(), Path(argv[2]), Path(argv[3]), argv[4], argv[5], argv[6])
            print(f"image {found['manifest'].removeprefix('sha256:')}")
            for executable in found["executables"]:
                print(f"executable {executable['entry']} {executable['digest']} {executable['build_id']}")
            return 0
        if len(argv) == 7 and argv[0] == "image":
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise Refused("GITHUB_TOKEN is unset: a publish job pushes with its repository's own token")
            actor = os.environ.get("GITHUB_ACTOR", "x-access-token")
            registry = store_replica.Registry(store_replica.REGISTRY,
                                              f"{store_replica.ORG}/{store_replica.package_of(argv[1])}",
                                              (actor, token))
            target = store_replica.Registry(store_replica.REGISTRY, f"{store_replica.ORG}/{argv[5]}", (actor, token))
            manifest = publish_image(registry, target, argv[1], argv[2], argv[3], Path(argv[4]), argv[5], argv[6], print)
            print(f"store_publish: PUBLISHED ghcr.io/{store_replica.ORG}/{argv[5]}@{manifest}")
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
        if len(argv) == 6 and argv[0] in ("stage", "stage-destinations"):
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise Refused("GITHUB_TOKEN is unset: a publish job reads its repository's own store package")
            package = store_replica.package_of(argv[1])
            registry = store_replica.Registry(store_replica.REGISTRY, f"{store_replica.ORG}/{package}",
                                              (os.environ.get("GITHUB_ACTOR", "x-access-token"), token))
            if argv[0] == "stage":
                for name in stage(registry, argv[1], argv[2], argv[3], Path(argv[4]), Path(argv[5]), print):
                    print(f"store_publish: ASSET {name}")
                return 0
            for destination, tag, seen, names in stage_destinations(registry, argv[1], argv[2], argv[3],
                                                                     Path(argv[4]), Path(argv[5]), print):
                print(f"store_publish: DESTINATION {destination} {tag} ({seen}): {' '.join(names)}")
            return 0
    except (Refused, store_replica.Refused) as refused:
        print(f"store_publish: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
