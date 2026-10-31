"""Every first-party package carries `licenses/THIRD-PARTY-NOTICES.txt`: the verbatim licence files of its third-party
closure, written inside its create by this ONE helper (DN-142.D39 (1), (3), (4)).

THE CLOSURE of a package P is every third-party package conan's `self.dependencies.host` names at package time (the
host context, no test requirement, nothing skipped), UNION the components of every first-party upstream it names: a
header-only library a static upstream instantiated is inside P's linked binary, yet conan may skip it in P's graph,
and the upstream's own file still lists it. A dependency is classified BY ITS BYTES, with no namespace list: one whose
package folder holds `licenses/THIRD-PARTY-NOTICES.txt` is a first-party upstream, whose components join the union;
any other is a component. So the hosted Windows job, a raw `conan create` with no malf driver, runs it unchanged.

THE COMPONENT KEY is `<name>/<version>#<recipe revision>`, built from the reference's three fields and never from its
printed form: conan 2.28 prints a cache-local timestamp after the revision (`#<rrev>%<seconds>`), which would make the
file a function of the cache (DN-142.D39 (3), measured in G2).

THE PACKAGE'S OWN LICENCE rides the same call: the repository's `LICENSE`, which malf's recipe-exports helper puts at
the root of every export (`malf_recipe_exports.py`), is copied to `licenses/LICENSE`. A first-party package is then
never shipped without the terms it is under; a create whose export lacks it fails.

FORMAT 1, byte-deterministic (UTF-8 framing, LF ends, no time, no host, no absolute path):

    THIRD-PARTY-NOTICES 1
    package: <name>/<version>
    note: <NOTE, below, verbatim>
    components: <k>
    component: <key> licence: <expression>
    file: <key> <path> <sha256> <bytes>
    ...
    <one blank line>
    === <key> <path> <sha256> <bytes>
    <exactly <bytes> bytes, verbatim><one LF, always>
    ...

Components sort bytewise by key, each followed by its file lines (paths relative to the component's package folder,
`/`-separated, sorted bytewise); the text blocks follow in the file lines' order. The parse is driven by each block's
byte count, never by a terminator, so a text with no final newline, or one holding `===`, round-trips. Changing the
note is format 2.

Staged by malf beside global.conf in every conan home it uses (malf's conf sync, setup-build-env, setup-proof-linux,
setup-proof-msvc) with `spdx_licence.py`, the one SPDX reader, which it imports from its own folder; global.conf names
it as `user.malf:recipe_notices`. Every first-party recipe ends `package()` with
`runpy.run_path(self.conf.get("user.malf:recipe_notices"))["write_notices"](self)`. It REFUSES (fails the create) on a
third-party dependency with no non-empty regular file under `licenses/`, a dependency declaring no licence, one key
reached twice with different rows, and a licence-file path holding a space, a newline or a byte that is not UTF-8.

    third_party_notices.py selftest
        the format's golden, its determinism and round trip, and every refusal, offline
"""

from __future__ import annotations

import hashlib
import runpy
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

FORMAT_LINE = "THIRD-PARTY-NOTICES 1"
NOTE = ("This package contains, or is built to link, the third-party software listed below. Each component's "
        "licence files follow verbatim, as its own package ships them.")
MEMBER = "licenses/THIRD-PARTY-NOTICES.txt"
OWN_LICENCE_SOURCE = "LICENSE"
OWN_LICENCE = "licenses/LICENSE"
LICENCES_FOLDER = "licenses"
BLOCK_MARK = "=== "
FORBIDDEN_PATH_CHARACTERS = (" ", "\n", "\r", "\t")


class NoticesRefused(Exception):
    """The notices cannot be written or read; the message names the dependency, the path or the line."""


@dataclass(frozen=True)
class LicenceFile:
    """One licence file of a component: its path in the component's package folder, its declared SHA-256 and size,
    and its bytes (as read; a parsed file's bytes may disagree with what its header declares, see `defects`)."""

    path: str
    sha256: str
    size: int
    data: bytes

    @staticmethod
    def of(path: str, data: bytes) -> "LicenceFile":
        return LicenceFile(path, hashlib.sha256(data).hexdigest(), len(data), data)


