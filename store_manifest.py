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
carried by an asset or an image.

A ROW BELONGS TO THE SLICE OF THE REPOSITORY THAT DECLARES IT, ITS OWNER (DN-142.D25 (2)): the
repository whose tag runs the publish job and whose store package holds the bytes. Where the row is
published is its DESTINATION, a field of the row: the owner's own Release, or a FOREIGN Release
the owner's plan entry declares under `destinations` with its tag. The structural refusals, here
and again in the deriver: a `conan-transport` row never has a foreign destination (N360 plan F9: a
transport carries its recipe's SOURCES, so it reaches only its own repository's Release, whose
visibility is its own); one (destination, name) pair has one row; one destination Release
(destination, tag) has one owner, the owner's own Release being `v<version>`.

AN ENTRY ASSET'S OBJECT IS THE ENTRY'S OWN BYTES, NEVER ITS PACKAGE'S TRANSPORT. The builder writes
the one member into the store under its entry digest, verified, and the slice names that object:
a public destination receives the executable, never the private package's sources around it.

AN IMAGE IS AN ASSET OF THE REPOSITORY THAT DECLARES IT (DN-142.D9; N360 plan C1): the plan names
each image with its executables, and step 0's image step (`store_image.py`) stores the image as
one OCI archive under a record `<image>/image` naming the entries it consumed. The builder admits
the image only when that record consumed exactly the plan's executables at this run's entry
digests, and counts each of them as carried.

AN OBJECT IS A MEMBER OF A STEP'S OUTPUT (DN-142.D25 (1)): a published byte that is no executable,
resolved the one way every row is, step id -> the record this step-0 run stored -> the output's
`objects` member -> bytes whole in the store. Three producers, one row: a TRACKED file (the keyed
step `<package>/tracked`, which judges nothing), a VERDICT's output (admitted only when that record's
verdict is `pass`, the record then its one verdict), and a HOSTED output, the ingestion record
`<repository>/ingest.<workflow stem>` B5 wrote (DN-142.D25 (3)), re-checked here: its step names the
row's owner, workflow and artifact, its title names THIS step-0 run (the record's `step0_run`), its
printed and writer-recomputed digests are the object, and it carries its dated exception. Every row
of every kind lists `verdicts`, the key of every verdict record the builder checked for it.

A DESTINATION SLICE (`coderoast.store.destination-slice/1`, DN-142.D25 (2)) is what a FOREIGN
Release receives: `{format, owner, destination, tag, version, step0_record, manifest, assets}`, its
rows the owner's rows for that destination PROJECTED to `{name, kind, object, record, verdicts}`, so
a public Release gets the proof of its own assets and none of the private inventory around them.
The writer signs it at the one seal, it is kept as an object, and the owner's slice binds it and its
signature by digest under `destinations: {<repository>: {tag, visibility, slice, signature}}`. Its
format is its own, so a destination slice never verifies as an owner slice, nor the reverse. A
repository that owns no row is no slice's repository: the seal never creates a store package for a
repository that is a destination only.

