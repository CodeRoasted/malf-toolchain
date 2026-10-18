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
is inherited; its working directory is the caller's, which must lie under a mount.

THE ACT'S USER (DN-142.D30 (8) R4). Every container starts its argv under `unshare --user
--map-user=1000 --map-group=1000 --`: the act is uid 1000 (the image's `ubuntu`) with no capability, in a
nested user namespace where uid 1000 is the container's root, which rootless Docker maps to the account
running the launcher. So the act writes the mounted trees by owner permission as on the host, a mode-0500
directory refuses it, and tar restores no archive member's owner. The uid is a constant, never the
caller's, so the desk and the seat present one uid to an act. Every container therefore lifts Docker's
default seccomp profile, which refuses CLONE_NEWUSER; holding no capability, the act is still refused
every privileged syscall by the kernel.

    build_environment.py check
        the daemon's store, the pinned id present and its config digest the pin's; exit 0, or 1 naming each
    build_environment.py load
        the verified load: the archive checked and loaded when the pinned id is absent, then `check`
    build_environment.py run [--host-pids] [--home <dir>] [--mount <dir>]... [--read-only-mount <dir>]...
                             [--corpus-gates <file>] [--env <NAME>]... [--backends <file>] [--attest <file>]
                             [--worktree-repository <dir>]... -- <argv>...
        <argv> in the environment, each corpus mount the document names mounted read-only; with
        --backends <file>, beside those backends on the walk's internal network instead of none; with
        --attest <file>, that file receives `{"image", "id"}` once the integrity check passed and before
        the container starts, the act's system identity (DN-142.D30 (9) E3); with --worktree-repository,
        refused unless every worktree entry of each named repository lies under a mount (R9); exit the
        command's own, or 2 refused before it ran
    build_environment.py definition-check [--conan-home <dir>]...
        the definition against the lock (DN-142.D30 (5) E2a): every third-party recipe of conan.lock whose
        source() fetches with git, read from the first conan home holding its revision (default: CONAN_HOME
        and each home under it), has a row in config/build-environment/git-mirrors.txt naming its URL and
        commit, and no row is left that no recipe fetches; exit 0, or 1 naming each
    build_environment.py mirror-check
        each mirror row served by the pinned image: a `--network none` container runs conan's own
        Git.fetch_commit of the row's URL and commit (the recipe's `git fetch --depth 1 origin <commit>`)
        and checks out exactly that commit; exit 0, or 1 naming each row that failed
    build_environment.py backends [--worktree] [--variables] <package dir> <package> [<package dir> <package>]...
        the backends the named packages declare under `backends:` in their repository's packages.yml (at HEAD,
        or the working tree), one per name (two packages declaring one name differently are refused), as
        the JSON list `run --backends` and `with-backends --backends` read; --variables prints instead each
        CODEROAST_BACKEND_<NAME>=<name>:<port> an act beside the walk's network reaches it by
        --parameters <document> prints instead the walk's parameter document with each such variable added
        to its `env`, the document a declaring step's act is keyed under
    build_environment.py backends-at <package dir>
        the backends the package at that directory declares in its working-tree packages.yml, as JSON
    build_environment.py with-backends --backends <file> -- <argv>...
        <argv> on the host beside those backends published on 127.0.0.1 at ports the daemon allocates, each
        address in its CODEROAST_BACKEND_<NAME> (the desk's `malf test`); exit the command's, 2 refused
    build_environment.py selftest
        every refusal and the container's isolation, over a throwaway image built here (FROM scratch, no
        network), never the real archive; the definition check's refusals over recipes written here

BACKENDS (DN-142.D30 (5) E2b, (7) R2). A package whose tests need a server declares it under `backends:`
in its packages.yml: `{name, image: <ref>@sha256:<64 hex>, port, health, command, environment}`. A walk
holding a declaring step runs its container on ONE `docker network create --internal` network the launcher
creates, each backend started on it under its name with its healthcheck as the barrier, bounded, and
removes the backends and the network on every exit path (a dead launcher's leftovers, labelled with its
pid, are reaped by the next); a walk with no declaring step keeps `--network none`. A declaring step's act
reaches each backend as CODEROAST_BACKEND_<NAME>=<name>:<port>, keyed in its parameter document, the
declaration itself in its key's `step` member. No container the launcher starts holds a Docker socket.
The desk's `malf test` starts the same backends through the same code, published on 127.0.0.1.

GIT MIRRORS. The walk has no network, and conan's source download cache serves only a downloaded file, so a
recipe whose source() fetches with git (ConanCenter's gnu-config) is served from a bare mirror built into the
image while it is built: each row `<upstream url> <commit>` of git-mirrors.txt becomes a bare clone at
/opt/build-environment/git-mirrors/<url without its scheme>, serving any commit id it holds, and a system
`url.file://<mirror>.insteadOf <upstream url>` rule, so the recipe's own fetch reads the mirror. The commit id
is in the recipe's conandata, and git verifies every object it serves against it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

PIN = Path(__file__).resolve().parent / "config" / "build-environment.json"
DEFINITION = Path(__file__).resolve().parent / "config" / "build-environment"
GIT_MIRRORS = DEFINITION / "git-mirrors.txt"
LOCK = Path(__file__).resolve().parent / "conan.lock"
MIRROR_ROOT = "/opt/build-environment/git-mirrors"
# A source() that fetches with git: conan's Git helper, or a git clone/fetch it runs itself.
GIT_SOURCE = re.compile(r"\bGit\s*\(|\bgit\s+(?:clone|fetch)\b")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
BASE_IMAGE = re.compile(r"^FROM (\S+@sha256:[0-9a-f]{64})", re.MULTILINE)
DIRECTORY_ENV = "MALF_BUILD_ENVIRONMENT_DIR"
DIRECTORY_DEFAULT = "build-environment"
SYSTEM_ENV = "MALF_SYSTEM"
CONTAINERD_STORE = "io.containerd.snapshotter.v1"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
REFUSED = 2
# The act's uid and gid inside its nested user namespace: the image's `ubuntu` (DN-142.D30 (8) R4).
ACT_UID = 1000
# The container's own HOME: the image's, never the caller's, which is not mounted.
CONTAINER_HOME = "/root"
# The caller's shared-memory tmpfs, whose size the container's /dev/shm takes: Docker's default is 64 MiB,
# and logcraft's shared-memory gates fallocate 17 MB per shard (measured in the first walk inside the
# image, 2026-10-18: `posix_fallocate ... No space left on device`).
SHM = Path("/dev/shm")
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
             docker: str = "docker", environ: dict | None = None,
             read_only: list[Path] | tuple = (), host_pids: bool = False, home: str = CONTAINER_HOME,
             network: str = "none", name: str | None = None) -> list[str]:
    """The `docker run` command of one act: `mounts` writable and `read_only` read-only, each at its own
    path; `host_pids` shares the host's process ids (a bench walk judging the build slot's holder);
    `network` is `none` unless the act runs beside the walk's backends, on their internal network, and
    `name` names the container so a teardown can remove it whatever happened to its client.
    raises Refused on an unset declared name, a mount that is not an existing absolute directory, or a
    working directory under no writable mount. `home` is the container's HOME: the image's own, or a
    location of the act's own under a mount (DN-142.D30 (9) R8), never the caller's."""
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
    shm = os.statvfs(SHM)
    command = [docker, "run", "--rm", "--init", "--network", network, *(["--name", name] if name else []),
               "--workdir", str(cwd),
               "--shm-size", str(shm.f_blocks * shm.f_frsize),
               "--security-opt", "seccomp=unconfined",
               "--env", f"HOME={home}", "--env", f"{SYSTEM_ENV}={pin.image}"]
    if host_pids:
        command += ["--pid", "host"]
    for mount in resolved:
        command += ["--volume", f"{mount}:{mount}"]
    for mount in read_only:
        command += ["--volume", f"{mount}:{mount}:ro"]
    for name in names:
        command += ["--env", f"{name}={environ[name]}"]
    return [*command, pin.id, "unshare", "--user", f"--map-user={ACT_UID}", f"--map-group={ACT_UID}", "--", *argv]


