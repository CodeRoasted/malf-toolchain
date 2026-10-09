#!/usr/bin/env python3
"""A step's key, an output's identity, and the local store that keeps them: the library behind `malf store-create`.

DN-142.D2 names the key: SHA-256 over the canonical JSON of every input of a step, each input
identified by bytes or git objects, never by a run, a time, a path or a build width. DN-142.D3 names
an output's identity: SHA-256 over the canonical JSON of its decoded file tree, `conanmanifest.txt`
at the tree's root excluded (its first line is a creation timestamp). DN-142.D6 names the store:
content-addressed objects, one create-only record per key, and in phase 1 (ROADMAP N358) a second
result under an existing key is a COMPARE, a differing one recorded as a mismatch event and failed.

    artefact_store.py key <document.json>
        the key of an input document, one line of 64 hex
    artefact_store.py tree <folder>
        the content digest of a file tree, one line of 64 hex
    artefact_store.py conan-step <store> <conan home> <create graph.json> <package> <profile name>
                                 <malf dir> <toolchain.json>
        the record of one `conan create` step, from the graph its `--format=json` printed: stored
        when the key is new (exit 0), compared when it is not (exit 0 on a match, 1 on a mismatch)
    artefact_store.py toolchain <compiler root> <out.json>
        the measured toolchain member, written once per run (hashing the compiler tree is the
        expensive part of a key)

The store is a directory: `objects/<sha256>` immutable blobs, `records/<key>.json` one per key,
`mismatches/<key>.<content>.json` one per differing rebuild. It stands in for the remote store
until its transport and its writer credential are ruled (DN-142.D6, R1 to R5).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "conanmanifest.txt"
HASH_CHUNK = 1 << 20
NAMED_FILES = 12


class KeyError_(ValueError):
    """An input document that cannot be keyed: a float, a non-string key, an absolute path."""


def _check(value: object, where: str) -> None:
    """pre: `value` is JSON made of objects with string keys, arrays, strings, integers, booleans
    and null, and no string is an absolute path (DN-142.D2: a path is never a key input)."""
    if isinstance(value, dict):
        for name, item in value.items():
            if not isinstance(name, str):
                raise KeyError_(f"{where}: a key member's name is not a string: {name!r}")
            _check(item, f"{where}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, f"{where}[{index}]")
    elif isinstance(value, float):
        raise KeyError_(f"{where}: a float is not a key input (integers only): {value!r}")
    elif isinstance(value, str):
        if value.startswith("/") or (len(value) > 2 and value[1] == ":" and value[2] in "/\\"):
            raise KeyError_(f"{where}: an absolute path is never a key input: {value!r}")
    elif value is not None and not isinstance(value, (bool, int)):
        raise KeyError_(f"{where}: {type(value).__name__} is not JSON")


def canonical_json(value: object) -> bytes:
    """Sorted keys, no insignificant whitespace, UTF-8, integers only (DN-142.D2)."""
    _check(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def key_of(document: object) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def tree_entries(folder: Path, exclude_root_manifest: bool = True) -> list[list[str]]:
    """The sorted `[relative path, mode, SHA-256]` of every file and symlink under `folder`.

    post: mode is `file`, `executable` (any execute bit) or `symlink` (its SHA-256 is that of the
    link's target text, never of what it points at); a directory contributes only its contents.
    """
    entries = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        base = Path(root)
        for name in sorted(files) + sorted(entry for entry in dirs if (base / entry).is_symlink()):
            path = base / name
            relative = path.relative_to(folder).as_posix()
            if exclude_root_manifest and relative == MANIFEST:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entries.append([relative, "symlink",
                                hashlib.sha256(os.readlink(path).encode()).hexdigest()])
            elif stat.S_ISREG(info.st_mode):
                mode = "executable" if info.st_mode & 0o111 else "file"
                entries.append([relative, mode, file_sha256(path)])
            else:
                raise KeyError_(f"{path}: neither a regular file nor a symlink")
    return sorted(entries)


def tree_digest(folder: Path) -> tuple[str, list[list[str]]]:
    """DN-142.D3's content digest of `folder`, and the entries it was computed over."""
    if not folder.is_dir():
        raise KeyError_(f"{folder}: not a directory")
    entries = tree_entries(folder)
    return hashlib.sha256(canonical_json(entries)).hexdigest(), entries


def _run(argv: list[str], env: dict[str, str] | None = None) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        sys.exit(f"artefact_store: `{' '.join(argv)}` failed:\n{done.stderr.strip()}")
    return done.stdout


def worktree_tree_id(repository: Path) -> str:
    """The git tree id of `repository`'s TRACKED files as they are on disk, read through a
    private index so the shared one is never written (a definition is the code that ran)."""
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _run(["git", "-C", str(repository), "read-tree", "HEAD"], env)
        _run(["git", "-C", str(repository), "add", "--update", "--", "."], env)
        return _run(["git", "-C", str(repository), "write-tree"], env).strip()


def measure_toolchain(compiler_root: Path) -> dict[str, object]:
    """DN-142.D2's `toolchain` member, measured in the job: the compiler tree's digest, and
    cmake, ninja and conan by version and by the digest of the executable that runs."""
    compiler, _ = tree_digest(compiler_root)
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        probe = Path(scratch) / "probe.cmake"
        probe.write_text('message("${CMAKE_COMMAND}")\n')
        cmake_binary = Path(subprocess.run(["cmake", "-P", str(probe)], capture_output=True,
                                           text=True, check=True).stderr.strip())
    tools: dict[str, object] = {"compiler": compiler}
    for name, executable, version_argv in (
            ("cmake", cmake_binary, ["cmake", "--version"]),
            ("ninja", Path(_run(["which", "ninja"]).strip()), ["ninja", "--version"]),
            ("conan", Path(_run(["which", "conan"]).strip()), ["conan", "--version"])):
        tools[name] = {"version": _run(version_argv).splitlines()[0].strip(),
                       "executable": file_sha256(executable.resolve())}
    return tools


def system_member() -> str:
    """DN-142.D2's `system` member: the sorted installed package list of the host."""
    listing = _run(["dpkg-query", "-W", "-f", "${Package}=${Version}\n"])
    return hashlib.sha256("\n".join(sorted(listing.splitlines())).encode()).hexdigest()


def profile_member(home: Path, profile: str, malf_dir: Path) -> str:
    """DN-142.D2's `profile` member: `conan profile show` at the step's profile, with the two
    absolute prefixes it can carry (the conan home, malf's checkout) replaced by fixed tokens;
    what those files contain is keyed by `definition` (malf's tree) and nothing else."""
    shown = _run(["conan", "profile", "show", f"--profile:host={profile}",
                  f"--profile:build={profile}"], {**os.environ, "CONAN_HOME": str(home)})
    for prefix, token in ((str(home), "<conan-home>"), (str(malf_dir), "<malf>")):
        shown = shown.replace(prefix, token)
    return hashlib.sha256("\n".join(line.rstrip() for line in shown.splitlines()).encode()).hexdigest()


def _owned(name: str, prefixes: tuple[str, ...]) -> bool:
    return name.startswith(prefixes)


def _cache_path(home: Path, reference: str, folder: str | None = None) -> Path:
    argv = ["conan", "cache", "path", reference] + ([f"--folder={folder}"] if folder else [])
    return Path(_run(argv, {**os.environ, "CONAN_HOME": str(home)}).strip())


def _export_digest(home: Path, recipe: str) -> str:
    """A first-party recipe's exported set: the export and export_sources folders together."""
    parts = {}
    for folder in ("export", "export_source"):
        path = _cache_path(home, recipe, None if folder == "export" else folder)
        parts[folder] = tree_digest(path)[0] if path.is_dir() else None
    return key_of(parts)


def graph_nodes(graph_json: Path) -> list[dict]:
    """The nodes of a `conan create --format=json` graph, the consumer root (node 0) excluded."""
    nodes = json.loads(graph_json.read_text())["graph"]["nodes"]
    return [node for index, node in nodes.items() if index != "0"]


def conan_step(store: Path, home: Path, graph_json: Path, package: str, profile: str,
               malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...]) -> int:
    nodes = graph_nodes(graph_json)
    sources, upstream, third_party = {}, {}, {}
    target = None
    for node in nodes:
        name = node["name"]
        recipe = f"{node['ref'].split('#')[0]}#{node['rrev']}"
        binary = f"{recipe}:{node['package_id']}#{node['prev']}"
        if node.get("binary") == "Skip":
            continue
        folder = _cache_path(home, binary)
        if _owned(name, prefixes):
            sources[f"{name}/{node['context']}"] = _export_digest(home, recipe)
            if name == package and node["context"] == "host":
                target = (binary, folder)
            else:
                upstream[f"{name}/{node['context']}"] = tree_digest(folder)[0]
        else:
            third_party[f"{name}/{node['context']}"] = {
                "ref": recipe, "package_id": node["package_id"],
                "content": tree_digest(folder)[0]}
    if target is None:
        sys.exit(f"artefact_store: the graph {graph_json} holds no host node named {package}")
    document = {
        "step": {"kind": "conan-create", "package": package, "profile": profile},
        "definition": {"malf": worktree_tree_id(malf_dir)},
        "sources": sources,
        "upstream": upstream,
        "third_party": third_party,
        "toolchain": json.loads(toolchain.read_text()),
        "profile": profile_member(home, profile, malf_dir),
        "system": system_member(),
    }
    binary, folder = target
    content, entries = tree_digest(folder)
    return LocalStore(store).commit(document, {"package": {"alias": binary, "content": content,
                                                           "entries": entries}},
                                    lambda: _transport(home, binary))


