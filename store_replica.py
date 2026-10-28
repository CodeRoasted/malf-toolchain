#!/usr/bin/env python3
"""The store's read replica on ghcr: the writer pushes a stored object by digest, a release job reads it back by digest.

DN-142.D6 (the remote transport, ruled 2026-10-10, R4 and R5 measured): one PRIVATE ghcr package per
source repository, `ghcr.io/coderoasted/store-<repository>`, written only by the writer account's
token, read by that repository's `GITHUB_TOKEN` alone. An object is one OCI artifact manifest whose
one layer is the object's bytes, tagged `sha256-<content digest>`, so a reader holding the content
digest from a signed manifest slice needs no other name. Every byte is checked against a digest
computed here: the object against its name in the local store before the push, the registry's
manifest digest against the canonical manifest's own, an existing tag against the manifest this
module would write, and a fetched layer against the digest asked for.

THE PACKAGE GUARD, BEFORE AND AFTER EVERY WRITE: the GitHub API must answer the visibility and
the link THE ONE RULE derives (`expected_package`, DN-142.D31 (3) and the Founder's item 6). A
repository that does not own its store is step 0's replica (DN-142.D6): `private`, linked to no
repository. A repository that owns its store (its candidate writes `store-<repository>`) has a
package of the repository's own visibility, LINKED to that repository: public for a public one,
private for a private one. Pharos's A1c reads the same rule from this module, so the writer and the
probe cannot drift. A package that does not exist yet is created by a SENTINEL push first — an empty
artifact carrying no product byte — and is then guarded before any object reaches it, because a
package that came up `internal` or `public` would expose every byte pushed to it. No REST endpoint
sets a container package's visibility, so a refusal names the settings page and writes nothing
more; setting it by hand is the remedy, and the next run verifies it.

    store_replica.py push [--own <public|private>] <store dir> <repository> <object digest>...
        the writer's act: each object of the local store, by its 64-hex name, to
        ghcr.io/coderoasted/store-<repository>, the package guarded before and after; an object
        already replicated is verified, never rewritten. `--own` names a repository that owns its
        store and that repository's visibility; without it the package is step 0's replica. Token:
        the file MALF_STORE_TOKEN_FILE (the writer account's classic PAT, `write:packages`), never
        an argument or an echo.
    store_replica.py push-slice [--own <public|private>] <repository> <step-0 record digest> <slice> <signature>
        the writer's act: the repository's signed manifest slice for that step-0 record, under
        the tag slice-<record digest>, the package guarded the same way; never rewritten
    store_replica.py fetch <repository> <object digest> <out file>
        a reader's act: the object by its digest, written only after its SHA-256 is verified.
        Token: GITHUB_TOKEN (the repository's own, `packages: read`).
    store_replica.py selftest
        every refusal against an in-process registry and API, and the round trip; no network

Exit 0 done, 1 refused (the reason on stderr), 2 a usage error.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REGISTRY = "https://ghcr.io"
API = "https://api.github.com"
ORG = "coderoasted"
PACKAGE_PREFIX = "store-"
REPOSITORY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DIGEST_HEX = re.compile(r"^[0-9a-f]{64}$")
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
OBJECT_TYPE = "application/vnd.coderoast.store.object.v1"
SENTINEL_TYPE = "application/vnd.coderoast.store.sentinel.v1"
SENTINEL_TAG = "store-sentinel"
SLICE_TYPE = "application/vnd.coderoast.store.slice.v1"
SLICE_MEDIA = "application/vnd.coderoast.store.slice.v1+json"
SIGNATURE_MEDIA = "application/vnd.coderoast.store.signature.v1"
EMPTY_BYTES = b"{}"
EMPTY_DIGEST = "sha256:" + hashlib.sha256(EMPTY_BYTES).hexdigest()
CHUNK = 1 << 20
TOKEN_FILE_ENV = "MALF_STORE_TOKEN_FILE"
REGISTRY_USER_ENV = "MALF_STORE_REGISTRY_USER"
REGISTRY_USER_DEFAULT = "coderoast-dev"


class Refused(Exception):
    """A check failed: nothing more is written, and the reason names what differed."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_of(repository: str) -> str:
    if not REPOSITORY_NAME.match(repository):
        raise Refused(f"{repository!r} is not a repository name")
    return PACKAGE_PREFIX + repository


