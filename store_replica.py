#!/usr/bin/env python3
"""The ghcr client a repository's own store is written and read through, and the guard on its package.

DN-142.D31 (3): one package per source repository, `ghcr.io/<owner>/store-<repository>`, written by
its own candidate (store_transport.py) and read back by digest. This module is the OCI distribution
client (`Registry`: Bearer-challenge auth, a token reused only on its own registry's origin, every
manifest and blob checked against the digest computed here) and THE ONE RULE on the package
(`expected_package`): it has the repository's own visibility and is LINKED to that repository —
public for a public one, private for a private one.

THE PACKAGE GUARD, BEFORE ANY WRITE (`ensure_guarded`): the GitHub view of the package must answer that
visibility and that link. A package that does not exist yet is created by a SENTINEL push first — an
empty artifact carrying no product byte, annotated with the repository it links to — and is then
guarded before any object reaches it, because a package that came up `internal` or `public` would
expose every byte pushed to it. No REST endpoint sets a container package's visibility, so a refusal
names the settings page and writes nothing more; setting it by hand is the remedy, and the next run
verifies it.

    store_replica.py selftest
        every refusal against an in-process registry and package view, and the round trip; no network

Exit 0 done, 1 refused (the reason on stderr), 2 a usage error.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

REGISTRY = "https://ghcr.io"
API = "https://api.github.com"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
OBJECT_TYPE = "application/vnd.coderoast.store.object.v1"
SENTINEL_TYPE = "application/vnd.coderoast.store.sentinel.v1"
SENTINEL_TAG = "store-sentinel"
EMPTY_BYTES = b"{}"
EMPTY_DIGEST = "sha256:" + hashlib.sha256(EMPTY_BYTES).hexdigest()
TAGS_PAGE = 1000
LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


class Refused(Exception):
    """A check failed: nothing more is written, and the reason names what differed."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        realm = urllib.parse.urlsplit(fields.get("realm", ""))
        if (realm.scheme, realm.netloc) != urllib.parse.urlsplit(self.base)[:2]:
            # note: the credentials go to this registry's own token endpoint, never to one a challenge names elsewhere
            raise Refused(f"the token realm {fields.get('realm', '')!r} is not on {self.base}: no credential is sent there")
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
        # note: a token this registry already issued for the scope rides the first attempt, only to its own origin
        scope = f"repository:{self.name}:{actions}"
        same_origin = urllib.parse.urlsplit(target)[:2] == urllib.parse.urlsplit(self.base)[:2]
        auth = f"Bearer {self.tokens[scope]}" if same_origin and scope in self.tokens else None
        cached = auth is not None
        for _attempt in range(3):
            request = urllib.request.Request(target, data=data, method=method)
            for key, value in (headers or {}).items():
                request.add_header(key, value)
            if auth:
                request.add_header("Authorization", auth)
            answer = _send(request)
            challenge = answer.headers.get("www-authenticate", "")
            if answer.status == 401 and challenge.lower().startswith("bearer") and (auth is None or cached):
                if cached:
                    self.tokens.pop(scope, None)
                    cached = False
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

    def tags(self) -> list[str]:
        """Every tag the repository holds, page by page as the registry's `Link: rel="next"` header leads."""
        found: list[str] = []
        url = f"{self.base}/v2/{self.name}/tags/list?n={TAGS_PAGE}"
        while url:
            answer = self.call("GET", "", "pull", url=url)
            if answer.status != 200:
                raise Refused(f"GET tags of {self.name}: HTTP {answer.status}")
            found += json.loads(answer.body or b"{}").get("tags") or []
            following = LINK_NEXT.search(answer.headers.get("link", ""))
            url = urllib.parse.urljoin(f"{self.base}/", following.group(1)) if following else ""
        return found

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

VISIBILITIES = ("public", "private")


@dataclass(frozen=True)
class Expected:
    """What the package guard requires of a store package: its visibility, and the repository it is
    linked to (`owner/name`, lower case)."""

    visibility: str
    linked: str


