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
        (DN-142.D21 (1)), whose red is that record's verdict and never this command's exit; and,
        once every record of the step is stored or matching, its `step-outcome` record naming them
        by key, the one record `serve` serves the step from. Its key's `step.form` is MALF_STEP_FORM
        (`writer` or `ship`, refused unset, DN-142.D31 (1)); a ship-form step whose results hold a
        result of the package's own tests is refused and stores nothing
    artefact_store.py source-key <conan home> <graph info.json> <package> <profile name> <malf dir>
                                 <prefixes> <reference> <document out>
        the key of a package's SOURCE VERDICT (DN-142.D31 (1)), one line of 64 hex, its document written
        to <document out>: from `conan graph info` over its recipe path at the dev profile, in a home
        holding every first-party export of its closure and every third-party recipe; <reference> is the
        `<ref>#<rrev>` its own export gave
    artefact_store.py source-serve <store> <source-key document>
        exit 0 printing SERVED when the store holds a passing source verdict at that key from a counted
        domain, 3 printing EXECUTES and why otherwise (always under MALF_STORE_REJUDGE=1)
    artefact_store.py source-verdict <store> <source-key document> <junit> <package source dir>
        store or compare the source verdict over the dev leg's JUnit file for the package (exit as
        conan-step's: 0 stored or matching, 1 differing at an equal key)
    artefact_store.py binding <store> <package record key> <source-verdict record key>
        DN-142.D31 (1)'s binding: EQUAL-INPUTS (exit 0) when a ship-form package record and a source
        verdict name one package over equal sources, malf tree, image and third-party `ref#rrev` set;
        UNEQUAL naming each member (exit 1); 2 when a record is absent
    artefact_store.py shipped <checkout>
        the packages a release of <checkout> ships, from its packages.yml at HEAD, one per line: each
        `release: true` package and each package with a `published` executable (an image's binary)
    artefact_store.py bound <store> <package>...
        each package has exactly one ship-form create record and one PASSING source verdict, EQUAL-INPUTS
        (`binding`); a line each, exit 0 all bound, 1 any not: what a candidate's store job pushes over
    artefact_store.py ship-equal <store>
        gate G-ship (DN-142.D31 (1)): each ship-form create paired with the writer-form create of equal
        inputs but the form, EQUAL when package id, revision and content digest agree; exit 0 when a
        pair exists and every ship-form record is paired and equal
    artefact_store.py key-first <store> <conan home> <graph info.json> <package> <profile name>
                                <malf dir> <toolchain.json> <prefixes> <document out>
                                reference=<ref>#<rrev>|tree=<source tree id>
        the step's key BEFORE it runs (DN-142.D19 (4)), one line of 64 hex, its document written
        to <document out>: from the graph `conan graph info` resolved over the recipe path the step
        builds (a create's, its source the exported reference's digest, in the context `conan
        create` gives it; a `conan build`'s materialized source, its source that tree), each
        first-party upstream's digest read from its own step's record; exit 1 refusing an upstream
        no record holds. A step handed that document in MALF_STEP_KEY_FIRST
        refuses its record when the key it computes after the build differs, naming each member
        (gate G2), and prints `KEY-FIRST <package> equal <key>` when it agrees
    artefact_store.py third-party-plan <store> <conan home> <graph info.json> <prefixes> <profile name>
        each third-party reference a first-party step's resolved graph needs built by a step of its
        own, `<name>/<version>` a line (DN-142.D17 (2), (4), (10)): one conan marks Missing, Build
        or Download, and one present at a content no third-party-create record at the profile
        holds; exit 1 refusing a python_requires or a node state no step can address
    artefact_store.py third-party-step <store> <conan home> <install graph.json> <prefixes>
                                      <profile name> <malf dir> <toolchain.json>
        one `third-party-create` record per third-party node the install built, in dependency
        order, keyed by its reference, package id, profile, malf's tree, its recipe's export, its
        dependencies' contents (each held by a record, or refused), the toolchain, the profile
        and the system; its output the whole package folder, compared at an equal key, and on a
        new key a RESTORE-ONLY transport (`conan cache save`, no sources) kept beside it, never
        compared and never published. Exit 0 stored or matching, 3 a mismatch, 1 a refusal
        (DN-142.D17 (2))
    artefact_store.py restore-document <store> <conan home> <graph.json> <prefixes> <profile name>
                                       <document out> needed|built [<name>/<version>...]
        the restore list of a graph as a document of `upstream` and `third_party` members, the
        shape a key-first document has (DN-142.D17 (2), amended): `needed`, every node the graph
        needs a binary of except the named references, which the step will build (a first-party
        node read from its own step's record, a third-party one from the record holding its
        folder); `built`, every node the graph built, read from its folder. Exit 1 refusing a
        needed node with no binary or one no record holds
    artefact_store.py restore <store> <conan home> <document.json> <ledger> <subject>
                              [<recipes home> <graph.json>]
        THE RESTORE (DN-142.D25 (5) call F, DN-142.D17 (2) amended): each member of the document
        resolved to the record at its profile holding its content AND a transport (the smallest
        key on a tie), every transport restored into the home in one conan process, after the
        graph's recipes from <recipes home> when named (`conan cache save --no-source`, recipes
        only, never a binary); then each restored folder checked against its record: the tree
        digest of the folder conan packaged equals the record's `packaged` (its `content` when
        it has none), the cache's reference, package id and package revision equal the record's
        alias. One ledger line per call (nodes, transport bytes, seconds). Exit 0 restored; 1
        refused, naming each node with no restorable transport or each RESTORE failure (a
        store-integrity fault, never a mismatch event); 2 STOPPED, a kept record whose transport
        object is absent (a corrupt store)
    artefact_store.py restore-ledger <ledger>
        the walk's restore totals, one line: calls, nodes, transport bytes, seconds
    artefact_store.py serve <store> <walk home> <key-first.json> <package> <profile name> <ledger>
                            [<corpus gates>]
        REUSE (N359, DN-142.D19; plan H13): the step whose key the key-first document gives is
        SERVED in place of executing when its `step-outcome` record (written last by `conan-step`
        and `build-step`, naming every record the step wrote) and each record it names are stored,
        were produced in their domain with their denials counted at zero, and judged `pass`: its
        package restored into the walk's home from its transport, each record printed `REUSED`.
        MALF_STORE_REJUDGE=1 serves nothing. Exit 0 served; 3 it must execute (why printed); 1 or 2
        from the restore
    artefact_store.py transports <store> <profile name>
        each `third-party-create` record at the profile with its transport's size in bytes, one
        `<alias> <bytes>` line, then the per-profile sum and the count of records holding none
    artefact_store.py upstreams <store>
        every create or build record whose inputs name an `upstream` package digest that no record
        of that package's OWN create step at the same profile holds, one line per record with each
        unrecorded upstream, and every such record whose `third_party` names a content no
        third-party-create record at its profile holds (DN-142.D17 (4)); exit 0 when there is
        none, 1 otherwise (DN-142.D16), read-only
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
        no package and judges the upstream packages the build linked; then its `step-outcome`
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
        (DN-142.D22), as canonical JSON; `{}` when it declares none; exit 2 refusing a malformed row
        or a package packages.yml does not list (RUL-200)
    artefact_store.py published-check <folder>
        each linked ELF file under <folder> whose bytes are not in the published form, one
        `<path>: symbol table|runpath` line; exit 1 when there is one
    artefact_store.py tracked-step <store> <repository dir> <commit> <package>
        the `<package>/tracked` step (DN-142.D25 (1)): each `tracked/<member>` object the package
        declares in packages.yml at <commit>, read as a git blob at that commit and kept as an
        object; keyed on the blob ids, stored when the key is new, matched when it is not; exit 1
        refusing a member that is no blob there, or a package declaring no tracked object
    artefact_store.py ingest-rows <repository dir> <commit>
        each `ingest:` row of packages.yml at <commit>, `<workflow>\t<artifact>\t<member>\t<class>` per line
    artefact_store.py candidate-ingest <store> <repository dir> <commit> <workflow file> <artifact dir> <run id> <attempt> <GitHub artifact digest>
        a CANDIDATE's ingestion (DN-142.D33 (3)): the row's member, read from the artifact a job of this candidate's
        own run uploaded, stored as an object and the record `<repository>/ingest.<stem>`; prints its key
    artefact_store.py tracked-packages <repository dir> <commit>
        each package declaring a `tracked/<member>` object in packages.yml at <commit>, one per line,
        sorted: the packages a candidate's store job runs `tracked-step` for (DN-142.D33 (3))
    artefact_store.py ingest <store> <step-0 run id> <reading.json>  < <the object's bytes>
        THE ONE INGESTION WRITE (DN-142.D20 (2), DN-142.D25 (3)), B5's alone: the bytes on stdin
        and what B5 read about them, `{repository, workflow, artefact, member, class, job, run: {id,
        attempt}, judged, workflow_blob, head_sha, title, seat: {runner, labels}, producers:
        {printed, github_artifact_digest}, exception: {since, discharge}}`, become an object and the
        record of `<repository>/ingest.<workflow stem>`, `class` (`executable` or `object`) the
        `ingest:` row's declaration, refused when the bytes contradict it (DN-142.D25 (4) A); exit 0
        stored, matching, or diverging
        from an earlier run at an equal `inputs_key` (written under `divergences/`, never a red),
        1 refused with nothing stored
    artefact_store.py parameters <profile> <argv>...
        the act's parameter document (DN-142.D19 (1)), canonical JSON on one line: MALF_STORE_PARAMETERS
        when a driver hands one, checked equal to the act's argv and profile, every `env` member equal
        to the act's environment; else the act's own, `env` empty. Exit 2 refusing a document that
        disagrees with the act it keys
    artefact_store.py object <store> <key> <output> <member>
        the SHA-256 of object <member> of <output> in the record at <key>, once the store holds its
        bytes whole; exit 1 naming the missing link (DN-142.D25 (1))
    artefact_store.py selftest
        every arm of the definition member, the objects convention, the tracked step and the
        ingestion API, on fixtures
    artefact_store.py toolchain <compiler root> <out.json>
        the measured toolchain member, written once per run (hashing the compiler tree is the
        expensive part of a key)

A package record's output carries `build_ids` (DN-142.D15): for every linked ELF file of the
package tree (an executable or a shared object) its GNU build-id, the NT_GNU_BUILD_ID note the
linker writes over the file's own bytes. It is READ FROM THE STORED TRANSPORT archive, never taken
from the create's folder, so it indexes exactly the bytes the store keeps; it is derived from bytes
the content digest covers, so it is an index, never compared and never a key member. A first-party
package one of whose linked ELF files carries no note is refused before anything is stored: the
index is total over what ships, or a build answering with an id would find no record.

A THIRD-PARTY BINARY IS THE OUTPUT OF A STEP OF ITS OWN (DN-142.D17): a `third-party-create`
record per configuration (`<ref>#<rrev>:<package id>`) the writer consumes, keyed by its reference,
package id and profile, malf's tree, its recipe's export digest, its dependencies' contents, the
toolchain, the profile and the system; its output the WHOLE package folder, compared at an equal key
like any package, plus `packaged`, the digest of the folder conan packaged (the immutable `p` a
`finalize()` recipe derives its consumers' folder from; the folder itself otherwise), compared too.
On a new key it keeps a RESTORE-ONLY transport (DN-142.D17 (2), amended 2026-10-14): never a key
member, never compared, never published, its single reader the per-step home's restore. A package
record (`conan-create` or `third-party-create`) offered with no transport to keep is refused at
commit, and the transport shares its record's fate. A first-party step's create builds only its own package
(a third-party node it built or downloaded refuses the step), and a step whose `third_party` names a
content no `third-party-create` record at its profile holds is refused; when a record holds that
configuration at another content, the refusal names the folder and the files written into it after
its step judged it. A graph carrying a `python_requires` is refused by every key derivation.
A third-party step also refuses a package folder that differs from its own `conanmanifest.txt`, the
md5 conan wrote when it packaged it, naming each file written after packaging, so a folder a later
build of the same install wrote into is never recorded as the package's own bytes.

A step's TEST VERDICT is a record of its own (DN-142.D5 (3), DN-142.D7), never a field of the
package record: the consumer's create runs no test, so a package record holding results could never
compare equal to the writer's. Its key is the build step's key plus the selection (the test helper's
blob id and its label expression); its outputs are the verdict, the result set (each population,
test name and outcome, sorted; the JUnit files of malf's helper, `<package>/<package id>.xml` for
the build and `<package>/<package id>.test_package.xml` for the test_package), `judged` (the content digests the tests judged:
the package's, or for a `stored: false` step the upstream packages it linked) and the logs as an
object, never compared. A rebuild at an equal key compares the verdict, the result set and `judged`,
never a timing.

A step's test verdict is `fail` on a failed row OR on an undeclared skip or a declaration defect
(DN-142.D23): every skipped or disabled row of the build or test_package population must be named,
with its outcome and the step's profile, by a row of the package's `tests/declared_skips.json`,
read from the create's keyed export_source folder (a `conan build` step's from the materialized
source MALF_STEP_SOURCE names). The covered rows are kept as `excused` and printed as EXCUSED.

A step's CORPUS VERDICT (DN-142.D21) is a third record, output name `corpus`, written when the
corpus gates parameter document names the package: its key is the build step's plus the selection
`-L corpus` with the legs and their suites, plus `data`, each mount role's content digest (never
its path); its rows are `[corpus-<leg>, test name, outcome]`, the outcome one of `passed`,
`failed`, `skipped`, `disabled`, `absent` (a named suite that selected no test, or ran none) and
`unguarded` (a test ctest could not see skip). Its verdict is `pass` only when every row is
`passed`: a skipped corpus gate is the green-blind state the corpus run exists to refuse.

A step's EXECUTABLES (DN-142.D22) are classified by its package's `executables:` map in the
recorded `packages.yml` (`published` or `run-from-cache`), which `malf store-create` hands over in a
JSON file named by MALF_STEP_EXECUTABLES and whose CLASS projection (DN-142.D24 (2): a `published`
row may name its publication sites, `{published: {<site kind>: ...}}`, which never enter a key)
enters the create's key as `step.executables`
(`{}` when none, the meaning of an unset variable too). The store REFUSES, before the record, an
executable entry the map does not name, a map key naming no executable entry, and a `published`
ELF carrying a symbol table (`.symtab` or `.debug_*`), a runpath (DT_RPATH or DT_RUNPATH) or no
build-id; a `run-from-cache` entry keeps its runpath.

A step's BACKENDS (DN-142.D30 (5) E2b): a package whose tests need a server declares it under
`backends:` in the recorded `packages.yml`; `malf store-create` hands the declaration over in a JSON file
named by MALF_STEP_BACKENDS, and a non-empty one enters the step's key as `step.backends` (absent for a
step declaring none), so a backend's image bump re-keys exactly the steps that declare it.

A step other than a package create may carry, in an output, OBJECTS (DN-142.D25 (1)): `objects:
{<member>: <sha256>}`, each member held under `objects/<sha256>` (a verdict's named files are this
field). Two such steps live here. The TRACKED step `<package>/tracked` holds a package's committed
`tracked/<member>` files, keyed on their git blob ids and judging nothing. The INGESTION record
`<repository>/ingest.<workflow stem>` holds a hosted output B5 verified: its key carries the run (an
observation, never reused), its output the object, the three digests that agreed (the job's printed
one, GitHub's artifact digest, the writer's recomputation), the seat, `head_sha`, the run title naming
this step-0 run, the dated exception and `inputs_key`; an executable among those bytes is judged
published (DN-142.D22 (4): a PE with no COFF symbol table and no CodeView debug entry, a linked ELF
in the published form). `object` resolves a row: step -> record -> output -> member -> bytes.

A key's `definition` is malf WHOLE, the git tree of the malf the step runs (DN-142.D29 (2): a malf
commit re-keys every step), plus the PARAMETER DOCUMENT of the act that ran the step
(DN-142.D2, DN-142.D19 (1)): `{argv, env, profile}`, the program and verb, each variable the driver
hands the act that is not a location, and the profile name. A driver hands it in
MALF_STORE_PARAMETERS and it is CHECKED against the act it keys (`parameters`); an act no driver ran
composes the same document itself with `env` empty. A driver's own code never enters it: a Pharos
commit re-keys no step malf runs.

The store is a directory: `objects/<sha256>` immutable blobs, `records/<key>.json` one per key,
`mismatches/<key>.<digest>.json` one per differing rebuild or re-judgement, `divergences/<inputs key>.<key>.json`
one per ingestion whose bytes differ from an earlier run's at equal inputs. It stands in for the remote store
until its transport and its writer credential are ruled (DN-142.D6, R1 to R5).
"""

from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
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

    post: mode is `file`, `executable` (the OWNER's execute bit, the one git records) or `symlink` (its SHA-256
    is that of the link's target text, never of what it points at); a directory contributes only its contents.
    note: a group or other execute bit is no member: a POSIX ACL raises the group bits through its mask, so on a runner whose home carries an ACL (malf-runner's grants the desk account r-x) every copied file read as executable, and one recipe exported there and on the desk had two digests (measured 2026-10-29, logcraft_core, 183 of 233 export sources at 654)
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
                mode = "executable" if info.st_mode & stat.S_IXUSR else "file"
                entries.append([relative, mode, file_sha256(path)])
            else:
                raise KeyError_(f"{path}: neither a regular file nor a symlink")
    return sorted(entries)


class EscapingSymlink(ValueError):
    """A symlink under a named root whose resolved target lies outside that root (DN-142.D21 (2))."""


def content_manifest(root: Path) -> str:
    """DN-142.D21 (5)'s content digest of a data root: SHA-256 over the canonical JSON of the sorted
    `[relative path, sha256]` of every regular file under it, symlinks followed; a file root is the
    one row `[".", sha256]`. No mtime, size or inode enters it, so a same-size rewrite moves it.
    A named root is symlink-closed (DN-142.D21 (2)): a link under it resolving outside it raises
    EscapingSymlink, naming the link and its target, since that read is one no role names."""
    if root.is_file():
        return key_of([[".", file_sha256(root)]])
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is neither a file nor a directory")
    closure = root.resolve()
    rows = []
    for directory, subdirectories, names in os.walk(root, followlinks=True):
        subdirectories.sort()
        for name in [*subdirectories, *names]:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve().is_relative_to(closure):
                raise EscapingSymlink(f"{path} -> {path.resolve()} resolves outside the named root {closure}")
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


PARAMETERS_ENV = "MALF_STORE_PARAMETERS"
PARAMETER_MEMBERS = ("argv", "env", "profile")


def parameter_document(argv: list[str], profile: str | None) -> dict:
    """The parameter document of the act `argv` runs at `profile` (DN-142.D19 (1)): the one its
    driver handed in MALF_STORE_PARAMETERS, checked against the act, else the act's own with `env`
    empty. `argv` is the program and its verb, never a path nor a package selection: a selection
    chooses which steps run, never what one computes.
    post: the document's argv and profile are the act's and each `env` member is the act's own
    environment's; raises KeyError_ naming every disagreement otherwise."""
    composed = {"argv": list(argv), "env": {}, "profile": profile}
    handed = os.environ.get(PARAMETERS_ENV)
    if handed is None:
        return composed
    try:
        document = json.loads(handed)
    except ValueError as unparsed:
        raise KeyError_(f"{PARAMETERS_ENV} is not JSON: {unparsed}") from unparsed
    if not isinstance(document, dict) or sorted(document) != sorted(PARAMETER_MEMBERS):
        raise KeyError_(f"{PARAMETERS_ENV} holds {sorted(document) if isinstance(document, dict) else document!r}, "
                        f"not exactly {list(PARAMETER_MEMBERS)}")
    wrong = [f"{member} is {document[member]!r}, the act's is {composed[member]!r}"
             for member in ("argv", "profile") if document[member] != composed[member]]
    environment = document["env"]
    if not isinstance(environment, dict):
        wrong.append(f"env is {environment!r}, not an object")
    else:
        wrong += [f"env {name} is {value!r}, the act was handed {os.environ.get(name)!r}"
                  for name, value in sorted(environment.items()) if os.environ.get(name) != value]
    if wrong:
        raise KeyError_(f"{PARAMETERS_ENV} disagrees with the act it keys: " + "; ".join(wrong))
    canonical_json(document)
    return document


def step_parameters(profile: str | None) -> dict:
    """The parameter document a step's act exported (`parameters`), its profile the step's own;
    an act no malf verb ran (a fixture calling a step directly) has the empty argv."""
    handed = os.environ.get(PARAMETERS_ENV)
    if handed is None:
        return {"argv": [], "env": {}, "profile": profile}
    document = json.loads(handed)
    if document.get("profile") != profile:
        raise KeyError_(f"{PARAMETERS_ENV} names profile {document.get('profile')!r}, and the step "
                        f"runs at {profile!r}")
    return document


# Provenance, never a key member and never compared (DN-142.D29 (8), ruled (c)): whether the act that
# produced a record ran in its Landlock domain with its denials counted at zero. Every record carries
# it, false unless its act's tally (MALF_STEP_TALLY, written by `malf_closure.py confine`) says so: an
# act no domain confined (a Pharos verdict act today, a declared-unconfined package's) is false, explicitly.
DENIALS_FIELD = "denials_counted"
TALLY_ENV = "MALF_STEP_TALLY"


def step_denials_counted() -> bool:
    """`denials_counted` for the record an act's step commits now: its tally's word, false without one."""
    named = os.environ.get(TALLY_ENV)
    return _closure().denials_counted(Path(named) if named else None)


def servable(record: dict) -> str | None:
    """None when a stored record may be SERVED in place of executing its step (N359's reuse), else why
    not: a record whose act did not run in its domain with its denials counted at zero is never served,
    and an absent field reads as false (DN-142.D29 (8), ruled (c)). Comparing a rebuild against it is
    always allowed."""
    if record.get("produced_by", {}).get(DENIALS_FIELD) is True:
        return None
    return (f"the record {record.get('key')} was produced by an act whose reads were not counted in its domain "
            f"({DENIALS_FIELD} is {record.get('produced_by', {}).get(DENIALS_FIELD)!r}), so it re-executes")


