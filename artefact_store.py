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
    artefact_store.py build-id <store> <build-id>
        every package record whose `build_ids` holds that GNU build-id (40 hex), one line each:
        `<key> <package alias> <path>`; exit 0 when one or more answer, 1 when none (DN-142.D15)
    artefact_store.py build-ids <folder>
        the `build_ids` index of a file tree, as JSON, exit 1 naming each linked ELF file that
        carries no build-id note
    artefact_store.py toolchain <compiler root> <out.json>
        the measured toolchain member, written once per run (hashing the compiler tree is the
        expensive part of a key)
    artefact_store.py verdict-step <store> <predicate> <malf dir> <clang-format> <repository>...
        the verdict record of one source predicate (`format`: `malf format --check`) over each
        repository's tracked tree, judged in an export of that tree beside an export of malf's
        (DN-142.D7): stored when the key is new, compared when it is not; exit 0 when every
        verdict is a pass and agrees with its record, 1 on a fail or a mismatch, 2 when the
        predicate ran but judged nothing

A package record's output carries `build_ids` (DN-142.D15): for every linked ELF file of the
package tree (an executable or a shared object) its GNU build-id, the NT_GNU_BUILD_ID note the
linker writes over the file's own bytes. It is READ FROM THE STORED TRANSPORT archive, never taken
from the create's folder, so it indexes exactly the bytes the store keeps; it is derived from bytes
the content digest covers, so it is an index, never compared and never a key member. A first-party
package one of whose linked ELF files carries no note is refused before anything is stored: the
index is total over what ships, or a build answering with an id would find no record.

MALF_STORE_DEFINITION, when set, is `<name>=<directory> ...`: each named directory's tracked tree
joins the key's `definition` beside malf's, for a step another tool drives (Pharos at step 0).

The store is a directory: `objects/<sha256>` immutable blobs, `records/<key>.json` one per key,
`mismatches/<key>.<digest>.json` one per differing rebuild or re-judgement. It stands in for the remote store
until its transport and its writer credential are ruled (DN-142.D6, R1 to R5).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import tarfile
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


def directory_tree_id(directory: Path) -> str:
    """The git tree id of the TRACKED files under `directory`, as they are on disk: its work
    tree's (`worktree_tree_id`), narrowed to the directory's prefix when it is not the top."""
    tree = worktree_tree_id(directory)
    prefix = _run(["git", "-C", str(directory), "rev-parse", "--show-prefix"]).strip().rstrip("/")
    return _run(["git", "-C", str(directory), "rev-parse", f"{tree}:{prefix}"]).strip() if prefix else tree


def definition_member(malf_dir: Path) -> dict[str, str]:
    """DN-142.D2's `definition` member: malf's tracked tree, and the tracked tree of every
    directory `MALF_STORE_DEFINITION` names (`<name>=<directory> ...`), so the code that drives a
    step is part of its key whichever tool drives it.
    pre: each name is distinct from `malf` and from the others, and each directory is inside a git work tree."""
    definition = {"malf": worktree_tree_id(malf_dir)}
    for member in os.environ.get("MALF_STORE_DEFINITION", "").split():
        name, _, directory = member.partition("=")
        if not name or not directory or name in definition:
            raise KeyError_(f"MALF_STORE_DEFINITION: `{member}` is not a new `<name>=<directory>`")
        definition[name] = directory_tree_id(Path(directory))
    return definition


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


ELF_MAGIC = b"\x7fELF"
ELF_LINKED_TYPES = {2, 3}
PT_NOTE = 4
NT_GNU_BUILD_ID = 3
GNU_NOTE_NAME = b"GNU\x00"
BUILD_ID_BYTES = 20


class NoBuildId(ValueError):
    """A linked ELF file whose notes hold no GNU build-id."""


