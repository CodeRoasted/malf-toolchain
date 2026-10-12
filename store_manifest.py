#!/usr/bin/env python3
"""A cut's publication manifest and its signed per-repository slices: built and sealed by the writer, verified by every publish job.

DN-142.D9: at the end of a green step 0 the writer lists, for every repository, each artefact it
publishes by digest, and an artefact enters only if it is EVALUATED (DN-142.D7): its package's
step-0 test verdict, its corpus verdict where one exists, and every verdict its asset names, each a
`pass` record whose `judged` holds the very digest published. DN-142.D20 (4): each repository
receives its own SLICE, `{repository, the step-0 record's digest, the whole manifest's digest, that
repository's assets}`, signed by the writer (DN-142.D8, `ssh-keygen -Y sign`, namespace
`coderoast-store`), so a public Release never carries the private inventory. DN-142.D22 (3): the
manifest and the `executables:` declarations agree in both directions — every executable an asset
or an image carries is classified `published`, and every `published` executable of the run is
carried by an asset or an image. N360 plan F9: a conan transport carries its recipe's SOURCES, so a
transport whose source repository is private never reaches a public repository.

AN ENTRY ASSET'S OBJECT IS THE ENTRY'S OWN BYTES, NEVER ITS PACKAGE'S TRANSPORT. The builder writes
the one member into the store under its entry digest, verified, and the slice names that object.
The transport would carry the package's sources into the destination repository's store package,
which that repository's token reads: a public repository's slice would have handed a private
package's sources to every job of it.

AN IMAGE IS AN ASSET OF THE REPOSITORY THAT DECLARES IT (DN-142.D9; N360 plan C1): the plan names
each image with its executables, and step 0's image step (`store_image.py`) stores the image as
one OCI archive under a record `<image>/image` naming the entries it consumed. The builder admits
the image only when that record consumed exactly the plan's executables at this run's entry
digests, and counts each of them as carried.

THE PLAN is the builder's declared input: which repository publishes what, under which name.

    {"format": "coderoast.store.plan/2",
     "repositories": {<repo>: {"visibility": "public" | "private",
                               "assets": [{"name", "kind": "conan-transport", "package", "source"},
                                          {"name", "kind": "entry", "package", "source", "entry",
                                           "verdicts": [<output>...]}],
                               "images": {<image>: {"dockerfile", "runtime": [<library>...],
                                                    "executables": [{"package", "entry", "source"}]}}}}}

    store_manifest.py build <store> <step-0 record> <plan.json> <out dir>
        <out dir>/manifest.json and <out dir>/slices/<repo>.json, canonical bytes; exit 1 naming
        every asset refused (no manifest is written then; entry objects already verified stay
        in the store, content-addressed)
    store_manifest.py seal <store> <step-0 record> <plan.json> <private key> <out dir>
        build, then sign the manifest and every slice, and keep the record, the manifest, each
        slice and each signature in the store under one `manifest` record keyed by the step-0
        record's digest; the slices and signatures stay in <out dir> for the replica
    store_manifest.py signer <private key> <allowed signers>
        exit 0 printing the key's public half only when it is the one writer key the file names
    store_manifest.py sign <file> <private key>
        <file>.sig, `ssh-keygen -Y sign -n coderoast-store`
    store_manifest.py verify <slice> <signature> <allowed signers> <repository>
        exit 0 and one line per asset, `<kind> <name> <object> <entry> <entry digest>` ("-" when
        absent), only when the signature verifies for the writer identity, the slice names
        <repository>, and a public slice carries no private-source transport
    store_manifest.py extract <transport archive> <entry> <entry digest> <out file>
        the one package member `<entry>`, written only when exactly one matches and it hashes to
        <entry digest>
    store_manifest.py selftest
        every refusal over a fixture store, a fixture key pair and a fixture transport

Exit 0 done, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artefact_store  # noqa: E402 — a sibling module, located from this file

PLAN_FORMAT = "coderoast.store.plan/2"
MANIFEST_FORMAT = "coderoast.store.manifest/2"
SLICE_FORMAT = "coderoast.store.slice/2"
NAMESPACE = "coderoast-store"
WRITER_IDENTITY = "coderoast-store-writer"
KINDS = ("conan-transport", "entry")
IMAGE_KIND = "oci-image"
IMAGE_STEP = "image"
IMAGE_MANIFEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RUNTIME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
VISIBILITIES = ("public", "private")
REPOSITORY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
DIGEST_HEX = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20


class Refused(Exception):
    """A check failed; the message names what differed."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_findings(plan: dict) -> list[str]:
    found = []
    if plan.get("format") != PLAN_FORMAT:
        return [f"the plan's format is {plan.get('format')!r}, not {PLAN_FORMAT!r}"]
    repositories = plan.get("repositories")
    if not isinstance(repositories, dict) or not repositories:
        return ["the plan names no repository"]
    names: set[str] = set()
    images: set[str] = set()
    for repo, body in sorted(repositories.items()):
        if not REPOSITORY_NAME.match(repo):
            found.append(f"{repo!r} is not a repository name")
        if body.get("visibility") not in VISIBILITIES:
            found.append(f"{repo}: visibility {body.get('visibility')!r} is not public or private")
        for asset in body.get("assets", []):
            where = f"{repo}: asset {asset.get('name')!r}"
            if asset.get("kind") not in KINDS:
                found.append(f"{where}: kind {asset.get('kind')!r} is not one of {', '.join(KINDS)}")
            if not ASSET_NAME.match(str(asset.get("name", ""))):
                found.append(f"{where}: not an asset file name")
            if (repo, asset.get("name")) in names:
                found.append(f"{where}: named twice")
            names.add((repo, asset.get("name")))
            if asset.get("source") not in repositories:
                found.append(f"{where}: its source repository {asset.get('source')!r} is not in the plan")
            if asset.get("kind") == "entry" and not asset.get("entry"):
                found.append(f"{where}: an entry asset names no entry")
        for image, declared in sorted((body.get("images") or {}).items()):
            where = f"{repo}: image {image!r}"
            if not ASSET_NAME.match(image) or image in images or (repo, image) in names:
                found.append(f"{where}: not an image name, or named twice")
            images.add(image)
            if not isinstance(declared.get("dockerfile"), str) or not declared["dockerfile"] \
                    or declared["dockerfile"].startswith("/") or ".." in declared["dockerfile"].split("/"):
                found.append(f"{where}: its dockerfile {declared.get('dockerfile')!r} is not a path in its repository")
            if any(not RUNTIME_NAME.match(str(name)) for name in declared.get("runtime") or []):
                found.append(f"{where}: a runtime library is a file name, not {declared.get('runtime')!r}")
            carried = declared.get("executables") or []
            if not carried:
                found.append(f"{where}: it carries no executable")
            for executable in carried:
                if set(executable) != {"package", "entry", "source"} or executable["source"] not in repositories:
                    found.append(f"{where}: an executable is {{package, entry, source}} from a plan "
                                 f"repository, not {executable!r}")
    return found


