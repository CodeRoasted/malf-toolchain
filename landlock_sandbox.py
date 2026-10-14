#!/usr/bin/env python3
"""A command run under a Landlock domain: the reads and writes it may make, and nothing else (gate G4).

DN-142.D19 (5) and its gate list: a step is reused only once what it READS is its declared input,
and a declaration is a claim until the kernel enforces it. Landlock (ABI 3 on the desk's 6.6 kernel,
measured) restricts the filesystem accesses of a process and of everything it executes, for good:
a domain is never lifted, so each command runs in a process of its own.

    landlock_sandbox.py [--read <root>]... [--write <root>]... -- <command> [<argument>...]
        <command> executed under a domain in which each --read root (a directory, or a file) is
        readable and executable beneath it, each --write root also writable, created into and
        removed from, and every other path denied: an open outside them fails with EACCES, which
        the command sees as its own error. Exit: the command's own, or 2 refused before it ran
        (no Landlock in this kernel, a root that does not exist, an empty command)
    landlock_sandbox.py abi
        the kernel's Landlock ABI version, one line; exit 1 when the kernel has none
    landlock_sandbox.py selftest
        every arm of the harness, each in a child process of its own

WHAT IT CANNOT SEE (stated, never implied): ABI 3 restricts no network access (TCP rules start at
ABI 4, kernel 6.7), and nothing a DAEMON reads on the command's behalf (the docker daemon reading an
image), so such an input enters a key by its digest or the step is ALWAYS (DN-142.D19 (9)). The
kernel reports no denial to anyone but the denied call: a denial is visible as the command's own
EACCES, and counting them per step needs a tracer of the command's calls.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
REFUSED = 2

# The filesystem rights by the ABI that introduced them (include/uapi/linux/landlock.h).
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE = 1 << 13, 1 << 14
HANDLED_BY_ABI = {1: (1 << 13) - 1, 2: (1 << 14) - 1, 3: (1 << 15) - 1}
READ_RIGHTS = EXECUTE | READ_FILE | READ_DIR
# The rights Landlock accepts on a rule whose root is a FILE: the others concern a directory's entries.
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE


class Refused(RuntimeError):
    """The domain could not be built, so the command must not run unconfined."""


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.syscall.restype = ctypes.c_long


def _syscall(number: int, *arguments) -> int:
    result = _LIBC.syscall(ctypes.c_long(number), *arguments)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def abi() -> int:
    """The kernel's Landlock ABI version; 0 when the kernel has no Landlock."""
    try:
        return _syscall(SYS_LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0),
                        ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION))
    except OSError:
        return 0


def restrict(read_roots: list[Path], write_roots: list[Path]) -> None:
    """Restrict THIS process, and everything it executes, to `read_roots` (read and execute) and
    `write_roots` (every handled right), for good.
    pre: each root exists. post: the domain is enforced, or Refused names why nothing was."""
    version = abi()
    if version < 1:
        raise Refused("this kernel answers no Landlock ABI, so no read can be confined")
    handled = HANDLED_BY_ABI[min(version, max(HANDLED_BY_ABI))]
    attributes = _RulesetAttr(handled)
    try:
        ruleset = _syscall(SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attributes),
                           ctypes.c_size_t(ctypes.sizeof(attributes)), ctypes.c_uint32(0))
    except OSError as failed:
        raise Refused(f"landlock_create_ruleset failed: {failed}") from failed
    try:
        for roots, rights in ((read_roots, READ_RIGHTS & handled), (write_roots, handled)):
            for root in roots:
                if not root.exists():
                    raise Refused(f"the root {root} does not exist")
                allowed = rights if root.is_dir() else rights & FILE_RIGHTS
                descriptor = os.open(root, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = _PathBeneathAttr(allowed, descriptor)
                    _syscall(SYS_LANDLOCK_ADD_RULE, ctypes.c_int(ruleset),
                             ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH), ctypes.byref(rule),
                             ctypes.c_uint32(0))
                except OSError as failed:
                    raise Refused(f"landlock_add_rule {root} failed: {failed}") from failed
                finally:
                    os.close(descriptor)
        if _LIBC.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise Refused(f"prctl(PR_SET_NO_NEW_PRIVS) failed: {os.strerror(ctypes.get_errno())}")
        try:
            _syscall(SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0))
        except OSError as failed:
            raise Refused(f"landlock_restrict_self failed: {failed}") from failed
    finally:
        os.close(ruleset)