def elf_build_id(data: bytes) -> str | None:
    """The GNU build-id of an ELF file's bytes, lowercase hex; None for a file that is not a LINKED
    ELF file (not ELF, or a relocatable object or core).

    post: a linked ELF file with no NT_GNU_BUILD_ID in any PT_NOTE segment raises NoBuildId
    note: the notes are read through the program headers, the loader's view, which `strip` keeps
    """
    if not data.startswith(ELF_MAGIC) or len(data) < 64:
        return None
    wide, little = data[4] == 2, data[5] == 1
    order = "<" if little else ">"
    e_type = struct.unpack_from(order + "H", data, 16)[0]
    if e_type not in ELF_LINKED_TYPES:
        return None
    if wide:
        phoff = struct.unpack_from(order + "Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from(order + "HH", data, 54)
    else:
        phoff = struct.unpack_from(order + "I", data, 28)[0]
        phentsize, phnum = struct.unpack_from(order + "HH", data, 42)
    for index in range(phnum):
        base = phoff + index * phentsize
        p_type = struct.unpack_from(order + "I", data, base)[0]
        if p_type != PT_NOTE:
            continue
        if wide:
            offset, filesz = struct.unpack_from(order + "Q", data, base + 8)[0], \
                struct.unpack_from(order + "Q", data, base + 32)[0]
        else:
            offset, filesz = struct.unpack_from(order + "I", data, base + 4)[0], \
                struct.unpack_from(order + "I", data, base + 16)[0]
        cursor, end = offset, offset + filesz
        while cursor + 12 <= end:
            namesz, descsz, n_type = struct.unpack_from(order + "III", data, cursor)
            name_at = cursor + 12
            desc_at = name_at + ((namesz + 3) & ~3)
            if n_type == NT_GNU_BUILD_ID and data[name_at:name_at + namesz] == GNU_NOTE_NAME:
                return data[desc_at:desc_at + descsz].hex()
            cursor = desc_at + ((descsz + 3) & ~3)
    raise NoBuildId("no NT_GNU_BUILD_ID note in any PT_NOTE segment")


def build_ids_of(files: dict[str, bytes]) -> tuple[dict[str, str], list[str]]:
    """(`{path: build-id}` over the linked ELF files among `files`, the paths of those lacking one)."""
    ids, missing = {}, []
    for path in sorted(files):
        try:
            found = elf_build_id(files[path])
        except NoBuildId:
            missing.append(path)
            continue
        if found is not None:
            ids[path] = found
    return ids, missing


def tree_files(folder: Path) -> dict[str, bytes]:
    """Every regular file under `folder`, by its path relative to it."""
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file() and not path.is_symlink()}


def transport_files(archive: Path) -> dict[str, bytes]:
    """Every regular file of a package's transport archive, by its path inside the package folder.

    note: `conan cache save` lays a binary out as `b/<storage folder>/p/...` beside its
    `b/<storage folder>/d/metadata/`; the three leading components are dropped, so the paths are
    the package tree's
    """
    files: dict[str, bytes] = {}
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            parts = member.name.split("/")
            if not member.isfile() or len(parts) < 4 or parts[0] != "b" or parts[2] != "p":
                continue
            relative = "/".join(parts[3:])
            extracted = bundle.extractfile(member)
            if relative and extracted is not None:
                files[relative] = extracted.read()
    return files


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
        "definition": definition_member(malf_dir),
        "sources": sources,
        "upstream": upstream,
        "third_party": third_party,
        "toolchain": json.loads(toolchain.read_text()),
        "profile": profile_member(home, profile, malf_dir),
        "system": system_member(),
    }
    binary, folder = target
    content, entries = tree_digest(folder)
    _ids, missing = build_ids_of(tree_files(folder))
    if missing:
        print(f"artefact_store: REFUSED {package}: {len(missing)} linked ELF file(s) carry no GNU "
              f"build-id, so the store's index would not be total over what ships (DN-142.D15): "
              + ", ".join(missing), file=sys.stderr)
        return 1
    return LocalStore(store).commit(document, {"package": {"alias": binary, "content": content,
                                                           "entries": entries}},
                                    {"package": ("transport", lambda: _transport(home, binary))},
                                    derived={"package": _transport_build_ids})


def _transport_build_ids(transport: Path) -> dict[str, dict]:
    """The `build_ids` field of a package output, read from its stored transport archive."""
    ids, missing = build_ids_of(transport_files(transport))
    if missing:
        sys.exit(f"artefact_store: the transport {transport} holds linked ELF file(s) with no "
                 f"build-id that the package folder did not: {', '.join(missing)}")
    return {"build_ids": ids}


def lookup_build_id(store: Path, build_id: str) -> list[tuple[str, str, str]]:
    """Every (record key, package alias, path) whose `build_ids` holds `build_id`, sorted."""
    found = []
    for record in sorted((store / "records").glob("*.json")):
        body = json.loads(record.read_text())
        for output in body["outputs"].values():
            for path, value in (output.get("build_ids") or {}).items():
                if value == build_id:
                    found.append((body["key"], output.get("alias", ""), path))
    return found


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


# A source predicate's verb in malf, and the lines that say it JUDGED: `malf format --check` prints
# one SUMMARY for clang-format and one CCC SUMMARY for the comment grammar. A run missing either, or
# whose clang-format summary checked nothing or failed with zero violations counted, judged nothing.
PREDICATES = {"format": ["format", "--check"]}
FORMAT_SUMMARY = re.compile(r"^malf format: SUMMARY · mode=check-\S+ · dir=\S+ · selected \d+, "
                            r"checked (\d+), (\d+) misformatted, \d+ skipped · rc=(\d+)$")
