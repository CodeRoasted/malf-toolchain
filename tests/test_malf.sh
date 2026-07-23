#!/usr/bin/env bash
# test_malf.sh — the smoke/selftest FLOOR for malf.
#
# malf is ~1900 lines of bash that every repo in the workspace builds through, and it had
# no tests of any kind. This is deliberately a floor, not coverage: it pins the failures
# that are catastrophic, silent, or both, and that no compiler will ever catch for us.
#
# WHAT IS PINNED, and why each one is here rather than trusted:
#   1. SYNTAX. A stray `fi` anywhere in a bash script is not found until the line runs.
#      `bash -n` is the cheapest possible guard against bricking every build in the
#      workspace at once, and it costs milliseconds.
#   2. DISPATCH INTEGRITY. The dispatch case maps a verb to a cmd_* function by NAME. Bash
#      resolves that name at CALL time, so a renamed or deleted function leaves a dispatch
#      arm that parses fine, passes `bash -n`, and dies only when a user types that verb.
#      Both directions are checked: no arm points at a missing function, and no cmd_* is
#      unreachable (an orphan is either dead code or a verb someone forgot to wire).
#   3. THE PROFILE/BUILD KEYS. These decide which CONAN_HOME a build reads and which
#      build-<key>/ tree it writes. They carry a deliberate ASYMMETRY — the default profile
#      is the UNKEYED base cache ('') but still gets a NAMED build tree — and getting it
#      wrong does not fail loudly: it silently reads a different cache, which is exactly the
#      stale-binary/ABI-skew class the workspace has already been bitten by. A truth table
#      is the only way this stays honest.
#
# Run: bash malf/tests/test_malf.sh   (no deps, no network, no build)

set -uo pipefail

MALF_TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MALF_ROOT="$(cd "$MALF_TEST_DIR/.." && pwd)"
MALF_BIN="$MALF_ROOT/malf"

pass_count=0
fail_count=0

# Verbose on failure (CLAUDE.md § Observability): print actual-vs-expected, so a red run is
# diagnosable from the CI log alone without re-running anything locally.
check() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass_count=$((pass_count + 1))
        printf '  ok   %s\n' "$label"
    else
        fail_count=$((fail_count + 1))
        printf '  FAIL %s\n       expected: %q\n       actual:   %q\n' \
               "$label" "$expected" "$actual"
    fi
}

echo "[1] syntax — every shell script parses"

