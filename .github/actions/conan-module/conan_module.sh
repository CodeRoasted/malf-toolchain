#!/usr/bin/env bash
# Test / create ONE Conan module (package) with Conan + CMake directly. This is the single source of
# truth for the per-module build logic: both the `conan-module` composite action and the
# `coderoast-ci` composite's module loop invoke it (the loop is why this is a script — a composite
# cannot loop a `uses:` step over a variable-length module list).
#
# Usage: conan_module.sh <module> <test:true|false> <create:true|false> [profile] [cmake-args]
#   module      path relative to the repo root (e.g. `core`, `.`, `infra/redis`, `sift`)
#   test        'true' => conan install + cmake build + ctest (Release); anything else skips the phase
#   create      'true' => conan create <module>; anything else skips it
#   profile     conan profile name in $CONAN_HOME/profiles (default linux-gcc16-release)
#   cmake-args  extra flags for the test-build configure, e.g. `-DPKG_BUILD_TESTS=ON -DPKG_BUILD_BENCH=ON`
#               (word-split into separate args; needed by packages that gate their unit tests behind a flag)
#
# Assumes the profile is already installed in $CONAN_HOME/profiles (by setup-build-env); it does NOT
# vendor or copy a profile itself. Build + test + create output is mirrored to $GITHUB_WORKSPACE/
# build.log so the Sift dogfood can diff it; the crash diagnostic prints to the console only.
#
# It DOES own the two toolchain-file settings — the conan lockfile and MALF_TOOLCHAIN_DIR
# — derived below from this script's own path, so every caller gets them whether it reaches here
# through `coderoast-ci` or by `uses:`-ing the `conan-module` action directly.

set -euo pipefail

MODULE="${1:?module arg required}"
TEST="${2:?test arg required}"
CREATE="${3:?create arg required}"
PROFILE_NAME="${4:-linux-gcc16-release}"
PROFILE="$CONAN_HOME/profiles/$PROFILE_NAME"
CMAKE_ARGS="${5:-}"   # extra -D flags for the test-build configure; intentionally word-split below
LOG="$GITHUB_WORKSPACE/build.log"

# The malf-toolchain root, derived from THIS script's own location:
#   <toolchain>/.github/actions/conan-module/conan_module.sh  ->  <toolchain>
# Every setting below that names a file SHIPPED BY THE TOOLCHAIN (the lockfile, the intent codegen
# tool) resolves from here, and deriving it here is what makes those settings unbypassable.
# The two callers are composite ACTIONS, and a composite step's `env:` reaches only that composite's
# own steps — never a workflow that `uses:` the *other* action directly. `insight-eidos`'s ci.yml
# does exactly that for the `insight-e2e` module, so while `coderoast-ci` was the only place these
# were set, that module resolved with NO lockfile at all — weaker than partial, and silent
# ("set once in the composite, which every repo reaches" was false for that path).
# This script is the one thing every path goes through, so it is the one place they belong.
# Callers still override by exporting the variable; a per-caller *copy* of the default is two
# places encoding one fact, one of which rots — and must not be added.
TOOLCHAIN_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"

# Build-time codegen reachability. logcraft/core's CMake FATALs unless it can find
# intent_library_codegen.py, and its fallback — the sibling <workspace>/malf — is unreachable under
# `conan create` / `--build=missing`, which build from sources exported into the conan cache. This
# path does not go through malf, so malf's own export of MALF_TOOLCHAIN_DIR never reaches it.
export MALF_TOOLCHAIN_DIR="${MALF_TOOLCHAIN_DIR:-$TOOLCHAIN_ROOT}"