THE PLAN is the builder's declared input: which repository publishes what, under which name.

    {"format": "coderoast.store.plan/3",
     "visibility": {<repo>: "public" | "private", ... every owner and every destination},
     "repositories": {<owner>: {"assets": [{"name", "kind": "conan-transport", "package", "destination"},
                                           {"name", "kind": "entry", "package", "entry", "destination",
                                            "verdicts": [<output>...]},
                                           {"name", "kind": "entry", "step", "member", "destination",
                                            "verdicts": [], "artifact"} (an INGESTED executable,
                                            DN-142.D25 (4) A: a row's kind follows its bytes),
                                           {"name", "kind": "object", "step", "member", "destination",
                                            "verdicts": [], "artifact" (an ingested row only)}],
                                "destinations": {<foreign repo>: {"tag"}},
                                "images": {<image>: {"dockerfile", "runtime": [<library>...],
                                                     "executables": [{"package", "entry", "source"}]}}}}}

    store_manifest.py build <store> <step-0 record> <plan.json> <out dir>
        <out dir>/manifest.json, <out dir>/slices/<repo>.json and
        <out dir>/destinations/<owner>.<destination>.json, canonical bytes, UNSIGNED (a binding's
        `signature` is null until the seal); exit 1 naming every asset refused (no manifest is
        written then; entry objects already verified stay in the store, content-addressed)
    store_manifest.py seal <store> <step-0 record> <plan.json> <private key> <out dir>
        build, signing every destination slice before the owner slice that binds it, then the
        manifest and every slice; keep the record, the manifest, each slice and each signature in
        the store under one `manifest` record keyed by the step-0 record's digest; the slices,
        destination slices and signatures stay in <out dir> for the replica
    store_manifest.py signer <private key> <allowed signers>
        exit 0 printing the key's public half only when it is the one writer key the file names
    store_manifest.py sign <file> <private key>
        <file>.sig, `ssh-keygen -Y sign -n coderoast-store`
    store_manifest.py verify <slice> <signature> <allowed signers> <repository>
        exit 0 and one line per asset, `<kind> <name> <object> <entry> <entry digest>` ("-" when
        absent), only when the signature verifies for the writer identity, the slice is an OWNER
        slice naming <repository>, every row names its destination, a foreign one bound by the
        slice, and no transport has a foreign destination
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

PLAN_FORMAT = "coderoast.store.plan/3"
MANIFEST_FORMAT = "coderoast.store.manifest/3"
SLICE_FORMAT = "coderoast.store.slice/3"
DESTINATION_FORMAT = "coderoast.store.destination-slice/1"
NAMESPACE = "coderoast-store"
WRITER_IDENTITY = "coderoast-store-writer"
KINDS = ("conan-transport", "entry", "object")
# The class an ingestion record must hold for each row kind it can feed (DN-142.D25 (4) A): the
# ingestion judged the bytes against the declaration, and the row's kind follows them.
INGESTED_CLASS = {"entry": "executable", "object": "object"}
PROJECTED = ("name", "kind", "object", "record", "verdicts")
BINDING = {"tag", "visibility", "slice", "signature"}
IMAGE_KIND = "oci-image"
IMAGE_STEP = "image"
IMAGE_MANIFEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RUNTIME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
VISIBILITIES = ("public", "private")
REPOSITORY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
TAG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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


def own_tag(version: object) -> str:
    """The tag an owner's own Release publishes under: the cut's."""
    return f"v{version}"


def _plan_findings(plan: dict, version: object) -> list[str]:
    found = []
    if plan.get("format") != PLAN_FORMAT:
        return [f"the plan's format is {plan.get('format')!r}, not {PLAN_FORMAT!r}"]
    repositories = plan.get("repositories")
    if not isinstance(repositories, dict) or not repositories:
        return ["the plan names no repository"]
    visibility = plan.get("visibility")
    if not isinstance(visibility, dict):
        return ["the plan carries no top-level visibility map"]
    for repo, seen in sorted(visibility.items()):
        if not REPOSITORY_NAME.match(repo) or seen not in VISIBILITIES:
            found.append(f"{repo!r}: visibility {seen!r} is not public or private")
    named: dict[tuple[str, str], list[str]] = {}
    claimed: dict[tuple[str, str], set[str]] = {}
    images: set[str] = set()
    for repo, body in sorted(repositories.items()):
        if not REPOSITORY_NAME.match(repo):
            found.append(f"{repo!r} is not a repository name")
        if repo not in visibility:
            found.append(f"{repo}: the plan states no visibility for it")
        foreign = body.get("destinations") or {}
        for destination, declared in sorted(foreign.items()):
            if destination == repo or destination not in visibility or not isinstance(declared, dict) \
                    or set(declared) != {"tag"} or not TAG_NAME.match(str(declared["tag"])):
                found.append(f"{repo}: destination {destination!r} is not a foreign repository of the plan "
                             f"with a {{tag}}: {declared!r}")
        for asset in body.get("assets", []):
            where = f"{repo}: asset {asset.get('name')!r}"
            destination = asset.get("destination")
            if asset.get("kind") not in KINDS:
                found.append(f"{where}: kind {asset.get('kind')!r} is not one of {', '.join(KINDS)}")
            if not ASSET_NAME.match(str(asset.get("name", ""))):
                found.append(f"{where}: not an asset file name")
            if "source" in asset:
                found.append(f"{where}: a row's source is its owner, never a field of it (DN-142.D25 (2))")
            if destination != repo and destination not in foreign:
                found.append(f"{where}: its destination {destination!r} is neither {repo} nor a destination "
                             f"{repo} declares")
                continue
            if asset.get("kind") == "conan-transport" and destination != repo:
                found.append(f"{where}: a transport carries {repo}'s sources, so it is published on {repo}'s "
                             f"own Release, never {destination}'s (N360 plan F9, DN-142.D25 (2))")
            if asset.get("kind") == "entry" and "step" in asset:
                found += [f"{where}: {defect}" for defect in _ingested_entry_defects(repo, asset)]
            elif asset.get("kind") == "entry" and not asset.get("entry"):
                found.append(f"{where}: an entry asset names no entry")
            if asset.get("kind") == "object":
                found += [f"{where}: {defect}" for defect in _object_row_defects(repo, asset)]
            named.setdefault((destination, asset.get("name")), []).append(repo)
            tag = own_tag(version) if destination == repo else foreign[destination]["tag"]
            claimed.setdefault((destination, tag), set()).add(repo)
        for image, declared in sorted((body.get("images") or {}).items()):
            where = f"{repo}: image {image!r}"
            if not ASSET_NAME.match(image) or image in images or (repo, image) in named:
                found.append(f"{where}: not an image name, or named twice")
            images.add(image)
            claimed.setdefault((repo, own_tag(version)), set()).add(repo)
            if not isinstance(declared.get("dockerfile"), str) or not declared["dockerfile"] \
                    or declared["dockerfile"].startswith("/") or ".." in declared["dockerfile"].split("/"):
                found.append(f"{where}: its dockerfile {declared.get('dockerfile')!r} is not a path in its repository")
            if any(not RUNTIME_NAME.match(str(name)) for name in declared.get("runtime") or []):
                found.append(f"{where}: a runtime library is a file name, not {declared.get('runtime')!r}")
            carried = declared.get("executables") or []
            if not carried:
                found.append(f"{where}: it carries no executable")
            for executable in carried:
                if set(executable) != {"package", "entry", "source"} or executable["source"] not in visibility:
                    found.append(f"{where}: an executable is {{package, entry, source}} from a plan "
                                 f"repository, not {executable!r}")
        if not body.get("assets") and not body.get("images"):
            aimed = sorted(owner for owner, other in repositories.items()
                           if repo in (other.get("destinations") or {}))
            found.append(f"{repo}: it owns no row and no image"
                         + (f", and it is a destination only (of {', '.join(aimed)})" if aimed else "")
                         + ": the seal never creates a store package nobody publishes from (DN-142.D25 (2))")
    for (destination, name), owners in sorted(named.items(), key=str):
        if len(owners) > 1:
            found.append(f"{destination}: asset {name!r} has {len(owners)} rows ({', '.join(owners)}): one "
                         f"(destination, name) pair has one row")
    for (destination, tag), owners in sorted(claimed.items()):
        if len(owners) > 1:
            found.append(f"the {destination} Release {tag} has {len(owners)} owners, {', '.join(sorted(owners))}: "
                         f"one destination Release has one owner per cut (DN-142.D25 (2))")
    return found


def ingested_step(step_id: str) -> bool:
    """Whether `step_id` is an ingestion record's, `<repository>/ingest.<workflow stem>`."""
    return step_id.partition("/")[2].startswith(f"{artefact_store.INGEST_OUTPUT}.")


def output_of(step_id: str) -> str:
    """The output a step's objects ride: the id's tail up to its first `.` (`tracked`, `ingest`,
    or a verdict's own output name)."""
    return step_id.partition("/")[2].partition(".")[0]


def _object_row_defects(owner: str, asset: dict) -> list[str]:
    """Every way an `object` plan row misses its shape (DN-142.D25 (1)); empty when it has it."""
    step = str(asset.get("step", ""))
    member = asset.get("member")
    found = []
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", step):
        found.append(f"an object row names its producing step `<subject>/<output>`, not {step!r}")
    if artefact_store.member_defect(member):
        found.append(f"its member {artefact_store.member_defect(member)}")
    if asset.get("verdicts"):
        found.append(f"an object row names no verdict: a verdict output's own record is its judgment, "
                     f"not {asset['verdicts']!r}")
    if ingested_step(step):
        if step.partition("/")[0] != owner:
            found.append(f"its ingestion step {step} is another repository's: a hosted output is "
                         f"ingested by the repository that declares it")
        if not ASSET_NAME.match(str(asset.get("artifact", ""))):
            found.append("an ingested row names the artifact its record must have read")
    elif "artifact" in asset:
        found.append("only an ingested row names an artifact")
    return found


def _ingested_entry_defects(owner: str, asset: dict) -> list[str]:
    """Every way an INGESTED entry row misses its shape (DN-142.D25 (4) A): a published executable a
    hosted run produced is an `entry` whoever produced it, resolved like an ingested object, step ->
    record -> member; empty when it has it."""
    found = [defect for defect in _object_row_defects(owner, asset)]
    if not ingested_step(str(asset.get("step", ""))):
        found.append(f"an entry row naming a step names an ingestion step `<repository>/ingest.<workflow>`, "
                     f"not {asset.get('step')!r}: a package's entry is named by `package` and `entry`")
    if "package" in asset or "entry" in asset:
        found.append("an ingested entry row names its step and member, never a package or an entry")
    return found


def project(rows: list[dict], destination: str) -> list[dict]:
    """The owner's rows for `destination`, PROJECTED to what a destination Release may learn."""
    return sorted(({field: row[field] for field in PROJECTED} for row in rows if row["destination"] == destination),
                  key=lambda row: row["name"])


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


Signer = Callable[[str, bytes], bytes]


def build(store: Path, record_bytes: bytes, plan: dict, sign_with: Signer | None = None
          ) -> tuple[bytes, dict[str, bytes], dict[tuple[str, str], tuple[bytes, bytes | None]]]:
    """(the whole manifest, {owner: slice}, {(owner, destination): (destination slice, its
    signature)}) as canonical bytes, or Refused naming every finding. `sign_with(label, body)` signs
    each destination slice before the owner slice that binds it; without one (a provisional
    judgment) every binding's `signature` is null. Each entry asset's bytes are kept in the store
    under their entry digest on the way."""
    record = json.loads(record_bytes)
    findings = _plan_findings(plan, record.get("version"))
    if findings:
        raise Refused("; ".join(findings))
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

    def evaluated(package: str, output: str, digest: str) -> str:
        """The key of the run's `<package>/<output>` verdict, once it passed over `digest`. A test
        verdict keeps what it judged in its output; a generalized verdict (DN-142.D19 (10)) in its
        key document."""
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
        return step["key"]

    def created(package: str) -> tuple[dict, dict[str, str], list[str]]:
        """The package's stored output, once its transport object is whole and its test verdicts
        (and its corpus verdict, where it has one) passed over its content; its executable classes;
        the keys of those verdicts."""
        package_record = stored(f"{package}/package")
        output = package_record["outputs"]["package"]
        object_path = store / "objects" / output["transport"]
        if not object_path.is_file() or sha256_file(object_path) != output["transport"]:
            raise Refused(f"{package}: the store's object {output['transport']} is absent or does not hash to its name")
        verdicts = [evaluated(package, "verdict", output["content"])]
        if f"{package}/corpus" in steps:
            verdicts.append(evaluated(package, "corpus", output["content"]))
        return output, package_record["inputs"]["step"].get("executables", {}), verdicts

    def classified(package: str, executables: dict[str, str], member: str, carrier: str) -> None:
        if executables.get(member) != "published":
            raise Refused(f"{package}: {carrier} carries {member}, classified "
                          f"{executables.get(member, 'nowhere')!r}, not 'published' (DN-142.D22 (3))")
        carried.add((package, member))

    def produced(owner: str, asset: dict) -> dict:
        """An object row, or an ingested entry row, once its producing record holds the member whole
        and admits it."""
        step_id = asset["step"]
        built = stored(step_id)
        step = steps[step_id]
        output = output_of(step_id)
        try:
            digest = artefact_store.output_object(store, step["key"], output, asset["member"])
        except artefact_store.Refused as refused:
            raise Refused(str(refused)) from refused
        kind = built["inputs"]["step"].get("kind")
        body = built["outputs"][output]
        verdicts: list[str] = []
        if ingested_step(step_id):
            ingestion(owner, asset, built, body, digest)
            if asset["kind"] == "entry":
                return {"name": asset["name"], "kind": "entry", "destination": asset["destination"],
                        "step": step_id, "record": step["key"], "object": digest, "entry": asset["member"],
                        "entry_digest": digest, "verdicts": [step["key"]]}
        elif kind != artefact_store.TRACKED_KIND:
            if step.get("verdict") != "pass" or body.get("verdict") != "pass":
                raise Refused(f"its producing record {step_id} is a {kind} whose verdict is "
                              f"{body.get('verdict')!r}, not pass (DN-142.D7)")
            verdicts = [step["key"]]
        return {"name": asset["name"], "kind": "object", "destination": asset["destination"], "step": step_id,
                "record": step["key"], "object": digest, "verdicts": verdicts}

    def ingestion(owner: str, asset: dict, built: dict, body: dict, digest: str) -> None:
        """An ingestion record re-checked against the row and this run (DN-142.D25 (3)): two
        producers of what B5 saw never diverge silently."""
        document = built["inputs"]["step"]
        if document.get("kind") != artefact_store.INGEST_KIND or document.get("repository") != owner or \
                artefact_store.ingest_step_id(owner, str(document.get("workflow"))) != asset["step"] or \
                document.get("artefact") != asset["artifact"]:
            raise Refused(f"its record is the {document.get('kind')} of {document.get('repository')} "
                          f"{document.get('workflow')} artifact {document.get('artefact')}, not {owner}'s "
                          f"{asset['step']} artifact {asset['artifact']}")
        titled = artefact_store.STEP0_RUN.findall(str(body.get("title")))
        if record.get("step0_run") is None or titled != [str(record["step0_run"])]:
            raise Refused(f"its ingestion's title {body.get('title')!r} names step 0 run(s) {titled or 'none'}, "
                          f"and this record is run {record.get('step0_run')}: run X's outputs never enter "
                          f"run Y (DN-142.D25 (3))")
        producers = body.get("producers") or {}
        if producers.get("printed") != digest or producers.get("writer_recomputed") != digest:
            raise Refused(f"its ingestion's producers {producers} do not all name the object {digest}")
        defect = artefact_store.exception_defect(body.get("exception"))
        if defect:
            raise Refused(f"its ingestion: {defect}")
        declared = INGESTED_CLASS[asset["kind"]]
        if body.get("class") != declared:
            raise Refused(f"its ingestion recorded the class {body.get('class')!r}, and an ingested "
                          f"{asset['kind']} row publishes only a {declared!r} (DN-142.D25 (4) A: a row's "
                          f"kind follows its bytes)")

    visibility = plan["visibility"]
    carried: set[tuple[str, str]] = set()
    whole: dict[str, dict] = {}
    for repo, body in sorted(plan["repositories"].items()):
        rows = []
        for asset in body.get("assets", []):
            try:
                if asset["kind"] == "object" or "step" in asset:
                    rows.append(produced(repo, asset))
                    continue
                package = asset["package"]
                output, executables, verdicts = created(package)
                row = {"name": asset["name"], "kind": asset["kind"], "package": package,
                       "destination": asset["destination"], "record": steps[f"{package}/package"]["key"],
                       "object": output["transport"], "content": output["content"]}
                if asset["kind"] == "conan-transport":
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
                    verdicts.append(evaluated(package, output_name, row.get("entry_digest") or output["content"]))
                for member in members:
                    classified(package, executables, member, asset["name"])
                rows.append({**row, "verdicts": verdicts})
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
                carried_by_image, verdicts = [], set()
                for (package, entry), row in sorted(consumed.items()):
                    package_output, executables, judged_by = created(package)
                    verdicts.update(judged_by)
                    held = {path: digest for path, _mode, digest in package_output["entries"]}
                    if row.get("digest") != held.get(entry) or row.get("record") != steps[f"{package}/package"]["key"]:
                        raise Refused(f"its image step consumed {package} {entry} at {row.get('digest')}, and this "
                                      f"run's create stored {held.get(entry)}")
                    classified(package, executables, entry, image)
                    build_id = (package_output.get("build_ids") or {}).get(entry)
                    if not build_id:
                        raise Refused(f"{package} records no GNU build-id for {entry} (DN-142.D15)")
                    carried_by_image.append({"package": package, "entry": entry, "digest": held[entry],
                                             "build_id": build_id})
                archive = store / "objects" / output["archive"]
                if not archive.is_file() or sha256_file(archive) != output["archive"] \
                        or not IMAGE_MANIFEST.match(output.get("manifest", "")):
                    raise Refused(f"the store's archive {output['archive']} is absent, does not hash to its "
                                  f"name, or names no image manifest")
                rows.append({"name": image, "kind": IMAGE_KIND, "destination": repo,
                             "record": steps[f"{image}/{IMAGE_STEP}"]["key"],
                             "object": output["archive"], "manifest": output["manifest"],
                             "executables": carried_by_image, "verdicts": sorted(verdicts)})
            except Refused as refused:
                findings.append(f"{repo}/{image}: {refused}")
        whole[repo] = {"visibility": visibility[repo], "assets": sorted(rows, key=lambda row: row["name"]),
                       "destinations": dict(sorted((body.get("destinations") or {}).items()))}
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
    record_digest = sha256_bytes(record_bytes)
    manifest = canonical({"format": MANIFEST_FORMAT, "version": record.get("version"),
                          "step0_record": record_digest, "repositories": whole})
    destination_slices: dict[tuple[str, str], tuple[bytes, bytes | None]] = {}
    slices = {}
    for repo, body in whole.items():
        bound = {}
        for destination, declared in body["destinations"].items():
            projected = canonical({"format": DESTINATION_FORMAT, "owner": repo, "destination": destination,
                                   "tag": declared["tag"], "version": record.get("version"),
                                   "step0_record": record_digest, "manifest": sha256_bytes(manifest),
                                   "assets": project(body["assets"], destination)})
            signature = sign_with(f"{repo}.{destination}", projected) if sign_with is not None else None
            destination_slices[(repo, destination)] = (projected, signature)
            bound[destination] = {"tag": declared["tag"], "visibility": visibility[destination],
                                  "slice": sha256_bytes(projected),
                                  "signature": sha256_bytes(signature) if signature is not None else None}
        slices[repo] = canonical({"format": SLICE_FORMAT, "repository": repo, "version": record.get("version"),
                                  "visibility": body["visibility"], "step0_record": record_digest,
                                  "manifest": sha256_bytes(manifest), "assets": body["assets"],
                                  "destinations": bound})
    return manifest, slices, destination_slices


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
    """Build the manifest, signing each destination slice before the owner slice that binds it,
    then sign the manifest and every slice, and keep the step-0 record, the manifest, each slice,
    each destination slice and each signature in the store under one `manifest` record keyed by
    the record's digest. `out` is created here and receives the signed files the replica pushes;
    the seal's summary."""
    if out.exists():
        raise Refused(f"{out} exists: a seal writes its signatures once, into a directory of its own")
    out.mkdir(parents=True)
    (out / "slices").mkdir()
    (out / "destinations").mkdir()

    def signed_destination(label: str, body: bytes) -> bytes:
        path = out / "destinations" / f"{label}.json"
        path.write_bytes(body)
        return sign(path, key).read_bytes()

    manifest, slices, destination_slices = build(store, record_bytes, plan, signed_destination)
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
    destinations = {}
    for (repo, destination), (body, signature) in sorted(destination_slices.items()):
        hold(store, sha256_bytes(body), body)
        hold(store, sha256_bytes(signature), signature)
        destinations[f"{repo}.{destination}"] = {"object": sha256_bytes(body), "signature": sha256_bytes(signature)}
    summary = {"record": record_digest, "manifest": held.pop("manifest"), "slices": held,
               "destinations": destinations}
    document = {"step": {"kind": "manifest", "subject": "step0"}, "step0_record": record_digest}
    if artefact_store.LocalStore(store).commit(document, {"manifest": dict(summary)}, {}) != 0:
        raise Refused(f"the store already holds another seal of step-0 record {record_digest}")
    return summary


def _signed(slice_bytes: bytes, signature: Path, allowed: Path, what: str) -> dict:
    """The body of `slice_bytes`, once the writer's signature over it verifies."""
    done = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", WRITER_IDENTITY,
                           "-n", NAMESPACE, "-s", str(signature)],
                          input=slice_bytes, capture_output=True, check=False)
    if done.returncode != 0:
        raise Refused(f"the {what}'s signature does not verify for {WRITER_IDENTITY}: "
                      f"{done.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return json.loads(slice_bytes)


def verify(slice_bytes: bytes, signature: Path, allowed: Path, repository: str) -> list[dict]:
    """The OWNER slice's assets, only once its signature, its format, its repository, its rows and
    the destinations it binds hold."""
    body = _signed(slice_bytes, signature, allowed, "slice")
    if body.get("format") != SLICE_FORMAT:
        raise Refused(f"the slice's format is {body.get('format')!r}, not {SLICE_FORMAT!r}: a destination "
                      f"slice never verifies as an owner slice (DN-142.D25 (2))")
    if body.get("repository") != repository:
        raise Refused(f"the slice is {body.get('repository')!r}'s, not {repository!r}'s")
    bound = body.get("destinations") or {}
    for destination, binding in sorted(bound.items()):
        if destination == repository or not isinstance(binding, dict) or set(binding) != BINDING \
                or binding["visibility"] not in VISIBILITIES or not TAG_NAME.match(str(binding["tag"])) \
                or not all(DIGEST_HEX.match(str(binding[field])) for field in ("slice", "signature")):
            raise Refused(f"destination {destination}: the slice binds {binding!r}, not a foreign "
                          f"{{tag, visibility, slice, signature}} with both digests")
    for asset in body.get("assets", []):
        if asset.get("kind") not in (*KINDS, IMAGE_KIND):
            raise Refused(f"{asset.get('name')}: kind {asset.get('kind')!r} is not one of "
                          f"{', '.join((*KINDS, IMAGE_KIND))}")
        if not DIGEST_HEX.match(asset.get("object", "")) or not DIGEST_HEX.match(str(asset.get("record", ""))):
            raise Refused(f"{asset.get('name')}: {asset.get('object')!r} or its record {asset.get('record')!r} is "
                          f"not a digest")
        if not isinstance(asset.get("verdicts"), list) or not all(DIGEST_HEX.match(str(key)) for key in asset["verdicts"]):
            raise Refused(f"{asset.get('name')}: its verdicts {asset.get('verdicts')!r} are not record keys")
        if asset.get("kind") == "entry" and asset.get("entry_digest") != asset["object"]:
            raise Refused(f"{asset.get('name')}: an entry asset's object is the entry's own bytes, and "
                          f"{asset['object']} is not its entry digest {asset.get('entry_digest')!r}")
        if asset.get("kind") == IMAGE_KIND and not IMAGE_MANIFEST.match(asset.get("manifest", "")):
            raise Refused(f"{asset.get('name')}: an image asset names no image manifest digest")
        destination = asset.get("destination")
        if destination != repository and destination not in bound:
            raise Refused(f"{asset.get('name')}: its destination {destination!r} is neither {repository} nor a "
                          f"destination its slice declares")
        if asset.get("kind") == "conan-transport" and destination != repository:
            raise Refused(f"{asset.get('name')}: a transport of {repository} aimed at {destination}'s Release "
                          f"(N360 plan F9, DN-142.D25 (2))")
    return body.get("assets", [])


def verify_destination(destination_bytes: bytes, signature: Path, allowed: Path, owner: dict,
                       destination: str) -> list[dict]:
    """The destination slice's rows, only once the writer signed it, it is the very slice the
    verified owner slice `owner` binds for `destination`, it names the owner slice's record,
    manifest, version and tag, and its rows are the PROJECTION of the owner's rows for that
    destination (DN-142.D25 (2), the publish job's step 1)."""
    binding = (owner.get("destinations") or {}).get(destination)
    if binding is None:
        raise Refused(f"the owner slice of {owner.get('repository')} binds no destination {destination}")
    if sha256_bytes(destination_bytes) != binding["slice"]:
        raise Refused(f"the destination slice hashes to {sha256_bytes(destination_bytes)}, and the owner slice "
                      f"binds {binding['slice']}")
    body = _signed(destination_bytes, signature, allowed, "destination slice")
    if body.get("format") != DESTINATION_FORMAT:
        raise Refused(f"the destination slice's format is {body.get('format')!r}, not {DESTINATION_FORMAT!r}: "
                      f"an owner slice never verifies as a destination slice (DN-142.D25 (2))")
    want = {"owner": owner.get("repository"), "destination": destination, "tag": binding["tag"],
            "version": owner.get("version"), "step0_record": owner.get("step0_record"),
            "manifest": owner.get("manifest")}
    differs = sorted(field for field, value in want.items() if body.get(field) != value)
    if differs:
        raise Refused(f"the destination slice differs from its owner slice on {', '.join(differs)}")
    projected = project(owner.get("assets", []), destination)
    if body.get("assets") != projected:
        raise Refused(f"the destination slice's rows {[row.get('name') for row in body.get('assets', [])]} are "
                      f"not the projection of the owner slice's rows for {destination} "
                      f"{[row['name'] for row in projected]}, field for field")
    return body["assets"]


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
        build_ids = {path: sha256_bytes(data)[:40] for path, (mode, data) in files.items() if mode == "executable"}
        key = put({"step": {"kind": "conan-create", "package": name, "executables": executables}},
                  {"package": {"content": content, "entries": entries, "transport": transport,
                               "build_ids": build_ids}})
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

    def held(data: bytes) -> str:
        (store / "objects" / sha256_bytes(data)).write_bytes(data)
        return sha256_bytes(data)

    tracked = put({"step": {"kind": "tracked", "repository": "priv-repo", "package": "priv_tools"},
                   "files": [["sbom.json", "e" * 40]]},
                  {"tracked": {"objects": {"sbom.json": held(b'{"bom": 1}\n')}}})
    steps.append({"id": "priv_tools/tracked", "key": tracked, "verdict": None})
    bench = put({"step": {"kind": "verdict", "id": "bench", "subject": "priv_tools"}, "judged": {"bin/tool": tool_entry}},
                {"bench": {"verdict": "pass", "objects": {"bench.json": held(b'{"ns": 7}\n')}}})
    steps.append({"id": "priv_tools/bench", "key": bench, "verdict": "pass"})
    windows = held(b"MZ windows bytes")
    ingested = put({"step": {"kind": "ingest", "repository": "priv-repo", "artefact": "win-x64",
                             "workflow": ".github/workflows/win.yml", "job": "build"},
                    "run": {"id": 31, "attempt": 1}, "judged": {"priv-repo": "f" * 40}},
                   {"ingest": {"objects": {"win.exe": windows},
                               "producers": {"printed": windows, "github_artifact_digest": "sha256:" + "a" * 64,
                                             "writer_recomputed": windows},
                               "seat": {"runner": "GitHub Actions 3", "labels": ["windows-2025"], "run": 31,
                                        "job": "build"},
                               "title": "Win — step 0 run 777 — priv-repo=" + "f" * 40,
                               "exception": {"since": "2026-10-13", "discharge": "a Windows seat"},
                               "head_sha": "f" * 40, "inputs_key": "b" * 64, "class": "executable"}})
    steps.append({"id": "priv-repo/ingest.win", "key": ingested, "verdict": None})
    golden = held(b"agreed golden digest\n")
    agreed = put({"step": {"kind": "ingest", "repository": "priv-repo", "artefact": "golden",
                           "workflow": ".github/workflows/golden.yml", "job": "compare"},
                  "run": {"id": 32, "attempt": 1}, "judged": {"priv-repo": "f" * 40}},
                 {"ingest": {"objects": {"golden.txt": golden},
                             "producers": {"printed": golden, "github_artifact_digest": "sha256:" + "c" * 64,
                                           "writer_recomputed": golden},
                             "seat": {"runner": "GitHub Actions 4", "labels": ["ubuntu-24.04"], "run": 32,
                                      "job": "compare"},
                             "title": "Golden — step 0 run 777 — priv-repo=" + "f" * 40,
                             "exception": {"since": "2026-10-14", "discharge": "a step-0 producer"},
                             "head_sha": "f" * 40, "inputs_key": "d" * 64, "class": "object"}})
    steps.append({"id": "priv-repo/ingest.golden", "key": agreed, "verdict": None})
    record = canonical({"version": "9.9.9", "built": True, "cuttable": True, "step0_run": "777", "steps": steps})
    plan = {"format": PLAN_FORMAT, "visibility": {"pub-repo": "public", "priv-repo": "private"}, "repositories": {
        "pub-repo": {"assets": [
            {"name": "pub_lib-9.9.9.tgz", "kind": "conan-transport", "package": "pub_lib",
             "destination": "pub-repo"}]},
        "priv-repo": {"assets": [
            {"name": "priv_tools-9.9.9.tgz", "kind": "conan-transport", "package": "priv_tools",
             "destination": "priv-repo"},
            {"name": "tool-linux-x64", "kind": "entry", "package": "priv_tools", "entry": "bin/tool",
             "destination": "pub-repo", "verdicts": ["consumer"]},
            {"name": "sbom.json", "kind": "object", "step": "priv_tools/tracked", "member": "sbom.json",
             "destination": "priv-repo", "verdicts": []},
            {"name": "bench.json", "kind": "object", "step": "priv_tools/bench", "member": "bench.json",
             "destination": "priv-repo", "verdicts": []},
            {"name": "win.exe", "kind": "entry", "step": "priv-repo/ingest.win", "member": "win.exe",
             "destination": "pub-repo", "verdicts": [], "artifact": "win-x64"},
            {"name": "golden.txt", "kind": "object", "step": "priv-repo/ingest.golden", "member": "golden.txt",
             "destination": "priv-repo", "verdicts": [], "artifact": "golden"}],
            "destinations": {"pub-repo": {"tag": "engine-v9.9.9"}},
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
            manifest, slices, destination_slices = built
            pub = json.loads(slices["pub-repo"])
            if sorted(slices) != ["priv-repo", "pub-repo"] or pub["manifest"] != sha256_bytes(manifest):
                failures.append("the slices do not name the whole manifest's digest, or a repository is missing")
            priv = json.loads(slices["priv-repo"])
            if [asset["name"] for asset in pub["assets"]] != ["pub_lib-9.9.9.tgz"] or pub["destinations"]:
                failures.append(f"the public slice carries a row another repository declares: {pub['assets']}")
            if b"priv_cache" in slices["pub-repo"] or b"priv_tools" in slices["pub-repo"]:
                failures.append("the public slice names a package it does not publish")
            tool_row = next((asset for asset in priv["assets"] if asset["name"] == "tool-linux-x64"), {})
            tool_digest = sha256_bytes(b"\x7fELF tool bytes")
            projected = destination_slices.get(("priv-repo", "pub-repo"), (b"{}", None))[0]
            if tool_row.get("object") != tool_digest or tool_row.get("destination") != "pub-repo" or \
                    priv["destinations"] != {"pub-repo": {"tag": "engine-v9.9.9", "visibility": "public",
                                                          "slice": sha256_bytes(projected), "signature": None}} or \
                    (store / "objects" / tool_digest).read_bytes() != b"\x7fELF tool bytes":
                failures.append(f"the entry row is not its OWNER's, destined to pub-repo by the slice's "
                                f"destinations, naming the entry's own bytes (DN-142.D25 (2)): {tool_row}, "
                                f"{priv['destinations']}")
            image_row = next((asset for asset in json.loads(slices["priv-repo"])["assets"]
                              if asset["name"] == "srv-image"), {})
            if image_row.get("kind") != IMAGE_KIND or image_row.get("manifest") != "sha256:" + "d" * 64 or \
                    image_row.get("executables") != [{"package": "priv_server", "entry": "bin/server",
                                                      "digest": sha256_bytes(b"\x7fELF server bytes"),
                                                      "build_id": sha256_bytes(b"\x7fELF server bytes")[:40]}]:
                failures.append(f"the image is not an asset of the repository that declares it, naming each "
                                f"executable's digest and build-id: {image_row}")
            keys = {step["id"]: step["key"] for step in json.loads(record)["steps"]}
            rows = {asset["name"]: asset for asset in priv["assets"]}
            want_verdicts = {"priv_tools-9.9.9.tgz": [keys["priv_tools/verdict"]],
                             "tool-linux-x64": [keys["priv_tools/verdict"], keys["priv_tools/consumer"]],
                             "srv-image": [keys["priv_server/verdict"]], "sbom.json": [],
                             "bench.json": [keys["priv_tools/bench"]], "win.exe": [keys["priv-repo/ingest.win"]],
                             "golden.txt": []}
            if {name: row.get("verdicts") for name, row in rows.items()} != want_verdicts:
                failures.append(f"every row lists the verdict records the builder checked for it: "
                                f"{ {name: row.get('verdicts') for name, row in rows.items()} }")
            objects = {name: (row["step"], row["record"], row["object"]) for name, row in rows.items()
                       if row["kind"] == "object"}
            if objects != {"sbom.json": ("priv_tools/tracked", keys["priv_tools/tracked"], sha256_bytes(b'{"bom": 1}\n')),
                           "bench.json": ("priv_tools/bench", keys["priv_tools/bench"], sha256_bytes(b'{"ns": 7}\n')),
                           "golden.txt": ("priv-repo/ingest.golden", keys["priv-repo/ingest.golden"],
                                          sha256_bytes(b"agreed golden digest\n"))}:
                failures.append(f"each object row names its producing step, record and object: {objects}")
            destination = json.loads(projected)
            if sorted(destination_slices) != [("priv-repo", "pub-repo")] or \
                    destination.get("format") != DESTINATION_FORMAT or \
                    [row["name"] for row in destination.get("assets", [])] != ["tool-linux-x64", "win.exe"] or \
                    any(set(row) != set(PROJECTED) for row in destination.get("assets", [])) or \
                    b"priv_tools" in projected or destination.get("manifest") != sha256_bytes(manifest):
                failures.append(f"the destination slice is not the owner's rows for pub-repo projected to "
                                f"{PROJECTED}, naming the manifest: {destination}")

        arm("a step-0 record that is not cuttable writes no manifest",
            lambda: build(store, canonical({**json.loads(record), "cuttable": False}), plan), "cuttable=False")
        arm("a transport aimed at a foreign destination is refused (N360 plan F9, DN-142.D25 (2))",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "x.tgz", "kind": "conan-transport", "package": "priv_tools",
                 "destination": "pub-repo"}))), "a transport carries priv-repo's sources")
        arm("a row aimed at a foreign destination its owner does not declare is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"].pop("destinations"))),
            "its destination 'pub-repo' is neither priv-repo nor a destination priv-repo declares")
        arm("a row still carrying a `source` is refused: the owner is the source",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["pub-repo"]["assets"][0]
                                                .__setitem__("source", "pub-repo"))), "never a field of it")
        arm("one (destination, name) pair with two rows is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "pub_lib-9.9.9.tgz", "kind": "entry", "package": "priv_tools", "entry": "bin/tool",
                 "destination": "pub-repo"}))), "has 2 rows (priv-repo, pub-repo)")
        arm("two owners on one destination Release are refused: a foreign tag that is the destination's own",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["destinations"]
                                                ["pub-repo"].__setitem__("tag", "v9.9.9"))),
            "the pub-repo Release v9.9.9 has 2 owners, priv-repo, pub-repo")
        arm("a plan with no top-level visibility is refused",
            lambda: build(store, record, edited(plan, lambda p: p.pop("visibility"))), "no top-level visibility")
        arm("an asset whose package step 0 never ran is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "ghost.tgz", "kind": "conan-transport", "package": "ghost", "destination": "priv-repo"}))),
            "step 0 ran no step ghost/package")
        arm("an entry classified run-from-cache cannot be published (DN-142.D22 (3), direction 1)",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"].append(
                {"name": "det", "kind": "entry", "package": "priv_cache", "destination": "priv-repo",
                 "entry": "bin/det"}))), "not 'published'")
        arm("a published executable no asset carries is refused (DN-142.D22 (3), direction 2)",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"].__setitem__(
                "assets", []))), "no asset or image of this manifest carries it")
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
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][1]
                                                .__setitem__("verdicts", ["consumer", "container"]))),
            "no container verdict")
        arm("RED: an object row whose producing step this run never ran is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][2]
                                                .__setitem__("step", "priv_tools/absent"))),
            "step 0 ran no step priv_tools/absent")
        arm("RED: an object row naming a verdict is refused: the producing record is its judgment",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][3]
                                                .__setitem__("verdicts", ["consumer"]))), "names no verdict")
        arm("RED: an ingested row of another repository's ingestion step is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][4]
                                                .__setitem__("step", "pub-repo/ingest.win"))),
            "is another repository's")
        arm("RED: an ingested row whose record read another artifact is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][4]
                                                .__setitem__("artifact", "linux-x64"))),
            "not priv-repo's priv-repo/ingest.win artifact linux-x64")
        arm("RED: a step-0 record naming no run admits no ingested row",
            lambda: build(store, canonical({**json.loads(record), "step0_run": None}), plan),
            "never enter run Y")
        arm("RED: a repository that owns no row is a destination only, and gets no store package",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"].__setitem__("pub-repo", {"assets": []}))),
            "pub-repo: it owns no row and no image, and it is a destination only (of priv-repo)")

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
        store_f, record_f, plan_f, _ = _fixture(root / "f")
        rewrite(store_f, "priv_tools/bench", record_f,
                lambda body: body["outputs"]["bench"].__setitem__("verdict", "fail"))
        arm("RED: an object whose producing verdict failed is refused",
            lambda: build(store_f, record_f, plan_f), "whose verdict is 'fail', not pass")
        store_g, record_g, plan_g, _ = _fixture(root / "g")
        rewrite(store_g, "priv-repo/ingest.win", record_g,
                lambda body: body["outputs"]["ingest"].__setitem__("title", "Win — step 0 run 776 — x"))
        arm("RED: an ingested row whose title names another step-0 run is refused",
            lambda: build(store_g, record_g, plan_g), "names step 0 run(s) ['776'], and this record is run 777")
        store_h, record_h, plan_h, _ = _fixture(root / "h")
        (store_h / "records" / f"{dict((s['id'], s['key']) for s in json.loads(record_h)['steps'])['priv_tools/tracked']}.json").unlink()
        arm("RED: an object row whose producing record the store does not hold is refused",
            lambda: build(store_h, record_h, plan_h), "the store holds no record")
        store_i, record_i, plan_i, _ = _fixture(root / "i")
        rewrite(store_i, "priv-repo/ingest.win", record_i,
                lambda body: body["outputs"]["ingest"]["producers"].__setitem__("printed", "0" * 64))
        arm("RED: an ingestion whose record names another printed digest than its object is refused",
            lambda: build(store_i, record_i, plan_i), "do not all name the object")
        win = rows["win.exe"]
        if (win["kind"], win["entry"], win["object"], win["entry_digest"], win["record"]) != \
                ("entry", "win.exe", sha256_bytes(b"MZ windows bytes"), sha256_bytes(b"MZ windows bytes"),
                 keys["priv-repo/ingest.win"]):
            failures.append(f"an ingested executable is an entry row, its object its own bytes: {win}")
        store_j, record_j, plan_j, _ = _fixture(root / "j")
        rewrite(store_j, "priv-repo/ingest.win", record_j,
                lambda body: body["outputs"]["ingest"].__setitem__("class", "object"))
        arm("RED: an ingested entry row whose ingestion recorded the class object is refused",
            lambda: build(store_j, record_j, plan_j), "an ingested entry row publishes only a 'executable'")
        store_k, record_k, plan_k, _ = _fixture(root / "k")
        rewrite(store_k, "priv-repo/ingest.golden", record_k,
                lambda body: body["outputs"]["ingest"].__setitem__("class", "executable"))
        arm("RED: an ingested object row whose ingestion recorded the class executable is refused",
            lambda: build(store_k, record_k, plan_k), "an ingested object row publishes only a 'object'")
        arm("RED: an ingested entry row naming a package is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][4]
                                                .__setitem__("package", "priv_tools"))),
            "never a package or an entry")
        arm("RED: an entry row naming a step that is no ingestion is refused",
            lambda: build(store, record, edited(plan, lambda p: p["repositories"]["priv-repo"]["assets"][4]
                                                .__setitem__("step", "priv_tools/tracked"))),
            "names an ingestion step")
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
        def writer_signed(label: str, body: bytes) -> bytes:
            path = root / f"destination-{label}.json"
            path.write_bytes(body)
            return sign(path, keys / "writer").read_bytes()

        built = arm("a build handed the writer's signer signs each destination slice and binds both digests",
                    lambda: build(store, record, plan, writer_signed), None)
        if built:
            _manifest, slices, destination_slices = built
            slice_path = root / "pub-repo.json"
            slice_path.write_bytes(slices["pub-repo"])
            signature = sign(slice_path, keys / "writer")
            assets = arm("the writer's signature verifies and the slice's assets come back",
                         lambda: verify(slices["pub-repo"], signature, allowed, "pub-repo"), None)
            if assets is not None and [asset["name"] for asset in assets] != ["pub_lib-9.9.9.tgz"]:
                failures.append(f"the verified slice lists {[asset['name'] for asset in assets]}")
            priv_path = root / "priv-repo.json"
            priv_path.write_bytes(slices["priv-repo"])
            priv_signature = sign(priv_path, keys / "writer")
            assets = arm("the owner's slice verifies with its foreign row and the destination it declares",
                         lambda: verify(slices["priv-repo"], priv_signature, allowed, "priv-repo"), None)
            if assets is not None and [(asset["name"], asset["destination"]) for asset in assets] != [
                    ("bench.json", "priv-repo"), ("golden.txt", "priv-repo"), ("priv_tools-9.9.9.tgz", "priv-repo"),
                    ("sbom.json", "priv-repo"),
                    ("srv-image", "priv-repo"), ("tool-linux-x64", "pub-repo"), ("win.exe", "pub-repo")]:
                failures.append(f"the verified owner slice lists {assets}")
            owner = json.loads(slices["priv-repo"])
            projected, projected_signature = destination_slices[("priv-repo", "pub-repo")]
            projected_path = root / "projected.json"
            projected_path.write_bytes(projected)
            (root / "projected.json.sig").write_bytes(projected_signature)
            rows = arm("the destination slice the owner slice binds verifies: its rows are the projection",
                       lambda: verify_destination(projected, root / "projected.json.sig", allowed, owner, "pub-repo"),
                       None)
            if rows is not None and [row["name"] for row in rows] != ["tool-linux-x64", "win.exe"]:
                failures.append(f"the verified destination slice lists {rows}")
            arm("RED: a destination slice offered as an owner slice is refused",
                lambda: verify(projected, root / "projected.json.sig", allowed, "priv-repo"),
                "a destination slice never verifies as an owner slice")
            arm("RED: an owner slice offered as a destination slice is refused",
                lambda: verify_destination(slices["priv-repo"], priv_signature, allowed,
                                           {**owner, "destinations": {"pub-repo": {
                                               **owner["destinations"]["pub-repo"],
                                               "slice": sha256_bytes(slices["priv-repo"])}}}, "pub-repo"),
                "an owner slice never verifies as a destination slice")
            narrowed = json.loads(projected)
            narrowed["assets"] = narrowed["assets"][:1]
            narrowed_path = root / "narrowed.json"
            narrowed_path.write_bytes(canonical(narrowed))
            narrowed_signature = sign(narrowed_path, keys / "writer")
            arm("RED: a writer-signed destination slice whose rows are not the owner's projection is refused",
                lambda: verify_destination(canonical(narrowed), narrowed_signature, allowed,
                                           {**owner, "destinations": {"pub-repo": {
                                               **owner["destinations"]["pub-repo"],
                                               "slice": sha256_bytes(canonical(narrowed))}}}, "pub-repo"),
                "not the projection of the owner slice's rows for pub-repo")
            arm("RED: a destination slice the owner slice does not bind by digest is refused",
                lambda: verify_destination(canonical(narrowed), narrowed_signature, allowed, owner, "pub-repo"),
                "and the owner slice binds")
            unbound = json.loads(slices["priv-repo"])
            unbound["destinations"]["pub-repo"]["signature"] = None
            unbound_path = root / "unbound.json"
            unbound_path.write_bytes(canonical(unbound))
            unbound_signature = sign(unbound_path, keys / "writer")
            arm("RED: an owner slice binding an unsigned destination slice is refused",
                lambda: verify(canonical(unbound), unbound_signature, allowed, "priv-repo"), "with both digests")
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
            smuggled = json.loads(slices["priv-repo"])
            smuggled["assets"].append({"name": "x.tgz", "kind": "conan-transport", "destination": "pub-repo",
                                       "object": "a" * 64, "record": "b" * 64, "verdicts": []})
            smuggled_path = root / "smuggled.json"
            smuggled_path.write_bytes(canonical(smuggled))
            smuggled_signature = sign(smuggled_path, keys / "writer")
            arm("even a writer-signed slice may not aim its transport at a foreign Release",
                lambda: verify(canonical(smuggled), smuggled_signature, allowed, "priv-repo"),
                "a transport of priv-repo aimed at pub-repo's Release")
            stray = json.loads(slices["pub-repo"])
            stray["assets"].append({"name": "y", "kind": "entry", "destination": "priv-repo", "object": "a" * 64,
                                    "entry_digest": "a" * 64, "record": "b" * 64, "verdicts": []})
            stray_path = root / "stray.json"
            stray_path.write_bytes(canonical(stray))
            stray_signature = sign(stray_path, keys / "writer")
            arm("even a writer-signed slice may not name a destination it does not declare",
                lambda: verify(canonical(stray), stray_signature, allowed, "pub-repo"),
                "is neither pub-repo nor a destination its slice declares")
            wrapped = json.loads(slices["priv-repo"])
            next(asset for asset in wrapped["assets"] if asset["kind"] == "entry")["object"] = \
                transports["priv_tools"].name
            wrapped_path = root / "wrapped.json"
            wrapped_path.write_bytes(canonical(wrapped))
            wrapped_signature = sign(wrapped_path, keys / "writer")
            arm("even a writer-signed slice may not ship an entry inside its package's transport",
                lambda: verify(canonical(wrapped), wrapped_signature, allowed, "priv-repo"),
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
                    (store_s / "objects" / sealed["record"]).read_bytes() != record_s or \
                    sorted(sealed["destinations"]) != ["priv-repo.pub-repo"] or not all(
                        (store_s / "objects" / held).is_file() for held in sealed["destinations"]["priv-repo.pub-repo"].values()):
                failures.append(f"the seal's record does not keep the record, manifest, slices and destination "
                                f"slices: {kept}")
            sealed_owner = json.loads((root / "seal-1" / "slices" / "priv-repo.json").read_bytes())
            if sealed_owner["destinations"]["pub-repo"]["slice"] != sealed["destinations"]["priv-repo.pub-repo"]["object"] \
                    or sealed_owner["destinations"]["pub-repo"]["signature"] != \
                    sealed["destinations"]["priv-repo.pub-repo"]["signature"]:
                failures.append("the sealed owner slice does not bind the destination slice the seal signed and kept")
            arm("the sealed destination slice verifies against the sealed owner slice",
                lambda: verify_destination((root / "seal-1" / "destinations" / "priv-repo.pub-repo.json").read_bytes(),
                                           root / "seal-1" / "destinations" / "priv-repo.pub-repo.json.sig",
                                           allowed, sealed_owner, "pub-repo"), None)
            for repo in ("priv-repo", "pub-repo"):
                arm(f"the sealed {repo} slice verifies under the writer key",
                    lambda repo=repo: verify((root / "seal-1" / "slices" / f"{repo}.json").read_bytes(),
                                             root / "seal-1" / "slices" / f"{repo}.json.sig", allowed, repo), None)
        arm("sealing the same record again keeps the same seal, never a second one",
            lambda: seal(store_s, record_s, plan_s, keys / "writer", root / "seal-2"), None)
        store_t, record_t, plan_t, _ = _fixture(root / "t")
        arm("RED: a seal that would push to a destination-only package is refused, nothing written",
            lambda: seal(store_t, record_t, edited(plan_t, lambda p: p["repositories"].__setitem__(
                "pub-repo", {"assets": []})), keys / "writer", root / "seal-3"), "is a destination only")
        if (root / "seal-3" / "slices" / "pub-repo.json").exists():
            failures.append("a refused seal wrote a slice for a destination-only repository")
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
            manifest, slices, destination_slices = build(Path(argv[1]), Path(argv[2]).read_bytes(),
                                                         json.loads(Path(argv[3]).read_text()))
            out = Path(argv[4])
            (out / "slices").mkdir(parents=True, exist_ok=True)
            (out / "destinations").mkdir(exist_ok=True)
            (out / "manifest.json").write_bytes(manifest)
            for repo, body in slices.items():
                (out / "slices" / f"{repo}.json").write_bytes(body)
            for (repo, destination), (body, _unsigned) in destination_slices.items():
                (out / "destinations" / f"{repo}.{destination}.json").write_bytes(body)
            print(f"store_manifest: manifest {sha256_bytes(manifest)}, {len(slices)} slice(s) and "
                  f"{len(destination_slices)} destination slice(s) in {out}, unsigned")
            return 0
        if len(argv) == 6 and argv[0] == "seal":
            summary = seal(Path(argv[1]), Path(argv[2]).read_bytes(), json.loads(Path(argv[3]).read_text()),
                           Path(argv[4]), Path(argv[5]))
            print(f"store_manifest: SEALED record {summary['record']} manifest {summary['manifest']['object']} "
                  + " ".join(f"{repo}={held['object']}" for repo, held in sorted(summary["slices"].items()))
                  + "".join(f" {pair}={held['object']}" for pair, held in sorted(summary["destinations"].items())))
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
