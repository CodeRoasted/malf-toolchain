#!/usr/bin/env python3
"""The second CVE scanner step 0 runs beside trivy: one grype release and one grype vulnerability database build, each admitted only by its digest.

WHY GRYPE (the Founder, 2026-10-13, the N360 plan's ruling R-10 (c)). trivy matches a pkg:conan
component by its NAME against its conan advisory source and ignores the CPE that `sbom_gen.py`
curates, so most of a CodeRoast SBOM's C/C++ components are never matched (measured 2026-10-12 with
trivy 0.74.0: nothing reported for curl 8.20.0, against which curl's own feed lists 27 advisories).
grype matches those CPEs. Both are REPORT-ONLY at step 0: no finding of either ever blocks a cut;
a scanner that did not run, or failed, is red.

WHY A DIGEST AND NEVER A TAG — for the binary, the same argument as `trivy_pin.py`'s: the scanner
runs on the release runner, whose account holds the store writer's signing key, so a moved tag, a
replaced asset or a man in the middle must yield bytes that hash otherwise, and nothing is
extracted or run.

WHY THE DATABASE IS PINNED TOO (the same ruling): the same SBOM scanned twice must give the same
findings, so the database is one BUILD, named by its archive path and admitted by its SHA-256, never
"latest". The cost is stated, not hidden: a CVE disclosed after the pinned build is invisible until
the pin moves. A scan therefore records the build date beside its findings, and moving the pin is a
one-commit act: read `latest.json`, download, take the three readings below, change DB together.

THE PINS, and how they were taken (Argos, 2026-10-13):
  * grype 0.118.0, published 2026-08-27 — the version that measured the 27 curl findings on
    2026-10-12. OSV lists two advisories for grype (GHSA-6gxw-85q2-q646 = GO-2025-4160, credential
    disclosure in its JSON output), both fixed in 0.104.1. Each tarball digest was given by three
    producers that agreed: GitHub's asset `digest` field, the release's own
    `grype_0.118.0_checksums.txt`, and a recomputation over the downloaded bytes. Licence:
    Apache-2.0, read in the tarball's LICENSE.
  * database schema v6.1.10, built 2026-10-13T06:35:20Z — the build `latest.json` named that day.
    Its digest was given by two producers that agreed: the `checksum` field of `latest.json` and a
    recomputation over the downloaded archive; `grype db status` after the import read the same
    schema and build date, which `fetch` re-checks on every run.

    grype_pin.py fetch <directory>
        downloads the pinned tarball for this machine and the pinned database archive (each kept
        under <directory>, reused only while it still hashes to its pin), refuses any byte that
        hashes otherwise, writes the one `grype` member to <directory>/grype (mode 0755), refuses a
        binary whose `version` names another version, imports the database into <directory>/db and
        refuses an import whose schema or build date is not the pin's; prints the binary's path,
        then the database directory
    grype_pin.py selftest
        the refusals over fixture tarballs, a fake download and a fake grype, no network

A scan runs the binary with `GRYPE_DB_CACHE_DIR=<the database directory>` and `ENVIRONMENT`
below, which keep grype from replacing, age-refusing or phoning about the pinned build.

Exit 0 done, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import posixpath
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

VERSION = "0.118.0"
ASSETS = {
    "x86_64": ("grype_0.118.0_linux_amd64.tar.gz",
               "1d444c5e7360471815f7158f71935fcecc68a3c417d85c7344f770854300bba2"),
    "aarch64": ("grype_0.118.0_linux_arm64.tar.gz",
                "32aceeb8ee837244775fcb522372c8b3a47914986385f3148f4ee2c930482a84"),
}
DB_SCHEMA = "v6.1.10"
DB_BUILT = "2026-10-13T06:35:20Z"
DB_ARCHIVE = "vulnerability-db_v6.1.10_2026-10-12T00:41:00Z_1791873320.tar.zst"
DB_SHA256 = "ed50c6deedbbe39f815f855fb55208a1f261ebec6a91d2847b1230200bbb7b20"
MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
RELEASES = "https://github.com/anchore/grype/releases/download"
DATABASES = "https://grype.anchore.io/databases/v6"
MEMBER = "grype"
DB_DIR = "db"
TIMEOUT_SECONDS = 600
HASH_CHUNK = 1 << 20
# The pinned build stays the scanned build: no update, no age refusal (a pinned build ages by
# design), no network call about the application's own version.
ENVIRONMENT = {"GRYPE_DB_AUTO_UPDATE": "false", "GRYPE_DB_VALIDATE_AGE": "false",
               "GRYPE_CHECK_FOR_APP_UPDATE": "false"}

Download = Callable[[str, Path], None]
Grype = Callable[[Path, list[str], dict[str, str]], str]


class Refused(Exception):
    """A check failed; the message names what differed."""


def http_download(url: str, target: Path) -> None:
    """post: `target` holds the bytes `url` served, streamed (the database is ~190 MB)."""
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response, \
            partial.open("wb") as out:
        while chunk := response.read(HASH_CHUNK):
            out.write(chunk)
    partial.replace(target)


def run_grype(binary: Path, args: list[str], env: dict[str, str]) -> str:
    ran = subprocess.run([str(binary), *args], capture_output=True, text=True, check=False,
                         env={**os.environ, **env})
    if ran.returncode != 0:
        raise Refused(f"`grype {' '.join(args)}` exited {ran.returncode}: "
                      f"{(ran.stderr or ran.stdout).strip()[-400:]}")
    return ran.stdout


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def asset_for(machine: str, assets: dict[str, tuple[str, str]] = ASSETS) -> tuple[str, str]:
    """(the tarball's name, its pinned SHA-256) for `machine`, as `platform.machine()` spells it."""
    arch = MACHINES.get(machine.lower())
    if arch is None or arch not in assets:
        raise Refused(f"no grype {VERSION} tarball is pinned for machine {machine!r} "
                      f"(pinned: {', '.join(sorted(assets))})")
    return assets[arch]


def admitted(path: Path, url: str, pinned: str, download: Download) -> Path:
    """post: `path` hashes to `pinned`; a kept copy that does is reused, any other is replaced
    and judged again."""
    if path.is_file() and file_sha256(path) == pinned:
        return path
    download(url, path)
    got = file_sha256(path)
    if got != pinned:
        path.unlink()
        raise Refused(f"{path.name} hashes to {got}, not the pinned {pinned}: refused, deleted, "
                      f"nothing extracted")
    return path


def admitted_member(tarball: Path, pinned: str) -> bytes:
    """The tarball's one `grype` regular file, only when the tarball hashes to `pinned`; `./grype`
    names it too, so a second spelling of the member is a refusal and never a choice."""
    got = file_sha256(tarball)
    if got != pinned:
        raise Refused(f"{tarball.name} hashes to {got}, not the pinned {pinned}: refused")
    with tarfile.open(tarball, mode="r:gz") as archive:
        members = [member for member in archive.getmembers()
                   if posixpath.normpath(member.name) == MEMBER]
        if len(members) != 1 or not members[0].isreg():
            raise Refused(f"{tarball.name} holds {len(members)} `{MEMBER}` member(s), not one "
                          f"regular file")
        handle = archive.extractfile(members[0])
        assert handle is not None, "a regular member always has a file object"
        return handle.read()


def fetch(directory: Path, machine: str = platform.machine(), download: Download = http_download,
          grype: Grype = run_grype, assets: dict[str, tuple[str, str]] = ASSETS,
          db: tuple[str, str, str, str] = (DB_ARCHIVE, DB_SHA256, DB_SCHEMA, DB_BUILT)
          ) -> tuple[Path, Path]:
    """post: (<directory>/grype, <directory>/db) — the pinned binary for `machine`, its version
    checked, and the pinned database build imported into the directory a scan names."""
    name, pinned = asset_for(machine, assets)
    archive_name, archive_sha, schema, built = db
    directory.mkdir(parents=True, exist_ok=True)
    tarball = admitted(directory / name, f"{RELEASES}/v{VERSION}/{name}", pinned, download)
    target = directory / MEMBER
    target.write_bytes(admitted_member(tarball, pinned))
    target.chmod(0o755)
    reported = json.loads(grype(target, ["version", "-o", "json"], ENVIRONMENT) or "{}")
    if reported.get("version") != VERSION:
        raise Refused(f"{target} reports version {reported.get('version')!r}, not {VERSION}")
    archive = admitted(directory / archive_name, f"{DATABASES}/{archive_name}", archive_sha,
                       download)
    cache = directory / DB_DIR
    env = {**ENVIRONMENT, "GRYPE_DB_CACHE_DIR": str(cache)}
    grype(target, ["db", "import", str(archive)], env)
    status = json.loads(grype(target, ["db", "status", "-o", "json"], env) or "{}")
    if (status.get("schemaVersion"), status.get("built")) != (schema, built):
        raise Refused(f"the imported database reads schema {status.get('schemaVersion')!r} built "
                      f"{status.get('built')!r}, not the pinned {schema} built {built}")
    return target, cache


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────


def _tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for member, data in members.items():
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def selftest() -> int:
    failures: list[str] = []
    armed = [0]

    def arm(label: str, call: Callable[[], object], want: str) -> None:
        armed[0] += 1
        try:
            got = f"returned {call()!r}"
        except Refused as refused:
            got = f"refused: {refused}"
        if want not in got:
            failures.append(f"{label}: got {got}, want a result containing {want!r}")

    good = _tarball({"LICENSE": b"license", MEMBER: b"#!/bin/sh\necho fixture\n"})
    twice = _tarball({MEMBER: b"one", f"./{MEMBER}": b"two", "x": b""})
    database = b"a fixture database archive"
    fixture_db = ("fixture-db.tar.zst", hashlib.sha256(database).hexdigest(), "v6.1.10",
                  "2026-10-13T06:35:20Z")
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        fetched: list[str] = []

        def serving(tarball: bytes, db_bytes: bytes = database) -> Download:
            def download(url: str, target: Path) -> None:
                fetched.append(url.rsplit("/", 1)[-1])
                target.write_bytes(db_bytes if url.endswith(".tar.zst") else tarball)
            return download

        def pinned_to(body: bytes) -> dict[str, tuple[str, str]]:
            return {"x86_64": ("fixture.tar.gz", hashlib.sha256(body).hexdigest())}

        def fake(version: str = VERSION, schema: str = "v6.1.10",
                 built: str = "2026-10-13T06:35:20Z") -> Grype:
            def grype(_binary: Path, args: list[str], env: dict[str, str]) -> str:
                if args[0] == "version":
                    return json.dumps({"version": version})
                if args[:2] == ["db", "status"]:
                    return json.dumps({"schemaVersion": schema, "built": built,
                                       "path": env["GRYPE_DB_CACHE_DIR"]})
                return ""
            return grype

        arm("the pinned bytes are admitted, the member written and the database imported",
            lambda: tuple(p.name for p in fetch(root / "ok", "x86_64", serving(good), fake(),
                                                pinned_to(good), fixture_db)),
            "('grype', 'db')")
        arm("a kept tarball and database that still hash to their pins are not downloaded again",
            lambda: (fetched.clear(), fetch(root / "ok", "x86_64", serving(good), fake(),
                                            pinned_to(good), fixture_db), fetched)[2],
            "returned []")
        arm("tarball bytes differing from the pin are refused before anything is extracted",
            lambda: fetch(root / "tampered", "x86_64", serving(good + b"\0"), fake(),
                          pinned_to(good), fixture_db),
            "not the pinned")
        arm("nothing is written for refused bytes, and the refused download is deleted",
            lambda: ((root / "tampered" / MEMBER).exists(),
                     (root / "tampered" / "fixture.tar.gz").exists()), "returned (False, False)")
        arm("database bytes differing from the pin are refused",
            lambda: fetch(root / "db-tampered", "x86_64", serving(good, database + b"\0"), fake(),
                          pinned_to(good), fixture_db),
            "fixture-db.tar.zst hashes to")
        arm("a binary reporting another version is refused",
            lambda: fetch(root / "other", "x86_64", serving(good), fake(version="0.104.0"),
                          pinned_to(good), fixture_db),
            "not 0.118.0")
        arm("an import reading another schema or build date is refused",
            lambda: fetch(root / "stale", "x86_64", serving(good),
                          fake(built="2026-01-01T00:00:00Z"), pinned_to(good), fixture_db),
            "not the pinned v6.1.10 built 2026-10-13T06:35:20Z")
        arm("a tarball without exactly one regular `grype` member is refused",
            lambda: fetch(root / "twice", "x86_64", serving(twice), fake(), pinned_to(twice),
                          fixture_db),
            "holds 2 `grype` member(s)")
        arm("a machine with no pinned tarball is refused, never a guess",
            lambda: fetch(root / "riscv", "riscv64", serving(good)), "no grype")
        arm("amd64 and arm64 spell the two pinned machines",
            lambda: (asset_for("amd64")[0], asset_for("arm64")[0]),
            f"('grype_{VERSION}_linux_amd64.tar.gz', 'grype_{VERSION}_linux_arm64.tar.gz')")
        arm("every pinned digest is a SHA-256",
            lambda: all(len(digest) == 64 and int(digest, 16) >= 0
                        for digest in [*(d for _n, d in ASSETS.values()), DB_SHA256]),
            "returned True")
    for failure in failures:
        print(f"grype_pin selftest FAILED: {failure}")
    print(f"grype_pin selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 2 and argv[0] == "fetch":
            binary, cache = fetch(Path(argv[1]))
            print(binary)
            print(cache)
            return 0
    except Refused as refused:
        print(f"grype_pin: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
