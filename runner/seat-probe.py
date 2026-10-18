#!/usr/bin/env python3
"""seat-probe.py — the seat proof of isolate-runner-wsl.sh (`--prove`, and the CPU half at the end of
every `--apply`): what the kernel DOES with a runner seat, never what its configuration says
(DN-119.D9, build rows R2 and R3).

    seat-probe.py cpu    --slice <slice> --cpus <list> [--unit <unit> ...]
    seat-probe.py memory --slice <slice> --max <bytes> [--min <bytes>] [--swap-zero]

Each arm prints `ok` or `BAD` with the reading it judged; the exit is 1 when any arm is BAD and 2
when an arm could not be run. Run from the DESK (any cgroup outside the seat slices): the CPU arms
judge where a desk task can run.

RED FIRST. Every arm judges behaviour, so on a box whose runner still sits in system.slice it reds:
a desk task pins onto a release CPU, an unpinned desk loop lands on one, an allocation past the cap
is not killed, and desk pressure evicts the slice's page cache. The CPU arms need no root, so their
red is reproducible from any desk shell; the memory arms start transient units and need root.
"""

import argparse
import errno
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
MIB = 1 << 20
GIB = 1 << 30
# The CPU arm samples each desk loop's last CPU (field 39 of /proc/<pid>/stat) this many rounds.
SAMPLE_ROUNDS = 1000
SAMPLE_PERIOD_S = 0.002
# An allocation this far past the cap must be killed inside the seat.
OVERSHOOT_BYTES = 512 * MIB
# The page cache the memory.min arm parks in the seat, and the share of it that must survive desk
# pressure. Below the slice's memory.min the kernel reclaims none of it; the slack is for pages the
# holder itself never touched again (its interpreter, the file's metadata). The pages are charged to
# the holder's own transient unit, a leaf with memory.min 0, so the arm also proves the slice's
# guarantee reaches its leaves (memory_recursiveprot on the cgroup2 mount), as a job's pages need.
HOLD_BYTES = 2 * GIB
PROTECTED_SHARE = 0.95
# The desk pressure exceeds what the kernel calls available by this much, so every reclaimable
# page outside a protection is reclaimed: the seat's cache included, unless memory.min holds it.
PRESSURE_EXCESS_BYTES = 1 * GIB
HOLD_READY_TIMEOUT_S = 300
HOLD_PATH_DIR = Path("/var/tmp")

ALLOCATE = (
    "import sys\n"
    "remaining = int(sys.argv[1])\n"
    "chunk = 64 << 20\n"
    "held = []\n"
    "while remaining > 0:\n"
    "    held.append(b'\\x01' * min(chunk, remaining))\n"
    "    remaining -= chunk\n"
)

HOLD = (
    "import os, sys, time\n"
    "path, size, ready = sys.argv[1], int(sys.argv[2]), sys.argv[3]\n"
    "block = b'\\x02' * (16 << 20)\n"
    "with open(path, 'wb') as out:\n"
    "    written = 0\n"
    "    while written < size:\n"
    "        out.write(block)\n"
    "        written += len(block)\n"
    "    out.flush()\n"
    "    os.fsync(out.fileno())\n"
    "with open(path, 'rb') as back:\n"
    "    while back.read(16 << 20):\n"
    "        pass\n"
    "open(ready, 'w').close()\n"
    "time.sleep(900)\n"
)


class Verdict:
    def __init__(self) -> None:
        self.bad = 0

    def judge(self, ok: bool, text: str) -> None:
        self.bad += not ok
        print(f"  {'ok ' if ok else 'BAD'} {text}", flush=True)