@dataclass(frozen=True)
class Component:
    """One third-party package of the closure: its key, its licence expression, its licence files sorted by path."""

    key: str
    licence: str
    files: tuple[LicenceFile, ...]


@dataclass(frozen=True)
class Notices:
    """A whole notices file: the package it belongs to (`<name>/<version>`) and its components sorted by key."""

    package: str
    components: tuple[Component, ...]


def _spdx():
    """The one SPDX reader, staged beside this file in every conan home and present beside it in malf."""
    return runpy.run_path(str(Path(__file__).resolve().parent / "spdx_licence.py"))


def component_key(reference) -> str:
    """`<name>/<version>#<revision>` from a conan reference's three fields, never from its printed form."""
    name, version, revision = reference.name, reference.version, reference.revision
    if not name or version is None or not revision:
        raise NoticesRefused(f"the reference {name}/{version} carries no recipe revision, and a component is keyed "
                             f"by it")
    return f"{name}/{version}#{revision}"


def _check_path(where: str, path: str) -> None:
    if any(character in path for character in FORBIDDEN_PATH_CHARACTERS):
        raise NoticesRefused(f"{where}: the licence file path {path!r} holds a space or a line break, which the "
                             f"line grammar of format 1 cannot carry")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as broken:
        raise NoticesRefused(f"{where}: the licence file path {path!r} is not UTF-8 ({broken})") from broken


def licence_files(where: str, folder: Path) -> tuple[LicenceFile, ...]:
    """Every regular file under `folder/licenses`, recursively, by its `/`-separated path relative to `folder`;
    refused when none of them is non-empty."""
    root = folder / LICENCES_FOLDER
    found = []
    for path in sorted(root.rglob("*")) if root.is_dir() else ():
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        _check_path(where, relative)
        found.append(LicenceFile.of(relative, path.read_bytes()))
    if not any(each.size for each in found):
        raise NoticesRefused(f"{where}: its package holds no non-empty regular file under {LICENCES_FOLDER}/, so "
                             f"its licence cannot be carried")
    return tuple(sorted(found, key=lambda each: each.path.encode("utf-8")))


def compose(package: str, direct: list[Component], upstreams: list[Notices]) -> Notices:
    """The closure: the direct components union every upstream's components, sorted by key. Refused when one key
    is reached twice with a different licence or different file rows."""
    chosen: dict[str, Component] = {}
    for component in [*direct, *(each for notices in upstreams for each in notices.components)]:
        held = chosen.get(component.key)
        if held is not None and _rows(held) != _rows(component):
            raise NoticesRefused(f"{package}: the component {component.key} is reached twice with different rows "
                                 f"({_described(held)} against {_described(component)})")
        chosen[component.key] = component
    return Notices(package, tuple(chosen[key] for key in sorted(chosen, key=lambda key: key.encode("utf-8"))))


def _rows(component: Component) -> tuple:
    return component.licence, tuple((each.path, each.sha256, each.size) for each in component.files)


def _described(component: Component) -> str:
    return f"licence {component.licence}, " + ", ".join(f"{each.path} {each.sha256}" for each in component.files)


def render(notices: Notices) -> bytes:
    """The file's bytes, format 1."""
    lines = [FORMAT_LINE, f"package: {notices.package}", f"note: {NOTE}", f"components: {len(notices.components)}"]
    blocks: list[bytes] = []
    for component in notices.components:
        if "\n" in component.licence or " " in component.key:
            raise NoticesRefused(f"{component.key}: a key holds no space and a licence no line break")
        lines.append(f"component: {component.key} licence: {component.licence}")
        for each in component.files:
            _check_path(component.key, each.path)
            header = f"{component.key} {each.path} {hashlib.sha256(each.data).hexdigest()} {len(each.data)}"
            lines.append(f"file: {header}")
            blocks.append(f"{BLOCK_MARK}{header}\n".encode("utf-8") + each.data + b"\n")
    return ("\n".join(lines) + "\n\n").encode("utf-8") + b"".join(blocks)