def _closure():
    """malf's step domain and its tally reader, beside this file (`malf_closure.py`, DN-142.D29 (8))."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import malf_closure

    return malf_closure


def definition_member(malf_dir: Path, parameters: dict) -> dict[str, object]:
    """DN-142.D2's `definition` member: malf's tracked tree as the step runs it and the act's parameter
    document, and nothing of the driver's own code (DN-142.D19 (1)). The tree is malf WHOLE
    (DN-142.D29 (2)): every malf byte the step can execute is in it, so no derivation can miss one."""
    return {"malf": worktree_tree_id(malf_dir), "parameters": parameters}


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


# THE `system` MEMBER IS THE BUILD ENVIRONMENT'S PINNED CONFIG DIGEST (DN-142.D30 (1), row E4): `{image: <digest>}`,
# from two producers that must agree. MALF_SYSTEM is handed to a container by malf's launcher only after the
# daemon reported the pinned ID and that config digest for it (build_environment.py `check`); the pin is in
# malf's tree, which every key names whole as its `definition`. The host's package list is in no key.
SYSTEM_ENV = "MALF_SYSTEM"
ENVIRONMENT_PIN = Path(__file__).resolve().parent / "config" / "build-environment.json"
CONFIG_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def system_member(environ: "dict[str, str] | None" = None, pin: Path = ENVIRONMENT_PIN) -> dict:
    """`{image: <the pinned config digest>}`. raises Refused outside the build environment (no MALF_SYSTEM, or
    one that is no config digest, the retired `host` attestation included) and when MALF_SYSTEM is not the pin."""
    handed = (os.environ if environ is None else environ).get(SYSTEM_ENV, "")
    if not CONFIG_DIGEST.match(handed):
        raise Refused(f"no system member outside the build environment: {SYSTEM_ENV} is {handed!r}, not the config "
                      f"digest malf's launcher hands an act it started (DN-142.D30 E4)")
    try:
        pinned = json.loads(pin.read_text(encoding="utf-8")).get("image")
    except (OSError, ValueError, AttributeError) as unread:
        raise Refused(f"the build-environment pin {pin} could not be read: {unread}") from unread
    if handed != pinned:
        raise Refused(f"{SYSTEM_ENV} is {handed}, and the pin {pin} names {pinned!r}: the act ran in another image "
                      f"than the one this malf pins (DN-142.D30 E4)")
    return {"image": handed}


WRITER_PROFILE = Path("config") / "writer.profile"


def profile_member(home: Path, profile: str, malf_dir: Path, writer: bool = True) -> str:
    """DN-142.D2's `profile` member: `conan profile show` at the step's profile with malf's writer
    layer composed over it, as every writer act composes it (DN-142.D17 (8)), or alone for the dev
    leg's source verdict (`writer` false), which composes none, with the two
    absolute prefixes it can carry (the conan home, malf's checkout) replaced by fixed tokens;
    what those files contain is keyed by `definition` (malf's tree) and nothing else."""
    layers = [str(malf_dir / WRITER_PROFILE)] if writer else []
    shown = _run(["conan", "profile", "show", *(f"--profile:{context}={name}" for context in ("host", "build")
                                                 for name in (profile, *layers))],
                 {**os.environ, "CONAN_HOME": str(home)})
    for prefix, token in ((str(home), "<conan-home>"), (str(malf_dir), "<malf>")):
        shown = shown.replace(prefix, token)
    return hashlib.sha256("\n".join(line.rstrip() for line in shown.splitlines()).encode()).hexdigest()


def _owned(name: str, prefixes: tuple[str, ...]) -> bool:
    return name.startswith(prefixes)


def _cache_path(home: Path, reference: str, folder: str | None = None) -> Path:
    argv = ["conan", "cache", "path", reference] + ([f"--folder={folder}"] if folder else [])
    return Path(_run(argv, {**os.environ, "CONAN_HOME": str(home)}).strip())


def _export_digest(home: Path, recipe: str) -> str:
    """A recipe's exported set: the export and export_sources folders together, the second None
    when the recipe has none.

    note: the export_sources folder is the export folder's sibling `es`; `conan cache path
          --folder=export_source` exits non-zero when it is absent, as for a recipe a remote served
          (measured on conan 2.28.1, cmake/4.4.4 from conancenter)."""
    export = _cache_path(home, recipe)
    parts = {"export": tree_digest(export)[0] if export.is_dir() else None,
             "export_source": tree_digest(export.parent / "es")[0] if (export.parent / "es").is_dir() else None}
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
# DN-142.D23: a `conan build` step's materialized source root, where its declared skips are read
# (a create's are read from its keyed export_source folder instead).
STEP_SOURCE_ENV = "MALF_STEP_SOURCE"
# DN-142.D19 (4), gate G2: the document `key-first` computed BEFORE the step ran, which the step's
# record must equal once it has run.
KEY_FIRST_ENV = "MALF_STEP_KEY_FIRST"
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


# DN-142.D22 (4): a PE image's published form, read from its bytes like the ELF one. The COFF file
# header names a symbol table (PointerToSymbolTable, NumberOfSymbols), and the debug data directory
# may hold an IMAGE_DEBUG_TYPE_CODEVIEW entry naming the PDB by the build host's absolute path.
MZ_MAGIC = b"MZ"
PE_SIGNATURE = b"PE\0\0"
DOS_LFANEW_AT = 0x3C
COFF_HEADER_BYTES = 20
IMAGE_FILE_DLL = 0x2000
PE32_MAGIC, PE32_PLUS_MAGIC = 0x10B, 0x20B
DIRECTORY_COUNT_AT = {PE32_MAGIC: 92, PE32_PLUS_MAGIC: 108}
DEBUG_DIRECTORY = 6
DIRECTORY_BYTES = 8
SECTION_HEADER_BYTES = 40
DEBUG_ENTRY_BYTES = 28
IMAGE_DEBUG_TYPE_CODEVIEW = 2


def pe_executable(data: bytes) -> bool:
    """True for a PE IMAGE that is an executable: `MZ`, a `PE\\0\\0` signature at `e_lfanew`, and no
    IMAGE_FILE_DLL flag (DN-142.D22 (4)); a DLL or a truncated header is not one."""
    if not data.startswith(MZ_MAGIC) or len(data) < DOS_LFANEW_AT + 4:
        return False
    at = struct.unpack_from("<I", data, DOS_LFANEW_AT)[0]
    if data[at:at + 4] != PE_SIGNATURE or len(data) < at + 4 + COFF_HEADER_BYTES:
        return False
    return not struct.unpack_from("<H", data, at + 22)[0] & IMAGE_FILE_DLL


def pe_published_defects(data: bytes) -> list[str]:
    """What keeps a PE executable's bytes from the published form: `symbol table` (a non-zero
    PointerToSymbolTable or NumberOfSymbols) and `PDB reference` (a CodeView debug entry); a header
    that cannot be walked is `unreadable PE` and its reason. Empty for bytes that are no PE executable."""
    if not pe_executable(data):
        return []
    at = struct.unpack_from("<I", data, DOS_LFANEW_AT)[0] + len(PE_SIGNATURE)
    defects = []
    try:
        sections, _stamp, symbols_at, symbols = struct.unpack_from("<HIII", data, at + 2)
        optional_bytes = struct.unpack_from("<H", data, at + 16)[0]
        if symbols_at or symbols:
            defects.append("symbol table")
        optional = at + COFF_HEADER_BYTES
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic not in DIRECTORY_COUNT_AT:
            return defects + [f"unreadable PE: optional header magic {magic:#x}"]
        count_at = DIRECTORY_COUNT_AT[magic]
        if struct.unpack_from("<I", data, optional + count_at)[0] <= DEBUG_DIRECTORY:
            return defects
        rva, size = struct.unpack_from("<II", data, optional + count_at + 4 + DEBUG_DIRECTORY * DIRECTORY_BYTES)
        if not size:
            return defects
        table = optional + optional_bytes
        offset = None
        for index in range(sections):
            virtual_size, virtual, raw_size, raw = struct.unpack_from(
                "<IIII", data, table + index * SECTION_HEADER_BYTES + 8)
            if virtual <= rva < virtual + max(virtual_size, raw_size):
                offset = rva - virtual + raw
                break
        if offset is None or offset + size > len(data):
            return defects + [f"unreadable PE: the debug directory at RVA {rva:#x} lies in no section"]
        for cursor in range(offset, offset + size - DEBUG_ENTRY_BYTES + 1, DEBUG_ENTRY_BYTES):
            if struct.unpack_from("<I", data, cursor + 12)[0] == IMAGE_DEBUG_TYPE_CODEVIEW:
                defects.append("PDB reference")
                break
    except struct.error as error:
        defects.append(f"unreadable PE: {error}")
    return defects


def ingested_defects(data: bytes) -> list[str]:
    """DN-142.D22 (4): an ingested object exists only to be published, so one whose bytes are an
    executable is judged `published` with no declaration: a PE executable by `pe_published_defects`,
    a linked ELF file by `elf_published_defects` and its build-id. Empty for any other bytes."""
    if pe_executable(data):
        return pe_published_defects(data)
    try:
        elf_build_id(data)
    except NoBuildId:
        return elf_published_defects(data) + ["no build-id"]
    return elf_published_defects(data)


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


def executable_class(value: object) -> str | None:
    """An `executables:` row's CLASS (DN-142.D24 (2)): the bare class, or `published` for the site
    form `{published: {<site kind>: ...}}`, whose sites are the publication plan's and never a key
    member; None for any other shape."""
    if isinstance(value, str):
        return value if value in EXECUTABLE_CLASSES else None
    if isinstance(value, dict) and list(value) == ["published"] and isinstance(value["published"], dict):
        return "published"
    return None


def executable_classes(declared: dict) -> dict[str, str]:
    """The class projection `{entry: published | run-from-cache}` of a declaration, the only part
    of it a create's key holds (DN-142.D24 (2)): renaming an asset or adding a site never re-keys."""
    return {path: executable_class(value) for path, value in sorted(declared.items())}


def listed_entry(text: str, package: str, manifest: Path, read_at: str) -> dict:
    """The entry `package` has in `text`, the `packages.yml` at `manifest` as read `read_at` ("at HEAD", or
    "in its working tree"). Every step malf walks is listed there (RUL-200): raises ValueError naming the
    step and the file when it lists no entry for it, so a step nobody declared is refused at its first
    read, before anything of it is built. The one reader of that rule, for every packages.yml read of a
    step (`declared_entry` here, `build_environment.declared_backends`)."""
    import yaml

    packages = (yaml.safe_load(text) or {}).get("packages") or {}
    if package not in packages:
        raise ValueError(f"{package}: a step {manifest} does not list {read_at}, and malf builds no step "
                         "that no packages.yml declares (RUL-200)")
    return packages[package] or {}


def declared_entry(package_dir: Path, package: str) -> dict:
    """The entry `package` has in the `packages.yml` of the repository holding `package_dir`, read from
    its RECORDED commit's blob (HEAD), never the disk (`listed_entry`: an unlisted step raises
    ValueError naming it, RUL-200)."""
    top = Path(_run(["git", "-C", str(package_dir), "rev-parse", "--show-toplevel"]).strip())
    return listed_entry(_run(["git", "-C", str(top), "show", "HEAD:packages.yml"]), package,
                        top / "packages.yml", "at HEAD")


def declared_executables(package_dir: Path, package: str) -> dict:
    """The `executables:` map `package` declares in its packages.yml entry at HEAD (declared_entry), `{}`
    when its entry declares none. A row is a bare class or the site form (DN-142.D24 (2)), returned whole.
    Raises ValueError on a row that is neither, and on a step packages.yml does not list."""
    declared = declared_entry(package_dir, package).get("executables") or {}
    wrong = {path: kind for path, kind in declared.items() if executable_class(kind) is None}
    if wrong:
        raise ValueError(f"{package}: executables neither classed {' nor '.join(EXECUTABLE_CLASSES)} "
                         f"nor in the site form {{published: {{<site kind>: ...}}}}: "
                         + ", ".join(f"{path}: {kind}" for path, kind in sorted(wrong.items())))
    return dict(sorted(declared.items()))


def declared_unconfined(package_dir: Path, package: str) -> str:
    """The `unconfined: {why: <reason>}` `package` declares beside its `executables:` in its packages.yml
    entry at HEAD (declared_entry): its `why`, or "" when it declares none (DN-142.D29 ruled (v): a package
    whose tests change mount topology, which no Landlock domain allows). Raises ValueError on a declaration
    that is not a mapping naming a non-empty `why`, and on a step packages.yml does not list."""
    entry = declared_entry(package_dir, package)
    if "unconfined" not in entry:
        return ""
    declared = entry["unconfined"]
    if not isinstance(declared, dict) or set(declared) != {"why"} or not str(declared.get("why") or "").strip():
        raise ValueError(f"{package}: `unconfined:` is a mapping holding exactly a non-empty `why:`, not {declared!r}")
    return " ".join(str(declared["why"]).split())


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


def _alias(node: dict) -> tuple[str, str, str]:
    """(recipe `<ref>#<rrev>`, configuration `<recipe>:<package id>`, binary `<configuration>#<prev>`)."""
    recipe = f"{node['ref'].split('#')[0]}#{node['rrev']}"
    configuration = f"{recipe}:{node['package_id']}"
    return recipe, configuration, f"{configuration}#{node['prev']}"


def refuse_python_requires(nodes: list[dict], graph_json: Path) -> None:
    """Exit naming each node that carries a `python_requires`: recipe code no graph node is, so no
    key member names it, and a key would hold it silently by omission (DN-142.D17 (11))."""
    carried = sorted(f"{node['ref'].split('#')[0]} carries {', '.join(sorted(node['python_requires']))}"
                     for node in nodes if node.get("python_requires"))
    if carried:
        sys.exit(f"artefact_store: REFUSED the graph {graph_json}: {len(carried)} node(s) carry a "
                 "python_requires, recipe code that is no graph node and so no member of any key "
                 "(DN-142.D17 (11)): " + "; ".join(carried))


def _step_inputs(home: Path, graph_json: Path, package: str, prefixes: tuple[str, ...],
                 with_root: bool) -> tuple[dict, dict, dict, tuple[str, Path] | None, dict]:
    """(sources, upstream, third_party, the target's (binary, folder), each third-party node's
    folder) of a step's graph."""
    nodes = graph_nodes(graph_json)
    refuse_python_requires(nodes, graph_json)
    sources, upstream, third_party, folders = {}, {}, {}, {}
    target = None
    # note: a `build-scripts` package (a content recipe) is created in the build context alone
    created = "host" if any(node["name"] == package and node["context"] == "host" for node in nodes) \
        else "build"
    for node in nodes:
        name = node["name"]
        recipe, _configuration, binary = _alias(node)
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
            folders[f"{name}/{node['context']}"] = folder
    if with_root and target is None:
        sys.exit(f"artefact_store: the graph {graph_json} holds no node named {package}")
    return sources, upstream, third_party, target, folders


TEST_FOLDER_ENV = "MALF_STEP_TEST_FOLDER"


TRACKED_LIST = "tracked"


def test_package_member() -> str | None:
    """DN-142.D26's `test_package` member: the tree digest of the TRACKED files of the copy of the
    create's test folder MALF_STEP_TEST_FOLDER names, which the create reads; the paths are the ones
    `<copy>/tracked` lists (NUL-separated, relative to the folder), because the create builds its
    test_package inside that folder and its build files are no input. None for `none` (no test
    folder, or a `conan build`, which runs none). raises Refused when the variable is unset (an absent
    member is never read as null) or a listed path is gone (the create removed an input it read)."""
    named = os.environ.get(TEST_FOLDER_ENV)
    if named is None:
        raise Refused(f"{TEST_FOLDER_ENV} names neither the copy of the step's test folder nor `none`, "
                      "and a step's test_package member is never assumed null (DN-142.D26)")
    if named == "none":
        return None
    folder = Path(named)
    listed = {path for path in (folder.parent / TRACKED_LIST).read_bytes().decode().split("\0") if path}
    entries = [entry for entry in tree_entries(folder, exclude_root_manifest=False) if entry[0] in listed]
    missing = sorted(listed - {entry[0] for entry in entries})
    if missing:
        raise Refused(f"the test folder {folder} no longer holds {len(missing)} tracked file(s) its key "
                      "names: " + ", ".join(missing[:NAMED_FILES]))
    return hashlib.sha256(canonical_json(entries)).hexdigest()


BACKENDS_ENV = "MALF_STEP_BACKENDS"


def step_backends() -> list:
    """The backends the running step declares (DN-142.D30 (5) E2b), the JSON list MALF_STEP_BACKENDS names
    (`build_environment.py backends`); [] when it names none or the step declares none."""
    named = os.environ.get(BACKENDS_ENV)
    return json.loads(Path(named).read_text()) if named else []


# THE FORM OF A CREATE (DN-142.D31 (1)): `writer`, step 0's create running every test and test_package,
# or `ship`, a candidate's create running its test_package alone (`malf_recipe_tests.CREATE_ARGS`). Two
# acts, so two keys: the form is a member of every `conan-create` step, and through the step's key it
# reaches its verdict's and its outcome's, so an outcome of one form never serves a request of the other.
STEP_FORM_ENV = "MALF_STEP_FORM"
STEP_FORMS = ("writer", "ship")


def step_form() -> str:
    """The form MALF_STEP_FORM names. raises Refused when it is unset or names no form: a create's
    form is never assumed, since a ship act keyed as a writer would serve a writer request with no
    unit-test verdict."""
    named = os.environ.get(STEP_FORM_ENV)
    if named not in STEP_FORMS:
        raise Refused(f"{STEP_FORM_ENV} is {named!r}, not one of {', '.join(STEP_FORMS)}: a create's form "
                      "is a member of its key and is never assumed (DN-142.D31 (1))")
    return named


def _step_document(kind: str, package: str, profile: str, home: Path, malf_dir: Path,
                   toolchain: Path, sources: dict, upstream: dict, third_party: dict) -> dict:
    backends = step_backends()
    return {
        "step": {"kind": kind, "package": package, "profile": profile,
                 **({"form": step_form()} if kind == "conan-create" else {}),
                 **({"backends": backends} if backends else {})},
        "definition": definition_member(malf_dir, step_parameters(profile)),
        "sources": sources,
        "test_package": test_package_member(),
        "upstream": upstream,
        "third_party": third_party,
        "toolchain": json.loads(toolchain.read_text()),
        "profile": profile_member(home, profile, malf_dir),
        "system": system_member(),
    }


# THE KEY BEFORE THE STEP (DN-142.D19 (4)). A key only the build reveals cannot be looked up, so
# each step's key is also computed BEFORE it runs: from `conan graph info` over its export (a
# create) or its materialized source (a `conan build`) at its profile under the lock, its own
# export's digest, each first-party upstream's OUTPUT digest read out of the record its own step
# wrote (never the home's folder), and each third-party binary's folder, which its own record
# already judged. The step then computes its key as it always has, from what it built, and the two
# must be one: a difference is an input only the build reveals, refused naming the members.


def recorded_contents(store: Path, profile: str) -> dict[str, set[str]]:
    """The content digests the `conan-create` records at `profile` hold, by package binary alias
    (`<ref>#<rrev>:<package id>#<prev>`)."""
    held: dict[str, set[str]] = {}
    records = store / "records"
    for path in sorted(records.glob("*.json")) if records.is_dir() else ():
        body = json.loads(path.read_text())
        step = body["inputs"]["step"]
        package = body["outputs"].get("package") or {}
        if step.get("kind") == "conan-create" and step.get("profile") == profile and package.get("alias"):
            held.setdefault(package["alias"], set()).add(package["content"])
    return held


def key_first_document(store: Path, home: Path, graph_json: Path, package: str, profile: str,
                       malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...],
                       anchor: str) -> dict:
    """The step's input document from the graph `conan graph info` resolved, before it ran, over
    the very recipe path its create or build will take. `anchor` is `reference=<ref>#<rrev>` for a
    `conan-create` step (the reference its `conan export` gave, whose export digest is the target's
    source) or `tree=<id>` for a `conan-build` step (its materialized source's git tree). The target
    is the graph's root node, in the context its create gives it: `build` for a `build-scripts`
    package, which `conan create` builds as a tool, `host` otherwise.
    post: every first-party upstream's digest is the one its own step's record holds at its
    binary alias, every third-party content the one the record holding its folder names (its
    `packaged` digest read as its content); raises Refused naming each upstream no record holds,
    or holds at two contents."""
    kind, _equals, value = anchor.partition("=")
    if kind not in ("reference", "tree") or not value:
        raise Refused(f"{package}: the key before the step needs `reference=<ref>` or `tree=<id>`, not {anchor!r}")
    root = json.loads(graph_json.read_text())["graph"]["nodes"]["0"]
    nodes = graph_nodes(graph_json)
    refuse_python_requires([root, *nodes], graph_json)
    held = recorded_contents(store, profile)
    judged = third_party_records(store, profile)
    sources, upstream, third_party = {}, {}, {}
    unrecorded = []
    for node in nodes:
        if node.get("binary") == "Skip":
            continue
        name, label = node["name"], f"{node['name']}/{node['context']}"
        recipe, configuration, binary = _alias(node)
        if not _owned(name, prefixes):
            digest = tree_digest(_cache_path(home, binary))[0]
            third_party[label] = {"ref": recipe, "package_id": node["package_id"],
                                  "content": held_content(judged.get(configuration, []), digest) or digest}
            continue
        sources[label] = _export_digest(home, recipe)
        contents = held.get(binary, set())
        if len(contents) != 1:
            unrecorded.append(f"{label} {binary}: " + (f"{len(contents)} recorded contents" if contents
                                                       else "no record of its own step holds it"))
            continue
        upstream[label] = next(iter(contents))
    if unrecorded:
        raise Refused(f"{package}: the key before the step reads each upstream from its own step's "
                      f"record (DN-142.D19 (4)), and {len(unrecorded)} cannot be read: " + "; ".join(unrecorded))
    if kind == "tree":
        sources[f"{package}/host"] = value
        return _step_document("conan-build", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    context = "build" if root.get("package_type") == "build-scripts" else "host"
    sources[f"{package}/{context}"] = _export_digest(home, value)
    document = _step_document("conan-create", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    document["step"]["executables"] = executable_classes(_declared_executables_env())
    return document


def document_differences(before: object, after: object, where: str = "") -> list[str]:
    """The dotted path of every leaf at which two input documents differ."""
    if isinstance(before, dict) and isinstance(after, dict):
        found = []
        for name in sorted(set(before) | set(after)):
            path = f"{where}.{name}" if where else name
            if name not in before or name not in after:
                found.append(f"{path} (only {'after' if name not in before else 'before'} the step)")
            else:
                found += document_differences(before[name], after[name], path)
        return found
    return [] if before == after else [where or "$"]


def key_first_agrees(document: dict) -> bool:
    """Gate G2 for one step: the document KEY_FIRST_ENV names, computed before the step, against
    the one the step computed after; True when none is named. Prints `KEY-FIRST <package> equal
    <key>` on agreement, and on a difference the refusal naming every differing member."""
    named = os.environ.get(KEY_FIRST_ENV)
    if not named:
        return True
    before = json.loads(Path(named).read_text())
    package = document["step"]["package"]
    if key_of(before) == key_of(document):
        print(f"artefact_store: KEY-FIRST {package} equal {key_of(document)}")
        return True
    differences = document_differences(before, document)
    print(f"artefact_store: REFUSED {package}: the key computed before the step ({key_of(before)}) "
          f"is not the key it computed after ({key_of(document)}), so an input only the build "
          f"reveals moved it, and a key nobody can compute before the build can never be looked up "
          f"(DN-142.D19 (4), gate G2): {len(differences)} member(s): "
          + ", ".join(differences[:NAMED_FILES]) + (", ..." if len(differences) > NAMED_FILES else ""),
          file=sys.stderr)
    return False


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
    """`upstreams`: every step record of the store judged against its own store, read-only: each
    first-party upstream its own step must have recorded (DN-142.D16), and each third-party input
    a `third-party-create` record must hold (DN-142.D17 (4))."""
    flagged = 0
    for path in sorted((store / "records").glob("*.json")):
        body = json.loads(path.read_text())
        document = body["inputs"]
        if document["step"].get("kind") not in ("conan-create", "conan-build") \
                or "upstream" not in document:
            continue
        unrecorded = unrecorded_upstreams(store, document)
        held = third_party_records(store, document["step"]["profile"])
        unheld = sorted(f"{label} {member['ref']}:{member['package_id']} {member['content']}"
                        for label, member in document.get("third_party", {}).items()
                        if member["content"] not in {output["content"] for output in
                                                     held.get(f"{member['ref']}:{member['package_id']}", [])})
        if unrecorded:
            print(f"{body['key']} {document['step']['package']}: {len(unrecorded)} of "
                  f"{len(document['upstream'])} upstream(s) unrecorded: " + "; ".join(unrecorded))
        if unheld:
            print(f"{body['key']} {document['step']['package']}: {len(unheld)} third-party input(s) "
                  "no third-party-create record holds: " + "; ".join(unheld))
        flagged += bool(unrecorded or unheld)
    print(f"artefact_store: {flagged} record(s) link an upstream no step of its own recorded, or "
          "a third-party binary no step of its own judged")
    return 1 if flagged else 0


THIRD_PARTY_KIND = "third-party-create"
MISMATCHED = 3
BUILT_BINARIES = ("Build", "Download")
PLANNED_BINARIES = ("Missing", "Build", "Download")


def third_party_records(store: Path, profile: str) -> dict[str, list[dict]]:
    """The package output of every `third-party-create` record at `profile`, by configuration
    (`<ref>#<rrev>:<package id>`)."""
    held: dict[str, list[dict]] = {}
    records = store / "records"
    for path in sorted(records.glob("*.json")) if records.is_dir() else ():
        body = json.loads(path.read_text())
        step = body["inputs"]["step"]
        if step.get("kind") == THIRD_PARTY_KIND and step.get("profile") == profile:
            output = body["outputs"]["package"]
            held.setdefault(output["alias"].rsplit("#", 1)[0], []).append(output)
    return held


def unheld_third_party(held: dict[str, list[dict]], third_party: dict, folders: dict) -> list[str]:
    """Each third-party input whose content no `third-party-create` record holds (DN-142.D17 (4)).

    A configuration that HAS a record at another content was written after its step judged it:
    the finding names its folder and the files that differ from the record, the detector of
    DN-142.D17 (8) for a package that writes into itself when it is used."""
    findings = []
    for label, member in sorted(third_party.items()):
        configuration = f"{member['ref']}:{member['package_id']}"
        recorded = held.get(configuration, [])
        if member["content"] in {output["content"] for output in recorded}:
            continue
        if not recorded:
            findings.append(f"{label} {configuration} at content {member['content']}: no "
                            "third-party-create record holds it")
            continue
        before = {entry[0]: entry[1:] for entry in recorded[-1]["entries"]}
        after = {entry[0]: entry[1:] for entry in tree_digest(folders[label])[1]}
        new = sorted(after.keys() - before.keys())
        changed = sorted(path for path in after.keys() & before.keys() if after[path] != before[path])
        removed = sorted(before.keys() - after.keys())
        named = "; ".join(f"{word} {len(paths)}: " + ", ".join(paths[:NAMED_FILES])
                          + (", ..." if len(paths) > NAMED_FILES else "")
                          for word, paths in (("new", new), ("changed", changed), ("removed", removed))
                          if paths)
        findings.append(f"{label} {configuration}: CHANGED SINCE ITS RECORD, its folder "
                        f"{folders[label]} was written after its step judged it: {named}")
    return findings


def _refuse_unheld_third_party(store: Path, document: dict, folders: dict) -> bool:
    """True, having named each one, when the step consumed a third-party binary no step judged."""
    findings = unheld_third_party(third_party_records(store, document["step"]["profile"]),
                                  document["third_party"], folders)
    if findings:
        print(f"artefact_store: REFUSED {document['step']['package']}: {len(findings)} third-party "
              f"package(s) at a content no third-party-create record at "
              f"{document['step']['profile']} holds, so the step consumed a binary no step of its "
              "own built and judged (DN-142.D17 (4), (8)): " + "; ".join(findings), file=sys.stderr)
    return bool(findings)


def built_third_party(nodes: list[dict], prefixes: tuple[str, ...]) -> list[str]:
    """Each third-party node a first-party step's graph built or downloaded (DN-142.D17 (1))."""
    return sorted(f"{node['ref'].split('#')[0]} ({node['context']}) {node['binary']}"
                  for node in nodes
                  if not _owned(node["name"], prefixes) and node.get("binary") in BUILT_BINARIES)


def restorable_third_party(store: Path, profile: str) -> dict[str, list[dict]]:
    """`third_party_records` narrowed to the records a home can be restored from: those keeping a
    transport (DN-142.D17 (2), amended). A record judged before the amendment holds a content and
    no transport, so its content is planned for a rebuild, which records one."""
    return {configuration: kept for configuration, outputs in third_party_records(store, profile).items()
            if (kept := [output for output in outputs if TRANSPORT_FIELD in output])}


def held_content(outputs: list[dict], digest: str) -> str | None:
    """The recorded content a third-party folder's digest stands for, among `outputs`: the content
    itself, or the content of the record whose `packaged` digest it is, the immutable folder of a
    `finalize()` package restored into a home that never consumed it; None when neither holds it."""
    for output in outputs:
        if digest in (output["content"], output.get(PACKAGED_FIELD)):
            return output["content"]
    return None


def third_party_plan(store: Path, home: Path, graph_json: Path, prefixes: tuple[str, ...],
                     profile: str) -> int:
    """`third-party-plan`: each third-party reference a first-party step's graph (`conan graph
    info` of the step's recipe, under the strict lock) needs built by a step of its own, one
    `<name>/<version>` a line, its reason on stderr: a node conan marks Missing, Build or
    Download (a downloaded binary is never consumed, DN-142.D17 (10)), and a present one whose
    content no record with a transport holds (DN-142.D17 (4), (2) amended). A planned reference makes the build-context tools
    its build needs appear in the next resolve, so the caller re-plans until nothing is added.
    Exit 1 on a third-party node in a state the plan does not know."""
    nodes = graph_nodes(graph_json)
    refuse_python_requires(nodes, graph_json)
    held = restorable_third_party(store, profile)
    plan: dict[str, str] = {}
    unknown = []
    for node in nodes:
        state = node.get("binary")
        if _owned(node["name"], prefixes) or state == "Skip":
            continue
        _recipe, configuration, binary = _alias(node)
        pattern = node["ref"].split("#")[0]
        if state in PLANNED_BINARIES:
            plan.setdefault(pattern, f"{configuration} ({node['context']}) is {state}")
        elif state == "Cache":
            content = tree_digest(_cache_path(home, binary))[0]
            if held_content(held.get(configuration, []), content) is None:
                plan.setdefault(pattern, f"{configuration} ({node['context']}) is present at "
                                         f"content {content}, which no record with a restorable "
                                         "transport holds")
        else:
            unknown.append(f"{configuration} ({node['context']}) is {state}")
    if unknown:
        print(f"artefact_store: REFUSED the plan of {graph_json}: {len(unknown)} third-party "
              "node(s) in a state no step of its own can address: " + "; ".join(unknown),
              file=sys.stderr)
        return 1
    for pattern, reason in sorted(plan.items()):
        print(pattern)
        print(f"artefact_store: PLAN third-party-create {pattern}: {reason}", file=sys.stderr)
    return 0


def immutable_folder(folder: Path) -> Path:
    """The folder conan PACKAGED for a package folder `conan cache path` answers: itself, or, for a
    recipe with a `finalize()` method, the immutable `p` beside the `f` folder conan derives from it
    per home and hands its consumers (meson/1.10.2 strips its scripts' PYTHONDONTWRITEBYTECODE line
    there, measured 2026-10-11). The record holds the folder consumers use; its manifest is `p`'s."""
    immutable = folder.parent / "p"
    return immutable if folder.name == "f" and immutable.is_dir() else folder


def written_after_packaging(folder: Path) -> list[str]:
    """Each file of a package folder that differs from the folder's own `conanmanifest.txt`, the
    md5 conan wrote when it packaged it: `new <path>`, `changed <path>`, `removed <path>`. A folder
    written into after packaging (a tool run from it writing its bytecode, DN-142.D17 (8)) is caught
    even when the act that wrote it is the one recording it."""
    manifest = {}
    for line in (folder / "conanmanifest.txt").read_text().splitlines()[1:]:
        if line:
            path, _separator, digest = line.rpartition(": ")
            manifest[path] = digest
    present = {}
    for root, directories, files in os.walk(folder):
        for name in files + [entry for entry in directories if (Path(root) / entry).is_symlink()]:
            path = Path(root) / name
            relative = path.relative_to(folder).as_posix()
            if relative == "conanmanifest.txt":
                continue
            present[relative] = (hashlib.md5(os.readlink(path).encode()).hexdigest() if path.is_symlink()
                                 else hashlib.md5(path.read_bytes()).hexdigest())
    return sorted([f"new {path}" for path in present.keys() - manifest.keys()]
                  + [f"changed {path}" for path in present.keys() & manifest.keys()
                     if present[path] != manifest[path]]
                  + [f"removed {path}" for path in manifest.keys() - present.keys()])


def _dependency_order(nodes: dict[str, dict], wanted: list[str]) -> list[str]:
    """The `wanted` node ids, each after every node it depends on."""
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(index: str) -> None:
        if index in seen:
            return
        seen.add(index)
        for dependency in sorted(nodes[index].get("dependencies", {}), key=int):
            visit(dependency)
        if index in wanted:
            ordered.append(index)

    for index in sorted(wanted, key=int):
        visit(index)
    return ordered


def third_party_step(store: Path, home: Path, graph_json: Path, prefixes: tuple[str, ...],
                     profile: str, malf_dir: Path, toolchain: Path) -> int:
    """`third-party-step`: one `third-party-create` record per third-party node the graph built
    (DN-142.D17 (2)), in dependency order, from the `conan install --format=json` graph that
    built them. Its key: the reference, package id and profile; malf's tree; the recipe's export
    digest; each dependency's content, each of which a record must hold (DN-142.D17 (4)); the
    toolchain, the profile and the system. Its output is the whole package folder and the folder
    conan packaged, compared at an equal key; on a new key a restore-only transport is kept, saved
    without sources (DN-142.D17 (2), amended), and nothing publishes a third-party package.
    post: exit 0 every node stored or matching; 3 (MISMATCHED) a node rebuilt at an equal key
          differs; else 1 a node consumed an unheld dependency (its record not written) or a
          binary was downloaded."""
    nodes = json.loads(graph_json.read_text())["graph"]["nodes"]
    listed = [node for index, node in nodes.items() if index != "0"]
    refuse_python_requires(listed, graph_json)
    downloaded = [entry for entry in built_third_party(listed, prefixes) if entry.endswith("Download")]
    if downloaded:
        print(f"artefact_store: REFUSED {graph_json}: {len(downloaded)} third-party binary(ies) "
              "downloaded, a producer outside the writer's domain (DN-142.D17 (10)): "
              + "; ".join(downloaded), file=sys.stderr)
        return 1
    built = [index for index, node in nodes.items()
             if index != "0" and not _owned(node["name"], prefixes) and node.get("binary") == "Build"]
    held = third_party_records(store, profile)
    shared = {"definition": definition_member(malf_dir, step_parameters(profile)),
              "toolchain": json.loads(toolchain.read_text()),
              "profile": profile_member(home, profile, malf_dir), "system": system_member()}
    worst, mismatched = 0, False
    for index in _dependency_order(nodes, built):
        node = nodes[index]
        recipe, _configuration, binary = _alias(node)
        third_party, folders = {}, {}
        for dependency in node.get("dependencies", {}):
            upstream = nodes[dependency]
            if upstream.get("binary") == "Skip":
                continue
            label = f"{upstream['name']}/{upstream['context']}"
            folders[label] = _cache_path(home, _alias(upstream)[2])
            third_party[label] = {"ref": _alias(upstream)[0], "package_id": upstream["package_id"],
                                  "content": tree_digest(folders[label])[0]}
        document = {"step": {"kind": THIRD_PARTY_KIND, "package": node["name"], "reference": recipe,
                             "package_id": node["package_id"], "profile": profile},
                    "definition": shared["definition"], "sources": _export_digest(home, recipe),
                    "third_party": third_party, "toolchain": shared["toolchain"],
                    "profile": shared["profile"], "system": shared["system"]}
        findings = unheld_third_party(held, third_party, folders)
        if findings:
            print(f"artefact_store: REFUSED {node['name']} third-party-create: {len(findings)} "
                  "dependency(ies) at a content no record holds, so its record is not written "
                  "(DN-142.D17 (4), (5)): " + "; ".join(findings), file=sys.stderr)
            worst = 1
            continue
        folder = _cache_path(home, binary)
        packaged = immutable_folder(folder)
        written = written_after_packaging(packaged)
        if written:
            print(f"artefact_store: REFUSED {node['name']} third-party-create: its folder {packaged} "
                  f"was written after conan packaged it, so its bytes are not its build's "
                  f"(DN-142.D17 (8)): {len(written)} file(s): " + ", ".join(written[:NAMED_FILES])
                  + (", ..." if len(written) > NAMED_FILES else ""), file=sys.stderr)
            worst = 1
            continue
        content, entries = tree_digest(folder)
        output = {"alias": binary, "content": content, PACKAGED_FIELD: tree_digest(packaged)[0],
                  "entries": entries}
        with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
            committed = LocalStore(store).commit(document, {PACKAGE_OUTPUT: output}, {PACKAGE_OUTPUT: (
                TRANSPORT_FIELD, lambda: _transport(home, binary, Path(scratch), sources=False))})
        if committed == 0:
            held.setdefault(binary.rsplit("#", 1)[0], []).append(output)
        else:
            mismatched = True
    return MISMATCHED if mismatched else worst


# THE PER-STEP HOME'S RESTORE (DN-142.D25 (5) call F, DN-142.D17 (2) amended 2026-10-14). Each keyed
# step runs in a fresh conan home holding exactly its graph, every binary restored from the store by
# digest, never fetched from a shared home. The restore list is a document of `upstream` and
# `third_party` members, a key-first document's own shape: each member resolves to the record at
# the step's profile holding its content AND a transport, the one at the configuration the step's graph
# names first, then the smallest key on a tie, so a log names the same source on every run. A package
# whose bytes did not change across two versions has one content under two configurations, and only
# its configuration says which one this walk resolves (1.10.8 desk walk, 2026-10-16: the smallest key
# restored coderoast_ipc_core/1.10.7 into a walk resolving 1.10.8, and every consumer missed it). After the restore each folder is checked against its record: its
# tree digest and the cache's alias. A failed check is a RESTORE refusal, a store-integrity fault
# that writes no mismatch event; a kept record whose transport object is absent is a corrupt store,
# and the act STOPS.
RESTORE_REFUSED = 1
RESTORE_STOPPED = 2
RESTORE_NEEDED = "needed"
RESTORE_BUILT = "built"


class CorruptStore(Exception):
    """A kept record whose transport object is absent: the act stops (DN-142.D17 (2), amended)."""


def _restore_index(store: Path, profile: str) -> dict[tuple, list[tuple[str, dict]]]:
    """Every package record at `profile`, (key, package output) sorted by key, under
    (`conan-create`, its package, content) and (`third-party-create`, None, content)."""
    index: dict[tuple, list[tuple[str, dict]]] = {}
    records = store / "records"
    for path in sorted(records.glob("*.json")) if records.is_dir() else ():
        body = json.loads(path.read_text())
        step = body["inputs"]["step"]
        output = body["outputs"].get(PACKAGE_OUTPUT)
        if step.get("kind") not in PACKAGE_KINDS or step.get("profile") != profile or not output:
            continue
        package = step["package"] if step["kind"] == "conan-create" else None
        index.setdefault((step["kind"], package, output["content"]), []).append((body["key"], output))
    return {member: sorted(found, key=lambda pair: pair[0]) for member, found in index.items()}


# A restore list's map of each member's label to the configuration (`<ref>#<rrev>:<package id>`) its
# graph names: never a key member, only which record of a content stands for the member.
CONFIGURATIONS_FIELD = "configurations"


def graph_configurations(graph_json: Path) -> dict[str, str]:
    """Each node's label (`<name>/<context>`) to the configuration the graph resolved it at."""
    return {f"{node['name']}/{node['context']}": _alias(node)[1] for node in graph_nodes(graph_json)
            if node.get("rrev") and node.get("package_id")}


def restore_sources(store: Path, document: dict) -> tuple[list[dict], list[str]]:
    """(each member's source: label, record key, output, the configuration the document names or
    None; each finding naming a member no record with a transport holds).
    raises CorruptStore naming a candidate record whose transport object is absent."""
    profile = document["step"]["profile"]
    index = _restore_index(store, profile)
    configurations = document.get(CONFIGURATIONS_FIELD, {})
    members = [(label, ("conan-create", label.split("/", 1)[0], content), configurations.get(label))
               for label, content in sorted(document.get("upstream", {}).items())]
    members += [(label, (THIRD_PARTY_KIND, None, member["content"]),
                 f"{member['ref']}:{member['package_id']}")
                for label, member in sorted(document.get("third_party", {}).items())]
    sources, findings = [], []
    for label, address, configuration in members:
        if configuration is None:
            findings.append(f"{label} at content {address[2]}: the restore list names no configuration for "
                            "it, so a record of another version holding the same bytes could stand in for it")
            continue
        candidates = sorted(index.get(address, []),
                            key=lambda pair: (pair[1]["alias"].rsplit("#", 1)[0] != configuration, pair[0]))
        kept = [(key, output) for key, output in candidates if TRANSPORT_FIELD in output]
        for key, output in kept:
            if not (store / "objects" / output[TRANSPORT_FIELD]).is_file():
                raise CorruptStore(f"the record {key} ({output['alias']}) keeps transport "
                                   f"{output[TRANSPORT_FIELD]}, and {store / 'objects'} does not hold it: "
                                   "a record and its transport share one fate, so the store is corrupt "
                                   "(DN-142.D17 (2), amended)")
        named = f"{label} {configuration or address[1]} at content {address[2]}"
        if not kept:
            findings.append(f"{named}: no restorable transport, "
                            + (f"held only by {len(candidates)} record(s) keeping none: "
                               + ", ".join(key for key, _ in candidates) if candidates
                               else f"no {address[0]} record at {profile} holds it"))
            continue
        key, output = kept[0]
        sources.append({"label": label, "key": key, "output": output, "configuration": configuration})
    return sources, findings


def _conan_python() -> list[str]:
    """The interpreter `conan` runs under, read from its launcher's first line, so a restore loads
    conan's own API in one process for every transport of a step."""
    launcher = shutil.which("conan")
    first = Path(launcher).read_bytes().split(b"\n", 1)[0].decode(errors="replace") if launcher else ""
    if not first.startswith("#!"):
        sys.exit(f"artefact_store: the conan launcher {launcher} names no interpreter on its first line, "
                 "so its API cannot be loaded for a restore")
    return shlex.split(first[2:])


CONAN_RESTORE = """import json, sys
from conan.api.conan_api import ConanAPI
api = ConanAPI(cache_folder=sys.argv[1])
print(json.dumps([api.cache.restore(path).serialize() for path in sys.argv[2:]]))
"""


def _conan_restore(home: Path, archives: list[Path]) -> list[dict]:
    """Each archive restored into `home` by conan's own API in one process, in order; the package
    list conan returned for each. raises Refused when conan fails, quoting it."""
    ran = subprocess.run([*_conan_python(), "-c", CONAN_RESTORE, str(home), *map(str, archives)],
                         capture_output=True, text=True, check=False)
    if ran.returncode != 0:
        raise Refused(f"conan's restore into {home} exited {ran.returncode}:\n{ran.stderr.strip()}")
    return json.loads(ran.stdout.splitlines()[-1])


def _recipes_archive(recipes_home: Path, graph_json: Path, scratch: Path) -> Path | None:
    """Every recipe revision of the graph's nodes, saved from `recipes_home` without sources or
    any binary: what conan resolves the graph over, a node it never builds or links included."""
    recipes: dict[str, dict] = {}
    for node in graph_nodes(graph_json):
        if node.get("rrev"):
            recipes.setdefault(node["ref"].split("#")[0], {"revisions": {}})["revisions"][node["rrev"]] = {}
    if not recipes:
        return None
    listing, archive = scratch / "recipes.json", scratch / "recipes.tgz"
    listing.write_text(json.dumps({"Local Cache": recipes}))
    _run(["conan", "cache", "save", f"--list={listing}", f"--file={archive}", "--no-source"],
         {**os.environ, "CONAN_HOME": str(recipes_home), "TMPDIR": str(scratch)})
    return archive


def _restored_folders(restored: list[dict]) -> dict[str, str]:
    """Each package `<ref>#<rrev>:<package id>#<prev>` a restore wrote, to its folder under the
    home's `p/`, from the package lists conan's restore returned."""
    folders = {}
    for listing in restored:
        for reference, body in listing.items():
            for rrev, recipe in body.get("revisions", {}).items():
                for package_id, package in recipe.get("packages", {}).items():
                    for prev, entry in package.get("revisions", {}).items():
                        folders[f"{reference}#{rrev}:{package_id}#{prev}"] = entry["package_folder"]
    return folders


def restore_findings(home: Path, sources: list[dict], folders: dict[str, str]) -> list[str]:
    """Each RESTORE failure of `sources` in `home`: a record's alias the restore did not write, an
    alias naming another configuration than the document's, a folder whose tree digest is not the
    record's `packaged` (its `content` when it has none)."""
    findings = []
    for source in sources:
        alias, output = source["output"]["alias"], source["output"]
        if source["configuration"] and alias.rsplit("#", 1)[0] != source["configuration"]:
            findings.append(f"{source['label']}: the record {source['key']} holding its content is "
                            f"{alias}, another configuration than {source['configuration']}")
            continue
        if alias not in folders:
            findings.append(f"{source['label']}: the restore of record {source['key']} wrote no "
                            f"package {alias}")
            continue
        want = output.get(PACKAGED_FIELD, output["content"])
        got = tree_digest(home / "p" / folders[alias])[0]
        if got != want:
            findings.append(f"{source['label']}: {alias} restored at {got}, its record {source['key']} "
                            f"holds {want}")
    return findings


def restore(store: Path, home: Path, document: dict, ledger: Path, subject: str,
            recipes: tuple[Path, Path] | None = None) -> int:
    """`restore`: the document's members into `home` from their records' transports, then each
    folder checked (DN-142.D25 (5) call F). `recipes` names a home and a graph whose recipes are
    restored first, without sources and without any binary.
    post: 0 restored and checked, one ledger line appended; RESTORE_REFUSED naming each member no
          record with a transport holds or each RESTORE failure, nothing written to the store;
          RESTORE_STOPPED on a corrupt store."""
    started = time.monotonic()
    if CONFIGURATIONS_FIELD not in document and recipes is not None:
        document = {**document, CONFIGURATIONS_FIELD: graph_configurations(recipes[1])}
    try:
        sources, findings = restore_sources(store, document)
    except CorruptStore as corrupt:
        print(f"artefact_store: STOPPED {subject}: {corrupt}", file=sys.stderr)
        return RESTORE_STOPPED
    if findings:
        print(f"artefact_store: REFUSED {subject}: {len(findings)} graph node(s) with no restorable "
              "transport, never fetched from a shared home (DN-142.D25 (5) call F, DN-142.D17 (2)): "
              + "; ".join(findings), file=sys.stderr)
        return RESTORE_REFUSED
    archives = [store / "objects" / source["output"][TRANSPORT_FIELD] for source in sources]
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        recipe_archive = _recipes_archive(recipes[0], recipes[1], Path(scratch)) if recipes else None
        try:
            restored = _conan_restore(home, ([recipe_archive] if recipe_archive else []) + archives)
        except Refused as refused:
            print(f"artefact_store: REFUSED {subject}: RESTORE failed, {refused}", file=sys.stderr)
            return RESTORE_REFUSED
    failures = restore_findings(home, sources, _restored_folders(restored))
    if failures:
        print(f"artefact_store: REFUSED {subject}: RESTORE failed for {len(failures)} node(s), a "
              "store-integrity fault and never a reproducibility mismatch (DN-142.D17 (2), amended): "
              + "; ".join(failures), file=sys.stderr)
        return RESTORE_REFUSED
    moved = sum(path.stat().st_size for path in archives)
    seconds = round(time.monotonic() - started, 2)
    with ledger.open("a") as handle:
        handle.write(json.dumps({"subject": subject, "nodes": len(sources), "bytes": moved,
                                 "seconds": seconds}, sort_keys=True) + "\n")
    print(f"artefact_store: RESTORED {subject}: {len(sources)} node(s), {moved} transport bytes, "
          f"{seconds} s")
    return 0


def upstream_document(store: Path, profile: str, packages: list[str]) -> dict:
    """`upstream-document`: the restore document of a repository's UPSTREAM first-party packages (DN-142.D31
    (2)), read from the ship-form create records an upstream candidate's restore put in `store`: each named
    package's one recorded content at `profile` and its configuration. A package no such record names is no
    member (an upstream's unreleased package, which no downstream graph may need, or it refuses there).
    raises Refused naming a package recorded at two contents: one version, one binary."""
    contents: dict[str, set[tuple[str, str]]] = {}
    for record in (json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))):
        step, output = record["inputs"]["step"], record.get("outputs", {}).get("package") or {}
        if step.get("kind") != "conan-create" or step.get("form") != "ship" or step.get("profile") != profile \
                or step.get("package") not in packages or TRANSPORT_FIELD not in output:
            continue
        contents.setdefault(step["package"], set()).add((output["content"], output["alias"].rsplit("#", 1)[0]))
    doubled = sorted(name for name, held in contents.items() if len(held) != 1)
    if doubled:
        raise Refused(f"the upstream package(s) {', '.join(doubled)} are recorded at more than one content at "
                      f"{profile}: an upstream restores one binary per version")
    return {"step": {"profile": profile},
            "upstream": {f"{name}/host": next(iter(held))[0] for name, held in sorted(contents.items())},
            CONFIGURATIONS_FIELD: {f"{name}/host": next(iter(held))[1] for name, held in sorted(contents.items())}}


def restore_document(store: Path, home: Path, graph_json: Path, prefixes: tuple[str, ...],
                     profile: str, mode: str, building: set[str]) -> dict:
    """`restore-document`: the graph's restore list, a key-first document's `upstream` and
    `third_party` members. `needed`: every node whose binary the graph needs, except the
    references in `building`, a first-party one read from its own step's record at its alias, a
    third-party one from the record holding its folder. `built`: every node the graph built, read
    from its folder. raises Refused naming a needed node with no binary or no record."""
    held = recorded_contents(store, profile)
    judged = third_party_records(store, profile)
    upstream, third_party, unheld, configurations = {}, {}, [], {}
    for node in graph_nodes(graph_json):
        state, pattern = node.get("binary"), node["ref"].split("#")[0]
        if state == "Skip" or (mode == RESTORE_BUILT) != (state == "Build") \
                or (mode == RESTORE_NEEDED and pattern in building):
            continue
        label = f"{node['name']}/{node['context']}"
        recipe, configuration, binary = _alias(node)
        configurations[label] = configuration
        if not node.get("prev"):
            unheld.append(f"{label} {configuration} is {state}, with no binary to restore")
            continue
        digest = tree_digest(_cache_path(home, binary))[0]
        if _owned(node["name"], prefixes):
            contents = held.get(binary, set()) if mode == RESTORE_NEEDED else {digest}
            if len(contents) != 1:
                unheld.append(f"{label} {binary}: {len(contents)} recorded contents")
                continue
            upstream[label] = next(iter(contents))
        else:
            content = held_content(judged.get(configuration, []), digest) if mode == RESTORE_NEEDED else digest
            if content is None:
                unheld.append(f"{label} {configuration} at content {digest}: no record holds it")
                continue
            third_party[label] = {"ref": recipe, "package_id": node["package_id"], "content": content}
    if unheld:
        raise Refused(f"the restore list of {graph_json}: {len(unheld)} node(s) cannot be restored: "
                      + "; ".join(unheld))
    return {"step": {"profile": profile}, "upstream": upstream, "third_party": third_party,
            CONFIGURATIONS_FIELD: {label: configurations[label] for label in [*upstream, *third_party]}}


def restore_ledger(ledger: Path) -> str:
    """The walk's restore totals from its ledger: calls, nodes, transport bytes, seconds."""
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line] if ledger.is_file() else []
    return (f"{len(rows)} restore(s), {sum(row['nodes'] for row in rows)} node(s), "
            f"{sum(row['bytes'] for row in rows)} transport bytes, "
            f"{round(sum(row['seconds'] for row in rows), 2)} s")