def expected_package(owner: str, repository: str, own_visibility: str) -> Expected:
    """THE ONE RULE (DN-142.D31 (3), item 6): the package has the repository's own visibility and is
    linked to it."""
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
        answered = ("carries no `repository` field, as a GITHUB_TOKEN's answer for a private package does: pass a "
                    "read:packages token as MALF_PACKAGE_PEEK_TOKEN" if "repository" not in seen
                    else f"names `repository` {seen['repository']!r}")
        return (f"{package} is linked to {linked or 'no repository'}, not {expected.linked} (the API's answer "
                f"{answered}): a repository that owns its store writes it with its own token, through that link")
    return None


def verify_package(view: Callable[[str], dict | None], org: str, package: str, expected: Expected) -> None:
    """Refuse unless the package exists with the visibility and the link `expected` names. A private
    package of a public repository is never made public by a setting (DN-142.D38 (7)): its content was
    written with no licence judged, so its remedy is deletion, and the next push recreates it public
    and judges every third-party record it writes. Every other defect is set in the package's settings."""
    seen = view(package)
    defect = package_defect(seen, package, expected)
    if defect is None:
        return
    settings = f"https://github.com/orgs/{org}/packages/container/{package}/settings"
    if seen is None:
        raise Refused(f"{defect} after its sentinel push, so it cannot be verified")
    if expected.visibility == "public" and seen.get("visibility") == "private":
        raise Refused(f"{package} is 'private' and {expected.linked} is public: a private store's content was "
                      f"written with no licence judged (DN-142.D38 (7)), so it is never made public by a "
                      f"setting — delete it in {settings}; the next push recreates it public and judges every "
                      f"third-party record it writes")
    raise Refused(f"{defect}: set it in {settings} (no API sets visibility or link); nothing more is "
                  f"pushed until a run verifies it")


SOURCE_ANNOTATION = "org.opencontainers.image.source"