def corpus_mounts(document: Path) -> list[Path]:
    """Every mount the corpus gates document names (malf_closure.corpus_mounts, the domain's reading)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import malf_closure

    try:
        every, _own = malf_closure.corpus_mounts(document, None)
    except malf_closure.ClosureError as unread:
        raise Refused(str(unread)) from unread
    return sorted(set(every))


def worktree_refusals(repositories: list[Path], mounts: list[Path]) -> list[str]:
    """One line per worktree entry of a repository in `repositories` whose location lies under none of
    `mounts`, or per repository git cannot name (DN-142.D30 (9) R9). An act that runs `git worktree prune`
    drops every entry whose directory it cannot see, so an act seeing another `/tmp` than the host's dropped
    a sibling lane's live candidate (measured 2026-10-19); and a host prune drops an entry the act made where
    the host cannot see it."""
    found = []
    for repository in repositories:
        common = subprocess.run(["git", "-C", str(repository), "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True, check=False)
        if common.returncode != 0:
            found.append(f"{repository} names no git repository ({common.stderr.strip() or 'no stderr'})")
            continue
        for gitdir in sorted(Path(common.stdout.strip(), "worktrees").glob("*/gitdir")):
            named = Path(gitdir.read_text().strip())
            location = (named if named.is_absolute() else (gitdir.parent / named).resolve()).parent
            if not any(location == mount or mount in location.parents for mount in mounts):
                found.append(f"the worktree entry {gitdir.parent.name} of {repository} names {location}, under "
                             f"no mount of the act")
    return found


def run(pin: Pin, argv: list[str], mounts: list[Path], names: list[str], docker: str = "docker", read_only: list[Path] | tuple = (), host_pids: bool = False,
        home: str = CONTAINER_HOME, backends: list[Backend] | tuple = (), attest: Path | None = None,
        worktree_repositories: list[Path] | tuple = ()) -> int:
    """The act in the environment; with `backends`, beside them on the walk's internal network, every
    backend and the network removed on every exit path. A repository of `worktree_repositories` holding
    an entry outside the mounts refuses the act first; `attest` receives the pin's two digests once the
    integrity check and every refusal of the container's command passed, just before it starts."""
    stray = worktree_refusals(list(worktree_repositories), [*mounts, *read_only])
    if stray:
        raise Refused("an act that runs git worktree commands sees every location the entries name, at the "
                      "host's path, or it can drop a sibling's live candidate (DN-142.D30 (9) R9): " + "; ".join(stray))
    load(pin, docker)

    def started(command: list[str]) -> int:
        if attest is not None:
            attest.write_text(json.dumps({"image": pin.image, "id": pin.id}, sort_keys=True) + "\n")
        return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode

    if not backends:
        return started(run_argv(pin, argv, mounts, names, Path.cwd(), docker,
                                read_only=read_only, host_pids=host_pids, home=home))
    with BackendWalk(docker, backends) as walk:
        return started(run_argv(pin, argv, mounts, names, Path.cwd(), docker,
                                read_only=read_only, host_pids=host_pids, home=home,
                                network=walk.network, name=walk.act))


# ── the walk's backends (DN-142.D30 (5) E2b, (7) R2) ───────────────────────────────────────────────

BACKEND_LABEL = "malf.build-environment.owner"
BACKEND_VARIABLE = "CODEROAST_BACKEND_"
BACKEND_NAME = re.compile(r"^[a-z][a-z0-9]*$")
BACKEND_MEMBERS = ("name", "image", "port", "health", "command", "environment")
PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
READY_DEADLINE_S = 90
READY_POLL_S = 0.1
LOG_TAIL_LINES = "40"
# Docker evaluates a healthcheck only on its interval, so the interval floors how late readiness is seen;
# the retry count is far above any real wait, the bounded deadline being the limit.
HEALTH_FLAGS = ("--health-interval", "250ms", "--health-timeout", "2s", "--health-retries", "600",
                "--health-start-period", "0s")


@dataclass(frozen=True)
class Backend:
    """One declared backend: a digest-pinned image serving `port`, ready when `health` (a shell command run
    inside it) exits 0; its address reaches an act as CODEROAST_BACKEND_<NAME>."""
    name: str
    image: str
    port: int
    health: str
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]

    @property
    def variable(self) -> str:
        return BACKEND_VARIABLE + self.name.upper()

    def document(self) -> dict:
        """The declaration as the step's key holds it, every member explicit."""
        return {"name": self.name, "image": self.image, "port": self.port, "health": self.health,
                "command": list(self.command), "environment": dict(self.environment)}


def parse_backends(declared: object, where: str) -> list[Backend]:
    """The backends of a `backends:` declaration. raises Refused naming each malformed entry."""
    if not isinstance(declared, list) or not declared:
        raise Refused(f"{where}: `backends:` is a non-empty list of {{{', '.join(BACKEND_MEMBERS)}}}, not {declared!r}")
    parsed, wrong = [], []
    for entry in declared:
        if not isinstance(entry, dict) or not {"name", "image", "port", "health"} <= set(entry) \
                or not set(entry) <= set(BACKEND_MEMBERS):
            wrong.append(f"{entry!r} is not a mapping of name, image, port, health and optionally command, environment")
            continue
        name, image, port, health = entry["name"], entry["image"], entry["port"], entry["health"]
        command, environment = entry.get("command", []), entry.get("environment", {})
        problems = []
        if not BACKEND_NAME.match(str(name)):
            problems.append(f"name {name!r} is not lowercase letters and digits")
        if not PINNED_IMAGE.match(str(image)):
            problems.append(f"image {image!r} is not pinned by digest (<ref>@sha256:<64 hex>)")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            problems.append(f"port {port!r} is not 1..65535")
        if not isinstance(health, str) or not health.strip():
            problems.append(f"health {health!r} is not a non-empty command")
        if not isinstance(command, list) or not all(isinstance(word, str) for word in command):
            problems.append(f"command {command!r} is not a list of strings")
        if not isinstance(environment, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                        for key, value in environment.items()):
            problems.append(f"environment {environment!r} is not a mapping of strings")
        if problems:
            wrong.append(f"{name!r}: " + ", ".join(problems))
            continue
        parsed.append(Backend(name, image, port, health, tuple(command), tuple(sorted(environment.items()))))
    names = [backend.name for backend in parsed]
    wrong += [f"the name {name!r} is declared twice" for name in sorted(set(names)) if names.count(name) > 1]
    if wrong:
        raise Refused(f"{where}: `backends:` is malformed: " + "; ".join(wrong))
    return parsed