def hold(store: Path, digest: str, data: bytes) -> None:
    """`data` kept as the store's object `digest`, create-only; refused when it does not hash to
    it, or when an object already there under that name does not."""
    if sha256_bytes(data) != digest:
        raise Refused(f"bytes meant for object {digest} hash to {sha256_bytes(data)}")
    target = store / "objects" / digest
    if target.exists():
        if sha256_file(target) != digest:
            raise Refused(f"the store's object {digest} does not hash to its name")
        return
    staged = target.with_name(f".{digest}.{os.getpid()}.partial")
    staged.write_bytes(data)
    try:
        os.link(staged, target)
    except FileExistsError:
        pass
    finally:
        staged.unlink()


def build(store: Path, record_bytes: bytes, plan: dict) -> tuple[bytes, dict[str, bytes]]:
    """(the whole manifest, {repo: slice}) as canonical bytes, or Refused naming every finding.
    Each entry asset's bytes are kept in the store under their entry digest on the way."""
    findings = _plan_findings(plan)
    if findings:
        raise Refused("; ".join(findings))
    record = json.loads(record_bytes)
    if not (record.get("built") is True and record.get("cuttable") is True):
        raise Refused(f"the step-0 record is built={record.get('built')} cuttable={record.get('cuttable')}: "
                      f"a manifest is written only after a green step 0")
    steps = {step["id"]: step for step in record.get("steps", [])}
    if not steps:
        raise Refused("the step-0 record lists no `steps`")

    def stored(step_id: str) -> dict:
        step = steps.get(step_id)
        if step is None:
            raise Refused(f"step 0 ran no step {step_id}")
        path = store / "records" / f"{step['key']}.json"
        if not path.is_file():
            raise Refused(f"the store holds no record {step['key']} for {step_id}")
        return json.loads(path.read_text())

    def evaluated(package: str, output: str, digest: str) -> None:
        """The run's `<package>/<output>` verdict passed over `digest`. A test verdict keeps what it
        judged in its output; a generalized verdict (DN-142.D19 (10)) in its key document."""
        step = steps.get(f"{package}/{output}")
        if step is None:
            raise Refused(f"{package}: no {output} verdict in this step-0 run")
        built = stored(f"{package}/{output}")
        body = built["outputs"][output]
        judged = body.get("judged") or built["inputs"].get("judged") or {}
        if step.get("verdict") != "pass" or body.get("verdict") != "pass":
            raise Refused(f"{package}: its {output} verdict is {body.get('verdict')!r}, not pass")
        if digest not in judged.values():
            raise Refused(f"{package}: its {output} verdict judged {sorted(judged.values())}, "
                          f"not the published digest {digest}")

    def created(package: str) -> tuple[dict, dict[str, str]]:
        """The package's stored output, once its transport object is whole and its test verdicts
        (and its corpus verdict, where it has one) passed over its content; its executable classes."""
        package_record = stored(f"{package}/package")
        output = package_record["outputs"]["package"]
        object_path = store / "objects" / output["transport"]
        if not object_path.is_file() or sha256_file(object_path) != output["transport"]:
            raise Refused(f"{package}: the store's object {output['transport']} is absent or does not hash to its name")
        evaluated(package, "verdict", output["content"])
        if f"{package}/corpus" in steps:
            evaluated(package, "corpus", output["content"])
        return output, package_record["inputs"]["step"].get("executables", {})

    def classified(package: str, executables: dict[str, str], member: str, carrier: str) -> None:
        if executables.get(member) != "published":
            raise Refused(f"{package}: {carrier} carries {member}, classified "
                          f"{executables.get(member, 'nowhere')!r}, not 'published' (DN-142.D22 (3))")
        carried.add((package, member))

    visibility = {repo: body["visibility"] for repo, body in plan["repositories"].items()}
    carried: set[tuple[str, str]] = set()
    whole: dict[str, dict] = {}
    for repo, body in sorted(plan["repositories"].items()):
        rows = []
        for asset in body.get("assets", []):
            package = asset["package"]
            try:
                output, executables = created(package)
                row = {"name": asset["name"], "kind": asset["kind"], "package": package,
                       "source": asset["source"], "record": steps[f"{package}/package"]["key"],
                       "object": output["transport"], "content": output["content"]}
                if asset["kind"] == "conan-transport":
                    if visibility[repo] == "public" and visibility[asset["source"]] == "private":
                        raise Refused(f"{package}'s transport carries the sources of the PRIVATE "
                                      f"{asset['source']}, and {repo} is public")
                    members = [path for path, mode, _digest in output["entries"] if mode == "executable"]
                else:
                    held = {path: digest for path, _mode, digest in output["entries"]}
                    if asset["entry"] not in held:
                        raise Refused(f"{package} holds no entry {asset['entry']}")
                    with tempfile.TemporaryDirectory() as scratch:
                        member = Path(scratch) / "entry"
                        extract(store / "objects" / output["transport"], asset["entry"], held[asset["entry"]], member)
                        hold(store, held[asset["entry"]], member.read_bytes())
                    row.update(object=held[asset["entry"]], entry=asset["entry"], entry_digest=held[asset["entry"]])
                    members = [asset["entry"]]
                for output_name in asset.get("verdicts", []):
                    evaluated(package, output_name, row.get("entry_digest") or output["content"])
                for member in members:
                    classified(package, executables, member, asset["name"])
                rows.append(row)
            except Refused as refused:
                findings.append(f"{repo}/{asset['name']}: {refused}")
        for image, declared in sorted((body.get("images") or {}).items()):
            try:
                built = stored(f"{image}/{IMAGE_STEP}")
                document, output = built["inputs"], built["outputs"][IMAGE_STEP]
                if document["step"].get("repository") != repo:
                    raise Refused(f"its image step built it for {document['step'].get('repository')!r}")
                consumed = {(row["package"], row["entry"]): row for row in document.get("executables", [])}
                wanted = {(row["package"], row["entry"]) for row in declared["executables"]}
                if set(consumed) != wanted:
                    raise Refused(f"its image step consumed {sorted(consumed)}, and the plan declares {sorted(wanted)}")
                for (package, entry), row in sorted(consumed.items()):
                    package_output, executables = created(package)
                    held = {path: digest for path, _mode, digest in package_output["entries"]}
                    if row.get("digest") != held.get(entry) or row.get("record") != steps[f"{package}/package"]["key"]:
                        raise Refused(f"its image step consumed {package} {entry} at {row.get('digest')}, and this "
                                      f"run's create stored {held.get(entry)}")
                    classified(package, executables, entry, image)
                archive = store / "objects" / output["archive"]
                if not archive.is_file() or sha256_file(archive) != output["archive"] \
                        or not IMAGE_MANIFEST.match(output.get("manifest", "")):
                    raise Refused(f"the store's archive {output['archive']} is absent, does not hash to its "
                                  f"name, or names no image manifest")
                rows.append({"name": image, "kind": IMAGE_KIND, "source": repo,
                             "record": steps[f"{image}/{IMAGE_STEP}"]["key"],
                             "object": output["archive"], "manifest": output["manifest"]})
            except Refused as refused:
                findings.append(f"{repo}/{image}: {refused}")
        whole[repo] = {"visibility": visibility[repo], "assets": sorted(rows, key=lambda row: row["name"])}
    for step_id in sorted(steps):
        package, _slash, output = step_id.partition("/")
        if output != "package":
            continue
        declared = stored(step_id)["inputs"]["step"].get("executables", {})
        for member, kind in sorted(declared.items()):
            if kind == "published" and (package, member) not in carried:
                findings.append(f"{package}: {member} is classified 'published' and no asset or image of "
                                f"this manifest carries it (DN-142.D22 (3))")
    if findings:
        raise Refused("; ".join(findings))
    manifest = canonical({"format": MANIFEST_FORMAT, "version": record.get("version"),
                          "step0_record": sha256_bytes(record_bytes), "repositories": whole})
    slices = {repo: canonical({"format": SLICE_FORMAT, "repository": repo, "version": record.get("version"),
                               "visibility": body["visibility"], "step0_record": sha256_bytes(record_bytes),
                               "manifest": sha256_bytes(manifest), "assets": body["assets"]})
              for repo, body in whole.items()}
    return manifest, slices