def _transport(home: Path, binary: str) -> Path:
    """The package's transport archive, produced once (`conan cache save`; DN-142.D3)."""
    scratch = Path(tempfile.mkdtemp(prefix="artefact_store."))
    pkglist = scratch / "pkglist.json"
    name, rest = binary.split("/", 1)
    version, revisions = rest.split("#", 1)
    rrev, package = revisions.split(":", 1)
    package_id, prev = package.split("#", 1)
    pkglist.write_text(json.dumps({"Local Cache": {f"{name}/{version}": {"revisions": {rrev: {
        "packages": {package_id: {"revisions": {prev: {}}}}}}}}}))
    archive = scratch / "transport.tgz"
    _run(["conan", "cache", "save", f"--list={pkglist}", f"--file={archive}"],
         {**os.environ, "CONAN_HOME": str(home), "TMPDIR": str(scratch)})
    return archive


class LocalStore:
    """DN-142.D6's store, as a directory: immutable objects, create-only records."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for part in ("objects", "records", "mismatches"):
            (root / part).mkdir(parents=True, exist_ok=True)

    def _create(self, path: Path, data: bytes) -> bool:
        """Write `data` at `path` unless it exists; True when this call created it.
        invariant: a path once created is never rewritten (a hard link fails on an existing name)."""
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
        try:
            os.link(handle.name, path)
            return True
        except FileExistsError:
            return False
        finally:
            os.unlink(handle.name)

    def put_object(self, source: Path) -> str:
        digest = file_sha256(source)
        target = self.root / "objects" / digest
        if not self._create(target, source.read_bytes()) and file_sha256(target) != digest:
            sys.exit(f"artefact_store: object {target} does not hash to its name — the store is corrupt")
        return digest

    def record(self, key: str) -> dict | None:
        path = self.root / "records" / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def commit(self, document: dict, outputs: dict, transport) -> int:
        """Store `outputs` under the document's key, or compare them with the stored ones.

        post: exit 0 when the key was new (stored) or every output's content and alias equal the
        record's; 1 when one differs, a mismatch event naming both digests written beside."""
        key = key_of(document)
        stored = self.record(key)
        if stored is None:
            for output in outputs.values():
                output["transport"] = self.put_object(transport())
            body = {"key": key, "inputs": document, "outputs": outputs,
                    "produced_by": {"seat": socket.gethostname(),
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
            if self._create(self.root / "records" / f"{key}.json",
                            json.dumps(body, sort_keys=True, indent=1).encode()):
                for name, output in outputs.items():
                    print(f"artefact_store: STORED {document['step']['package']} {name} key {key} "
                          f"content {output['content']} ({output['alias']})")
                return 0
            stored = self.record(key)
        differ = []
        for name, output in outputs.items():
            before = stored["outputs"].get(name)
            if before is None:
                differ.append((name, ["absent from the record"]))
                continue
            axes = [axis for axis in ("content", "alias") if before[axis] != output[axis]]
            if axes:
                old = {entry[0]: entry[1:] for entry in before["entries"]}
                new = {entry[0]: entry[1:] for entry in output["entries"]}
                files = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
                differ.append((name, axes + [f"{len(files)} file(s): " + ", ".join(files[:NAMED_FILES])
                                             + (", ..." if len(files) > NAMED_FILES else "")]))
                event = {"key": key, "output": name, "stored": {"content": before["content"],
                                                                 "alias": before["alias"]},
                         "rebuilt": {"content": output["content"], "alias": output["alias"]},
                         "files": files}
                self._create(self.root / "mismatches" / f"{key}.{output['content']}.json",
                             json.dumps(event, sort_keys=True, indent=1).encode())
            else:
                print(f"artefact_store: MATCH {document['step']['package']} {name} key {key} "
                      f"content {output['content']} ({output['alias']})")
        for name, reasons in differ:
            print(f"artefact_store: MISMATCH {document['step']['package']} {name} at an equal key "
                  f"{key}: stored {stored['outputs'].get(name, {}).get('content')} rebuilt "
                  f"{outputs[name]['content']}; {'; '.join(reasons)}")
        return 1 if differ else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "key":
        print(key_of(json.loads(Path(argv[1]).read_text())))
        return 0
    if len(argv) == 2 and argv[0] == "tree":
        print(tree_digest(Path(argv[1]))[0])
        return 0
    if len(argv) == 3 and argv[0] == "toolchain":
        Path(argv[2]).write_text(json.dumps(measure_toolchain(Path(argv[1])), sort_keys=True))
        return 0
    if len(argv) == 9 and argv[0] == "conan-step":
        return conan_step(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5],
                          Path(argv[6]), Path(argv[7]), tuple(argv[8].split()))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyError_ as refused:
        sys.exit(f"artefact_store: refused — {refused}")