def declared_backends(package_dir: Path, package: str, at_head: bool = True) -> list[Backend]:
    """The backends `package` declares in the packages.yml of the repository holding `package_dir`, read
    from HEAD (a walk's recorded commit) or from the working tree (the desk's `malf test`); [] for none."""
    import yaml

    top = subprocess.run(["git", "-C", str(package_dir), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=False)
    if top.returncode != 0:
        raise Refused(f"{package_dir} is in no git repository: {top.stderr.strip()}")
    root = Path(top.stdout.strip())
    if at_head:
        shown = subprocess.run(["git", "-C", str(root), "show", "HEAD:packages.yml"], capture_output=True,
                               text=True, check=False)
        if shown.returncode != 0:
            raise Refused(f"{root}: HEAD holds no packages.yml: {shown.stderr.strip()}")
        text = shown.stdout
    else:
        text = (root / "packages.yml").read_text()
    entry = ((yaml.safe_load(text) or {}).get("packages") or {}).get(package) or {}
    if "backends" not in entry:
        return []
    return parse_backends(entry["backends"], f"{root / 'packages.yml'} ({package})")


def backends_at(directory: Path) -> list[Backend]:
    """The backends the package at `directory` declares in its repository's working-tree packages.yml
    (the desk's `malf test`): the package whose `path:` is `directory` relative to the repository; [] for a
    directory no package names."""
    import yaml

    top = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=False)
    if top.returncode != 0 or not (Path(top.stdout.strip()) / "packages.yml").is_file():
        return []
    root = Path(top.stdout.strip())
    relative = directory.resolve().relative_to(root.resolve()).as_posix()
    packages = (yaml.safe_load((root / "packages.yml").read_text()) or {}).get("packages") or {}
    named = [name for name, entry in packages.items() if isinstance(entry, dict) and entry.get("path") == relative]
    return declared_backends(directory, named[0], at_head=False) if named else []


def read_backends(path: Path) -> list[Backend]:
    """The backends a walk's declaration file holds (`backends-of` wrote it)."""
    try:
        declared = json.loads(path.read_text())
    except (OSError, ValueError) as unread:
        raise Refused(f"the backend declaration {path} cannot be read: {unread!r}") from unread
    return parse_backends(declared, str(path)) if declared else []


def walk_backends(declarations: list[tuple[str, list[Backend]]]) -> list[Backend]:
    """The union of the backends the walk's steps declare ([(package, backends)]): one backend per name,
    refused when two steps declare one name differently."""
    held: dict[str, tuple[str, Backend]] = {}
    wrong = []
    for package, backends in declarations:
        for backend in backends:
            if backend.name in held and held[backend.name][1] != backend:
                wrong.append(f"{held[backend.name][0]} and {package} declare the backend {backend.name!r} differently")
            held.setdefault(backend.name, (package, backend))
    if wrong:
        raise Refused("the walk's backends disagree: " + "; ".join(wrong))
    return [held[name][1] for name in sorted(held)]


class Interrupted(BaseException):
    """A signal reached the launcher while it held backends: the walk unwinds through its teardown."""


def _interrupt(signum: int, _frame: object) -> None:
    raise Interrupted(f"signal {signum}")


def _owner_alive(text: str) -> bool:
    """Whether the owner pid a label names may still run: a dead one is reaped, an unreadable or
    unsignalable one (another account's) is left alone."""
    try:
        os.kill(int(text), 0)
    except ValueError:
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_backends(docker: str) -> list[str]:
    """Remove every backend container, act container and network a dead launcher left (labelled with its
    pid): the crashed launcher runs no teardown, so the next one does. Returns what was removed."""
    removed = []
    for kind, listing in (("container", ["ps", "--all"]), ("network", ["network", "ls"])):
        done = _docker(docker, *listing, "--filter", f"label={BACKEND_LABEL}", "--format",
                       f'{{{{.ID}}}} {{{{.Label "{BACKEND_LABEL}"}}}}')
        for line in done.stdout.splitlines() if done.returncode == 0 else []:
            ident, _sep, owner = line.partition(" ")
            if ident and not _owner_alive(owner):
                gone = _docker(docker, *(["rm", "--force", "--volumes"] if kind == "container" else ["network", "rm"]),
                               ident)
                if gone.returncode == 0:
                    removed.append(f"{kind} {ident}")
    return removed


