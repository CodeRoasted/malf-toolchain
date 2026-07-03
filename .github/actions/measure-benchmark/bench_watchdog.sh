#!/usr/bin/env bash
# bench_watchdog.sh <timeout-seconds> -- <command...>
#
# Run <command...> (a `malf bench ...` invocation) under a deadlock watchdog. If it does not finish
# within <timeout-seconds> it is presumed HUNG (a benchmark deadlock), and BEFORE killing it we dump a
# full thread backtrace of every process in its tree via gdb — so an intermittent hang is
# self-diagnosing (you get the stuck threads' stacks) instead of burning the job's wall-clock until the
# 6h runner limit. Shipped in malf-toolchain and used by measure-benchmark, so every repo inherits it.
#
# On a clean finish it is transparent: it forwards the command's exit code. On timeout it exits 124
# after printing the backtraces.

set -uo pipefail

TIMEOUT="${1:?usage: bench_watchdog.sh <timeout-seconds> -- <command...>}"; shift
[ "${1:-}" = "--" ] && shift
[ "$#" -ge 1 ] || { echo "bench_watchdog: no command given" >&2; exit 2; }

POLL_SECONDS=5

# Best-effort so the dump can attach; never block on an interactive sudo (self-hosted runners have no
# passwordless sudo) and never fail the run just because a diagnostic prerequisite is missing.
command -v gdb >/dev/null 2>&1 || sudo -n apt-get install -y -qq gdb >/dev/null 2>&1 || true
# YAMA ptrace_scope=1 would block attaching to a non-child; relax it if we can (else attach may fail).
sudo -n sysctl -w kernel.yama.ptrace_scope=0 >/dev/null 2>&1 || true

dump_thread_backtraces() {
  local root="$1" kid
  # Depth-first: dump the leaves (the actual bench binary) before the driver processes above it.
  for kid in $(ps -o pid= --ppid "$root" 2>/dev/null); do
    dump_thread_backtraces "$kid"
  done
  local comm; comm="$(ps -o comm= -p "$root" 2>/dev/null || true)"
  [ -n "$comm" ] || return 0
  echo "::group::gdb thread apply all bt full — pid $root ($comm)"
  if command -v gdb >/dev/null 2>&1; then
    gdb --batch -p "$root" \
        -ex 'set pagination off' \
        -ex 'thread apply all bt full' \
        -ex 'info registers' 2>&1 | tail -400
  else
    echo "gdb unavailable — cannot backtrace pid $root"
    # Fallback: the kernel's own view of every thread's stack.
    for t in "/proc/$root/task/"*; do
      [ -r "$t/stack" ] && { echo "-- $t/stack --"; cat "$t/stack" 2>/dev/null; }
    done
  fi
  echo "::endgroup::"
}

"$@" &
CMD_PID=$!

elapsed=0
while kill -0 "$CMD_PID" 2>/dev/null; do
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "::error::bench watchdog: the benchmark exceeded ${TIMEOUT}s — presumed DEADLOCK. Dumping thread backtraces of the process tree below, then killing it."
    dump_thread_backtraces "$CMD_PID"
    # Kill the whole tree (leaves first via pkill -P, then the root).
    pkill -9 -P "$CMD_PID" 2>/dev/null || true
    kill -9 "$CMD_PID" 2>/dev/null || true
    exit 124
  fi
  sleep "$POLL_SECONDS"
  elapsed=$((elapsed + POLL_SECONDS))
done

wait "$CMD_PID"
exit $?
