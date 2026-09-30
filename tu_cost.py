#!/usr/bin/env python3
"""tu_cost.py <timeout seconds> <cost file> <command...> — run one tool invocation under a time
cap and record what it cost.

WHY IT EXISTS. `malf lint` fans one clang-tidy out per translation unit, and the width of that
fan-out is derived from the memory one child may take. That figure is a measurement, so every
child leaves one: `<elapsed ms> <max RSS KiB> <peak address space KiB>`, one line in the cost file.
The third figure is there because the child's cap is an ADDRESS-SPACE limit (`ulimit -v`), which
bounds virtual size, not resident memory: a cap chosen from RSS alone would be validated by
killing translation units.

IT KEEPS `timeout(1)`'s CONTRACT, because it stands where that tool stood:
  * the command's own exit code is this process's, a signal death as 128 + the signal;
  * past the cap the command is sent SIGTERM and the exit code is 124, then SIGKILL if it has
    not gone within `KILL_AFTER_S`;
  * the command runs in a process group of its own and the whole group is signalled, so a
    wrapper script's children do not outlive it;
  * SIGINT, SIGTERM and SIGHUP received here are passed on to that group.

THE THREE FIGURES. Elapsed is wall time on the monotonic clock. Max RSS is `ru_maxrss` of the
waited-for children, the largest resident set any process of the command's tree reached. Peak
address space is the command process's `VmPeak`, read from `/proc/<pid>/status` every
`POLL_S` while it runs — itself a high-water mark, so the last reading misses only what the
process grew by in its final `POLL_S`; it is 0 where `/proc` does not report it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

TIMED_OUT_EXIT = 124
SIGNAL_EXIT_BASE = 128
KILL_AFTER_S = 10.0
POLL_S = 0.2
USAGE_EXIT = 125


def vm_peak_kib(pid: int) -> int:
    """`VmPeak` of `pid` in KiB, or 0 when the process is gone or the file does not say it."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmPeak:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def signal_group(pid: int, signum: int) -> None:
    """Send `signum` to the command's process group; a group already gone is not an error."""
    try:
        os.killpg(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: tu_cost.py <timeout seconds> <cost file> <command...>", file=sys.stderr)
        return USAGE_EXIT
    try:
        cap_s = float(argv[1])
    except ValueError:
        print(f"tu_cost.py: the timeout {argv[1]!r} is not a number of seconds", file=sys.stderr)
        return USAGE_EXIT
    cost_file, command = argv[2], argv[3:]
    started = time.monotonic()
    try:
        child = subprocess.Popen(command, preexec_fn=os.setpgrp)
    except OSError as error:
        print(f"tu_cost.py: cannot run {command[0]}: {error}", file=sys.stderr)
        return 127 if isinstance(error, FileNotFoundError) else 126
    for forwarded in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(forwarded, lambda signum, _frame: signal_group(child.pid, signum))
    peak = 0
    timed_out = False
    killed = False
    while child.poll() is None:
        peak = max(peak, vm_peak_kib(child.pid))
        waited = time.monotonic() - started
        if not timed_out and waited >= cap_s:
            timed_out = True
            signal_group(child.pid, signal.SIGTERM)
        elif timed_out and not killed and waited >= cap_s + KILL_AFTER_S:
            killed = True
            signal_group(child.pid, signal.SIGKILL)
        try:
            child.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            pass
    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        import resource
        max_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except ImportError:
        max_rss = 0
    try:
        with open(cost_file, "w", encoding="utf-8") as cost:
            cost.write(f"{elapsed_ms} {max_rss} {peak}\n")
    except OSError as error:
        print(f"tu_cost.py: the cost file {cost_file} could not be written: {error}",
              file=sys.stderr)
    if timed_out:
        return TIMED_OUT_EXIT
    code = child.returncode
    return code if code >= 0 else SIGNAL_EXIT_BASE - code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