class BackendWalk:
    """The walk's backends, up for the walk and gone after it (DN-142.D30 (5) E2b).

    Internal (a walk): ONE `docker network create --internal` named by the walk, each backend on it under
    its declared name, so an act attached to the network reaches each by `<name>:<port>` and nothing else;
    `addresses` maps each CODEROAST_BACKEND_<NAME> to that. Published (the desk's `malf test`): each backend
    publishes its port on the host's 127.0.0.1 at a port the daemon allocates, `addresses` naming it.
    Entering pulls each image by digest when the daemon lacks it, reaps a dead launcher's leftovers, starts
    each backend and waits for its healthcheck, bounded; any failure tears down what was started and
    raises Refused naming the image, the port and the container's log tail. Leaving removes the act
    container (`act`, when the act ran under that name), every backend and the network, on every exit path,
    a signal included."""

    def __init__(self, docker: str, backends: list[Backend] | tuple, publish: bool = False,
                 ready_deadline_s: float = READY_DEADLINE_S):
        self.docker, self.backends, self.publish, self.deadline = docker, list(backends), publish, ready_deadline_s
        token = f"{os.getpid()}-{secrets.token_hex(4)}"
        self.network = None if publish else f"malf-walk-{token}"
        self.act = None if publish else f"malf-act-{token}"
        self.token = token
        self.addresses: dict[str, str] = {}
        self.started: list[str] = []
        self.signals: dict[int, object] = {}

    def __enter__(self) -> "BackendWalk":
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            self.signals[signum] = signal.signal(signum, _interrupt)
        try:
            reap_backends(self.docker)
            for backend in self.backends:
                if inspected(self.docker, backend.image) is None:
                    pulled = _docker(self.docker, "pull", "--quiet", backend.image)
                    if pulled.returncode != 0:
                        raise Refused(f"the backend {backend.name}'s image {backend.image} is not held and its pull "
                                      f"failed: {pulled.stderr.strip()}")
            label = ["--label", f"{BACKEND_LABEL}={os.getpid()}"]
            if self.network:
                created = _docker(self.docker, "network", "create", "--internal", *label, self.network)
                if created.returncode != 0:
                    raise Refused(f"`docker network create --internal {self.network}` failed: {created.stderr.strip()}")
            for backend in self.backends:
                self._start(backend, label)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _start(self, backend: Backend, label: list[str]) -> None:
        name = f"malf-backend-{backend.name}-{self.token}"
        placement = (["--publish", f"127.0.0.1::{backend.port}"] if self.publish
                     else ["--network", self.network, "--network-alias", backend.name])
        environment = [word for key, value in backend.environment for word in ("--env", f"{key}={value}")]
        started = _docker(self.docker, "run", "--detach", "--quiet", "--name", name, *label, *placement,
                          "--health-cmd", backend.health, *HEALTH_FLAGS, *environment, backend.image,
                          *backend.command)
        if started.returncode != 0:
            raise Refused(f"the backend {backend.name} ({backend.image}) did not start: {started.stderr.strip()}")
        self.started.append(name)
        deadline = time.monotonic() + self.deadline
        state = "<never observed>"
        while time.monotonic() < deadline:
            seen = _docker(self.docker, "inspect", "--format",
                           "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", name)
            state = seen.stdout.strip() if seen.returncode == 0 else f"inspect failed: {seen.stderr.strip()}"
            if state == "running healthy":
                break
            if not state.startswith("running"):
                raise Refused(f"the backend {backend.name} ({backend.image}, port {backend.port}) stopped during "
                              f"startup (state: {state}); its last {LOG_TAIL_LINES} log lines:\n{self._tail(name)}")
            time.sleep(READY_POLL_S)
        else:
            raise Refused(f"the backend {backend.name} ({backend.image}, port {backend.port}) never reported healthy "
                          f"within {self.deadline:g} s (last state: {state}; healthcheck: {backend.health}); its last "
                          f"{LOG_TAIL_LINES} log lines:\n{self._tail(name)}")
        if self.publish:
            port = _docker(self.docker, "port", name, f"{backend.port}/tcp")
            host_port = port.stdout.strip().splitlines()[0].rpartition(":")[2] if port.returncode == 0 and port.stdout.strip() else ""
            if not host_port.isdigit():
                raise Refused(f"the backend {backend.name} ({backend.image}) publishes no host port for "
                              f"{backend.port}/tcp: {(port.stdout + port.stderr).strip()}")
            self.addresses[backend.variable] = f"127.0.0.1:{host_port}"
        else:
            self.addresses[backend.variable] = f"{backend.name}:{backend.port}"

    def _tail(self, name: str) -> str:
        logs = _docker(self.docker, "logs", "--tail", LOG_TAIL_LINES, name)
        return (logs.stdout + logs.stderr).strip()

    def __exit__(self, *_exc: object) -> None:
        for name in ([self.act] if self.act else []) + self.started:
            _docker(self.docker, "rm", "--force", "--volumes", name)
        self.started = []
        if self.network:
            _docker(self.docker, "network", "rm", self.network)
        for signum, previous in self.signals.items():
            signal.signal(signum, previous)
        self.signals = {}


def with_backends(docker: str, backends: list[Backend], argv: list[str]) -> int:
    """`argv` run on the host beside the backends published on 127.0.0.1, each address in its
    CODEROAST_BACKEND_<NAME> (the desk's `malf test`, the same start, barrier and teardown as a walk's)."""
    with BackendWalk(docker, backends, publish=True) as walk:
        return subprocess.run(argv, env={**os.environ, **walk.addresses}, check=False).returncode


# ── the definition's git mirrors ──────────────────────────────────────────────────────────────────

def mirror_path(url: str) -> str:
    """Where the image keeps the bare mirror of `url`: its scheme dropped, under MIRROR_ROOT."""
    return f"{MIRROR_ROOT}/{url.split('://', 1)[1]}"


def read_mirrors(path: Path = GIT_MIRRORS) -> dict[str, str]:
    """{upstream url: commit} of the definition's mirror rows. raises Refused on a malformed or repeated row."""
    try:
        lines = path.read_text().splitlines()
    except OSError as unread:
        raise Refused(f"the definition's mirror list {path} cannot be read: {unread!r}") from unread
    rows: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2 or "://" not in fields[0] or not COMMIT.match(fields[1]):
            raise Refused(f"{path}:{number} is not `<url> <40-hex commit>`: {line!r}")
        if fields[0] in rows:
            raise Refused(f"{path}:{number} repeats the mirror of {fields[0]}")
        rows[fields[0]] = fields[1]
    return rows


def lock_third_party(lock: Path = LOCK) -> list[str]:
    """Every third-party reference of the lock, `name/version#revision`: the ones carrying a revision."""
    try:
        document = json.loads(lock.read_text())
    except (OSError, ValueError) as unread:
        raise Refused(f"the lock {lock} cannot be read: {unread!r}") from unread
    references = [*document.get("requires", []), *document.get("build_requires", [])]
    return sorted(reference.split("%", 1)[0] for reference in references if "#" in reference)


def git_source(recipe: Path, version: str) -> tuple[str, str] | None:
    """(url, commit) the recipe at `recipe` fetches with git in source(), None when its source() does not.
    raises Refused when it does and its conandata names no URL and commit for `version`."""
    import yaml

    text = (recipe / "conanfile.py").read_text()
    sources = [ast.get_source_segment(text, node) or "" for node in ast.walk(ast.parse(text))
               if isinstance(node, ast.FunctionDef) and node.name == "source"]
    if not any(GIT_SOURCE.search(source) for source in sources):
        return None
    data = yaml.safe_load((recipe / "conandata.yml").read_text()) if (recipe / "conandata.yml").is_file() else None
    entry = ((data or {}).get("sources") or {}).get(version)
    if not isinstance(entry, dict) or not entry.get("url") or not COMMIT.match(str(entry.get("commit", ""))):
        raise Refused(f"{recipe} fetches with git in source(), and its conandata names no url and 40-hex commit "
                      f"for {version}: no mirror can serve it")
    return str(entry["url"]), str(entry["commit"])


def judge_mirrors(sourced: dict[str, tuple[str, str]], mirrors: dict[str, str]) -> list[str]:
    """Each disagreement between the git-sourced recipes ({reference: (url, commit)}) and the mirror rows."""
    findings = []
    for reference, (url, commit) in sorted(sourced.items()):
        if url not in mirrors:
            findings.append(f"{reference} fetches {url} at {commit} with git in source(), and the definition "
                            f"mirrors no such URL ({GIT_MIRRORS.name}): the walk has no network to fetch it")
        elif mirrors[url] != commit:
            findings.append(f"{reference} fetches {url} at {commit}, and the definition's row names {mirrors[url]}")
    fetched = {url for url, _commit in sourced.values()}
    findings += [f"the mirror row {url} {commit} serves no git-sourced recipe of the lock" for url, commit in
                 sorted(mirrors.items()) if url not in fetched]
    return findings


