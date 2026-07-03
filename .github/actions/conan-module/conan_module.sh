#!/usr/bin/env bash
# Test / create ONE Conan module (package) with Conan + CMake directly. This is the single source of
# truth for the per-module build logic: both the `conan-module` composite action and the
# `coderoast-ci` composite's module loop invoke it (the loop is why this is a script — a composite
# cannot loop a `uses:` step over a variable-length module list).
#
# Usage: conan_module.sh <module> <test:true|false> <create:true|false> [profile] [cmake-args]
#   module      path relative to the repo root (e.g. `core`, `.`, `infra/redis`, `sift-cli`)
#   test        'true' => conan install + cmake build + ctest (Release); anything else skips the phase
#   create      'true' => conan create <module>; anything else skips it
#   profile     conan profile name in $CONAN_HOME/profiles (default linux-gcc15-release)
#   cmake-args  extra flags for the test-build configure, e.g. `-DPKG_BUILD_TESTS=ON -DPKG_BUILD_BENCH=ON`
#               (word-split into separate args; needed by packages that gate their unit tests behind a flag)
#
# Assumes the profile is already installed in $CONAN_HOME/profiles (by setup-build-env); it does NOT
# vendor or copy a profile itself. Build + test + create output is mirrored to $GITHUB_WORKSPACE/
# build.log so the Sift dogfood can diff it; the crash diagnostic prints to the console only.

set -euo pipefail

MODULE="${1:?module arg required}"
TEST="${2:?test arg required}"
CREATE="${3:?create arg required}"
PROFILE_NAME="${4:-linux-gcc15-release}"
PROFILE="$CONAN_HOME/profiles/$PROFILE_NAME"
CMAKE_ARGS="${5:-}"   # extra -D flags for the test-build configure; intentionally word-split below
LOG="$GITHUB_WORKSPACE/build.log"