def object_tag(hex_digest: str) -> str:
    return f"sha256-{hex_digest}"


def slice_tag(record_hex: str) -> str:
    return f"slice-{record_hex}"


def slice_manifest(slice_bytes: bytes, signature: bytes) -> bytes:
    """A repository's signed slice for one step-0 record: the slice and its signature, two layers."""
    return canonical({
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": SLICE_TYPE,
        "config": {"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)},
        "layers": [{"mediaType": SLICE_MEDIA, "digest": "sha256:" + hashlib.sha256(slice_bytes).hexdigest(),
                    "size": len(slice_bytes)},
                   {"mediaType": SIGNATURE_MEDIA, "digest": "sha256:" + hashlib.sha256(signature).hexdigest(),
                    "size": len(signature)}]})


def object_manifest(hex_digest: str, size: int, artifact: str = OBJECT_TYPE) -> bytes:
    """The one manifest an object is stored under: canonical bytes, so equal objects give equal
    manifest digests and a re-push is recognised as the same write."""
    return canonical({
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": artifact,
        "config": {"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)},
        "layers": [{"mediaType": artifact, "digest": f"sha256:{hex_digest}", "size": size}]})


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def _send(request: urllib.request.Request) -> Response:
    """One HTTP exchange; a 4xx/5xx is a Response, never an exception, and redirects are not followed."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 — the handler's own signature
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=300) as answer:
            return Response(answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read())
    except urllib.error.HTTPError as answer:
        return Response(answer.code, {k.lower(): v for k, v in answer.headers.items()}, answer.read())


@dataclass
class Registry:
    """An OCI distribution client for one repository path, Bearer-challenge auth when asked."""

    base: str
    name: str
    credentials: tuple[str, str] | None = None
    tokens: dict[str, str] = field(default_factory=dict)

    def _authorization(self, challenge: str, actions: str) -> str:
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        scope = f"repository:{self.name}:{actions}"
        if scope not in self.tokens:
            query = urllib.parse.urlencode({"service": fields.get("service", ""), "scope": scope})
            request = urllib.request.Request(f"{fields['realm']}?{query}")
            if self.credentials:
                import base64
                pair = f"{self.credentials[0]}:{self.credentials[1]}".encode()
                request.add_header("Authorization", "Basic " + base64.b64encode(pair).decode())
            answer = _send(request)
            if answer.status != 200:
                raise Refused(f"the token endpoint refused {scope}: HTTP {answer.status}")
            body = json.loads(answer.body)
            self.tokens[scope] = body.get("token") or body.get("access_token") or ""
        return f"Bearer {self.tokens[scope]}"

    def call(self, method: str, path: str, actions: str, headers: dict[str, str] | None = None,
             data: bytes | None = None, url: str | None = None) -> Response:
        target = url or f"{self.base}/v2/{self.name}/{path}"
        auth = None
        for _attempt in range(2):
            request = urllib.request.Request(target, data=data, method=method)
            for key, value in (headers or {}).items():
                request.add_header(key, value)
            if auth:
                request.add_header("Authorization", auth)
            answer = _send(request)
            challenge = answer.headers.get("www-authenticate", "")
            if answer.status == 401 and challenge.lower().startswith("bearer") and auth is None:
                auth = self._authorization(challenge, actions)
                continue
            return answer
        return answer

    def manifest_digest(self, reference: str) -> str | None:
        """The digest the registry holds at a tag or digest, None when it holds nothing there."""
        answer = self.call("HEAD", f"manifests/{reference}", "pull", {"Accept": OCI_MANIFEST})
        if answer.status == 404:
            return None
        if answer.status != 200:
            raise Refused(f"HEAD manifest {self.name}:{reference}: HTTP {answer.status}")
        return answer.headers.get("docker-content-digest")

    def push_blob(self, hex_digest: str, data: bytes) -> None:
        if self.call("HEAD", f"blobs/sha256:{hex_digest}", "pull,push").status == 200:
            return
        start = self.call("POST", "blobs/uploads/", "pull,push", {"Content-Length": "0"}, b"")
        if start.status != 202 or "location" not in start.headers:
            raise Refused(f"blob upload start on {self.name}: HTTP {start.status}")
        location = urllib.parse.urljoin(f"{self.base}/", start.headers["location"])
        separator = "&" if "?" in location else "?"
        done = self.call("PUT", "", "pull,push", {"Content-Type": "application/octet-stream",
                                                   "Content-Length": str(len(data))}, data,
                         url=f"{location}{separator}digest=sha256:{hex_digest}")
        if done.status != 201:
            raise Refused(f"blob upload of sha256:{hex_digest} on {self.name}: HTTP {done.status}")
        answered = done.headers.get("docker-content-digest")
        if answered and answered != f"sha256:{hex_digest}":
            raise Refused(f"the registry stored blob {answered} for sha256:{hex_digest}")

    def put_manifest(self, tag: str, body: bytes) -> str:
        expected = "sha256:" + hashlib.sha256(body).hexdigest()
        answer = self.call("PUT", f"manifests/{tag}", "pull,push", {"Content-Type": OCI_MANIFEST}, body)
        if answer.status != 201:
            raise Refused(f"manifest PUT {self.name}:{tag}: HTTP {answer.status} "
                          f"{answer.body[:300].decode('utf-8', 'replace')}")
        answered = answer.headers.get("docker-content-digest")
        if answered != expected:
            raise Refused(f"manifest {self.name}:{tag}: the registry answered {answered}, the bytes "
                          f"written hash to {expected}")
        return expected

    def layers(self, tag: str) -> list[dict]:
        answer = self.call("GET", f"manifests/{tag}", "pull", {"Accept": OCI_MANIFEST})
        if answer.status != 200:
            raise Refused(f"GET manifest {self.name}:{tag}: HTTP {answer.status}")
        return json.loads(answer.body).get("layers", [])

    def blob(self, hex_digest: str) -> bytes:
        """A blob's bytes, only once they hash to the digest asked for; a redirect is followed bare."""
        blob = self.call("GET", f"blobs/sha256:{hex_digest}", "pull")
        if blob.status in (301, 302, 303, 307, 308):
            blob = _send(urllib.request.Request(blob.headers["location"]))
        if blob.status != 200:
            raise Refused(f"GET blob sha256:{hex_digest} on {self.name}: HTTP {blob.status}")
        got = hashlib.sha256(blob.body).hexdigest()
        if got != hex_digest:
            raise Refused(f"the blob served for sha256:{hex_digest} hashes to sha256:{got}")
        return blob.body

    def get_layer(self, hex_digest: str, out: Path) -> None:
        """The object's bytes from its manifest's one layer, written to `out` only once verified."""
        tag = object_tag(hex_digest)
        layers = self.layers(tag)
        if len(layers) != 1 or layers[0].get("digest") != f"sha256:{hex_digest}":
            raise Refused(f"{self.name}:{tag} does not name sha256:{hex_digest} as its one layer")
        data = self.blob(hex_digest)
        staged = out.with_name(f".{out.name}.partial")
        staged.write_bytes(data)
        staged.replace(out)

    def get_slice(self, record_hex: str) -> tuple[bytes, bytes]:
        """(the slice, its signature) for one step-0 record, each verified against its layer digest.
        The signature itself is the publish job's to verify (store_manifest.verify)."""
        tag = slice_tag(record_hex)
        layers = self.layers(tag)
        media = [layer.get("mediaType") for layer in layers]
        if media != [SLICE_MEDIA, SIGNATURE_MEDIA]:
            raise Refused(f"{self.name}:{tag} holds layers {media}, not a slice and its signature")
        slice_layer, signature_layer = (layer["digest"].split(":", 1)[1] for layer in layers)
        return self.blob(slice_layer), self.blob(signature_layer)


def github_guard(api: str, org: str, token: str) -> Callable[[str], dict | None]:
    """The package's GitHub view: its JSON, or None when the API says it does not exist."""

    def view(package: str) -> dict | None:
        request = urllib.request.Request(f"{api}/orgs/{org}/packages/container/{package}")
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/vnd.github+json")
        answer = _send(request)
        if answer.status == 404:
            return None
        if answer.status != 200:
            raise Refused(f"the package API answered HTTP {answer.status} for {package}; its visibility "
                          f"cannot be verified, so nothing is written")
        return json.loads(answer.body)

    return view


VISIBILITIES = ("public", "private")


@dataclass(frozen=True)
class Expected:
    """What the package guard requires of a store package: its visibility, and the repository it is
    linked to (`owner/name`, lower case), or None for a package linked to no repository."""

    visibility: str
    linked: str | None


STEP0_REPLICA = Expected("private", None)


def expected_package(owner: str, repository: str, own_visibility: str | None) -> Expected:
    """THE ONE RULE (DN-142.D31 (3), item 6): `own_visibility` is None for a repository whose store is
    step 0's replica (private, unlinked, DN-142.D6), else that repository's own visibility, and its
    package then has that visibility and is linked to it."""
    if own_visibility is None:
        return STEP0_REPLICA
    if own_visibility not in VISIBILITIES:
        raise Refused(f"{owner}/{repository} is {own_visibility!r}: a repository owning its store is public "
                      f"or private, and no rule names a package for any other visibility")
    return Expected(own_visibility, f"{owner}/{repository}".lower())


def package_defect(seen: dict | None, package: str, expected: Expected) -> str | None:
    """Why the GitHub view `seen` of `package` breaks `expected`, else None."""
    if seen is None:
        return f"{package} does not exist"
    if seen.get("visibility") != expected.visibility:
        return f"{package} is {seen.get('visibility')!r}, not {expected.visibility!r}"
    linked = seen.get("repository")
    linked = str(linked.get("full_name", linked) if isinstance(linked, dict) else linked or "").lower() or None
    if linked != expected.linked:
        if expected.linked is None:
            return (f"{package} is linked to {linked}: a linked package lets that repository's workflows "
                    f"write it, and step 0's replica is linked to no repository")
        answered = "carries no `repository` field" if "repository" not in seen else f"names `repository` {seen['repository']!r}"
        return (f"{package} is linked to {linked or 'no repository'}, not {expected.linked} (the API's answer "
                f"{answered}): a repository that owns its store writes it with its own token, through that link")
    return None


def verify_package(view: Callable[[str], dict | None], org: str, package: str, expected: Expected) -> None:
    """Refuse unless the package exists with the visibility and the link `expected` names."""
    defect = package_defect(view(package), package, expected)
    if defect is None:
        return
    settings = f"https://github.com/orgs/{org}/packages/container/{package}/settings"
    if defect.endswith("does not exist"):
        raise Refused(f"{defect} after its sentinel push, so it cannot be verified")
    raise Refused(f"{defect}: set it in {settings} (no API sets visibility or link); nothing more is "
                  f"pushed until a run verifies it")


SOURCE_ANNOTATION = "org.opencontainers.image.source"


def sentinel_manifest(expected: Expected) -> bytes:
    """The empty artifact that creates a package; for a package linked to its repository it carries
    the `org.opencontainers.image.source` annotation ghcr links a new package by (DN-142.D31 (3))."""
    linked = {"annotations": {SOURCE_ANNOTATION: f"https://github.com/{expected.linked}"}} if expected.linked else {}
    return canonical({
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": SENTINEL_TYPE,
        "config": {"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)},
        "layers": [{"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)}], **linked})


def ensure_guarded(registry: Registry, view: Callable[[str], dict | None], org: str, package: str,
                   say: Callable[[str], None], expected: Expected = STEP0_REPLICA) -> None:
    """The package exists, with the visibility and link `expected` names, before any product byte reaches it."""
    if view(package) is None:
        registry.push_blob(EMPTY_DIGEST.split(":")[1], EMPTY_BYTES)
        registry.put_manifest(SENTINEL_TAG, sentinel_manifest(expected))
        say(f"store_replica: created {package} with an empty sentinel; guarding it before any object")
    verify_package(view, org, package, expected)


def replicate(store: Path, registry: Registry, view: Callable[[str], dict | None], org: str,
              package: str, digests: list[str], say: Callable[[str], None],
              expected: Expected = STEP0_REPLICA) -> None:
    """Push each named object of `store`, the package guarded before the first and after the last."""
    for hex_digest in digests:
        if not DIGEST_HEX.match(hex_digest):
            raise Refused(f"{hex_digest!r} is not an object digest (64 hex)")
    ensure_guarded(registry, view, org, package, say, expected)
    for hex_digest in digests:
        source = store / "objects" / hex_digest
        if not source.is_file():
            raise Refused(f"no object {hex_digest} in {store / 'objects'}")
        actual = sha256_file(source)
        if actual != hex_digest:
            raise Refused(f"the local object {source} hashes to {actual}, not its name")
        manifest = object_manifest(hex_digest, source.stat().st_size)
        manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        held = registry.manifest_digest(object_tag(hex_digest))
        if held is not None:
            if held != manifest_digest:
                raise Refused(f"{package}:{object_tag(hex_digest)} holds {held}, not the object's "
                              f"manifest {manifest_digest}: an object's name is never rewritten")
            say(f"store_replica: PRESENT {package} sha256:{hex_digest} (manifest {manifest_digest})")
            continue
        registry.push_blob(EMPTY_DIGEST.split(":")[1], EMPTY_BYTES)
        registry.push_blob(hex_digest, source.read_bytes())
        registry.put_manifest(object_tag(hex_digest), manifest)
        if registry.manifest_digest(object_tag(hex_digest)) != manifest_digest:
            raise Refused(f"{package}:{object_tag(hex_digest)} did not read back as {manifest_digest}")
        say(f"store_replica: PUSHED {package} sha256:{hex_digest} (manifest {manifest_digest})")
    verify_package(view, org, package, expected)


def replicate_slice(registry: Registry, view: Callable[[str], dict | None], org: str, package: str,
                    record_hex: str, slice_bytes: bytes, signature: bytes, say: Callable[[str], None],
                    expected: Expected = STEP0_REPLICA) -> None:
    """Push a repository's signed slice under `slice-<step-0 record digest>`, never rewritten."""
    if not DIGEST_HEX.match(record_hex):
        raise Refused(f"{record_hex!r} is not a step-0 record digest (64 hex)")
    ensure_guarded(registry, view, org, package, say, expected)
    manifest = slice_manifest(slice_bytes, signature)
    manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    held = registry.manifest_digest(slice_tag(record_hex))
    if held is not None:
        if held != manifest_digest:
            raise Refused(f"{package}:{slice_tag(record_hex)} holds {held}, not this slice's manifest "
                          f"{manifest_digest}: a record's slice is never rewritten")
        say(f"store_replica: PRESENT {package} {slice_tag(record_hex)}")
    else:
        registry.push_blob(EMPTY_DIGEST.split(":")[1], EMPTY_BYTES)
        for data in (slice_bytes, signature):
            registry.push_blob(hashlib.sha256(data).hexdigest(), data)
        registry.put_manifest(slice_tag(record_hex), manifest)
        if registry.manifest_digest(slice_tag(record_hex)) != manifest_digest:
            raise Refused(f"{package}:{slice_tag(record_hex)} did not read back as {manifest_digest}")
        say(f"store_replica: PUSHED {package} {slice_tag(record_hex)} (manifest {manifest_digest})")
    verify_package(view, org, package, expected)


# ── the selftest: an in-process registry and API, every refusal fired ──────────────────────────


@dataclass
class Fake:
    blobs: dict[str, bytes] = field(default_factory=dict)
    manifests: dict[str, bytes] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    package: dict | None = None
    created_visibility: str = "private"
    lie_manifest_digest: bool = False
    corrupt_blobs: bool = False
    bearer: str = "fake-bearer"


def _fake_server(fake: Fake) -> tuple[http.server.ThreadingHTTPServer, str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: ANN002 — silence the default access log
            return

        def _reply(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _authorized(self) -> bool:
            if self.path.startswith("/token") or self.path.startswith("/orgs/"):
                return True
            if self.headers.get("Authorization") == f"Bearer {fake.bearer}":
                return True
            host = f"http://{self.headers['Host']}"
            self._reply(401, b"", {"WWW-Authenticate":
                                   f'Bearer realm="{host}/token",service="fake",scope="x"'})
            return False

        def _route(self) -> None:
            if not self._authorized():
                return
            parsed = urllib.parse.urlparse(self.path)
            parts = parsed.path.split("/")
            if parsed.path.startswith("/token"):
                self._reply(200, canonical({"token": fake.bearer}))
                return
            if parsed.path.startswith("/orgs/"):
                if fake.package is None:
                    self._reply(404, b'{"message":"Package not found."}')
                else:
                    self._reply(200, canonical(fake.package))
                return
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else b""
            if "manifests" in parts:
                reference = parts[-1]
                if self.command == "PUT":
                    digest = "sha256:" + hashlib.sha256(data).hexdigest()
                    fake.manifests[digest] = data
                    if not reference.startswith("sha256:"):
                        fake.tags[reference] = digest
                    if fake.package is None:
                        fake.package = {"name": parts[3], "visibility": fake.created_visibility}
                    answered = ("sha256:" + "0" * 64) if fake.lie_manifest_digest else digest
                    self._reply(201, b"", {"Docker-Content-Digest": answered})
                    return
                digest = reference if reference.startswith("sha256:") else fake.tags.get(reference)
                if digest is None or digest not in fake.manifests:
                    self._reply(404)
                    return
                self._reply(200, fake.manifests[digest], {"Docker-Content-Digest": digest,
                                                          "Content-Type": OCI_MANIFEST})
                return
            if "uploads" in parts:
                if self.command == "POST":
                    self._reply(202, b"", {"Location": f"/v2/{parts[2]}/{parts[3]}/blobs/uploads/u1"})
                    return
                digest = urllib.parse.parse_qs(parsed.query)["digest"][0]
                if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
                    self._reply(400, b'{"errors":[{"code":"DIGEST_INVALID"}]}')
                    return
                fake.blobs[digest] = data
                self._reply(201, b"", {"Docker-Content-Digest": digest})
                return
            if "blobs" in parts:
                digest = parts[-1]
                if digest not in fake.blobs:
                    self._reply(404)
                    return
                body = fake.blobs[digest]
                self._reply(200, body[:-1] + b"X" if fake.corrupt_blobs and body else body)
                return
            self._reply(404)

        do_GET = do_HEAD = do_PUT = do_POST = _route

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, run: Callable[[], None], refusal: str | None) -> None:
        armed[0] += 1
        try:
            run()
        except Refused as refused:
            if refusal is None or refusal not in str(refused):
                failures.append(f"{label}: refused with {refused!r}, want "
                                f"{'success' if refusal is None else repr(refusal)}")
            return
        if refusal is not None:
            failures.append(f"{label}: succeeded, want a refusal containing {refusal!r}")

    with tempfile.TemporaryDirectory() as scratch:
        store = Path(scratch) / "store"
        (store / "objects").mkdir(parents=True)
        payload = b"a stored object\n" * 64
        good = hashlib.sha256(payload).hexdigest()
        (store / "objects" / good).write_bytes(payload)
        liar = hashlib.sha256(b"what the name claims").hexdigest()
        (store / "objects" / liar).write_bytes(b"other bytes")

        def scene(**knobs) -> tuple[Fake, Registry, Callable[[str], dict | None], list[str]]:
            fake = Fake(**knobs)
            server, base = _fake_server(fake)
            scenes.append(server)
            said: list[str] = []
            return fake, Registry(base, f"{ORG}/store-probe", ("user", "token")), \
                github_guard(base, ORG, "api-token"), said

        scenes: list[http.server.ThreadingHTTPServer] = []
        try:
            fake, registry, view, said = scene()
            arm("a new package: sentinel, guard, push, read back byte-equal",
                lambda: replicate(store, registry, view, ORG, "store-probe", [good], said.append), None)
            out = Path(scratch) / "fetched"
            arm("the reader fetches the object by digest", lambda: registry.get_layer(good, out), None)
            if not out.is_file() or out.read_bytes() != payload:
                failures.append("the fetched bytes differ from the stored object")
            if SENTINEL_TAG not in fake.tags or object_tag(good) not in fake.tags:
                failures.append(f"the registry holds tags {sorted(fake.tags)}, want the sentinel and the object")
            pushes = len(fake.manifests)
            arm("a re-push of the same object is recognised and writes nothing",
                lambda: replicate(store, registry, view, ORG, "store-probe", [good], said.append), None)
            if len(fake.manifests) != pushes or not any("PRESENT" in line for line in said):
                failures.append("a re-push wrote a manifest or did not report PRESENT")

            fake.tags[object_tag(good)] = next(d for d, b in fake.manifests.items() if SENTINEL_TYPE in b.decode())
            arm("a tag holding another manifest is refused, never rewritten",
                lambda: replicate(store, registry, view, ORG, "store-probe", [good], said.append),
                "never rewritten")

            _fake, registry, view, said = scene()
            arm("a local object that does not hash to its name is refused before any push",
                lambda: replicate(store, registry, view, ORG, "store-probe", [liar], said.append),
                "not its name")
            arm("a name that is not 64 hex is refused",
                lambda: replicate(store, registry, view, ORG, "store-probe", ["../x"], said.append),
                "not an object digest")

            fake, registry, view, said = scene(created_visibility="internal")
            arm("a package that came up internal is refused BEFORE any object reaches it",
                lambda: replicate(store, registry, view, ORG, "store-probe", [good], said.append),
                "not 'private'")
            if object_tag(good) in fake.tags:
                failures.append("an object was pushed into a package that is not private")

            fake, registry, view, said = scene()
            fake.package = {"name": "store-probe", "visibility": "private",
                            "repository": {"full_name": "CodeRoasted/coderoast-ipc"}}
            arm("a package linked to a repository is refused", lambda: replicate(
                store, registry, view, ORG, "store-probe", [good], said.append), "is linked to")

            for label, package_view, own, refusal in (
                    ("a public repository's own store, public and linked to it, is accepted",
                     {"visibility": "public", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}},
                     "public", None),
                    ("a private repository's own store, private and linked to it, is accepted",
                     {"visibility": "private", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}},
                     "private", None),
                    ("RED: a public repository's own store still private (before the flip) is refused",
                     {"visibility": "private", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}},
                     "public", "not 'public'"),
                    ("RED: a public repository's own store linked to no repository is refused",
                     {"visibility": "public"}, "public", "linked to no repository, not coderoasted/coderoast-ipc"),
                    ("RED: an own store linked to ANOTHER repository is refused",
                     {"visibility": "public", "repository": {"full_name": "CodeRoasted/insight-canon"}},
                     "public", "not coderoasted/coderoast-ipc"),
                    ("RED: a private repository's own store linked to no repository is refused",
                     {"visibility": "private"}, "private", "linked to no repository"),
                    ("RED: step 0's replica that came up public is refused",
                     {"visibility": "public"}, None, "not 'private'")):
                fake, registry, view, said = scene()
                fake.package = {"name": "store-coderoast-ipc", **package_view}
                arm(label, lambda own=own: replicate(store, registry, view, ORG, "store-coderoast-ipc", [good],
                                                     said.append, expected_package(ORG, "coderoast-ipc", own)),
                    refusal)
            arm("RED: an owned store of a visibility no rule names is refused",
                lambda: expected_package(ORG, "coderoast-ipc", "internal"), "no rule names")
            linked_sentinel = sentinel_manifest(expected_package(ORG, "coderoast-ipc", "public"))
            if json.loads(linked_sentinel).get("annotations") != {
                    SOURCE_ANNOTATION: "https://github.com/coderoasted/coderoast-ipc"}:
                failures.append(f"an owned store's sentinel carries {linked_sentinel!r}, want the source annotation")
            if "annotations" in json.loads(sentinel_manifest(STEP0_REPLICA)):
                failures.append("step 0's replica sentinel carries an annotation that would link it")

            fake, registry, view, said = scene(lie_manifest_digest=True)
            arm("a registry answering another manifest digest is refused",
                lambda: replicate(store, registry, view, ORG, "store-probe", [good], said.append),
                "the bytes written hash to")

            fake, registry, view, said = scene()
            replicate(store, registry, view, ORG, "store-probe", [good], said.append)
            fake.corrupt_blobs = True
            arm("a fetched blob whose bytes differ from its digest is refused, and nothing is written",
                lambda: registry.get_layer(good, Path(scratch) / "corrupt"), "hashes to")
            if (Path(scratch) / "corrupt").exists():
                failures.append("a corrupt fetch left its bytes at the output path")
            arm("a fetch of an object the package does not hold is refused",
                lambda: registry.get_layer("f" * 64, Path(scratch) / "absent"), "HTTP 404")

            def unreachable(_package: str) -> dict | None:
                raise Refused("the package API answered HTTP 403 for store-probe; its visibility "
                              "cannot be verified, so nothing is written")

            _fake, registry, _view, said = scene()
            arm("an API that cannot answer refuses the write",
                lambda: replicate(store, registry, unreachable, ORG, "store-probe", [good], said.append),
                "cannot be verified")
        finally:
            for server in scenes:
                server.shutdown()

    for failure in failures:
        print(f"store_replica selftest FAILED: {failure}")
    print(f"store_replica selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def _token(env: str, label: str) -> str:
    if env == TOKEN_FILE_ENV:
        path = os.environ.get(TOKEN_FILE_ENV)
        if not path or not Path(path).is_file():
            raise Refused(f"{TOKEN_FILE_ENV} names no file: the {label} reads its token from the writer "
                          f"account's token file, never from an argument")
        return Path(path).read_text().strip()
    value = os.environ.get(env, "")
    if not value:
        raise Refused(f"{env} is unset: the {label} reads the repository's own token")
    return value


def main(argv: list[str]) -> int:
    say = print
    try:
        if argv == ["selftest"]:
            return selftest()
        verb, own, rest = (argv[0] if argv else ""), None, argv[1:]
        if verb in ("push", "push-slice") and rest[:1] == ["--own"] and len(rest) >= 2:
            own, rest = rest[1], rest[2:]
        if verb == "push" and len(rest) >= 3:
            token = _token(TOKEN_FILE_ENV, "writer")
            package = package_of(rest[1])
            registry = Registry(REGISTRY, f"{ORG}/{package}",
                                (os.environ.get(REGISTRY_USER_ENV, REGISTRY_USER_DEFAULT), token))
            replicate(Path(rest[0]), registry, github_guard(API, ORG, token), ORG, package, rest[2:], say,
                      expected_package(ORG, rest[1], own))
            return 0
        if verb == "push-slice" and len(rest) == 4:
            token = _token(TOKEN_FILE_ENV, "writer")
            package = package_of(rest[0])
            registry = Registry(REGISTRY, f"{ORG}/{package}",
                                (os.environ.get(REGISTRY_USER_ENV, REGISTRY_USER_DEFAULT), token))
            replicate_slice(registry, github_guard(API, ORG, token), ORG, package, rest[1],
                            Path(rest[2]).read_bytes(), Path(rest[3]).read_bytes(), say,
                            expected_package(ORG, rest[0], own))
            return 0
        if len(argv) == 4 and argv[0] == "fetch":
            if not DIGEST_HEX.match(argv[2]):
                raise Refused(f"{argv[2]!r} is not an object digest (64 hex)")
            token = _token("GITHUB_TOKEN", "reader")
            package = package_of(argv[1])
            registry = Registry(REGISTRY, f"{ORG}/{package}",
                                (os.environ.get("GITHUB_ACTOR", "x-access-token"), token))
            registry.get_layer(argv[2], Path(argv[3]))
            say(f"store_replica: FETCHED {package} sha256:{argv[2]} -> {argv[3]}")
            return 0
    except Refused as refused:
        print(f"store_replica: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