# Lockfile args, shared by the install and create phases below so the two can never
# disagree about which graph they resolved. Third-party recipes require by RANGE
# (clickhouse-cpp -> lz4/[>=1.9.4 <2], libcurl -> openssl/[>=3 <4]) and conan resolves a range
# against whatever is reachable at that moment — so without this, two legs of one release can
# legitimately resolve different dependency versions, and the 5-leg golden compare reds with no
# diagnosable cause. NOTE this path does NOT go through malf either — raw `conan install` /
# `conan create` — so MALF_LOCKFILE_STRICT is inert here; these two variables are the CI seam.
#
# MALF_LOCKFILE overrides the derived path. Setting it to the EMPTY string is the one way to opt out
# ("resolve live"), and it has to be deliberate: a NON-EMPTY path that does not exist is a hard
# error, not a silent downgrade to live resolution. The old form (`-n $VAR && -f $VAR`) turned a
# typo'd or missing lockfile into a free resolve that reads as green — the exact silent-green class
# the lockfile exists to stop.
MALF_LOCKFILE="${MALF_LOCKFILE-$TOOLCHAIN_ROOT/conan.lock}"
LOCKFILE_ARGS=()
if [ -n "$MALF_LOCKFILE" ]; then
  if [ ! -f "$MALF_LOCKFILE" ]; then
    echo "::error::conan_module: lockfile '$MALF_LOCKFILE' does not exist. The default is derived from"
    echo "::error::this script's location (toolchain root '$TOOLCHAIN_ROOT'), so a missing file means a"
    echo "::error::broken malf-toolchain checkout or a bad MALF_LOCKFILE override. Set MALF_LOCKFILE='' to"
    echo "::error::resolve live on purpose."
    exit 1
  fi
  LOCKFILE_ARGS+=("--lockfile=$MALF_LOCKFILE")
  # GATE, and this is the POLICY, not a backstop: '1' pins (the desk posture), anything else
  # drops --lockfile-partial so that "a dependency entered UNLOCKED" is a hard red.
  # Flipped '1' (pin) -> '0' (gate) at the 1.8.4 head, after v1.8.3 was the first observed-green
  # cut. Set here for the same reason the path is: one place, every caller.
  [ "${MALF_LOCKFILE_PARTIAL:-0}" = "1" ] && LOCKFILE_ARGS+=("--lockfile-partial")
  echo "conan_module: using lockfile $MALF_LOCKFILE ${LOCKFILE_ARGS[*]}"
else
  echo "conan_module: MALF_LOCKFILE set EMPTY by the caller — resolving live, unlocked"