def third_party_transports(store: Path, profile: str) -> list[str]:
    """Each `third-party-create` record at `profile` keeping a transport, `<alias> <bytes>`, then
    `sum <records> <bytes>` and `without-transport <records>`."""
    lines, total, without = [], 0, 0
    for outputs in third_party_records(store, profile).values():
        for output in outputs:
            if TRANSPORT_FIELD not in output:
                without += 1
                continue
            size = (store / "objects" / output[TRANSPORT_FIELD]).stat().st_size
            total += size
            lines.append(f"{output['alias']} {size}")
    return sorted(lines) + [f"sum {len(lines)} {total}", f"without-transport {without}"]


def _declared_executables_env() -> dict:
    """The `executables:` declaration MALF_STEP_EXECUTABLES names; `{}` when it names none."""
    named = os.environ.get(EXECUTABLES_ENV)
    return json.loads(Path(named).read_text()) if named else {}


def conan_step(store: Path, home: Path, graph_json: Path, package: str, profile: str,
               malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...],
               results: Path | None = None, corpus_gates: Path | None = None) -> int:
    built = built_third_party(graph_nodes(graph_json), prefixes)
    if built:
        print(f"artefact_store: REFUSED {package}: its create built or downloaded {len(built)} "
              "third-party package(s), and a first-party step builds exactly one package, its own "
              "(DN-142.D17 (1)): " + "; ".join(built), file=sys.stderr)
        return 1
    sources, upstream, third_party, target, folders = _step_inputs(home, graph_json, package,
                                                                   prefixes, True)
    document = _step_document("conan-create", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    declared = executable_classes(_declared_executables_env())
    unclassed = sorted(path for path, kind in declared.items() if kind is None)
    if unclassed:
        print(f"artefact_store: REFUSED {package}: {len(unclassed)} declared executable(s) neither "
              f"classed nor in the site form (DN-142.D24 (2)): " + ", ".join(unclassed), file=sys.stderr)
        return 1
    document["step"]["executables"] = declared
    refused = _refuse_unrecorded(store, document) | _refuse_unheld_third_party(store, document, folders)
    if not key_first_agrees(document) or refused:
        return 1
    binary, folder = target
    ran = [] if results is None else ship_form_tests(document, results, package,
                                                     binary.split(":", 1)[1].split("#", 1)[0])
    if ran:
        print(f"artefact_store: REFUSED {package}: its key names the ship form, and its create left "
              f"{', '.join(ran)}, a run of its own tests that form never makes, so nothing is stored "
              "(DN-142.D31 (1))", file=sys.stderr)
        return 1
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
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        stored = LocalStore(store).commit(
            document, {"package": {"alias": binary, "content": content, "entries": entries}},
            {"package": (TRANSPORT_FIELD, lambda: _transport(home, binary, Path(scratch)))},
            derived={"package": _transport_build_ids})
    if results is None:
        return stored
    package_id = binary.split(":", 1)[1].split("#", 1)[0]
    recipe = binary.split(":", 1)[0]
    declared = _helper().read_declarations(_cache_path(home, recipe, "export_source"))
    keys = {PACKAGE_OUTPUT: key_of(document)}
    judged = max(stored, verdict_record(store, key_of(document), package, package_id, profile,
                                        malf_dir, results, {package: content}, step_form() != "ship",
                                        declared, keys))
    if corpus_gates is not None:
        judged = max(judged, corpus_record(store, key_of(document), package, package_id, profile,
                                           malf_dir, results, {package: content}, corpus_gates, keys))
    bench = os.environ.get(BENCH_ENV)
    if bench:
        judged = max(judged, bench_record(store, key_of(document), package, package_id, profile, malf_dir,
                                          results, {package: content}, Path(bench), keys))
    tested = document["test_package"] is not None \
        or (results / package / f"{package_id}{LISTED_SUFFIX}").is_file()
    return judged or outcome_record(store, key_of(document), package, profile, corpus_gates, keys,
                                    packaged=True, tested=tested)


def ship_form_tests(document: dict, results: Path, package: str, package_id: str) -> list[str]:
    """The result files of the package's OWN tests a ship-form create left, empty for a writer-form step:
    the ship form promises a test_package verdict at most, never the unit tests', which the source
    verdict judges, so such a file says the act ran another form than its key names."""
    if document["step"].get("form") != "ship":
        return []
    return [path.name for path in (results / package / f"{package_id}.xml",
                                   results / package / f"{package_id}{LISTED_SUFFIX}") if path.is_file()]


def ship_equal(store: Path) -> int:
    """`ship-equal`, gate G-ship (DN-142.D31 (1)): every ship-form `conan-create` record of `store` paired with
    the writer-form record whose inputs are equal but for the form, and the two packages compared by
    binary alias (package id and revision) and content digest. Prints EQUAL or UNEQUAL per pair and UNPAIRED
    per ship-form record no writer-form record matches.
    post: exit 0 when at least one pair exists and every ship-form record is paired and equal, 1 otherwise."""
    records = [json.loads(path.read_text()) for path in sorted((store / "records").glob("*.json"))]
    creates = [record for record in records if record["inputs"]["step"].get("kind") == "conan-create"]

    def unformed(record: dict) -> str:
        inputs = json.loads(json.dumps(record["inputs"]))
        inputs["step"].pop("form", None)
        return key_of(inputs)

    writers = {unformed(record): record for record in creates if record["inputs"]["step"].get("form") == "writer"}
    pairs = defects = 0
    for record in creates:
        if record["inputs"]["step"].get("form") != "ship":
            continue
        package, held = record["inputs"]["step"]["package"], record["outputs"][PACKAGE_OUTPUT]
        writer = writers.get(unformed(record))
        if writer is None:
            defects += 1
            print(f"artefact_store: UNPAIRED {package} ship {record['key']}: no writer-form record has its inputs")
            continue
        pairs += 1
        other = writer["outputs"][PACKAGE_OUTPUT]
        if (held["alias"], held["content"]) == (other["alias"], other["content"]):
            print(f"artefact_store: EQUAL {package} {held['alias']} content {held['content']}")
        else:
            defects += 1
            print(f"artefact_store: UNEQUAL {package}: ship {held['alias']} content {held['content']}, "
                  f"writer {other['alias']} content {other['content']}")
    print(f"artefact_store: G-ship {pairs} pair(s), {defects} defect(s) in {store}")
    return 0 if pairs and not defects else 1


def build_step(store: Path, home: Path, graph_json: Path, package: str, profile: str,
               malf_dir: Path, toolchain: Path, prefixes: tuple[str, ...], source_tree: str,
               results: Path) -> int:
    """The verdict record of a `stored: false` package's `conan build` step (DN-142.D14): no
    package record, the source its git tree id, `judged` the upstream packages it linked."""
    built = built_third_party(graph_nodes(graph_json), prefixes)
    if built:
        print(f"artefact_store: REFUSED {package}: its build built or downloaded {len(built)} "
              "third-party package(s) (DN-142.D17 (1)): " + "; ".join(built), file=sys.stderr)
        return 1
    sources, upstream, third_party, _target, folders = _step_inputs(home, graph_json, package,
                                                                    prefixes, False)
    sources[f"{package}/host"] = source_tree
    document = _step_document("conan-build", package, profile, home, malf_dir, toolchain,
                              sources, upstream, third_party)
    refused = _refuse_unrecorded(store, document) | _refuse_unheld_third_party(store, document, folders)
    if not key_first_agrees(document) or refused:
        return 1
    built = sorted((results / package).glob("*.xml")) if (results / package).is_dir() else []
    package_ids = sorted({path.name.split(".", 1)[0] for path in built})
    if len(package_ids) > 1:
        sys.exit(f"artefact_store: {results / package} holds results of {len(package_ids)} package "
                 f"ids, and a `conan build` step builds one: {', '.join(package_ids)}")
    source = os.environ.get(STEP_SOURCE_ENV)
    if not source:
        sys.exit(f"artefact_store: build-step {package}: {STEP_SOURCE_ENV} names no source directory, "
                 "so its declared skips cannot be read (DN-142.D23)")
    keys: dict = {}
    judged = verdict_record(store, key_of(document), package, package_ids[0] if package_ids else "-",
                            profile, malf_dir, results,
                            {name.split("/")[0]: digest for name, digest in upstream.items()}, True,
                            _helper().read_declarations(source), keys)
    return judged or outcome_record(store, key_of(document), package, profile, None, keys,
                                    packaged=False, tested=True)


VERDICT_POPULATIONS = (("build", "{pid}.xml"), ("test_package", "{pid}.test_package.xml"))


# What the helper's default run records beside its JUnit files, never a population of its own:
# the corpus-labelled names it listed and did not run.
LISTED_SUFFIX = ".corpus-listed.json"


def _helper():
    """malf's test helper, beside this file: the one reading of a JUnit row and of a skip."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import malf_recipe_tests

    return malf_recipe_tests


def junit_rows(path: Path, population: str) -> list[list[str]]:
    """`[population, test name, outcome]` per test case of one JUnit file, by the helper's reading."""
    return _helper().junit_rows(path, population)


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
    blob = _run(["git", "hash-object", "--no-filters", str(helper)]).strip()
    sys.path.insert(0, str(malf_dir))
    import malf_recipe_tests

    return {"helper": blob, "labels": ["-LE", malf_recipe_tests.CORPUS_LABEL]}


def verdict_record(store: Path, build_key: str, package: str, package_id: str, profile: str,
                   malf_dir: Path, results: Path, judged: dict[str, str], in_package_ran: bool,
                   declarations: list | None = None, keys: dict | None = None) -> int:
    """Store or compare one step's test verdict (DN-142.D5 (3)); a step with no result file ran no
    test and has no verdict to keep (exit 0, saying so). The step's own files are linked at
    `<results>/steps/<package>.xml` and `.test_package.xml`, where a reader of the run finds the
    population the step's binary ran, whatever variants of it the walk's later creates built.
    `in_package_ran` is false for a ship-form create, which skips the in-package tests by design: its run
    judges no declared skip stale, and the source verdict, which runs them all, does."""
    rows, files = junit_results(results, package, package_id)
    _link_step_files(results, package, package_id,
                     files + [path for path in (results / package / f"{package_id}{LISTED_SUFFIX}",)
                              if path.is_file()])
    if not files:
        print(f"artefact_store: NO VERDICT {package}: the step wrote no result file, it ran no test")
        return 0
    undeclared, excused, defects = _helper().skip_findings(
        [row for row in rows if row[0] != "corpus-listed"], declarations or [], profile,
        unfiltered=in_package_ran)
    verdict = "fail" if any(row[2] == "failed" for row in rows) or undeclared or defects else "pass"
    if excused:
        print(f"artefact_store: EXCUSED {package} {len(excused)} declared skip(s): "
              + ", ".join(row[1] for row in excused))
    if undeclared or defects:
        print(f"artefact_store: SKIP RED {package}: " + "; ".join(undeclared + defects))
    document = {"step": {"kind": "test-verdict", "package": package, "profile": profile},
                "build": build_key, "selection": selection_member(malf_dir), "system": system_member()}
    scratch = Path(tempfile.mkdtemp(prefix="artefact_store."))
    log = scratch / "results.tar"
    with tarfile.open(log, "w") as bundle:
        for path in files:
            info = bundle.gettarinfo(str(path), arcname=path.name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            with path.open("rb") as handle:
                bundle.addfile(info, handle)
    output = {"verdict": verdict, "results": rows, "findings": key_of(rows), "judged": judged,
              "tests": len(rows), "excused": excused}
    if keys is not None:
        keys[VERDICT_OUTPUT] = key_of(document)
    return LocalStore(store).commit(document, {"verdict": output}, {"verdict": ("log", lambda: log)})


# THE SOURCE VERDICT (DN-142.D31 (1), slice S3): what the dev leg's clang tests judge. They judge SOURCES,
# never bytes: the dev leg compiles every first-party package of its closure from its working tree, against
# third-party binaries the image built and no store holds (DN-142.D19 (11)). So its key names the export
# digest of every first-party node of the package's graph (DN-142.D2, the very digests a create's `sources`
# holds), each third-party node by `ref#rrev` alone, the rendered dev profile, the selection, malf's tree
# and the image, and its one output is the verdict over the package's own test population. A gcc package of
# the same inputs is bound to it by equal members (`source_binding`), never by a judged digest.
SOURCE_VERDICT_KIND = "source-verdict"
BINDING_EQUAL = "EQUAL-INPUTS"


def source_verdict_document(home: Path, graph_json: Path, package: str, profile: str, malf_dir: Path,
                            prefixes: tuple[str, ...], reference: str) -> dict:
    """The key document of `package`'s source verdict, from `conan graph info` over its recipe path at the
    dev profile, in a home holding every first-party export of the closure and every third-party RECIPE
    (no binary): `reference` is the `<ref>#<rrev>` its own `conan export` gave. A node conan skips is no
    member, as in a create's key."""
    root = json.loads(graph_json.read_text())["graph"]["nodes"]["0"]
    nodes = graph_nodes(graph_json)
    refuse_python_requires([root, *nodes], graph_json)
    sources, third_party = {}, set()
    for node in nodes:
        if node.get("binary") == "Skip":
            continue
        recipe = _alias(node)[0]
        if _owned(node["name"], prefixes):
            sources[f"{node['name']}/{node['context']}"] = _export_digest(home, recipe)
        else:
            third_party.add(recipe)
    context = "build" if root.get("package_type") == "build-scripts" else "host"
    sources[f"{package}/{context}"] = _export_digest(home, reference)
    return {"step": {"kind": SOURCE_VERDICT_KIND, "package": package, "profile": profile},
            "definition": definition_member(malf_dir, step_parameters(profile)),
            "sources": sources, "third_party": sorted(third_party),
            "profile": profile_member(home, profile, malf_dir, writer=False),
            "selection": selection_member(malf_dir), "system": system_member()}


def source_verdict_record(store: Path, document: dict, junit: Path, source_dir: Path) -> int:
    """Store or compare `document`'s source verdict over the JUnit file the dev leg's `malf test` wrote for
    the package (`<build tree>/malf-test.junit.xml`), its skips judged against the package's own
    declarations at the document's profile (DN-142.D23). A package whose tree registered no test leaves no
    file and is judged over none: `malf test`'s own guard already refused a tree that owed one and ran none.
    post: as LocalStore.commit; the verdict `fail` on a failed row, an undeclared skip or a declaration
    defect, `pass` otherwise."""
    package, profile = document["step"]["package"], document["step"]["profile"]
    rows = sorted(junit_rows(junit, "build")) if junit.is_file() else []
    undeclared, excused, defects = _helper().skip_findings(rows, _helper().read_declarations(source_dir),
                                                           profile, unfiltered=True)
    verdict = "fail" if any(row[2] == "failed" for row in rows) or undeclared or defects else "pass"
    if excused:
        print(f"artefact_store: EXCUSED {package} {len(excused)} declared skip(s): " + ", ".join(row[1] for row in excused))
    if undeclared or defects:
        print(f"artefact_store: SKIP RED {package}: " + "; ".join(undeclared + defects))
    scratch = Path(tempfile.mkdtemp(prefix="artefact_store."))
    log = scratch / "results.tar"
    with tarfile.open(log, "w") as bundle:
        if junit.is_file():
            info = bundle.gettarinfo(str(junit), arcname=junit.name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            with junit.open("rb") as handle:
                bundle.addfile(info, handle)
    output = {"verdict": verdict, "results": rows, "findings": key_of(rows), "tests": len(rows),
              "excused": excused}
    return LocalStore(store).commit(document, {VERDICT_OUTPUT: output}, {VERDICT_OUTPUT: ("log", lambda: log)})


def source_verdict_unservable(store: Path, document: dict, rejudge: bool) -> str | None:
    """None when the source verdict at `document`'s key may be SERVED in place of running the dev leg's
    tests again (stored, produced in a counted domain, judged `pass`), else why it runs."""
    if rejudge:
        return "a re-judge was asked for (MALF_STORE_REJUDGE), so every package's tests run and are compared"
    record = LocalStore(store).record(key_of(document))
    if record is None:
        return "no source verdict at this key"
    why = servable(record)
    if why is not None:
        return why
    verdict = record["outputs"][VERDICT_OUTPUT]["verdict"]
    return None if verdict == "pass" else f"its source verdict judged {verdict}, and only a pass is served"


def source_binding(package: dict, verdict: dict) -> list[str]:
    """DN-142.D31 (1)'s binding of a ship-form package record to a source verdict record: empty when their
    inputs are EQUAL — one package, the same first-party `sources` (export digests of the whole closure),
    the same malf tree, the same image, and the same set of third-party `ref#rrev` — else one line per
    unequal member, naming it and what differs. Inputs only: whether the verdict passed is its own output."""
    held, judged = package["inputs"], verdict["inputs"]
    unequal = []
    if held["step"].get("kind") != "conan-create" or held["step"].get("form") != "ship":
        unequal.append(f"step: the package record is a {held['step'].get('kind')} of form "
                       f"{held['step'].get('form')!r}, and only a ship-form create is bound to a source verdict")
    if judged["step"].get("kind") != SOURCE_VERDICT_KIND:
        unequal.append(f"step: the verdict record is a {judged['step'].get('kind')}, not a {SOURCE_VERDICT_KIND}")
    if held["step"].get("package") != judged["step"].get("package"):
        unequal.append(f"step.package: {held['step'].get('package')} against {judged['step'].get('package')}")
    sources = document_differences(held.get("sources"), judged.get("sources"))
    if sources:
        unequal.append("sources: " + ", ".join(sources[:NAMED_FILES]))
    for member, left, right in (("definition.malf", held["definition"]["malf"], judged["definition"]["malf"]),
                                ("system", held.get("system"), judged.get("system"))):
        if left != right:
            unequal.append(f"{member}: {left} against {right}")
    refs = {member["ref"] for member in held.get("third_party", {}).values()}
    named = set(judged.get("third_party", []))
    if refs != named:
        unequal.append("third_party: only the package's " + (", ".join(sorted(refs - named)) or "none")
                       + "; only the verdict's " + (", ".join(sorted(named - refs)) or "none"))
    return unequal


def binding(store: Path, package_key: str, verdict_key: str) -> int:
    """`binding`: prints EQUAL-INPUTS with both keys, or UNEQUAL naming each member (exit 1); exit 2 when a
    record is absent."""
    local = LocalStore(store)
    package, verdict = local.record(package_key), local.record(verdict_key)
    if package is None or verdict is None:
        print(f"artefact_store: {store} holds no record {package_key if package is None else verdict_key}",
              file=sys.stderr)
        return 2
    unequal = source_binding(package, verdict)
    subject = package["inputs"]["step"].get("package")
    if not unequal:
        print(f"artefact_store: {BINDING_EQUAL} {subject} package {package_key} source-verdict {verdict_key}")
        return 0
    print(f"artefact_store: UNEQUAL {subject} package {package_key} source-verdict {verdict_key}: "
          + "; ".join(unequal))
    return 1


def shipped_packages(checkout: Path) -> list[str]:
    """`shipped`: the packages a release of the repository at `checkout` ships (its packages.yml at HEAD, the
    recorded commit, never the disk), sorted: each `release: true` package, whose package is published, and each
    package with a `published` executable (DN-142.D22), whose binary ships inside a publication site such as an
    image while the package itself is `release: false`. What a candidate's store job creates in the ship form."""
    import yaml

    shown = subprocess.run(["git", "-C", str(checkout), "show", "HEAD:packages.yml"], capture_output=True,
                           text=True, check=False)
    if shown.returncode != 0:
        raise Refused(f"{checkout} holds no packages.yml at HEAD: {shown.stderr.strip()}")
    packages = (yaml.safe_load(shown.stdout) or {}).get("packages") or {}
    return sorted(name for name, entry in packages.items()
                  if (entry or {}).get("release") is True
                  or any(executable_class(value) == "published" for value in ((entry or {}).get("executables") or {}).values()))


def bound(store: Path, packages: list[str]) -> int:
    """`bound`: each named package has exactly ONE ship-form create record and ONE source verdict in `store`,
    the verdict a pass and the two EQUAL-INPUTS (DN-142.D31 (1): a package is publishable only bound to a
    passing clang verdict over its inputs); a line per package, exit 0 all bound, 1 any not."""
    local = LocalStore(store)
    records = [local.record(path.stem) for path in sorted((store / "records").glob("*.json"))]
    failed = 0
    for name in packages:
        creates = [record for record in records if record["inputs"]["step"].get("kind") == "conan-create"
                   and record["inputs"]["step"].get("form") == "ship" and record["inputs"]["step"].get("package") == name]
        verdicts = [record for record in records if record["inputs"]["step"].get("kind") == SOURCE_VERDICT_KIND
                    and record["inputs"]["step"].get("package") == name]
        if len(creates) != 1 or len(verdicts) != 1:
            print(f"artefact_store: UNBOUND {name}: {len(creates)} ship-form create record(s) and {len(verdicts)} "
                  f"source verdict(s) in {store}, and a binding takes exactly one of each")
            failed += 1
            continue
        package, verdict = creates[0], verdicts[0]
        judged = verdict["outputs"][VERDICT_OUTPUT]["verdict"]
        unequal = source_binding(package, verdict) + ([] if judged == "pass" else [f"the source verdict judged {judged}"])
        if unequal:
            print(f"artefact_store: UNBOUND {name} package {package['key']} source-verdict {verdict['key']}: "
                  + "; ".join(unequal))
            failed += 1
            continue
        print(f"artefact_store: {BINDING_EQUAL} {name} package {package['key']} source-verdict {verdict['key']} (pass)")
    return 1 if failed else 0


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
                  malf_dir: Path, results: Path, judged: dict[str, str], gates: Path,
                  keys: dict | None = None) -> int:
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
                "build": build_key, "selection": selection, "data": document_in["data"], "system": system_member()}
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
    if keys is not None:
        keys[CORPUS_OUTPUT] = key_of(document)
    stored = LocalStore(store).commit(document, {"corpus": output}, {"corpus": ("log", lambda: log)})
    if verdict != "pass":
        bad = [row for row in rows if row[2] != "passed"]
        print(f"artefact_store: CORPUS RED {package}: {len(bad)} row(s) not passed: "
              + ", ".join(f"{row[0]} {row[1]} {row[2]}" for row in bad[:NAMED_FILES])
              + (", ..." if len(bad) > NAMED_FILES else ""))
    return stored


# THE BENCH VERDICT (DN-142.D25 (5) calls D and E): a timing verdict run INSIDE the create of the
# package that builds the benchmark, under the `user.malf:bench` conf step 0 sets, and recorded as a
# generalized verdict of its own, `<package>/bench`. Its key is the build key, the helper that ran the
# selection, the predicate and the runner it measured on; its objects are the google-benchmark JSONs,
# one per executable, never compared (DN-142.D7: a timing verdict compares its verdict, never its
# number). The predicate admits a run only when every executable exited 0 under the watchdog and
# measured with CPU frequency scaling off. Anything else judged nothing: it is refused, never stored,
# and runs again (DN-142.D19 (8)). Exclusivity is the caller's (`malf store-create` refuses a bench
# walk without the machine's build slot).
BENCH_ENV = "MALF_BENCH"
BENCH_OUTPUT = "bench"
BENCH_PREDICATE = "malf_recipe_tests.run_benches"


def bench_runner() -> dict[str, object]:
    """The `context.runner` a bench verdict declares it reads: the host, its CPU model and the CPUs
    this process may run on — its affinity, which a seat's cpuset partition narrows (DN-119.D9 (1)),
    never the machine's count, which reads 16 inside a 6-CPU seat."""
    model = ""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        model = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                      if line.startswith("model name")), "")
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    return {"host": socket.gethostname(), "cpu": model, "cpus": cpus}


