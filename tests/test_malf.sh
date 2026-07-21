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
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