# On a failed test phase, capture a backtrace for whatever crashed — at ANY phase: startup (e.g.
# SIGILL from a library auto-dispatching CPU features beyond the -march baseline), the test body, or
# *teardown* (a static-destruction / shutdown race that traps after the test already reported PASSED).
# A bare `--gtest_list_tests` only exercises startup, so it cannot see a run/teardown crash; here we
# (1) post-mortem any natural-run core — the only reliable catch for a flaky race — then (2) re-run the
# ctest-recorded failed tests under gdb, then (3) fall back to the startup probe when ctest recorded
# no per-test failure.
diagnose_crash() {
  set +e
  echo "::group::Runner CPU features"
  lscpu | head -25
  grep -m1 -oE '(sse4_2|avx2?|avx512[a-z]*|bmi[12]?|fma|adx|popcnt)( |$)' /proc/cpuinfo | sort -u
  echo "::endgroup::"
  # Skip the sudo install when gdb is already present — on a self-hosted runner (no passwordless
  # sudo) an unguarded sudo here would block this diagnostic on an interactive password prompt.
  command -v gdb >/dev/null 2>&1 || sudo apt-get install -y -qq gdb >/dev/null

  # The test binaries this module built.
  local bins=()
  local b
  for b in "build/$MODULE"/*tests "build/$MODULE"/*_tests; do [ -x "$b" ] && bins+=("$b"); done
  [ "${#bins[@]}" -gt 0 ] || { echo "no test binary under build/$MODULE"; return 0; }

  # Run a binary under gdb, stopping + dumping a full backtrace on any fatal signal.
  # $1=bin  $2=run args (e.g. "--gtest_filter=Suite.Case" or "--gtest_list_tests").
  run_under_gdb() {
    # shellcheck disable=SC2016  # $pc / gdb -ex expressions are gdb vars — must NOT be shell-expanded
    gdb --batch \
        -ex 'set pagination off' \
        -ex 'handle SIGILL SIGSEGV SIGABRT SIGBUS stop print pass' \
        -ex "run $2" \
        -ex 'echo \n== thread apply all bt full ==\n' \
        -ex 'thread apply all bt full' \
        -ex 'info registers' \
        -ex 'x/4i $pc' \
        "$1" 2>&1 | tail -200
  }

  # (1) Post-mortem any core the natural ctest run dropped — works regardless of crash phase and does
  #     not perturb timing, so it is the reliable catch for a flaky teardown race.
  shopt -s nullglob
  local core bin
  for core in core core.* "build/$MODULE"/core "build/$MODULE"/core.*; do
    [ -f "$core" ] || continue
    for bin in "${bins[@]}"; do
      echo "::group::gdb core post-mortem: $(basename "$bin") $core"
      gdb --batch -ex 'set pagination off' -ex 'thread apply all bt full' \
          -ex 'info registers' "$bin" "$core" 2>&1 | tail -200
      echo "::endgroup::"
    done
  done
  if command -v coredumpctl >/dev/null 2>&1; then  # systemd-coredump runners keep cores in the journal
    echo "::group::coredumpctl (last 5)"
    coredumpctl list --no-pager 2>/dev/null | tail -6
    # Extract the latest core per binary to a file and gdb it — portable across systemd versions
    # (the `coredumpctl gdb` debugger-args flag is not).
    for bin in "${bins[@]}"; do
      local base extracted
      base="$(basename "$bin")"
      extracted="build/$MODULE/$base.core"
      if coredumpctl dump "$base" --output="$extracted" >/dev/null 2>&1 && [ -s "$extracted" ]; then
        gdb --batch -ex 'set pagination off' -ex 'thread apply all bt full' \
            -ex 'info registers' "$bin" "$extracted" 2>&1 | tail -200
      fi
    done
    echo "::endgroup::"
  fi

  # (2) Re-run the tests ctest marked failed, under gdb. The binary executes the real test body +
  #     teardown, so this catches run/teardown crashes the startup probe never could. A teardown race
  #     is flaky — loop a few times. (gdb can mask the race; (1) is the backstop.)
  local failed_log="build/$MODULE/Testing/Temporary/LastTestsFailed.log"
  if [ -f "$failed_log" ]; then
    local line name attempt out
    while IFS= read -r line; do
      name="${line#*:}"   # strip the leading "<index>:"
      [ -n "$name" ] || continue
      for bin in "${bins[@]}"; do
        echo "::group::gdb x8 on $(basename "$bin") --gtest_filter=$name"
        for attempt in $(seq 1 8); do
          echo "--- attempt $attempt ---"
          out="$(run_under_gdb "$bin" "--gtest_filter=$name")"
          echo "$out"
          if echo "$out" | grep -qE 'received signal|SIGSEGV|SIGABRT|SIGBUS|SIGILL'; then
            echo "captured a crashing backtrace on attempt $attempt"; break
          fi
        done
        echo "::endgroup::"
      done
    done < "$failed_log"
  else
    # (3) No per-test failure recorded → a startup crash before any test ran. Probe with the test-list
    #     load (the original fast SIGILL catch).
    for bin in "${bins[@]}"; do
      echo "::group::gdb startup probe (--gtest_list_tests) on $(basename "$bin")"
      run_under_gdb "$bin" "--gtest_list_tests"
      echo "::endgroup::"
    done
  fi
}

if [ "$TEST" = "true" ]; then
  # Build + test in a pipe to tee (pipefail propagates a build/test failure past tee). ulimit lets a
  # crashing test drop a core so diagnose_crash can post-mortem it — the only reliable capture for a
  # *flaky* crash (attaching gdb serialises signals and routinely masks the race; a natural-run core
  # does not). core_pattern is runner-owned (no passwordless sudo); the diagnostic handles both cwd
  # cores and systemd-coredump.
  if {
        ulimit -c unlimited 2>/dev/null || true
        conan install "$MODULE" \
          --output-folder="build/$MODULE" \
          --build=missing \
          --profile:host="$PROFILE" \
          --profile:build="$PROFILE" \
          -s build_type=Release
        # shellcheck disable=SC2086  # CMAKE_ARGS is intentionally word-split into separate -D flags
        cmake --preset conan-release -S "$MODULE" -B "build/$MODULE" $CMAKE_ARGS
        cmake --build "build/$MODULE"
        # Robust test detection: some modules emit Testing/ but not a top-level CTestTestfile.cmake.
        if [[ -d "build/$MODULE/Testing" || -f "build/$MODULE/CTestTestfile.cmake" ]]; then
          ctest --test-dir "build/$MODULE" --output-on-failure
        fi
      } 2>&1 | tee -a "$LOG"; then
    :
  else
    diagnose_crash || true
    exit 1
  fi
fi

if [ "$CREATE" = "true" ]; then
  {
    conan create "$MODULE" \
      --build=missing \
      --build-test=missing \
      --profile:host="$PROFILE" \
      --profile:build="$PROFILE"
  } 2>&1 | tee -a "$LOG"
fi