def bench_judgment(sidecar: dict, outputs: Path) -> tuple[dict[str, Path], list[str]]:
    """({member: JSON file}, why the run judged nothing) of one create's bench sidecar."""
    executables = sidecar.get("executables") or []
    if not executables:
        return {}, ["the create built no benchmark executable"]
    objects, unjudged = {}, []
    for ran in executables:
        name, status = ran.get("name"), ran.get("exit")
        produced = outputs / f"{name}.json"
        if status != 0:
            unjudged.append(f"{name} exited {status} under the watchdog"
                            + (" (presumed hung: its thread dump is in the create's log)" if status == 124 else ""))
            continue
        try:
            body = json.loads(produced.read_text())
        except (OSError, ValueError) as unread:
            unjudged.append(f"{name} wrote no readable JSON at {produced}: {unread}")
            continue
        if body.get("context", {}).get("cpu_scaling_enabled") is not False:
            unjudged.append(f"{name} measured with CPU frequency scaling "
                            f"{body.get('context', {}).get('cpu_scaling_enabled')!r}, not off")
            continue
        if not body.get("benchmarks"):
            unjudged.append(f"{name} measured no benchmark")
            continue
        objects[f"{name}.json"] = produced
    return objects, unjudged


def bench_record(store: Path, build_key: str, package: str, package_id: str, profile: str,
                 malf_dir: Path, results: Path, judged: dict[str, str], document_path: Path,
                 keys: dict | None = None) -> int:
    """Store or compare one step's bench verdict when the bench document names the package; exit 0
    when it names none. post: exit 0 stored or matching; 1 when the run judged nothing (each cause
    printed, no record written) or differs at an equal key."""
    if package not in json.loads(document_path.read_text()).get("packages", []):
        return 0
    sidecar_path = results / package / f"{package_id}.bench.json"
    if not sidecar_path.is_file():
        print(f"artefact_store: UNJUDGED {package} {BENCH_OUTPUT}: the create wrote no bench sidecar "
              f"at {sidecar_path}, so no benchmark ran")
        return 1
    sidecar = json.loads(sidecar_path.read_text())
    objects, unjudged = bench_judgment(sidecar, results / package / f"{package_id}.bench")
    if unjudged:
        print(f"artefact_store: UNJUDGED {package} {BENCH_OUTPUT}: " + "; ".join(unjudged))
        return 1
    selection = selection_member(malf_dir)
    document = {
        "step": {"kind": "verdict", "id": f"{package}/{BENCH_OUTPUT}", "subject": package,
                 "predicate": {"id": BENCH_PREDICATE, "version": selection["helper"]},
                 "params": {"repetitions": sidecar.get("repetitions"), "timeout": sidecar.get("timeout"),
                            "executables": sorted(objects)}},
        "build": build_key,
        "definition": {"profile": profile},
        "judged": judged,
        "data": {},
        "toolchain": {},
        "system": system_member(),
        "context": {"runner": bench_runner()},
    }
    if keys is not None:
        keys[BENCH_OUTPUT] = key_of(document)
    return judged_verdict(store, document,
                          {"verdict": "pass", "findings": [],
                           "entries": [[member] for member in sorted(objects)], "record": {}},
                          objects, name=BENCH_OUTPUT)


