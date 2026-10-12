#!/usr/bin/env python3
"""The CVE scanner every CodeRoast SBOM gate runs: one trivy release, fetched by URL and admitted only by its digest.

WHY A DIGEST AND NEVER A TAG. The gate used to run `curl …/aquasecurity/trivy/v<V>/contrib/install.sh |
sh`: the script, and the binary it chose, were whatever that tag named at the moment of the run.
Trivy's own release publishing was taken over on 2026-03-19 and shipped a malicious v0.69.4
(GO-2026-4919), and insight-eidos's release runs the gate on the release runner, whose account
holds the store writer's signing key. So the scanner is admitted here by the SHA-256 of its
release tarball, a constant of this file: a moved tag, a replaced asset or a man in the middle
yields bytes that hash otherwise, and nothing is extracted or run.

THE PIN, and how it was taken (2026-10-12): trivy 0.74.0, published 2026-08-14, the newest release
with two months behind it. 0.71.0, the gate's previous version, is inside GHSA-mcj4-mphf-j9ff
(path traversal through a crafted vulnerability database, fixed in 0.71.1) and
GHSA-8rc5-4fr6-64pw (plugin manager path traversal, fixed in 0.72.0); OSV lists no advisory for
0.74.0 but GO-2026-4919, whose range is open-ended and which names 0.69.4 alone in its text. Each
digest below was given by three producers that agreed: GitHub's asset `digest` field, the
release's own `trivy_0.74.0_checksums.txt`, and a recomputation over the downloaded bytes. To move
it, take the same three readings for the new version and change VERSION and ASSETS together.

    trivy_pin.py fetch <directory>
        downloads the pinned tarball for this machine, refuses it unless it hashes to the pin,
        writes its one `trivy` member to <directory>/trivy (mode 0755), refuses a binary whose
        `--version` names another version, and prints the binary's path
    trivy_pin.py selftest
        the refusals over fixture tarballs and a fake download, no network

Exit 0 done, 1 refused, 2 a usage error.
"""

from __future__ import annotations

import hashlib
import io
import platform
import posixpath
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

VERSION = "0.74.0"
ASSETS = {
    "x86_64": ("trivy_0.74.0_Linux-64bit.tar.gz",
               "2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a"),
    "aarch64": ("trivy_0.74.0_Linux-ARM64.tar.gz",
                "b94ce1976bbf3c15b514b605ee88be7c6d94a29be2302847ff01cb794d47aad5"),
}
MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
RELEASES = "https://github.com/aquasecurity/trivy/releases/download"
MEMBER = "trivy"
TIMEOUT_SECONDS = 120

Download = Callable[[str], bytes]


class Refused(Exception):
    """A check failed; the message names what differed."""


def http_download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def asset_for(machine: str, assets: dict[str, tuple[str, str]] = ASSETS) -> tuple[str, str]:
    """(the tarball's name, its pinned SHA-256) for `machine`, as `platform.machine()` spells it."""
    arch = MACHINES.get(machine.lower())
    if arch is None or arch not in assets:
        raise Refused(f"no trivy {VERSION} tarball is pinned for machine {machine!r} "
                      f"(pinned: {', '.join(sorted(assets))})")
    return assets[arch]


def admitted_member(tarball: bytes, name: str, pinned: str) -> bytes:
    """The tarball's one `trivy` regular file, only when the tarball hashes to `pinned`; `./trivy`
    names it too, so a second spelling of the member is a refusal and never a choice."""
    got = hashlib.sha256(tarball).hexdigest()
    if got != pinned:
        raise Refused(f"{name} hashes to {got}, not the pinned {pinned}: refused, nothing extracted")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if posixpath.normpath(member.name) == MEMBER]
        if len(members) != 1 or not members[0].isreg():
            raise Refused(f"{name} holds {len(members)} `{MEMBER}` member(s), not one regular file")
        handle = archive.extractfile(members[0])
        assert handle is not None, "a regular member always has a file object"
        return handle.read()


def fetch(directory: Path, machine: str = platform.machine(), download: Download = http_download,
          version_of: Callable[[Path], str] | None = None,
          assets: dict[str, tuple[str, str]] = ASSETS) -> Path:
    """post: <directory>/trivy is the pinned binary for `machine`, its version line checked."""
    name, pinned = asset_for(machine, assets)
    binary = admitted_member(download(f"{RELEASES}/v{VERSION}/{name}"), name, pinned)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MEMBER
    target.write_bytes(binary)
    target.chmod(0o755)
    reported = (version_of or _version_line)(target)
    if reported != f"Version: {VERSION}":
        raise Refused(f"{target} reports {reported!r}, not `Version: {VERSION}`")
    return target


def _version_line(binary: Path) -> str:
    ran = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=False)
    return ran.stdout.splitlines()[0].strip() if ran.returncode == 0 and ran.stdout else \
        f"exit {ran.returncode}: {ran.stderr.strip()}"


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
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)

        def serving(body: bytes) -> Download:
            return lambda url: body

        def pinned_to(body: bytes) -> dict[str, tuple[str, str]]:
            return {"x86_64": ("fixture.tar.gz", hashlib.sha256(body).hexdigest())}

        def version(_binary: Path) -> str:
            return f"Version: {VERSION}"

        arm("the pinned bytes are admitted and their one member written",
            lambda: fetch(root / "ok", "x86_64", serving(good), version, pinned_to(good)).read_bytes(),
            "echo fixture")
        arm("bytes differing from the pin are refused before anything is extracted",
            lambda: fetch(root / "tampered", "x86_64", serving(good + b"\0"), version, pinned_to(good)),
            "not the pinned")
        arm("nothing is written for refused bytes",
            lambda: (root / "tampered" / MEMBER).exists(), "returned False")
        arm("a binary reporting another version is refused",
            lambda: fetch(root / "other", "x86_64", serving(good), lambda _p: "Version: 0.69.4", pinned_to(good)),
            "not `Version: ")
        arm("a tarball without exactly one regular `trivy` member is refused",
            lambda: fetch(root / "twice", "x86_64", serving(twice), version, pinned_to(twice)),
            "holds 2 `trivy` member(s)")
        arm("a machine with no pinned tarball is refused, never a guess",
            lambda: fetch(root / "riscv", "riscv64", serving(good)), "no trivy")
        arm("amd64 and arm64 spell the two pinned machines",
            lambda: (asset_for("amd64")[0], asset_for("arm64")[0]),
            f"('trivy_{VERSION}_Linux-64bit.tar.gz', 'trivy_{VERSION}_Linux-ARM64.tar.gz')")
        arm("every pinned digest is a SHA-256",
            lambda: all(len(digest) == 64 and int(digest, 16) >= 0 for _name, digest in ASSETS.values()),
            "returned True")
    for failure in failures:
        print(f"trivy_pin selftest FAILED: {failure}")
    print(f"trivy_pin selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv == ["selftest"]:
            return selftest()
        if len(argv) == 2 and argv[0] == "fetch":
            print(fetch(Path(argv[1])))
            return 0
    except Refused as refused:
        print(f"trivy_pin: REFUSED — {refused}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