def sentinel_manifest(expected: Expected) -> bytes:
    """The empty artifact that creates a package, carrying the `org.opencontainers.image.source`
    annotation ghcr links a new package to its repository by (DN-142.D31 (3))."""
    return canonical({
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": SENTINEL_TYPE,
        "config": {"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)},
        "layers": [{"mediaType": OCI_EMPTY, "digest": EMPTY_DIGEST, "size": len(EMPTY_BYTES)}],
        "annotations": {SOURCE_ANNOTATION: f"https://github.com/{expected.linked}"}})


def ensure_guarded(registry: Registry, view: Callable[[str], dict | None], org: str, package: str,
                   say: Callable[[str], None], expected: Expected) -> None:
    """The package exists, with the visibility and link `expected` names, before any product byte reaches it."""
    if view(package) is None:
        registry.push_blob(EMPTY_DIGEST.split(":")[1], EMPTY_BYTES)
        registry.put_manifest(SENTINEL_TAG, sentinel_manifest(expected))
        say(f"store_replica: created {package} with an empty sentinel; guarding it before any object")
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
    realm: str = ""
    challenged: int = 0


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
            realm = fake.realm or f"http://{self.headers['Host']}/token"
            fake.challenged += 1
            self._reply(401, b"", {"WWW-Authenticate": f'Bearer realm="{realm}",service="fake",scope="x"'})
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
                        source = (json.loads(data).get("annotations") or {}).get(SOURCE_ANNOTATION, "")
                        fake.package = {"name": parts[3], "visibility": fake.created_visibility,
                                        **({"repository": {"full_name": source.removeprefix("https://github.com/")}}
                                           if source else {})}
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
            if parts[-2:] == ["tags", "list"]:
                asked = urllib.parse.parse_qs(parsed.query)
                page = int(asked.get("n", ["100"])[0])
                names = sorted(name for name in fake.tags if name > asked.get("last", [""])[0])
                more = {"Link": f'</v2/{parts[2]}/{parts[3]}/tags/list?n={page}&last={names[page - 1]}>; rel="next"'} \
                    if len(names) > page else {}
                self._reply(200, canonical({"name": f"{parts[2]}/{parts[3]}", "tags": names[:page]}), more)
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


OWNER = "coderoasted"
REPOSITORY = "coderoast-ipc"
PACKAGE = f"store-{REPOSITORY}"


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

    payload = b"a stored object\n" * 64
    good = hashlib.sha256(payload).hexdigest()
    private = expected_package(OWNER, REPOSITORY, "private")
    scenes: list[http.server.ThreadingHTTPServer] = []

    def scene(**knobs) -> tuple[Fake, Registry, Callable[[str], dict | None], list[str]]:
        fake = Fake(**knobs)
        server, base = _fake_server(fake)
        scenes.append(server)
        return fake, Registry(base, f"{OWNER}/{PACKAGE}", ("user", "token")), lambda _package: fake.package, []

    def guarded(registry: Registry, view: Callable[[str], dict | None], said: list[str],
                expected: Expected = private) -> Callable[[], None]:
        return lambda: ensure_guarded(registry, view, OWNER, PACKAGE, said.append, expected)

    def round_trip(registry: Registry) -> None:
        manifest = object_manifest(good, len(payload))
        for data in (EMPTY_BYTES, payload):
            registry.push_blob(hashlib.sha256(data).hexdigest(), data)
        registry.put_manifest(f"sha256-{good}", manifest)
        if registry.manifest_digest(f"sha256-{good}") != "sha256:" + hashlib.sha256(manifest).hexdigest():
            raise Refused("the object's tag did not read back as its manifest")
        if registry.blob(good) != payload:
            raise Refused("the fetched bytes differ from the pushed object")

    try:
        fake, registry, view, said = scene()
        arm("a new package: the sentinel creates it linked to its repository, and the guard passes",
            guarded(registry, view, said), None)
        if SENTINEL_TAG not in fake.tags:
            failures.append(f"the registry holds tags {sorted(fake.tags)}, want the sentinel")
        arm("an object pushed by digest reads back byte-equal under its canonical manifest",
            lambda: round_trip(registry), None)
        pushes = len(fake.blobs)
        arm("a blob the registry already holds is not uploaded again",
            lambda: registry.push_blob(good, payload), None)
        if len(fake.blobs) != pushes:
            failures.append("a re-push of a held blob uploaded it again")

        fake, registry, view, said = scene(created_visibility="internal")
        arm("a package that came up internal is refused BEFORE any object reaches it",
            guarded(registry, view, said), "not 'private'")

        for label, package_view, own, refusal in (
                ("a public repository's own store, public and linked to it, is accepted",
                 {"visibility": "public", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}},
                 "public", None),
                ("a private repository's own store, private and linked to it, is accepted",
                 {"visibility": "private", "repository": {"full_name": "CodeRoasted/coderoast-ipc"}},
                 "private", None),
                ("RED: a public repository's own store linked to no repository is refused",
                 {"visibility": "public"}, "public", "linked to no repository, not coderoasted/coderoast-ipc"),
                ("RED: an own store linked to ANOTHER repository is refused",
                 {"visibility": "public", "repository": {"full_name": "CodeRoasted/insight-canon"}},
                 "public", "not coderoasted/coderoast-ipc"),
                ("RED: a private repository's own store linked to no repository is refused",
                 {"visibility": "private"}, "private", "linked to no repository")):
            fake, registry, view, said = scene()
            fake.package = {"name": PACKAGE, **package_view}
            arm(label, guarded(registry, view, said, expected_package(OWNER, REPOSITORY, own)), refusal)
        fake, registry, view, said = scene()
        fake.package = {"name": PACKAGE, "visibility": "private",
                        "repository": {"full_name": "CodeRoasted/coderoast-ipc"}}
        arm("RED: a public repository's own store still private is refused with the DELETE remedy, never a setting",
            guarded(registry, view, said, expected_package(OWNER, REPOSITORY, "public")),
            "is never made public by a setting — delete it in https://github.com/orgs/")
        if fake.blobs or fake.manifests:
            failures.append(f"the delete-remedy refusal pushed {len(fake.blobs)} blob(s) and "
                            f"{len(fake.manifests)} manifest(s), want none")
        fake, registry, view, said = scene()
        fake.package = {"name": PACKAGE, "visibility": "public",
                        "repository": {"full_name": "CodeRoasted/coderoast-ipc"}}
        arm("RED: a private repository's own store made public keeps the set-it remedy",
            guarded(registry, view, said, private), "is 'public', not 'private': set it in https://github.com/orgs/")
        fake, registry, view, said = scene(created_visibility="public")
        arm("a public repository with no package: the sentinel creates it public, and the guard passes",
            guarded(registry, view, said, expected_package(OWNER, REPOSITORY, "public")), None)
        if SENTINEL_TAG not in fake.tags:
            failures.append(f"the public repository's registry holds tags {sorted(fake.tags)}, want the sentinel")
        arm("RED: an owned store of a visibility no rule names is refused",
            lambda: expected_package(OWNER, REPOSITORY, "internal"), "no rule names")
        if json.loads(sentinel_manifest(private)).get("annotations") != {
                SOURCE_ANNOTATION: "https://github.com/coderoasted/coderoast-ipc"}:
            failures.append(f"the sentinel carries {sentinel_manifest(private)!r}, want the source annotation")

        _fake, registry, _view, said = scene(lie_manifest_digest=True)
        arm("a registry answering another manifest digest is refused",
            lambda: registry.put_manifest("t", object_manifest(good, len(payload))), "the bytes written hash to")

        fake, registry, _view, said = scene()
        round_trip(registry)
        fake.corrupt_blobs = True
        arm("a fetched blob whose bytes differ from its digest is refused",
            lambda: registry.blob(good), "hashes to")
        arm("a fetch of a blob the package does not hold is refused",
            lambda: registry.blob("f" * 64), "HTTP 404")

        fake, registry, _view, said = scene()
        fake.tags.update({f"tag-{number:04d}": EMPTY_DIGEST for number in range(TAGS_PAGE + 5)})

        def every_tag_across_pages() -> None:
            listed = registry.tags()
            if listed != sorted(fake.tags):
                raise Refused(f"the tag list holds {len(listed)} tag(s), want {len(fake.tags)} across two pages")

        arm("the tag list follows the registry's Link header across pages, every tag once",
            every_tag_across_pages, None)

        fake, registry, _view, said = scene()

        def second_call_unchallenged() -> None:
            registry.manifest_digest(SENTINEL_TAG)
            fake.challenged = 0
            registry.manifest_digest(SENTINEL_TAG)
            if fake.challenged:
                raise Refused(f"the second call drew {fake.challenged} challenge(s)")

        arm("a token the registry issued rides the next call on its scope, which draws no challenge",
            second_call_unchallenged, None)
        fake.bearer = "rotated-bearer"
        arm("a cached token the registry no longer accepts is dropped and re-fetched once",
            lambda: registry.manifest_digest(SENTINEL_TAG), None)
        _fake, registry, _view, said = scene(realm="http://elsewhere.invalid/token")
        arm("RED: a challenge naming a token realm off the registry's origin is refused, and no credential goes there",
            lambda: registry.manifest_digest(SENTINEL_TAG), "no credential is sent there")

        def unreachable(_package: str) -> dict | None:
            raise Refused(f"the package API answered HTTP 403 for {PACKAGE}; its visibility cannot be "
                          f"verified, so nothing is written")

        _fake, registry, _view, said = scene()
        arm("an API that cannot answer refuses the write", guarded(registry, unreachable, said), "cannot be verified")
    finally:
        for server in scenes:
            server.shutdown()

    for failure in failures:
        print(f"store_replica selftest FAILED: {failure}")
    print(f"store_replica selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