# THE STEP'S OUTCOME (N359, DN-142.D19 (3), (8); plan H13): written LAST, once every record a step of
# the walk writes (its package, its test verdict, its corpus and bench verdicts) was stored or matched,
# so its existence says the step COMPLETED and its `records` name each of them by key. Its key is what
# those records' keys add to the build key that the walk can know BEFORE the step runs: the corpus
# legs and data the corpus gates document hands this package, for a package the bench document names
# the runner its benchmarks would measure on, and the system member, the pinned image the step runs in,
# carried by the outcome itself because a `stored: false` step holds no record at its build key
# (DN-142.D14, DN-142.D30 E4). A step that failed, was refused,
# crashed, timed out or judged nothing writes none (R6), so nothing of it can ever be served.
# THE EXPECTED RECORD SET (DN-142.D31 (1), the serve hole): the outcome also DECLARES every record its
# step promised, decided from what the step was asked to do and never from what it happened to write:
# the package when the step stores one; the test verdict when the step's key holds a test_package (whose
# run always selects a test, DN-142.D13 (2)), when it is a `conan build` (its one product, DN-142.D14),
# or when malf's helper promised its default selection, the `<package id>.corpus-listed.json` that
# `run_tests` writes only once the tree enabled testing and before any test runs, past the guard that
# fails a create whose enabled tree selects no test; the corpus verdict when the gates document names the
# package; the bench verdict when the bench document does. A content-only package's create, which never
# reaches the helper's selection, promises no verdict. The outcome is `pass` only when the records it
# names are exactly the expected ones and each passed; otherwise it is `incomplete` or `fail` and never
# served. The set is an OUTPUT, not a key member: the helper's promise is made inside the act, and the
# outcome's key must be known before it.
OUTCOME_KIND = "step-outcome"
OUTCOME_OUTPUT = "outcome"
EXPECTED_FIELD = "expected"
VERDICT_OUTPUT = "verdict"
CORPUS_OUTPUT = "corpus"
SERVE_MISS = 3


def outcome_document(build_key: str, package: str, profile: str, corpus_gates: Path | None) -> dict:
    """The input document of `package`'s outcome at `build_key`, from what the walk hands the step."""
    corpus = None
    if corpus_gates is not None:
        gates = json.loads(corpus_gates.read_text())
        legs = gates.get("packages", {}).get(package)
        corpus = {"legs": [{"leg": leg["leg"], "suites": sorted(leg["suites"])} for leg in legs],
                  "data": gates["data"]} if legs else None
    bench_path = os.environ.get(BENCH_ENV)
    benched = bool(bench_path) and package in json.loads(Path(bench_path).read_text()).get("packages", [])
    return {"step": {"kind": OUTCOME_KIND, "package": package, "profile": profile}, "build": build_key,
            "corpus": corpus, "bench": {"runner": bench_runner()} if benched else None, "system": system_member()}


def _record_verdict(record: dict) -> str:
    """A record's verdict: a package's is `pass` (it exists only once judged), a verdict output's its own."""
    outputs = record["outputs"]
    if PACKAGE_OUTPUT in outputs:
        return "pass"
    return next(iter(outputs.values())).get("verdict", "unjudged")


def expected_records(document: dict, packaged: bool, tested: bool) -> list[str]:
    """The records a step promised, sorted, from its outcome document and the two promises its caller
    read: `packaged` the step stores a package, `tested` it promised a test verdict."""
    promised = {PACKAGE_OUTPUT: packaged, VERDICT_OUTPUT: tested, CORPUS_OUTPUT: document["corpus"] is not None,
                BENCH_OUTPUT: document["bench"] is not None}
    return sorted(name for name, made in promised.items() if made)


def record_set_defects(expected: list[str], records: dict) -> list[str]:
    """What separates the records an outcome names from the ones its step promised, empty when equal."""
    defects = []
    missing = sorted(set(expected) - records.keys())
    if missing:
        defects.append(f"its outcome lacks the record(s) it expected: {', '.join(missing)}")
    undeclared = sorted(records.keys() - set(expected))
    if undeclared:
        defects.append(f"its outcome names record(s) its step never promised: {', '.join(undeclared)}")
    return defects


def outcome_record(store: Path, build_key: str, package: str, profile: str, corpus_gates: Path | None,
                   keys: dict, packaged: bool, tested: bool) -> int:
    """Store or compare `package`'s outcome over the records `keys` names (every one already committed),
    declaring the set its step promised (`expected_records`).
    post: as LocalStore.commit; the outcome is `pass` only when `keys` names exactly the expected records
          and each passed, `incomplete` when the two sets differ (each difference printed), else `fail`."""
    local = LocalStore(store)
    document = outcome_document(build_key, package, profile, corpus_gates)
    expected = expected_records(document, packaged, tested)
    verdicts = {name: _record_verdict(local.record(key)) for name, key in sorted(keys.items())}
    defects = record_set_defects(expected, keys)
    for defect in defects:
        print(f"artefact_store: INCOMPLETE {package} {OUTCOME_OUTPUT}: {defect} (DN-142.D31 (1))")
    verdict = "incomplete" if defects else "pass" if all(value == "pass" for value in verdicts.values()) else "fail"
    return local.commit(document,
                        {OUTCOME_OUTPUT: {"verdict": verdict, "records": dict(sorted(keys.items())),
                                          EXPECTED_FIELD: expected, "findings": key_of(verdicts)}}, {})


def unservable(store: Path, document: dict, rejudge: bool) -> tuple[dict | None, str | None]:
    """(the outcome record a step may be SERVED from instead of executing, None) or (None, why it runs).
    It is served only when its outcome names exactly the records it declares its step promised (DN-142.D31
    (1)), and the outcome and each of them are stored, were each produced by an act that ran in its domain
    with its denials counted at zero (`servable`), and judged `pass`; `rejudge` (the walk's explicit flag,
    never a retry) runs every step and compares it."""
    if rejudge:
        return None, "a re-judge was asked for (MALF_STORE_REJUDGE), so every step executes and is compared"
    local = LocalStore(store)
    outcome = local.record(key_of(document))
    if outcome is None:
        return None, "no completed step at this key"
    expected = outcome["outputs"][OUTCOME_OUTPUT].get(EXPECTED_FIELD)
    if not isinstance(expected, list):
        return None, "its outcome declares no expected record set, so nothing says what its step promised"
    defects = record_set_defects(expected, outcome["outputs"][OUTCOME_OUTPUT]["records"])
    if defects:
        return None, "; ".join(defects)
    named = {OUTCOME_OUTPUT: outcome} | {name: local.record(key)
                                         for name, key in outcome["outputs"][OUTCOME_OUTPUT]["records"].items()}
    for name, record in named.items():
        if record is None:
            return None, f"its {name} record is absent from the store"
        why = servable(record)
        if why is not None:
            return None, f"its {name} record: {why}"
        verdict = record["outputs"][OUTCOME_OUTPUT]["verdict"] if name == OUTCOME_OUTPUT else _record_verdict(record)
        if verdict != "pass":
            return None, f"its {name} record judged {verdict}, and only a pass is served"
    return outcome, None


def serve(store: Path, home: Path, key_first: Path, package: str, profile: str, ledger: Path,
          corpus_gates: Path | None) -> int:
    """`serve`: the step `key_first` names served from the store in place of executing it, when
    `unservable` finds nothing against it: its stored package restored into the walk's `home` (where the
    steps after it resolve, exactly as an executed step's recorded package is), and each of its records
    printed REUSED, the line a reading takes as an executed step's STORED or MATCH.
    post: 0 served; SERVE_MISS it must execute, why printed; RESTORE_REFUSED or RESTORE_STOPPED from
          the restore."""
    document = json.loads(key_first.read_text())
    build_key = key_of(document)
    outcome, why = unservable(store, outcome_document(build_key, package, profile, corpus_gates),
                              os.environ.get(REJUDGE_ENV) == "1")
    if outcome is None:
        print(f"artefact_store: EXECUTES {package} key {build_key}: {why}")
        return SERVE_MISS
    local = LocalStore(store)
    records = outcome["outputs"][OUTCOME_OUTPUT]["records"]
    if PACKAGE_OUTPUT in records:
        output = local.record(records[PACKAGE_OUTPUT])["outputs"][PACKAGE_OUTPUT]
        label = next(label for label in document["sources"] if label.split("/", 1)[0] == package)
        restored = restore(store, home, {"step": {"profile": profile},
                                         "upstream": {label: output["content"]}, "third_party": {},
                                         CONFIGURATIONS_FIELD: {label: output["alias"].rsplit("#", 1)[0]}},
                           ledger, f"{package}:served")
        if restored != 0:
            return restored
    for name, key in sorted(records.items()):
        print(f"artefact_store: REUSED {package} {name} key {key} {_described(local.record(key)['outputs'][name])}")
    print(f"artefact_store: REUSED {package} {OUTCOME_OUTPUT} key {outcome['key']} "
          f"{_described(outcome['outputs'][OUTCOME_OUTPUT])}")
    print(f"artefact_store: SERVED {package} at key {build_key}, nothing executed (N359, DN-142.D19)")
    return 0


REJUDGE_ENV = "MALF_STORE_REJUDGE"


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


def _transport(home: Path, binary: str, scratch: Path, sources: bool = True) -> Path:
    """The package's transport archive in `scratch`, produced once (`conan cache save`; DN-142.D3);
    a third-party one leaves the recipe's sources out (`--no-source`), its one reader a restore.
    post: the archive is the caller's to delete once the store holds its bytes."""
    pkglist = scratch / "pkglist.json"
    name, rest = binary.split("/", 1)
    version, revisions = rest.split("#", 1)
    rrev, package = revisions.split(":", 1)
    package_id, prev = package.split("#", 1)
    pkglist.write_text(json.dumps({"Local Cache": {f"{name}/{version}": {"revisions": {rrev: {
        "packages": {package_id: {"revisions": {prev: {}}}}}}}}}))
    archive = scratch / "transport.tgz"
    _run(["conan", "cache", "save", f"--list={pkglist}", f"--file={archive}"]
         + ([] if sources else ["--no-source"]),
         {**os.environ, "CONAN_HOME": str(home), "TMPDIR": str(scratch)})
    return archive


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


def judged_verdict(store: Path, document: dict, outputs: dict, objects: dict[str, Path],
                   name: str = "verdict") -> int:
    """Store or compare one generalized verdict: `outputs` holds the verdict, the findings as a list
    (kept as their digest), the entries and the record fragment; `objects` names the files kept as
    the record's objects; `name` is the output's name, which the step-0 record's step id carries
    (`<subject>/<name>`), so a release verdict over a package (`consumer`) never shares an id with
    the package's test verdict. post: exit 0 stored or matching, 1 a mismatch; a shape that is not
    the kind's exits the process naming every defect (a refused record is never written)."""
    wrong = verdict_shape(document, outputs)
    if wrong:
        sys.exit(f"artefact_store: REFUSED verdict {document.get('step', {}).get('id')}: "
                 + "; ".join(wrong))
    output = {"verdict": outputs["verdict"], "findings": key_of(sorted(outputs["findings"])),
              "entries": outputs["entries"], "record": outputs["record"]}
    return LocalStore(store).commit(document, {name: output},
                                    {name: ("objects", lambda: dict(objects))})


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
        return self.put_bytes(source.read_bytes())

    def put_bytes(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / "objects" / digest
        if not self._create(target, data) and file_sha256(target) != digest:
            sys.exit(f"artefact_store: object {target} does not hash to its name — the store is corrupt")
        return digest

    def record(self, key: str) -> dict | None:
        path = self.root / "records" / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def commit(self, document: dict, outputs: dict, objects: dict,
               derived: dict | None = None, subject: str | None = None) -> int:
        """Store `outputs` under the document's key, or compare them with the stored ones.

        `objects` maps an output's name to `(field, produce)`: on a new key `produce()` returns the
        file stored as that output's object, its digest kept under `field` and never compared — or
        a `{name: file}` map, each stored, the field then holding `{name: digest}`.
        `derived` maps an output's name to a function of that STORED object returning fields the
        record keeps beside it, an index read from the stored bytes and never compared.
        `subject` names the step in the printed lines when its document names no package or subject.
        pre: a package step (`conan-create`, `third-party-create`) offers its transport, or is
             refused: a package record with no transport could never be restored (DN-142.D17 (2)).
        post: exit 0 when the key was new (stored) or every output's compared axes equal the
        record's; 1 when one differs, a mismatch event naming both digests written beside."""
        key = key_of(document)
        subject = subject or document["step"].get("package") or document["step"]["subject"]
        if document["step"].get("kind") in PACKAGE_KINDS \
                and objects.get(PACKAGE_OUTPUT, (None,))[0] != TRANSPORT_FIELD:
            raise Refused(f"{subject} {document['step']['kind']}: a package record is committed with "
                          "its transport, and this one offers none, so no home could ever restore it "
                          "(DN-142.D17 (2), DN-142.D25 (5) call F)")
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
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    DENIALS_FIELD: step_denials_counted()}}
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


def _bare_objects(output: dict) -> bool:
    """An output that is its objects alone (a `tracked` or an `ingest` one), judging nothing."""
    return OBJECTS_FIELD in output and not {"entries", "results", "findings", "content"} & output.keys()


def _units(output: dict) -> dict[str, list]:
    """An output's comparable units by name: a package's files, a verdict's tests, an object step's
    members."""
    if "entries" in output:
        return {entry[0]: entry[1:] for entry in output["entries"]}
    if _bare_objects(output):
        return {member: [digest] for member, digest in output[OBJECTS_FIELD].items()}
    return {f"{row[0]}:{row[1]}": row[2:] for row in output.get("results", [])}


def _identity(output: dict) -> str:
    """The digest an output is identified by: a package's content, a verdict's findings, an object
    step's objects map."""
    if _bare_objects(output):
        return key_of(output[OBJECTS_FIELD])
    return output.get("content") or output["findings"]


def _unit(output: dict) -> str:
    if "content" in output:
        return "file(s)"
    if _bare_objects(output):
        return "object(s)"
    return "test(s)" if "results" in output else "finding(s)"


def _described(output: dict) -> str:
    if "slices" in output:
        return f"manifest {output['manifest']['object']} over {len(output['slices'])} slice(s)"
    if "archive" in output:
        return f"image manifest {output['manifest']} archive {output['archive']}"
    if "content" in output:
        return f"content {output['content']} ({output['alias']})"
    if "verdict" in output and "tests" in output:
        return f"verdict {output['verdict']} over {output['tests']} test(s) findings {output['findings']}"
    if "verdict" in output:
        return f"verdict {output['verdict']} findings {output['findings']}"
    if _bare_objects(output):
        return " ".join(f"object {member} {digest}" for member, digest in sorted(output[OBJECTS_FIELD].items()))
    return "nothing"


# THE OBJECTS CONVENTION (DN-142.D25 (1)): any step but a package create may carry, in an output,
# `objects: {<member>: <sha256>}`, each member held under `objects/<sha256>`; a package create's bytes
# stay its `entries` (DN-142.D22). A publication row resolves either the same way: step id -> the
# record this step-0 run stored -> the output member. Two producers of such outputs live here: the
# TRACKED step (a package's committed files, keyed on their git blob ids) and the INGESTION record
# (a hosted output step 0's B5 verified, DN-142.D20 (2)), whose one caller is B5.
OBJECTS_FIELD = "objects"
PACKAGE_OUTPUT = "package"
TRANSPORT_FIELD = "transport"
PACKAGED_FIELD = "packaged"
PACKAGE_KINDS = ("conan-create", "third-party-create")
TRACKED_KIND = TRACKED_OUTPUT = "tracked"
INGEST_KIND = INGEST_OUTPUT = "ingest"
INGEST_ARGV = ["artefact_store.py", "ingest"]
TRACKED_PREFIX = f"{TRACKED_OUTPUT}/"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
WORKFLOW_PATH = re.compile(r"^\.github/workflows/([A-Za-z0-9._-]+)\.ya?ml$")
GITHUB_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
STEP0_RUN = re.compile(r"\bstep 0 run (\d+)\b")
EXCEPTION_KEYS = {"since", "discharge"}
SEAT_KEYS = {"runner", "labels"}
READING_KEYS = {"repository", "workflow", "artefact", "member", "class", "job", "run", "judged",
                "workflow_blob", "head_sha", "title", "seat", "producers", "exception"}
# DN-142.D25 (4), ruling A: a published row's kind follows its BYTES. The `ingest:` row declares its
# member under exactly one of `executable:` or `object:`, and the write refuses a declaration the
# bytes contradict, so the class has two producers: the declaration and the bytes.
INGEST_CLASSES = ("executable", "object")


class Refused(ValueError):
    """A step the store will not record, or an object it will not resolve; the message names why."""


def member_defect(member: object) -> str | None:
    """Why `member` is not an object member name — a relative path with no `.` or `..` component —
    else None."""
    if not isinstance(member, str) or not member or member.startswith("/") or "\\" in member \
            or any(part in ("", ".", "..") for part in member.split("/")):
        return f"{member!r} is not a relative path with no empty, `.` or `..` component"
    return None


def output_object(store: Path, key: str, output: str, member: str) -> str:
    """The SHA-256 of object `member` of output `output` of the record at `key`, once the record
    holds it and the store holds those bytes whole (DN-142.D25 (1)). Raises Refused naming the first
    link of step -> record -> output -> member -> bytes that is missing."""
    path = store / "records" / f"{key}.json"
    if not HEX64.match(key) or not path.is_file():
        raise Refused(f"no record at key {key}")
    outputs = json.loads(path.read_text())["outputs"]
    if output == PACKAGE_OUTPUT:
        raise Refused(f"record {key}: a package create's bytes are its entries, never objects (DN-142.D22)")
    if output not in outputs:
        raise Refused(f"record {key} has no output {output!r} (it has {', '.join(sorted(outputs))})")
    held = outputs[output].get(OBJECTS_FIELD)
    if not isinstance(held, dict) or member not in held:
        raise Refused(f"record {key} output {output} holds no object {member!r} "
                      f"(it holds {', '.join(sorted(held)) if isinstance(held, dict) else 'none'})")
    digest = held[member]
    target = store / "objects" / str(digest)
    if not HEX64.match(str(digest)) or not target.is_file() or file_sha256(target) != digest:
        raise Refused(f"record {key} output {output} object {member}: the store does not hold "
                      f"{digest} whole")
    return digest


def _git_bytes(repository: Path, argv: list[str]) -> bytes:
    done = subprocess.run(["git", "-C", str(repository), *argv], capture_output=True, check=False)
    if done.returncode != 0:
        raise Refused(f"`git {' '.join(argv)}` in {repository} failed: "
                      f"{done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def tracked_step(store: Path, repository: Path, commit: str, name: str, package: str,
                 package_path: str, members: list[str]) -> str:
    """The `<package>/tracked` step (DN-142.D25 (1)): each member, a path under the package's path,
    read from git at `commit`, never from disk. Its key document is `{step: {kind: tracked,
    repository, package}, files: [[<member>, <blob id>]...], system}`, so a commit that leaves those
    blobs equal is a key hit within one build environment; its output `{tracked: {objects: {<member>: <sha256>}}}` judges nothing and
    carries no verdict. post: the record's key, stored or matching; Refused on a member that is no
    blob at the commit, or on a mismatch at an equal key."""
    defects = [defect for member in members if (defect := member_defect(member))]
    if not members or defects or len(set(members)) != len(members):
        raise Refused(f"{package}: tracked members {members!r} are not distinct relative paths"
                      + (f" ({'; '.join(defects)})" if defects else ""))
    files, absent = [], []
    for member in sorted(members):
        path = posixpath.normpath(posixpath.join(package_path, member))
        if path.startswith("../") or path == "..":
            raise Refused(f"{package}: {member} leaves the repository from path {package_path!r}")
        found = subprocess.run(["git", "-C", str(repository), "rev-parse", "--verify", "--quiet",
                                f"{commit}:{path}"], capture_output=True, text=True, check=False)
        blob = found.stdout.strip()
        if found.returncode != 0 or _git_bytes(repository, ["cat-file", "-t", blob]).strip() != b"blob":
            absent.append(path)
            continue
        files.append([member, blob])
    if absent:
        raise Refused(f"{package}: {', '.join(absent)} is no blob of {name} at {commit}")
    document = {"step": {"kind": TRACKED_KIND, "repository": name, "package": package}, "files": files,
                "system": system_member()}
    local = LocalStore(store)
    objects = {member: local.put_bytes(_git_bytes(repository, ["cat-file", "blob", blob]))
               for member, blob in files}
    if local.commit(document, {TRACKED_OUTPUT: {OBJECTS_FIELD: objects}}, {},
                    subject=f"{package}/{TRACKED_OUTPUT}"):
        raise Refused(f"{package}: the tracked step's objects differ from its record at an equal key")
    return key_of(document)


def declared_tracked(repository: Path, commit: str, package: str) -> tuple[str, list[str]]:
    """(the package's `path:`, the members of its `tracked/<member>` objects) as `packages.yml`
    declares them at `commit`. Raises Refused when the package declares none."""
    import yaml

    document = yaml.safe_load(_git_bytes(repository, ["show", f"{commit}:packages.yml"])) or {}
    entry = (document.get("packages") or {}).get(package)
    if not isinstance(entry, dict):
        raise Refused(f"{repository.name}/packages.yml at {commit} declares no package {package}")
    members = sorted(str(named).removeprefix(TRACKED_PREFIX) for named in entry.get(OBJECTS_FIELD) or {}
                     if str(named).startswith(TRACKED_PREFIX))
    if not members:
        raise Refused(f"{package} declares no `{TRACKED_PREFIX}<member>` object at {commit}, so its "
                      f"tracked step has nothing to hold")
    return str(entry.get("path", ".")), members


def tracked_packages(repository: Path, commit: str) -> list[str]:
    """Every package whose `packages.yml` entry at `commit` declares a `tracked/<member>` object, sorted:
    the packages a candidate's store job runs `tracked-step` for (DN-142.D33 (3))."""
    import yaml

    document = yaml.safe_load(_git_bytes(repository, ["show", f"{commit}:packages.yml"])) or {}
    return sorted(package for package, entry in (document.get("packages") or {}).items()
                  if any(str(named).startswith(TRACKED_PREFIX) for named in ((entry or {}).get(OBJECTS_FIELD) or {})))


def ingest_step_id(repository: str, workflow: str) -> str:
    """`<repository>/ingest.<workflow stem>` (DN-142.D25 (1)), from `.github/workflows/<stem>.yml`."""
    matched = WORKFLOW_PATH.match(workflow)
    return f"{repository}/{INGEST_KIND}.{matched.group(1) if matched else workflow}"


def seat_defect(seat: object) -> str | None:
    """Why `seat` is not `{runner, labels}` naming who produced an ingested output, else None."""
    if not isinstance(seat, dict) or set(seat) != SEAT_KEYS or not isinstance(seat["runner"], str) \
            or not seat["runner"].strip() or not isinstance(seat["labels"], list) \
            or not all(isinstance(label, str) and label for label in seat["labels"]):
        return (f"its seat {seat!r} is not {{runner, labels}}: an output with no seat is judged by "
                f"nobody anyone can name (DN-142.D7)")
    return None


def exception_defect(exception: object) -> str | None:
    """Why `exception` is not the dated `{since: <YYYY-MM-DD>, discharge}` every hosted output
    carries, else None."""
    try:
        dated = isinstance(exception, dict) and set(exception) == EXCEPTION_KEYS \
            and isinstance(exception["discharge"], str) and bool(exception["discharge"].strip()) \
            and datetime.strptime(str(exception["since"]), "%Y-%m-%d").strftime("%Y-%m-%d") == exception["since"]
    except ValueError:
        dated = False
    if not dated:
        return (f"its exception {exception!r} is not {{since: <YYYY-MM-DD>, discharge}}: an output "
                f"produced outside the writer's domain is a DATED exception (DN-142.D7)")
    return None