for script in "$MALF_BIN" "$MALF_ROOT"/*.sh "$MALF_TEST_DIR"/*.sh; do
    [[ -f "$script" ]] || continue
    if bash -n "$script" 2>/dev/null; then
        check "bash -n $(basename "$script")" "ok" "ok"
    else
        check "bash -n $(basename "$script")" "ok" "$(bash -n "$script" 2>&1 | head -3)"
    fi
done

echo "[2] dispatch — every verb resolves to a function that exists"

# Source the definitions WITHOUT dispatching (the MALF_SOURCE_ONLY seam), so the functions
# can be interrogated directly rather than by shelling out per verb.
# shellcheck disable=SC1090
MALF_SOURCE_ONLY=1 source "$MALF_BIN"
# malf sets `set -euo pipefail`, and sourcing leaks that into THIS shell. Under -e the first
# deliberately-failing command (the unknown-verb check below) kills the runner mid-suite —
# which looked like a clean pass, because the summary line never printed and the sections
# after it silently never ran. Re-assert the runner's own options; a test harness must be
# able to run commands that fail on purpose.
set +e
set -uo pipefail

# The dispatch arms, read from the source rather than restated here: a verb added to malf
# is covered the moment it is written, with no edit to this file.
dispatch_block="$(sed -n '/─── Dispatch ───/,$p' "$MALF_BIN")"
mapfile -t dispatched < <(grep -oE '\bcmd_[a-z_]+' <<< "$dispatch_block" | sort -u)

check "dispatch arms were found at all (guards a silent zero)" \
      "many" "$( ((${#dispatched[@]} >= 10)) && echo many || echo "only ${#dispatched[@]}")"

for fn in "${dispatched[@]}"; do
    if declare -F "$fn" >/dev/null 2>&1; then
        check "dispatch -> $fn is defined" "defined" "defined"
    else
        check "dispatch -> $fn is defined" "defined" "MISSING"
    fi
done

# The other direction: a cmd_* nobody dispatches is dead code or an unwired verb.
mapfile -t defined < <(declare -F | awk '{print $3}' | grep -E '^cmd_[a-z_]+$' | sort -u)
for fn in "${defined[@]}"; do
    if grep -qF "$fn" <<< "$dispatch_block"; then
        check "$fn is reachable from dispatch" "reachable" "reachable"
    else
        check "$fn is reachable from dispatch" "reachable" "ORPHAN (defined, never dispatched)"
    fi
done

echo "[3] profile key — which CONAN_HOME a build reads"

# The default profile is the UNKEYED base cache. This empty string is load-bearing, not an
# oversight: it is what makes the dev default share the base $CONAN_HOME.
check "default profile -> unkeyed base cache" \
      "" "$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_profile_key)"
check "empty profile -> unkeyed base cache" \
      "" "$(MALF_PROFILE_NAME="" _malf_profile_key)"
check "linux-gcc15-release -> gcc15-release (the linux- prefix is stripped)" \
      "gcc15-release" "$(MALF_PROFILE_NAME=linux-gcc15-release _malf_profile_key)"
check "linux-clang21-asan -> clang21-asan" \
      "clang21-asan" "$(MALF_PROFILE_NAME=linux-clang21-asan _malf_profile_key)"
check "a non-linux profile passes through verbatim" \
      "windows-msvc-release" "$(MALF_PROFILE_NAME=windows-msvc-release _malf_profile_key)"

echo "[4] build key — which build-<key>/ tree a build writes"

# ALWAYS named, default included. The old asymmetry (default squatting on a bare build/)
# is gone, and this is what keeps a tree self-documenting about its toolchain.
check "default profile still gets a NAMED build tree" \
      "clang21-libcxx-release" "$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_build_key)"
check "linux-gcc15-release -> gcc15-release" \
      "gcc15-release" "$(MALF_PROFILE_NAME=linux-gcc15-release _malf_build_key)"
check "a non-linux profile passes through verbatim" \
      "windows-msvc-release" "$(MALF_PROFILE_NAME=windows-msvc-release _malf_build_key)"

# The asymmetry itself, stated as a property rather than as two separate numbers: for the
# DEFAULT profile the cache key is empty while the build key is not. If someone ever
# "tidies" these two functions into one, this is the test that objects.
check "default profile: cache key is empty but build key is NOT (the deliberate asymmetry)" \
      "empty-cache/named-build" \
      "$(k="$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_profile_key)"
         b="$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_build_key)"
         [[ -z "$k" && -n "$b" ]] && echo "empty-cache/named-build" || echo "k='$k' b='$b'")"

echo "[5] every profile on disk resolves to a non-empty build tree"

# A profile that produced an empty build key would write to a bare `build-`, colliding with
# every other such profile. Driven off the profiles/ directory, so a new profile is covered
# the moment it is added.
for profile_path in "$MALF_ROOT"/profiles/*; do
    [[ -f "$profile_path" ]] || continue
    profile_name="$(basename "$profile_path")"
    key="$(MALF_PROFILE_NAME="$profile_name" _malf_build_key)"
    check "profile '$profile_name' -> non-empty build key" \
          "non-empty" "$([[ -n "$key" ]] && echo non-empty || echo EMPTY)"
done

echo "[6] CLI contract — help succeeds, an unknown verb fails LOUDLY"

"$MALF_BIN" help >/dev/null 2>&1
check "malf help exits 0" "0" "$?"

"$MALF_BIN" definitely-not-a-real-verb >/dev/null 2>&1
check "an unknown verb exits non-zero (never a silent no-op)" "1" "$?"

unknown_output="$("$MALF_BIN" definitely-not-a-real-verb 2>&1)"
check "an unknown verb names itself in the error" \
      "named" "$(grep -q "definitely-not-a-real-verb" <<< "$unknown_output" && echo named || echo "unnamed: $unknown_output")"

echo "[7] the python helpers at least parse"

# ast.parse rather than py_compile: py_compile writes a __pycache__/ next to the source, so
# running the tests would dirty the working tree. A test that litters is a test people stop
# running locally.
for py in "$MALF_ROOT"/*.py; do
    [[ -f "$py" ]] || continue
    parse_error="$(python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$py" 2>&1)"
    if [[ -z "$parse_error" ]]; then
        check "parses: $(basename "$py")" "ok" "ok"
    else
        check "parses: $(basename "$py")" "ok" "$(tail -2 <<< "$parse_error")"
    fi
done

echo

echo "[7b] intent_library_codegen carries its own fence proof (--selftest)"

# The codegen tool is the public half of the LogCraft Intent-library soundness fence
# (teeth 1-4 + the canonicalize-then-hash determinism MUSTs). Unlike the parse-only
# smoke above, its selftest FALSIFIES every fence predicate on synthetic fixtures —
# so a fence regression fails here, in the tool's own repo, before any consumer build.
selftest_output="$(python3 "$MALF_ROOT/intent_library_codegen.py" --selftest 2>&1)"
selftest_status=$?
check "intent_library_codegen --selftest" \
      "ok" "$([[ $selftest_status -eq 0 ]] && echo ok || echo "failed: $(tail -3 <<< "$selftest_output")")"

echo

echo "[7c] sbom_gen carries its own derivation proof (--selftest)"

# sbom_gen projects the resolved conan graph into the SBOM that sbom-cve.yml scans, so its
# accuracy is the CEILING on our CVE detection — a component it drops is a component no
# scanner ever looks at. The selftest is offline (no conan, no network) and targets the
# failure modes that produce a WRONG SBOM rather than a crash: the host/test/build filter,
# node dedupe (the first cut emitted glaze 4x, once per consumer), the CPE vendor+product
# mapping (a wrong vendor string silently matches nothing in NVD), and the refusal to invent
# a CPE for an unmapped package. It also pins that dedupe is by name/VERSION, so a genuine
# two-version split still surfaces as two rows instead of collapsing to one false answer.
sbom_selftest_output="$(python3 "$MALF_ROOT/sbom_gen.py" --selftest 2>&1)"
sbom_selftest_status=$?
check "sbom_gen --selftest" \
      "ok" "$([[ $sbom_selftest_status -eq 0 ]] && echo ok || echo "failed: $(tail -3 <<< "$sbom_selftest_output")")"

echo

echo "[7d] _malf_with_build_lock serializes concurrent runs on ONE build tree"

# Two malf runs started from different repo roots resolve the same conan editables and so run
# ninja in the SAME build dir, writing the same module BMIs. ninja takes no lock; without one in
# malf the loser reads a half-written BMI, which surfaces as a clang ICE in
# ASTReader::FindExternalVisibleDeclsByName or "malformed or corrupted precompiled file"
# (bugs.md 2026-07-22). The lock is therefore load-bearing, and these are its four properties.
lock_tmp="$(mktemp -d)"
trap 'rm -rf "$lock_tmp"' EXIT
# Extract the function under test from malf itself, so this tests the SHIPPED code, not a copy.
lock_fn="$(sed -n '/^_malf_with_build_lock() {/,/^}/p' "$MALF_BIN")"
cat > "$lock_tmp/probe.sh" <<PROBE
#!/usr/bin/env bash
set -euo pipefail
$lock_fn
critical() {
    # Deliberately NON-atomic read-modify-write, so an interleaving is detectable rather than
    # merely improbable: without the lock the updates collide and the counter loses increments.
    local v; v="\$(cat "\$1/counter")"
    sleep 0.2
    echo \$((v + 1)) > "\$1/counter"
}
_malf_with_build_lock "\$1" critical "\$1"
PROBE
chmod +x "$lock_tmp/probe.sh"

# (a) mutual exclusion: 6 concurrent runs on one tree must produce exactly 6 increments.
mkdir -p "$lock_tmp/tree"; echo 0 > "$lock_tmp/tree/counter"
for _ in 1 2 3 4 5 6; do "$lock_tmp/probe.sh" "$lock_tmp/tree" 2>/dev/null & done; wait
check "build lock — 6 concurrent runs on one tree do not interleave" \
      "6" "$(cat "$lock_tmp/tree/counter")"

# (b) the ANTI-VACUITY leg: the same probe WITHOUT the lock must lose updates. If this ever
# reports 6 the probe has stopped being able to detect a race, and (a) proves nothing.
echo 0 > "$lock_tmp/tree/counter"
for _ in 1 2 3 4 5 6; do MALF_BUILD_LOCK=0 "$lock_tmp/probe.sh" "$lock_tmp/tree" 2>/dev/null & done; wait
check "build lock — opt-out DOES race (proves the probe can fail)" \
      "raced" "$([[ "$(cat "$lock_tmp/tree/counter")" -lt 6 ]] && echo raced || echo "no-race: $(cat "$lock_tmp/tree/counter")")"

# (c) stderr must SURVIVE the lock. `exec {fd}>file 2>/dev/null` would apply the redirection to
# the SHELL and silence every later compiler diagnostic — a silent-failure regression that no
# other check here would catch, because the build would still succeed and still look clean.
cat > "$lock_tmp/err.sh" <<PROBE
#!/usr/bin/env bash
set -euo pipefail
$lock_fn
emit() { echo "DIAGNOSTIC" >&2; }
_malf_with_build_lock "\$1" emit
echo "AFTER" >&2
PROBE
chmod +x "$lock_tmp/err.sh"
mkdir -p "$lock_tmp/errtree"
check "build lock — stderr survives (no shell-wide 2>/dev/null)" \
      "DIAGNOSTIC AFTER" \
      "$("$lock_tmp/err.sh" "$lock_tmp/errtree" 2>&1 >/dev/null | tr '\n' ' ' | sed 's/ $//')"

# (d) the exit code of the guarded command must propagate, or a failed build reads as success.
cat > "$lock_tmp/rc.sh" <<PROBE
#!/usr/bin/env bash
set -euo pipefail
$lock_fn
boom() { return 7; }
rc=0; _malf_with_build_lock "\$1" boom || rc=\$?
echo "\$rc"
PROBE
chmod +x "$lock_tmp/rc.sh"
mkdir -p "$lock_tmp/rctree"
check "build lock — guarded command's exit code propagates" \
      "7" "$("$lock_tmp/rc.sh" "$lock_tmp/rctree" 2>/dev/null)"

echo

echo "[7e] _malf_prune_args splits exclude dirs WITHOUT globbing them"

# MALF_SOURCE_EXCLUDE_DIRS carries `build-*`, which must reach `find -name` as a literal glob.
# If it is expanded unquoted it globs against the CWD, resolving to only the build dirs at the
# CWD root and MISSING profile-variant build dirs in sub-packages — find then descends into them
# and hands clang-tidy/clang-format CMake compiler-probe TUs (spurious findings; ASTReader crash
# on a module TU). This builds a tree where the buggy glob would miss `pkg/build-clang21-asan`
# (the CWD root carries a DIFFERENT build dir, so `build-*` globs to that and not to the one in
# the sub-package) and asserts the prune still excludes it.
prune_tmp="$(mktemp -d)"   # cleaned inline below (a second `trap ... EXIT` would REPLACE [7d]'s)
mkdir -p "$prune_tmp/pkg/build-clang21-asan/CMakeFiles" "$prune_tmp/pkg/src" "$prune_tmp/build-gcc15-release"
: > "$prune_tmp/pkg/build-clang21-asan/CMakeFiles/probe.cpp"   # must be pruned
: > "$prune_tmp/pkg/src/real.cpp"                              # must be kept
prune_fn="$(sed -n '/^_malf_prune_args() {/,/^}/p' "$MALF_BIN")"
prune_out="$(cd "$prune_tmp" && bash -c "
    set -uo pipefail
    MALF_SOURCE_EXCLUDE_DIRS='build build-* .git'
    $prune_fn
    mapfile -d '' -t p < <(_malf_prune_args)
    find \"\$PWD\" \\( -type d \\( \"\${p[@]}\" \\) -prune \\) -o \\( -type f -name '*.cpp' -print \\)
")"
check "prune excludes sub-package build-* dir (glob-safe)" \
      "kept+pruned" \
      "$([[ "$prune_out" == *real.cpp* && "$prune_out" != *probe.cpp* ]] && echo 'kept+pruned' \
         || echo "LEAK: $(printf '%s' "$prune_out" | tr '\n' ' ')")"
rm -rf "$prune_tmp"

echo

echo "[7f] _malf_db_lintable_files drops entries whose build dir is gone"

# clang-tidy chdir's into an entry's `directory` before parsing and aborts with
# `LLVM ERROR: Cannot chdir` when it is a sub-package build dir that was never built in the
# active profile. lint must exclude such entries up front. This builds a DB with one entry whose
# directory EXISTS and one whose directory is MISSING and asserts only the live one is emitted.
db_tmp="$(mktemp -d)"
mkdir -p "$db_tmp/live"                       # exists
cat > "$db_tmp/cc.json" <<JSON
[{"directory":"$db_tmp/live","file":"$db_tmp/a.cpp","command":"cc -c a.cpp"},
 {"directory":"$db_tmp/gone","file":"$db_tmp/b.cpp","command":"cc -c b.cpp"}]
JSON
db_fn="$(sed -n '/^_malf_db_lintable_files() {/,/^}/p' "$MALF_BIN")"
db_out="$(bash -c "$db_fn; _malf_db_lintable_files '$db_tmp/cc.json'" | tr '\n' ' ')"
check "db-lintable keeps live-dir entry, drops missing-dir entry (no chdir crash)" \
      "a-only" \
      "$([[ "$db_out" == *a.cpp* && "$db_out" != *b.cpp* ]] && echo a-only || echo "GOT: $db_out")"
rm -rf "$db_tmp"

echo
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