def conan_homes(given: list[Path]) -> list[Path]:
    """The homes searched for a recipe: those given, else CONAN_HOME and each home directly under it."""
    if given:
        return given
    base = Path(os.environ.get("CONAN_HOME") or Path.home() / ".conan2")
    return [base, *sorted(child for child in base.iterdir() if (child / "p").is_dir())] if base.is_dir() else []


def recipe_folder(reference: str, homes: list[Path]) -> Path | None:
    """The export folder of `reference` in the first home holding it."""
    for home in homes:
        done = subprocess.run(["conan", "cache", "path", reference], capture_output=True, text=True, check=False,
                              env={**os.environ, "CONAN_HOME": str(home)})
        if done.returncode == 0 and Path(done.stdout.strip(), "conanfile.py").is_file():
            return Path(done.stdout.strip())
    return None


def definition_check(homes: list[Path], lock: Path = LOCK, mirrors_path: Path = GIT_MIRRORS) -> list[str]:
    """Each finding of the definition against the lock (definition-check)."""
    mirrors = read_mirrors(mirrors_path)
    sourced: dict[str, tuple[str, str]] = {}
    findings = []
    for reference in lock_third_party(lock):
        folder = recipe_folder(reference, homes)
        if folder is None:
            findings.append(f"{reference} is in no conan home searched ({', '.join(map(str, homes)) or 'none'}): "
                            "its source() cannot be judged")
            continue
        try:
            fetched = git_source(folder, reference.split("#", 1)[0].split("/", 1)[1])
        except Refused as refused:
            findings.append(str(refused))
            continue
        if fetched is not None:
            sourced[reference] = fetched
    return findings + judge_mirrors(sourced, mirrors)


MIRROR_PROBE = """from conan import ConanFile
from conan.tools.scm import Git


class MirrorProbe(ConanFile):
    name = "mirror-probe"
    version = "0"

    def source(self):
        Git(self).fetch_commit(url={url!r}, commit={commit!r})
"""


def mirror_check(pin: Pin, docker: str) -> list[str]:
    """Each mirror row the pinned image fails to serve to conan's own fetch under `--network none`."""
    load(pin, docker)
    findings = []
    for url, commit in sorted(read_mirrors().items()):
        with tempfile.TemporaryDirectory(prefix="build_environment.mirror.") as held:
            scratch = Path(held)
            (scratch / "conanfile.py").write_text(MIRROR_PROBE.format(url=url, commit=commit))
            script = (f"conan source {scratch} >{scratch}/source.log 2>&1 || {{ cat {scratch}/source.log; exit 1; }}; "
                      f"git -C {scratch} rev-parse HEAD")
            done = subprocess.run(run_argv(pin, ["sh", "-c", script], [scratch], ["CONAN_HOME"], scratch,
                                           docker=docker, environ={"CONAN_HOME": f"{scratch}/home"}),
                                  capture_output=True, text=True, check=False)
            head = done.stdout.strip().splitlines()[-1:] if done.stdout.strip() else []
            if done.returncode != 0 or head != [commit]:
                findings.append(f"{url} at {commit}: exit {done.returncode}, HEAD {head or 'none'}: "
                                f"{(done.stdout + done.stderr).strip()[-1500:]}")
            else:
                print(f"  ok   {url} served at {commit} under --network none")
    return findings


# ── selftest────────────────────────────────────────────────────────────────────────────────────

PROBE_SOURCE = r"""
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/statvfs.h>
extern char **environ;
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "env") == 0) {
        for (char **entry = environ; *entry; ++entry) puts(*entry);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "shm") == 0) {
        struct statvfs fs;
        if (statvfs("/dev/shm", &fs) != 0) return 1;
        printf("%llu\n", (unsigned long long)fs.f_blocks * fs.f_frsize);
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


def _base_image() -> str:
    """The definition's own digest-pinned base image, already held by the daemon that built the pin."""
    found = BASE_IMAGE.search((DEFINITION / "Dockerfile").read_text())
    assert found, "the definition's Dockerfile names no digest-pinned base image"
    return found.group(1)


def _throwaway(base: Path, docker: str) -> tuple[Pin, Path]:
    """A throwaway image (the definition's base image, whose util-linux and tar the act's user arms run,
    plus one static probe), built with the pin's flags, saved, and the pin its own daemon reports: (the
    pin, the archive's directory)."""
    context = base / "context"
    context.mkdir()
    (context / "probe.c").write_text(PROBE_SOURCE)
    subprocess.run(["cc", "-static", "-O1", "-o", str(context / "probe"), str(context / "probe.c")], check=True)
    (context / "Dockerfile").write_text(f"FROM {_base_image()}\nCOPY probe /probe\nENTRYPOINT []\n")
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


NETWORK_PROBE_SOURCE = r"""
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "serve") == 0) {
        int sock = socket(AF_INET, SOCK_STREAM, 0), one = 1;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        struct sockaddr_in at;
        memset(&at, 0, sizeof at);
        at.sin_family = AF_INET;
        at.sin_port = htons(atoi(argv[2]));
        at.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(sock, (struct sockaddr *)&at, sizeof at) != 0 || listen(sock, 16) != 0) { perror("serve"); return 1; }
        for (;;) { int peer = accept(sock, NULL, NULL); if (peer >= 0) close(peer); }
    }
    if (argc == 3 && strcmp(argv[1], "unix") == 0) {
        int sock = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un at;
        memset(&at, 0, sizeof at);
        at.sun_family = AF_UNIX;
        strncpy(at.sun_path, argv[2], sizeof at.sun_path - 1);
        if (connect(sock, (struct sockaddr *)&at, sizeof at) == 0) { printf("reached %s\n", argv[2]); return 0; }
        printf("unreachable %s: %s\n", argv[2], strerror(errno));
        return 1;
    }
    if (argc >= 3 && (strcmp(argv[1], "connect") == 0 || strcmp(argv[1], "resolve") == 0)) {
        struct addrinfo hints, *found;
        memset(&hints, 0, sizeof hints);
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_STREAM;
        int failed = getaddrinfo(argv[2], argc > 3 ? argv[3] : "1", &hints, &found);
        if (failed) { printf("unresolved %s: %s\n", argv[2], gai_strerror(failed)); return 2; }
        if (strcmp(argv[1], "resolve") == 0) { printf("resolved %s\n", argv[2]); return 0; }
        int sock = socket(AF_INET, SOCK_STREAM, 0);
        struct timeval bound = {2, 0};
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &bound, sizeof bound);
        if (connect(sock, found->ai_addr, found->ai_addrlen) == 0) { printf("reached %s:%s\n", argv[2], argv[3]); return 0; }
        printf("unreachable %s:%s: %s\n", argv[2], argv[3], strerror(errno));
        return 1;
    }
    return 3;
}
"""
def _network_throwaway(base: Path, docker: str) -> tuple[Pin, str, Path]:
    """A throwaway image for the backend arms: the definition's own base image (a shell for the
    healthcheck, the resolver for names) plus a network probe, built with the pin's flags and never saved:
    (its pin, its `<repository>@<manifest digest>` reference, the probe built for the host)."""
    context = base / "network"
    context.mkdir()
    (context / "probe.c").write_text(NETWORK_PROBE_SOURCE)
    subprocess.run(["cc", "-O1", "-o", str(context / "probe"), str(context / "probe.c")], check=True)
    (context / "Dockerfile").write_text(f"FROM {_base_image()}\nCOPY probe /probe\n")
    tag = f"malf-build-environment-network:{os.getpid()}"
    subprocess.run([docker, "buildx", "build", "--platform", "linux/amd64", "--provenance=false", "--sbom=false",
                    "--load", "--quiet", "-t", tag, str(context)], check=True, capture_output=True)
    seen = inspected(docker, tag)
    assert seen is not None, f"the throwaway image {tag} was built and is not inspectable"
    return Pin(seen[1], seen[0], "unsaved.tar", 1, "0" * 64), f"malf-build-environment-network@{seen[0]}", context / "probe"