def _reading_defects(reading: dict, step0_run: str) -> list[str]:
    """Every way `reading` misses DN-142.D20 (2)'s shape; empty when it has it."""
    wrong = [f"it lacks `{member}`" for member in sorted(READING_KEYS - reading.keys())]
    wrong += [f"it carries `{member}`, which no ingestion record holds" for member in sorted(reading.keys() - READING_KEYS)]
    if wrong:
        return wrong
    if not all(isinstance(reading[field], str) and NAME.match(reading[field])
               for field in ("repository", "artefact", "member")):
        wrong.append("its repository, artefact or member is not a file name")
    if not isinstance(reading["workflow"], str) or not WORKFLOW_PATH.match(reading["workflow"]):
        wrong.append(f"its workflow {reading['workflow']!r} is not .github/workflows/<file>.yml")
    if reading["class"] not in INGEST_CLASSES:
        wrong.append(f"its class {reading['class']!r} is not one of {' or '.join(INGEST_CLASSES)}")
    if not isinstance(reading["job"], str) or not reading["job"].strip():
        wrong.append("it names no producing job")
    run = reading["run"]
    if not isinstance(run, dict) or set(run) != {"id", "attempt"} or not all(
            type(run[field]) is int and run[field] > 0 for field in ("id", "attempt")):
        wrong.append(f"its run {run!r} is not {{id, attempt}}, two positive integers")
    judged = reading["judged"]
    if not isinstance(judged, dict) or reading["repository"] not in judged or not all(
            isinstance(name, str) and NAME.match(name) and isinstance(tree, str) and HEX40.match(tree)
            for name, tree in judged.items()):
        wrong.append(f"its judged {judged!r} is not {{<repository>: <tree id>}} holding its own repository")
    for field in ("workflow_blob", "head_sha"):
        if not isinstance(reading[field], str) or not HEX40.match(reading[field]):
            wrong.append(f"its {field} {reading[field]!r} is not a 40-hex git object id")
    wrong += [defect for defect in (seat_defect(reading["seat"]), exception_defect(reading["exception"]))
              if defect]
    producers = reading["producers"]
    if not isinstance(producers, dict) or set(producers) != {"printed", "github_artifact_digest"} \
            or not HEX64.match(str(producers["printed"])) \
            or not GITHUB_DIGEST.match(str(producers["github_artifact_digest"])):
        wrong.append(f"its producers {producers!r} are not {{printed: <sha256>, github_artifact_digest: "
                     f"sha256:<sha256>}}")
    titled = STEP0_RUN.findall(reading["title"]) if isinstance(reading["title"], str) else []
    if titled != [step0_run]:
        wrong.append(f"its title {reading['title']!r} names step 0 run(s) {titled or 'none'}, not this "
                     f"run {step0_run}: run X's outputs never enter run Y (DN-142.D25 (3))")
    return wrong


def bytes_class(data: bytes) -> str:
    """`executable` for a PE executable or a linked ELF file (an executable or a shared object),
    `object` for any other bytes: the class a published row's bytes give it (DN-142.D25 (4) A)."""
    if pe_executable(data):
        return "executable"
    try:
        linked = elf_build_id(data) is not None
    except NoBuildId:
        linked = True
    return "executable" if linked else "object"


def ingest(store: Path, step0_run: str, reading: dict, body: bytes, malf_dir: Path) -> str:
    """THE ONE INGESTION WRITE (DN-142.D20 (2), DN-142.D25 (3)): `body`, the bytes B5 verified, and
    `reading`, what it read about them, become an object and the record of the step
    `<repository>/ingest.<workflow stem>`. Its key document is `{step: {kind: ingest, repository,
    artefact, workflow, job, class}, run: {id, attempt}, judged, definition: {workflow: <blob>, malf,
    ...}}`
    — the run IS in it, a declared exception to DN-142.D2: an ingestion is an observation, never
    reused. Its output `{ingest: {objects: {<member>: <sha256>}, producers: {printed,
    github_artifact_digest, writer_recomputed}, seat: {runner, labels, run, job}, head_sha, title,
    exception, inputs_key, class}}`, `inputs_key` the key of the same document without its run, and
    `class` the declared class the bytes agreed with (DN-142.D25 (4) A).
    post: the record key. Raises Refused, storing nothing, on a reading off the shape, a title naming
    another step-0 run, a printed digest that is not the bytes', a declared class the bytes
    contradict (an `executable` that is neither a PE executable nor a linked ELF, an `object` that is
    either), or an executable not in the published form (DN-142.D22 (4)); and on other outputs at the
    same run's key. Another run at an
    equal `inputs_key` with other bytes is a DIVERGENCE: written under `divergences/`, printed, and
    never a refusal, while the dated exception stands — MSVC output is not shown deterministic."""
    if not re.fullmatch(r"\d+", step0_run):
        raise Refused(f"the step-0 run {step0_run!r} is not a run id")
    wrong = _reading_defects(reading, step0_run)
    recomputed = hashlib.sha256(body).hexdigest()
    if not wrong and reading["producers"]["printed"] != recomputed:
        wrong.append(f"the printed digest {reading['producers']['printed']} is not the bytes' "
                     f"{recomputed}: three producers agree or nothing counts")
    step_id = ingest_step_id(str(reading.get("repository")), str(reading.get("workflow")))
    if not wrong and bytes_class(body) != reading["class"]:
        wrong.append(f"{reading['member']}: declared {reading['class']}, but its bytes are "
                     + ("a PE executable or a linked ELF file" if reading["class"] == "object"
                        else "neither a PE executable nor a linked ELF file")
                     + ": a row's kind follows its bytes (DN-142.D25 (4) A)")
    if not wrong and reading["class"] == "executable":
        wrong += [f"{reading['member']}: {defect}" for defect in ingested_defects(body)]
    if wrong:
        raise Refused(f"ingestion {step_id}: " + "; ".join(wrong))
    definition = definition_member(malf_dir, parameter_document(INGEST_ARGV, None))
    document = {"step": {"kind": INGEST_KIND, "repository": reading["repository"],
                         "artefact": reading["artefact"], "workflow": reading["workflow"],
                         "job": reading["job"], "class": reading["class"]},
                "run": reading["run"], "judged": reading["judged"],
                "definition": {**definition, "workflow": reading["workflow_blob"]}}
    inputs_key = key_of({member: value for member, value in document.items() if member != "run"})
    output = {OBJECTS_FIELD: {reading["member"]: recomputed},
              "producers": {**reading["producers"], "writer_recomputed": recomputed},
              "seat": {**reading["seat"], "run": reading["run"], "job": reading["job"]},
              "head_sha": reading["head_sha"], "title": reading["title"],
              "exception": reading["exception"], "inputs_key": inputs_key, "class": reading["class"]}
    local = LocalStore(store)
    local.put_bytes(body)
    key = key_of(document)
    if local.commit(document, {INGEST_OUTPUT: output}, {}, subject=step_id):
        raise Refused(f"ingestion {step_id}: run {reading['run']['id']} attempt {reading['run']['attempt']} "
                      f"is already recorded at {key} with other outputs")
    _record_divergence(local, key, document, output)
    return key


CANDIDATE_INGEST_KEYS = {"artifact", "commits", "after", "published"}


def ingest_rows(repository: Path, commit: str) -> list[tuple[str, str, str, str]]:
    """Every `ingest:` row packages.yml declares at `commit`, as (workflow file, artifact, member, class),
    sorted: the hosted outputs a candidate's store job ingests from its own run (DN-142.D33 (3))."""
    import yaml

    document = yaml.safe_load(_git_bytes(repository, ["show", f"{commit}:packages.yml"])) or {}
    rows = []
    for workflow, row in sorted((document.get("ingest") or {}).items()):
        classes = [klass for klass in INGEST_CLASSES if isinstance(row, dict) and klass in row]
        if len(classes) != 1 or not isinstance(row.get("artifact"), str) \
                or set(row) - CANDIDATE_INGEST_KEYS - set(INGEST_CLASSES):
            raise Refused(f"packages.yml ingest {workflow}: an ingest row is {{artifact, executable | object, commits, "
                          f"after, published}}, its member under exactly one class, not {row!r}")
        rows.append((str(workflow), row["artifact"], str(row[classes[0]]), classes[0]))
    return rows


def candidate_ingest(store: Path, repository: Path, commit: str, workflow: str, artifact_dir: Path,
                     run: dict, github_digest: str) -> str:
    """A CANDIDATE's ingestion (DN-142.D33 (3)): the member packages.yml's `ingest:` row for `workflow` declares,
    read from the artifact `artifact_dir` holds, which a job of THIS candidate's own run produced (the golden,
    insight-eidos's Windows leg), becomes an object and the record of the step `<repository>/ingest.<stem>`.
    Its key document is `{step: {kind: ingest, repository, artefact, workflow, class}, run: {id, attempt},
    commit, workflow_blob}`: the run is in it, an ingestion being an observation, never reused. Its output
    `{ingest: {objects: {<member>: <sha256>}, class, producers: {github_artifact_digest, writer_recomputed}}}`.
    Refused, storing nothing, on a row packages.yml does not declare, a member the artifact lacks, a declared
    class the bytes contradict (DN-142.D25 (4) A), an executable not in the published form (DN-142.D22 (4)), a
    digest GitHub did not answer, or other outputs at the same key. post: the record key."""
    matched = WORKFLOW_PATH.match(f".github/workflows/{workflow}")
    rows = {row[0]: row for row in ingest_rows(repository, commit)}
    if matched is None or workflow not in rows:
        raise Refused(f"packages.yml at {commit} declares no `ingest:` row for {workflow}")
    _workflow, artefact, member, klass = rows[workflow]
    if member_defect(member) or not GITHUB_DIGEST.match(github_digest):
        raise Refused(f"ingest {workflow}: the member {member!r} or GitHub's artifact digest {github_digest!r} is off "
                      f"its shape")
    if not isinstance(run.get("id"), int) or not isinstance(run.get("attempt"), int):
        raise Refused(f"ingest {workflow}: the run {run!r} is not {{id, attempt}} integers")
    path = artifact_dir / member
    if not path.is_file():
        raise Refused(f"ingest {workflow}: the artifact {artefact} holds no {member}")
    body = path.read_bytes()
    if bytes_class(body) != klass:
        raise Refused(f"ingest {workflow}: {member} is declared {klass}, and its bytes are "
                      + ("a PE executable or a linked ELF file" if klass == "object"
                         else "neither a PE executable nor a linked ELF file")
                      + ": a row's kind follows its bytes (DN-142.D25 (4) A)")
    defects = ingested_defects(body) if klass == "executable" else []
    if defects:
        raise Refused(f"ingest {workflow}: {member}: " + "; ".join(defects))
    name = repository.resolve().name
    blob = _git_bytes(repository, ["rev-parse", f"{commit}:.github/workflows/{workflow}"]).decode().strip()
    document = {"step": {"kind": INGEST_KIND, "repository": name, "artefact": artefact,
                         "workflow": f".github/workflows/{workflow}", "class": klass},
                "run": {"id": run["id"], "attempt": run["attempt"]}, "commit": commit, "workflow_blob": blob}
    local = LocalStore(store)
    digest = local.put_bytes(body)
    output = {OBJECTS_FIELD: {member: digest}, "class": klass,
              "producers": {"github_artifact_digest": github_digest, "writer_recomputed": digest}}
    if local.commit(document, {INGEST_OUTPUT: output}, {}, subject=ingest_step_id(name, f".github/workflows/{workflow}")):
        raise Refused(f"ingest {workflow}: run {run['id']} attempt {run['attempt']} is already recorded with other outputs")
    return key_of(document)


def _record_divergence(local: LocalStore, key: str, document: dict, output: dict) -> None:
    """Write and print one divergence event when an earlier ingestion at `output`'s `inputs_key`
    holds other objects (DN-142.D20 (2)): an observation, never a red."""
    earlier = []
    for path in sorted((local.root / "records").glob("*.json")):
        body = json.loads(path.read_text())
        held = body["outputs"].get(INGEST_OUTPUT)
        if body["key"] != key and body["inputs"]["step"].get("kind") == INGEST_KIND and held \
                and held.get("inputs_key") == output["inputs_key"] \
                and held.get(OBJECTS_FIELD) != output[OBJECTS_FIELD]:
            earlier.append({"key": body["key"], "run": body["inputs"]["run"], OBJECTS_FIELD: held[OBJECTS_FIELD]})
    if not earlier:
        return
    event = {"inputs_key": output["inputs_key"], "key": key, "run": document["run"],
             OBJECTS_FIELD: output[OBJECTS_FIELD], "earlier": earlier,
             "exception": output["exception"]}
    folder = local.root / "divergences"
    folder.mkdir(exist_ok=True)
    local._create(folder / f"{output['inputs_key']}.{key}.json", json.dumps(event, sort_keys=True, indent=1).encode())
    step_id = ingest_step_id(document["step"]["repository"], document["step"]["workflow"])
    print(f"artefact_store: DIVERGENCE {step_id} inputs_key {output['inputs_key']}: run "
          f"{document['run']['id']} holds {' '.join(f'{m}={d}' for m, d in sorted(output[OBJECTS_FIELD].items()))}, "
          f"{len(earlier)} earlier run(s) other bytes ({', '.join(str(row['run']['id']) for row in earlier)}); "
          f"recorded under the exception since {output['exception']['since']}, never a red")


def _pe_fixture(symbols: int = 0, debug_types: tuple[int, ...] = (), dll: bool = False) -> bytes:
    """A minimal PE32+ image: one `.rdata` section holding the debug directory's entries."""
    lfanew, optional_bytes, raw = 0x40, 112 + 16 * DIRECTORY_BYTES, 0x200
    data = bytearray(raw * 2)
    data[:2] = MZ_MAGIC
    struct.pack_into("<I", data, DOS_LFANEW_AT, lfanew)
    data[lfanew:lfanew + 4] = PE_SIGNATURE
    struct.pack_into("<HHIIIHH", data, lfanew + 4, 0x8664, 1, 0, raw if symbols else 0, symbols,
                     optional_bytes, 0x22 | (IMAGE_FILE_DLL if dll else 0))
    optional = lfanew + 4 + COFF_HEADER_BYTES
    struct.pack_into("<H", data, optional, PE32_PLUS_MAGIC)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 112 + DEBUG_DIRECTORY * DIRECTORY_BYTES,
                     0x1000, DEBUG_ENTRY_BYTES * len(debug_types))
    data[optional + optional_bytes:optional + optional_bytes + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, optional + optional_bytes + 8, raw, 0x1000, raw, raw)
    for index, kind in enumerate(debug_types):
        struct.pack_into("<IIHHIIII", data, raw + index * DEBUG_ENTRY_BYTES, 0, 0, 0, 0, kind, 0, 0, 0)
    return bytes(data)


def _raises(run) -> bool:
    try:
        run()
    except KeyError_:
        return True
    return False


