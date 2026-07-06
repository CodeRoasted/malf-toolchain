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

# Ptrace-free degraded dump: per-thread state + kernel wait channel, readable same-uid without
# any ptrace grant. Enough to tell a futex/cv park from a busy spin from a blocked syscall —
# a hung run must never end up with ZERO thread evidence (the logcraft v1.7.2 hang did: YAMA
# denied every gdb attach and the dump printed nothing usable).
dump_proc_state() {
  local root="$1" t tid
  echo "-- ptrace unavailable: /proc state+wchan fallback for pid $root --"
  for t in "/proc/$root/task/"*; do
    tid="${t##*/}"
    printf 'tid %-8s name=%-18s state=%-14s wchan=%s\n' "$tid" \
      "$(cat "$t/comm" 2>/dev/null || echo '?')" \
      "$(awk '/^State:/{print $2 $3}' "$t/status" 2>/dev/null || echo '?')" \
      "$(cat "$t/wchan" 2>/dev/null || echo '?')"
  done
  # Kernel stacks when readable (root); harmless no-op otherwise.
  for t in "/proc/$root/task/"*; do
    [ -r "$t/stack" ] && { echo "-- $t/stack --"; cat "$t/stack" 2>/dev/null; }
  done
}

dump_thread_backtraces() {
  local root="$1" kid
  # Depth-first: dump the leaves (the actual bench binary) before the driver processes above it.
  for kid in $(ps -o pid= --ppid "$root" 2>/dev/null); do
    dump_thread_backtraces "$kid"
  done
  local comm; comm="$(ps -o comm= -p "$root" 2>/dev/null || true)"
  [ -n "$comm" ] || return 0
  echo "::group::thread dump — pid $root ($comm)"
  local gdb_out=""
  if command -v gdb >/dev/null 2>&1; then
    # YAMA ptrace_scope=1 only lets an ANCESTOR attach; gdb spawned here is a
    # sibling of the bench tree, so an unprivileged attach can be denied even
    # though this script is the tree's parent. Try unprivileged, then sudo -n
    # (hosted runners have passwordless sudo; self-hosted may not).
    gdb_out="$(gdb --batch -p "$root" \
        -ex 'set pagination off' \
        -ex 'thread apply all bt full' \
        -ex 'info registers' 2>&1 || true)"
    if printf '%s\n' "$gdb_out" | grep -q 'Could not attach'; then
      gdb_out="$(sudo -n gdb --batch -p "$root" \
          -ex 'set pagination off' \
          -ex 'thread apply all bt full' \
          -ex 'info registers' 2>&1 || true)"
    fi
    printf '%s\n' "$gdb_out" | tail -400
  else
    echo "gdb unavailable — cannot backtrace pid $root"
  fi
  # No usable backtrace from gdb (absent, or every attach denied) → degraded /proc view.
  if ! printf '%s\n' "$gdb_out" | grep -Eq '^(Thread|#0)'; then
    dump_proc_state "$root"
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