CCC_SUMMARY = re.compile(r"^malf format: CCC SUMMARY · .* · rc=\d+$")
FINDING = re.compile(r": (error|warning): | CCC (?!SUMMARY)")
JUDGED_ROOT = "<judged>"


def _export(repository: Path, tree: str, into: Path) -> None:
    """Write git tree `tree` of `repository` into `into`: the judged bytes, never the disk's."""
    into.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(repository), "archive", "--format=tar", tree],
                             capture_output=True, check=False)
    if archive.returncode != 0:
        sys.exit(f"artefact_store: `git archive {tree}` in {repository} failed:\n"
                 f"{archive.stderr.decode(errors='replace').strip()}")
    subprocess.run(["tar", "-x", "-C", str(into)], input=archive.stdout, check=True)


def _executable_member(executable: Path) -> dict[str, str]:
    return {"version": _run([str(executable), "--version"]).splitlines()[0].strip(),
            "executable": file_sha256(executable.resolve())}


def verdict_step(store: Path, predicate: str, malf_dir: Path, clang_format: Path,
                 repositories: list[Path]) -> int:
    """DN-142.D7's verdict record of `predicate` over each repository's tracked tree.

    The predicate runs in an export: the repository's tree beside malf's, under a scratch root with
    every MALF_* variable cleared, so what was judged is exactly the keyed trees and a repository's
    `.clang-format` link into `../malf/config/` resolves to the keyed malf. Its findings are the
    finding lines sorted, each path relative to the repository and the scratch root replaced by a
    token: the export's walk order is the filesystem's, so the log's line order is not an output.
    post: exit 0 when every repository passed and agreed with its record, 1 on a fail or a
    mismatch, 2 when a run judged nothing (no record is written for it)."""
    if predicate not in PREDICATES:
        sys.exit(f"artefact_store: no verdict predicate `{predicate}` (known: {' '.join(sorted(PREDICATES))})")
    definition = definition_member(malf_dir)
    toolchain = {"clang-format": _executable_member(clang_format),
                 "python3": _executable_member(Path(sys.executable))}
    system = system_member()
    environment = {name: value for name, value in os.environ.items() if not name.startswith("MALF_")}
    local = LocalStore(store)
    worst = 0
    for repository in repositories:
        name = repository.resolve().name
        tree = worktree_tree_id(repository)
        with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
            root = Path(scratch) / "workspace"
            _export(malf_dir, definition["malf"], root / "malf")
            _export(repository, tree, root / name)
            judged = subprocess.run(["bash", str(root / "malf" / "malf"), *PREDICATES[predicate]],
                                    cwd=root / name, env=environment, capture_output=True, text=True,
                                    check=False)
            lines = ((judged.stdout + judged.stderr).replace(f"{root / name}/", "")
                     .replace(str(root), JUDGED_ROOT).splitlines())
            log = Path(scratch) / "verdict.log"
            log.write_text("\n".join(lines) + "\n")
            summary = next((match for line in lines if (match := FORMAT_SUMMARY.match(line))), None)
            if (summary is None or not any(CCC_SUMMARY.match(line) for line in lines)
                    or int(summary.group(1)) == 0
                    or (summary.group(3) != "0" and summary.group(2) == "0")):
                print(f"artefact_store: UNJUDGED {name} {predicate}: `malf {' '.join(PREDICATES[predicate])}` "
                      f"exited {judged.returncode} without a verdict — {lines[-1] if lines else 'no output'}")
                worst = max(worst, 2)
                continue
            findings = sorted(line for line in lines if FINDING.search(line))
            verdict = "pass" if judged.returncode == 0 else "fail"
            document = {
                "step": {"kind": "verdict", "subject": name,
                         "predicate": {"id": f"malf {' '.join(PREDICATES[predicate])}",
                                       "version": definition["malf"]}},
                "definition": definition,
                "judged": {name: tree},
                "toolchain": toolchain,
                "system": system,
            }
            output = {"verdict": verdict, "findings": key_of(findings),
                      "entries": [[finding] for finding in findings]}
            rc = local.commit(document, {"verdict": output}, {"verdict": ("log", lambda: log)})
            if verdict == "fail":
                print(f"artefact_store: FAIL {name} {predicate}: {len(findings)} finding(s); first "
                      f"{findings[0] if findings else lines[-1]}")
                rc = 1
            worst = max(worst, rc)
    return worst


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

    def commit(self, document: dict, outputs: dict, objects: dict,
               derived: dict | None = None) -> int:
        """Store `outputs` under the document's key, or compare them with the stored ones.

        `objects` maps an output's name to `(field, produce)`: on a new key `produce()` returns the
        file stored as that output's object, its digest kept under `field` and never compared.
        `derived` maps an output's name to a function of that STORED object returning fields the
        record keeps beside it, an index read from the stored bytes and never compared.
        post: exit 0 when the key was new (stored) or every output's compared axes equal the
        record's; 1 when one differs, a mismatch event naming both digests written beside."""
        key = key_of(document)
        subject = document["step"].get("package") or document["step"]["subject"]
        stored = self.record(key)
        if stored is None:
            for name, (field, produce) in objects.items():
                outputs[name][field] = self.put_object(produce())
                if derived and name in derived:
                    outputs[name].update(derived[name](self.root / "objects" / outputs[name][field]))
            body = {"key": key, "inputs": document, "outputs": outputs,
                    "produced_by": {"seat": socket.gethostname(),
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
            if self._create(self.root / "records" / f"{key}.json",
                            json.dumps(body, sort_keys=True, indent=1).encode()):
                for name, output in outputs.items():
                    print(f"artefact_store: STORED {subject} {name} key {key} {_described(output)}")
                return 0
            stored = self.record(key)
        uncompared = {"entries", "build_ids"} | {field for field, _ in objects.values()}
        differ = []
        for name, output in outputs.items():
            before = stored["outputs"].get(name)
            if before is None:
                differ.append((name, ["absent from the record"]))
                continue
            axes = sorted(axis for axis in (before.keys() | output.keys()) - uncompared
                          if before.get(axis) != output.get(axis))
            if axes:
                old = {entry[0]: entry[1:] for entry in before["entries"]}
                new = {entry[0]: entry[1:] for entry in output["entries"]}
                units = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
                differ.append((name, axes + [f"{len(units)} {_unit(output)}: " + ", ".join(units[:NAMED_FILES])
                                             + (", ..." if len(units) > NAMED_FILES else "")]))
                event = {"key": key, "output": name,
                         "stored": {axis: before.get(axis) for axis in axes},
                         "rebuilt": {axis: output.get(axis) for axis in axes},
                         "differing": units}
                self._create(self.root / "mismatches" / f"{key}.{_identity(output)}.json",
                             json.dumps(event, sort_keys=True, indent=1).encode())
            else:
                print(f"artefact_store: MATCH {subject} {name} key {key} {_described(output)}")
        for name, reasons in differ:
            print(f"artefact_store: MISMATCH {subject} {name} at an equal key {key}: stored "
                  f"{_described(stored['outputs'].get(name, {}))} rebuilt {_described(outputs[name])}; "
                  f"{'; '.join(reasons)}")
        return 1 if differ else 0


def _identity(output: dict) -> str:
    """The digest an output is identified by: a package's content, a verdict's findings."""
    return output.get("content") or output["findings"]


def _unit(output: dict) -> str:
    return "file(s)" if "content" in output else "finding(s)"


def _described(output: dict) -> str:
    if "content" in output:
        return f"content {output['content']} ({output['alias']})"
    if "verdict" in output:
        return f"verdict {output['verdict']} findings {output['findings']}"
    return "nothing"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "key":
        print(key_of(json.loads(Path(argv[1]).read_text())))
        return 0
    if len(argv) == 2 and argv[0] == "tree":
        print(tree_digest(Path(argv[1]))[0])
        return 0
    if len(argv) == 3 and argv[0] == "build-id":
        found = lookup_build_id(Path(argv[1]), argv[2].lower())
        for key, alias, path in found:
            print(f"{key} {alias} {path}")
        return 0 if found else 1
    if len(argv) == 2 and argv[0] == "build-ids":
        ids, missing = build_ids_of(tree_files(Path(argv[1])))
        print(json.dumps(ids, sort_keys=True, indent=1))
        for path in missing:
            print(f"artefact_store: no GNU build-id: {path}", file=sys.stderr)
        return 1 if missing else 0
    if len(argv) == 3 and argv[0] == "toolchain":
        Path(argv[2]).write_text(json.dumps(measure_toolchain(Path(argv[1])), sort_keys=True))
        return 0
    if len(argv) >= 6 and argv[0] == "verdict-step":
        return verdict_step(Path(argv[1]), argv[2], Path(argv[3]), Path(argv[4]),
                            [Path(repository) for repository in argv[5:]])
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