def sign(path: Path, key: Path) -> Path:
    """`<path>.sig`, never over an existing one: ssh-keygen would ask on stdin whether to overwrite,
    and a caller whose stdin is an open pipe then waits forever (measured on the selftest)."""
    signature = path.with_name(path.name + ".sig")
    if signature.exists():
        raise Refused(f"{signature} already exists: a signature is written once, never replaced")
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(path)],
                          capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise Refused(f"ssh-keygen could not sign {path}: {done.stderr.strip()[-300:]}")
    return signature


def signer(key: Path, allowed: Path) -> str:
    """The writer key's public half, once it is the one key `allowed` names for the writer identity
    and namespace: a seal is signed only by the key every publish job verifies against."""
    derived = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], capture_output=True, text=True,
                             check=False, stdin=subprocess.DEVNULL)
    if derived.returncode != 0:
        raise Refused(f"ssh-keygen cannot read the signing key {key} (a key with a passphrase is never "
                      f"used here): {derived.stderr.strip()[-300:]}")
    public = derived.stdout.split()[:2]
    lines = [line.split() for line in allowed.read_text().splitlines() if line.strip()]
    if len(lines) != 1 or lines[0][:4] != [WRITER_IDENTITY, f'namespaces="{NAMESPACE}"', *public]:
        raise Refused(f"the signing key's public half {' '.join(public)[:60]}... is not the one writer key "
                      f"{allowed} names for {WRITER_IDENTITY}")
    return " ".join(public)