def parse(data: bytes) -> Notices:
    """The notices `data` holds, format 1; raises NoticesRefused naming the first defect. A text block's bytes are
    kept as read, with its header's declared digest and size: `defects` compares the two."""
    head, separator, body = data.partition(b"\n\n")
    if not separator:
        raise NoticesRefused("no blank line ends the header")
    try:
        lines = head.decode("utf-8").split("\n")
    except UnicodeDecodeError as broken:
        raise NoticesRefused(f"the header is not UTF-8 ({broken})") from broken
    if lines[:1] != [FORMAT_LINE]:
        raise NoticesRefused(f"the first line is {lines[:1]!r}, not {FORMAT_LINE!r}")
    if len(lines) < 4 or not lines[1].startswith("package: ") or lines[2] != f"note: {NOTE}" \
            or not lines[3].startswith("components: "):
        raise NoticesRefused("the header is not `package:`, the format's `note:` and `components:`")
    package, count = lines[1].removeprefix("package: "), lines[3].removeprefix("components: ")
    if not count.isdigit():
        raise NoticesRefused(f"`components: {count}` is no count")
    rows: list[tuple[str, str, list[tuple[str, str, int]]]] = []
    for line in lines[4:]:
        if line.startswith("component: "):
            key, marker, licence = line.removeprefix("component: ").partition(" licence: ")
            if not marker or not key or not licence or " " in key:
                raise NoticesRefused(f"the line {line!r} is not `component: <key> licence: <expression>`")
            rows.append((key, licence, []))
            continue
        fields = line.removeprefix("file: ").split(" ") if line.startswith("file: ") else []
        if len(fields) != 4 or not rows or fields[0] != rows[-1][0] or not fields[3].isdigit():
            raise NoticesRefused(f"the line {line!r} is not `file: <key> <path> <sha256> <bytes>` under its component")
        rows[-1][2].append((fields[1], fields[2], int(fields[3])))
    if len(rows) != int(count):
        raise NoticesRefused(f"`components: {count}` and {len(rows)} component line(s)")
    components, offset = [], 0
    for key, licence, files in rows:
        read = []
        for path, sha, size in files:
            header = f"{BLOCK_MARK}{key} {path} {sha} {size}\n".encode("utf-8")
            if body[offset:offset + len(header)] != header:
                raise NoticesRefused(f"the text block of {key} {path} does not start where its file line says, with "
                                     f"its header")
            start = offset + len(header)
            if body[start + size:start + size + 1] != b"\n":
                raise NoticesRefused(f"the text block of {key} {path} is not {size} byte(s) and one line feed")
            read.append(LicenceFile(path, sha, size, body[start:start + size]))
            offset = start + size + 1
        components.append(Component(key, licence, tuple(read)))
    if offset != len(body):
        raise NoticesRefused(f"{len(body) - offset} byte(s) follow the last text block")
    return Notices(package, tuple(components))


def defects(notices: Notices) -> list[str]:
    """`<key> <path>: text hashes <sha>, the header says <sha>` for every block whose bytes do not hash to the digest
    its header declares."""
    return [f"{component.key} {each.path}: text hashes {hashlib.sha256(each.data).hexdigest()}, the header says "
            f"{each.sha256}"
            for component in notices.components for each in component.files
            if hashlib.sha256(each.data).hexdigest() != each.sha256]


def component_of(key: str, licence: object, folder: Path) -> Component:
    """A third-party dependency as a component: its key, its declared licence through the one SPDX reader, every
    licence file of its package folder."""
    try:
        expression = _spdx()["expression_of"](licence)
    except ValueError as missing:
        raise NoticesRefused(f"{key} declares no licence ({missing})") from missing
    return Component(key, expression, licence_files(key, folder))


