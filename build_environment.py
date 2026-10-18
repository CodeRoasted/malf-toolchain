#!/usr/bin/env python3
"""The build environment's launcher (DN-142.D30 (3)): every keyed act runs in ONE pinned image.

The pin is `config/build-environment.json`, three values and no registry:
  image     the image's CONFIG digest (`sha256:<hex>`), the key's `system` member: it names the decoded
            layers through their diff_ids, content identity independent of compression and daemon;
  id        the MANIFEST digest, what a containerd-store daemon reports as the image's ID and what a
            container is started by; it depends on compression, so it is pinned here and keyed nowhere;
  archive   `{name, bytes, sha256}` of the `docker save` archive the image is loaded from, kept in the
            build-environment directory (`~/build-environment`, MALF_BUILD_ENVIRONMENT_DIR names another).

INTEGRITY, FROM TWO PRODUCERS. The pin is in git; the daemon computes the two digests from the bytes it
loaded. The launcher refuses unless the daemon runs the containerd image store (on the classic store an
image's ID is its config digest, so the ID's species would depend on the daemon), the pinned `id` is
present, and its `config.digest` annotation equals the pinned `image`. When the `id` is absent it loads
the archive first, and only after checking the archive's size and SHA-256 against the pin: a wrong
archive is refused and nothing is loaded.

A CONTAINER is started with `docker run --rm --init --network none` by the pinned ID, never by a tag;
each mount at its own absolute path; the environment it receives is exactly the declared names (each
read from this process and refused when unset) plus MALF_SYSTEM, the pinned config digest, and nothing
is inherited; its working directory is the caller's, which must lie under a mount. `--seccomp-unconfined`
lifts Docker's default seccomp profile, for the store-create container alone (its declared-unconfined
namespace tests unshare a mount namespace, which the default profile refuses).

    build_environment.py check
        the daemon's store, the pinned id present and its config digest the pin's; exit 0, or 1 naming each
    build_environment.py load
        the verified load: the archive checked and loaded when the pinned id is absent, then `check`
    build_environment.py run [--seccomp-unconfined] [--host-pids] [--home <dir>] [--mount <dir>]... [--read-only-mount <dir>]...
                             [--corpus-gates <file>] [--env <NAME>]... -- <argv>...
        <argv> in the environment, each corpus mount the document names mounted read-only; exit the
        command's own, or 2 refused before it ran
    build_environment.py selftest
        every refusal and the container's isolation, over a throwaway image built here (FROM scratch, no
        network), never the real archive
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PIN = Path(__file__).resolve().parent / "config" / "build-environment.json"
DIRECTORY_ENV = "MALF_BUILD_ENVIRONMENT_DIR"
DIRECTORY_DEFAULT = "build-environment"
SYSTEM_ENV = "MALF_SYSTEM"
CONTAINERD_STORE = "io.containerd.snapshotter.v1"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
REFUSED = 2
# The container's own HOME: the image's, never the caller's, which is not mounted.
CONTAINER_HOME = "/root"
HASH_CHUNK = 1 << 20


class Refused(RuntimeError):
    """The environment cannot be trusted or reached, so nothing runs in it."""


@dataclass(frozen=True)
class Pin:
    image: str
    id: str
    archive_name: str
    archive_bytes: int
    archive_sha256: str


def read_pin(path: Path = PIN) -> Pin:
    """The pin at `path`. raises Refused on a pin that is not exactly its three values, well formed."""
    try:
        document = json.loads(path.read_text())
        archive = document["archive"]
        pin = Pin(document["image"], document["id"], archive["name"], archive["bytes"], archive["sha256"])
    except (OSError, ValueError, KeyError, TypeError) as unread:
        raise Refused(f"the build-environment pin {path} cannot be read as {{image, id, archive: {{name, bytes, "
                      f"sha256}}}}: {unread!r}") from unread
    wrong = [f"{name} is {value!r}" for name, value in (("image", pin.image), ("id", pin.id)) if not DIGEST.match(str(value))]
    if not HEX.match(str(pin.archive_sha256)):
        wrong.append(f"archive.sha256 is {pin.archive_sha256!r}")
    if not isinstance(pin.archive_bytes, int) or pin.archive_bytes <= 0:
        wrong.append(f"archive.bytes is {pin.archive_bytes!r}")
    if "/" in str(pin.archive_name) or not str(pin.archive_name).endswith(".tar"):
        wrong.append(f"archive.name is {pin.archive_name!r}, not a file name ending .tar")
    if wrong:
        raise Refused(f"the build-environment pin {path} is malformed: {'; '.join(wrong)}")
    return pin


def archive_directory() -> Path:
    return Path(os.environ.get(DIRECTORY_ENV) or Path.home() / DIRECTORY_DEFAULT)


def _docker(docker: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([docker, *args], capture_output=True, text=True, check=False)


def daemon_store(docker: str) -> str:
    """The daemon's image store: its `driver-type`, or its storage driver when it reports none."""
    done = _docker(docker, "info", "--format", "{{json .DriverStatus}}|{{.Driver}}")
    if done.returncode != 0:
        raise Refused(f"`docker info` failed, so no daemon answers: {done.stderr.strip()}")
    status, _sep, driver = done.stdout.strip().partition("|")
    for pair in json.loads(status or "null") or []:
        if len(pair) == 2 and pair[0] == "driver-type":
            return pair[1]
    return driver