def _backend_arms(arm, refusal, base: Path, docker: str) -> None:
    """The walk's backends (DN-142.D30 (5) E2b): reached by name and nothing else, torn down, reaped."""
    import socket

    pin, reference, host_probe = _network_throwaway(base, docker)
    mount = base / "network-act"
    mount.mkdir()
    probe = Backend("probe", reference, 7000, "/probe connect 127.0.0.1 7000", ("/probe", "serve", "7000"), ())
    socket_path = re.sub(r"^unix://", "", os.environ.get("DOCKER_HOST") or f"unix:///run/user/{os.getuid()}/docker.sock")
    try:
        with socket.create_server(("0.0.0.0", 0)) as listener, \
                BackendWalk(docker, [Backend("published", reference, 7000, "/probe connect 127.0.0.1 7000",
                                             ("/probe", "serve", "7000"), ())], publish=True) as published:
            host_port = int(listener.getsockname()[1])
            loopback_port = published.addresses["CODEROAST_BACKEND_PUBLISHED"].rpartition(":")[2]
            arm("the host reaches the port a backend publishes on its loopback, and its docker socket (the controls)",
                (subprocess.run([str(host_probe), "connect", "127.0.0.1", loopback_port], capture_output=True).returncode,
                 subprocess.run([str(host_probe), "unix", socket_path], capture_output=True).returncode), (0, 0))
            with BackendWalk(docker, [probe]) as walk:
                gateway = _docker(docker, "network", "inspect", walk.network, "--format",
                                  "{{range .IPAM.Config}}{{.Gateway}}{{end}}").stdout.strip()

                def act(*words: str) -> tuple[int, str]:
                    done = subprocess.run(run_argv(pin, ["/probe", *words], [mount], [], mount, docker=docker,
                                                   environ={}, network=walk.network),
                                          capture_output=True, text=True, check=False)
                    return done.returncode, done.stdout.strip()

                arm("a walk's act reaches its backend by name, at the address CODEROAST_BACKEND_PROBE names",
                    (walk.addresses, act("connect", "probe", "7000")[0]), ({"CODEROAST_BACKEND_PROBE": "probe:7000"}, 0))
                arm("RED: a walk's act reaches no external address", act("connect", "1.1.1.1", "53")[0], 1)
                arm("RED: a walk's act reaches no listener of the host through the network's gateway",
                    (bool(gateway), act("connect", gateway, str(host_port))[0]), (True, 1))
                arm("RED: a walk's act reaches no port the host publishes on its loopback, by the gateway or its own",
                    (act("connect", gateway, loopback_port)[0], act("connect", "127.0.0.1", loopback_port)[0]), (1, 1))
                arm("RED: a walk's act resolves its backend's name and no name outside the network",
                    (act("resolve", "probe")[0], act("resolve", "example.com")[0], act("resolve", "host.docker.internal")[0]),
                    (0, 2, 2))
                arm("RED: a walk's act holds no Docker socket: the daemon's socket the host reaches is absent inside",
                    act("unix", socket_path)[0], 1)
                held = _docker(docker, "ps", "--filter", f"label={BACKEND_LABEL}={os.getpid()}", "--format",
                               "{{.Names}}").stdout.split()
            left = [name for name in _docker(docker, "ps", "--all", "--format", "{{.Names}}").stdout.split()
                    if walk.token in name]
            arm("the walk's backends and its network are removed when it ends",
                (any(walk.token in name for name in held), left,
                 _docker(docker, "network", "inspect", walk.network).returncode != 0), (True, [], True))
        said = refusal(lambda: BackendWalk(docker, [Backend("sick", reference, 7000, "/probe connect 127.0.0.1 7001",
                                                            ("/probe", "serve", "7000"), ())],
                                           ready_deadline_s=3).__enter__())
        arm("RED: a backend whose healthcheck never passes is refused within its bound, naming the image, the port "
            "and the log tail", (f"({reference}, port 7000) never reported healthy within 3 s" in said,
                                 "log lines" in said), (True, True))
        dead = subprocess.Popen(["true"])
        dead.wait()
        _docker(docker, "network", "create", "--internal", "--label", f"{BACKEND_LABEL}={dead.pid}",
                f"malf-walk-reap-{dead.pid}")
        _docker(docker, "run", "--detach", "--label", f"{BACKEND_LABEL}={dead.pid}", "--name",
                f"malf-backend-reap-{dead.pid}", reference, "/probe", "serve", "7000")
        reaped = reap_backends(docker)
        arm("a dead launcher's leftover backend and network are reaped by the next walk",
            (sum(line.startswith("container ") for line in reaped) >= 1, sum(line.startswith("network ") for line in reaped) >= 1,
             _docker(docker, "network", "inspect", f"malf-walk-reap-{dead.pid}").returncode != 0), (True, True, True))
        script = base / "desk.sh"
        script.write_text(f'#!/bin/sh\nexec {host_probe} connect "${{CODEROAST_BACKEND_PROBE%:*}}" "${{CODEROAST_BACKEND_PROBE#*:}}"\n')
        script.chmod(0o755)
        arm("the desk's with-backends hands the command each backend published on 127.0.0.1, by its variable",
            with_backends(docker, [probe], [str(script)]), 0)
        arm("RED: a declaration whose image is not pinned by digest is refused",
            refusal(lambda: parse_backends([{"name": "redis", "image": "redis:7", "port": 6379, "health": "true"}],
                                           "packages.yml")).endswith("image 'redis:7' is not pinned by digest "
                                                                     "(<ref>@sha256:<64 hex>)"), True)
        arm("RED: two steps declaring one backend name differently are refused, naming both",
            refusal(lambda: walk_backends([("one", [probe]), ("two", [Backend("probe", reference, 7001, "true", (), ())])]))
            .endswith("one and two declare the backend 'probe' differently"), True)
    finally:
        _docker(docker, "rmi", "--force", reference)


