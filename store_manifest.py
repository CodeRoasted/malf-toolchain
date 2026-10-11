#!/usr/bin/env python3
"""A cut's publication manifest and its signed per-repository slices: built by the writer, verified by every publish job.

DN-142.D9: at the end of a green step 0 the writer lists, for every repository, each artefact it
publishes by digest, and an artefact enters only if it is EVALUATED (DN-142.D7): its package's
step-0 test verdict, its corpus verdict where one exists, and every verdict its asset names, each a
`pass` record whose `judged` holds the very digest published. DN-142.D20 (4): each repository
receives its own SLICE, `{repository, the step-0 record's digest, the whole manifest's digest, that
repository's assets}`, signed by the writer (DN-142.D8, `ssh-keygen -Y sign`, namespace
`coderoast-store`), so a public Release never carries the private inventory. DN-142.D22 (3): the
manifest and the `executables:` declarations agree in both directions — every executable an asset
carries is classified `published`, and every `published` executable of the run is carried by an
asset. N360 plan F9: a conan transport carries its recipe's SOURCES, so a transport whose source
repository is private never reaches a public repository; a single entry (a binary) may.

THE PLAN is the builder's declared input: which repository publishes what, under which name.

    {"format": "coderoast.store.plan/1",
     "repositories": {<repo>: {"visibility": "public" | "private",
                               "assets": [{"name", "kind": "conan-transport", "package", "source"},
                                          {"name", "kind": "entry", "package", "source", "entry",
                                           "verdicts": [<output>...]}]}}}

    store_manifest.py build <store> <step-0 record> <plan.json> <out dir>
        <out dir>/manifest.json and <out dir>/slices/<repo>.json, canonical bytes; exit 1 naming
        every asset refused (nothing is written then)
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
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

PLAN_FORMAT = "coderoast.store.plan/1"
MANIFEST_FORMAT = "coderoast.store.manifest/1"
SLICE_FORMAT = "coderoast.store.slice/1"
NAMESPACE = "coderoast-store"
WRITER_IDENTITY = "coderoast-store-writer"
KINDS = ("conan-transport", "entry")
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
    return found


def build(store: Path, record_bytes: bytes, plan: dict) -> tuple[bytes, dict[str, bytes]]:
    """(the whole manifest, {repo: slice}) as canonical bytes, or Refused naming every finding."""
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
        step = steps.get(f"{package}/{output}")
        if step is None:
            raise Refused(f"{package}: no {output} verdict in this step-0 run")
        body = stored(f"{package}/{output}")["outputs"][output]
        if step.get("verdict") != "pass" or body.get("verdict") != "pass":
            raise Refused(f"{package}: its {output} verdict is {body.get('verdict')!r}, not pass")
        if digest not in (body.get("judged") or {}).values():
            raise Refused(f"{package}: its {output} verdict judged {sorted((body.get('judged') or {}).values())}, "
                          f"not the published digest {digest}")

    visibility = {repo: body["visibility"] for repo, body in plan["repositories"].items()}
    published_of: dict[str, dict[str, str]] = {}
    entries_of: dict[str, list[list[str]]] = {}
    carried: set[tuple[str, str]] = set()
    whole: dict[str, dict] = {}
    for repo, body in sorted(plan["repositories"].items()):
        rows = []
        for asset in body.get("assets", []):
            package = asset["package"]
            try:
                package_record = stored(f"{package}/package")
                output = package_record["outputs"]["package"]
                executables = package_record["inputs"]["step"].get("executables", {})
                entries = output["entries"]
                transport = output["transport"]
                object_path = store / "objects" / transport
                if not object_path.is_file() or sha256_file(object_path) != transport:
                    raise Refused(f"{package}: the store's object {transport} is absent or does not hash to its name")
                evaluated(package, "verdict", output["content"])
                if f"{package}/corpus" in steps:
                    evaluated(package, "corpus", output["content"])
                row = {"name": asset["name"], "kind": asset["kind"], "package": package,
                       "source": asset["source"], "record": steps[f"{package}/package"]["key"],
                       "object": transport, "content": output["content"]}
                if asset["kind"] == "conan-transport":
                    if visibility[repo] == "public" and visibility[asset["source"]] == "private":
                        raise Refused(f"{package}'s transport carries the sources of the PRIVATE "
                                      f"{asset['source']}, and {repo} is public")
                    members = [path for path, mode, _digest in entries if mode == "executable"]
                else:
                    held = {path: digest for path, _mode, digest in entries}
                    if asset["entry"] not in held:
                        raise Refused(f"{package} holds no entry {asset['entry']}")
                    row.update(entry=asset["entry"], entry_digest=held[asset["entry"]])
                    members = [asset["entry"]]
                for output_name in asset.get("verdicts", []):
                    evaluated(package, output_name,
                              row.get("entry_digest") or output["content"])
                for member in members:
                    if executables.get(member) != "published":
                        raise Refused(f"{package}: {asset['name']} carries {member}, classified "
                                      f"{executables.get(member, 'nowhere')!r}, not 'published' (DN-142.D22 (3))")
                    carried.add((package, member))
                published_of[package] = executables
                entries_of[package] = entries
                rows.append(row)
            except Refused as refused:
                findings.append(f"{repo}/{asset['name']}: {refused}")
        whole[repo] = {"visibility": visibility[repo], "assets": sorted(rows, key=lambda row: row["name"])}
    for step_id in sorted(steps):
        package, _slash, output = step_id.partition("/")
        if output != "package":
            continue
        declared = stored(step_id)["inputs"]["step"].get("executables", {})
        for member, kind in sorted(declared.items()):
            if kind == "published" and (package, member) not in carried:
                findings.append(f"{package}: {member} is classified 'published' and no asset of this "
                                f"manifest carries it (DN-142.D22 (3))")
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
    done = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(path)],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Refused(f"ssh-keygen could not sign {path}: {done.stderr.strip()[-300:]}")
    return path.with_name(path.name + ".sig")


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
        if not DIGEST_HEX.match(asset.get("object", "")):
            raise Refused(f"{asset.get('name')}: {asset.get('object')!r} is not an object digest")
        if asset.get("kind") == "entry" and not DIGEST_HEX.match(asset.get("entry_digest", "")):
            raise Refused(f"{asset.get('name')}: no entry digest")
        if (body.get("visibility") == "public" and asset.get("kind") == "conan-transport"
                and asset.get("source") != repository):
            raise Refused(f"{asset.get('name')}: a transport from {asset.get('source')} in the public "
                          f"{repository}'s slice")
    return body.get("assets", [])


def extract(archive: Path, entry: str, digest: str, out: Path) -> None:
    """The one package member `<…>/p/<entry>` of a `conan cache save` archive, verified."""
    with tarfile.open(archive, "r:*") as bundle:
        members = [member for member in bundle.getmembers()
                   if member.isfile() and re.fullmatch(rf"[^/]+/p/{re.escape(entry)}", member.name)]
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
                info = tarfile.TarInfo(f"{name[:5]}0123/p/{path}")
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
    tool_entry = sha256_bytes(b"\x7fELF tool bytes")
    consumer = put({"step": {"kind": "verdict", "package": "priv_tools", "id": "consumer"}},
                   {"consumer": {"verdict": "pass", "judged": {"bin/tool": tool_entry}}})
    steps.append({"id": "priv_tools/consumer", "key": consumer, "verdict": "pass"})
    record = canonical({"version": "9.9.9", "built": True, "cuttable": True, "steps": steps})
    plan = {"format": PLAN_FORMAT, "repositories": {
        "pub-repo": {"visibility": "public", "assets": [
            {"name": "pub_lib-9.9.9.tgz", "kind": "conan-transport", "package": "pub_lib", "source": "pub-repo"},
            {"name": "tool-linux-x64", "kind": "entry", "package": "priv_tools", "source": "priv-repo",
             "entry": "bin/tool", "verdicts": ["consumer"]}]},
        "priv-repo": {"visibility": "private", "assets": [
            {"name": "priv_tools-9.9.9.tgz", "kind": "conan-transport", "package": "priv_tools",
             "source": "priv-repo"}]}}}
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
                p["repositories"]["pub-repo"]["assets"].pop()))), "no asset of this manifest carries it")
        arm("an entry asset whose named verdict does not exist is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["pub-repo"]["assets"][1]
                                                .__setitem__("verdicts", ["consumer", "container"]))),
            "no container verdict")

        store_b, record_b, plan_b, _ = _fixture(root / "b")
        rewrite(store_b, "priv_tools/consumer", record_b,
                lambda body: body["outputs"]["consumer"]["judged"].__setitem__("bin/tool", "0" * 64))
        arm("a verdict that judged another digest does not evaluate the published one",
            lambda: build(store_b, record_b, plan_b), "not the published digest")
        store_c, record_c, plan_c, _ = _fixture(root / "c")
        rewrite(store_c, "pub_lib/verdict", record_c,
                lambda body: body["outputs"]["verdict"].__setitem__("verdict", "fail"))
        arm("a package whose test verdict failed is not published",
            lambda: build(store_c, record_c, plan_c), "its verdict verdict is 'fail'")
        store_d, record_d, plan_d, transports_d = _fixture(root / "d")
        transports_d["pub_lib"].write_bytes(b"tampered")
        arm("a transport object that does not hash to its name is refused",
            lambda: build(store_d, record_d, plan_d), "does not hash to its name")

        keys = root / "keys"
        keys.mkdir()
        for name in ("writer", "other"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(keys / name)],
                           check=True)
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
