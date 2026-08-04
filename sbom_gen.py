#!/usr/bin/env python3
"""Generate a CycloneDX SBOM from the RESOLVED conan graph.

Why this exists
---------------
The Sift SBOM used to be hand-maintained, and on 2026-07-21 it was measured three minors
stale: it still named four long-removed packages (``insight_detection`` /
``insight_explain`` / ``insight_engine`` / ``insight_diff``) which no longer exist, pinned
first-party at ``1.5.4`` against an actual ``1.8.3``, and omitted ``insight_llm`` plus the
three ``insight_semantic_*`` dialect packages entirely. Nobody noticed because the file
*asserts* the dependency set instead of *deriving* it.

That matters more than tidiness: ``.github/workflows/sbom-cve.yml`` scans this file, so its
accuracy is the ceiling on our CVE detection. A component the SBOM omits is a component no
scanner ever looks at.

So the SBOM stops being a claim maintained beside the graph and becomes a projection of it.
Pair it with ``malf lock``: lockfile pins the graph -> this derives the SBOM -> the weekly
trivy gate scans it -> a CVE forces a deliberate ``malf lock --update``. Closed loop, no
hand-maintenance anywhere in it.

Scope
-----
Components are the SHIPPED link closure: host context only, test-requires excluded, build
context (cmake / ninja / b2 / autotools) excluded — none of it is in the binary. The root
package is the ``metadata.component``, not a component of itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# ── CPE vendor table ────────────────────────────────────────────────────────────────────
# trivy matches CVEs by PURL *and* CPE; for the C/C++ ecosystem CPE is what actually hits
# NVD, so a wrong vendor string silently scans nothing. Curated, deliberately explicit, and
# small enough to review. A package ABSENT here gets no CPE and is unconditionally REPORTED
# on stderr (never silently dropped). Absence is a real state: several of these genuinely
# have no NVD vendor entry, and inventing one would be worse than omitting it.
# Curation pass 2026-07-29 against the live NVD CPE dictionary
# (services.nvd.nist.gov/rest/json/cpes/2.0): every entry below was confirmed present and
# non-deprecated; where NVD carries a legacy twin the CURRENT one was taken (c-ares over
# c-ares_project, redis over redislabs for hiredis). Verified as having NO NVD entry and
# therefore deliberately unmapped: asio, clickhouse-cpp, fmt, glaze (NVD's only "glaze" hit
# is glazedlists:glazed_lists, an unrelated Java library), libnuma, libpqxx, picosha2,
# redis-plus-plus, trantor.
CPE_VENDOR: dict[str, str] = {
    "openssl": "openssl",
    "libcurl": "haxx",
    "zlib": "zlib",
    "spdlog": "gabime",
    "boost": "boost",
    "sqlite3": "sqlite",
    "libpq": "postgresql",
    "lz4": "lz4",
    "zstd": "facebook",
    "yaml-cpp": "yaml-cpp_project",
    # NVD's product for abseil-cpp; its dictionary carries abseil's YYYYMMDD.patch
    # versioning (incl. 20260107.1), so version-range matching lines up with the conan pin.
    "abseil": "abseil",
    "c-ares": "c-ares",
    "cityhash": "google",
    "drogon": "drogon",
    "hiredis": "redis",
    "jsoncpp": "jsoncpp_project",
    "simdjson": "simdjson_project",
    # The conan package is the libuuid subset, but it is built from util-linux source at
    # util-linux's version, so NVD's kernel:util-linux ranges match it — over-reporting
    # (mount/login CVEs land on a uuid-only consumer) is the conservative direction.
    "util-linux-libuuid": "kernel",
}

# ── First-party predicate ───────────────────────────────────────────────────────────────
# THE single definition of "first-party" for every consumer in this file: the unmapped-CPE
# report AND the coderoast:cpe-coverage ratio. A second, independently-written
# definition of "third-party" is how a numerator and a denominator drift apart — so both
# derive from this one predicate, on the component's rendered (display) name.
FIRST_PARTY_PREFIXES: tuple[str, ...] = ("insight_", "coderoast_", "logcraft_")


def is_first_party(component_name: str) -> bool:
    return component_name.startswith(FIRST_PARTY_PREFIXES)


def cpe_coverage(components: list[dict]) -> str:
    """The coderoast:cpe-coverage ratio, `<with-CPE>/<third-party-total>`.

    trivy matches C/C++ components to NVD by CPE; a component without one is matched by PURL
    alone and in practice scanned weakly or not at all. Without this declared ratio an SBOM
    whose components are mostly CPE-less reads as green while being nearly blind — the
    workspace's signature defect shape. First-party components carry no CVE surface, so they
    are excluded from BOTH sides via `is_first_party`. The `malf sbom --check` byte-compare
    freshness gate is what makes this number a control: a component entering without a CPE
    mapping changes the ratio, stales the committed file, and reds the gate.
    """
    third_party = [comp for comp in components if not is_first_party(comp["name"])]
    with_cpe = sum(1 for comp in third_party if "cpe" in comp)
    return f"{with_cpe}/{len(third_party)}"


# conan package name -> CycloneDX component name, where the ecosystem spells it differently.
# The PURL always keeps the CONAN name (it is a pkg:conan purl); only the display name moves.
DISPLAY_NAME: dict[str, str] = {
    "libcurl": "curl",
}

# CPE product name, when it differs from the display name.
CPE_PRODUCT: dict[str, str] = {
    "libcurl": "curl",
    "libpq": "postgresql",
    "abseil": "common_libraries",
    "util-linux-libuuid": "util-linux",
}


def resolve_graph(root: Path, profile: Path, lockfile: Path | None) -> dict:
    """Ask conan for the resolved graph. No parsing of recipes, no guessing."""
    cmd = [
        "conan", "graph", "info", str(root),
        f"--profile:all={profile}",
        "--format=json",
    ]
    if lockfile and lockfile.is_file():
        cmd += [f"--lockfile={lockfile}", "--lockfile-partial"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"sbom_gen: conan graph info failed for {root}")
    return json.loads(proc.stdout)


def shipped_components(graph: dict) -> tuple[dict, list[dict]]:
    """Split the graph into (root node, shipped dependency nodes).

    Shipped means: host context, not a test requirement, not the root. Build-context nodes
    are the toolchain (cmake/ninja/b2/m4/…) and are not in the delivered binary.
    """
    nodes = graph["graph"]["nodes"]
    root_node, comps = None, []
    # A package required by several consumers is several NODES in the graph (glaze is pulled
    # by eidos, llm and sift, each with visible=False). An SBOM is an inventory, so it
    # wants one row per distinct ref — dedupe on the full name/version, NOT on the name, so a
    # genuine two-version split (measured: lz4 1.9.4 vs 1.10.0 across roots) still shows up as
    # two rows instead of being collapsed into a false single answer.
    seen: set[str] = set()
    for node_id, node in nodes.items():
        ref = node.get("ref") or ""
        if node_id == "0" or not ref:
            root_node = node
            continue
        if node.get("context") != "host":
            continue          # build context: toolchain, never shipped
        if node.get("test"):
            continue          # test_requires: gtest/benchmark, never shipped
        key = ref.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        comps.append(node)
    return root_node, comps


def to_component(node: dict) -> tuple[dict, bool]:
    """Render one graph node as a CycloneDX component. Returns (component, has_cpe)."""
    ref = node["ref"].split("#", 1)[0]
    name, version = ref.split("/", 1)
    display = DISPLAY_NAME.get(name, name)
    comp = {
        "type": "library",
        "name": display,
        "version": version,
        "purl": f"pkg:conan/{name}@{version}",
    }
    vendor = CPE_VENDOR.get(name)
    if vendor:
        product = CPE_PRODUCT.get(name, display)
        comp["cpe"] = f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*"
    return comp, bool(vendor)


def build_sbom(root: Path, profile: Path, lockfile: Path | None,
               note: str | None) -> tuple[dict, list[str]]:
    graph = resolve_graph(root, profile, lockfile)
    root_node, nodes = shipped_components(graph)
    root_ref = (root_node.get("ref") or "").split("#", 1)[0]
    root_name, _, root_version = root_ref.partition("/")

    comps, missing_cpe = [], []
    for node in sorted(nodes, key=lambda n: n["ref"].lower()):
        comp, has_cpe = to_component(node)
        # A first-party package carries no CVE surface and needs no CPE; only flag THIRD-party
        # gaps, or the report cries wolf on every insight_* row and stops being read.
        if not has_cpe and not is_first_party(comp["name"]):
            missing_cpe.append(f'{comp["name"]}/{comp["version"]}')
        comps.append(comp)

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": root_name, "version": root_version},
            "properties": [
                {"name": "coderoast:note", "value": note_for(root_name, note)},
                {"name": "coderoast:cpe-coverage", "value": cpe_coverage(comps)},
            ],
        },
        "components": comps,
    }
    return sbom, missing_cpe


DEFAULT_NOTE = (
    "Shipped dependency set of the distributed binary, DERIVED from the resolved conan graph "
    "by malf/sbom_gen.py — do not hand-edit; regenerate with `malf sbom`. Human-readable "
    "provenance + license verdict: insight-eidos/SBOM.md. Scanned by "
    ".github/workflows/sbom-cve.yml."
)

# Per-artifact note, keyed on the ROOT package name. This artifact is "the
# linked-closure SBOM for coderoast_server", never "the server SBOM" — the deployed server is
# THREE inventories and this projection covers exactly one, so the boundary must travel WITH
# the artifact (a reader cannot recover it from the file otherwise). An artifact that
# over-reports its blast radius converts an absent check into a believed one.
ARTIFACT_NOTE: dict[str, str] = {
    "coderoast_server": (
        "The LINKED-CLOSURE SBOM for the coderoast_server binary — not 'the server SBOM'. "
        "The deployed server is three inventories: (1) the linked closure — "
        "third-party conan packages archived into the executable (readelf -d NEEDED lists "
        "only the C/C++ runtime) — covered by THIS artifact; (2) the Docker image's apt "
        "layer — scanned by coderoast-server/.github/workflows/ci-security-trivy.yml; "
        "(3) the sidecar images (redis, postgres, clickhouse-server, llama) — covered by "
        "NEITHER, declared deliberately rather than silently omitted. DERIVED from the "
        "resolved conan graph by malf/sbom_gen.py — do not hand-edit; regenerate with "
        "`malf sbom`."
    ),
}


def note_for(root_name: str, explicit: str | None) -> str:
    return explicit if explicit is not None else ARTIFACT_NOTE.get(root_name, DEFAULT_NOTE)


def note_fingerprint() -> str:
    """Digest of every note string that reaches a generated SBOM.

    THE NOTES ARE DOCUMENT CONTENT, not commentary about it — they are written into
    `metadata.properties`, so editing one changes the bytes of every committed SBOM and
    silently invalidates it. There is no other tripwire: the staleness check runs at the
    TAG, so an edit here surfaces days later as a red release, which is exactly how it
    went. Measured 2026-08-04: malf-toolchain `3e63f82` (a documentation sweep) removed an
    `adr/` citation from the server note on 2026-07-31; `coderoast-server`'s committed SBOM
    was stale from that moment and reded `v1.9.0` at Wave 5.

    Pinning a digest makes the edit fail HERE, at the moment it is made, with the repair in
    the message. It is deliberately a fingerprint and not a golden copy of the prose: the
    point is to force `malf sbom` to be re-run in the same pass, not to freeze the wording.
    """
    h = hashlib.sha256()
    h.update(DEFAULT_NOTE.encode())
    for key in sorted(ARTIFACT_NOTE):
        h.update(key.encode())
        h.update(ARTIFACT_NOTE[key].encode())
    return h.hexdigest()[:16]


# Bump this WITH the note edit, in the same commit, after running `malf sbom` so the
# committed artifacts move with it. A mismatch is not a lint nit — it means every
# committed SBOM in the workspace is now stale.
NOTE_FINGERPRINT = "d432ac021939c88b"


def selftest() -> int:
    """Offline self-test of the pure logic (no conan, no network).

    Deliberately exercises the parts that can silently produce a WRONG SBOM rather than a
    crash: the host/test/build filter, and the CPE vendor+product mapping.
    """
    graph = {"graph": {"nodes": {
        "0": {"ref": "insight_sift/1.8.3", "context": "host"},
        "1": {"ref": "openssl/3.6.3#abc", "context": "host"},
        "2": {"ref": "libcurl/8.20.0#def", "context": "host"},
        "3": {"ref": "gtest/1.17.0#ghi", "context": "host", "test": True},
        "4": {"ref": "cmake/4.4.0#jkl", "context": "build"},
        "5": {"ref": "insight_canon/1.8.3", "context": "host"},
        # Same ref reached via two consumers -> ONE row. Regression guard: the first cut of
        # this tool emitted glaze four times because glaze is pulled by eidos, llm and
        # sift independently.
        "6": {"ref": "openssl/3.6.3#abc", "context": "host"},
        # A genuine two-VERSION split must survive dedup as two rows (measured: lz4 1.9.4 vs
        # 1.10.0 across roots). Deduping on name would hide exactly the skew we care about.
        "7": {"ref": "lz4/1.9.4#m", "context": "host"},
        "8": {"ref": "lz4/1.10.0#n", "context": "host"},
    }}}
    root, comps = shipped_components(graph)
    got = sorted(c["ref"].split("#")[0] for c in comps)
    expect = ["insight_canon/1.8.3", "libcurl/8.20.0", "lz4/1.10.0", "lz4/1.9.4",
              "openssl/3.6.3"]
    assert got == expect, f"filter/dedupe: got {got}, expected {expect}"
    assert (root.get("ref") or "").startswith("insight_sift"), "root node misidentified"

    curl, has_cpe = to_component({"ref": "libcurl/8.20.0#def"})
    assert has_cpe, "libcurl must carry a CPE"
    assert curl["name"] == "curl", f'display name: {curl["name"]}'
    assert curl["purl"] == "pkg:conan/libcurl@8.20.0", f'purl: {curl["purl"]}'
    assert curl["cpe"] == "cpe:2.3:a:haxx:curl:8.20.0:*:*:*:*:*:*:*", f'cpe: {curl["cpe"]}'

    ssl, _ = to_component({"ref": "openssl/3.6.3#abc"})
    assert ssl["cpe"] == "cpe:2.3:a:openssl:openssl:3.6.3:*:*:*:*:*:*:*", f'cpe: {ssl["cpe"]}'

    unknown, has_cpe = to_component({"ref": "picosha2/1.0.0#x"})
    assert not has_cpe and "cpe" not in unknown, "unmapped package must not invent a CPE"

    # cpe-coverage ratio: third-party only, on BOTH sides, via the ONE
    # first-party predicate. curl carries a CPE, picosha2 does not, insight_canon is
    # first-party and must count on neither side -> 1/2.
    canon, _ = to_component({"ref": "insight_canon/1.8.7"})
    assert is_first_party(canon["name"]), "insight_canon must be first-party"
    assert not is_first_party(unknown["name"]), "picosha2 must be third-party"
    ratio = cpe_coverage([curl, unknown, canon])
    assert ratio == "1/2", f"cpe_coverage: got {ratio}, expected 1/2"
    assert cpe_coverage([canon]) == "0/0", "all-first-party SBOM must declare 0/0, not crash"

    # Per-artifact note: the server root gets the linked-closure boundary note,
    # any other root the generic derived note, and an explicit --note always wins.
    assert "LINKED-CLOSURE" in note_for("coderoast_server", None), "server note override lost"
    assert note_for("coderoast_server", "custom") == "custom", "explicit --note must win"
    # The routing assertion, stated so it can FAIL. It used to read
    # `note_for("insight_sift", None) == DEFAULT_NOTE` — which compares the function against
    # the constant it returns for any non-server root, and is therefore true for every
    # possible prose. That tautology is why 74/74 stayed green while a note edit left
    # coderoast-server's committed SBOM stale for three days and reded the v1.9.0 tag.
    # What is actually worth pinning is the ROUTING: a non-server root must not receive the
    # server's boundary note. That is a real distinction and a wrong ARTIFACT_NOTE key
    # breaks it.
    assert note_for("insight_sift", None) != note_for("coderoast_server", None), \
        "sift must NOT receive the server's linked-closure note"
    assert "LINKED-CLOSURE" not in note_for("insight_sift", None), \
        "the linked-closure boundary must not travel onto a non-server artifact"

    # The tripwire the stale-SBOM defect actually needed. The notes are document CONTENT, so
    # any edit to them invalidates every committed SBOM — and the only other check runs at
    # the tag. This fails at edit time instead, with the repair in the message.
    assert note_fingerprint() == NOTE_FINGERPRINT, (
        f"SBOM note text changed (fingerprint {note_fingerprint()}, pinned "
        f"{NOTE_FINGERPRINT}). The notes are written into metadata.properties, so EVERY "
        "committed sbom.cdx.json is now stale. Re-run `malf sbom`, commit the regenerated "
        "artifacts, and update NOTE_FINGERPRINT in the same commit.")

    # No `adr/` path form may reach a generated artifact: the workspace ADR shelf is private
    # and this file is PUBLIC, which is what the 2026-07-31 sweep was enforcing when it
    # edited the note and stranded the committed SBOMs.
    for label, text in [("DEFAULT_NOTE", DEFAULT_NOTE),
                        *((k, v) for k, v in ARTIFACT_NOTE.items())]:
        assert "adr/" not in text, f"{label} cites a private workspace path"

    print("sbom_gen selftest: OK (filter, display name, purl, cpe vendor+product, cpe absence, "
          "cpe-coverage ratio, per-artifact note)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, help="package dir whose graph to resolve")
    ap.add_argument("--profile", type=Path, help="conan profile applied to host and build")
    ap.add_argument("--out", type=Path, help="SBOM path to write (or compare against)")
    ap.add_argument("--lockfile", type=Path, default=None, help="workspace conan.lock")
    ap.add_argument("--note", default=None,
                    help="coderoast:note property text (default: the per-artifact note for the "
                         "resolved root, else the generic derived-artifact note)")
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if --out differs from the derived SBOM")
    ap.add_argument("--selftest", action="store_true", help="run offline self-test and exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not (args.root and args.profile and args.out):
        ap.error("--root, --profile and --out are required unless --selftest")

    sbom, missing_cpe = build_sbom(args.root, args.profile, args.lockfile, args.note)
    rendered = json.dumps(sbom, indent=2) + "\n"

    if missing_cpe:
        # Reported, never silent: a component with no CPE is scanned by PURL only, which is
        # weaker against NVD. The operator decides whether to extend CPE_VENDOR.
        sys.stderr.write("sbom_gen: NOTE — third-party components with no CPE mapping "
                         "(PURL-only CVE matching): " + ", ".join(missing_cpe) + "\n")

    if args.check:
        if not args.out.is_file():
            sys.stderr.write(f"sbom_gen: {args.out} does not exist\n")
            return 1
        if args.out.read_text() != rendered:
            sys.stderr.write(
                f"sbom_gen: {args.out} is STALE against the resolved graph. "
                f"Regenerate with `malf sbom`.\n")
            return 1
        print(f"sbom_gen: {args.out} matches the resolved graph ({len(sbom['components'])} components)")
        return 0

    args.out.write_text(rendered)
    print(f"sbom_gen: wrote {args.out} ({len(sbom['components'])} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