def parse(argv: list[str]) -> tuple[list[Path], list[Path], list[str]]:
    """(read roots, write roots, command) of `[--read R | --write W]... -- command...`."""
    reads: list[Path] = []
    writes: list[Path] = []
    index = 0
    while index < len(argv) and argv[index] != "--":
        flag = argv[index]
        if flag not in ("--read", "--write") or index + 1 >= len(argv):
            raise Refused(f"expected --read <root>, --write <root> or --, not {flag!r}")
        (reads if flag == "--read" else writes).append(Path(argv[index + 1]).resolve())
        index += 2
    command = argv[index + 1:]
    if index >= len(argv) or not command:
        raise Refused("no command after --")
    return reads, writes, command


def sandboxed(argv: list[str]) -> int:
    """Build the domain, then become the command; returns only when either was refused."""
    try:
        reads, writes, command = parse(argv)
        restrict(reads, writes)
    except Refused as refused:
        print(f"landlock_sandbox: REFUSED — {refused}; the command did not run", file=sys.stderr)
        return REFUSED
    try:
        os.execvp(command[0], command)
    except OSError as failed:
        print(f"landlock_sandbox: {command[0]}: {failed}", file=sys.stderr)
        return 127


def selftest() -> int:
    """Each arm runs this file as a child, so no domain ever binds the selftest itself."""
    failures: list[str] = []
    armed = [0]
    here = Path(__file__).resolve()
    system = [Path(path) for path in ("/usr", "/lib", "/lib64", "/bin", "/etc") if Path(path).exists()]

    def run(reads: list[Path], writes: list[Path], command: list[str]) -> subprocess.CompletedProcess:
        argv = [sys.executable, "-I", str(here)]
        for root in (*system, *reads):
            argv += ["--read", str(root)]
        for root in writes:
            argv += ["--write", str(root)]
        return subprocess.run([*argv, "--", *command], capture_output=True, text=True, check=False)

    def arm(label: str, got: object, want: object) -> None:
        armed[0] += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory(prefix="landlock_sandbox.") as scratch:
        root = Path(scratch)
        inside, outside, work = root / "inside", root / "outside", root / "work"
        for folder in (inside, outside, work):
            folder.mkdir()
        (inside / "input").write_text("declared\n")
        (outside / "secret").write_text("undeclared\n")
        arm("the kernel answers a Landlock ABI", abi() >= 1, True)
        done = run([inside], [work], ["cat", str(inside / "input")])
        arm("a read beneath a read root succeeds", (done.returncode, done.stdout), (0, "declared\n"))
        done = run([inside], [work], ["cat", str(outside / "secret")])
        arm("RED: a read outside every root fails with EACCES, naming the path",
            (done.returncode != 0, "Permission denied" in done.stderr, str(outside / "secret") in done.stderr),
            (True, True, True))
        done = run([inside], [work], ["sh", "-c", f"echo x > {inside / 'written'}"])
        arm("RED: a write beneath a READ root fails", (done.returncode != 0, (inside / "written").exists()),
            (True, False))
        done = run([inside], [work], ["sh", "-c", f"mkdir {work / 'tree'} && echo y > {work / 'tree' / 'out'} && cat {work / 'tree' / 'out'}"])
        arm("a write root is created into, written and read", (done.returncode, done.stdout), (0, "y\n"))
        done = run([inside / "input"], [work], ["cat", str(inside / "input")])
        arm("a FILE root is readable alone", (done.returncode, done.stdout), (0, "declared\n"))
        done = run([inside / "input"], [work], ["ls", str(inside)])
        arm("RED: a file root does not open its directory", done.returncode != 0, True)
        done = run([root / "absent"], [work], ["true"])
        arm("RED: an absent root refuses before the command runs", (done.returncode, "REFUSED" in done.stderr),
            (REFUSED, True))
        done = run([inside], [work], ["sh", "-c", f"cat {outside / 'secret'} 2>/dev/null; sh -c 'cat {outside / 'secret'}'"])
        arm("RED: the domain binds every process the command starts", (done.returncode != 0, done.stdout), (True, ""))
    for failure in failures:
        print(f"landlock_sandbox selftest FAILED: {failure}")
    print(f"landlock_sandbox selftest: {armed[0]} arm(s), {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if argv == ["selftest"]:
        return selftest()
    if argv == ["abi"]:
        version = abi()
        print(version)
        return 0 if version else 1
    return sandboxed(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