def inspected(docker: str, reference: str) -> tuple[str, str] | None:
    """(the ID, the `config.digest` annotation) the daemon reports for `reference`, None when it holds none."""
    done = _docker(docker, "image", "inspect", "--format", '{{.Id}}|{{index .Descriptor.annotations "config.digest"}}',
                   reference)
    if done.returncode != 0:
        return None
    image_id, _sep, config = done.stdout.strip().partition("|")
    return image_id, ("" if config == "<no value>" else config)


def check(pin: Pin, docker: str) -> None:
    """raises Refused naming each disagreement between the daemon and the pin."""
    store = daemon_store(docker)
    if store != CONTAINERD_STORE:
        raise Refused(f"the daemon's image store is {store!r}, not the containerd store ({CONTAINERD_STORE}): an image's "
                      f"ID there is not the manifest digest the pin names (DN-142.D30 (3))")
    seen = inspected(docker, pin.id)
    if seen is None:
        raise Refused(f"the daemon holds no image {pin.id}; `build_environment.py load` loads it from "
                      f"{archive_directory() / pin.archive_name}")
    wrong = []
    if seen[0] != pin.id:
        wrong.append(f"its ID is {seen[0]}, the pin's {pin.id}")
    if seen[1] != pin.image:
        wrong.append(f"its config digest is {seen[1] or 'absent'}, the pin's {pin.image}")
    if wrong:
        raise Refused(f"the image the daemon runs as {pin.id} is not the pinned build environment: {'; '.join(wrong)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(pin: Pin, docker: str, directory: Path | None = None) -> str:
    """The verified load (DN-142.D30 (3) *The load*): when the daemon lacks the pinned id, the archive is
    checked against the pin's size and SHA-256, then loaded; then `check`. Returns what was done.
    raises Refused naming the file and the pin; a wrong archive loads nothing."""
    if inspected(docker, pin.id) is None:
        archive = (directory or archive_directory()) / pin.archive_name
        if not archive.is_file():
            raise Refused(f"the build-environment archive {archive} does not exist, and the daemon holds no {pin.id}")
        size = archive.stat().st_size
        if size != pin.archive_bytes:
            raise Refused(f"the build-environment archive {archive} is {size} bytes, the pin's {pin.archive_bytes}: "
                          "nothing was loaded")
        found = _sha256(archive)
        if found != pin.archive_sha256:
            raise Refused(f"the build-environment archive {archive} has SHA-256 {found}, the pin's {pin.archive_sha256}: "
                          "nothing was loaded")
        done = _docker(docker, "load", "-i", str(archive))
        if done.returncode != 0:
            raise Refused(f"`docker load -i {archive}` failed: {done.stderr.strip()}")
        check(pin, docker)
        return f"loaded {archive} as {pin.id}"
    check(pin, docker)
    return f"held {pin.id}"


def run_argv(pin: Pin, argv: list[str], mounts: list[Path], names: list[str], cwd: Path,
             seccomp_unconfined: bool = False, docker: str = "docker", environ: dict | None = None,
             read_only: list[Path] | tuple = (), host_pids: bool = False, home: str = CONTAINER_HOME) -> list[str]:
    """The `docker run` command of one act: `mounts` writable and `read_only` read-only, each at its own
    path; `host_pids` shares the host's process ids (a bench walk judging the build slot's holder).
    raises Refused on an unset declared name, a mount that is not an existing absolute directory, or a
    working directory under no writable mount. `home` is the container's HOME: the image's own unless the
    act's conf renders a path under the caller's (malf's source download cache)."""
    environ = os.environ if environ is None else environ
    unset = sorted(name for name in names if name not in environ)
    if unset:
        raise Refused(f"the act declares {', '.join(unset)}, unset in its caller: an undeclared default would run")
    resolved = []
    for mount in [*mounts, *read_only]:
        if not mount.is_absolute() or not mount.is_dir():
            raise Refused(f"the mount {mount} is not an existing absolute directory")
    resolved = list(mounts)
    if not any(cwd == mount or mount in cwd.parents for mount in resolved):
        raise Refused(f"the working directory {cwd} lies under no mount ({', '.join(map(str, resolved)) or 'none'})")
    command = [docker, "run", "--rm", "--init", "--network", "none", "--workdir", str(cwd),
               "--env", f"HOME={home}", "--env", f"{SYSTEM_ENV}={pin.image}"]
    if seccomp_unconfined:
        command += ["--security-opt", "seccomp=unconfined"]
    if host_pids:
        command += ["--pid", "host"]
    for mount in resolved:
        command += ["--volume", f"{mount}:{mount}"]
    for mount in read_only:
        command += ["--volume", f"{mount}:{mount}:ro"]
    for name in names:
        command += ["--env", f"{name}={environ[name]}"]
    return [*command, pin.id, *argv]


def corpus_mounts(document: Path) -> list[Path]:
    """Every mount the corpus gates document names (malf_closure.corpus_mounts, the domain's reading)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import malf_closure

    try:
        every, _own = malf_closure.corpus_mounts(document, None)
    except malf_closure.ClosureError as unread:
        raise Refused(str(unread)) from unread
    return sorted(set(every))


def run(pin: Pin, argv: list[str], mounts: list[Path], names: list[str], seccomp_unconfined: bool,
        docker: str = "docker", read_only: list[Path] | tuple = (), host_pids: bool = False,
        home: str = CONTAINER_HOME) -> int:
    load(pin, docker)
    return subprocess.run(run_argv(pin, argv, mounts, names, Path.cwd(), seccomp_unconfined, docker,
                                   read_only=read_only, host_pids=host_pids, home=home),
                          stdin=subprocess.DEVNULL, check=False).returncode


# ── selftest────────────────────────────────────────────────────────────────────────────────────

PROBE_SOURCE = r"""
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
extern char **environ;
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "env") == 0) {
        for (char **entry = environ; *entry; ++entry) puts(*entry);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "net") == 0) {
        int sock = socket(AF_INET, SOCK_DGRAM, 0);
        struct sockaddr_in to;
        memset(&to, 0, sizeof to);
        to.sin_family = AF_INET;
        to.sin_port = htons(53);
        inet_pton(AF_INET, "1.1.1.1", &to.sin_addr);
        if (connect(sock, (struct sockaddr *)&to, sizeof to) == 0) { puts("reached"); return 0; }
        perror("unreachable");
        return 1;
    }
    return 0;
}
"""


def _throwaway(base: Path, docker: str) -> tuple[Pin, Path]:
    """A throwaway image (FROM scratch, one static probe), built with the pin's flags, saved, and the pin
    its own daemon reports: (the pin, the archive's directory)."""
    context = base / "context"
    context.mkdir()
    (context / "probe.c").write_text(PROBE_SOURCE)
    subprocess.run(["cc", "-static", "-O1", "-o", str(context / "probe"), str(context / "probe.c")], check=True)
    (context / "Dockerfile").write_text("FROM scratch\nCOPY probe /probe\nENTRYPOINT []\n")
    tag = f"malf-build-environment-selftest:{os.getpid()}"
    subprocess.run([docker, "buildx", "build", "--platform", "linux/amd64", "--provenance=false", "--sbom=false",
                    "--load", "--quiet", "-t", tag, str(context)], check=True, capture_output=True)
    seen = inspected(docker, tag)
    assert seen is not None, f"the throwaway image {tag} was built and is not inspectable"
    directory = base / "archives"
    directory.mkdir()
    archive = directory / "build-environment-selftest.tar"
    subprocess.run([docker, "save", "-o", str(archive), tag], check=True)
    subprocess.run([docker, "rmi", "--force", tag], check=True, capture_output=True)
    return Pin(seen[1], seen[0], archive.name, archive.stat().st_size, _sha256(archive)), directory


def selftest() -> int:
    passed = failed = 0

    def arm(label: str, got: object, want: object) -> None:
        nonlocal passed, failed
        if got == want:
            passed += 1
            print(f"  ok   {label}")
        else:
            failed += 1
            print(f"  FAIL {label}: got {got!r}, want {want!r}")

    def refusal(call) -> str:
        try:
            call()
        except Refused as refused:
            return str(refused)
        return "no refusal"

    docker = shutil.which("docker") or "docker"
    with tempfile.TemporaryDirectory(prefix="build_environment.selftest.") as held:
        base = Path(held)
        pin, directory = _throwaway(base, docker)
        try:
            arm("the throwaway image's pin is its config digest and its manifest digest, two different values",
                (bool(DIGEST.match(pin.image)), bool(DIGEST.match(pin.id)), pin.image != pin.id), (True, True, True))
            wrong_sha = Pin(pin.image, pin.id, pin.archive_name, pin.archive_bytes, "0" * 64)
            said = refusal(lambda: load(wrong_sha, docker, directory))
            arm("RED: an archive whose SHA-256 differs from the pin is refused, naming both, and nothing is loaded",
                (f"has SHA-256 {pin.archive_sha256}, the pin's {'0' * 64}" in said, inspected(docker, pin.id)),
                (True, None))
            arm("RED: an absent archive is refused, naming the file",
                refusal(lambda: load(Pin(pin.image, pin.id, "absent.tar", 1, "0" * 64), docker, directory)).endswith(
                    f"{directory / 'absent.tar'} does not exist, and the daemon holds no {pin.id}"), True)
            arm("the verified load: an absent pinned id is loaded from its archive, then checked",
                load(pin, docker, directory), f"loaded {directory / pin.archive_name} as {pin.id}")
            arm("a second load finds the id held and checks it", load(pin, docker, directory), f"held {pin.id}")
            other = "sha256:" + "1" * 64
            arm("RED: an image whose config digest differs from the pin is refused, naming both",
                f"its config digest is {pin.image}, the pin's {other}" in refusal(
                    lambda: check(Pin(other, pin.id, pin.archive_name, pin.archive_bytes, pin.archive_sha256), docker)),
                True)
            arm("RED: a daemon holding no image under the pinned id is refused, naming it",
                refusal(lambda: check(Pin(pin.image, other, pin.archive_name, pin.archive_bytes, pin.archive_sha256),
                                      docker)).startswith(f"the daemon holds no image {other}"), True)
            classic = base / "classic-docker"
            classic.write_text(f'#!/bin/sh\nif [ "$1" = info ]; then echo "null|overlay2"; exit 0; fi\nexec {docker} "$@"\n')
            classic.chmod(0o755)
            arm("RED: a daemon on the classic image store is refused",
                "is 'overlay2', not the containerd store" in refusal(lambda: check(pin, str(classic))), True)
            mount = base / "mounted"
            mount.mkdir()
            environ = {"DECLARED": "yes", "HOST_SECRET": "leaked"}
            printed = subprocess.run(run_argv(pin, ["/probe", "env"], [mount], ["DECLARED"], mount, docker=docker,
                                              environ=environ), capture_output=True, text=True, check=False)
            names = sorted(line.split("=", 1)[0] for line in printed.stdout.splitlines())
            arm("RED: an undeclared variable of the host is absent inside; the declared one and MALF_SYSTEM are there",
                (printed.returncode, "HOST_SECRET" in names, "DECLARED=yes" in printed.stdout,
                 f"{SYSTEM_ENV}={pin.image}" in printed.stdout), (0, False, True, True))
            netted = subprocess.run(run_argv(pin, ["/probe", "net"], [mount], [], mount, docker=docker),
                                    capture_output=True, text=True, check=False)
            arm("RED: a container cannot reach the network", (netted.returncode, netted.stdout), (1, ""))
            arm("RED: a declared name unset in the caller is refused, never run with a default",
                refusal(lambda: run_argv(pin, ["/probe"], [mount], ["UNSET_NAME"], mount, environ={})).startswith(
                    "the act declares UNSET_NAME, unset in its caller"), True)
            arm("RED: a working directory under no mount is refused",
                refusal(lambda: run_argv(pin, ["/probe"], [mount], [], base, environ={})).startswith(
                    f"the working directory {base} lies under no mount"), True)
            arm("the store-create container alone lifts the default seccomp profile",
                ("seccomp=unconfined" in run_argv(pin, ["/probe"], [mount], [], mount, True, environ={}),
                 "seccomp=unconfined" in run_argv(pin, ["/probe"], [mount], [], mount, False, environ={})),
                (True, False))
            (base / "pin.json").write_text(json.dumps({"image": pin.image, "id": "latest", "archive": {
                "name": pin.archive_name, "bytes": pin.archive_bytes, "sha256": pin.archive_sha256}}))
            arm("RED: a pin naming its id by anything but a digest is refused",
                "id is 'latest'" in refusal(lambda: read_pin(base / "pin.json")), True)
        finally:
            subprocess.run([docker, "rmi", "--force", pin.id], capture_output=True, check=False)
    print(f"build_environment selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    docker = shutil.which("docker") or "docker"
    if argv[:1] == ["run"]:
        if "--" not in argv:
            print("build_environment.py run: name the command after --", file=sys.stderr)
            return REFUSED
        split = argv.index("--")
        options = argparse.ArgumentParser(prog="build_environment.py run")
        options.add_argument("--seccomp-unconfined", action="store_true")
        options.add_argument("--mount", action="append", default=[], type=Path)
        options.add_argument("--read-only-mount", action="append", default=[], type=Path)
        options.add_argument("--corpus-gates", type=Path)
        options.add_argument("--host-pids", action="store_true")
        options.add_argument("--home", default=CONTAINER_HOME)
        options.add_argument("--env", action="append", default=[])
        given = options.parse_args(argv[1:split])
        if not argv[split + 1:]:
            print("build_environment.py run: the command after -- is empty", file=sys.stderr)
            return REFUSED
        try:
            read_only = [*given.read_only_mount, *(corpus_mounts(given.corpus_gates) if given.corpus_gates else [])]
            return run(read_pin(), argv[split + 1:], given.mount, given.env, given.seccomp_unconfined, docker,
                       read_only, given.host_pids, given.home)
        except Refused as refused:
            print(f"build_environment: REFUSED — {refused}; the act did not run (DN-142.D30, OPS-1.S27)", file=sys.stderr)
            return REFUSED
    parser = argparse.ArgumentParser(prog="build_environment.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("check", "load", "selftest"):
        verbs.add_parser(verb)
    args = parser.parse_args(argv)
    if args.verb == "selftest":
        return selftest()
    try:
        pin = read_pin()
        if args.verb == "check":
            check(pin, docker)
            print(f"build_environment: {pin.id} held, its config digest {pin.image}")
        else:
            print(f"build_environment: {load(pin, docker)}, its config digest {pin.image}")
        return 0
    except Refused as refused:
        print(f"build_environment: REFUSED — {refused} (DN-142.D30, OPS-1.S27)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