def seal(store: Path, record_bytes: bytes, plan: dict, key: Path, out: Path) -> dict:
    """Build the manifest, sign it and every slice, and keep the step-0 record, the manifest, each
    slice and each signature in the store under one `manifest` record keyed by the record's digest.
    `out` is created here and receives the signed files the replica pushes; the seal's summary."""
    if out.exists():
        raise Refused(f"{out} exists: a seal writes its signatures once, into a directory of its own")
    manifest, slices = build(store, record_bytes, plan)
    out.mkdir(parents=True)
    (out / "slices").mkdir()
    (out / "manifest.json").write_bytes(manifest)
    signed = {"manifest": (out / "manifest.json", sign(out / "manifest.json", key))}
    for repo, body in sorted(slices.items()):
        path = out / "slices" / f"{repo}.json"
        path.write_bytes(body)
        signed[repo] = (path, sign(path, key))
    record_digest = sha256_bytes(record_bytes)
    hold(store, record_digest, record_bytes)
    held = {}
    for name, (path, signature) in signed.items():
        for kept in (path, signature):
            hold(store, sha256_file(kept), kept.read_bytes())
        held[name] = {"object": sha256_file(path), "signature": sha256_file(signature)}
    summary = {"record": record_digest, "manifest": held.pop("manifest"), "slices": held}
    document = {"step": {"kind": "manifest", "subject": "step0"}, "step0_record": record_digest}
    if artefact_store.LocalStore(store).commit(document, {"manifest": dict(summary)}, {}) != 0:
        raise Refused(f"the store already holds another seal of step-0 record {record_digest}")
    return summary