def selftest() -> int:
    """Every arm of the definition member, the objects convention, the tracked step and the
    ingestion API, on fixtures."""
    failures: list[str] = []
    armed = [0]
    malf_dir = Path(__file__).resolve().parent

    def arm(label: str, run, refusal: str | None):
        armed[0] += 1
        try:
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                result = run()
        except Refused as refused:
            if refusal is None or refusal not in str(refused):
                failures.append(f"{label}: refused with {refused}, want "
                                f"{'success' if refusal is None else repr(refusal)}")
            return None, ""
        except Exception as error:
            failures.append(f"{label}: raised {type(error).__name__}: {error}")
            return None, ""
        if refusal is not None:
            failures.append(f"{label}: succeeded with {result!r}, want a refusal containing {refusal!r}")
        return result, printed.getvalue()

    def expect(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def reading(body: bytes, run_id: int = 7, **change) -> dict:
        digest = hashlib.sha256(body).hexdigest()
        base = {"repository": "eidos", "workflow": ".github/workflows/sift-windows.yml",
                "artefact": "sift-windows-x64", "member": "sift.exe", "class": "executable", "job": "build",
                "run": {"id": run_id, "attempt": 1}, "judged": {"eidos": "a" * 40, "canon": "b" * 40},
                "workflow_blob": "c" * 40, "head_sha": "d" * 40,
                "title": "Sift Windows — step 0 run 900 — eidos=dd canon=bb",
                "seat": {"runner": "GitHub Actions 12", "labels": ["windows-2025"]},
                "producers": {"printed": digest, "github_artifact_digest": "sha256:" + "e" * 64},
                "exception": {"since": "2026-10-13", "discharge": "a Windows seat in the writer domain"}}
        base.update(change)
        return {name: value for name, value in base.items() if value is not None}

    def listing(store: Path, part: str) -> list[str]:
        return sorted(path.name for path in (store / part).glob("*")) if (store / part).is_dir() else []

    def handed(document: object | None, run, **environment: str):
        """`run()` with MALF_STORE_PARAMETERS set to `document` (unset for None) and `environment`
        added, the process environment restored after."""
        saved = dict(os.environ)
        os.environ.pop(PARAMETERS_ENV, None)
        if document is not None:
            os.environ[PARAMETERS_ENV] = document if isinstance(document, str) else json.dumps(document)
        os.environ.update(environment)
        try:
            return run()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def refused_document(label: str, document: object, want: str, **environment: str) -> None:
        try:
            got = handed(document, lambda: parameter_document(act, "linux-gcc16-release"), **environment)
        except KeyError_ as refused:
            if want not in str(refused):
                failures.append(f"{label}: refused with {refused}, want {want!r}")
            return
        failures.append(f"{label}: accepted {got!r}, want a refusal containing {want!r}")

    # THE SYSTEM MEMBER (DN-142.D30 E4): the pinned config digest, MALF_SYSTEM checked against the pin, and
    # nothing outside the build environment; the arms below run as an act the launcher started.
    pinned = json.loads(ENVIRONMENT_PIN.read_text(encoding="utf-8"))["image"]
    arm("RED-FIRST (DN-142.D30 E4): an act outside the launcher (no MALF_SYSTEM) is refused a system member",
        lambda: system_member({}), "no system member outside the build environment: MALF_SYSTEM is ''")
    arm("RED-FIRST (DN-142.D30 E4): the retired `host` attestation is refused, so a host-built binary never shares "
        "a key with an image-built one", lambda: system_member({SYSTEM_ENV: "host"}), "MALF_SYSTEM is 'host'")
    arm("RED-FIRST (DN-142.D30 E4): a MALF_SYSTEM naming another ID than the pin is refused",
        lambda: system_member({SYSTEM_ENV: "sha256:" + "0" * 64}), "the act ran in another image")
    expect("inside the build environment the member is {image: <the pinned config digest>}",
           system_member({SYSTEM_ENV: pinned}), {"image": pinned})
    outside = os.environ.get(SYSTEM_ENV)
    os.environ[SYSTEM_ENV] = pinned

    # THE DEFINITION MEMBER (DN-142.D19 (1)): malf's tree and the act's parameter document, never a
    # driver's own tree; a handed document is checked against the act it keys.
    act = ["malf", "store-create"]
    own = {"argv": act, "env": {}, "profile": "linux-gcc16-release"}
    expect("an act no driver handed a document composes its own, env empty",
           handed(None, lambda: parameter_document(act, "linux-gcc16-release")), own)
    expect("a handed document equal to the act's is the act's",
           handed(own, lambda: parameter_document(act, "linux-gcc16-release")), own)
    expect("a declared variable the act was handed is a parameter",
           handed({**own, "env": {"KNOB": "on"}}, lambda: parameter_document(act, "linux-gcc16-release"),
                  KNOB="on"), {**own, "env": {"KNOB": "on"}})
    refused_document("RED: a document naming another verb is refused", {**own, "argv": ["malf", "twin-verify"]},
                     "argv is ['malf', 'twin-verify']")
    refused_document("RED: a document naming another profile is refused", {**own, "profile": "linux-clang21"},
                     "profile is 'linux-clang21'")
    refused_document("RED: a variable the act was not handed is refused", {**own, "env": {"KNOB": "on"}},
                     "env KNOB is 'on', the act was handed None")
    refused_document("RED: a member beyond argv, env and profile is refused", {**own, "tree": "pharos"},
                     "not exactly ['argv', 'env', 'profile']")
    refused_document("RED: a location among the variables is refused, never keyed",
                     {**own, "env": {"MALF_STORE_DIR": "/home/x/store"}}, "an absolute path is never a key input",
                     MALF_STORE_DIR="/home/x/store")
    refused_document("RED: a document that is not JSON is refused", "pharos=/x", "is not JSON")
    definition = handed(own, lambda: definition_member(malf_dir, step_parameters("linux-gcc16-release")))
    expect("the definition holds malf's tree and the parameters, and nothing of a driver's",
           (sorted(definition), definition["parameters"]), (["malf", "parameters"], own))
    other = handed({**own, "env": {"KNOB": "on"}},
                   lambda: definition_member(malf_dir, step_parameters("linux-gcc16-release")), KNOB="on")
    expect("parameters that differ give create keys that differ",
           key_of({"definition": definition}) != key_of({"definition": other}), True)
    expect("RED: a step run at another profile than its act's is refused",
           handed(own, lambda: _raises(lambda: step_parameters("linux-clang21"))), True)

    # A STEP'S BACKENDS (DN-142.D30 (5) E2b): the declaration MALF_STEP_BACKENDS names is the step's, and a
    # backend's image bump re-keys the step that declares it.
    with tempfile.TemporaryDirectory(prefix="artefact_store.backends.") as scratch:
        declared = [{"name": "redis", "image": "ghcr.io/x/redis:7@sha256:" + "1" * 64, "port": 6379,
                     "health": "true", "command": [], "environment": {}}]
        (Path(scratch) / "one.json").write_text(json.dumps(declared))
        (Path(scratch) / "bumped.json").write_text(json.dumps([{**declared[0], "image": "ghcr.io/x/redis:7@sha256:" + "2" * 64}]))
        (Path(scratch) / "none.json").write_text("[]")
        expect("a step declaring no backend has none, whether the variable is unset or names an empty list",
               (handed(None, step_backends), handed(None, step_backends, MALF_STEP_BACKENDS=f"{scratch}/none.json")), ([], []))
        expect("a step's declared backends are its own",
               handed(None, step_backends, MALF_STEP_BACKENDS=f"{scratch}/one.json"), declared)
        expect("RED: a backend's image bump gives the declaring step another key",
               key_of({"step": {"backends": handed(None, step_backends, MALF_STEP_BACKENDS=f"{scratch}/one.json")}})
               != key_of({"step": {"backends": handed(None, step_backends, MALF_STEP_BACKENDS=f"{scratch}/bumped.json")}}),
               True)

    # THE MALF MEMBER IS MALF WHOLE (DN-142.D29 (2), RUL-167): over a fixture malf tree, the member is
    # the tree id of HEAD on a clean checkout, a commit touching only `.github/` re-keys a create, an
    # uncommitted edit of a tracked file re-keys it, and a reworded commit over identical files moves nothing.
    with tempfile.TemporaryDirectory(prefix="artefact_store.selftest.") as scratch:
        fixture = _closure()._fixture(Path(scratch))
        create = {"argv": ["malf", "store-create"], "env": {}, "profile": "linux-gcc16-release"}
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "f", "GIT_AUTHOR_EMAIL": "f@f", "GIT_COMMITTER_NAME": "f",
                   "GIT_COMMITTER_EMAIL": "f@f", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(fixture), *args], check=True, env=git_env, capture_output=True,
                                  text=True).stdout.strip()

        before = definition_member(fixture, create)
        expect("the malf member is the tree id of HEAD on a clean checkout",
               before["malf"], git("rev-parse", "HEAD^{tree}"))
        (fixture / ".github" / "workflows" / "ci.yml").write_text("run: ./malf lint\n")
        git("commit", "-q", "-am", "ci only")
        expect("RED: a commit touching only `.github/` re-keys a create (malf is keyed whole, never per file)",
               definition_member(fixture, create) != before, True)
        committed = definition_member(fixture, create)
        git("commit", "-q", "--amend", "-m", "reworded")
        expect("a reworded commit over identical files moves no key (a tree, never a commit id)",
               definition_member(fixture, create) == committed, True)
        (fixture / "helper.py").write_text("VALUE = 'edited, not committed'\n")
        expect("an uncommitted edit of a tracked file re-keys it: the key names the bytes that ran",
               definition_member(fixture, create) != committed, True)
        # A RECORD SAYS WHETHER ITS ACT'S READS WERE COUNTED IN ITS DOMAIN (DN-142.D29 (8), ruled (c)), and
        # the reuse path serves only a counted one. A Pharos verdict act runs no domain before its own
        # traced walk: its record is false, explicitly, and is never served.
        verdict_store = Path(scratch) / "verdict-store"
        verdict_body = {"step": {"kind": "verdict", "id": "B4/clang-tidy/fixture", "subject": "fixture",
                                 "predicate": {"id": "malf lint", "version": "1"}, "params": {}},
                        "definition": {"malf": "0" * 40, "parameters": create},
                        "judged": {}, "data": {}, "toolchain": {}, "system": "s", "context": {}}

        def committed_with(tally: str | None, salt: str) -> dict:
            saved = os.environ.pop(TALLY_ENV, None)
            if tally is not None:
                (Path(scratch) / f"tally-{salt}").write_text(tally)
                os.environ[TALLY_ENV] = str(Path(scratch) / f"tally-{salt}")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    judged_verdict(verdict_store, {**verdict_body, "context": {"salt": salt}},
                                   {"verdict": "pass", "findings": [], "entries": [], "record": {}}, {})
            finally:
                os.environ.pop(TALLY_ENV, None)
                if saved is not None:
                    os.environ[TALLY_ENV] = saved
            return LocalStore(verdict_store).record(key_of({**verdict_body, "context": {"salt": salt}}))

        unconfined = committed_with(None, "verdict-act")
        expect("a verdict act's record before its confinement carries denials_counted false, explicitly, and is "
               "never served", (unconfined["produced_by"].get(DENIALS_FIELD, "absent"), servable(unconfined) is None),
               (False, False))
        counted = committed_with("counted 0\n", "counted")
        expect("a record whose act ran in its domain with zero denials carries true and may be served",
               (counted["produced_by"][DENIALS_FIELD], servable(counted)), (True, None))
        expect("RED: an uncounted act's record is false and never served; an absent field reads false",
               (committed_with("uncounted\n", "uncounted")["produced_by"][DENIALS_FIELD],
                servable({"key": "k", "produced_by": {}}) is None), (False, False))

    with tempfile.TemporaryDirectory(prefix="artefact_store.selftest.") as scratch:
        root = Path(scratch)
        store = root / "store"
        clean = _pe_fixture(debug_types=(13,))
        key, _out = arm("a reading of the published shape over a clean PE (one POGO debug entry) is "
                        "stored, its key the document's", lambda: ingest(store, "900", reading(clean), clean, malf_dir), None)
        if key:
            body = json.loads((store / "records" / f"{key}.json").read_text())
            held = body["outputs"][INGEST_OUTPUT]
            digest = hashlib.sha256(clean).hexdigest()
            expect("the ingestion key is the key of its document", key_of(body["inputs"]), key)
            expect("the document is D20 (2)'s, the run in it", (body["inputs"]["step"], body["inputs"]["run"],
                   sorted(body["inputs"]["definition"])),
                   ({"kind": "ingest", "repository": "eidos", "artefact": "sift-windows-x64",
                     "workflow": ".github/workflows/sift-windows.yml", "job": "build", "class": "executable"},
                    {"id": 7, "attempt": 1}, ["malf", "parameters", "workflow"]))
            expect("the output carries the objects, the three producers, the seat, head_sha, title, "
                   "exception and inputs_key", (held[OBJECTS_FIELD], held["producers"]["writer_recomputed"],
                   held["seat"]["run"], sorted(held)),
                   ({"sift.exe": digest}, digest, {"id": 7, "attempt": 1},
                    ["class", "exception", "head_sha", "inputs_key", "objects", "producers", "seat", "title"]))
            expect("inputs_key is the key of the document without its run",
                   held["inputs_key"], key_of({k: v for k, v in body["inputs"].items() if k != "run"}))
            resolved, _out = arm("the ingested object resolves step -> record -> output -> member",
                                 lambda: output_object(store, key, INGEST_OUTPUT, "sift.exe"), None)
            expect("the resolved object is the bytes ingested", resolved, digest)
        before = (listing(store, "records"), listing(store, "objects"))
        bad = b"other bytes"
        arm("RED: a printed digest that differs from the bytes is refused",
            lambda: ingest(store, "900", reading(clean, run_id=101, producers={
                "printed": hashlib.sha256(bad).hexdigest(), "github_artifact_digest": "sha256:" + "e" * 64}),
                clean, malf_dir), "is not the bytes'")
        arm("RED: a record without its seat is refused",
            lambda: ingest(store, "900", reading(clean, run_id=102, seat=None), clean, malf_dir), "it lacks `seat`")
        arm("RED: a seat naming no runner is refused",
            lambda: ingest(store, "900", reading(clean, run_id=103, seat={"runner": "", "labels": []}), clean, malf_dir),
            "judged by nobody")
        arm("RED: a record without its dated exception is refused",
            lambda: ingest(store, "900", reading(clean, run_id=104, exception=None), clean, malf_dir), "it lacks `exception`")
        arm("RED: an exception with no date is refused",
            lambda: ingest(store, "900", reading(clean, run_id=105, exception={"since": "soon", "discharge": "x"}),
                           clean, malf_dir), "a DATED exception")
        codeview = _pe_fixture(debug_types=(13, IMAGE_DEBUG_TYPE_CODEVIEW))
        arm("RED: a PE with a CodeView debug entry is refused",
            lambda: ingest(store, "900", reading(codeview, run_id=106), codeview, malf_dir), "sift.exe: PDB reference")
        symbols = _pe_fixture(symbols=40)
        arm("RED: a PE with a COFF symbol table is refused",
            lambda: ingest(store, "900", reading(symbols, run_id=107), symbols, malf_dir), "sift.exe: symbol table")
        elf = ELF_MAGIC + bytes([2, 1, 1]) + bytes(9) + struct.pack("<H", 2) + bytes(46)
        arm("RED: a linked ELF with no build-id is refused (DN-142.D22 (4): ingested means published)",
            lambda: ingest(store, "900", reading(elf, run_id=108), elf, malf_dir), "sift.exe: no build-id")
        arm("RED: a title naming another step-0 run is refused",
            lambda: ingest(store, "901", reading(clean, run_id=109), clean, malf_dir), "not this run 901")
        arm("RED: a judged map without the producing repository is refused",
            lambda: ingest(store, "900", reading(clean, run_id=110, judged={"canon": "b" * 40}), clean, malf_dir),
            "holding its own repository")
        expect("a refused ingestion stores nothing", (listing(store, "records"), listing(store, "objects")), before)
        dll = _pe_fixture(symbols=40, dll=True)
        arm("a PE DLL is no executable, so it is an object and the published predicates do not select it",
            lambda: ingest(store, "900", reading(dll, artefact="lib-x64", member="lib.dll", **{"class": "object"}),
                           dll, malf_dir), None)
        arm("RED: a PE executable declared an object is refused: a row's kind follows its bytes",
            lambda: ingest(store, "900", reading(clean, run_id=111, **{"class": "object"}), clean, malf_dir),
            "sift.exe: declared object, but its bytes are a PE executable or a linked ELF file")
        text = b"a golden digest, no executable\n"
        arm("RED: a text file declared an executable is refused",
            lambda: ingest(store, "900", reading(text, run_id=112, artefact="golden", member="golden.txt"),
                           text, malf_dir),
            "golden.txt: declared executable, but its bytes are neither a PE executable nor a linked ELF file")
        arm("RED: a class that is neither is refused",
            lambda: ingest(store, "900", reading(clean, run_id=113, **{"class": "entry"}), clean, malf_dir),
            "its class 'entry' is not one of executable or object")
        golden, _out = arm("a text file declared an object is stored, its class in its key and its output",
                           lambda: ingest(store, "900", reading(text, run_id=114, artefact="golden",
                                                                member="golden.txt", **{"class": "object"}),
                                          text, malf_dir), None)
        if golden:
            held = json.loads((store / "records" / f"{golden}.json").read_text())
            expect("the declared class is in the key document and in the output",
                   (held["inputs"]["step"]["class"], held["outputs"][INGEST_OUTPUT]["class"]), ("object", "object"))
        expect("the real-binary shape: no defect on a PE with only a POGO entry",
               pe_published_defects(clean), [])
        _same, printed = arm("a second run at the same inputs with the same bytes is no divergence",
                             lambda: ingest(store, "900", reading(clean, run_id=9), clean, malf_dir), None)
        expect("no divergence event for equal bytes", (listing(store, "divergences"), "DIVERGENCE" in printed), ([], False))
        other = _pe_fixture(debug_types=(13, 13))
        diverged, printed = arm("a second ingestion at an equal inputs_key with other bytes is recorded, never red",
                                lambda: ingest(store, "900", reading(other, run_id=10), other, malf_dir), None)
        events = listing(store, "divergences")
        expect("one divergence event, named by the inputs_key and the new key, printed",
               (len(events), bool(diverged) and bool(events) and events[0].endswith(f".{diverged}.json"),
                "DIVERGENCE" in printed),
               (1, True, True))
        if events:
            event = json.loads((store / "divergences" / events[0]).read_text())
            expect("the event names the earlier runs' other bytes",
                   sorted(row["run"]["id"] for row in event["earlier"]), [7, 9])
        arm("RED: the same run re-ingested with other bytes is refused (one run, one observation)",
            lambda: ingest(store, "900", reading(other, run_id=7), other, malf_dir), "already recorded")

        repo = root / "eidos"
        (repo / "sift").mkdir(parents=True)
        git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
        (repo / "packages.yml").write_text(
            "packages:\n  insight_sift:\n    path: sift\n    objects:\n"
            "      tracked/sbom.cdx.json: {published: {assets: [{name: sbom.cdx.json}]}}\n"
            "      bench/run.json: {published: {assets: [{name: bench.json}]}}\n"
            "  bare:\n    path: sift\n")
        (repo / "sift" / "sbom.cdx.json").write_text('{"bom": 1}\n')
        (repo / "sift" / "main.cpp").write_text("int main() {}\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "one"], check=True)
        first = _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
        (repo / "sift" / "main.cpp").write_text("int main() { return 0; }\n")
        subprocess.run([*git, "commit", "-qam", "two: only a source moves"], check=True)
        second = _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
        (repo / "sift" / "sbom.cdx.json").write_text('{"bom": "a disk edit nobody committed"}\n')
        tstore = root / "tracked-store"
        declared, _out = arm("the tracked members are the package's `tracked/` objects, read at the commit",
                             lambda: declared_tracked(repo, first, "insight_sift"), None)
        expect("the declared tracked members", declared, ("sift", ["sbom.cdx.json"]))
        tracked, printed = arm("a tracked step is stored, keyed on its blob ids at the commit",
                               lambda: tracked_step(tstore, repo, first, "eidos", "insight_sift", "sift",
                                                    ["sbom.cdx.json"]), None)
        if tracked:
            blob = _run(["git", "-C", str(repo), "rev-parse", f"{first}:sift/sbom.cdx.json"]).strip()
            record = json.loads((tstore / "records" / f"{tracked}.json").read_text())
            expect("the tracked document and its output", (record["inputs"], record["outputs"]),
                   ({"step": {"kind": "tracked", "repository": "eidos", "package": "insight_sift"},
                     "files": [["sbom.cdx.json", blob]], "system": {"image": pinned}},
                    {"tracked": {"objects": {"sbom.cdx.json": hashlib.sha256(b'{"bom": 1}\n').hexdigest()}}}))
            expect("the STORED line names the step and its output", "STORED insight_sift/tracked tracked key" in printed, True)
            resolved, _out = arm("the tracked object resolves, its bytes the commit's and never the disk's",
                                 lambda: output_object(tstore, tracked, TRACKED_OUTPUT, "sbom.cdx.json"), None)
            expect("the stored bytes are the committed ones",
                   (tstore / "objects" / str(resolved)).read_bytes() if resolved else None, b'{"bom": 1}\n')
        again, printed = arm("a commit that leaves the tracked blobs equal is a key hit",
                             lambda: tracked_step(tstore, repo, second, "eidos", "insight_sift", "sift",
                                                  ["sbom.cdx.json"]), None)
        expect("the same key, matched, one record", (again == tracked, "MATCH" in printed,
               len(listing(tstore, "records"))), (True, True, 1))
        arm("RED: a tracked member absent at the commit is refused",
            lambda: tracked_step(tstore, repo, first, "eidos", "insight_sift", "sift", ["absent.json"]),
            "sift/absent.json is no blob of eidos")
        arm("RED: a tracked member climbing out of the package path is refused",
            lambda: tracked_step(tstore, repo, first, "eidos", "insight_sift", "sift", ["../packages.yml"]),
            "not distinct relative paths")
        arm("RED: a package declaring no tracked object has no tracked step",
            lambda: declared_tracked(repo, first, "bare"), "declares no `tracked/<member>` object")
        if tracked:
            arm("RED: an output named `package` never resolves an object",
                lambda: output_object(tstore, tracked, PACKAGE_OUTPUT, "sbom.cdx.json"), "never objects")

            arm("RED: a member the output does not hold is refused",
                lambda: output_object(tstore, tracked, TRACKED_OUTPUT, "absent.json"), "holds no object")
            arm("RED: an absent record is refused",
                lambda: output_object(tstore, "0" * 64, TRACKED_OUTPUT, "sbom.cdx.json"), "no record at key")
            target = tstore / "objects" / hashlib.sha256(b'{"bom": 1}\n').hexdigest()
            target.chmod(0o644)
            target.write_bytes(b"tampered")
            arm("RED: an object whose bytes no longer hash to its name is refused",
                lambda: output_object(tstore, tracked, TRACKED_OUTPUT, "sbom.cdx.json"), "does not hold")

    with tempfile.TemporaryDirectory(prefix="artefact_store.selftest.") as scratch:
        crepo = root / "canon"
        (crepo / ".github" / "workflows").mkdir(parents=True)
        cgit = ["git", "-C", str(crepo), "-c", "user.email=t@t", "-c", "user.name=t"]
        (crepo / "packages.yml").write_text(
            "packages:\n  core: {release: true, version: 1.0.0}\ningest:\n"
            "  golden.yaml: {artifact: determinism-golden, object: determinism-golden.txt, commits: [canon],\n"
            "                published: {assets: [{name: canon.golden.txt}]}}\n"
            "  windows.yml: {artifact: win, executable: win.exe, commits: [canon]}\n")
        (crepo / ".github" / "workflows" / "golden.yaml").write_text("on: workflow_call\n")
        (crepo / ".github" / "workflows" / "windows.yml").write_text("on: workflow_call\n")
        subprocess.run(["git", "init", "-q", str(crepo)], check=True)
        subprocess.run([*cgit, "add", "-A"], check=True)
        subprocess.run([*cgit, "commit", "-qm", "one"], check=True)
        ccommit = _run(["git", "-C", str(crepo), "rev-parse", "HEAD"]).strip()
        artifact = root / "golden-artifact"
        artifact.mkdir()
        (artifact / "determinism-golden.txt").write_bytes(b"agreed golden digest\n")
        cstore = root / "candidate-ingest-store"
        zipped = "sha256:" + "a" * 64
        rows, _out = arm("CANDIDATE INGEST: packages.yml's ingest rows, each with its artifact, member and class",
                         lambda: ingest_rows(crepo, ccommit), None)
        expect("the ingest rows", rows, [("golden.yaml", "determinism-golden", "determinism-golden.txt", "object"),
                                         ("windows.yml", "win", "win.exe", "executable")])
        ckey, _out = arm("CANDIDATE INGEST: the golden a job of the candidate's own run uploaded is stored as the record "
                         "`<repository>/ingest.golden`",
                         lambda: candidate_ingest(cstore, crepo, ccommit, "golden.yaml", artifact,
                                                  {"id": 41, "attempt": 1}, zipped), None)
        if ckey:
            crecord = json.loads((cstore / "records" / f"{ckey}.json").read_text())
            expect("the candidate ingestion's step, run and object",
                   (crecord["inputs"]["step"], crecord["inputs"]["run"],
                    crecord["outputs"][INGEST_OUTPUT][OBJECTS_FIELD]),
                   ({"kind": "ingest", "repository": "canon", "artefact": "determinism-golden",
                     "workflow": ".github/workflows/golden.yaml", "class": "object"}, {"id": 41, "attempt": 1},
                    {"determinism-golden.txt": hashlib.sha256(b"agreed golden digest\n").hexdigest()}))
        arm("RED: a workflow packages.yml declares no ingest row for is refused",
            lambda: candidate_ingest(cstore, crepo, ccommit, "ci.yml", artifact, {"id": 41, "attempt": 1}, zipped),
            "declares no `ingest:` row for ci.yml")
        arm("RED: an artifact missing the declared member is refused",
            lambda: candidate_ingest(cstore, crepo, ccommit, "windows.yml", artifact, {"id": 41, "attempt": 1}, zipped),
            "holds no win.exe")
        (artifact / "win.exe").write_bytes(b"not an executable\n")
        arm("RED: a member declared an executable whose bytes are not one is refused (its kind follows its bytes)",
            lambda: candidate_ingest(cstore, crepo, ccommit, "windows.yml", artifact, {"id": 41, "attempt": 1}, zipped),
            "neither a PE executable nor a linked ELF file")
        (artifact / "determinism-golden.txt").write_bytes(b"other bytes\n")
        arm("RED: the same run re-ingested with other bytes is refused (one run, one observation)",
            lambda: candidate_ingest(cstore, crepo, ccommit, "golden.yaml", artifact, {"id": 41, "attempt": 1}, zipped),
            "already recorded with other outputs")
        arm("RED: a GitHub artifact digest off its shape is refused",
            lambda: candidate_ingest(cstore, crepo, ccommit, "golden.yaml", artifact, {"id": 42, "attempt": 1}, "zip"),
            "is off its shape")

        bstore, results, build_key = Path(scratch) / "store", Path(scratch) / "results", "b" * 64
        named = Path(scratch) / "bench.json"
        named.write_text(json.dumps({"packages": ["logcraft_core"]}))
        package_dir = results / "logcraft_core"
        outputs = package_dir / "pid.bench"
        outputs.mkdir(parents=True)

        def bench_run(executables: list[dict], scaling: object = False, real_time: float = 9.1) -> None:
            (package_dir / "pid.bench.json").write_text(json.dumps(
                {"repetitions": 5, "timeout": 1200, "executables": executables}))
            for ran in executables:
                (outputs / f"{ran['name']}.json").write_text(json.dumps(
                    {"context": {"cpu_scaling_enabled": scaling, "load_avg": [12.6]},
                     "benchmarks": [{"name": "BM_Replay/real_time_median", "real_time": real_time}]}))

        def bench(package: str = "logcraft_core") -> int:
            return bench_record(bstore, build_key, package, "pid", "linux-gcc16-release", malf_dir, results,
                                {package: "c" * 64}, named)

        ok = [{"name": "logcraft_core_bench", "exit": 0}]
        bench_run(ok)
        rc, printed = arm("a bench run that every executable passed is stored as the verdict `bench`", bench, None)
        key = next(iter(listing(bstore, "records")), "").removesuffix(".json")
        expect("stored once, its verdict pass, its line read by step 0",
               (rc, len(listing(bstore, "records")), f"STORED logcraft_core {BENCH_OUTPUT} key {key} verdict pass" in printed),
               (0, 1, True))
        if key:
            body = json.loads((bstore / "records" / f"{key}.json").read_text())
            expect("the key names the build, the predicate and the runner it measured on",
                   (body["inputs"]["build"], body["inputs"]["step"]["predicate"]["id"],
                    sorted(body["inputs"]["context"]["runner"])),
                   (build_key, BENCH_PREDICATE, ["cpu", "cpus", "host"]))
            expect("RED-FIRST (DN-142.D30 (11) R22): the bench verdict carries the image's system member, never ''",
                   body["inputs"]["system"], {"image": pinned})
            resolved, _out = arm("the benchmark JSON resolves step -> record -> output -> member",
                                 lambda: output_object(bstore, key, BENCH_OUTPUT, "logcraft_core_bench.json"), None)
            expect("the object is the JSON the run wrote", resolved,
                   file_sha256(outputs / "logcraft_core_bench.json"))
        bench_run(ok, real_time=11.7)
        rc, printed = arm("other numbers at an equal key MATCH: a timing verdict compares its verdict, never its number",
                          bench, None)
        expect("matched, still one record", (rc, "MATCH" in printed, len(listing(bstore, "records"))), (0, True, 1))
        for label, executables, scaling, cause in (
                ("RED: a run measured with CPU frequency scaling on judged nothing", ok, True, "scaling True, not off"),
                ("RED: a hung executable the watchdog killed judged nothing",
                 [{"name": "logcraft_core_bench", "exit": 124}], False, "presumed hung"),
                ("RED: a create that built no benchmark executable judged nothing", [], False,
                 "built no benchmark executable")):
            bench_run(executables, scaling=scaling)
            rc, printed = arm(label, bench, None)
            expect(f"{label}: refused, nothing stored", (rc, f"UNJUDGED logcraft_core {BENCH_OUTPUT}" in printed,
                                                        cause in printed, len(listing(bstore, "records"))),
                   (1, True, True, 1))
        (package_dir / "pid.bench.json").unlink()
        rc, printed = arm("RED: a named package whose create wrote no sidecar judged nothing", bench, None)
        expect("no sidecar: refused", (rc, "no bench sidecar" in printed), (1, True))
        rc, _out = arm("a package the bench document does not name has no bench verdict",
                       lambda: bench("coderoast_ipc_core"), None)
        expect("unnamed: nothing to judge, nothing stored", (rc, len(listing(bstore, "records"))), (0, 1))

    # THE TEST_PACKAGE MEMBER (DN-142.D26): the digest of the copy of the create's tracked test folder,
    # null for none, and never assumed null when nobody named it.
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        folder = Path(scratch) / "test_package"
        folder.mkdir()
        (folder / "conanfile.py").write_text("from conan import ConanFile\n")
        (folder / "test.cpp").write_text("TEST(Tt, Holds) {}\n")
        (Path(scratch) / TRACKED_LIST).write_bytes(b"conanfile.py\0test.cpp\0")
        named = handed(None, test_package_member, **{TEST_FOLDER_ENV: str(folder)})
        expect("the member is the copy's tree digest", named, tree_digest(folder)[0])
        (folder / "build" / "Release").mkdir(parents=True)
        (folder / "build" / "Release" / "test_package").write_text("the create's own build output")
        expect("RED: the files the create builds inside its test folder do not move the member",
               handed(None, test_package_member, **{TEST_FOLDER_ENV: str(folder)}), named)
        (folder / "test.cpp").write_text("TEST(Tt, Holds) {};\n")
        expect("RED: a one-byte edit of a test source moves the member",
               handed(None, test_package_member, **{TEST_FOLDER_ENV: str(folder)}) != named, True)
        expect("`none` is null", handed(None, test_package_member, **{TEST_FOLDER_ENV: "none"}), None)
        (Path(scratch) / TRACKED_LIST).write_bytes(b"conanfile.py\0test.cpp\0gone.cpp\0")
        arm("RED: a tracked path gone from the folder is refused",
            lambda: handed(None, test_package_member, **{TEST_FOLDER_ENV: str(folder)}), "no longer holds 1")
        (Path(scratch) / TRACKED_LIST).write_bytes(b"conanfile.py\0test.cpp\0")
        saved = os.environ.pop(TEST_FOLDER_ENV, None)
        try:
            arm("RED: an unnamed test folder is refused, never read as null", test_package_member,
                "never assumed null")
        finally:
            if saved is not None:
                os.environ[TEST_FOLDER_ENV] = saved
        before = Path(scratch) / "before.json"
        before.write_text(json.dumps({"step": {"package": "tt_w"}, "sources": {}}))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            agreed = handed(None, lambda: key_first_agrees(
                {"step": {"package": "tt_w"}, "sources": {}, "test_package": None}),
                **{KEY_FIRST_ENV: str(before)})
        expect("RED: a key-first document missing the member is refused at G2, not read as null",
               (agreed, "test_package (only after the step)" in err.getvalue()), (False, True))

    # THE RESTORE (DN-142.D25 (5) call F, DN-142.D17 (2) amended 2026-10-14): a package record keeps
    # its transport; each member of a restore list resolves to a record with one, is restored, and is
    # checked against it. conan's restore is stood in for by a fixture writing the archive's folder.
    with tempfile.TemporaryDirectory(prefix="artefact_store.") as scratch:
        root = Path(scratch)
        rstore, home, ledger = root / "store", root / "home", root / "ledger.jsonl"
        local = LocalStore(rstore)
        profile = "fixture"

        def package_tree(name: str, body: str) -> tuple[Path, str]:
            folder = root / "trees" / name
            (folder / "lib").mkdir(parents=True, exist_ok=True)
            (folder / "lib" / f"lib{name}.a").write_text(body)
            return folder, tree_digest(folder)[0]

        archives: dict[str, tuple[str, Path]] = {}

        def put_record(key: str, kind: str, package: str, alias: str, folder: Path | None,
                       content: str, transport: str | None = None) -> None:
            output = {"alias": alias, "content": content, "entries": []}
            if folder is not None:
                transport = local.put_bytes(f"archive of {alias} under {key}".encode())
                archives[transport] = (alias, folder)
            if transport is not None:
                output[TRANSPORT_FIELD] = transport
            body = {"key": key, "inputs": {"step": {"kind": kind, "package": package, "profile": profile}},
                    "outputs": {PACKAGE_OUTPUT: output}}
            (rstore / "records" / f"{key}.json").write_text(json.dumps(body))

        conan_calls: list[list[str]] = []
        tampered: dict[str, str] = {}

        def fixture_restore(into: Path, paths: list[Path]) -> list[dict]:
            conan_calls.append([path.name for path in paths])
            listings = []
            for path in paths:
                alias, folder = archives[path.name]
                alias = tampered.get(alias, alias)
                reference, _hash, rest = alias.partition("#")
                rrev, package_id, prev = rest.replace(":", "#").split("#")
                target = into / "p" / f"{reference.split('/')[0]}{prev[:6]}" / "p"
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(folder, target)
                if alias in tampered.values():
                    (target / "lib" / "tampered").write_text("written after its record judged it")
                listings.append({reference: {"revisions": {rrev: {"packages": {package_id: {
                    "revisions": {prev: {"package_folder": target.relative_to(into / "p").as_posix()}}}}}}}})
            return listings

        def restored(document: dict) -> tuple[int, str]:
            saved = globals()["_conan_restore"]
            globals()["_conan_restore"] = fixture_restore
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    code = restore(rstore, home, document, ledger, "ex_top")
            finally:
                globals()["_conan_restore"] = saved
            return code, out.getvalue() + err.getvalue()

        tpa_folder, tpa = package_tree("tpa", "the third-party bytes")
        top_folder, top = package_tree("ex_mid", "the first-party bytes")
        _old_folder, old = package_tree("tpold", "judged before the amendment")
        with contextlib.redirect_stderr(io.StringIO()):
            arm("RED: a third-party-create record offered with no transport is REFUSED at commit",
                lambda: local.commit({"step": {"kind": THIRD_PARTY_KIND, "package": "tpa", "profile": profile}},
                                     {PACKAGE_OUTPUT: {"alias": "tpa/1#r:p#v", "content": tpa, "entries": []}},
                                     {}), "offers none")
            arm("RED: a conan-create record offered with no transport is REFUSED at commit",
                lambda: local.commit({"step": {"kind": "conan-create", "package": "ex_mid", "profile": profile}},
                                     {PACKAGE_OUTPUT: {"alias": "ex_mid/1#r:p#v", "content": top, "entries": []}},
                                     {}), "offers none")
        expect("a refused package commit stores nothing", listing(rstore, "records"), [])
        put_record("b" * 64, THIRD_PARTY_KIND, "tpa", "tpa/1#r:p#v", tpa_folder, tpa)
        put_record("a" * 64, THIRD_PARTY_KIND, "tpa", "tpa/1#r:p#v", tpa_folder, tpa)
        put_record("c" * 64, "conan-create", "ex_mid", "ex_mid/1#r:q#w", top_folder, top)
        put_record("d" * 64, THIRD_PARTY_KIND, "tpold", "tpold/1#r:p#v", None, old)
        whole = {"step": {"profile": profile}, "upstream": {"ex_mid/host": top},
                 "third_party": {"tpa/host": {"ref": "tpa/1#r", "package_id": "p", "content": tpa}},
                 CONFIGURATIONS_FIELD: {"ex_mid/host": "ex_mid/1#r:q", "tpa/host": "tpa/1#r:p"}}
        sources, findings = restore_sources(rstore, whole)
        expect("each member resolves to a record keeping a transport, the smallest key on a tie",
               ([source["key"] for source in sources], findings), (["c" * 64, "a" * 64], []))
        # TWO VERSIONS, ONE CONTENT (1.10.8 desk walk, 2026-10-16): a package whose bytes did not change
        # across versions is held by a record of each, and the restore lands the one the graph names,
        # never the smallest key's.
        put_record("0" * 64, "conan-create", "ex_mid", "ex_mid/0#r:q#w", top_folder, top)
        sources, findings = restore_sources(rstore, whole)
        expect("two versions holding one content: the record at the graph's configuration is restored, not the "
               "smallest key's", ([source["output"]["alias"] for source in sources], findings),
               (["ex_mid/1#r:q#w", "tpa/1#r:p#v"], []))
        sources, findings = restore_sources(rstore, {key: value for key, value in whole.items()
                                                     if key != CONFIGURATIONS_FIELD})
        expect("RED: a first-party member whose configuration the restore list does not name is refused, never "
               "resolved by the smallest key", (sources, [finding.split(":", 1)[0] for finding in findings]),
               ([{"label": "tpa/host", "key": "a" * 64, "output": sources[0]["output"] if sources else None,
                  "configuration": "tpa/1#r:p"}], ["ex_mid/host at content " + top]))
        (rstore / "records" / ("0" * 64 + ".json")).unlink()
        code, printed = restored(whole)
        expect("a restore of records keeping transports restores every node and logs one ledger line",
               (code, "RESTORED ex_top: 2 node(s)" in printed, len(ledger.read_text().splitlines())),
               (0, True, 1))
        conan_calls.clear()
        code, printed = restored({**whole, "third_party": {
            **whole["third_party"], "tpold/host": {"ref": "tpold/1#r", "package_id": "p", "content": old}}})
        expect("RED: a content held only by a record keeping no transport is REFUSED, naming the node, "
               "and nothing is restored",
               (code, "tpold/host tpold/1#r:p at content " + old + ": no restorable transport, held only "
                "by 1 record(s) keeping none: " + "d" * 64 in printed, conan_calls), (RESTORE_REFUSED, True, []))
        code, printed = restored({**whole, "third_party": {
            "tpz/host": {"ref": "tpz/1#r", "package_id": "p", "content": "e" * 64}}})
        expect("RED: a content no record holds is REFUSED, naming the node",
               (code, "tpz/host tpz/1#r:p at content " + "e" * 64 + ": no restorable transport, no "
                "third-party-create record at fixture holds it" in printed), (RESTORE_REFUSED, True))
        tampered["tpa/1#r:p#v"] = "tpa/1#r:p#v"
        code, printed = restored(whole)
        expect("RED: a restored folder that differs from its record is a RESTORE refusal, and no "
               "mismatch event is written",
               (code, "RESTORE failed for 1 node(s)" in printed,
                f"tpa/host: tpa/1#r:p#v restored at" in printed, listing(rstore, "mismatches")),
               (RESTORE_REFUSED, True, True, []))
        tampered["tpa/1#r:p#v"] = "tpa/1#r:p#other"
        code, printed = restored(whole)
        expect("RED: a restore that wrote another package revision than the record's alias is a RESTORE refusal",
               (code, "tpa/host: the restore of record " + "a" * 64 + " wrote no package tpa/1#r:p#v" in printed),
               (RESTORE_REFUSED, True))
        tampered.clear()
        code, printed = restored({**whole, "third_party": {
            "tpa/host": {"ref": "tpa/1#r", "package_id": "other", "content": tpa}}})
        expect("RED: a content resolving to a record of another configuration is a RESTORE refusal",
               (code, "another configuration than tpa/1#r:other" in printed), (RESTORE_REFUSED, True))
        (rstore / "objects" / json.loads((rstore / "records" / f"{'b' * 64}.json").read_text())
         ["outputs"][PACKAGE_OUTPUT][TRANSPORT_FIELD]).unlink()
        code, printed = restored(whole)
        expect("RED: a kept record whose transport object is absent STOPS the act as a corrupt store",
               (code, "STOPPED ex_top: the record " + "b" * 64 in printed), (RESTORE_STOPPED, True))
        expect("the plan's restorable records are those keeping a transport",
               sorted(restorable_third_party(rstore, profile)), ["tpa/1#r:p"])
        expect("a folder digest stands for its record's content, or for the content whose packaged folder it is",
               (held_content([{"content": "f" * 64, PACKAGED_FIELD: "p" * 64}], "p" * 64),
                held_content([{"content": "f" * 64, PACKAGED_FIELD: "p" * 64}], "f" * 64),
                held_content([{"content": "f" * 64}], "p" * 64)), ("f" * 64, "f" * 64, None))
        expect("the ledger totals the walk's restores",
               restore_ledger(ledger).split(", ")[:2], ["1 restore(s)", "2 node(s)"])

        # REUSE (N359, DN-142.D19; plan H13): a completed step, every record of it a counted pass, is SERVED:
        # its package restored into the walk's home exactly as a cold walk's recorded package is, so the
        # steps after it key against the same bytes; anything else executes.
        sstore = root / "serve-store"
        slocal = LocalStore(sstore)
        mid_folder, mid = package_tree("ex_low", "the served upstream's bytes")
        build_key, verdict_key = "1" * 64, "2" * 64

        def put_counted(key: str, outputs: dict, counted: bool = True, kind: str = "conan-create") -> None:
            body = {"key": key, "inputs": {"step": {"kind": kind, "package": "ex_low", "profile": profile}},
                    "outputs": outputs, "produced_by": {DENIALS_FIELD: counted}}
            (sstore / "records" / f"{key}.json").write_text(json.dumps(body))

        transport = slocal.put_bytes(b"archive of ex_low/1#r:q#w")
        archives[transport] = ("ex_low/1#r:q#w", mid_folder)
        put_counted(build_key, {PACKAGE_OUTPUT: {"alias": "ex_low/1#r:q#w", "content": mid, "entries": [],
                                                 TRANSPORT_FIELD: transport}})
        put_counted(verdict_key, {"verdict": {"verdict": "pass", "tests": 3, "findings": "f" * 64}},
                    kind="test-verdict")
        key_first = root / "ex_low.key-first.json"
        key_first.write_text(json.dumps({"sources": {"ex_low/host": "s" * 64}, "step": {"package": "ex_low"}}))
        os.environ.pop(REJUDGE_ENV, None)

        def outcome_with(keys: dict, tally: str, tested: bool = True) -> tuple[int, str]:
            (root / "tally").write_text(tally)
            saved = os.environ.get(TALLY_ENV)
            os.environ[TALLY_ENV] = str(root / "tally")
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    code = outcome_record(sstore, key_of(json.loads(key_first.read_text())), "ex_low", profile,
                                          None, keys, packaged=True, tested=tested)
            finally:
                os.environ.pop(TALLY_ENV, None)
                if saved is not None:
                    os.environ[TALLY_ENV] = saved
            return code, out.getvalue() + err.getvalue()

        def served(into: Path) -> tuple[int, str]:
            saved = globals()["_conan_restore"]
            globals()["_conan_restore"] = fixture_restore
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    code = serve(sstore, into, key_first, "ex_low", profile, root / "serve.jsonl", None)
            finally:
                globals()["_conan_restore"] = saved
            return code, out.getvalue() + err.getvalue()

        gates = root / "corpus_gates.json"
        gates.write_text(json.dumps({"packages": {"ex_low": [{"leg": "sample", "suites": ["S", "R"],
                                                              "environment": {"MOUNT": str(root / "mount")}}]},
                                     "data": {"MOUNT": "d" * 64}}))
        keyed = outcome_document(build_key, "ex_low", profile, gates)
        expect("RED: a corpus leg's outcome member is its legs and suites and its data's digests, never the mount's "
               "path, which no key may hold (step 0 run 39851092354: insight_canon refused)",
               (keyed["corpus"], len(key_of(keyed))),
               ({"legs": [{"leg": "sample", "suites": ["R", "S"]}], "data": {"MOUNT": "d" * 64}}, 64))
        code, printed = served(root / "walk-miss")
        expect("RED: a key the store never completed EXECUTES, naming why, and nothing is restored",
               (code, "EXECUTES ex_low key" in printed and "no completed step at this key" in printed,
                (root / "walk-miss").exists()), (SERVE_MISS, True, False))
        code, printed = outcome_with({PACKAGE_OUTPUT: build_key, "verdict": verdict_key}, "counted 0\n")
        expect("a completed step's outcome is stored LAST, naming each record by key, its verdict the records'",
               (code, LocalStore(sstore).record(key_of(outcome_document(
                   key_of(json.loads(key_first.read_text())), "ex_low", profile, None)))["outputs"][OUTCOME_OUTPUT]
                ["records"]), (0, {PACKAGE_OUTPUT: build_key, "verdict": verdict_key}))
        code, printed = served(root / "walk-served")
        cold = root / "walk-cold"
        saved = globals()["_conan_restore"]
        globals()["_conan_restore"] = fixture_restore
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                restore(sstore, cold, {"step": {"profile": profile}, "upstream": {"ex_low/host": mid},
                                       "third_party": {}, CONFIGURATIONS_FIELD: {"ex_low/host": "ex_low/1#r:q"}},
                        root / "cold.jsonl", "ex_low:recorded")
        finally:
            globals()["_conan_restore"] = saved
        expect("RED: a completed counted pass is SERVED: its package lands in the walk's home byte for byte as a "
               "cold walk's recorded restore lands it, so its dependent keys against the same upstream, and "
               "every record prints REUSED",
               (code, (root / "walk-served").is_dir() and tree_digest(root / "walk-served")[0] == tree_digest(cold)[0],
                sum(line.startswith("artefact_store: REUSED ex_low ") for line in printed.splitlines()),
                f"REUSED ex_low {PACKAGE_OUTPUT} key {build_key} content {mid}" in printed),
               (0, True, 3, True))
        code, printed = outcome_with({PACKAGE_OUTPUT: build_key, "verdict": verdict_key}, "counted 0\n")
        expect("an outcome rebuilt at an equal key over the same records MATCHES",
               (code, "MATCH ex_low outcome key" in printed), (0, True))
        put_counted("3" * 64, {"verdict": {"verdict": "pass", "tests": 4, "findings": "e" * 64}},
                    kind="test-verdict")
        code, printed = outcome_with({PACKAGE_OUTPUT: build_key, "verdict": "3" * 64}, "counted 0\n")
        expect("RED: an outcome naming another record at an equal key is a MISMATCH",
               (code, "MISMATCH ex_low outcome at an equal key" in printed), (1, True))
        for name in os.listdir(sstore / "mismatches"):
            (sstore / "mismatches" / name).unlink()
        os.environ[REJUDGE_ENV] = "1"
        code, printed = served(root / "walk-rejudge")
        os.environ.pop(REJUDGE_ENV)
        expect("RED: MALF_STORE_REJUDGE=1, the explicit re-judge, serves nothing",
               (code, "a re-judge was asked for" in printed), (SERVE_MISS, True))
        put_counted(verdict_key, {"verdict": {"verdict": "fail", "tests": 3, "findings": "f" * 64}},
                    kind="test-verdict")
        code, printed = served(root / "walk-fail")
        expect("RED: a step whose test verdict judged fail is never served",
               (code, "its verdict record judged fail, and only a pass is served" in printed), (SERVE_MISS, True))
        put_counted(verdict_key, {"verdict": {"verdict": "pass", "tests": 3, "findings": "f" * 64}},
                    counted=False, kind="test-verdict")
        code, printed = served(root / "walk-uncounted")
        expect("RED: a record whose act's denials were not counted in its domain is never served",
               (code, "its verdict record: the record" in printed and "re-executes" in printed), (SERVE_MISS, True))
        (sstore / "records" / f"{verdict_key}.json").unlink()
        code, printed = served(root / "walk-absent")
        expect("RED: an outcome naming a record the store no longer holds is never served",
               (code, "its verdict record is absent from the store" in printed), (SERVE_MISS, True))
        put_counted(verdict_key, {"verdict": {"verdict": "pass", "tests": 3, "findings": "f" * 64}},
                    kind="test-verdict")
        for name in os.listdir(sstore / "records"):
            if LocalStore(sstore).record(name[:-len(".json")])["inputs"]["step"].get("kind") == OUTCOME_KIND:
                (sstore / "records" / name).unlink()
        code, printed = outcome_with({PACKAGE_OUTPUT: build_key}, "counted 0\n")
        outcome_key = key_of(outcome_document(key_of(json.loads(key_first.read_text())), "ex_low", profile, None))
        stored_outcome = LocalStore(sstore).record(outcome_key)["outputs"][OUTCOME_OUTPUT]
        expect("RED (DN-142.D31 (1), the serve hole): a step that promised a verdict and names its package alone "
               "is stored `incomplete`, declaring both records, and is never served",
               (code, "INCOMPLETE ex_low outcome: its outcome lacks the record(s) it expected: verdict" in printed,
                stored_outcome["verdict"], stored_outcome[EXPECTED_FIELD], served(root / "walk-unpromised")[0]),
               (0, True, "incomplete", [PACKAGE_OUTPUT, VERDICT_OUTPUT], SERVE_MISS))
        record_path = sstore / "records" / f"{outcome_key}.json"
        forged = json.loads(record_path.read_text())
        forged["outputs"][OUTCOME_OUTPUT]["verdict"] = "pass"
        record_path.write_text(json.dumps(forged))
        code, printed = served(root / "walk-forged")
        expect("RED: an outcome whose records lack an expected one is never served, whatever verdict it states",
               (code, "its outcome lacks the record(s) it expected: verdict" in printed), (SERVE_MISS, True))
        forged["outputs"][OUTCOME_OUTPUT]["records"] = {PACKAGE_OUTPUT: build_key, VERDICT_OUTPUT: verdict_key,
                                                         CORPUS_OUTPUT: verdict_key}
        forged["outputs"][OUTCOME_OUTPUT][EXPECTED_FIELD] = [PACKAGE_OUTPUT, VERDICT_OUTPUT]
        record_path.write_text(json.dumps(forged))
        code, printed = served(root / "walk-undeclared")
        expect("RED: an outcome naming a record its step never promised is never served",
               (code, "its outcome names record(s) its step never promised: corpus" in printed), (SERVE_MISS, True))
        del forged["outputs"][OUTCOME_OUTPUT][EXPECTED_FIELD]
        record_path.write_text(json.dumps(forged))
        code, printed = served(root / "walk-undeclaring")
        expect("RED: an outcome that declares no expected record set is never served",
               (code, "its outcome declares no expected record set" in printed), (SERVE_MISS, True))

        # THE SYSTEM READING OVER WHAT THE WRITERS STORE (DN-142.D30 E4): the manifest's reading runs on the
        # records verdict_record and outcome_record write, at a build key the store holds no record for, as a
        # `stored: false` step's is (DN-142.D14). Step 0 run 40203426790's B6 refused 34 outcomes and
        # insight_twin's verdict, read only against hand-written fixtures until then.
        import store_manifest

        ystore, yresults, unheld = root / "system-store", root / "system-results", "4" * 64
        (yresults / "ex_low").mkdir(parents=True)
        (yresults / "ex_low" / "pid.xml").write_text(
            '<testsuites><testsuite name="S"><testcase classname="S" name="passes"/></testsuite></testsuites>')
        (root / "tally").write_text("counted 0\n")
        saved = os.environ.get(TALLY_ENV)
        os.environ[TALLY_ENV] = str(root / "tally")
        ykeys: dict = {}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                written = (verdict_record(ystore, unheld, "ex_low", "pid", profile, malf_dir, yresults, {}, True, [], ykeys),
                           outcome_record(ystore, unheld, "ex_low", profile, None, ykeys,
                                          packaged=False, tested=True))
        finally:
            os.environ.pop(TALLY_ENV, None)
            if saved is not None:
                os.environ[TALLY_ENV] = saved
        ysteps = {"ex_low/verdict": {"key": ykeys.get("verdict")},
                  "ex_low/outcome": {"key": key_of(outcome_document(unheld, "ex_low", profile, None))}}
        expect("RED-FIRST (DN-142.D30 E4, step 0 run 40203426790): a test verdict and a step outcome each carry "
               "the pinned system member themselves, so the manifest's reading passes them with no record held "
               "at their build key",
               (written, store_manifest.system_findings(ystore, ysteps, store_manifest.pinned_system())), ((0, 0), []))

    if outside is None:
        os.environ.pop(SYSTEM_ENV, None)
    else:
        os.environ[SYSTEM_ENV] = outside
    for failure in failures:
        print(f"artefact_store selftest FAILED: {failure}")
    print(f"artefact_store selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    try:
        if len(argv) == 4 and argv[0] == "ingest":
            ingest(Path(argv[1]), argv[2], json.loads(Path(argv[3]).read_text()), sys.stdin.buffer.read(),
                   Path(__file__).resolve().parent)
            return 0
        if len(argv) == 3 and argv[0] == "ingest-rows":
            for workflow, artefact, member, klass in ingest_rows(Path(argv[1]), argv[2]):
                print(f"{workflow}\t{artefact}\t{member}\t{klass}")
            return 0
        if len(argv) == 9 and argv[0] == "candidate-ingest":
            print(candidate_ingest(Path(argv[1]), Path(argv[2]), argv[3], argv[4], Path(argv[5]),
                                   {"id": int(argv[6]), "attempt": int(argv[7])}, argv[8]))
            return 0
        if len(argv) == 3 and argv[0] == "tracked-packages":
            for package in tracked_packages(Path(argv[1]), argv[2]):
                print(package)
            return 0
        if len(argv) == 5 and argv[0] == "tracked-step":
            path, members = declared_tracked(Path(argv[2]), argv[3], argv[4])
            tracked_step(Path(argv[1]), Path(argv[2]), argv[3], Path(argv[2]).resolve().name, argv[4],
                         path, members)
            return 0
        if len(argv) == 5 and argv[0] == "object":
            print(output_object(Path(argv[1]), argv[2], argv[3], argv[4]))
            return 0
    except Refused as refused:
        print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
        return 1
    if len(argv) >= 3 and argv[0] == "parameters":
        try:
            document = parameter_document(argv[2:], argv[1])
        except KeyError_ as refused:
            print(f"artefact_store: REFUSED — {refused}", file=sys.stderr)
            return 2
        print(canonical_json(document).decode())
        return 0
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
    if len(argv) == 3 and argv[0] == "unconfined":
        try:
            print(declared_unconfined(Path(argv[1]), argv[2]))
        except ValueError as malformed:
            print(f"artefact_store: REFUSED {malformed}", file=sys.stderr)
            return 2
        return 0
    if len(argv) == 3 and argv[0] == "executables":
        try:
            print(json.dumps(declared_executables(Path(argv[1]), argv[2]), sort_keys=True))
        except ValueError as malformed:
            print(f"artefact_store: REFUSED {malformed}", file=sys.stderr)
            return 2
        return 0
    if len(argv) == 2 and argv[0] == "published-check":
        files = tree_files(Path(argv[1]))
        found = [f"{path}: {defect}" for path in sorted(files)
                 for defect in elf_published_defects(files[path])]
        for line in found:
            print(line)
        return 1 if found else 0
    if len(argv) == 2 and argv[0] == "content-manifest":
        try:
            print(content_manifest(Path(argv[1])))
        except EscapingSymlink as escaping:
            print(f"artefact_store: REFUSED {escaping}", file=sys.stderr)
            return 2
        return 0
    if len(argv) == 3 and argv[0] == "content-manifest-rows":
        print(manifest_rows_digest(Path(argv[1]), argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "toolchain":
        Path(argv[2]).write_text(json.dumps(measure_toolchain(Path(argv[1])), sort_keys=True))
        return 0
    if len(argv) in (9, 10, 11) and argv[0] == "conan-step":
        try:
            return conan_step(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5],
                              Path(argv[6]), Path(argv[7]), tuple(argv[8].split()),
                              Path(argv[9]) if len(argv) >= 10 else None,
                              Path(argv[10]) if len(argv) == 11 else None)
        except Refused as refused:
            print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
            return 1
    if len(argv) == 9 and argv[0] == "source-key":
        try:
            document = source_verdict_document(Path(argv[1]), Path(argv[2]), argv[3], argv[4], Path(argv[5]),
                                               tuple(argv[6].split()), argv[7])
        except Refused as refused:
            print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
            return 1
        Path(argv[8]).write_text(json.dumps(document, sort_keys=True))
        print(key_of(document))
        return 0
    if len(argv) == 3 and argv[0] == "source-serve":
        document = json.loads(Path(argv[2]).read_text())
        package = document["step"]["package"]
        why = source_verdict_unservable(Path(argv[1]), document, os.environ.get(REJUDGE_ENV) == "1")
        if why is not None:
            print(f"artefact_store: EXECUTES {package} source-verdict key {key_of(document)}: {why}")
            return SERVE_MISS
        record = LocalStore(Path(argv[1])).record(key_of(document))
        print(f"artefact_store: REUSED {package} source-verdict key {record['key']} "
              f"{_described(record['outputs'][VERDICT_OUTPUT])}")
        print(f"artefact_store: SERVED {package} source verdict at key {record['key']}, no test run (DN-142.D31 (2))")
        return 0
    if len(argv) == 5 and argv[0] == "source-verdict":
        document = json.loads(Path(argv[2]).read_text())
        return source_verdict_record(Path(argv[1]), document, Path(argv[3]), Path(argv[4]))
    if len(argv) == 2 and argv[0] == "ship-equal":
        return ship_equal(Path(argv[1]))
    if len(argv) == 4 and argv[0] == "binding":
        return binding(Path(argv[1]), argv[2], argv[3])
    if len(argv) == 2 and argv[0] == "shipped":
        try:
            print("\n".join(shipped_packages(Path(argv[1]))))
        except Refused as refused:
            print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
            return 1
        return 0
    if len(argv) >= 3 and argv[0] == "bound":
        return bound(Path(argv[1]), argv[2:])
    if len(argv) == 11 and argv[0] == "key-first":
        try:
            document = key_first_document(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5],
                                          Path(argv[6]), Path(argv[7]), tuple(argv[8].split()), argv[10])
        except Refused as refused:
            print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
            return 1
        Path(argv[9]).write_text(json.dumps(document, sort_keys=True))
        print(key_of(document))
        return 0
    if len(argv) >= 8 and argv[0] == "restore-document" and argv[7] in (RESTORE_NEEDED, RESTORE_BUILT):
        document = restore_document(Path(argv[1]), Path(argv[2]), Path(argv[3]), tuple(argv[4].split()),
                                    argv[5], argv[7], set(argv[8:]))
        Path(argv[6]).write_text(json.dumps(document, sort_keys=True))
        return 0
    if len(argv) >= 4 and argv[0] == "upstream-document":
        try:
            document = upstream_document(Path(argv[1]), argv[2], argv[4:])
        except Refused as refused:
            print(f"artefact_store: REFUSED {refused}", file=sys.stderr)
            return 1
        Path(argv[3]).write_text(json.dumps(document, sort_keys=True))
        print(f"artefact_store: UPSTREAM {len(document['upstream'])} package(s) recorded at {argv[2]}: "
              + ", ".join(sorted(document["upstream"])))
        return 0
    if len(argv) in (6, 8) and argv[0] == "restore":
        return restore(Path(argv[1]), Path(argv[2]), json.loads(Path(argv[3]).read_text()), Path(argv[4]),
                       argv[5], (Path(argv[6]), Path(argv[7])) if len(argv) == 8 else None)
    if len(argv) in (7, 8) and argv[0] == "serve":
        return serve(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4], argv[5], Path(argv[6]),
                     Path(argv[7]) if len(argv) == 8 else None)
    if len(argv) == 2 and argv[0] == "restore-ledger":
        print(restore_ledger(Path(argv[1])))
        return 0
    if len(argv) == 3 and argv[0] == "transports":
        print("\n".join(third_party_transports(Path(argv[1]), argv[2])))
        return 0
    if len(argv) == 6 and argv[0] == "third-party-plan":
        return third_party_plan(Path(argv[1]), Path(argv[2]), Path(argv[3]), tuple(argv[4].split()),
                                argv[5])
    if len(argv) == 8 and argv[0] == "third-party-step":
        return third_party_step(Path(argv[1]), Path(argv[2]), Path(argv[3]), tuple(argv[4].split()),
                                argv[5], Path(argv[6]), Path(argv[7]))
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
    except Refused as refused:
        sys.exit(f"artefact_store: REFUSED {refused}")
