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
                                 <malf dir> <toolchain.json> <prefixes> [<results dir> [<corpus gates>]]
        the record of one `conan create` step, from the graph its `--format=json` printed: stored
        when the key is new (exit 0), compared when it is not (exit 0 on a match, 1 on a mismatch);
        with a results directory, the step's test verdict record beside it (DN-142.D5 (3)); with
        the corpus gates parameter document naming the package, its corpus verdict too
        (DN-142.D21 (1)), whose red is that record's verdict and never this command's exit
    artefact_store.py upstreams <store>
        every create or build record whose inputs name an `upstream` package digest that no record
        of that package's OWN create step at the same profile holds, one line per record with each
        unrecorded upstream; exit 0 when there is none, 1 otherwise (DN-142.D16), read-only
    artefact_store.py build-id <store> <build-id>
        every package record whose `build_ids` holds that GNU build-id (40 hex), one line each:
        `<key> <package alias> <path>`; exit 0 when one or more answer, 1 when none (DN-142.D15)
    artefact_store.py build-ids <folder>
        the `build_ids` index of a file tree, as JSON, exit 1 naming each linked ELF file that
        carries no build-id note
    artefact_store.py build-step <store> <conan home> <build graph.json> <package> <profile name>
                                 <malf dir> <toolchain.json> <prefixes> <source tree id> <results dir>
        the verdict record of one `conan build` step (a `stored: false` package, DN-142.D14): keyed
        like a create's record by its inputs, the source being the package's git tree id; it stores
        no package and judges the upstream packages the build linked
    artefact_store.py content-manifest <root>
        the content digest of a data root (DN-142.D21 (5)): SHA-256 over the canonical JSON of the
        sorted [relative path, sha256] of every regular file under it, symlinks followed; a file
        root is the one row [".", sha256]. One line of 64 hex
    artefact_store.py content-manifest-rows <sha256sum file> <prefix>
        the same digest computed from a committed `sha256sum` manifest whose paths carry <prefix>,
        for a root that manifest was just verified equal to; nothing under the root is read
    artefact_store.py verdict <store> <document.json> <outputs.json> [<name>=<file>...]
        one generalized verdict record (DN-142.D19 (10)): the document {step: {kind: verdict, id,
        subject, predicate: {id, version}, params}, definition, judged, data, toolchain, system,
        context}, the outputs {verdict, findings (a list), entries, record}, each named file kept
        as an object; stored when the key is new (exit 0), compared when it is not on the verdict,
        the findings and the record, never an entry or an object (exit 0 a match, 1 a mismatch)
    artefact_store.py executables <package dir> <package>
        the `executables:` map the package declares in its repository's packages.yml at HEAD
        (DN-142.D22), as canonical JSON; `{}` when it declares none
    artefact_store.py published-check <folder>
        each linked ELF file under <folder> whose bytes are not in the published form, one
        `<path>: symbol table|runpath` line; exit 1 when there is one
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

A step's TEST VERDICT is a record of its own (DN-142.D5 (3), DN-142.D7), never a field of the
package record: the consumer's create runs no test, so a package record holding results could never
compare equal to the writer's. Its key is the build step's key plus the selection (the test helper's
blob id and its label expression); its outputs are the verdict, the result set (each population,
test name and outcome, sorted; the JUnit files of malf's helper, `<package>/<package id>.xml` for
the build and `<package>/<package id>.test_package.xml` for the test_package), `judged` (the content digests the tests judged:
the package's, or for a `stored: false` step the upstream packages it linked) and the logs as an
object, never compared. A rebuild at an equal key compares the verdict, the result set and `judged`,
never a timing.

A step's CORPUS VERDICT (DN-142.D21) is a third record, output name `corpus`, written when the
corpus gates parameter document names the package: its key is the build step's plus the selection
`-L corpus` with the legs and their suites, plus `data`, each mount role's content digest (never
its path); its rows are `[corpus-<leg>, test name, outcome]`, the outcome one of `passed`,
`failed`, `skipped`, `disabled`, `absent` (a named suite that selected no test, or ran none) and
`unguarded` (a test ctest could not see skip). Its verdict is `pass` only when every row is
`passed`: a skipped corpus gate is the green-blind state the corpus run exists to refuse.