fi

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
  # Build + test in a SUBSHELL with `set -e` active, run as a plain pipeline statement (NOT an `if`
  # condition). Putting the group in an `if`/`&&`/`||` condition DISABLES `set -e` inside it, so an
  # early failure (conan install unresolved dep, cmake configure, compile) would NOT abort — execution
  # falls through to the trailing `if … ctest; fi`, which returns 0 when the build dir is absent (the
  # install failed → no Testing/), and the whole group exits 0, silently GREENING a failed build. That
  # exact hole let a missing transitive dep pass `ci` green and only fail at publish. `set -e` in the
  # subshell aborts on the first failing step; PIPESTATUS[0] reads its status past `tee`. ulimit lets a
  # crashing test drop a core for diagnose_crash — the only reliable capture for a *flaky* crash
  # (attaching gdb serialises signals and routinely masks the race; a natural-run core does not).
  # core_pattern is runner-owned (no passwordless sudo); the diagnostic handles cwd + systemd cores.
  set +e
  (
    set -e
    ulimit -c unlimited 2>/dev/null || true
    conan install "$MODULE" \
      --output-folder="build/$MODULE" \
      --build=missing \
      --profile:host="$PROFILE" \
      --profile:build="$PROFILE" \
      "${LOCKFILE_ARGS[@]}" \
      -s build_type=Release
    # shellcheck disable=SC2086  # CMAKE_ARGS is intentionally word-split into separate -D flags
    cmake --preset conan-release -S "$MODULE" -B "build/$MODULE" $CMAKE_ARGS
    cmake --build "build/$MODULE"
    # Robust test detection: some modules emit Testing/ but not a top-level CTestTestfile.cmake.
    if [[ -d "build/$MODULE/Testing" || -f "build/$MODULE/CTestTestfile.cmake" ]]; then
      # `-LE corpus` — THE SAME EXCLUSION `malf test` APPLIES BY DEFAULT, and this is the caller
      # that bypasses malf. The ctest label `corpus` marks gates needing a PRIVATE, machine-local,
      # gitignored third-party bank mounted through an environment variable, and since
      # insight-eidos 8c82df2 (2026-09-04) a missing mount is a HARD FAILURE rather than a gtest
      # skip — deliberately, because a skip exits 0 and greened a gate that reached nothing. That
      # commit taught malf to exclude the label and did not follow to the ctest callers that go
      # around it. Measured 2026-09-05: insight-eidos drives `sift true true` through this script
      # (its ci.yml modules list), sift carries 21 corpus-labelled tests, and release.yaml has
      # `uses: ./.github/workflows/ci.yml` with golden/sbom/publish all `needs: ci` — so the next
      # tag reds here and BLOCKS THE PUBLISH. Mounting the banks would be the wrong remedy: they
      # are one developer's disk, and the corpus population has its own workflow and its own
      # runner. Repos with no corpus label are unaffected — the flag removes an empty set.
      #
      # VACUITY GUARD, SCOPED TO THE VACUITY THIS FLAG CAN CREATE AND TO NOTHING ELSE. ctest exits
      # 0 on "No tests were found!!!", and an exclusion is exactly what can empty a run that had
      # content — so the flag owes a guard. But this script runs for EVERY module of EVERY repo,
      # and a guard that simply demanded "at least one test" would also red every module that
      # already ran zero, a population no one can enumerate without building all of them. That is
      # a new red shipped into the release path on an unverified premise, which is the shape this
      # whole change exists to remove.
      #
      # So the predicate is the DIFFERENCE, not the floor: fail only when the unfiltered suite has
      # tests and the filtered one has none. A module that ran zero before behaves exactly as it
      # did. The blast radius is therefore provably "modules where EVERY test is corpus-labelled",
      # which is empty today — measured 2026-09-05, the label is set in one repo, in two lines of
      # insight-eidos/sift/CMakeLists.txt, and that suite has 416 tests outside it.
      ctest_all=$(ctest --test-dir "build/$MODULE" -N 2>/dev/null \
                    | grep -cE '^[[:space:]]*Test[[:space:]]+#[0-9]+:' || true)
      ctest_n=$(ctest --test-dir "build/$MODULE" -N -LE corpus 2>/dev/null \
                  | grep -cE '^[[:space:]]*Test[[:space:]]+#[0-9]+:' || true)
      if [ "${ctest_all:-0}" -gt 0 ] && [ "${ctest_n:-0}" -lt 1 ]; then
        echo "conan_module: $MODULE discovers $ctest_all test(s) and ZERO of them survive -LE corpus." >&2
        echo "conan_module: ctest exits 0 on an empty selection, so running it here would be a green" >&2
        echo "conan_module: that measured nothing. Every test in this module is corpus-labelled, so it" >&2
        echo "conan_module: belongs to the corpus job and its runner, not to this one — either move it" >&2
        echo "conan_module: there or give this module a test that does not need a private bank." >&2
        exit 1
      fi
      echo "conan_module: $MODULE — $ctest_n of $ctest_all test(s) run here (the rest are 'corpus'-labelled)"
      ctest --test-dir "build/$MODULE" --output-on-failure -LE corpus
    fi
  ) 2>&1 | tee -a "$LOG"
  build_status=${PIPESTATUS[0]}
  set -e
  if [ "$build_status" -ne 0 ]; then
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
      --profile:build="$PROFILE" \
      "${LOCKFILE_ARGS[@]}"
  } 2>&1 | tee -a "$LOG"
fi