def verify(slice_bytes: bytes, signature: Path, allowed: Path, repository: str) -> list[dict]:
    """The slice's assets, only once its signature, its repository and its F9 rule hold."""
    done = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", WRITER_IDENTITY,
                           "-n", NAMESPACE, "-s", str(signature)],
                          input=slice_bytes, capture_output=True, check=False)
    if done.returncode != 0:
        raise Refused(f"the slice's signature does not verify for {WRITER_IDENTITY}: "
                      f"{done.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    body = json.loads(slice_bytes)
    if body.get("format") != SLICE_FORMAT:
        raise Refused(f"the slice's format is {body.get('format')!r}, not {SLICE_FORMAT!r}")
    if body.get("repository") != repository:
        raise Refused(f"the slice is {body.get('repository')!r}'s, not {repository!r}'s")
    for asset in body.get("assets", []):
        if asset.get("kind") not in (*KINDS, IMAGE_KIND):
            raise Refused(f"{asset.get('name')}: kind {asset.get('kind')!r} is not one of "
                          f"{', '.join((*KINDS, IMAGE_KIND))}")
        if not DIGEST_HEX.match(asset.get("object", "")):
            raise Refused(f"{asset.get('name')}: {asset.get('object')!r} is not an object digest")
        if asset.get("kind") == "entry" and asset.get("entry_digest") != asset["object"]:
            raise Refused(f"{asset.get('name')}: an entry asset's object is the entry's own bytes, and "
                          f"{asset['object']} is not its entry digest {asset.get('entry_digest')!r}")
        if asset.get("kind") == IMAGE_KIND and not IMAGE_MANIFEST.match(asset.get("manifest", "")):
            raise Refused(f"{asset.get('name')}: an image asset names no image manifest digest")
        if (body.get("visibility") == "public" and asset.get("kind") == "conan-transport"
                and asset.get("source") != repository):
            raise Refused(f"{asset.get('name')}: a transport from {asset.get('source')} in the public "
                          f"{repository}'s slice")
    return body.get("assets", [])


def extract(archive: Path, entry: str, digest: str, out: Path) -> None:
    """The one package member `b/<storage folder>/p/<entry>` of a `conan cache save` archive (the
    layout artefact_store.transport_files reads), verified."""
    with tarfile.open(archive, "r:*") as bundle:
        members = [member for member in bundle.getmembers()
                   if member.isfile() and re.fullmatch(rf"b/[^/]+/p/{re.escape(entry)}", member.name)]
        if len(members) != 1:
            raise Refused(f"{archive.name} holds {len(members)} package member(s) {entry}, not one")
        data = bundle.extractfile(members[0]).read()
    if sha256_bytes(data) != digest:
        raise Refused(f"{entry} in {archive.name} hashes to {sha256_bytes(data)}, not {digest}")
    staged = out.with_name(f".{out.name}.partial")
    staged.write_bytes(data)
    staged.replace(out)


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────


def _fixture(root: Path) -> tuple[Path, bytes, dict, dict[str, Path]]:
    """A store holding three packages, a green step-0 record over them, and the plan publishing them."""
    store = root / "store"
    for part in ("objects", "records"):
        (store / part).mkdir(parents=True)
    steps, transports = [], {}

    def put(document: dict, outputs: dict) -> str:
        key = sha256_bytes(canonical(document))
        (store / "records" / f"{key}.json").write_text(json.dumps({"key": key, "inputs": document,
                                                                     "outputs": outputs}))
        return key

    def package(name: str, files: dict[str, tuple[str, bytes]], executables: dict[str, str]) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for path, (_mode, data) in files.items():
                info = tarfile.TarInfo(f"b/{name[:5]}0123/p/{path}")
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
        transport = sha256_bytes(buffer.getvalue())
        (store / "objects" / transport).write_bytes(buffer.getvalue())
        transports[name] = store / "objects" / transport
        entries = [[path, mode, sha256_bytes(data)] for path, (mode, data) in sorted(files.items())]
        content = sha256_bytes(canonical(entries))
        key = put({"step": {"kind": "conan-create", "package": name, "executables": executables}},
                  {"package": {"content": content, "entries": entries, "transport": transport}})
        steps.append({"id": f"{name}/package", "key": key, "verdict": None})
        verdict = put({"step": {"kind": "test-verdict", "package": name}},
                      {"verdict": {"verdict": "pass", "judged": {name: content}}})
        steps.append({"id": f"{name}/verdict", "key": verdict, "verdict": "pass"})

    package("pub_lib", {"include/a.h": ("file", b"int a;\n")}, {})
    package("priv_tools", {"bin/tool": ("executable", b"\x7fELF tool bytes"),
                           "conaninfo.txt": ("file", b"[settings]\n")}, {"bin/tool": "published"})
    package("priv_cache", {"bin/det": ("executable", b"\x7fELF det bytes")}, {"bin/det": "run-from-cache"})
    package("priv_server", {"bin/server": ("executable", b"\x7fELF server bytes")}, {"bin/server": "published"})
    server_key = next(step["key"] for step in steps if step["id"] == "priv_server/package")
    archive = b"an OCI archive's bytes"
    (store / "objects" / sha256_bytes(archive)).write_bytes(archive)
    image = put({"step": {"kind": "image", "subject": "srv-image", "repository": "priv-repo", "reproducible": False},
                 "run": "fixture-run",
                 "executables": [{"package": "priv_server", "entry": "bin/server",
                                  "digest": sha256_bytes(b"\x7fELF server bytes"), "record": server_key}]},
                {"image": {"archive": sha256_bytes(archive), "manifest": "sha256:" + "d" * 64, "reproducible": False}})
    steps.append({"id": "srv-image/image", "key": image, "verdict": None})
    tool_entry = sha256_bytes(b"\x7fELF tool bytes")
    consumer = put({"step": {"kind": "verdict", "id": "consumer", "subject": "priv_tools"},
                    "judged": {"bin/tool": tool_entry}},
                   {"consumer": {"verdict": "pass", "findings": sha256_bytes(canonical([]))}})
    steps.append({"id": "priv_tools/consumer", "key": consumer, "verdict": "pass"})
    record = canonical({"version": "9.9.9", "built": True, "cuttable": True, "steps": steps})
    plan = {"format": PLAN_FORMAT, "repositories": {
        "pub-repo": {"visibility": "public", "assets": [
            {"name": "pub_lib-9.9.9.tgz", "kind": "conan-transport", "package": "pub_lib", "source": "pub-repo"},
            {"name": "tool-linux-x64", "kind": "entry", "package": "priv_tools", "source": "priv-repo",
             "entry": "bin/tool", "verdicts": ["consumer"]}]},
        "priv-repo": {"visibility": "private", "assets": [
            {"name": "priv_tools-9.9.9.tgz", "kind": "conan-transport", "package": "priv_tools",
             "source": "priv-repo"}],
            "images": {"srv-image": {"dockerfile": "docker/Dockerfile", "runtime": ["libstdc++.so.6"],
                                     "executables": [{"package": "priv_server", "entry": "bin/server",
                                                      "source": "priv-repo"}]}}}}}
    return store, record, plan, transports


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

    def edited(plan: dict, change: Callable[[dict], None]) -> dict:
        copy = json.loads(json.dumps(plan))
        change(copy)
        return copy

    def rewrite(store: Path, step_id: str, record: bytes, change: Callable[[dict], None]) -> None:
        key = next(step["key"] for step in json.loads(record)["steps"] if step["id"] == step_id)
        path = store / "records" / f"{key}.json"
        body = json.loads(path.read_text())
        change(body)
        path.write_text(json.dumps(body))

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        store, record, plan, transports = _fixture(root / "a")
        built = arm("a green record and a valid plan build a manifest and one slice per repository",
                    lambda: build(store, record, plan), None)
        if built:
            manifest, slices = built
            pub = json.loads(slices["pub-repo"])
            if sorted(slices) != ["priv-repo", "pub-repo"] or pub["manifest"] != sha256_bytes(manifest):
                failures.append("the slices do not name the whole manifest's digest, or a repository is missing")
            if any("priv_tools-9.9.9.tgz" == asset["name"] for asset in pub["assets"]):
                failures.append("the public slice carries the private repository's asset")
            if b"priv_cache" in slices["pub-repo"]:
                failures.append("the public slice names a package it does not publish")
            tool_row = next((asset for asset in pub["assets"] if asset["name"] == "tool-linux-x64"), {})
            tool_digest = sha256_bytes(b"\x7fELF tool bytes")
            if tool_row.get("object") != tool_digest or \
                    (store / "objects" / tool_digest).read_bytes() != b"\x7fELF tool bytes" or \
                    transports["priv_tools"].name.encode() in slices["pub-repo"]:
                failures.append(f"the public entry asset does not name the entry's own bytes, or names the "
                                f"private transport: {tool_row}")
            image_row = next((asset for asset in json.loads(slices["priv-repo"])["assets"]
                              if asset["name"] == "srv-image"), {})
            if image_row.get("kind") != IMAGE_KIND or image_row.get("manifest") != "sha256:" + "d" * 64:
                failures.append(f"the image is not an asset of the repository that declares it: {image_row}")

        arm("a step-0 record that is not cuttable writes no manifest",
            lambda: build(store, canonical({**json.loads(record), "cuttable": False}), plan), "cuttable=False")
        arm("a private source's transport aimed at a public repository is refused (N360 plan F9)",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["pub-repo"]["assets"].append(
                {"name": "priv_tools-9.9.9.tgz", "kind": "conan-transport", "package": "priv_tools",
                 "source": "priv-repo"}))), "carries the sources of the PRIVATE")
        arm("an asset whose package step 0 never ran is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "ghost.tgz", "kind": "conan-transport", "package": "ghost", "source": "priv-repo"}))),
            "step 0 ran no step ghost/package")
        arm("an entry classified run-from-cache cannot be published (DN-142.D22 (3), direction 1)",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "det", "kind": "entry", "package": "priv_cache", "source": "priv-repo",
                 "entry": "bin/det"}))), "not 'published'")
        arm("a published executable no asset carries is refused (DN-142.D22 (3), direction 2)",
            lambda: build(store, record, edited(plan, lambda p: (
                p["repositories"]["priv-repo"].__setitem__("assets", []),
                p["repositories"]["pub-repo"]["assets"].pop()))), "no asset or image of this manifest carries it")
        arm("a published executable whose image the plan drops is refused (DN-142.D22 (3), direction 2)",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"].pop("images"))),
            "priv_server: bin/server is classified 'published' and no asset or image")
        arm("an image step 0 did not build is refused",
            lambda: build(store, canonical({**json.loads(record), "steps": [
                step for step in json.loads(record)["steps"] if step["id"] != "srv-image/image"]}), plan),
            "step 0 ran no step srv-image/image")
        arm("an image that consumed an executable the plan does not declare is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["images"]["srv-image"]
                                                ["executables"].append({"package": "priv_tools", "entry": "bin/tool",
                                                                        "source": "priv-repo"}))),
            "its image step consumed")
        arm("an entry asset whose named verdict does not exist is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["pub-repo"]["assets"][1]
                                                .__setitem__("verdicts", ["consumer", "container"]))),
            "no container verdict")

        store_b, record_b, plan_b, _ = _fixture(root / "b")
        rewrite(store_b, "priv_tools/consumer", record_b,
                lambda body: body["inputs"]["judged"].__setitem__("bin/tool", "0" * 64))
        arm("a verdict that judged another digest does not evaluate the published one",
            lambda: build(store_b, record_b, plan_b), "not the published digest")
        store_c, record_c, plan_c, _ = _fixture(root / "c")
        rewrite(store_c, "pub_lib/verdict", record_c,
                lambda body: body["outputs"]["verdict"].__setitem__("verdict", "fail"))
        arm("a package whose test verdict failed is not published",
            lambda: build(store_c, record_c, plan_c), "its verdict verdict is 'fail'")
        store_e, record_e, plan_e, _ = _fixture(root / "e")
        rewrite(store_e, "srv-image/image", record_e,
                lambda body: body["inputs"]["executables"][0].__setitem__("digest", "0" * 64))
        arm("an image whose step consumed another digest than this run's create stored is refused",
            lambda: build(store_e, record_e, plan_e), "and this run's create stored")
        store_d, record_d, plan_d, transports_d = _fixture(root / "d")
        transports_d["pub_lib"].write_bytes(b"tampered")
        arm("a transport object that does not hash to its name is refused",
            lambda: build(store_d, record_d, plan_d), "does not hash to its name")

        keys = root / "keys"
        keys.mkdir()
        for name in ("writer", "other"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(keys / name)],
                           check=True, stdin=subprocess.DEVNULL)
        allowed = keys / "allowed_signers"
        allowed.write_text(f'{WRITER_IDENTITY} namespaces="{NAMESPACE}" '
                           f"{(keys / 'writer.pub').read_text().strip()}\n")
        if built:
            _manifest, slices = built
            slice_path = root / "pub-repo.json"
            slice_path.write_bytes(slices["pub-repo"])
            signature = sign(slice_path, keys / "writer")
            assets = arm("the writer's signature verifies and the slice's assets come back",
                         lambda: verify(slices["pub-repo"], signature, allowed, "pub-repo"), None)
            if assets is not None and [asset["name"] for asset in assets] != ["pub_lib-9.9.9.tgz", "tool-linux-x64"]:
                failures.append(f"the verified slice lists {[asset['name'] for asset in assets]}")
            arm("one flipped byte fails the signature",
                lambda: verify(slices["pub-repo"].replace(b"9.9.9", b"9.9.8", 1), signature, allowed, "pub-repo"),
                "does not verify")
            arm("a slice for another repository is refused",
                lambda: verify(slices["pub-repo"], signature, allowed, "priv-repo"), "not 'priv-repo''s")
            forged = root / "forged.json"
            forged.write_bytes(slices["pub-repo"])
            forged_signature = sign(forged, keys / "other")
            arm("a slice signed by any key but the writer's is refused",
                lambda: verify(slices["pub-repo"], forged_signature, allowed, "pub-repo"), "does not verify")
            smuggled = json.loads(slices["pub-repo"])
            smuggled["assets"].append({"name": "x.tgz", "kind": "conan-transport", "source": "priv-repo",
                                       "object": "a" * 64})
            smuggled_path = root / "smuggled.json"
            smuggled_path.write_bytes(canonical(smuggled))
            smuggled_signature = sign(smuggled_path, keys / "writer")
            arm("even a writer-signed public slice may not carry another repository's transport",
                lambda: verify(canonical(smuggled), smuggled_signature, allowed, "pub-repo"),
                "a transport from priv-repo in the public")
            wrapped = json.loads(slices["pub-repo"])
            next(asset for asset in wrapped["assets"] if asset["kind"] == "entry")["object"] = \
                transports["priv_tools"].name
            wrapped_path = root / "wrapped.json"
            wrapped_path.write_bytes(canonical(wrapped))
            wrapped_signature = sign(wrapped_path, keys / "writer")
            arm("even a writer-signed slice may not ship an entry inside its package's transport",
                lambda: verify(canonical(wrapped), wrapped_signature, allowed, "pub-repo"),
                "is not its entry digest")

        arm("the writer key the allowed-signers file names is the signer",
            lambda: signer(keys / "writer", allowed), None)
        arm("any other key is refused as the signer before anything is signed",
            lambda: signer(keys / "other", allowed), "is not the one writer key")
        store_s, record_s, plan_s, _ = _fixture(root / "s")
        sealed = arm("the seal signs the manifest and every slice and keeps them under one manifest record",
                     lambda: seal(store_s, record_s, plan_s, keys / "writer", root / "seal-1"), None)
        if sealed:
            kept = json.loads(next(path.read_text() for path in (store_s / "records").glob("*.json")
                                   if json.loads(path.read_text())["inputs"]["step"]["kind"] == "manifest"))
            if kept["outputs"]["manifest"] != sealed or sorted(sealed["slices"]) != ["priv-repo", "pub-repo"] or \
                    (store_s / "objects" / sealed["record"]).read_bytes() != record_s:
                failures.append(f"the seal's record does not keep the record, manifest and slices: {kept}")
            for repo in ("priv-repo", "pub-repo"):
                arm(f"the sealed {repo} slice verifies under the writer key",
                    lambda repo=repo: verify((root / "seal-1" / "slices" / f"{repo}.json").read_bytes(),
                                             root / "seal-1" / "slices" / f"{repo}.json.sig", allowed, repo), None)
        arm("sealing the same record again keeps the same seal, never a second one",
            lambda: seal(store_s, record_s, plan_s, keys / "writer", root / "seal-2"), None)
        arm("a seal over an output directory that exists is refused",
            lambda: seal(store_s, record_s, plan_s, keys / "writer", root / "seal-1"), "exists")

        tool = sha256_bytes(b"\x7fELF tool bytes")
        out = root / "tool-linux-x64"
        arm("the published entry is extracted from its transport, verified",
            lambda: extract(transports["priv_tools"], "bin/tool", tool, out), None)
        if not out.is_file() or out.read_bytes() != b"\x7fELF tool bytes":
            failures.append("the extracted entry's bytes differ")
        arm("an entry whose bytes differ from the slice's digest is refused, nothing written",
            lambda: extract(transports["priv_tools"], "bin/tool", "b" * 64, root / "bad"), "hashes to")
        if (root / "bad").exists():
            failures.append("a refused extraction left its bytes behind")
        arm("an entry the transport does not hold is refused",
            lambda: extract(transports["priv_tools"], "bin/absent", tool, root / "absent"), "holds 0")

    for failure in failures:
        print(f"store_manifest selftest FAILED: {failure}")
    print(f"store_manifest selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 5 and argv[0] == "build":
            manifest, slices = build(Path(argv[1]), Path(argv[2]).read_bytes(),
                                     json.loads(Path(argv[3]).read_text()))
            out = Path(argv[4])
            (out / "slices").mkdir(parents=True, exist_ok=True)
            (out / "manifest.json").write_bytes(manifest)
            for repo, body in slices.items():
                (out / "slices" / f"{repo}.json").write_bytes(body)
            print(f"store_manifest: manifest {sha256_bytes(manifest)}, {len(slices)} slice(s) in {out}")
            return 0
        if len(argv) == 6 and argv[0] == "seal":
            summary = seal(Path(argv[1]), Path(argv[2]).read_bytes(), json.loads(Path(argv[3]).read_text()),
                           Path(argv[4]), Path(argv[5]))
            print(f"store_manifest: SEALED record {summary['record']} manifest {summary['manifest']['object']} "
                  + " ".join(f"{repo}={held['object']}" for repo, held in sorted(summary["slices"].items())))
            return 0
        if len(argv) == 3 and argv[0] == "signer":
            print(f"store_manifest: SIGNER {signer(Path(argv[1]), Path(argv[2]))}")
            return 0
        if len(argv) == 3 and argv[0] == "sign":
            print(sign(Path(argv[1]), Path(argv[2])))
            return 0
        if len(argv) == 5 and argv[0] == "verify":
            for asset in verify(Path(argv[1]).read_bytes(), Path(argv[2]), Path(argv[3]), argv[4]):
                print(asset["kind"], asset["name"], asset["object"], asset.get("entry", "-"),
                      asset.get("entry_digest", "-"))
            return 0
        if len(argv) == 5 and argv[0] == "extract":
            extract(Path(argv[1]), argv[2], argv[3], Path(argv[4]))
            print(f"store_manifest: EXTRACTED {argv[2]} sha256:{argv[3]} -> {argv[4]}")
            return 0
    except Refused as refused:
        print(f"store_manifest: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