def notices_of(package: str, dependencies: list[tuple[str, object, Path]]) -> Notices:
    """The notices of `package` from its host dependencies, each (key, declared licence, package folder): a folder
    holding the member is a first-party upstream, any other a component."""
    direct, upstreams = [], []
    for key, licence, folder in dependencies:
        member = folder / MEMBER
        if member.is_file():
            try:
                upstreams.append(parse(member.read_bytes()))
            except NoticesRefused as unread:
                raise NoticesRefused(f"{key}'s {MEMBER} cannot be read: {unread}") from unread
        else:
            direct.append(component_of(key, licence, folder))
    return compose(package, direct, upstreams)


def write_notices(conanfile) -> None:
    """The recipe entry: `licenses/THIRD-PARTY-NOTICES.txt` and `licenses/LICENSE` in the package folder.

    pre: called at the end of `package()`; the export carries the repository's `LICENSE` at its root
    post: both files written, or the create failed naming why
    """
    from conan.errors import ConanException

    package = f"{conanfile.name}/{conanfile.version}"
    try:
        dependencies = [(component_key(dependency.ref), dependency.license, Path(dependency.package_folder))
                        for _require, dependency in conanfile.dependencies.host.items()]
        notices = notices_of(package, dependencies)
        data = render(notices)
    except NoticesRefused as refused:
        raise ConanException(f"malf: {package}: the third-party notices cannot be written (DN-142.D39): "
                             f"{refused}") from refused
    own = Path(conanfile.folders.base_source) / OWN_LICENCE_SOURCE
    if not own.is_file() or not own.stat().st_size:
        raise ConanException(f"malf: {package}: its export carries no {OWN_LICENCE_SOURCE} at {own}, the "
                             f"repository's licence malf's recipe-exports helper puts there; a first-party package "
                             f"is never shipped without its own terms")
    target = Path(conanfile.package_folder) / MEMBER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    shutil.copyfile(own, Path(conanfile.package_folder) / OWN_LICENCE)
    conanfile.output.info(f"malf: {MEMBER} lists {len(notices.components)} third-party component(s); "
                          f"{OWN_LICENCE} is the repository's {OWN_LICENCE_SOURCE}")


# ── the selftest ─────────────────────────────────────────────────────────────────────────────────

GOLDEN = Path(__file__).resolve().parent / "tests" / "fixtures" / "third_party_notices" / "golden-v1.txt"


class _Reference:
    """A conan reference's three fields, and the printed form conan 2.28 gives it (with its cache timestamp)."""

    def __init__(self, name: str, version: str, revision: str) -> None:
        self.name, self.version, self.revision = name, version, revision

    def __str__(self) -> str:
        return f"{self.name}/{self.version}#{self.revision}%1793435009.8775158"