A step's EXECUTABLES (DN-142.D22) are classified by its package's `executables:` map in the
recorded `packages.yml` (`published` or `run-from-cache`), which `malf store-create` hands over in a
JSON file named by MALF_STEP_EXECUTABLES and which enters the create's key as `step.executables`
(`{}` when none, the meaning of an unset variable too). The store REFUSES, before the record, an
executable entry the map does not name, a map key naming no executable entry, and a `published`
ELF carrying a symbol table (`.symtab` or `.debug_*`), a runpath (DT_RPATH or DT_RUNPATH) or no
build-id; a `run-from-cache` entry keeps its runpath.

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


def content_manifest(root: Path) -> str:
    """DN-142.D21 (5)'s content digest of a data root: SHA-256 over the canonical JSON of the sorted
    `[relative path, sha256]` of every regular file under it, symlinks followed; a file root is the
    one row `[".", sha256]`. No mtime, size or inode enters it, so a same-size rewrite moves it."""
    if root.is_file():
        return key_of([[".", file_sha256(root)]])
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is neither a file nor a directory")
    rows = []
    for directory, subdirectories, names in os.walk(root, followlinks=True):
        subdirectories.sort()
        for name in names:
            path = Path(directory) / name
            if path.is_file():
                rows.append([path.relative_to(root).as_posix(), file_sha256(path)])
    return key_of(sorted(rows))