def parse_cpus(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        low, _, high = part.partition("-")
        cpus.update(range(int(low), int(high or low) + 1))
    return cpus


def show(cpus: set[int]) -> str:
    return ",".join(str(cpu) for cpu in sorted(cpus)) or "(none)"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as error:
        return f"<unreadable: {errno.errorcode.get(error.errno, error.errno)}>"


def own_cgroup() -> str:
    for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
        if line.startswith("0::"):
            return line[3:]
    return ""


def last_cpu(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    return int(stat[stat.rindex(")") + 2:].split()[36])


def cpu_arms(slice_name: str, seat: set[int], units: list[str]) -> int:
    verdict = Verdict()
    where = own_cgroup()
    if where.split("/")[1:2] and where.split("/")[1].startswith("seat_"):
        print(f"seat-probe: this probe runs in {where}, inside a seat; run it from the desk", file=sys.stderr)
        return 2
    seat_cg = CGROUP_ROOT / slice_name
    verdict.judge(read(seat_cg / "cpuset.cpus.partition") == "root",
                  f"{slice_name} cpuset.cpus.partition reads '{read(seat_cg / 'cpuset.cpus.partition')}' (want root)")
    effective = read(seat_cg / "cpuset.cpus.effective")
    verdict.judge(not effective.startswith("<") and parse_cpus(effective) == seat,
                  f"{slice_name} cpuset.cpus.effective reads '{effective}' (want {show(seat)})")
    for desk in ("init.scope", "user.slice", "system.slice"):
        desk_effective = read(CGROUP_ROOT / desk / "cpuset.cpus.effective")
        if desk_effective.startswith("<"):
            continue
        shared = parse_cpus(desk_effective) & seat
        verdict.judge(not shared, f"{desk} cpuset.cpus.effective {desk_effective} shares {show(shared)} with the seat")
    for cpu in sorted(seat):
        child = subprocess.run(
            [sys.executable, "-I", "-c",
             "import errno, os, sys\n"
             "try:\n"
             "    os.sched_setaffinity(0, {int(sys.argv[1])})\n"
             "    print('PINNED')\n"
             "except OSError as error:\n"
             "    print(errno.errorcode.get(error.errno, error.errno))\n", str(cpu)],
            capture_output=True, text=True, check=False)
        got = child.stdout.strip() or child.stderr.strip()
        verdict.judge(got == "EINVAL", f"a desk task pinned onto cpu {cpu}: {got} (want EINVAL)")
    loops = []
    try:
        for _ in range(os.cpu_count() or 1):
            pid = os.fork()
            if pid == 0:
                while True:
                    pass
            loops.append(pid)
        landed: dict[int, int] = {}
        for _ in range(SAMPLE_ROUNDS):
            for pid in loops:
                cpu = last_cpu(pid)
                if cpu in seat:
                    landed[cpu] = landed.get(cpu, 0) + 1
            time.sleep(SAMPLE_PERIOD_S)
    finally:
        for pid in loops:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    verdict.judge(not landed,
                  f"{len(loops)} unpinned desk loops sampled {SAMPLE_ROUNDS} rounds: "
                  + (f"seen on seat cpu(s) {', '.join(f'{c} x{n}' for c, n in sorted(landed.items()))}" if landed
                     else "never on a seat cpu"))
    for unit in units:
        props = dict(line.split("=", 1) for line in subprocess.run(
            ["systemctl", "show", "-p", "ControlGroup", "-p", "MainPID", unit],
            capture_output=True, text=True, check=False).stdout.splitlines() if "=" in line)
        group = props.get("ControlGroup", "")
        verdict.judge(group.startswith(f"/{slice_name}/"), f"{unit} runs in '{group or '(not running)'}' (want /{slice_name}/...)")
        main_pid = int(props.get("MainPID", "0") or 0)
        if main_pid:
            affinity = os.sched_getaffinity(main_pid)
            verdict.judge(affinity <= seat, f"{unit} main pid {main_pid} may run on {show(affinity)} (want within {show(seat)})")
    return 1 if verdict.bad else 0


def memory_events(slice_cg: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    for line in read(slice_cg / "memory.events").splitlines():
        key, _, value = line.partition(" ")
        if value.isdigit():
            events[key] = int(value)
    return events


def memory_stat(slice_cg: Path, key: str) -> int:
    for line in read(slice_cg / "memory.stat").splitlines():
        name, _, value = line.partition(" ")
        if name == key and value.isdigit():
            return int(value)
    return -1


def recursive_protection() -> bool:
    for line in Path("/proc/mounts").read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == str(CGROUP_ROOT) and fields[2] == "cgroup2":
            return "memory_recursiveprot" in fields[3].split(",")
    return False


def mem_available() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("/proc/meminfo carries no MemAvailable")


def transient(slice_name: str, name: str, code: str, args: list[str], wait: bool,
              extra: list[str]) -> subprocess.Popen[str]:
    argv = ["systemd-run", f"--slice={slice_name}", f"--unit={name}", "--quiet", "--collect",
            "--service-type=exec", "-p", "OOMScoreAdjust=1000", *extra]
    if wait:
        argv.append("--wait")
    argv += ["/usr/bin/python3", "-I", "-c", code, *args]
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def memory_arms(slice_name: str, cap: int, guarantee: int | None, swap_zero: bool) -> int:
    if os.geteuid() != 0:
        print("seat-probe: the memory arms start transient units: run as root", file=sys.stderr)
        return 2
    verdict = Verdict()
    slice_cg = CGROUP_ROOT / slice_name
    if not slice_cg.is_dir():
        print(f"seat-probe: {slice_cg} does not exist", file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    before = memory_events(slice_cg)
    probe = transient(slice_name, f"seat-probe-oom-{tag}", ALLOCATE, [str(cap + OVERSHOOT_BYTES)], True,
                      ["-p", "MemorySwapMax=0"])
    swap_seen = 0
    while probe.poll() is None:
        swap_now = read(slice_cg / "memory.swap.current")
        if swap_now.isdigit():
            swap_seen = max(swap_seen, int(swap_now))
        time.sleep(0.1)
    output = probe.stdout.read().strip() if probe.stdout else ""
    after = memory_events(slice_cg)
    limit_hits = after.get("oom", 0) - before.get("oom", 0)
    kills = after.get("oom_kill", 0) - before.get("oom_kill", 0)
    verdict.judge(probe.returncode != 0 and limit_hits >= 1 and kills >= 1,
                  f"an allocation of {(cap + OVERSHOOT_BYTES) // MIB} MiB in {slice_name}: exit {probe.returncode}, "
                  f"memory.events oom +{limit_hits}, oom_kill +{kills} (want a kill AT the seat's cap: both +1 or more)"
                  + (f" [{output}]" if output else ""))
    if swap_zero:
        verdict.judge(swap_seen == 0, f"{slice_name} memory.swap.current peaked at {swap_seen // MIB} MiB while it ran (want 0)")
    if guarantee is not None:
        ready = HOLD_PATH_DIR / f"seat-probe-ready-{tag}"
        hold_file = HOLD_PATH_DIR / f"seat-probe-hold-{tag}"
        hold_unit = f"seat-probe-hold-{tag}"
        holder = transient(slice_name, hold_unit, HOLD, [str(hold_file), str(HOLD_BYTES), str(ready)], False, [])
        holder.wait()
        try:
            deadline = time.monotonic() + HOLD_READY_TIMEOUT_S
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.5)
            if not ready.exists():
                print(f"seat-probe: the holder did not park its {HOLD_BYTES // MIB} MiB in {HOLD_READY_TIMEOUT_S} s", file=sys.stderr)
                return 2
            held = memory_stat(slice_cg, "file")
            pressure_bytes = mem_available() + PRESSURE_EXCESS_BYTES
            pressure = transient("system.slice", f"seat-probe-pressure-{tag}", ALLOCATE, [str(pressure_bytes)], True,
                                 ["-p", "MemorySwapMax=0"])
            pressure.wait()
            kept = memory_stat(slice_cg, "file")
            verdict.judge(held > 0 and kept >= held * PROTECTED_SHARE,
                          f"{slice_name} page cache under {pressure_bytes // MIB} MiB of desk pressure: "
                          f"{held // MIB} MiB held, {kept // MIB} MiB kept (want at least {int(PROTECTED_SHARE * 100)} %; "
                          f"memory.min reads {read(slice_cg / 'memory.min')}; cgroup2 mount "
                          f"{'carries' if recursive_protection() else 'LACKS'} memory_recursiveprot)")
        finally:
            subprocess.run(["systemctl", "stop", f"{hold_unit}.service"], check=False)
            hold_file.unlink(missing_ok=True)
            ready.unlink(missing_ok=True)
    return 1 if verdict.bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parser.add_subparsers(dest="verb", required=True)
    cpu = verbs.add_parser("cpu")
    cpu.add_argument("--slice", required=True)
    cpu.add_argument("--cpus", required=True)
    cpu.add_argument("--unit", action="append", default=[])
    memory = verbs.add_parser("memory")
    memory.add_argument("--slice", required=True)
    memory.add_argument("--max", type=int, required=True)
    memory.add_argument("--min", type=int)
    memory.add_argument("--swap-zero", action="store_true")
    args = parser.parse_args()
    if args.verb == "cpu":
        return cpu_arms(args.slice, parse_cpus(args.cpus), args.unit)
    return memory_arms(args.slice, args.max, args.min, args.swap_zero)


if __name__ == "__main__":
    sys.exit(main())