def _fixture_package(root: Path, name: str, files: dict[str, bytes]) -> Path:
    folder = root / name
    for relative, data in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(data)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def selftest() -> int:
    import tempfile

    failures: list[str] = []
    checked = [0]

    def expect(what: str, actual: object, expected: object) -> None:
        checked[0] += 1
        if actual != expected:
            failures.append(f"{what}: expected {expected!r}, actual {actual!r}")

    def refused(what: str, act, needle: str) -> None:
        checked[0] += 1
        try:
            act()
        except NoticesRefused as refusal:
            if needle not in str(refusal):
                failures.append(f"{what}: refused, but the message lacks {needle!r}: {refusal}")
            return
        failures.append(f"{what}: expected a refusal naming {needle!r}, and it passed")

    with tempfile.TemporaryDirectory(prefix="third_party_notices.") as scratch:
        root = Path(scratch)
        fmt = _fixture_package(root, "fmt", {"licenses/LICENSE": b"Copyright (c) fmt authors\nMIT terms\n"})
        zlib = _fixture_package(root, "zlib", {"licenses/LICENSE": b"zlib terms, no final newline",
                                               "licenses/extra/NOTE.txt": b"=== looks like a header\n===\n"})
        upstream_notices = Notices("insight_canon/1.0.0", (
            Component("simdjson/4.6.3#aaaa", "Apache-2.0 AND MIT",
                      (LicenceFile.of("licenses/LICENSE", b"simdjson licence text\n"),)),
            Component("fmt/12.1.0#bbbb", "MIT", (LicenceFile.of("licenses/LICENSE", b"Copyright (c) fmt authors\nMIT terms\n"),)),))
        canon = _fixture_package(root, "canon", {MEMBER: render(upstream_notices), "licenses/LICENSE": b"Apache\n"})
        dependencies = [("zlib/1.3.1#cccc", "Zlib", zlib), ("fmt/12.1.0#bbbb", ("MIT",), fmt),
                        ("insight_canon/1.0.0#dddd", "Apache-2.0", canon)]
        made = notices_of("insight_sift_tools/1.0.0", dependencies)
        data = render(made)
        expect("the fixture closure renders the committed golden", data, GOLDEN.read_bytes() if GOLDEN.is_file() else None)
        expect("the same inputs in shuffled order render identical bytes",
               render(notices_of("insight_sift_tools/1.0.0", list(reversed(dependencies)))), data)
        expect("a first-party upstream is no component, its components join the union",
               [each.key for each in made.components], ["fmt/12.1.0#bbbb", "simdjson/4.6.3#aaaa", "zlib/1.3.1#cccc"])
        expect("parse(render(x)) == x, a text with no final newline and one holding === included", parse(data), made)
        expect("an empty closure renders components: 0 and parses back",
               (render(Notices("coderoast_ipc_core/1.0.0", ())).split(b"\n")[3],
                parse(render(Notices("coderoast_ipc_core/1.0.0", ())))),
               (b"components: 0", Notices("coderoast_ipc_core/1.0.0", ())))
        expect("the output holds no absolute path", str(root).encode() in data or b"/home/" in data, False)
        expect("the key is built from the reference's fields: no `%<timestamp>` of its printed form",
               (component_key(_Reference("fmt", "12.1.0", "6baf0fb8")), "%" in str(_Reference("fmt", "12.1.0", "x"))),
               ("fmt/12.1.0#6baf0fb8", True))
        refused("a reference with no revision is refused", lambda: component_key(_Reference("fmt", "12.1.0", "")),
                "carries no recipe revision")
        empty = _fixture_package(root, "empty", {"licenses/LICENSE": b""})
        refused("a third-party package with an empty licenses/ is refused",
                lambda: notices_of("p/1", [("empty/1#e", "MIT", empty)]), "no non-empty regular file")
        bare = _fixture_package(root, "bare", {"include/x.h": b"x"})
        refused("a third-party package with no licenses/ at all is refused (a first-party upstream lacking its file "
                "reads exactly so)", lambda: notices_of("p/1", [("bare/1#e", "MIT", bare)]), "no non-empty regular file")
        refused("a dependency declaring no licence is refused",
                lambda: notices_of("p/1", [("fmt/12.1.0#bbbb", None, fmt)]), "declares no licence")
        other = Notices("other/1", (Component("fmt/12.1.0#bbbb", "MIT", (LicenceFile.of("licenses/LICENSE", b"other\n"),)),))
        refused("one key reached twice with different file rows is refused",
                lambda: compose("p/1", [], [upstream_notices, other]), "reached twice with different rows")
        spaced = _fixture_package(root, "spaced", {"licenses/THE LICENSE": b"terms\n"})
        refused("a licence-file path holding a space is refused",
                lambda: notices_of("p/1", [("spaced/1#s", "MIT", spaced)]), "holds a space or a line break")
        refused("a header off format 1 is refused", lambda: parse(data.replace(b"THIRD-PARTY-NOTICES 1", b"NOTICES 2")),
                "not 'THIRD-PARTY-NOTICES 1'")
        refused("a block shorter than its header says is refused",
                lambda: parse(data[:-3] + b"\n"), "is not")
        tampered = data.replace(b"MIT terms\n", b"MIT TERMS\n", 1)
        expect("a block whose bytes do not hash to its header is a defect, not a parse failure",
               len(defects(parse(tampered))), 1)
    for failure in failures:
        print(f"third_party_notices selftest: FAIL {failure}", file=sys.stderr)
    print(f"third_party_notices selftest: {checked[0] - len(failures)}/{checked[0]} arms passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] != ["selftest"]:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(selftest())