def manifest_rows_digest(manifest: Path, prefix: str) -> str:
    """`content_manifest` of the root a `sha256sum` manifest names, from the manifest's rows alone:
    every line is `<sha256>  <prefix><relative path>`. pre: the root was just verified equal to it."""
    rows = []
    for line in manifest.read_text().splitlines():
        digest, separator, path = line.partition("  ")
        if not separator or not path.startswith(prefix) or len(digest) != 64:
            raise ValueError(f"{manifest}: not a `<sha256>  {prefix}<path>` line: {line!r}")
        rows.append([path.removeprefix(prefix), digest])
    return key_of(sorted(rows))


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
    cmake, ninja, conan and patchelf by version and by the digest of the executable that runs.
    patchelf is a member because a published executable's package() runs it (DN-142.D20 (3)), and
    only on Linux, where those package() methods run it: another seat measures no patchelf."""
    compiler, _ = tree_digest(compiler_root)
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        probe = Path(scratch) / "probe.cmake"
        probe.write_text('message("${CMAKE_COMMAND}")\n')
        cmake_binary = Path(subprocess.run(["cmake", "-P", str(probe)], capture_output=True,
                                           text=True, check=True).stderr.strip())
    tools: dict[str, object] = {"compiler": compiler}
    measured = [("cmake", cmake_binary, ["cmake", "--version"]),
                ("ninja", Path(_run(["which", "ninja"]).strip()), ["ninja", "--version"]),
                ("conan", Path(_run(["which", "conan"]).strip()), ["conan", "--version"])]
    if sys.platform.startswith("linux"):
        measured.append(("patchelf", Path(_run(["which", "patchelf"]).strip()),
                         ["patchelf", "--version"]))
    for name, executable, version_argv in measured:
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


# DN-142.D22: which of a package's executables are PUBLISHED, and the published form's predicates,
# read from the bytes beside elf_build_id (section headers for the names, PT_DYNAMIC for the tags).
EXECUTABLE_CLASSES = ("published", "run-from-cache")
EXECUTABLES_ENV = "MALF_STEP_EXECUTABLES"
SHT_SYMTAB = 2
PT_DYNAMIC = 2
DT_NULL, DT_RPATH, DT_RUNPATH = 0, 15, 29


def elf_published_defects(data: bytes) -> list[str]:
    """What keeps a LINKED ELF file's bytes from the published form: `symbol table` (a `.symtab`
    section or any `.debug_*` one; `.dynsym` is the loader's and stays) and `runpath` (DT_RPATH or
    DT_RUNPATH in the PT_DYNAMIC segment). Empty for a file that is not a linked ELF file."""
    if not data.startswith(ELF_MAGIC) or len(data) < 64:
        return []
    wide, order = data[4] == 2, "<" if data[5] == 1 else ">"
    if struct.unpack_from(order + "H", data, 16)[0] not in ELF_LINKED_TYPES:
        return []
    word = "Q" if wide else "I"
    defects = []
    if wide:
        phoff, shoff = struct.unpack_from(order + "QQ", data, 32)
        phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(order + "HHHHH", data, 54)
    else:
        phoff, shoff = struct.unpack_from(order + "II", data, 28)
        phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(order + "HHHHH", data, 42)
    sections = []
    for index in range(shnum if shoff else 0):
        base = shoff + index * shentsize
        name, kind = struct.unpack_from(order + "II", data, base)
        offset = struct.unpack_from(order + word, data, base + (24 if wide else 16))[0]
        sections.append((name, kind, offset))
    names_at = sections[shstrndx][2] if 0 < shstrndx < len(sections) else None

    def section_name(offset: int) -> str:
        if names_at is None:
            return ""
        end = data.index(b"\0", names_at + offset)
        return data[names_at + offset:end].decode("ascii", "replace")
    if any(kind == SHT_SYMTAB or section_name(name) == ".symtab"
           or section_name(name).startswith(".debug") for name, kind, _offset in sections):
        defects.append("symbol table")
    entry = 16 if wide else 8
    for index in range(phnum):
        base = phoff + index * phentsize
        if struct.unpack_from(order + "I", data, base)[0] != PT_DYNAMIC:
            continue
        offset = struct.unpack_from(order + word, data, base + (8 if wide else 4))[0]
        size = struct.unpack_from(order + word, data, base + (32 if wide else 16))[0]
        for cursor in range(offset, offset + size - entry + 1, entry):
            tag = struct.unpack_from(order + word, data, cursor)[0]
            if tag == DT_NULL:
                break
            if tag in (DT_RPATH, DT_RUNPATH):
                defects.append("runpath")
                break
    return defects


def executable_findings(entries: list[list[str]], files: dict[str, bytes],
                        declared: dict[str, str]) -> list[str]:
    """DN-142.D22 (1), (2): `<path>: <reason>` for every executable entry the declaration does not
    classify (`unclassified`), every declared path naming no executable entry (`stale`), and every
    defect of a `published` ELF entry (`symbol table`, `runpath`, `no build-id`); sorted."""
    executables = {path for path, mode, _digest in entries if mode == "executable"}
    findings = [f"{path}: unclassified" for path in sorted(executables - declared.keys())]
    findings += [f"{path}: stale" for path in sorted(declared.keys() - executables)]
    for path in sorted(executables & declared.keys()):
        if declared[path] != "published":
            continue
        findings += [f"{path}: {defect}" for defect in elf_published_defects(files[path])]
        try:
            elf_build_id(files[path])
        except NoBuildId:
            findings.append(f"{path}: no build-id")
    return findings


def declared_executables(package_dir: Path, package: str) -> dict[str, str]:
    """The `executables:` map `package` declares in the `packages.yml` of the repository holding
    `package_dir`, read from its RECORDED commit's blob (HEAD), never the disk; `{}` when it declares
    none. Raises ValueError on a class outside EXECUTABLE_CLASSES or an undeclared package."""
    import yaml

    top = Path(_run(["git", "-C", str(package_dir), "rev-parse", "--show-toplevel"]).strip())
    document = yaml.safe_load(_run(["git", "-C", str(top), "show", "HEAD:packages.yml"])) or {}
    entry = (document.get("packages") or {}).get(package)
    if entry is None:
        raise ValueError(f"{top}/packages.yml at HEAD declares no package {package}")
    declared = entry.get("executables") or {}
    wrong = {path: kind for path, kind in declared.items() if kind not in EXECUTABLE_CLASSES}
    if wrong:
        raise ValueError(f"{package}: executables classed outside {', '.join(EXECUTABLE_CLASSES)}: "
                         + ", ".join(f"{path}: {kind}" for path, kind in sorted(wrong.items())))
    return dict(sorted(declared.items()))


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


def _step_inputs(home: Path, graph_json: Path, package: str, prefixes: tuple[str, ...],
                 with_root: bool) -> tuple[dict, dict, dict, tuple[str, Path] | None]:
    """(sources, upstream, third_party, the target's (binary, folder)) of a step's graph."""
    nodes = graph_nodes(graph_json)
    sources, upstream, third_party = {}, {}, {}
    target = None
    # note: a `build-scripts` package (a content recipe) is created in the build context alone
    created = "host" if any(node["name"] == package and node["context"] == "host" for node in nodes) \
        else "build"
    for node in nodes:
        name = node["name"]
        recipe = f"{node['ref'].split('#')[0]}#{node['rrev']}"
        binary = f"{recipe}:{node['package_id']}#{node['prev']}"
        if node.get("binary") == "Skip":
            continue
        folder = _cache_path(home, binary)
        if _owned(name, prefixes):
            sources[f"{name}/{node['context']}"] = _export_digest(home, recipe)
            if name == package and node["context"] == created:
                target = (binary, folder)
            else:
                upstream[f"{name}/{node['context']}"] = tree_digest(folder)[0]
        else:
            third_party[f"{name}/{node['context']}"] = {
                "ref": recipe, "package_id": node["package_id"],
                "content": tree_digest(folder)[0]}
    if with_root and target is None:
        sys.exit(f"artefact_store: the graph {graph_json} holds no node named {package}")
    return sources, upstream, third_party, target


def _step_document(kind: str, package: str, profile: str, home: Path, malf_dir: Path,
                   toolchain: Path, sources: dict, upstream: dict, third_party: dict) -> dict:
    return {
        "step": {"kind": kind, "package": package, "profile": profile},
        "definition": definition_member(malf_dir),
        "sources": sources,
        "upstream": upstream,
        "third_party": third_party,
        "toolchain": json.loads(toolchain.read_text()),
        "profile": profile_member(home, profile, malf_dir),
        "system": system_member(),
    }


def produced_packages(store: Path, profile: str) -> dict[str, set[str]]:
    """The content digests each package's own create step recorded at `profile`."""
    produced: dict[str, set[str]] = {}
    records = store / "records"
    for path in sorted(records.glob("*.json")) if records.is_dir() else ():
        body = json.loads(path.read_text())
        step = body["inputs"]["step"]
        content = body["outputs"].get("package", {}).get("content")
        if step.get("kind") == "conan-create" and step.get("profile") == profile and content:
            produced.setdefault(step["package"], set()).add(content)
    return produced


def unrecorded_upstreams(store: Path, document: dict) -> list[str]:
    """Each `upstream` of a step whose digest no record of that package's own step holds.

    A first-party binary the step's graph names must be the one that package's own step created
    and judged (DN-142.D16): a digest no record holds is a variant some consumer's create built
    with options of its own, and its tests ran in no step."""
    produced = produced_packages(store, document["step"]["profile"])
    return sorted(f"{name} {digest}" for name, digest in document["upstream"].items()
                  if digest not in produced.get(name.split("/", 1)[0], set()))


def _refuse_unrecorded(store: Path, document: dict) -> bool:
    """True, having named each one, when the step's graph links an unrecorded first-party binary."""
    unrecorded = unrecorded_upstreams(store, document)
    if unrecorded:
        print(f"artefact_store: REFUSED {document['step']['package']}: {len(unrecorded)} upstream "
              f"package(s) at a digest no record of their own step at {document['step']['profile']} "
              f"holds, so the step linked a binary no step created and judged (DN-142.D16): "
              + "; ".join(unrecorded), file=sys.stderr)
    return bool(unrecorded)


def audit_upstreams(store: Path) -> int:
    """`upstreams`: every step record of the store judged against its own store, read-only."""
    flagged = 0
    for path in sorted((store / "records").glob("*.json")):
        body = json.loads(path.read_text())
        document = body["inputs"]
        if document["step"].get("kind") not in ("conan-create", "conan-build") \
                or "upstream" not in document:
            continue
        unrecorded = unrecorded_upstreams(store, document)
        if unrecorded:
            flagged += 1
            print(f"{body['key']} {document['step']['package']}: {len(unrecorded)} of "
                  f"{len(document['upstream'])} upstream(s) unrecorded: " + "; ".join(unrecorded))
    print(f"artefact_store: {flagged} record(s) link an upstream no step of its own recorded")
    return 1 if flagged else 0


def conan_step(store: Path, home: Path, graph_json: Path, package: str, profile: str,
               malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...],
               results: Path | None = None, corpus_gates: Path | None = None) -> int:
    sources, upstream, third_party, target = _step_inputs(home, graph_json, package, prefixes, True)
    document = _step_document("conan-create", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    declared_path = os.environ.get(EXECUTABLES_ENV)
    declared = json.loads(Path(declared_path).read_text()) if declared_path else {}
    document["step"]["executables"] = declared
    if _refuse_unrecorded(store, document):
        return 1
    binary, folder = target
    content, entries = tree_digest(folder)
    files = tree_files(folder)
    _ids, missing = build_ids_of(files)
    if missing:
        print(f"artefact_store: REFUSED {package}: {len(missing)} linked ELF file(s) carry no GNU "
              f"build-id, so the store's index would not be total over what ships (DN-142.D15): "
              + ", ".join(missing), file=sys.stderr)
        return 1
    findings = executable_findings(entries, files, declared)
    if findings:
        print(f"artefact_store: REFUSED {package}: {len(findings)} executable finding(s) against "
              f"its packages.yml `executables:` declaration (DN-142.D22): " + "; ".join(findings),
              file=sys.stderr)
        return 1
    stored = LocalStore(store).commit(document, {"package": {"alias": binary, "content": content,
                                                             "entries": entries}},
                                      {"package": ("transport", lambda: _transport(home, binary))},
                                      derived={"package": _transport_build_ids})
    if results is None:
        return stored
    package_id = binary.split(":", 1)[1].split("#", 1)[0]
    judged = max(stored, verdict_record(store, key_of(document), package, package_id, profile,
                                        malf_dir, results, {package: content}))
    if corpus_gates is None:
        return judged
    return max(judged, corpus_record(store, key_of(document), package, package_id, profile,
                                     malf_dir, results, {package: content}, corpus_gates))


def build_step(store: Path, home: Path, graph_json: Path, package: str, profile: str,
               malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...], source_tree: str,
               results: Path) -> int:
    """The verdict record of a `stored: false` package's `conan build` step (DN-142.D14): no
    package record, the source its git tree id, `judged` the upstream packages it linked."""
    sources, upstream, third_party, _target = _step_inputs(home, graph_json, package, prefixes, False)
    sources[f"{package}/host"] = source_tree
    document = _step_document("conan-build", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    if _refuse_unrecorded(store, document):
        return 1
    built = sorted((results / package).glob("*.xml")) if (results / package).is_dir() else []
    package_ids = sorted({path.name.split(".", 1)[0] for path in built})
    if len(package_ids) > 1:
        sys.exit(f"artefact_store: {results / package} holds results of {len(package_ids)} package "
                 f"ids, and a `conan build` step builds one: {', '.join(package_ids)}")
    return verdict_record(store, key_of(document), package, package_ids[0] if package_ids else "-",
                          profile, malf_dir, results,
                          {name.split("/")[0]: digest for name, digest in upstream.items()})


VERDICT_POPULATIONS = (("build", "{pid}.xml"), ("test_package", "{pid}.test_package.xml"))


# What the helper's default run records beside its JUnit files, never compared by population:
# the corpus-labelled names it listed and did not run, and the tree's linked targets.
LISTED_SUFFIX = ".corpus-listed.json"
TARGETS_SUFFIX = ".targets.txt"


def junit_rows(path: Path, population: str) -> list[list[str]]:
    """`[population, test name, outcome]` per test case of one JUnit file; an outcome is `passed`,
    `failed`, `skipped` or `disabled`."""
    import xml.etree.ElementTree as ElementTree

    rows = []
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        status = case.get("status")
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif status == "disabled":
            outcome = "disabled"
        elif case.find("skipped") is not None or status == "notrun":
            outcome = "skipped"
        else:
            outcome = "passed"
        rows.append([population, case.get("name") or "", outcome])
    return rows


def junit_results(results: Path, package: str, package_id: str
                  ) -> tuple[list[list[str]], list[Path]]:
    """([population, test name, outcome] sorted, the JUnit files read) of one step's binary, from
    malf's helper's result files `<results>/<package>/<package id>.*`, with the corpus-labelled
    names the default run listed as `[corpus-listed, name, listed]` rows."""
    rows, files = [], []
    for population, pattern in VERDICT_POPULATIONS:
        path = results / package / pattern.format(pid=package_id)
        if not path.is_file():
            continue
        files.append(path)
        rows += junit_rows(path, population)
    listed = results / package / f"{package_id}{LISTED_SUFFIX}"
    if listed.is_file():
        rows += [["corpus-listed", name, "listed"] for name in json.loads(listed.read_text())]
    return sorted(rows), files


def selection_member(malf_dir: Path) -> dict[str, object]:
    """The test selection a verdict ran: the helper's git blob id and its label expression."""
    helper = malf_dir / "malf_recipe_tests.py"
    blob = _run(["git", "hash-object", str(helper)]).strip()
    sys.path.insert(0, str(malf_dir))
    import malf_recipe_tests

    return {"helper": blob, "labels": ["-LE", malf_recipe_tests.CORPUS_LABEL]}


def verdict_record(store: Path, build_key: str, package: str, package_id: str, profile: str,
                   malf_dir: Path, results: Path, judged: dict[str, str]) -> int:
    """Store or compare one step's test verdict (DN-142.D5 (3)); a step with no result file ran no
    test and has no verdict to keep (exit 0, saying so). The step's own files are linked at
    `<results>/steps/<package>.xml` and `.test_package.xml`, where a reader of the run finds the
    population the step's binary ran, whatever variants of it the walk's later creates built."""
    rows, files = junit_results(results, package, package_id)
    _link_step_files(results, package, package_id,
                     files + [path for suffix in (LISTED_SUFFIX, TARGETS_SUFFIX)
                              if (path := results / package / f"{package_id}{suffix}").is_file()])
    if not files:
        print(f"artefact_store: NO VERDICT {package}: the step wrote no result file, it ran no test")
        return 0
    verdict = "fail" if any(row[2] == "failed" for row in rows) else "pass"
    document = {"step": {"kind": "test-verdict", "package": package, "profile": profile},
                "build": build_key, "selection": selection_member(malf_dir)}
    scratch = Path(tempfile.mkdtemp(prefix="artefact_store."))
    log = scratch / "results.tar"
    with tarfile.open(log, "w") as bundle:
        for path in files:
            info = bundle.gettarinfo(str(path), arcname=path.name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            with path.open("rb") as handle:
                bundle.addfile(info, handle)
    output = {"verdict": verdict, "results": rows, "findings": key_of(rows), "judged": judged,
              "tests": len(rows)}
    return LocalStore(store).commit(document, {"verdict": output}, {"verdict": ("log", lambda: log)})


def _link_step_files(results: Path, package: str, package_id: str, files: list[Path]) -> None:
    """Link each of the step's own files at `<results>/steps/<package><suffix>`."""
    steps = results / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    for path in files:
        alias = steps / (package + path.name.removeprefix(package_id))
        alias.unlink(missing_ok=True)
        os.link(path, alias)


def corpus_rows(results: Path, package: str, package_id: str, legs: list[dict]
                ) -> tuple[list[list[str]], list[Path]]:
    """The corpus verdict's rows, each leg's JUnit rows and its sidecar's `absent` and `unguarded`
    rows, plus one `absent` row per named suite with no test row at all (a leg that never ran, or
    ran without it); and the files read."""
    rows, files = [], []
    for leg in legs:
        population = f"corpus-{leg['leg']}"
        junit = results / package / f"{package_id}.{population}.xml"
        sidecar = results / package / f"{package_id}.{population}.json"
        leg_rows = []
        if junit.is_file():
            files.append(junit)
            leg_rows += junit_rows(junit, population)
        if sidecar.is_file():
            files.append(sidecar)
            leg_rows += json.loads(sidecar.read_text())["rows"]
        for suite in leg["suites"]:
            if not any(row[1].startswith(f"{suite}.") for row in leg_rows):
                leg_rows.append([population, f"{suite}.*", "absent"])
        rows += leg_rows
    return sorted({tuple(row): row for row in rows}.values()), files


def corpus_record(store: Path, build_key: str, package: str, package_id: str, profile: str,
                  malf_dir: Path, results: Path, judged: dict[str, str], gates: Path) -> int:
    """Store or compare one step's corpus verdict (DN-142.D21 (1), (3), (5)) when the corpus gates
    parameter document names the package; exit 0 when it names none. The verdict's red is in the
    record, never in the exit: a red corpus gate is judged by step 0's corpus stage, and failing
    here would fail the package's step for a cause that is not the package."""
    document_in = json.loads(gates.read_text())
    legs = document_in.get("packages", {}).get(package)
    if not legs:
        return 0
    rows, files = corpus_rows(results, package, package_id, legs)
    _link_step_files(results, package, package_id, files)
    selection = selection_member(malf_dir)
    selection.update({"labels": ["-L", "corpus"],
                      "legs": [{"leg": leg["leg"], "suites": sorted(leg["suites"])} for leg in legs]})
    document = {"step": {"kind": "test-verdict", "package": package, "profile": profile},
                "build": build_key, "selection": selection, "data": document_in["data"]}
    verdict = "pass" if rows and all(row[2] == "passed" for row in rows) else "fail"
    scratch = Path(tempfile.mkdtemp(prefix="artefact_store."))
    log = scratch / "corpus.tar"
    with tarfile.open(log, "w") as bundle:
        for path in files:
            info = bundle.gettarinfo(str(path), arcname=path.name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            with path.open("rb") as handle:
                bundle.addfile(info, handle)
    output = {"verdict": verdict, "results": rows, "findings": key_of(rows), "judged": judged,
              "tests": len(rows)}
    stored = LocalStore(store).commit(document, {"corpus": output}, {"corpus": ("log", lambda: log)})
    if verdict != "pass":
        bad = [row for row in rows if row[2] != "passed"]
        print(f"artefact_store: CORPUS RED {package}: {len(bad)} row(s) not passed: "
              + ", ".join(f"{row[0]} {row[1]} {row[2]}" for row in bad[:NAMED_FILES])
              + (", ..." if len(bad) > NAMED_FILES else ""))
    return stored


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


# THE GENERALIZED VERDICT KIND (DN-142.D19 (10)): every keyed step that is not a package's tests.
# Its document names the step and its predicate with the predicate's parameters, the definition,
# what it judged, the data it read (content digests), the toolchain and system, and `context`, only
# what the predicate declares it reads (the date, V, the runner). Its outputs: the verdict, the
# findings' digest and their entries, `record` (the exact fragment the step writes into the step-0
# record, so a reused step reproduces it byte for byte) and named `objects`. A rebuild compares the
# verdict, the findings and the record, NEVER an entry or an object: a timing verdict's numbers ride
# its object, so equal verdicts over different numbers MATCH.
VERDICT_DOCUMENT_MEMBERS = ("step", "definition", "judged", "data", "toolchain", "system", "context")
VERDICT_STEP_MEMBERS = ("kind", "id", "subject", "predicate", "params")
VERDICT_OUTPUTS = ("verdict", "findings", "entries", "record")


def verdict_shape(document: dict, outputs: dict) -> list[str]:
    """What keeps a document and its outputs from the generalized verdict kind's shape; empty when
    nothing does."""
    wrong = [f"document lacks `{member}`" for member in VERDICT_DOCUMENT_MEMBERS if member not in document]
    step = document.get("step") or {}
    wrong += [f"step lacks `{member}`" for member in VERDICT_STEP_MEMBERS if member not in step]
    if step.get("kind") != "verdict":
        wrong.append(f"step kind is {step.get('kind')!r}, not 'verdict'")
    if not {"id", "version"} <= set(step.get("predicate") or {}):
        wrong.append("predicate lacks `id` or `version`")
    wrong += [f"outputs lack `{member}`" for member in VERDICT_OUTPUTS if member not in outputs]
    if outputs.get("verdict") not in ("pass", "fail"):
        wrong.append(f"verdict is {outputs.get('verdict')!r}, not pass or fail")
    if not isinstance(outputs.get("findings"), list):
        wrong.append("findings is not a list")
    return wrong


def judged_verdict(store: Path, document: dict, outputs: dict, objects: dict[str, Path]) -> int:
    """Store or compare one generalized verdict: `outputs` holds the verdict, the findings as a list
    (kept as their digest), the entries and the record fragment; `objects` names the files kept as
    the record's objects. post: exit 0 stored or matching, 1 a mismatch; a shape that is not the
    kind's exits the process naming every defect (a refused record is never written)."""
    wrong = verdict_shape(document, outputs)
    if wrong:
        sys.exit(f"artefact_store: REFUSED verdict {document.get('step', {}).get('id')}: "
                 + "; ".join(wrong))
    output = {"verdict": outputs["verdict"], "findings": key_of(sorted(outputs["findings"])),
              "entries": outputs["entries"], "record": outputs["record"]}
    return LocalStore(store).commit(document, {"verdict": output},
                                    {"verdict": ("objects", lambda: dict(objects))})


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
                "step": {"kind": "verdict", "id": f"{predicate}/{name}", "subject": name,
                         "predicate": {"id": f"malf {' '.join(PREDICATES[predicate])}",
                                       "version": definition["malf"]},
                         "params": {}},
                "definition": definition,
                "judged": {name: tree},
                "data": {},
                "toolchain": toolchain,
                "system": system,
                "context": {},
            }
            rc = judged_verdict(local.root, document,
                                {"verdict": verdict, "findings": findings,
                                 "entries": [[finding] for finding in findings], "record": {}},
                                {"log": log})
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
        file stored as that output's object, its digest kept under `field` and never compared — or
        a `{name: file}` map, each stored, the field then holding `{name: digest}`.
        `derived` maps an output's name to a function of that STORED object returning fields the
        record keeps beside it, an index read from the stored bytes and never compared.
        post: exit 0 when the key was new (stored) or every output's compared axes equal the
        record's; 1 when one differs, a mismatch event naming both digests written beside."""
        key = key_of(document)
        subject = document["step"].get("package") or document["step"]["subject"]
        stored = self.record(key)
        if stored is None:
            for name, (field, produce) in objects.items():
                produced = produce()
                outputs[name][field] = ({label: self.put_object(path)
                                         for label, path in sorted(produced.items())}
                                        if isinstance(produced, dict) else self.put_object(produced))
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
                old, new = _units(before), _units(output)
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


def _units(output: dict) -> dict[str, list]:
    """An output's comparable units by name: a package's files, a verdict's tests."""
    if "entries" in output:
        return {entry[0]: entry[1:] for entry in output["entries"]}
    return {f"{row[0]}:{row[1]}": row[2:] for row in output.get("results", [])}


def _identity(output: dict) -> str:
    """The digest an output is identified by: a package's content, a verdict's findings."""
    return output.get("content") or output["findings"]


def _unit(output: dict) -> str:
    if "content" in output:
        return "file(s)"
    return "test(s)" if "results" in output else "finding(s)"


def _described(output: dict) -> str:
    if "content" in output:
        return f"content {output['content']} ({output['alias']})"
    if "verdict" in output and "tests" in output:
        return f"verdict {output['verdict']} over {output['tests']} test(s) findings {output['findings']}"
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
    if len(argv) == 2 and argv[0] == "upstreams":
        return audit_upstreams(Path(argv[1]))
    if len(argv) == 2 and argv[0] == "build-ids":
        ids, missing = build_ids_of(tree_files(Path(argv[1])))
        print(json.dumps(ids, sort_keys=True, indent=1))
        for path in missing:
            print(f"artefact_store: no GNU build-id: {path}", file=sys.stderr)
        return 1 if missing else 0
    if len(argv) >= 4 and argv[0] == "verdict":
        objects = {}
        for spec in argv[4:]:
            label, separator, path = spec.partition("=")
            if not separator or not label:
                print(f"artefact_store: verdict: an object is `<name>=<file>`, not {spec!r}", file=sys.stderr)
                return 2
            objects[label] = Path(path)
        return judged_verdict(Path(argv[1]), json.loads(Path(argv[2]).read_text()),
                              json.loads(Path(argv[3]).read_text()), objects)
    if len(argv) == 3 and argv[0] == "executables":
        print(json.dumps(declared_executables(Path(argv[1]), argv[2]), sort_keys=True))
        return 0
    if len(argv) == 2 and argv[0] == "published-check":
        files = tree_files(Path(argv[1]))
        found = [f"{path}: {defect}" for path in sorted(files)
                 for defect in elf_published_defects(files[path])]
        for line in found:
            print(line)
        return 1 if found else 0
    if len(argv) == 2 and argv[0] == "content-manifest":
        print(content_manifest(Path(argv[1])))
        return 0
    if len(argv) == 3 and argv[0] == "content-manifest-rows":
        print(manifest_rows_digest(Path(argv[1]), argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "toolchain":
        Path(argv[2]).write_text(json.dumps(measure_toolchain(Path(argv[1])), sort_keys=True))
        return 0
    if len(argv) >= 6 and argv[0] == "verdict-step":
        return verdict_step(Path(argv[1]), argv[2], Path(argv[3]), Path(argv[4]),
                            [Path(repository) for repository in argv[5:]])
    if len(argv) in (9, 10, 11) and argv[0] == "conan-step":
        return conan_step(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5],
                          Path(argv[6]), Path(argv[7]), tuple(argv[8].split()),
                          Path(argv[9]) if len(argv) >= 10 else None,
                          Path(argv[10]) if len(argv) == 11 else None)
    if len(argv) == 11 and argv[0] == "build-step":
        return build_step(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5],
                          Path(argv[6]), Path(argv[7]), tuple(argv[8].split()), argv[9],
                          Path(argv[10]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyError_ as refused:
        sys.exit(f"artefact_store: refused — {refused}")
