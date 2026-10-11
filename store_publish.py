#!/usr/bin/env python3
"""A release job's whole act on the store: fetch this repository's signed slice and every object it names, verify, stage.

DN-142.D9: a tag publishes; it builds nothing. `coderoast-publish.yml` runs this after `attest`
proved the tagged commit is the one the step-0 record judged, and hands it that record's digest:
1. the slice `slice-<record digest>` is read from ghcr.io/coderoasted/store-<repository> (both
   layers verified against their digests);
2. its signature verifies under the writer's key for this repository (store_manifest.verify), and
   the slice names the very record `attest` attested — a slice of another step-0 run is refused,
   as is one whose visibility (read from GitHub at step 0) differs from this repository's own now
   (DN-142.D24: two producers of the one fact that decides whether sources may cross);
3. each object it lists is fetched by its content digest and verified;
4. a `conan-transport` asset is staged as is, an `entry` asset is extracted from its transport
   and verified against the slice's entry digest, its `.sha256` file written FROM that digest;
5. the slice and its signature are staged beside them, so the Release carries its own evidence;
6. after the upload, the Release's asset names must equal the staged files, both ways (check-release).
Nothing reaches the output directory unverified, and nothing here compiles, tests or packs.

    store_publish.py stage <repository> <visibility> <step-0 record digest> <allowed signers> <out dir>
        Token: GITHUB_TOKEN (the repository's own, `packages: read`); <visibility> is the job's own
        reading of its repository (`github.event.repository.visibility`)
    store_publish.py check-release <out dir> <release.json>
        the reading after the publish (DN-142.D24 (3)): the Release's asset names, from
        `gh release view --json assets`, equal the staged files, both ways
    store_publish.py selftest
        the whole act against an in-process registry seeded by the writer's own functions

Exit 0 staged, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_manifest  # noqa: E402 — a sibling module, located from this file
import store_replica  # noqa: E402 — a sibling module, located from this file


class Refused(Exception):
    """A check failed; the message names what differed."""


def stage(registry: store_replica.Registry, repository: str, visibility: str, record_hex: str,
          allowed: Path, out: Path, say: Callable[[str], None]) -> list[str]:
    """Stage every asset of this repository's slice into `out`; the staged file names."""
    if not store_replica.DIGEST_HEX.match(record_hex):
        raise Refused(f"{record_hex!r} is not a step-0 record digest (64 hex)")
    try:
        slice_bytes, signature = registry.get_slice(record_hex)
    except store_replica.Refused as refused:
        raise Refused(str(refused)) from refused
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        (work / "slice.sig").write_bytes(signature)
        try:
            assets = store_manifest.verify(slice_bytes, work / "slice.sig", allowed, repository)
        except store_manifest.Refused as refused:
            raise Refused(str(refused)) from refused
        body = json.loads(slice_bytes)
        if body.get("step0_record") != record_hex:
            raise Refused(f"the slice names step-0 record {body.get('step0_record')}, and attest attested "
                          f"{record_hex}")
        if body.get("visibility") != visibility:
            raise Refused(f"the slice was built for a {body.get('visibility')} {repository}, and the "
                          f"repository is {visibility} now")
        staged: list[str] = []
        for asset in assets:
            name = asset["name"]
            if (out / name).exists() or name in staged:
                raise Refused(f"{name} is staged twice")
            fetched = work / asset["object"]
            if not fetched.exists():
                try:
                    registry.get_layer(asset["object"], fetched)
                except store_replica.Refused as refused:
                    raise Refused(f"{name}: {refused}") from refused
            if asset["kind"] == "conan-transport":
                (out / name).write_bytes(fetched.read_bytes())
                staged.append(name)
            else:
                try:
                    store_manifest.extract(fetched, asset["entry"], asset["entry_digest"], out / name)
                except store_manifest.Refused as refused:
                    raise Refused(f"{name}: {refused}") from refused
                (out / name).chmod(0o755)
                (out / f"{name}.sha256").write_text(f"{asset['entry_digest']}  {name}\n")
                staged += [name, f"{name}.sha256"]
            say(f"store_publish: STAGED {name} ({asset['kind']}, object {asset['object']})")
        evidence = f"{repository}-{body.get('version')}.slice.json"
        (out / evidence).write_bytes(slice_bytes)
        (out / f"{evidence}.sig").write_bytes(signature)
        staged += [evidence, f"{evidence}.sig"]
    return staged


def check_release(out: Path, release: dict) -> None:
    """The Release holds exactly the staged files: one more is an act no slice listed, one fewer a
    publication that did not happen."""
    attached = {asset["name"] for asset in release.get("assets", [])}
    staged = {path.name for path in out.iterdir() if path.is_file()}
    if attached != staged:
        raise Refused(f"the Release differs from the slice: attached and never staged "
                      f"{sorted(attached - staged)}, staged and not attached {sorted(staged - attached)}")


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
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        store, record, plan, _transports = store_manifest._fixture(root / "fixture")
        manifest, slices = store_manifest.build(store, record, plan)
        record_hex = hashlib.sha256(record).hexdigest()
        keys = root / "keys"
        keys.mkdir()
        for name in ("writer", "other"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(keys / name)],
                           check=True, stdin=subprocess.DEVNULL)
        allowed = keys / "allowed_signers"
        allowed.write_text(f'{store_manifest.WRITER_IDENTITY} namespaces="{store_manifest.NAMESPACE}" '
                           f"{(keys / 'writer.pub').read_text().strip()}\n")

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
            objects = sorted({asset["object"] for asset in json.loads(slice_bytes)["assets"]})
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
            want = ["pub_lib-9.9.9.tgz", "tool-linux-x64", "tool-linux-x64.sha256",
                    "pub-repo-9.9.9.slice.json", "pub-repo-9.9.9.slice.json.sig"]
            if staged != want:
                failures.append(f"staged {staged}, want {want}")
            elif (out / "tool-linux-x64").read_bytes() != b"\x7fELF tool bytes" or \
                    not (out / "tool-linux-x64.sha256").read_text().startswith(
                        hashlib.sha256(b"\x7fELF tool bytes").hexdigest()):
                failures.append("the staged entry or its .sha256 differs from the slice")
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


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 3 and argv[0] == "check-release":
            check_release(Path(argv[1]), json.loads(Path(argv[2]).read_text()))
            print(f"store_publish: the Release equals the slice ({argv[1]})")
            return 0
        if len(argv) == 6 and argv[0] == "stage":
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                raise Refused("GITHUB_TOKEN is unset: a publish job reads its repository's own store package")
            package = store_replica.package_of(argv[1])
            registry = store_replica.Registry(store_replica.REGISTRY, f"{store_replica.ORG}/{package}",
                                              (os.environ.get("GITHUB_ACTOR", "x-access-token"), token))
            for name in stage(registry, argv[1], argv[2], argv[3], Path(argv[4]), Path(argv[5]), print):
                print(f"store_publish: ASSET {name}")
            return 0
    except (Refused, store_replica.Refused) as refused:
        print(f"store_publish: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