def _act_user_arms(arm, pin: Pin, mount: Path, docker: str) -> None:
    """The act's user (DN-142.D30 (8) R4, row E2c): uid 1000 with no capability, writing the mounts by owner
    permission, refused where the host account is refused, restoring no archive owner, able to nest the
    namespaces the declared-unconfined tests create."""
    import tarfile

    def act(script: str) -> subprocess.CompletedProcess:
        return subprocess.run(run_argv(pin, ["sh", "-c", script], [mount], [], mount, docker=docker, environ={}),
                              capture_output=True, text=True, check=False)

    who = act("id -u; grep '^CapEff:' /proc/self/status")
    arm("RED: the act is uid 1000 holding no capability", (who.returncode, who.stdout.split()),
        (0, ["1000", "CapEff:", "0000000000000000"]))
    made = act("touch made-by-the-act")
    arm("a file the act creates in a mount is owned, on the host, by the account that runs the launcher",
        (made.returncode, (mount / "made-by-the-act").stat().st_uid), (0, os.getuid()))
    locked = act("mkdir locked && chmod 0500 locked && { touch locked/written; echo $?; }; chmod 0700 locked")
    arm("RED: a directory of mode 0500 the act creates refuses the act's write",
        (locked.stdout.strip() != "0", (mount / "locked" / "written").exists()), (True, False))
    member = tarfile.TarInfo("owned-by-another-uid")
    member.uid = member.gid = 4242
    with tarfile.open(mount / "foreign.tar", "w") as archive:
        archive.addfile(member, None)
    (mount / "extracted").mkdir()
    extracted = act("tar -xf foreign.tar -C extracted")
    owner = (mount / "extracted" / "owned-by-another-uid").stat().st_uid
    act("rm -rf extracted")
    arm("RED: an archive member owned by another uid extracts as the act's own uid, never as that uid",
        (extracted.returncode, owner), (0, os.getuid()))
    nested = act("unshare -Urpfm --mount-proc sh -c 'mount -t tmpfs none /mnt && grep -c \" /mnt tmpfs \" /proc/self/mounts'")
    arm("RED: a nested unshare -Urpfm --mount-proc mounts a tmpfs (DN-142.D29 (ix)'s namespace tests)",
        (nested.returncode, nested.stdout.strip()), (0, "1"))


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

    with tempfile.TemporaryDirectory(prefix="build_environment.definition.") as held:
        recipes = Path(held)
        upstream, commit = "https://git.example.org/config.git", "1" * 40
        for name, source in (("fetched", "        Git(self).fetch_commit(**self.conan_data['sources'][self.version])\n"),
                             ("cloned", "        self.run('git clone https://git.example.org/other.git src')\n"),
                             ("downloaded", "        get(self, **self.conan_data['sources'][self.version])\n")):
            (recipes / name).mkdir()
            (recipes / name / "conanfile.py").write_text(
                "from conan import ConanFile\n\n\nclass Recipe(ConanFile):\n    def build(self):\n"
                "        Git(self)\n\n    def source(self):\n" + source)
            (recipes / name / "conandata.yml").write_text(f"sources:\n  '1.0':\n    url: {upstream}\n"
                                                          f"    commit: '{commit}'\n")
        arm("a recipe whose source() calls conan's Git helper is git-sourced, at its conandata url and commit",
            git_source(recipes / "fetched", "1.0"), (upstream, commit))
        arm("a recipe whose source() runs git clone itself is git-sourced",
            git_source(recipes / "cloned", "1.0"), (upstream, commit))
        arm("a recipe whose source() downloads is not git-sourced, though another method names Git",
            git_source(recipes / "downloaded", "1.0"), None)
        arm("RED: a git-sourced recipe whose conandata names no commit for its version is refused",
            refusal(lambda: git_source(recipes / "fetched", "2.0")).endswith("no mirror can serve it"), True)
        arm("RED: a git-sourced recipe of the lock whose URL the definition does not mirror is named and refused",
            judge_mirrors({"config/1.0#abc": (upstream, commit)}, {}),
            [f"config/1.0#abc fetches {upstream} at {commit} with git in source(), and the definition mirrors no "
             f"such URL ({GIT_MIRRORS.name}): the walk has no network to fetch it"])
        arm("RED: a mirror row at another commit than the recipe's is refused, naming both",
            judge_mirrors({"config/1.0#abc": (upstream, commit)}, {upstream: "2" * 40}),
            [f"config/1.0#abc fetches {upstream} at {commit}, and the definition's row names {'2' * 40}"])
        arm("RED: a mirror row no recipe fetches is refused as stale",
            judge_mirrors({}, {upstream: commit}), [f"the mirror row {upstream} {commit} serves no git-sourced recipe "
                                                    "of the lock"])
        arm("a git-sourced recipe mirrored at its commit passes", judge_mirrors({"c#a": (upstream, commit)},
                                                                                {upstream: commit}), [])
        (recipes / "mirrors.txt").write_text(f"# comment\n{upstream} {commit}\n{upstream} {commit}\n")
        arm("RED: a mirror list repeating a URL is refused",
            refusal(lambda: read_mirrors(recipes / "mirrors.txt")).endswith(f"repeats the mirror of {upstream}"), True)
        arm("the mirror of a URL is its path without the scheme, under the image's mirror root",
            mirror_path(upstream), f"{MIRROR_ROOT}/git.example.org/config.git")

    with tempfile.TemporaryDirectory(prefix="build_environment.worktrees.") as held:
        base = Path(held)
        repository, inside, outside = base / "repository", base / "mounted" / "candidate", base / "elsewhere"
        quiet = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        for words in (["init", "-q", "-b", "main", str(repository)],
                      ["-C", str(repository), "-c", "user.name=f", "-c", "user.email=f@f", "commit", "-q",
                       "--allow-empty", "-m", "fixture"],
                      ["-C", str(repository), "worktree", "add", "-q", "--detach", str(inside)]):
            subprocess.run(["git", *words], check=True, capture_output=True, env=quiet)
        arm("a repository whose every worktree entry lies under a mount refuses nothing (DN-142.D30 (9) R9)",
            worktree_refusals([repository], [repository, base / "mounted"]), [])
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q", "--detach", str(outside)],
                       check=True, capture_output=True, env=quiet)
        refused_entry = refusal(lambda: run(Pin("sha256:" + "0" * 64, "sha256:" + "0" * 64, "absent.tar", 1, "0" * 64),
                                            ["true"], [repository, base / "mounted"], [], docker="false",
                                            worktree_repositories=[repository]))
        arm("RED: a worktree-making act is refused while a mounted repository holds an entry naming a path outside "
            "its mounts, the entry and the path named, before any image is loaded (DN-142.D30 (9) R9)",
            (f"names {outside}, under no mount of the act" in refused_entry, "elsewhere" in refused_entry,
             "absent.tar" in refused_entry), (True, True, False))
        arm("RED: a directory git cannot name is refused, never read as a repository with no entry",
            worktree_refusals([base / "mounted"], [base])[0].startswith(f"{base / 'mounted'} names no git repository"),
            True)

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
            attested, unattested = base / "attested.json", base / "unattested.json"
            mounted_for_attest = base / "attest-mount"
            mounted_for_attest.mkdir()
            previous = Path.cwd()
            os.chdir(mounted_for_attest)
            try:
                ran_attested = run(pin, ["/probe"], [mounted_for_attest], [], docker, attest=attested)
                refusal(lambda: run(Pin("sha256:" + "1" * 64, pin.id, pin.archive_name, pin.archive_bytes,
                                        pin.archive_sha256), ["/probe"], [mounted_for_attest], [], docker,
                                    attest=unattested))
                refusal(lambda: run(pin, ["/probe"], [base / "mounted"], [], docker, attest=unattested))
            finally:
                os.chdir(previous)
            arm("an act the integrity check passed is attested with the pin's two digests; one the check refused, "
                "and one whose working directory lies under no mount, are not (DN-142.D30 (9) E3: the key's system "
                "member comes from this file)",
                (ran_attested, json.loads(attested.read_text()) if attested.is_file() else None, unattested.exists()),
                (0, {"image": pin.image, "id": pin.id}, False))
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
            sized = subprocess.run(run_argv(pin, ["/probe", "shm"], [mount], [], mount, docker=docker),
                                   capture_output=True, text=True, check=False)
            caller = os.statvfs(SHM)
            arm("RED: the container's /dev/shm is the caller's size, never Docker's 64 MiB default",
                (sized.returncode, sized.stdout.strip()), (0, str(caller.f_blocks * caller.f_frsize)))
            arm("RED: a declared name unset in the caller is refused, never run with a default",
                refusal(lambda: run_argv(pin, ["/probe"], [mount], ["UNSET_NAME"], mount, environ={})).startswith(
                    "the act declares UNSET_NAME, unset in its caller"), True)
            arm("RED: a working directory under no mount is refused",
                refusal(lambda: run_argv(pin, ["/probe"], [mount], [], base, environ={})).startswith(
                    f"the working directory {base} lies under no mount"), True)
            _act_user_arms(arm, pin, mount, docker)
            started = run_argv(pin, ["/probe"], [mount], [], mount, environ={})
            arm("every act container lifts the default seccomp profile and starts its argv as uid 1000 in a nested "
                "user namespace", ("seccomp=unconfined" in started, started[started.index(pin.id) + 1:]),
                (True, ["unshare", "--user", "--map-user=1000", "--map-group=1000", "--", "/probe"]))
            (base / "pin.json").write_text(json.dumps({"image": pin.image, "id": "latest", "archive": {
                "name": pin.archive_name, "bytes": pin.archive_bytes, "sha256": pin.archive_sha256}}))
            arm("RED: a pin naming its id by anything but a digest is refused",
                "id is 'latest'" in refusal(lambda: read_pin(base / "pin.json")), True)
        finally:
            subprocess.run([docker, "rmi", "--force", pin.id], capture_output=True, check=False)
        _backend_arms(arm, refusal, base, docker)
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
        options.add_argument("--mount", action="append", default=[], type=Path)
        options.add_argument("--read-only-mount", action="append", default=[], type=Path)
        options.add_argument("--corpus-gates", type=Path)
        options.add_argument("--host-pids", action="store_true")
        options.add_argument("--home", default=CONTAINER_HOME)
        options.add_argument("--env", action="append", default=[])
        options.add_argument("--backends", type=Path)
        options.add_argument("--attest", type=Path)
        options.add_argument("--worktree-repository", action="append", default=[], type=Path)
        given = options.parse_args(argv[1:split])
        if not argv[split + 1:]:
            print("build_environment.py run: the command after -- is empty", file=sys.stderr)
            return REFUSED
        try:
            read_only = [*given.read_only_mount, *(corpus_mounts(given.corpus_gates) if given.corpus_gates else [])]
            return run(read_pin(), argv[split + 1:], given.mount, given.env, docker,
                       read_only, given.host_pids, given.home,
                       read_backends(given.backends) if given.backends else (), given.attest,
                       given.worktree_repository)
        except Refused as refused:
            print(f"build_environment: REFUSED — {refused}; the act did not run (DN-142.D30, OPS-1.S27)", file=sys.stderr)
            return REFUSED
        except Interrupted as interrupted:
            print(f"build_environment: INTERRUPTED by {interrupted}; the walk's backends and network were removed",
                  file=sys.stderr)
            return REFUSED
    if argv[:1] == ["with-backends"]:
        if "--" not in argv or argv[1:2] != ["--backends"] or argv.index("--") != 3 or not argv[4:]:
            print("build_environment.py with-backends: --backends <file> -- <argv>...", file=sys.stderr)
            return REFUSED
        try:
            return with_backends(docker, read_backends(Path(argv[2])), argv[4:])
        except Refused as refused:
            print(f"build_environment: REFUSED — {refused}; the command did not run (DN-142.D30 (5) E2b)", file=sys.stderr)
            return REFUSED
        except Interrupted as interrupted:
            print(f"build_environment: INTERRUPTED by {interrupted}; the backends were removed", file=sys.stderr)
            return REFUSED
    parser = argparse.ArgumentParser(prog="build_environment.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("check", "load", "mirror-check", "selftest"):
        verbs.add_parser(verb)
    verbs.add_parser("definition-check").add_argument("--conan-home", action="append", default=[], type=Path)
    declared = verbs.add_parser("backends")
    declared.add_argument("--worktree", action="store_true")
    declared.add_argument("--variables", action="store_true")
    declared.add_argument("--parameters")
    declared.add_argument("steps", nargs="+")
    verbs.add_parser("backends-at").add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.verb == "selftest":
        return selftest()
    try:
        if args.verb == "backends":
            if len(args.steps) % 2:
                print("build_environment.py backends: name <package dir> <package> pairs", file=sys.stderr)
                return REFUSED
            pairs = list(zip(args.steps[0::2], args.steps[1::2]))
            union = walk_backends([(package, declared_backends(Path(folder), package, not args.worktree))
                                   for folder, package in pairs])
            if args.variables:
                for backend in union:
                    print(f"{backend.variable}={backend.name}:{backend.port}")
            elif args.parameters is not None:
                document = json.loads(args.parameters)
                document["env"] = {**document["env"], **{backend.variable: f"{backend.name}:{backend.port}"
                                                         for backend in union}}
                print(json.dumps(document, sort_keys=True, separators=(",", ":")))
            else:
                print(json.dumps([backend.document() for backend in union], sort_keys=True))
            return 0
        if args.verb == "backends-at":
            print(json.dumps([backend.document() for backend in backends_at(args.directory)], sort_keys=True))
            return 0
        if args.verb in ("definition-check", "mirror-check"):
            findings = (definition_check(conan_homes(args.conan_home)) if args.verb == "definition-check"
                        else mirror_check(read_pin(), docker))
            for finding in findings:
                print(f"build_environment: {args.verb}: {finding}", file=sys.stderr)
            judged = (f" over {len(lock_third_party())} third-party reference(s) of {LOCK.name}"
                      if args.verb == "definition-check" else f" over {len(read_mirrors())} mirror row(s)")
            print(f"build_environment {args.verb}: {len(findings)} finding(s){judged}")
            return 1 if findings else 0
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
