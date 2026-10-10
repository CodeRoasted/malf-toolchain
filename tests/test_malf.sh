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

echo "[2b] every verb refuses an unknown flag and answers --help — and runs nothing either way"

# THE HAZARD THAT DID NOT FIRE, measured 2026-09-02 (N113): `./malf/malf bench --help` — no such
# flag — started a WORKSPACE-WIDE bench sweep beside a live `malf lint`, outside the build-slot
# protocol, and touched no build tree only because a closed pipe killed it first. bench forwarded
# the unknown flag to the benchmark binaries and swept the empty target; build/test/bin-dir made
# it the TARGET; commands/profiles dropped every argument on the floor. The fixture is a package
# the resolver WOULD find (a conanfile) with nothing it could build (no CMakeLists), under a
# scratch workspace root and conan home so a regressed verb reaches no real cache. The observables:
# the exit status, the refusal text, the absence of any `=== malf` banner, and no build tree
# appearing. Derived over the dispatch block, so a verb added without the guard reds here the day
# it is written; `--` is exempt from the --help scan because what follows it belongs to the
# benchmark binary, and that is checked too.

vb_tmp="$(mktemp -d)"; mkdir -p "$vb_tmp/pkg" "$vb_tmp/home"
cat > "$vb_tmp/pkg/conanfile.py" <<'PYR'
from conan import ConanFile
class Probe(ConanFile):
    name = "vb_probe"
    version = "0.0.1"
PYR
vb_run() {   # <verb> <args...> — the verb, from the fixture package, sandboxed and bounded
    local verb="$1"; shift
    (cd "$vb_tmp/pkg" && MALF_WORKSPACE_ROOT="$vb_tmp" CONAN_HOME="$vb_tmp/home" MALF_SKIP_INVENTORY=1 \
        timeout 60 bash "$MALF_BIN" "$verb" "$@" 2>&1)
}
vb_built() { compgen -G "$vb_tmp/pkg/build-*" >/dev/null && echo built || echo none; }

# The verbs, read from the dispatch block — the same derivation [2] uses; help's spellings excluded.
mapfile -t vb_verbs < <(sed -n '/─── Dispatch ───/,$p' "$MALF_BIN" | grep -oE '^    [a-z|-]+\)' \
    | tr -d ' )' | tr '|' '\n' | grep -vE '^(help|-h|--help)$' | sort -u)
check "verb list derived from the dispatch block (guards a silent zero)" \
      "many" "$( ((${#vb_verbs[@]} >= 12)) && echo many || echo "only ${#vb_verbs[@]}")"

for vb in "${vb_verbs[@]}"; do
    vb_out="$(vb_run "$vb" --no-such-flag)"; vb_rc=$?
    check "malf $vb --no-such-flag refuses (rc≠0), prints no run banner, builds nothing" \
          "refused/none" \
          "$( [[ $vb_rc -ne 0 && "$vb_out" != *"=== malf"* ]] && echo "refused/$(vb_built)" || echo "rc=$vb_rc GOT: $(head -c 240 <<< "$vb_out")")"
    vb_help="$(vb_run "$vb" --help)"; vb_help_rc=$?
    check "malf $vb --help prints usage, exits 0, prints no run banner" \
          "usage" \
          "$( [[ $vb_help_rc -eq 0 && "$vb_help" == "usage:"* && "$vb_help" != *"=== malf"* ]] && echo usage || echo "rc=$vb_help_rc GOT: $(head -c 240 <<< "$vb_help")")"
done

# The four verbs the hazard reached name the option they refused — a reader re-derives nothing.
# Captured, then compared: under this suite's pipefail a `| grep -q` that exits on its first match
# closes the pipe while malf is still printing the usage block, and the writer's SIGPIPE reads as
# "unnamed" — the very closed-pipe shape that stopped the N113 sweep.
for vb in bench build test lint; do
    vb_named="$(vb_run "$vb" --no-such-flag)"
    check "malf $vb names the unknown option it refused" \
          "named" "$([[ "$vb_named" == *"malf $vb: unknown option '--no-such-flag'"* ]] && echo named || echo unnamed)"
done

# bench's `--` still hands what follows to the benchmark binary: `--help` AFTER it is not a usage
# request, so the verb runs (and reaches its own no-target-built refusal in this fixture).
vb_pass="$(vb_run bench --no-build -- --help)"
check "bench: --help after -- is the benchmark binary's, not malf's (the verb runs)" \
      "ran" "$([[ "$vb_pass" == *"=== malf bench"* || "$vb_pass" == *"no benchmark"* ]] && echo ran || echo "GOT: $(head -c 240 <<< "$vb_pass")")"
rm -rf "$vb_tmp"
echo

echo "[3] profile key — which CONAN_HOME a build reads"

# The default profile is the UNKEYED base cache. This empty string is load-bearing, not an
# oversight: it is what makes the dev default share the base $CONAN_HOME.
check "default profile -> unkeyed base cache" \
      "" "$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_profile_key)"
check "empty profile -> unkeyed base cache" \
      "" "$(MALF_PROFILE_NAME="" _malf_profile_key)"
check "linux-gcc16-release -> gcc16-release (the linux- prefix is stripped)" \
      "gcc16-release" "$(MALF_PROFILE_NAME=linux-gcc16-release _malf_profile_key)"
check "linux-clang21-asan -> clang21-asan" \
      "clang21-asan" "$(MALF_PROFILE_NAME=linux-clang21-asan _malf_profile_key)"
check "a non-linux profile passes through verbatim" \
      "windows-msvc-release" "$(MALF_PROFILE_NAME=windows-msvc-release _malf_profile_key)"

echo "[3b] a file another malf may be reading is replaced by ONE rename, never rewritten in place"
# Every invocation, whatever its verb, copies its profile into the shared conan cache and syncs
# global.conf there. Measured 2026-10-01: eight concurrent `malf format --check` runs, one per
# format-armed repository, and one read the profile while another's `cp` had truncated it, found no
# build_type and exited 1. The property is held at the INODE: a reader that opened the cached file
# before the install still reads the OLD bytes whole, and the path names the new ones. An in-place
# `cp` fails it — the reader's inode is the one being truncated and rewritten.
ri_tmp="$(mktemp -d)"
mkdir -p "$ri_tmp/profiles"
printf 'stale profile, no build type\n' > "$ri_tmp/profiles/$MALF_DEFAULT_PROFILE"
exec 9< "$ri_tmp/profiles/$MALF_DEFAULT_PROFILE"
ri_path="$(CONAN_HOME="$ri_tmp" MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_profile_path)"
check "the profile lands at the cache path" "$ri_tmp/profiles/$MALF_DEFAULT_PROFILE" "$ri_path"
check "the cached profile is the registry's bytes" "same" \
      "$(cmp -s "$MALF_ROOT/profiles/$MALF_DEFAULT_PROFILE" "$ri_path" && echo same || echo differs)"
check "a reader holding the old file still reads it whole (replaced by rename, not rewritten)" \
      "stale profile, no build type" "$(cat <&9)"
exec 9<&-
printf 'stale conf\n' > "$ri_tmp/global.conf"
exec 9< "$ri_tmp/global.conf"
(CONAN_HOME="$ri_tmp" _malf_sync_conan_conf)
check "global.conf is synced to the in-tree bytes" "same" \
      "$(cmp -s "$MALF_ROOT/global.conf" "$ri_tmp/global.conf" && echo same || echo differs)"
check "a reader holding the old global.conf still reads it whole" "stale conf" "$(cat <&9)"
exec 9<&-
check "no staging file is left behind" "" \
      "$(find "$ri_tmp" -name '*.malf-staged.*' -printf '%P\n')"
rm -rf "$ri_tmp"

echo "[4] build key — which build-<key>/ tree a build writes"

# ALWAYS named, default included. The old asymmetry (default squatting on a bare build/)
# is gone, and this is what keeps a tree self-documenting about its toolchain.
check "default profile still gets a NAMED build tree" \
      "clang21-libcxx-release" "$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_build_key)"
check "linux-gcc16-release -> gcc16-release" \
      "gcc16-release" "$(MALF_PROFILE_NAME=linux-gcc16-release _malf_build_key)"
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

# The MERGED ROOT DB's location, both halves, because the two are in tension and a change
# that satisfies one alone is the defect this pins.
#
# HALF 1 — THE EDITOR CONTRACT. config/.clangd names `CompilationDatabase:
# build-clang21-libcxx-release` STATICALLY, so the default profile's merged root MUST land
# exactly there. If this fails, every editor in the workspace silently stops finding its
# index, and nothing else in this suite would notice.
check "default profile: the merged root IS the path .clangd names statically" \
      "$(_malf_default_build_root /R)" \
      "/R/build-$(MALF_PROFILE_NAME="$MALF_DEFAULT_PROFILE" _malf_build_key)"

# HALF 2 — THE CONTAMINATION FIX. _malf_merge_compile_commands used to write EVERY profile's
# entries to the default leg's root, so `malf build --profile linux-gcc16-release` merged
# g++ commands into a directory named for clang, keyed per source file, last writer wins.
# Measured on insight-canon before the fix: 169 g++ against 3 clang++-21 in
# build-clang21-libcxx-release/compile_commands.json. `malf lint` then ran clang-tidy over
# gcc flags and produced findings that were specific and WRONG. A non-default profile must
# resolve somewhere ELSE — the assertion is inequality, which is what "keyed by the ACTIVE
# profile" means operationally.
check "gcc16 profile: the merged root is NOT the default leg's (no cross-profile clobber)" \
      "differs" \
      "$(d="$(_malf_default_build_root /R)"
         g="/R/build-$(MALF_PROFILE_NAME=linux-gcc16-release _malf_build_key)"
         [[ "$d" != "$g" ]] && echo "differs" || echo "SAME: $d")"

# HALF 3 — THE WIRING, read from the source rather than restated, the same way the dispatch
# arms above are. Halves 1 and 2 pin a PROPERTY of the key functions; neither would notice
# if _malf_merge_compile_commands stopped calling them and went back to the constant
# default root, which is precisely the defect. So assert what that function actually
# resolves its root to. Without this arm the two above are true and vacuous.
check "the merge root is wired to the ACTIVE profile key, not the default build root" \
      "build-key" \
      "$(line="$(sed -n '/^_malf_merge_compile_commands()/,/^}/p' "$MALF_BIN" | grep 'local root_db=')"
         if [[ "$line" == *_malf_build_key* && "$line" != *_malf_default_build_root* ]]; then
             echo "build-key"
         else
             echo "WIRED TO: $line"
         fi)"

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

# The dialect codegen's fence set is DIFFERENT and its most dangerous predicate is the one the
# two tools disagree about: the Intent library SORTS its entries, a dialect's rows are content in
# DECLARED order and must never be sorted. That is why the two tools are separate entry points
# over a shared parser, and why each proves its own fences here rather than sharing a selftest.
dialect_selftest_output="$(python3 "$MALF_ROOT/dialect_package_codegen.py" --selftest 2>&1)"
dialect_selftest_status=$?
check "dialect_package_codegen --selftest" \
      "ok" "$([[ $dialect_selftest_status -eq 0 ]] && echo ok || echo "failed: $(tail -3 <<< "$dialect_selftest_output")")"

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

echo "[7h] comment_contract_lint carries its own grammar proof (--selftest), post-format leg included"

# The comment-grammar phase of `malf format` (ADR-26.D7). Its selftest falsifies every violation
# class on a fixture, proves the clean fixture green, and pins the one shape that justifies the
# phase's placement: a `// pre:` over the column limit followed by a `// post:` is CLEAN before
# clang-format and RED after it, because the reflow swallows the second tag mid-line. That leg
# needs the version-matched clang-format; it is passed when found and the selftest SAYS so when
# it is not, so a skipped leg reads as skipped and never as a pass.
ccc_clang_format=""
ccc_clang_bin="$(command -v clang-21 || command -v clang || true)"
if [[ -n "$ccc_clang_bin" && -x "$(dirname "$(readlink -f "$ccc_clang_bin")")/clang-format" ]]; then
    ccc_clang_format="$(dirname "$(readlink -f "$ccc_clang_bin")")/clang-format"
elif command -v clang-format-21 >/dev/null 2>&1; then
    ccc_clang_format="$(command -v clang-format-21)"
fi
if [[ -n "$ccc_clang_format" ]]; then
    ccc_selftest_output="$(python3 "$MALF_ROOT/comment_contract_lint.py" --selftest --format-via "$ccc_clang_format" 2>&1)"
else
    ccc_selftest_output="$(python3 "$MALF_ROOT/comment_contract_lint.py" --selftest 2>&1)"
    echo "  note: no clang-format-21 found — the post-format leg of the CCC selftest is SKIPPED here"
fi
ccc_selftest_status=$?
check "comment_contract_lint --selftest" \
      "ok" "$([[ $ccc_selftest_status -eq 0 ]] && echo ok || echo "failed: $(tail -5 <<< "$ccc_selftest_output")")"
check "comment_contract_lint --selftest ran the post-format leg (or said it skipped it)" \
      "yes" "$(grep -qE 'RED after formatting|post-format leg' <<< "$ccc_selftest_output" && echo yes || echo no)"

echo

echo "[7i] contract_gen renders the derived contract surface from the gate's own parser (--selftest)"

# ADR-26.O3: the surface is DERIVED and never committed, so the generator's own selftest is its only
# proof — a fixture carrying a contract, a law, a witnessed and an unwitnessed TEST and an orphan
# law, rendered and read back section by section; and an empty population must say so rather than
# render a blank.
cg_selftest_output="$(python3 "$MALF_ROOT/contract_gen.py" --selftest 2>&1)" && cg_selftest_status=0 || cg_selftest_status=$?
check "contract_gen --selftest" \
      "ok" "$([[ $cg_selftest_status -eq 0 ]] && echo ok || echo "failed: $(tail -5 <<< "$cg_selftest_output")")"

echo

echo "[7d] _malf_with_build_lock serializes concurrent runs on ONE build tree"

# Two malf runs started from different repo roots resolve the same conan editables and so run
# ninja in the SAME build dir, writing the same module BMIs. ninja takes no lock; without one in
# malf the loser reads a half-written BMI, which surfaces as a clang ICE in
# ASTReader::FindExternalVisibleDeclsByName or "malformed or corrupted precompiled file"
# (measured 2026-07-22). The lock is therefore load-bearing, and these are its four properties.
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
mkdir -p "$prune_tmp/pkg/build-clang21-asan/CMakeFiles" "$prune_tmp/pkg/src" "$prune_tmp/build-gcc16-release"
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

echo "[7g] lint + format REFUSE the states in which their output would be meaningless"

# All three arms pin the same class: a checker that cannot be right must say so, not produce
# output. Each was a live silent path before, and each is now the thing that makes these two
# verbs safe to wire into CI — which is why they are pinned here rather than trusted.
#
# The probe tree is a bare directory with two source files and no build, no git, no config.
# That is exactly a CI checkout of a repo whose .clang-format is a symlink into a sibling repo
# that was not cloned.
guard_tmp="$(mktemp -d)"
printf 'export module probe;\n' > "$guard_tmp/probe.cppm"
printf 'namespace  probe { int  f( ){return 0;} }\n' > "$guard_tmp/probe.cpp"

# 1. lint with no compile DB. Previously a warning followed by a flag-less clang-tidy run:
#    measured 39 phantom clang-diagnostic-errors on a clean insight-twin clone, i.e. a gate
#    that can never pass. Both modes refuse, because neither can produce a trustworthy verdict.
lint_all_out="$(cd "$guard_tmp" && bash "$MALF_BIN" lint --all-files --console 2>&1)"; lint_all_rc=$?
check "lint --all-files refuses with no compile_commands.json" \
      "rc=1 refused" \
      "rc=$lint_all_rc $([[ "$lint_all_out" == *"no compile_commands.json"* ]] && echo refused || echo "GOT: $lint_all_out")"

lint_def_out="$(cd "$guard_tmp" && bash "$MALF_BIN" lint --console 2>&1)"; lint_def_rc=$?
check "lint (default mode) refuses with no compile_commands.json" \
      "rc=1 refused" \
      "rc=$lint_def_rc $([[ "$lint_def_out" == *"no compile_commands.json"* ]] && echo refused || echo "GOT: $lint_def_out")"

# 2. format with an unresolvable style. `-style=file` answers a missing config by formatting to
#    LLVM style SILENTLY — no warning, no error — so every file in the tree reports as violating
#    a style nobody chose. The refusal is checked against a malf whose bundled config is not
#    reachable, since the real one always is.
fmt_iso="$(mktemp -d)"
cp "$MALF_BIN" "$fmt_iso/malf"          # copied ALONE: no sibling config/ dir, so no fallback
fmt_none_out="$(cd "$guard_tmp" && bash "$fmt_iso/malf" format --check 2>&1)"; fmt_none_rc=$?
check "format refuses rather than fall back to LLVM style" \
      "rc=1 refused" \
      "rc=$fmt_none_rc $([[ "$fmt_none_out" == *"refusing to run"* ]] && echo refused || echo "GOT: $fmt_none_out")"

# 3. format resolves the TOOLCHAIN config when the repo's own does not resolve — the CI case.
#    The probe tree has no .clang-format at all, which is what `[[ -f ]]` also reports for the
#    dangling symlink every C++ repo but insight-twin ships. It must announce the substitution
#    (a silent one would be the same defect wearing a different hat) and must not write the
#    config into the tree it is checking.
fmt_fb_out="$(cd "$guard_tmp" && bash "$MALF_BIN" format --check 2>&1)" || true
check "format falls back to the toolchain config, and says so" \
      "announced" \
      "$([[ "$fmt_fb_out" == *"config/.clang-format"* ]] && echo announced || echo "GOT: $fmt_fb_out")"
check "format does not copy a config into the tree it checks" \
      "clean" \
      "$([[ -e "$guard_tmp/.clang-format" ]] && echo "DIRTIED: .clang-format written into the checkout" || echo clean)"

rm -rf "$guard_tmp" "$fmt_iso"

echo

echo "[7g2] lint's SUBJECT cannot be decided by build order or by a stray file"

# TWO defects measured on insight-eidos 2026-08-30, one disease: the set of files `malf lint`
# actually checks was decided by something other than the source tree, and neither said so.
#
#  * The repo-root compile database is an ACCUMULATION — `malf build <pkg>` merges one package's
#    entries into it, additively, and only `malf commands` rebuilds it whole. Measured with NO
#    source change between two runs: 73 files checked, then 32. Re-derived a second way the same
#    day: the root DB held 34 distinct files while the sub-package databases held 83, 80 of them
#    absent from the root. A 70% coverage hole, exit 0.
#  * `insight-eidos/llm/compile_commands.json` — gcc-produced, gitignored, dated 2026-06-26 — sat
#    beside the recipe and was PREFERRED over the profile-keyed build root, so insight_llm went
#    unlinted for two months.
#
# Both arms below are INVERT-OR-DIE against the old behaviour: under the previous code the first
# run exits 0 and the second reads the stray file. Neither needs clang-tidy, deliberately — this
# suite runs where no toolchain exists, and an arm that skips there is zero coverage in a green
# shirt.

# --- arm 1: a TU the walk names but the database does not cover is FATAL under --all-files ------
# Driven through the extracted predicate rather than a full run, so it is pure. The header case is
# asserted in the same breath, because collapsing it into "missing" would red every run on every
# header and is the obvious wrong fix.
verdict_fn="$(sed -n '/^_malf_lint_db_verdict() {/,/^}/p; /^_malf_lint_is_header() {/,/^}/p' "$MALF_BIN")"
# It publishes through a global (no fork per walked file), so each probe echoes the global back.
v_in="$(bash -c "$verdict_fn; _malf_lint_db_verdict /w/a.cpp a.cpp 1; echo \"\$_MALF_LINT_VERDICT\"")"
v_hdr="$(bash -c "$verdict_fn; _malf_lint_db_verdict /w/a.hpp a.hpp ''; echo \"\$_MALF_LINT_VERDICT\"")"
v_miss="$(bash -c "$verdict_fn; _malf_lint_db_verdict /w/b.cpp b.cpp ''; echo \"\$_MALF_LINT_VERDICT\"")"
check "db verdict: in-DB TU / uncovered HEADER / uncovered TU are three distinct answers" \
      "in-db header missing" \
      "$v_in $v_hdr $v_miss"

# The verdict is only half of it — the wiring that turns `missing` into a red under --all-files is
# the half that was absent, and it is a MODE-dependent rule, so both modes are pinned.
# Located by LINE ARITHMETIC, not by a sed range over `if $all_files`: there are THREE such
# blocks in cmd_lint (the walk, this one, the empty-set refusal) and a range anchored on the
# pattern latches onto the first, walks out through a different block, and reports NOT WIRED about
# correctly wired code. That false negative is exactly the thing this file exists to catch, so the
# anchor is the refusal message — which is unique — and the assertion is that the verdict is set
# in the three lines above it.
refuse_line="$(grep -n 'refusing to report success — --all-files walks the TREE' "$MALF_BIN" | cut -d: -f1)"
allfiles_fatal="$([[ -n "$refuse_line" ]] \
  && grep -q 'lint_status=1' <<<"$(sed -n "$((refuse_line - 3)),${refuse_line}p" "$MALF_BIN")" \
  && echo wired || echo "NOT WIRED (refusal at line ${refuse_line:-none})")"
check "an uncovered TU fails --all-files (the mode promises the tree, so a partial subject is a hole)" \
      "wired" \
      "$allfiles_fatal"

# And the accumulator must be declared BEFORE that site. It was not: `local lint_status=0` sat
# below the DB filter and would have reset the fatal verdict to zero on the way past — an arm
# setting a flag a later declaration wipes never fires, and looks exactly like the silence it ends.
decl_line="$(grep -n '^    local lint_status=0$' "$MALF_BIN" | cut -d: -f1)"
first_set="$(grep -n 'lint_status=1' "$MALF_BIN" | head -1 | cut -d: -f1)"
check "lint_status is declared above every site that sets it (no later 'local' wipes the verdict)" \
      "declared-first" \
      "$([[ -n "$decl_line" && -n "$first_set" && "$decl_line" -lt "$first_set" ]] && echo declared-first || echo "GOT decl=$decl_line first_set=$first_set")"

# --- arm 2: the profile-keyed build root beats a stray $CWD database --------------------------
# The discriminator is chosen so it needs no toolchain and cannot pass by accident: the build root
# gets a GCC database and $CWD gets a CLANG one. Under the old order $CWD wins, the toolchain guard
# is satisfied, and the run proceeds; under the new order the build root wins and that guard REFUSES,
# naming the g++ census. So the refusal itself is the proof of which file was read.
shadow_tmp="$(mktemp -d)"
shadow_root="$shadow_tmp/build-clang21-libcxx-release"
mkdir -p "$shadow_root"
printf 'export module probe;\n' > "$shadow_tmp/probe.cppm"
printf '[{"directory":"%s","file":"%s/probe.cppm","command":"/usr/bin/g++-16 -c probe.cppm"}]\n' \
       "$shadow_tmp" "$shadow_tmp" > "$shadow_root/compile_commands.json"
printf '[{"directory":"%s","file":"%s/probe.cppm","command":"/usr/bin/clang++-21 -c probe.cppm"}]\n' \
       "$shadow_tmp" "$shadow_tmp" > "$shadow_tmp/compile_commands.json"
shadow_out="$(cd "$shadow_tmp" && bash "$MALF_BIN" lint --all-files --console 2>&1)"; shadow_rc=$?
check "the profile-keyed build root is read, not the stray \$CWD database beside the recipe" \
      "rc=1 read-build-root" \
      "rc=$shadow_rc $([[ "$shadow_out" == *"not produced by clang"* && "$shadow_out" == *"g++-16"* ]] \
         && echo read-build-root || echo "GOT: $shadow_out")"
check "and the ignored stray file is NAMED, since silence is what let one survive two months" \
      "named" \
      "$([[ "$shadow_out" == *"malf never writes that path"* ]] && echo named || echo "GOT: $shadow_out")"
rm -rf "$shadow_tmp"

echo

echo "[7j] lint tells a DEAD translation unit apart from a clean one"

# Two subjects, one disease. On 2026-08-30 clang-tidy 21.1.8 was found to SIGSEGV on three
# translation units in insight-eidos, and the way both of the first two were found is the point:
# by accident, by lanes doing unrelated work. Nothing anywhere said a named file had gone through
# zero checks — the crash dump merged into the findings list, where it reads as something the
# SOURCE did wrong. A file can be clean, dirty, or UNREAD, and only the first two had a word.
#
# Pinned here and not trusted, because it fails SILENTLY: `_malf_tidy_crashed` decides which of
# "reported" and "died" happened, and it is exported into the fan-out children, so a regression in
# it turns every future crash back into a plausible-looking finding on a file nobody knows went
# unchecked.
#
# A SECOND subject stood here until 2026-08-30 — an expiry arm on config/.clang-tidy's
# `-modernize-use-std-print` line, which disabled that check because clang-tidy 21.1.8 SIGSEGVs on
# a printf/fprintf format literal carrying a byte >= 0x80. Exclusion and arm are both gone, and
# the reason is NOT that the tool was fixed (it still crashes): the workspace's last 202
# printf/fprintf calls became std::print, so the check has no matchable call site left to die on
# and was re-enabled. A newly written printf carrying a non-ASCII format literal would still kill
# its own TU — and the predicate below is what reports that, by name, instead of letting it read
# as a finding.
#
# No clang-tidy is needed: the predicate is pure. That matters — this suite runs on a hosted
# runner with no toolchain, and an arm that quietly skips there would be zero coverage in a green
# shirt.

crash_fn="$(sed -n '/^_malf_tidy_crashed() {/,/^}/p' "$MALF_BIN")"
crash_verdict() { bash -c "$crash_fn; if _malf_tidy_crashed \"\$1\" \"\$2\"; then echo died; else echo reported; fi" _ "$1" "$2"; }

check "clean run (rc 0, no banner) reads as REPORTED" \
      "reported" "$(crash_verdict 0 'no warnings')"
check "findings (rc 1, no banner) read as REPORTED — a red gate is not a dead one" \
      "reported" "$(crash_verdict 1 'x.cpp:1:1: warning: something [some-check]')"
check "SIGSEGV (rc 139) reads as DIED" \
      "died" "$(crash_verdict 139 '')"
check "timeout (rc 124) reads as DIED — the crash HANGS symbolizing, so slow IS dead" \
      "died" "$(crash_verdict 124 '')"
check "rc 0 WITH the LLVM crash banner reads as DIED — the status alone is not the tell" \
      "died" "$(crash_verdict 0 'PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/')"

echo

echo "[7j2] lint's changed-file set resolves from any directory of the repo, or refuses"

# THE FALSE ZERO, measured by Kleio 2026-09-02 on insight-canon/core with one changed TU: `malf
# lint` from the package subdirectory printed "no files to check" and exited 0; from the repo root
# it checked 1 file. `git diff --name-only` emits REPO-ROOT-relative paths whatever `-C` says, and
# the default leg prepended $CWD to them, so from a subdirectory every path failed the existence
# test and the set came out empty — "could not look" sharing the exit path of "found nothing", the
# class CLAUDE.md § Searching hunts. The leg now lets git resolve (`--relative`: cwd-relative AND
# scoped to the cwd's subtree, a no-op at the root), refuses when git itself fails, and refuses on
# a listed path that does not resolve. Pinned end to end, because the resolution is one flag on one
# git call and a flag is one edit from being "simplified" back.
#
# No clang-tidy is needed: a FAKE clang-21/clang-tidy pair on PATH records the arguments it was
# handed, which is the observable — WHICH files reached the tool. The compile databases are
# per-directory as malf keys them, one entry per product TU, so the DB-completeness filter passes
# exactly what the resolution selected and nothing else.

lk_tmp="$(realpath "$(mktemp -d)")"
lk_key="${MALF_DEFAULT_PROFILE#linux-}"      # the build-<key>/ malf reads a database from
lk_bin="$lk_tmp/bin"; mkdir -p "$lk_bin"
printf '#!/usr/bin/env bash\nexit 0\n' > "$lk_bin/clang-21"                       # -print-resource-dir -> nothing
cat > "$lk_bin/clang-tidy" <<'LKTIDY'
#!/usr/bin/env bash
# Records every argument it is handed (the observable: WHICH files reached the tool), then either
# answers clean, sleeps past the per-TU cap on the named TU ([7j3]), or dies with 139 on it.
# With LK_TIDY_DB_LOG set it also records the DATABASE it was pointed at ([7j4]): the flags a TU
# is checked under are not visible in the argument list — `-p` names a directory, and what that
# directory holds is the whole subject of the -isystem/-I question.
printf '%s\n' "$@" >> "$LK_TIDY_LOG"
if [[ -n "${LK_TIDY_DB_LOG:-}" ]]; then
    for _i in $(seq 1 $#); do
        if [[ "${!_i}" == "-p" ]]; then
            _n=$((_i + 1)); cat "${!_n}/compile_commands.json" >> "$LK_TIDY_DB_LOG" 2>/dev/null
        fi
    done
fi
last="${*: -1}"
# One diagnostic plus its two note lines ([7j5]): a note is a CONTINUATION of the finding above
# it, never a finding of its own, and a summary that counted lines would report three.
if [[ -n "${LK_TIDY_WARN_ON:-}" && "$last" == *"$LK_TIDY_WARN_ON" ]]; then
    printf '%s:1:1: warning: fixture finding [fixture-check]\n' "$last"
    printf '%s:2:1: note: +1, nesting level increased to 1\n' "$last"
    printf '%s:3:1: note: +2, nesting level increased to 2\n' "$last"
    exit 0
fi
[[ -n "${LK_TIDY_SLEEP_ON:-}" && "$last" == *"$LK_TIDY_SLEEP_ON" ]] && exec sleep 5
[[ -n "${LK_TIDY_DIE_ON:-}" && "$last" == *"$LK_TIDY_DIE_ON" ]] && exit 139
# The address-space limit this process runs under ([7j3c]): the width counts each child at that cap.
[[ -n "${LK_TIDY_ULIMIT_LOG:-}" ]] && ulimit -v >> "$LK_TIDY_ULIMIT_LOG"
# Grow until the address-space limit refuses, as a checker on a too-heavy unit does, then abort.
# It stays at its peak for a moment first, so the peak is one a sampler can read.
if [[ -n "${LK_TIDY_HOG_ON:-}" && "$last" == *"$LK_TIDY_HOG_ON" ]]; then
    exec python3 -c 'import os, time
held = []
try:
    while True:
        held.append(bytearray(8 * 1024 * 1024))
except MemoryError:
    time.sleep(0.6)
    os.abort()'
fi
exit 0
LKTIDY
chmod +x "$lk_bin/clang-21" "$lk_bin/clang-tidy"

lk_repo="$lk_tmp/repo"
mkdir -p "$lk_repo/core/src" "$lk_repo/core/tests" "$lk_repo/sift/src"
git -C "$lk_repo" init -q 2>/dev/null
git -C "$lk_repo" -c user.email=t@t -c user.name=t checkout -q -b main 2>/dev/null || true
printf 'int engine() { return 1; }\n'  > "$lk_repo/core/src/engine.cpp"
printf 'int probe() { return 1; }\n'   > "$lk_repo/core/tests/engine_probe.cpp"
printf 'int other() { return 1; }\n'   > "$lk_repo/sift/src/other.cpp"
git -C "$lk_repo" add -A
git -C "$lk_repo" -c user.email=t@t -c user.name=t commit -q -m fixture
# The change under test: every TU edited in the worktree, none staged.
printf 'int engine() { return 2; }\n'  > "$lk_repo/core/src/engine.cpp"
printf 'int probe() { return 2; }\n'   > "$lk_repo/core/tests/engine_probe.cpp"
printf 'int other() { return 2; }\n'   > "$lk_repo/sift/src/other.cpp"

lk_db() {   # <dir> <file>... — a clang database covering exactly these TUs, keyed as malf keys it
    local dir="$1"; shift
    local build="$dir/build-$lk_key"; mkdir -p "$build"
    python3 - "$build" "$@" <<'PYDB'
import json, sys
build, files = sys.argv[1], sys.argv[2:]
json.dump([{"directory": build, "command": f"clang++-21 -std=c++23 -c {f} -o out.o", "file": f}
           for f in files], open(f"{build}/compile_commands.json", "w"))
PYDB
}
lk_db "$lk_repo/core" "$lk_repo/core/src/engine.cpp"
lk_db "$lk_repo"      "$lk_repo/core/src/engine.cpp" "$lk_repo/sift/src/other.cpp"

lk_run() {   # <dir> <log> — malf lint (default mode, console) from <dir> with the fake toolchain
    (cd "$1" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$2" MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)
}
lk_checked() {   # <log> — the files the fake clang-tidy was handed, relative to the repo, sorted
    [[ -f "$1" ]] || return 0
    grep -E '\.cpp$' "$1" | sed "s|^$lk_repo/||" | sort | tr '\n' ' '
}

lk_sub_out="$(lk_run "$lk_repo/core" "$lk_tmp/tidy.subdir.log")"; lk_sub_rc=$?
check "lint from a package SUBDIRECTORY checks the changed TU under it (the Kleio case)" \
      "rc=0 core/src/engine.cpp " "rc=$lk_sub_rc $(lk_checked "$lk_tmp/tidy.subdir.log")"
check "lint from the subdirectory says how many it checked (never 'no files to check')" \
      "checking 1 file(s)" "$(grep -oE 'checking [0-9]+ file\(s\)|no files to check' <<< "$lk_sub_out" | head -1)"

lk_root_out="$(lk_run "$lk_repo" "$lk_tmp/tidy.root.log")"; lk_root_rc=$?
check "lint from the repo ROOT checks both product TUs and not the tests/ one (unchanged behaviour)" \
      "rc=0 core/src/engine.cpp sift/src/other.cpp " "rc=$lk_root_rc $(lk_checked "$lk_tmp/tidy.root.log")"

# Anti-vacuity: the historical authoring, restated as the MUTANT, must LOSE on this fixture from
# the subdirectory — or the first arm above proves nothing about the resolution.
lk_historical() {   # the pre-2026-09-02 leg: $CWD prepended to git's repo-relative paths
    local cwd="$1" f out=""
    while IFS= read -r f; do
        [[ "$f" =~ \.(cpp|cc|cxx|h|hpp|cppm)$ ]] && [[ -f "$cwd/$f" ]] \
            && ! _malf_lint_path_excluded "$f" && out+="$f "
    done < <(git -C "$cwd" diff --name-only HEAD 2>/dev/null || true)
    printf '%s' "$out"
}
check "the historical leg selects NOTHING from the subdirectory (the false zero, reproduced)" \
      "" "$(lk_historical "$lk_repo/core")"
check "the historical leg selects both product TUs from the root (the asymmetry Kleio measured)" \
      "core/src/engine.cpp sift/src/other.cpp " "$(lk_historical "$lk_repo")"

# The two refusals: an empty set reached by a FAILED listing is never a pass.
lk_nonrepo="$lk_tmp/nonrepo"; mkdir -p "$lk_nonrepo"
lk_db "$lk_nonrepo" "$lk_nonrepo/x.cpp"            # a database, so the DB guard is not what refuses
lk_nr_out="$(lk_run "$lk_nonrepo" "$lk_tmp/tidy.nonrepo.log")"; lk_nr_rc=$?
check "lint outside a git checkout REFUSES (rc 1, named) instead of 'no files to check' rc 0" \
      "rc=1 refused" "rc=$lk_nr_rc $([[ "$lk_nr_out" == *"could not list the changed files"* ]] && echo refused || echo "GOT: $lk_nr_out")"

echo "[7j3] lint's NOT LINTED verdict names the cause — a per-TU timeout is not a checker death"

# `_malf_tidy_crashed` counts timeout's exit 124 as died, correctly: the TU went through zero
# checks either way. But the report used to say "clang-tidy died on them" for both causes, and a
# reader then hunts a crash that never happened. Measured by Hephaïstos 2026-09-02 on logcraft
# under MALF_LINT_TU_TIMEOUT_S=60: engine_manager.cpp and http_sink.cpp read as dead; at the
# default cap they were 0 findings in 72 s and 57 s. The cause is read from the exit status the
# fan-out child records, never re-derived from the crash text. Same fixture, same fake toolchain:
# told to sleep past a 1 s cap on one TU, then told to exit 139 on it.
lk_to_out="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.timeout.log" \
    LK_TIDY_SLEEP_ON=engine.cpp MALF_LINT_TU_TIMEOUT_S=1 MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"; lk_to_rc=$?
check "a TU past the per-TU cap reads TIMED OUT at N s (MALF_LINT_TU_TIMEOUT_S), by name, rc 1" \
      "rc=1 timed-out:src/engine.cpp" \
      "rc=$lk_to_rc $([[ "$lk_to_out" == *"src/engine.cpp — TIMED OUT at 1 s (MALF_LINT_TU_TIMEOUT_S)"* ]] && echo timed-out:src/engine.cpp || echo "GOT: $lk_to_out")"
check "the timed-out verdict counts 0 deaths and never calls that TU dead" \
      "no-crash-wording" \
      "$([[ "$lk_to_out" == *"died on 0, TIMED OUT on 1"* && "$lk_to_out" != *"src/engine.cpp — died"* ]] && echo no-crash-wording || echo "GOT: $lk_to_out")"
lk_die_out="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.die.log" \
    LK_TIDY_DIE_ON=engine.cpp MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"; lk_die_rc=$?
check "a TU whose checker exits 139 reads died (exit 139), never timed out" \
      "rc=1 died:src/engine.cpp" \
      "rc=$lk_die_rc $([[ "$lk_die_out" == *"src/engine.cpp — died (exit 139)"* && "$lk_die_out" != *"TIMED OUT at"* ]] && echo died:src/engine.cpp || echo "GOT: $lk_die_out")"

echo "[7j3b] every linted translation unit leaves what it cost: elapsed, max RSS, peak address space"

# The fan-out's width is derived from the memory one child may take, and that figure is a
# measurement. So each child records its own, on its progress line, and the run names the heaviest.
# The third figure is the one the child's cap actually bounds: `ulimit -v` limits address space.
tc_tool="$MALF_ROOT/tu_cost.py"
tc_tmp="$(mktemp -d)"
python3 "$tc_tool" 5 "$tc_tmp/cost" bash -c 'exit 3'; tc_rc=$?
check "tu_cost passes the command's exit code through and writes three integers" \
      "rc=3 three" "rc=$tc_rc $([[ "$(cat "$tc_tmp/cost" 2>/dev/null)" =~ ^[0-9]+\ [0-9]+\ [0-9]+$ ]] && echo three || echo "GOT: $(cat "$tc_tmp/cost" 2>&1)")"
python3 "$tc_tool" 0.3 "$tc_tmp/cost" sleep 30; tc_rc=$?
read -r tc_ms _ _ < "$tc_tmp/cost"
check "past its cap the command is ended and the exit code is timeout's 124, without waiting it out" \
      "rc=124 ended" "rc=$tc_rc $([[ "${tc_ms:-99999}" -lt 5000 ]] && echo ended || echo "GOT: ${tc_ms:-none} ms")"
python3 "$tc_tool" 5 "$tc_tmp/cost" bash -c 'kill -SEGV $$'; tc_rc=$?
check "a command that dies on a signal reads 128 + the signal, as a shell reports it" \
      "rc=139" "rc=$tc_rc"
python3 "$tc_tool" 5 "$tc_tmp/cost" python3 -c 'x = bytearray(64 * 1024 * 1024); import time; time.sleep(0.5)'
read -r _ tc_rss tc_vm < "$tc_tmp/cost"
check "a command holding 64 MiB records at least that in max RSS and in peak address space" \
      "rss vm" "$([[ "${tc_rss:-0}" -ge 65536 ]] && echo rss || echo "RSS:${tc_rss:-none}") $([[ "${tc_vm:-0}" -ge 65536 ]] && echo vm || echo "VM:${tc_vm:-none}")"
rm -rf "$tc_tmp"
lk_cost_out="$(lk_run "$lk_repo" "$lk_tmp/tidy.cost.log")"; lk_cost_rc=$?
check "each progress line carries its translation unit's three figures" \
      "rc=0 2" \
      "rc=$lk_cost_rc $(grep -cE '^\[[0-9]+/2\] (core/src/engine|sift/src/other)\.cpp  [0-9]+\.[0-9] s  rss [0-9]+ MiB  vm [0-9]+ MiB$' <<< "$lk_cost_out")"
check "the run ends with one COST line naming the heaviest and the longest translation unit" \
      "named" "$(grep -qE '^malf lint: COST · 2 translation unit\(s\) measured · heaviest rss [0-9]+ MiB \((core/src/engine|sift/src/other)\.cpp\) · heaviest address space [0-9]+ MiB \([^)]+\) · longest [0-9]+\.[0-9] s \([^)]+\)$' <<< "$lk_cost_out" && echo named || echo "GOT: $(grep -E 'COST|SUMMARY' <<< "$lk_cost_out")")"
check "a timed-out translation unit still leaves its cost, and the verdict is unchanged" \
      "rc=1 1 timed-out" \
      "$(out="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.cost2.log" LK_TIDY_SLEEP_ON=engine.cpp MALF_LINT_TU_TIMEOUT_S=1 MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"; echo "rc=$? $(grep -cE '^\[1/1\] src/engine\.cpp  [0-9]+\.[0-9] s  rss ' <<< "$out") $([[ "$out" == *"TIMED OUT at 1 s"* ]] && echo timed-out || echo no-timeout)")"

echo "[7j3c] the lint's width is derived like the build's, at a cap each child is held to"

check "the lint child's declared cap is 1.75 GiB, and on an idle 16-core machine that is 10 jobs" \
      "1835008 10" "$MALF_LINT_MEM_LIMIT_KB $(_malf_width 16 21486592 "$MALF_FANOUT_RESERVE_KB" "$MALF_LINT_MEM_LIMIT_KB")"
check "beside a build holding 7.8 GiB (12.2 GiB available) the lint gets 5 jobs" \
      "5" "$(_malf_width 16 12792627 "$MALF_FANOUT_RESERVE_KB" "$MALF_LINT_MEM_LIMIT_KB")"
check "the run prints its width's derivation, the four operands and the width" \
      "1" "$(grep -cE '^malf lint: -j[0-9]+ = (max\(1, min\([0-9]+ cores, floor\(\(MemAvailable [0-9]+ MiB - reserve 2048 MiB\) / [0-9]+ MiB a job\)\)\)|[0-9]+ cores; MemAvailable is not reported)' <<< "$lk_cost_out")"
lk_ul_out="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.ul.log" \
    LK_TIDY_ULIMIT_LOG="$lk_tmp/ulimit.log" MALF_LINT_MEM_LIMIT_KB=3000000 MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"
check "each child runs under the address-space cap the width counted it at (MALF_LINT_MEM_LIMIT_KB)" \
      "3000000 2929" \
      "$(cat "$lk_tmp/ulimit.log" 2>/dev/null) $(sed -n 's|^malf lint: -j.* / \([0-9]*\) MiB a job.*|\1|p' <<< "$lk_ul_out")"
# A unit the cap ends is a THIRD cause, beside a checker death and a timeout: named, because its
# remedy is the cap and not the check.
lk_hog_out="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.hog.log" \
    LK_TIDY_HOG_ON=engine.cpp MALF_LINT_MEM_LIMIT_KB=307200 MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"; lk_hog_rc=$?
check "a unit that dies with its address space at the cap reads AT THE MEMORY CAP, naming both figures" \
      "rc=1 capped" \
      "rc=$lk_hog_rc $(grep -qE 'src/engine\.cpp — died \(exit 134\) AT THE MEMORY CAP: address space [0-9]+ MiB of 300 MiB \(MALF_LINT_MEM_LIMIT_KB\)' <<< "$lk_hog_out" && echo capped || echo "GOT: $(grep -E 'engine.cpp|NOT LINTED' <<< "$lk_hog_out")")"
check "a unit that dies far under the cap is a plain death, with no cap wording" \
      "plain" "$([[ "$lk_die_out" == *"src/engine.cpp — died (exit 139)"* && "$lk_die_out" != *"AT THE MEMORY CAP"* ]] && echo plain || echo "GOT: $lk_die_out")"

echo "[7j4] lint de-systems FIRST-PARTY include roots, and leaves third-party ones alone"

# WHY THIS EXISTS. Every dependency reaches a consumer as a CMake IMPORTED target, and CMake's
# default is that an IMPORTED target's include directories are SYSTEM directories — so the
# generator emits `-isystem` for our own editable packages exactly as it does for a conan cache
# package, and clang-tidy suppresses every diagnostic whose location is in a system header.
# Measured 2026-09-02 on coderoast-server: a `throw 1;` outside the guard in the log seat's
# `noexcept` `flush_logger` was SILENT through a consumer TU; the identical throw in that TU's own
# `.cpp` fired `bugprone-exception-escape`. The cost was not the missing diagnostics but the
# header comment asserting the check "stays ARMED on this frame", which had been reading as a
# guarantee. `malf lint` now rewrites `-isystem <first-party>` to `-I<first-party>` in a database
# derived into the run's own scratch directory; nothing that ships changes.
#
# THE OBSERVABLE IS THE DATABASE, NOT THE ARGUMENT LIST. `-p` names a directory, so the fake
# clang-tidy above records what that directory HELD when it was handed one (LK_TIDY_DB_LOG).
#
# The fixture puts a workspace root and a conan home under $lk_tmp so the classification has all
# four shapes to separate: a first-party root inside the workspace, a conan-cache root inside the
# workspace, a root OUTSIDE the workspace, and a first-party-looking path that is not a directory
# at all. The last is the precision arm: a stale database entry must not be rewritten, and it is
# also what keeps a path containing a space (which the command-string form returns truncated)
# out of the rewrite.

lk_ws_api="$lk_repo/api"                                   # first-party: in the workspace, in no cache
lk_ws_cache="$lk_tmp/.conan2/p/dep/include"                # third-party: inside the conan home
lk_ws_out="$(realpath "$(mktemp -d)")/include"             # third-party: outside the workspace root
lk_ws_gone="$lk_repo/api-that-was-removed"                 # first-party SHAPE, no directory
mkdir -p "$lk_ws_api" "$lk_ws_cache" "$lk_ws_out"
printf 'inline int seat() { return 1; }\n' > "$lk_ws_api/seat.hpp"

lk_ws_db() {   # a database for core/ whose one entry carries all four -isystem shapes
    local build="$lk_repo/core/build-$lk_key"; mkdir -p "$build"
    python3 - "$build" "$lk_repo/core/src/engine.cpp" "$1" "$2" "$3" "$4" <<'PYWS'
import json, sys
build, tu, api, cache, out, gone = sys.argv[1:7]
cmd = (f"clang++-21 -std=c++23 -isystem {api} -isystem {cache} -isystem {out} "
       f"-isystem {gone} -c {tu} -o out.o")
json.dump([{"directory": build, "command": cmd, "file": tu}], open(f"{build}/compile_commands.json", "w"))
PYWS
}
lk_ws_db "$lk_ws_api" "$lk_ws_cache" "$lk_ws_out" "$lk_ws_gone"

lk_ws_run() {   # <dblog> — malf lint from core/ with the fixture's own workspace root and conan home
    (cd "$lk_repo/core" && PATH="$lk_bin:$PATH" \
        LK_TIDY_LOG="$lk_tmp/tidy.ws.log" LK_TIDY_DB_LOG="$1" \
        MALF_WORKSPACE_ROOT="$lk_tmp" CONAN_HOME="$lk_tmp/.conan2" MALF_PROFILE_NAME="" \
        bash "$MALF_BIN" lint --console 2>&1)
}

rm -f "$lk_tmp/tidy.ws.log"
lk_ws_out_txt="$(lk_ws_run "$lk_tmp/db.ws.json")"; lk_ws_rc=$?
lk_ws_db_seen="$(cat "$lk_tmp/db.ws.json" 2>/dev/null)"

check "the first-party root reaches clang-tidy as -I, not -isystem (the whole subject)" \
      "de-systemed" \
      "$([[ "$lk_ws_db_seen" == *"-I$lk_ws_api "* && "$lk_ws_db_seen" != *"-isystem $lk_ws_api "* ]] \
         && echo de-systemed || echo "GOT: $lk_ws_db_seen")"
check "a root inside the CONAN HOME stays -isystem (third-party must not be un-suppressed)" \
      "system" \
      "$([[ "$lk_ws_db_seen" == *"-isystem $lk_ws_cache "* ]] && echo system || echo "GOT: $lk_ws_db_seen")"
check "a root OUTSIDE the workspace stays -isystem (the workspace test is half the classifier)" \
      "system" \
      "$([[ "$lk_ws_db_seen" == *"-isystem $lk_ws_out "* ]] && echo system || echo "GOT: $lk_ws_db_seen")"
check "a first-party-SHAPED path that is not a directory is left alone (precision, and the space guard)" \
      "system" \
      "$([[ "$lk_ws_db_seen" == *"-isystem $lk_ws_gone "* ]] && echo system || echo "GOT: $lk_ws_db_seen")"
check "the run says what it de-systemed and what it left — a silent rewrite is unauditable" \
      "reported" \
      "$([[ "$lk_ws_out_txt" == *"first-party headers de-systemed: 1 include reference(s) over 1 root(s); 3 reference(s) left as system headers"* ]] \
         && echo reported || echo "GOT: $lk_ws_out_txt")"
check "the run still checks the changed TU (the rewrite is not allowed to lose the subject), rc 0" \
      "rc=0 core/src/engine.cpp " "rc=$lk_ws_rc $(lk_checked "$lk_tmp/tidy.ws.log")"

# THE ZERO CASE PASSES AND IS NAMED. coderoast-ipc and coderoast-security carry no first-party
# editable include root at all, so a fatal-on-empty rule here would make the step unusable — but a
# run that rewrote nothing must not read like a run that failed to look.
lk_ws_db "$lk_ws_cache" "$lk_ws_cache" "$lk_ws_out" "$lk_ws_out"
rm -f "$lk_tmp/tidy.ws0.log"
lk_ws0_txt="$(cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$lk_tmp/tidy.ws0.log" \
    LK_TIDY_DB_LOG="$lk_tmp/db.ws0.json" MALF_WORKSPACE_ROOT="$lk_tmp" CONAN_HOME="$lk_tmp/.conan2" \
    MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --console 2>&1)"; lk_ws0_rc=$?
check "a database with no first-party root PASSES and says so (an empty seat is not a failure)" \
      "rc=0 named" \
      "rc=$lk_ws0_rc $([[ "$lk_ws0_txt" == *"no first-party include root reaches this database as -isystem; 4 reference(s) left as system headers"* ]] \
         && echo named || echo "GOT: $lk_ws0_txt")"

echo

printf 'int ghost() { return 1; }\n' > "$lk_repo/core/src/ghost.cpp"
git -C "$lk_repo" add core/src/ghost.cpp
rm "$lk_repo/core/src/ghost.cpp"                    # staged, then gone: listed by the index leg, unresolvable
lk_gh_out="$(lk_run "$lk_repo/core" "$lk_tmp/tidy.ghost.log")"; lk_gh_rc=$?
check "a listed path that does not resolve REFUSES by name instead of being skipped" \
      "rc=1 refused:src/ghost.cpp" "rc=$lk_gh_rc $([[ "$lk_gh_out" == *"git lists 'src/ghost.cpp' as changed"* ]] && echo refused:src/ghost.cpp || echo "GOT: $lk_gh_out")"

echo "[7j5] every lint run STATES ITS OWN SCOPE — mode, population, checked, findings"

# THE RULING (Founder, 2026-09-03) and the measurement under it. `malf lint` with no arguments
# takes its subject from `git diff` against HEAD, so on a CLEAN worktree it selected nothing,
# printed "no files to check" and returned 0 having read zero bytes of source. Measured 2026-09-02
# in coderoast-ipc and insight-twin: both exited 0 that way once an unrelated guard stopped
# refusing, and the real verdict needed --all-files. NOTHING IN THE OUTPUT SEPARATED THAT FROM A
# RUN THAT CHECKED EVERYTHING AND FOUND NOTHING, so "malf lint, 0 findings" was one sentence about
# two opposite facts and a reader could not tell which he had. The defect was in what the green
# FAILED TO SAY, which is why the fix is an output line and why it needs pinning: a summary line is
# exactly what a later edit trims as noise, and the loss would be silent — every run still passes.
# The arms below are the three states a run can be in, plus the two facts that must never merge.

lk_sum() { grep -oE 'malf lint: SUMMARY .*' <<< "$1" | head -1; }
lk_counts() { grep -oE 'selected [0-9]+ = [0-9]+ translation unit\(s\) \+ [0-9]+ header\(s\), checked [0-9]+, [0-9]+ finding\(s\), [0-9]+ not linted' <<< "$1" | head -1; }
lk_af_run() {   # <log> [env...] — malf lint --all-files from core/ with the fake toolchain
    (cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$1" MALF_PROFILE_NAME="" \
        env "${@:2}" bash "$MALF_BIN" lint --all-files --console 2>&1)
}

git -C "$lk_repo" reset -q                                    # drop the ghost arm's staged path
git -C "$lk_repo" -c user.email=t@t -c user.name=t commit -aq -m clean
lk_clean_out="$(lk_run "$lk_repo/core" "$lk_tmp/tidy.clean.log")"; lk_clean_rc=$?
check "STATE 1/3 — a CLEAN tree checks nothing, and the summary says so instead of reading as a pass" \
      "rc=0 declared" \
      "rc=$lk_clean_rc $([[ "$lk_clean_out" == *"selected 0 = 0 translation unit(s) + 0 header(s), CHECKED 0 — NOTHING WAS INSPECTED"* \
          && "$lk_clean_out" == *"NOT a clean verdict"* ]] && echo declared || echo "GOT: $lk_clean_out")"
check "the zero-file summary NAMES the selection mode that produced the empty set" \
      "named" \
      "$([[ "$lk_clean_out" == *"mode=default (git diff --name-only HEAD, worktree+index, --relative)"* ]] \
         && echo named || echo "GOT: $lk_clean_out")"
check "the zero-file run never says 'no files to check' — the phrase that read as a verdict" \
      "gone" \
      "$([[ "$lk_clean_out" != *"no files to check"* ]] && echo gone || echo "GOT: $lk_clean_out")"

lk_af_out="$(lk_af_run "$lk_tmp/tidy.af.log")"; lk_af_rc=$?
check "STATE 2/3 — --all-files on that SAME clean tree checks the TU and finds nothing, rc 0" \
      "rc=0 selected 1 = 1 translation unit(s) + 0 header(s), checked 1, 0 finding(s), 0 not linted" \
      "rc=$lk_af_rc $(lk_counts "$lk_af_out")"
check "the two states are DISTINGUISHABLE — the clean-run summary and the zero-run summary differ" \
      "distinct" \
      "$([[ "$(lk_sum "$lk_af_out")" != "$(lk_sum "$lk_clean_out")" \
          && "$lk_af_out" != *"NOTHING WAS INSPECTED"* ]] && echo distinct || echo "GOT: $(lk_sum "$lk_af_out")")"
check "--all-files names ITS mode, so the fact that was invisible is on both paths" \
      "named" \
      "$([[ "$lk_af_out" == *"mode=--all-files (walk of source extensions under the tree)"* ]] \
         && echo named || echo "GOT: $(lk_sum "$lk_af_out")")"

lk_warn_out="$(lk_af_run "$lk_tmp/tidy.warn.log" LK_TIDY_WARN_ON=engine.cpp)"; lk_warn_rc=$?
# note: the fixture's clang-tidy exits 0 on its warning, as the real one does on any check outside
# WarningsAsErrors; the run used to return that 0, so a gate reading the exit passed 14 findings.
check "STATE 3/3 — a run WITH findings counts them, a note line is not one, and a WARNING fails the run" \
      "rc=1 selected 1 = 1 translation unit(s) + 0 header(s), checked 1, 1 finding(s), 0 not linted" \
      "rc=$lk_warn_rc $(lk_counts "$lk_warn_out")"
check "the failing run says why: every finding fails it, a warning as much as an error" \
      "1" "$(grep -c '1 finding(s) — every finding fails the run' <<< "$lk_warn_out")"

# A TU THE CHECKER NEVER READ IS ITS OWN COLUMN. Clean, dirty and UNREAD are three states and the
# summary must not fold the third into either of the first two — 1 finding and 1 not-linted are
# opposite facts about coverage. [7j3] pins the NOT LINTED block itself; this pins that the
# one-line summary carries the same count, since that line is what a reader stops at.
lk_nl_out="$(lk_af_run "$lk_tmp/tidy.nl.log" LK_TIDY_DIE_ON=engine.cpp)"; lk_nl_rc=$?
check "a TU clang-tidy never read is counted NOT LINTED in the summary, not as 0 findings, rc 1" \
      "rc=1 selected 1 = 1 translation unit(s) + 0 header(s), checked 1, 0 finding(s), 1 not linted" \
      "rc=$lk_nl_rc $(lk_counts "$lk_nl_out")"

# A HEADER IS NEVER A TRANSLATION UNIT, AND THE SUMMARY USED TO COUNT IT AS ONE THE RUN SKIPPED.
# The walk matches `.h`/`.hpp`, the compile-DB filter drops them by design (a header has no compile
# command; it is reached through --header-filter from a TU that includes it), and `selected` still
# carried them — so `selected > checked` read as a coverage gap that was only headers. Measured on
# insight-eidos's v1.10.4 release lint (job 106726130358, 2026-09-22): "selected 107, checked 85,
# 2 platform-refused", of which 20 were headers and no TU was unchecked. The split puts the header
# count on the line, so the TU count is the one a reader compares with `checked`.
printf 'int engine();\n' > "$lk_repo/core/src/engine.hpp"
git -C "$lk_repo" add core/src/engine.hpp
git -C "$lk_repo" -c user.email=t@t -c user.name=t commit -q -m header
lk_hdr_out="$(lk_af_run "$lk_tmp/tidy.hdr.log")"; lk_hdr_rc=$?
check "a walked HEADER is counted apart from the translation units, never among the unchecked" \
      "rc=0 selected 2 = 1 translation unit(s) + 1 header(s), checked 1, 0 finding(s), 0 not linted" \
      "rc=$lk_hdr_rc $(lk_counts "$lk_hdr_out")"
check "and the header itself never reaches clang-tidy — it is linted only through the TU including it" \
      "0" "$(grep -c 'engine\.hpp$' "$lk_tmp/tidy.hdr.log" || true)"

rm -rf "$lk_tmp"
echo

echo "[7h] build_inventory — the ADR-3.D10 shape gate on workspace-grain cells"

# A cell whose defines dereference \${workspace} beyond the repo root is workspace-grain.
# Absent sibling => single-repo shape SKIPS (loud, counted, declared) while the workspace
# shape FAILS — and the skip must be UNREACHABLE in the workspace shape (ADR-3.D10's
# BOTH-SHAPES MUST). The tool is driven directly (the same seam malf's
# MALF_SKIP_INVENTORY mutation arms use); python runs with -B so no __pycache__ dirties
# the tree. Homing: RATIFIED in place (Kleio, 2026-08-17) — every property here is a
# malf-repo-local tool contract, so the tool's own no-network selftest is the home; the
# workspace shape's LIVE compile proof is deliberately NOT here — it is held by
# ADR-3.D10's release-train coverage MUST (scripts/workspace_grain_coverage.py), and a
# stubbed compile in this suite would be a second, weaker copy of that gate.
inv_tmp="$(mktemp -d)"   # cleaned inline below (a second `trap ... EXIT` would REPLACE [7d]'s)
BI="$MALF_ROOT/build_inventory.py"

# THE NEEDLE IS IMPORTED FROM ITS ONE WRITE SITE, never retyped here: an absence
# assertion keyed on a hand-copied string goes vacuous on the first rewording
# (MEM:synthetic-gate-vacuity-vs-judgment). Test A proves this same needle matches real
# output, which is what makes the absence assertions in C non-vacuous.
skip_needle="$(python3 -B -c "import sys; sys.path.insert(0, '$MALF_ROOT'); \
import build_inventory; print(build_inventory.WORKSPACE_GRAIN_SKIP_NEEDLE)")"
check "the skip needle constant resolves non-empty (guards a vacuous absence assert)" \
      "non-empty" "$([[ -n "$skip_needle" ]] && echo non-empty || echo EMPTY)"

# One fixture writer => the SAME manifest in every arrangement, so the arm that proves
# "this condition skips" (A) and the arm that proves "the same condition FAILS in the
# workspace shape" (C) are bound to one condition, not to two hand-copies.
write_probe_repo() {
    mkdir -p "$1/cell"
    printf 'project(probe_cell LANGUAGES NONE)\n' > "$1/cell/CMakeLists.txt"
    cat > "$1/packages.yml" <<'YAML'
inventory:
  probe_cell:
    path: cell
    toolchain_from: .
    target: probe_bin
    defines:
      SIB_ROOT: ${workspace}/insight-sib
      OWN_ROOT: ${repo}
YAML
}

solo="$inv_tmp/solo"          # single-repo shape: workspace root == repo root
write_probe_repo "$solo"
ws="$inv_tmp/ws"              # workspace shape: workspace root != repo root
write_probe_repo "$ws/repoA"

# (A) single-repo shape + absent sibling -> declared, counted SKIP; exit 0.
check "A: arrangement applied — the sibling is genuinely absent (single-repo)" \
      "absent" "$([[ ! -e "$solo/insight-sib" ]] && echo absent || echo PRESENT)"
solo_out="$(python3 -B "$BI" build --workspace "$solo" --repo "$solo" \
            --build-key probe --profile probe --build-type Release 2>&1)"; solo_rc=$?
check "A: single-repo + absent sibling exits 0" "0" "$solo_rc"
check "A: the skip line is PRESENT, matched via the imported needle" \
      "present" "$(grep -qF "$skip_needle" <<< "$solo_out" && echo present || echo "ABSENT: $solo_out")"
check "A: the skip names the cell" \
      "named" "$(grep -qF "cell probe_cell" <<< "$solo_out" && echo named || echo "unnamed: $solo_out")"
check "A: the skip names the missing root" \
      "named" "$(grep -qF "$solo/insight-sib" <<< "$solo_out" && echo named || echo "unnamed: $solo_out")"
check "A: the skip is COUNTED, not only declared" \
      "counted" "$(grep -qF "1 workspace-grain cell(s) skipped" <<< "$solo_out" && echo counted || echo "uncounted: $solo_out")"

# (B) lint membership is shape-independent: the cell still counts toward non-vacuity in
# the single-repo shape with the sibling absent — the skip lives in the BUILD arm only.
git -C "$solo" init -q 2>/dev/null \
    && git -C "$solo" add packages.yml cell/CMakeLists.txt 2>/dev/null
lint_solo_out="$(python3 -B "$BI" lint --workspace "$solo" 2>&1)"; lint_solo_rc=$?
check "B: lint counts the workspace-grain cell in single-repo shape (sibling absent)" \
      "rc=0 counted" \
      "rc=$lint_solo_rc $(grep -qF "1 declared CMake project" <<< "$lint_solo_out" && echo counted || echo "GOT: $lint_solo_out")"

# (B') the SAME two predicates in the WORKSPACE shape — the leg the BOTH-SHAPES MUST was
# minted for: repo discovery went blind on exactly one shape at the v1.9.3 ipc tag
# (run 31634074680 — zero repos found, the non-vacuity arm was right and discovery was
# blind), so a one-shape proof of discovery+lint is the scope-blindness ADR-3.D10 names.
# The ws root carries NO .git, so the only way lint can count this cell is by DISCOVERING
# repoA as a child repo. The needle pins both facts at once: 1 repo found, 1 cell counted.
git -C "$ws/repoA" init -q 2>/dev/null \
    && git -C "$ws/repoA" add packages.yml cell/CMakeLists.txt 2>/dev/null
lint_ws_out="$(python3 -B "$BI" lint --workspace "$ws" 2>&1)"; lint_ws_rc=$?
check "B': lint DISCOVERS the child repo and counts its cell in the workspace shape" \
      "rc=0 counted" \
      "rc=$lint_ws_rc $(grep -qF "1 repos, 1 declared CMake project" <<< "$lint_ws_out" && echo counted || echo "GOT: $lint_ws_out")"

# (C) workspace shape + absent sibling -> loud FAIL naming the cell, and the skip is
# UNREACHABLE: the identical manifest that skipped in A must not skip here.
check "C: arrangement applied — the sibling is genuinely absent (workspace)" \
      "absent" "$([[ ! -e "$ws/insight-sib" ]] && echo absent || echo PRESENT)"
ws_out="$(python3 -B "$BI" build --workspace "$ws" --repo "$ws/repoA" \
          --build-key probe --profile probe --build-type Release 2>&1)"; ws_rc=$?
check "C: workspace + absent sibling FAILS (exit 1)" "1" "$ws_rc"
check "C: the FAIL names the cell" \
      "named" "$(grep -qF "cell probe_cell" <<< "$ws_out" && echo named || echo "unnamed: $ws_out")"
check "C: the FAIL names the absent sibling" \
      "named" "$(grep -qF "$ws/insight-sib" <<< "$ws_out" && echo named || echo "unnamed: $ws_out")"
check "C: the skip is UNREACHABLE in the workspace shape (needle absent; A proved it real)" \
      "absent" "$(grep -qF "$skip_needle" <<< "$ws_out" && echo "LEAKED: $ws_out" || echo absent)"

# (D) sibling PRESENT -> the cell builds in EITHER shape, no skip line: the machinery
# must not have widened into the live path. conan/cmake are stubbed (this suite's floor
# is no-network/no-build); the stub still produces the linked artifact the tool demands,
# so the assertion reaches the "linked:" proof, not merely a zero exit.
stub_bin="$inv_tmp/bin"
mkdir -p "$stub_bin"
cat > "$stub_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-of" ]] && out="$a"; prev="$a"; done
[[ -n "$out" ]] && mkdir -p "$out" && : > "$out/conan_toolchain.cmake"
exit 0
STUB
cat > "$stub_bin/cmake" <<'STUB'
#!/usr/bin/env bash
# Record the argv when asked. Without this the suite can prove the cell BUILDS and cannot prove
# WHAT IT WAS CONFIGURED AS — which is the whole of the build-type question (`N120`).
[[ -n "${MALF_STUB_CMAKE_LOG:-}" ]] && printf '%s\n' "$*" >> "$MALF_STUB_CMAKE_LOG"
build=""; target=""; prev=""
for a in "$@"; do
    case "$prev" in
        -B|--build) build="$a" ;;
        --target)   target="$a" ;;
    esac
    prev="$a"
done
if [[ -n "$build" && -n "$target" ]]; then
    mkdir -p "$build" && printf '#!/bin/sh\n' > "$build/$target" && chmod +x "$build/$target"
fi
exit 0
STUB
chmod +x "$stub_bin/conan" "$stub_bin/cmake"
check "D: stub toolchain applied (conan resolves to the stub, not the real one)" \
      "$stub_bin/conan" "$(PATH="$stub_bin:$PATH" command -v conan)"

mkdir -p "$ws/insight-sib" "$solo/insight-sib"
ws_sat_out="$(PATH="$stub_bin:$PATH" MALF_STUB_CMAKE_LOG="$inv_tmp/cmake_argv.log" \
              python3 -B "$BI" build --workspace "$ws" \
              --repo "$ws/repoA" --build-key probe --profile probe --build-type Debug 2>&1)"; ws_sat_rc=$?
check "D: workspace shape + sibling present -> the cell configures and links (exit 0)" \
      "rc=0 linked" \
      "rc=$ws_sat_rc $(grep -qF "linked:" <<< "$ws_sat_out" && echo linked || echo "GOT: $ws_sat_out")"
check "D: no skip line when the path is satisfiable (workspace shape)" \
      "absent" "$(grep -qF "$skip_needle" <<< "$ws_sat_out" && echo "LEAKED: $ws_sat_out" || echo absent)"

# THE BUILD TYPE REACHED THE CONFIGURE, and the arm is deliberately run at Debug — a cell whose
# cmake line was checked for `Release` would pass against the LITERAL this parameter replaced
# (build_inventory.py hardcoded `-DCMAKE_BUILD_TYPE=Release` until 2026-09-02) and prove nothing.
# Both directions are asserted: the requested type is present AND the old literal is absent.
inv_cmake_argv="$(cat "$inv_tmp/cmake_argv.log" 2>/dev/null || echo "NO LOG")"
check "D: the cell is CONFIGURED at the build type it was handed, not at a literal" \
      "Debug-present Release-absent" \
      "$(grep -qF -- "-DCMAKE_BUILD_TYPE=Debug" <<< "$inv_cmake_argv" && echo Debug-present || echo "DEBUG-ABSENT: $inv_cmake_argv") \
$(grep -qF -- "-DCMAKE_BUILD_TYPE=Release" <<< "$inv_cmake_argv" && echo "RELEASE-LEAKED: $inv_cmake_argv" || echo Release-absent)"
check "D: build mode REFUSES with no --build-type (no default — a default is a second declaration)" \
      "2" "$(PATH="$stub_bin:$PATH" python3 -B "$BI" build --workspace "$ws" --repo "$ws/repoA" \
             --build-key probe --profile probe >/dev/null 2>&1; echo $?)"
solo_sat_out="$(PATH="$stub_bin:$PATH" python3 -B "$BI" build --workspace "$solo" \
              --repo "$solo" --build-key probe --profile probe --build-type Release 2>&1)"; solo_sat_rc=$?
check "D: single-repo shape + sibling STAGED -> the cell builds, no skip (the D6 staging clause)" \
      "rc=0 linked no-skip" \
      "rc=$solo_sat_rc $(grep -qF "linked:" <<< "$solo_sat_out" && echo linked || echo "GOT: $solo_sat_out") $(grep -qF "$skip_needle" <<< "$solo_sat_out" && echo "LEAKED" || echo no-skip)"

rm -rf "$inv_tmp"

echo "[7i] cmd_bump — every hygiene step runs, and the toolchain never calls the orchestrator"

# Post-bump, the FULL pin-coherence verification is structurally RED until the lockfile is
# re-derived: INV-14 compares conan.lock's first-party pins against the recipes the bump just
# moved. Measured 2026-08-15 (the 1.9.4 bump): with the verification mid-chain, cmd_bump exited
# at that check and STRANDED the editable re-sync, the stale prune and the SBOM — the caches
# stayed one version behind and the next `malf lock --update` refused 19 roots. That check is no
# longer malf's: DN-108.D1, crossing 1 — `malf bump` rewrites the axes it owns and STOPS, and
# `./pharos bump X.Y.Z` runs the verification last. The contract this section pins: every hygiene
# step runs, in order, with the plain (behaviour-neutral, first-party-only) lock chained where the
# lock doc prescribes it — and NO call reaches the orchestrator. The python3 stub still carries
# INV-14's semantics, so a verification call put back mid-chain shows as `verify-RED` and one put
# back last shows as `verify`: either reds this check.
bump_tmp="$(mktemp -d)"
mkdir -p "$bump_tmp/ws/scripts"
: > "$bump_tmp/ws/scripts/version_line.py"   # existence-checked by cmd_bump; python3 is stubbed
: > "$bump_tmp/ws/pharos"                    # the check's one spelling, existence-checked too
# Extract the function under test from malf itself, so this tests the SHIPPED code, not a copy.
bump_fn="$(sed -n '/^cmd_bump() {/,/^}/p' "$MALF_BIN")"
cat > "$bump_tmp/probe.sh" <<PROBE
#!/usr/bin/env bash
set -uo pipefail
T="\$1"
MALF_WORKSPACE_ROOT="\$T/ws"
log() { printf '%s ' "\$1" >> "\$T/order"; }
# The collaborators, stubbed to record order — and python3 carries INV-14's SEMANTICS:
# the verification is red until the lock re-derive has run. A stub that always greens
# would let the broken ordering pass, which is the gate-lying rule this exists to obey.
python3() {
    if [[ "\${2:-}" == "bump" ]]; then log rewrite; return 0; fi
    if [[ -f "\$T/lock-ran" ]]; then log verify; return 0; fi
    log verify-RED; return 1
}
_malf_editables_sync() { log sync; }
cmd_lock()             { log lock; : > "\$T/lock-ran"; }
_malf_clean_stale()    { log clean; }
cmd_sbom()             { log sbom; }
echo() { :; }   # silence the banners; the order file is the observable
$bump_fn
cmd_bump 1.2.3
command echo "rc=\$? order=\$(cat "\$T/order" 2>/dev/null)"
PROBE
chmod +x "$bump_tmp/probe.sh"
check "bump chain — rc=0, every hygiene step in order, and no call to the orchestrator" \
      "rc=0 order=rewrite sync lock clean " \
      "$("$bump_tmp/probe.sh" "$bump_tmp")"
rm -rf "$bump_tmp"

echo "[7k] malf_graph deps — the BUILD closure, not the LINK closure"

# malf configures every workspace dependency as the TOP-LEVEL project of its own cmake preset, so
# that dependency's PROJECT_IS_TOP_LEVEL test/bench subtrees turn ON and its own test_requires
# become resolution requirements of THIS run — while conan's `test` trait never propagates them to
# the target. Emitting what the target LINKS therefore left such a package unregistered, and
# `malf build insight-twin/core` died inside `conan install logcraft/core` with "Package
# 'coderoast_ipc_consumer/1.10.3' not resolved" — a package the target's own recipe never mentions.
# Silent at a desk whose editable registry earlier work had already populated; fatal on a fresh one.
#
# The fixture is SYNTHETIC on purpose. The real workspace exposed exactly ONE target->dependency
# pair of this shape (17 others carried the needed package through an unrelated ordinary `require`),
# so an arm keyed on the real graph would go vacuous the next time a recipe moves an edge.
graph_tmp="$(mktemp -d)"   # cleaned inline below (a second `trap ... EXIT` would REPLACE [7d]'s)
GRAPH_PY="$MALF_ROOT/malf_graph.py"

# ONE recipe writer, so every arm below reads the same four-recipe graph:
#   probe_target --requires--> probe_dep --test_requires--> probe_testonly --test_requires--> probe_deep
#                                        --test_requires--> gtest/1.17.0 (third-party, must NOT appear)
write_probe_recipe() {   # <subdir> <name> <requires…> | <subdir> <name> "" <test_requires…>
    local d="$graph_tmp/ws/$1" n="$2" r="$3" t="${4:-}" x
    mkdir -p "$d"
    {
        printf 'from conan import ConanFile\n\n\nclass Probe(ConanFile):\n'
        printf '    name = "%s"\n    version = "1.0"\n' "$n"
        if [[ -n "$r" ]]; then
            printf '    requires = ['
            for x in $r; do printf '"%s", ' "$x"; done
            printf ']\n'
        fi
        if [[ -n "$t" ]]; then
            printf '    test_requires = ['
            for x in $t; do printf '"%s", ' "$x"; done
            printf ']\n'
        fi
    } > "$d/conanfile.py"
    : > "$d/CMakeLists.txt"
}

write_probe_recipe target   probe_target   "probe_dep/1.0" ""
write_probe_recipe dep      probe_dep      ""              "probe_testonly/1.0 gtest/1.17.0"
write_probe_recipe testonly probe_testonly ""              "probe_deep/1.0"
write_probe_recipe deep     probe_deep     ""              ""

graph_refs() {   # <malf_graph.py path> -> the emitted refs, space-separated, IN ORDER
    python3 "$1" deps "$graph_tmp/ws" "$graph_tmp/ws/target" 2>&1 | cut -f1 | tr '\n' ' ' | sed 's/ $//'
}

# (a) the closure reaches a DEPENDENCY's test_requires and follows them TRANSITIVELY, in
# dependency-first order — probe_deep must be built before probe_testonly, which must be built
# before the probe_dep whose tests link it.
check "deps — a dependency's first-party test_requires enter the closure, transitively and in order" \
      "probe_deep/1.0 probe_testonly/1.0 probe_dep/1.0" \
      "$(graph_refs "$GRAPH_PY")"

# (b) the widening stays FIRST-PARTY: a third-party test_requires is conan's to resolve and must
# never be emitted as a workspace editable. Without this, (a) could pass by emitting everything.
check "deps — a third-party test_requires (gtest) is NOT emitted as a workspace member" \
      "absent" \
      "$(grep -q 'gtest' <<< "$(graph_refs "$GRAPH_PY")" && echo "LEAKED: $(graph_refs "$GRAPH_PY")" || echo absent)"

# (c) ANTI-VACUITY. Restore the pre-fix walk (test_requires followed from the ROOT only) in a copy
# and re-run the identical probe: it must lose both extra members. If this ever reports the full
# closure the probe has stopped being able to detect the regression and (a) proves nothing.
# The mutation asserts its own arity first — a sed that silently matched nothing would green here.
mutant="$graph_tmp/malf_graph_linkclosure.py"
mutation_count="$(python3 - "$GRAPH_PY" "$mutant" <<'PY'
import sys
src = open(sys.argv[1]).read()
old = '            deps = recipe["requires"] + recipe["test_requires"]\n'
new = ('            deps = list(recipe["requires"])\n'
       '            if ref == target_ref:\n'
       '                deps += recipe["test_requires"]\n')
print(src.count(old))
open(sys.argv[2], "w").write(src.replace(old, new))
PY
)"
check "deps — the anti-vacuity mutation applied to exactly one site (arming proof)" \
      "1" "$mutation_count"
check "deps — the LINK-closure walk DROPS them (proves the arm above can fail)" \
      "probe_dep/1.0" \
      "$(graph_refs "$mutant")"

# (d) the retired third argument is GONE, not merely ignored. It was dormant plumbing whose comment
# described a caller that never existed, and a silently-accepted extra arg would let it grow back.
python3 "$GRAPH_PY" deps "$graph_tmp/ws" "$graph_tmp/ws/target" 1 >/dev/null 2>&1
check "deps — the retired include_test_requires argument is REFUSED (exit 2), not ignored" \
      "2" "$?"

rm -rf "$graph_tmp"

echo "[7l] lint exclusion — ONE authoring, and the two legs agree on a tree where they did not"

# `malf lint` selects its subject two ways: --all-files walks the tree and PRUNES with
# `find -name <NAME> -prune`, the default leg filters `git diff --name-only` paths. Both must
# apply the SAME policy (LSRC-1), and until 2026-08-31 the second one restated it as
# `*/NAME/*` globs — which had already drifted, not merely risked drifting. A leading `*/`
# demands a component ahead of the name and `git diff` emits REPO-RELATIVE paths, so an excluded
# directory at a repo ROOT slipped through: 41 tracked TUs were in that shape.
#
# THE ARM IS NOT "THE TWO AGREE" EVALUATED ONCE. Both legs now derive from one variable, so an
# agreement assertion alone could never fail. It is driven on a fixture built to make them
# disagree — root-level AND nested instances of every excluded name — and the mutation below
# restores the historical filter and requires the disagreement back, naming its exact residue.
lx_tmp="$(mktemp -d)"   # cleaned inline below (a second `trap ... EXIT` would REPLACE [7d]'s)
lx_files=(
    tests/root_test.cpp                    # root-level: the four the old globs could not see
    benchmarks/root_bench.cpp
    test_package/root_pkg.cpp
    technical_docs/root_doc.cpp
    core/tests/nested_test.cpp             # nested: the shape both legs always agreed on
    core/benchmarks/nested_bench.cpp
    core/test_package/nested_pkg.cpp
    build-gcc16-release/probe.cpp          # hazard baseline, keyed build dir (MALF_SOURCE_EXCLUDE_DIRS)
    core/src/engine.cpp                    # product
    core/src/build-helper.cpp              # product whose NAME matches `build-*` — the glob trap
    semantic/test_frameworks/src/vocab.cpp # product whose DIRECTORY carries "test" — must stay linted
)
for lx_f in "${lx_files[@]}"; do mkdir -p "$lx_tmp/$(dirname "$lx_f")"; : > "$lx_tmp/$lx_f"; done

mapfile -d '' -t lx_prune < <(_malf_prune_args $MALF_LINT_EXCLUDE_EXTRA)
lx_walk() {   # leg A — the real --all-files prune, relative and sorted
    find "$lx_tmp" \( -type d \( "${lx_prune[@]}" \) -prune \) -o \( -type f -name '*.cpp' -print \) \
        | sed "s|^$lx_tmp/||" | sort | tr '\n' ' '
}
lx_incremental() {   # leg B — the real per-path predicate over the same subject
    local f out=""
    for f in $(printf '%s\n' "${lx_files[@]}" | sort); do
        _malf_lint_path_excluded "$f" || out+="$f "
    done
    printf '%s' "$out"
}
lx_incremental_historical() {   # the pre-2026-08-31 authoring, restated here as the MUTANT
    local f out=""
    for f in $(printf '%s\n' "${lx_files[@]}" | sort); do
        [[ "$f" != */test_package/* ]] && [[ "$f" != */technical_docs/* ]] \
            && [[ "$f" != */tests/* ]] && [[ "$f" != */benchmarks/* ]] && out+="$f "
    done
    printf '%s' "$out"
}

lx_expected="core/src/build-helper.cpp core/src/engine.cpp semantic/test_frameworks/src/vocab.cpp "
check "lint exclusion — the --all-files walk keeps exactly the product TUs" \
      "$lx_expected" "$(lx_walk)"
check "lint exclusion — the incremental leg keeps the SAME set (one authoring, two shapes)" \
      "$(lx_walk)" "$(lx_incremental)"
# Anti-vacuity: the historical filter must LOSE on this fixture, or the arm above proves nothing.
check "lint exclusion — the historical globs disagree (proves the arm above can fail)" \
      "benchmarks/root_bench.cpp build-gcc16-release/probe.cpp core/src/build-helper.cpp core/src/engine.cpp semantic/test_frameworks/src/vocab.cpp technical_docs/root_doc.cpp test_package/root_pkg.cpp tests/root_test.cpp " \
      "$(lx_incremental_historical)"
# The named not-a-leak: a PRODUCT directory carrying "test" in its name stays inside the surface.
check "lint exclusion — semantic/test_frameworks/ is product code and stays linted" \
      "kept kept" \
      "$(_malf_lint_path_excluded semantic/test_frameworks/src/vocab.cpp && echo excluded || echo kept) \
$(grep -q 'semantic/test_frameworks/src/vocab.cpp' <<< "$(lx_walk)" && echo kept || echo excluded)"

# The --header-filter is the third consumer of the same list. It must DERIVE, not hold a copy:
# changing the variable must change the output, which a frozen string could not do.
check "lint exclusion — the header filter derives from the variable, position-free, regex-escaped" \
      '(.*/)?alpha/|(.*/)?be\.ta/' \
      "$(MALF_LINT_EXCLUDE_EXTRA='alpha be.ta' _malf_lint_header_filter)"

# llvm::Regex is POSIX ERE, and a pattern it cannot compile matches NOTHING, silently: that is how
# every first-party header went unlinted until 2026-09-10. This pins the SHAPE — no `(?` group in
# either filter flag — and the behaviour was measured on a fixture when the flags were split.
check "lint filters — no (? group reaches --header-filter or --exclude-header-filter" \
      "0" \
      "$(grep -cE 'header-filter=.*\(\?' "$MALF_BIN")"

rm -rf "$lx_tmp"

echo "[7m] lint residue detector — the name list cannot see a spelling it does not carry"

# MALF_LINT_EXCLUDE_EXTRA is a list of SPELLINGS and its blindness is silent: a bench directory
# spelled something else is simply linted, which reads as coverage. _malf_lint_assert_no_test_tu
# is the name-blind arm. Fixture uses `perf/` — a spelling the list does not carry.
dt_tmp="$(mktemp -d)"
mkdir -p "$dt_tmp/perf" "$dt_tmp/src"
printf '#include <benchmark/benchmark.h>\nint main(){}\n'  > "$dt_tmp/perf/bench_hot.cpp"
printf '#include <gtest/gtest.h>\nTEST(A,B){}\n'           > "$dt_tmp/perf/unit.cpp"
printf 'module;\nimport logcraft.core.test;\n'             > "$dt_tmp/perf/agg.cppm"
printf '#include <string>\nint f(){return 0;}\n'           > "$dt_tmp/src/engine.cpp"
printf '// a comment mentioning benchmark/benchmark.h and gtest/gtest.h\n' > "$dt_tmp/src/prose.cpp"

CWD="$dt_tmp" _malf_lint_assert_no_test_tu "$dt_tmp/src/engine.cpp" "$dt_tmp/src/prose.cpp" >/dev/null 2>&1
check "detector — product TUs pass, and a mere MENTION of a framework header is not a dependency" \
      "0" "$?"
dt_out="$(CWD="$dt_tmp" _malf_lint_assert_no_test_tu "$dt_tmp/perf/bench_hot.cpp" \
            "$dt_tmp/perf/unit.cpp" "$dt_tmp/perf/agg.cppm" "$dt_tmp/src/engine.cpp" 2>&1)"
check "detector — refuses the run (exit 1) when a test/bench TU is inside the surface" \
      "1" "$?"
check "detector — names every offender and no product TU" \
      "perf/agg.cppm perf/bench_hot.cpp perf/unit.cpp" \
      "$(grep -oE 'perf/[a-z_]+\.(cpp|cppm)|src/engine\.cpp' <<< "$dt_out" | sort -u | tr '\n' ' ' | sed 's/ $//')"

# ANTI-VACUITY. Strip the benchmark alternative from the pattern set in a COPY of malf and
# re-run the identical probe in a subshell: the bench TU must stop being named. Without this,
# the arm above could be passing on the gtest alternative alone. The mutation asserts its arity.
dt_mutant="$dt_tmp/malf_no_bench_pattern"
dt_mut_count="$(python3 - "$MALF_BIN" "$dt_mutant" <<'PY'
import sys
src = open(sys.argv[1]).read()
old = "gtest/gtest\\.h|gmock/gmock\\.h|benchmark/benchmark\\.h"
new = "gtest/gtest\\.h|gmock/gmock\\.h"
print(src.count(old))
open(sys.argv[2], "w").write(src.replace(old, new))
PY
)"
check "detector — the anti-vacuity mutation applied to exactly one site (arming proof)" \
      "1" "$dt_mut_count"
check "detector — without the benchmark pattern the bench TU is MISSED (proves it can fail)" \
      "unit.cpp agg.cppm" \
      "$(bash -c 'MALF_SOURCE_ONLY=1 source "$1"; set +e
                  CWD="$2" _malf_lint_assert_no_test_tu "$2/perf/bench_hot.cpp" "$2/perf/unit.cpp" "$2/perf/agg.cppm" 2>&1 \
                    | grep -oE "[a-z_]+\.(cpp|cppm)" | tr "\n" " " | sed "s/ $//"' _ "$dt_mutant" "$dt_tmp")"

# AN UNREADABLE TU IS A REFUSAL, NEVER "NO FRAMEWORK". grep exits 2 on it; the scan used to read
# through a process substitution that dropped every status, so the guard passed it. chmod 000 does
# not deny root, so the arm states that rather than passing vacuously.
if [ "$(id -u)" -ne 0 ]; then
    printf 'int g(){return 0;}\n' > "$dt_tmp/src/locked.cpp"; chmod 000 "$dt_tmp/src/locked.cpp"
    dt_out="$(CWD="$dt_tmp" _malf_lint_assert_no_test_tu "$dt_tmp/src/engine.cpp" "$dt_tmp/src/locked.cpp" 2>&1)"
    dt_rc=$?
    chmod 644 "$dt_tmp/src/locked.cpp"
    check "detector — an unreadable TU refuses the run (exit 1), never passes as framework-free" "1" "$dt_rc"
    check "detector — the refusal says the scan could not read a TU" "1" \
          "$(grep -c 'could not read every selected TU' <<< "$dt_out")"
else
    check "detector — the unreadable-TU arm needs a non-root user (chmod 000 does not deny root)" "non-root" "root"
fi

rm -rf "$dt_tmp"

echo "[7n] lint scratch — a corpse and a stall no longer read alike"

# A killed `malf lint` left /tmp/malf_lint.*/ behind with a frozen _progress counter, which is
# byte-identical to what a slow run leaves. The directory now carries its owner's identity.
sc_tmp="$(mktemp -d)"
cat > "$sc_tmp/owner_probe.sh" <<PROBE
#!/usr/bin/env bash
MALF_SOURCE_ONLY=1 source "$MALF_BIN"
set +e; set -uo pipefail
_malf_lint_open_scratch
command sleep 60 &                 # an orphan-to-be: it inherits everything the owner holds
printf '%s %s\n' "\$_MALF_LINT_SCRATCH" "\$!" > "\$1"
wait
PROBE
chmod +x "$sc_tmp/owner_probe.sh"

sc_wait_state() {   # the state file is written after the scratch exists; poll, never sleep blind
    local i
    for i in $(seq 1 500); do [[ -s "$1" ]] && return 0; command sleep 0.01; done
    return 1
}

bash "$sc_tmp/owner_probe.sh" "$sc_tmp/state" & sc_owner=$!
sc_wait_state "$sc_tmp/state"
read -r sc_dir sc_child < "$sc_tmp/state"
_malf_lint_owner_alive "$sc_dir"
check "scratch — a running owner reads ALIVE" "0" "$?"
check "scratch — _owner tells a reader the exact command that answers it" \
      "yes" "$(grep -qF "/proc/$sc_owner/stat | awk '{print \$20}'" "$sc_dir/_owner" && echo yes || echo no)"
check "scratch — _owner records its run's START, field 22 of /proc/<pid>/stat, not a /proc mtime" \
      "pid $sc_owner start $(sed 's/.*) //' "/proc/$sc_owner/stat" | awk '{print $20}')" \
      "$(head -1 "$sc_dir/_owner")"

# SIGKILL: no trap can run, so this is the case the recorded identity exists for. The orphaned
# child is deliberately left RUNNING — an flock-based owner stamp reads ALIVE here, because a
# bash {var} descriptor is not close-on-exec and the lock rides the inherited open file
# description. Assert the orphan really is alive first, or a pass here is unattributable.
kill -9 "$sc_owner"; wait "$sc_owner" 2>/dev/null
check "scratch — the orphaned child is genuinely still running (the probe means something)" \
      "0" "$(kill -0 "$sc_child" 2>/dev/null; echo $?)"
_malf_lint_owner_alive "$sc_dir"
check "scratch — after SIGKILL the corpse reads DEAD, orphaned child notwithstanding" \
      "1" "$?"
kill -9 "$sc_child" 2>/dev/null

# The reaper's three guards, each isolated. Its own live directory must survive its own sweep.
sc_corpse="$(mktemp -d -t malf_lint.XXXXXX)"; printf 'pid 4294967295 start 1\n' > "$sc_corpse/_owner"
touch -d "5 minutes ago" "$sc_corpse"
sc_fresh="$(mktemp -d -t malf_lint.XXXXXX)";  printf 'pid 4294967295 start 1\n' > "$sc_fresh/_owner"
bash "$sc_tmp/owner_probe.sh" "$sc_tmp/state2" & sc_owner2=$!
sc_wait_state "$sc_tmp/state2"
read -r sc_dir2 sc_child2 < "$sc_tmp/state2"
touch -d "5 minutes ago" "$sc_dir2"        # old enough to be swept; alive, so it must not be
_malf_lint_reap_corpses
check "scratch — the reaper removes a dead run's directory" \
      "gone" "$([[ -d "$sc_corpse" ]] && echo survives || echo gone)"
check "scratch — it spares a directory younger than the mtime floor (the mktemp/write window)" \
      "survives" "$([[ -d "$sc_fresh" ]] && echo survives || echo gone)"
check "scratch — it spares a LIVE run whose mtime is old (a quiet run is not a corpse)" \
      "survives" "$([[ -d "$sc_dir2" ]] && echo survives || echo gone)"
kill -9 "$sc_owner2" "$sc_child2" 2>/dev/null; wait "$sc_owner2" 2>/dev/null
# A CATCHABLE death must leave nothing at all — the other half, which the identity never sees.
bash "$sc_tmp/owner_probe.sh" "$sc_tmp/state3" & sc_owner3=$!
sc_wait_state "$sc_tmp/state3"
read -r sc_dir3 sc_child3 < "$sc_tmp/state3"
kill -TERM "$sc_owner3"; wait "$sc_owner3" 2>/dev/null
check "scratch — a catchable death (SIGTERM) leaves no directory behind at all" \
      "gone" "$([[ -d "$sc_dir3" ]] && echo survives || echo gone)"
kill -9 "$sc_child3" 2>/dev/null
rm -rf "$sc_tmp" "$sc_corpse" "$sc_fresh" "$sc_dir" "$sc_dir2"

echo '[7o] clean — the bare verb destroys NOTHING, and only the word all is nuclear'

# `malf clean` DEFAULTED TO `all` until 2026-09-02 (Founder: "N114 : Yes, semantic change
# approved"), so one missing word wiped every build tree under the cwd, every cached conan package
# (third-party included — the gcc-16 toolchain has no ConanCenter binary, so the next build is a
# from-source rebuild of the world) and the editable registry. Last verb carrying the N113 class:
# a bare invocation performing a workspace-wide destructive act, with no confirmation and no undo.
#
# The fixture holds one artifact of each thing `all` removes, so a regression is proved by an
# ABSENCE on disk rather than by output text: two build trees under the cwd, a package dir in the
# base conan cache AND one in a keyed subdir (the `*/p` half of the glob — a lane wiping only the
# base would otherwise pass), and the editable registry file. Everything is under a scratch
# CONAN_HOME and MALF_WORKSPACE_ROOT, so a regressed verb reaches no real cache.
cl_tmp="$(mktemp -d)"
cl_seed() {   # rebuild the full fixture — each arm starts from the same known-populated state
    rm -rf "$cl_tmp"/pkg "$cl_tmp"/home
    mkdir -p "$cl_tmp/pkg/build-probe" "$cl_tmp/pkg/build" "$cl_tmp/home/p/pkgdir" "$cl_tmp/home/keyed/p/pkgdir"
    cat > "$cl_tmp/pkg/conanfile.py" <<'PYR'
from conan import ConanFile
class Probe(ConanFile):
    name = "n114_probe"
    version = "0.0.1"
PYR
    touch "$cl_tmp/pkg/build-probe/artifact.o" "$cl_tmp/pkg/build/artifact.o" \
          "$cl_tmp/home/editable_packages.json" "$cl_tmp/home/settings.yml" "$cl_tmp/home/keyed/settings.yml"
}
cl_run() {   # <args...> — clean, from the fixture package, sandboxed and bounded
    (cd "$cl_tmp/pkg" && MALF_WORKSPACE_ROOT="$cl_tmp" CONAN_HOME="$cl_tmp/home" MALF_SKIP_INVENTORY=1 \
        timeout 60 bash "$MALF_BIN" clean "$@" 2>&1)
}
# One string naming every fixture artifact still on disk: the whole verdict in one comparable value.
cl_state() {
    local p out=""
    for p in pkg/build-probe pkg/build home/p home/keyed/p home/editable_packages.json; do
        [[ -e "$cl_tmp/$p" ]] && out+="$p "
    done
    echo "${out:-EMPTY}"
}
cl_all="pkg/build-probe pkg/build home/p home/keyed/p home/editable_packages.json "

# 1. THE REGRESSION ARM. If a bare `clean` ever destroys again, this goes red on the survivors,
#    not on a message — the message could be reworded, the deletion cannot be faked.
cl_seed
cl_bare="$(cl_run)"; cl_bare_rc=$?
check "clean (bare) refuses — rc 1" "1" "$cl_bare_rc"
check "clean (bare) removes NOTHING: build trees, both conan caches, the editable registry" \
      "$cl_all" "$(cl_state)"
check "clean (bare) prints no run banner" \
      "none" "$([[ "$cl_bare" != *"=== malf"* ]] && echo none || echo "GOT: $(head -c 200 <<< "$cl_bare")")"

#    The refusal must name the nuclear form as the operator would TYPE it. An instrument reports
#    state, and its message is what the operator acts on: a refusal that does not spell the next
#    command has moved the work, not removed it. Compared as a captured string — a `| grep -q`
#    under this suite's pipefail closes the pipe mid-usage and reads the writer's SIGPIPE as a miss.
check "clean (bare) spells the nuclear form the operator must type" \
      "named" "$([[ "$cl_bare" == *"malf clean all"* ]] && echo named || echo "GOT: $(head -c 300 <<< "$cl_bare")")"
check "clean (bare) says nothing was removed" \
      "said" "$([[ "$cl_bare" == *"nothing was removed"* ]] && echo said || echo "GOT: $(head -c 300 <<< "$cl_bare")")"

# 2. THE CAPABILITY ARM. The nuclear form must still be nuclear — a refusal that also broke `all`
#    would pass arm 1 and leave the workspace with no way to reset itself.
cl_seed
cl_all_out="$(cl_run all)"; cl_all_rc=$?
check "clean all succeeds — rc 0" "0" "$cl_all_rc"
check "clean all still wipes EVERYTHING (both caches included)" "EMPTY" "$(cl_state)"

# 3. Each named target removes ITS OWN artifact and leaves the others — the reason `all` is a word
#    and not the default: the surgical forms are the common ones.
cl_seed; cl_run build      >/dev/null
check "clean build removes build trees only" "home/p home/keyed/p home/editable_packages.json " "$(cl_state)"
cl_seed; cl_run conan      >/dev/null
check "clean conan removes cached packages only (base AND keyed)" "pkg/build-probe pkg/build home/editable_packages.json " "$(cl_state)"
cl_seed; cl_run editables  >/dev/null
check "clean editables removes the registry only" "pkg/build-probe pkg/build home/p home/keyed/p " "$(cl_state)"

# 4. A second target used to be dropped on the floor — `clean build conan` ran `build` and reported
#    "build done", which reads as a cache wipe that never happened. It is refused, and refusing
#    must not destroy anything either.
cl_seed
cl_two="$(cl_run build conan)"; cl_two_rc=$?
check "clean with two targets refuses rather than silently drop one — rc 1" "1" "$cl_two_rc"
check "clean with two targets removes nothing" "$cl_all" "$(cl_state)"

# 5. An unknown target and an unknown option both refuse WITH the usage block, so the operator
#    reading the terminal is handed the five targets either way.
cl_seed
cl_bad="$(cl_run nonsense)"; cl_bad_rc=$?
check "clean <unknown target> refuses with usage — rc 1" \
      "1 usage" "$cl_bad_rc $([[ "$cl_bad" == *"unknown target 'nonsense'"* && "$cl_bad" == *"usage:"* ]] && echo usage || echo "GOT: $(head -c 200 <<< "$cl_bad")")"
check "clean <unknown target> removes nothing" "$cl_all" "$(cl_state)"
rm -rf "$cl_tmp"

echo "[7p] build type — a tree's configuration is the PROFILE's, and a drifting tree reds BEFORE the compile"

# WHAT THIS PINS (`N120`). Until 2026-09-02 the build type was a per-INVOCATION variable while the
# build tree is a per-PROFILE coordinate: `cmd_build`/`cmd_test`/`cmd_commands` seeded `Debug`,
# `cmd_bench`/`cmd_inventory` seeded `Release`, and `--debug`/`--release` moved it again. The
# default profile never goes through `_malf_apply_profile`, so on the dev default the seed was the
# ONLY writer — and all 23 `build-clang21-libcxx-release` trees carried `CMAKE_BUILD_TYPE=Debug`
# under a profile declaring `Release`. Nothing failed; the trees simply were not the configuration
# their names announce, so every green taken in one was a claim about a leg nobody ran.
#
# THREE ARMS, and they are three different kinds. (1) the DERIVATION: every registry profile
# declares a build_type and `_malf_profile_build_type` returns it, refusing a profile that declares
# none. (2) the GATE: `_malf_assert_tree_matches_profile` compares the CMakeCache the configure
# produced against the profile FILE, and must be seen to RED. (3) the STRUCTURAL arm: no verb may
# write MALF_CONFIG again, and no `--debug`/`--release` arm may come back — read from the source,
# because that is the only form that catches the regression rather than the symptom.

bt_tmp="$(mktemp -d)"

# ── (1) THE DERIVATION ───────────────────────────────────────────────────────────────────────
# The roster is DERIVED from the registry directory, never listed here: a profile added tomorrow
# is covered without anyone remembering this file.
bt_profiles=(); bt_missing=()
for bt_p in "$MALF_ROOT"/profiles/*; do
    [[ -f "$bt_p" ]] || continue
    bt_profiles+=("$(basename "$bt_p")")
    grep -qE '^[[:space:]]*build_type[[:space:]]*=' "$bt_p" || bt_missing+=("$(basename "$bt_p")")
done
check "every registry profile declares a build_type (roster derived, ${#bt_profiles[@]} profiles)" \
      "none-missing" "$([[ ${#bt_missing[@]} -eq 0 ]] && echo none-missing || echo "MISSING: ${bt_missing[*]}")"
check "the profile roster is non-empty (a vacuous sweep would pass the arm above)" \
      "non-empty" "$([[ ${#bt_profiles[@]} -gt 0 ]] && echo non-empty || echo EMPTY)"

# `_malf_profile_build_type` reads the ACTIVE profile, so drive it through _malf_apply_profile —
# the same seam every verb uses. Compared against the file read independently, two ways of asking.
# CONAN_HOME is scoped here and at both sites below for TWO reasons, and the second is correctness,
# not hygiene. (1) `_malf_apply_profile` COPIES the resolved profile into `$CONAN_HOME/profiles/`,
# so an unscoped run writes into the developer's live cache — measured 2026-09-04: the junk probe
# `probe-no-build-type` was sitting in the real base cache, and `conan profile list` reported it.
# (2) Resolution reads `$CONAN_HOME/profiles/` FIRST, so against the live cache both sides of this
# comparison could come from the same stale copy — the "two ways of asking" would be one. A fresh
# empty home forces the registry file, which is the artifact the expectation is read from.
bt_derived_mismatch=()
for bt_name in "${bt_profiles[@]}"; do
    bt_expect="$(sed -nE 's/^[[:space:]]*build_type[[:space:]]*=[[:space:]]*([A-Za-z]+).*/\1/p' \
                 "$MALF_ROOT/profiles/$bt_name" | head -n1)"
    bt_got="$(CONAN_HOME="$bt_tmp/home" bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
                       _malf_apply_profile "$2" >/dev/null 2>&1
                       echo "$MALF_CONFIG"' _ "$MALF_BIN" "$bt_name" 2>/dev/null)"
    [[ "$bt_got" == "$bt_expect" ]] || bt_derived_mismatch+=("$bt_name(got=$bt_got want=$bt_expect)")
done
check "--profile <name> takes the build type FROM that profile, for every registry profile" \
      "all-match" "$([[ ${#bt_derived_mismatch[@]} -eq 0 ]] && echo all-match || echo "MISMATCH: ${bt_derived_mismatch[*]}")"

# THE DEFAULT PATH, which is where N120 lived: no --profile is named, nothing calls
# _malf_apply_profile, and MALF_CONFIG must still be the default profile's declared type.
bt_default_expect="$(sed -nE 's/^[[:space:]]*build_type[[:space:]]*=[[:space:]]*([A-Za-z]+).*/\1/p' \
                     "$MALF_ROOT/profiles/linux-clang21-libcxx-release" | head -n1)"
bt_default_got="$(bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; echo "$MALF_CONFIG"' \
                  _ "$MALF_BIN" 2>/dev/null)"
check "the DEFAULT path (no --profile) carries the default profile's build type, not a verb's seed" \
      "$bt_default_expect" "$bt_default_got"

# A profile declaring no build_type is FATAL, not a silent carry-over of the previous value.
mkdir -p "$bt_tmp/profiles"
printf '[settings]\narch=x86_64\ncompiler=gcc\n' > "$bt_tmp/profiles/probe-no-build-type"
bt_nb_rc=0
bt_nb_out="$(MALF_DIR="$bt_tmp" CONAN_HOME="$bt_tmp/home" bash -c '
    MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
    MALF_DIR="$2"; _malf_apply_profile probe-no-build-type' _ "$MALF_BIN" "$bt_tmp" 2>&1)" || bt_nb_rc=$?
check "a profile declaring no build_type is FATAL (exit 1), never a carried-over default" \
      "1 named" \
      "$bt_nb_rc $([[ "$bt_nb_out" == *"declares no [settings] build_type"* ]] && echo named || echo "GOT: $(head -c 200 <<< "$bt_nb_out")")"

# ── (2) THE GATE ─────────────────────────────────────────────────────────────────────────────
# Drive _malf_assert_tree_matches_profile against three hand-written caches. The subject is the
# CMakeCache on disk versus the profile file on disk — two independent artifacts, which is what
# makes this more than malf agreeing with its own variable.
bt_gate() {   # $1 = cache body or the literal NONE; echoes "<rc> <output>"
    local dir="$bt_tmp/tree"; rm -rf "$dir"; mkdir -p "$dir"
    [[ "$1" == "NONE" ]] || printf '%s\n' "$1" > "$dir/CMakeCache.txt"
    local out rc=0
    out="$(CONAN_HOME="$bt_tmp/home" bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
                    _malf_apply_profile linux-gcc16-release >/dev/null 2>&1
                    _malf_assert_tree_matches_profile "$2"' _ "$MALF_BIN" "$dir" 2>&1)" || rc=$?
    printf '%s\n%s' "$rc" "$out"
}
bt_ok="$(bt_gate 'CMAKE_BUILD_TYPE:STRING=Release')"
check "the gate PASSES when the cache matches the profile (linux-gcc16-release declares Release)" \
      "0" "$(head -n1 <<< "$bt_ok")"
bt_bad="$(bt_gate 'CMAKE_BUILD_TYPE:STRING=Debug')"
check "the gate REDS (exit 1) on a Debug cache under a Release profile — the exact N120 shape" \
      "1" "$(head -n1 <<< "$bt_bad")"
check "the red NAMES both values and the profile, so the reader needs no second command" \
      "complete" \
      "$([[ "$bt_bad" == *"CMAKE_BUILD_TYPE=Debug"* && "$bt_bad" == *"linux-gcc16-release"* \
            && "$bt_bad" == *"build_type=Release"* ]] && echo complete || echo "INCOMPLETE: $bt_bad")"
bt_empty="$(bt_gate 'CMAKE_BUILD_TYPE:STRING=')"
check "an EMPTY cache value reds too (a multi-config tree is not a pass here)" \
      "1" "$(head -n1 <<< "$bt_empty")"
bt_none="$(bt_gate NONE)"
check "an ABSENT CMakeCache.txt reds rather than passing vacuously" \
      "1 named" \
      "$(head -n1 <<< "$bt_none") $([[ "$bt_none" == *"no CMakeCache.txt"* ]] && echo named || echo "GOT: $bt_none")"

# ── (3) THE STRUCTURAL ARM ───────────────────────────────────────────────────────────────────
# Read from the source, because the symptom (a tree with the wrong type) is downstream of the
# mechanism (a second writer of MALF_CONFIG), and only the mechanism can be pinned in a no-build
# suite. Comments are stripped first so this file's own prose about the flags cannot satisfy it.
bt_src="$(sed 's/#.*$//' "$MALF_BIN")"
bt_writers="$(grep -cE '^[[:space:]]*MALF_CONFIG=' <<< "$bt_src" || true)"
check "MALF_CONFIG has exactly TWO writers, both _malf_profile_build_type (load seed + --profile)" \
      "2" "$bt_writers"
bt_writer_lines="$(grep -E '^[[:space:]]*MALF_CONFIG=' <<< "$bt_src" | grep -vc '_malf_profile_build_type' || true)"
check "neither writer is a literal — a verb-level seed is what N120 was" "0" "$bt_writer_lines"
bt_flags="$(grep -cE '^[[:space:]]*--(debug|release)\)' <<< "$bt_src" || true)"
check "no verb carries a --debug/--release case arm (the flags that moved the type off the profile)" \
      "0" "$bt_flags"
# The gate must be WIRED, not merely defined: an assertion nothing calls is the shape this whole
# section exists to refuse.
bt_calls="$(grep -cE '_malf_assert_tree_matches_profile[[:space:]]+"' <<< "$bt_src" || true)"
check "the tree/profile assertion is actually CALLED (a defined-but-unwired gate checks nothing)" \
      "1" "$bt_calls"
rm -rf "$bt_tmp"


echo "[7q] every format run STATES ITS OWN SCOPE — mode, population, misformatted, skipped"

# THE SAME DEFECT [7j5] closed for lint, one verb away. Measured 2026-09-03: `malf format --check`
# from the workspace root inspected 887 files and printed exactly ONE line, the banner. Its zero
# case was already named ("no source files found … nothing to do") where lint's was not, so this
# verb was the less bad of the two — but a GREEN still stated no population, and "malf format
# --check, exit 0" is one sentence about 887 files, or 3, or none. The counts here are NOT lint's
# three: there is no compile database and no lintability filter on this path, so every checked
# file reaches the tool and the only coverage gap is the oversized SKIP. The arms below pin the
# identity `selected = checked + skipped`, the zero case, the two modes, and the one property a
# reader would otherwise have to trust — that a file with many violations is ONE misformatted file.
#
# These use the REAL clang-format, as [7g] arm 3 already does, but assert nothing that depends on
# the STYLE: population counts, the mode string, an all-garbage file set, and an idempotence round
# trip are the same answer under any configuration.

fq_tmp="$(mktemp -d)"
fq_sum() { grep -oE 'malf format: SUMMARY .*' <<< "$1" | head -1; }
fq_run() {   # <dir> [env...] — malf format from <dir>, everything after it prefixed as env
    local d="$1"; shift
    (cd "$d" && env "$@" bash "$MALF_BIN" format --check 2>&1)
}

# 1/4 — ZERO POPULATION. A directory with no C++ at all. This is a real, declared state (the
# TypeScript repos rest on it), and it must not read as a verdict about any source.
mkdir -p "$fq_tmp/empty"
fq_zero_out="$(fq_run "$fq_tmp/empty")"; fq_zero_rc=$?
check "STATE 1/4 — a run with NO source says so instead of reading as a clean check, rc 0" \
      "rc=0 declared" \
      "rc=$fq_zero_rc $([[ "$fq_zero_out" == *"selected 0, CHECKED 0 — NOTHING WAS INSPECTED"* \
          && "$fq_zero_out" == *"NOT a clean verdict"* ]] && echo declared || echo "GOT: $fq_zero_out")"
check "the zero-population run never says 'nothing to do' — the phrase that read as a pass" \
      "gone" \
      "$([[ "$fq_zero_out" != *"nothing to do"* ]] && echo gone || echo "GOT: $fq_zero_out")"

# 2/4 — A REAL POPULATION, ALL OF IT MISFORMATTED. Three files of deliberate garbage: no
# configuration formats them, so the expected count is 3 whatever .clang-format says.
mkdir -p "$fq_tmp/bad/sub"
printf 'int  main( ){int   x=1;return   x;}\n' > "$fq_tmp/bad/a.cpp"
printf 'void  g( ){int   y=2;(void)y;}\n'      > "$fq_tmp/bad/b.cpp"
printf 'struct  S{int   a;};\n'                > "$fq_tmp/bad/sub/c.hpp"
fq_bad_out="$(fq_run "$fq_tmp/bad")"; fq_bad_rc=$?
check "STATE 2/4 — three misformatted files are counted as three, and the run is red" \
      "red selected 3, checked 3, 3 misformatted, 0 skipped" \
      "$([[ $fq_bad_rc -ne 0 ]] && echo red || echo "rc=$fq_bad_rc") $(grep -oE 'selected [0-9]+, checked [0-9]+, [0-9]+ misformatted, [0-9]+ skipped' <<< "$fq_bad_out" | head -1)"

# THE COUNT IS FILES, NOT DIAGNOSTICS, and that is the one number a reader cannot re-derive from
# the output without counting by hand. clang-format emits one `error:` per violation, so counting
# lines would report the SEVERITY of one file as the SIZE of the population — "10 misformatted"
# for a single bad line. One garbage file, many violations, one file.
mkdir -p "$fq_tmp/one"
printf 'int  main( ){int   x=1;return   x;}\n' > "$fq_tmp/one/a.cpp"
fq_one_out="$(fq_run "$fq_tmp/one")"
fq_one_diags="$(grep -c 'code should be clang-formatted' <<< "$fq_one_out")"
check "ONE file with many violations is ONE misformatted file (the count is files, not diagnostics)" \
      "1 misformatted / many diagnostics" \
      "$(grep -oE '[0-9]+ misformatted' <<< "$fq_one_out" | head -1) / $( ((fq_one_diags >= 2)) && echo "many diagnostics" || echo "only $fq_one_diags diagnostics — the fixture no longer proves the distinction")"

# 3/4 — THE WRITE MODE. It reports a population too, because in a shared worktree that number is
# how many files this run just made dirty. It must NOT report a misformatted count: clang-format
# -i is silent about what it changed, so any such number would be invented.
fq_write_out="$(cd "$fq_tmp/bad" && bash "$MALF_BIN" format . 2>&1)"; fq_write_rc=$?
check "STATE 3/4 — the write mode states its population, and names the mode as a write" \
      "rc=0 mode=write-paths selected 3, formatted 3, 0 skipped" \
      "rc=$fq_write_rc $(grep -oE 'mode=write-paths' <<< "$fq_write_out" | head -1) $(grep -oE 'selected [0-9]+, formatted [0-9]+, [0-9]+ skipped' <<< "$fq_write_out" | head -1)"
check "the write summary claims NO misformatted count — clang-format -i never reports one" \
      "absent" \
      "$([[ "$(fq_sum "$fq_write_out")" != *misformatted* ]] && echo absent || echo "INVENTED: $(fq_sum "$fq_write_out")")"
# The round trip is what makes the count above a measurement rather than a coincidence: the same
# three files, checked after being written, are zero.
fq_after_out="$(fq_run "$fq_tmp/bad")"; fq_after_rc=$?
check "after the write, the same three files check clean — so the count of 3 was a measurement" \
      "rc=0 selected 3, checked 3, 0 misformatted, 0 skipped" \
      "rc=$fq_after_rc $(grep -oE 'selected [0-9]+, checked [0-9]+, [0-9]+ misformatted, [0-9]+ skipped' <<< "$fq_after_out" | head -1)"

# 4/4 — THE COVERAGE GAP. An oversized file is walked, reported, and NOT formatted. It must appear
# in `selected` and not in `checked`, because `selected = checked + skipped` is the identity that
# lets a reader see the hole on the line itself. MALF_SOURCE_MAX_FILE_KB is lowered rather than a
# multi-megabyte file written: the subject is the accounting, not the size.
# NOT MALF_SOURCE_MAX_FILE_KB=1, and the reason is a `find` trap worth pinning here rather than
# rediscovering: `-size -Nk` rounds a file UP to whole 1K blocks, so a 21-byte file is ONE block
# and `-size -1k` is false for it — at N=1 the whole population is "oversized" and the arm
# measures nothing. 4 KB leaves a real gap between the two files on both sides of the cut.
mkdir -p "$fq_tmp/skip"
printf 'int  h( ){return 0;}\n' > "$fq_tmp/skip/small.cpp"
head -c 8192 /dev/zero | tr '\0' 'x' | sed 's/^/\/\/ /' > "$fq_tmp/skip/big.cpp"
fq_skip_out="$(fq_run "$fq_tmp/skip" MALF_SOURCE_MAX_FILE_KB=4)"
check "STATE 4/4 — a skipped file is SELECTED but not CHECKED (selected = checked + skipped)" \
      "selected 2, checked 1, 1 misformatted, 1 skipped" \
      "$(grep -oE 'selected [0-9]+, checked [0-9]+, [0-9]+ misformatted, [0-9]+ skipped' <<< "$fq_skip_out" | head -1)"
# And when the skip eats the WHOLE population, the zero case must still fire and still carry the
# reason — otherwise it reads as "this tree has no C++", which is a different fact.
rm -f "$fq_tmp/skip/small.cpp"
fq_allskip_out="$(fq_run "$fq_tmp/skip" MALF_SOURCE_MAX_FILE_KB=4)"
check "a population entirely skipped is a zero run that still names the skip" \
      "selected 1, CHECKED 0 — NOTHING WAS INSPECTED, 1 skipped" \
      "$(grep -oE 'selected [0-9]+, CHECKED 0 — NOTHING WAS INSPECTED, [0-9]+ skipped' <<< "$fq_allskip_out" | head -1)"

# THE MODE IS PART OF THE VERDICT. A sweep of $CWD and a named pathspec produce different
# populations from the same directory, and a reader who assumes the wrong one reads the green as
# wider than it is. Both spellings must appear, and they must differ.
fq_sweep_out="$(fq_run "$fq_tmp/one")"
fq_paths_out="$(cd "$fq_tmp/one" && bash "$MALF_BIN" format --check a.cpp 2>&1)"
check "the summary names BOTH axes — check/write and sweep/paths — and the two spellings differ" \
      "check-sweep check-paths" \
      "$(grep -oE 'mode=check-[a-z]+' <<< "$fq_sweep_out" | head -1 | sed 's/mode=//') $(grep -oE 'mode=check-[a-z]+' <<< "$fq_paths_out" | head -1 | sed 's/mode=//')"

# rc != 0 WITH ZERO VIOLATIONS IS A COVERAGE HOLE WEARING A VIOLATION'S EXIT STATUS. The run
# exits 123 either way (xargs: "some invocation exited 1-125"), so the status alone cannot tell a
# formatting failure from clang-format never starting. A 2 MB address-space cap is far below what
# any clang-format needs, so the tool cannot run at all and the count is necessarily zero.
fq_dead_out="$(fq_run "$fq_tmp/one" MALF_SOURCE_MEM_LIMIT_KB=2048)"; fq_dead_rc=$?
check "clang-format that never RAN reds with 0 violations, and the run says that is not formatting" \
      "red 0 misformatted named" \
      "$([[ $fq_dead_rc -ne 0 ]] && echo red || echo "rc=$fq_dead_rc") $(grep -oE '[0-9]+ misformatted' <<< "$fq_dead_out" | head -1) $([[ "$fq_dead_out" == *"ZERO violations counted"* ]] && echo named || echo "UNNAMED: $(fq_sum "$fq_dead_out")")"

rm -rf "$fq_tmp"
echo

echo "[7q2] malf commands STATES ITS OWN SCOPE, and a member left in DEPENDENCY role is FATAL"

# `N146`. A package is configured TWICE in one `malf commands` pass — once as the indexed TARGET
# (`_malf_cmake_feature_args <pkg> ON ON`) and once as a sibling member's DEPENDENCY
# (`_malf_dependency_cmake_args`, both OFF) via _malf_bootstrap_workspace_deps — and BOTH writes
# land in the same `<pkg>/build-<key>` tree, because that key is `(package, profile)` and carries
# no dimension for ROLE. The dependency configure is the later one, so the merge reads a database
# from which the target's test and bench TUs have already been removed.
#
# MEASURED 2026-09-03 at profile clang21-libcxx-release, both repos, against the same command run
# with the repair in place:
#   coderoast-ipc   15 entries where the complete database is  19 —  4 first-party TUs lost, 0 external
#   insight-canon   60 entries where the complete database is 123 — 63 first-party TUs lost, 0 external
# All 67 lost entries were test or bench TUs; both runs printed only "(N entries)" and exited 0.
# That file's one reader is the editor index, which reports a missing TU as an unknown SYMBOL
# rather than as a missing TU, so nothing anywhere said half the database was gone.
#
# NO ARM BELOW NEEDS A TOOLCHAIN, deliberately — this suite runs where none exists and an arm that
# skips there is zero coverage in a green shirt. The role verdict is driven against fixture
# CMakeCache files; the repair is pinned STRUCTURALLY, by line arithmetic against the merge it has
# to precede, because the symptom needs two members and a compiler while the mechanism does not.

cm_tmp="$(mktemp -d)"
mkdir -p "$cm_tmp/target" "$cm_tmp/dep" "$cm_tmp/plain" "$cm_tmp/half"
printf 'CMAKE_BUILD_TYPE:STRING=Release\nFOO_BUILD_TESTS:BOOL=ON\nFOO_BUILD_BENCH:BOOL=ON\n'   > "$cm_tmp/target/CMakeCache.txt"
printf 'CMAKE_BUILD_TYPE:STRING=Release\nFOO_BUILD_TESTS:BOOL=OFF\nFOO_BUILD_BENCH:BOOL=OFF\n' > "$cm_tmp/dep/CMakeCache.txt"
printf 'CMAKE_BUILD_TYPE:STRING=Release\nCMAKE_CXX_COMPILER:FILEPATH=/usr/bin/clang++-21\n'    > "$cm_tmp/plain/CMakeCache.txt"
printf 'CMAKE_BUILD_TYPE:STRING=Release\nFOO_BUILD_TESTS:BOOL=ON\nFOO_BUILD_BENCH:BOOL=OFF\n'  > "$cm_tmp/half/CMakeCache.txt"

# `plain` is the OBVIOUS WRONG FIX, pinned as an arm: a package that declares no test or bench
# option cannot be demoted, so answering anything but `target` there would red every option-less
# package in the workspace while measuring nothing. `absent` is the failed-configure case — a
# member with no CMakeCache contributed NOTHING to the database, which is a truncation too and
# must not read as a pass.
_malf_commands_tree_role "$cm_tmp/target"; cm_role_target="$_MALF_TREE_ROLE"
_malf_commands_tree_role "$cm_tmp/dep";    cm_role_dep="$_MALF_TREE_ROLE"; cm_vars_dep="$_MALF_TREE_ROLE_VARS"
_malf_commands_tree_role "$cm_tmp/plain";  cm_role_plain="$_MALF_TREE_ROLE"
_malf_commands_tree_role "$cm_tmp/absent"; cm_role_absent="$_MALF_TREE_ROLE"

check "tree role: ON/ON, OFF/OFF, no such option at all, and no cache are FOUR distinct answers" \
      "target dependency target no-cache" \
      "$cm_role_target $cm_role_dep $cm_role_plain $cm_role_absent"

check "a demoted tree NAMES the variables that demoted it (the message has to be the repro)" \
      "FOO_BUILD_BENCH FOO_BUILD_TESTS" \
      "$cm_vars_dep"

_malf_commands_tree_role "$cm_tmp/half"
check "one variable OFF is enough to demote, and only the OFF one is named" \
      "dependency FOO_BUILD_BENCH" \
      "$_MALF_TREE_ROLE $_MALF_TREE_ROLE_VARS"

# --- the summary: nested members, and the first-party/external split --------------------------
# Members NEST — insight-eidos is a package AND the parent of insight-eidos/sift — so every entry
# is billed to the LONGEST matching member prefix. Under a shortest-prefix attribution the parent
# absorbs each subpackage's TUs and a member that contributed NOTHING still reports coverage,
# which is precisely the misreading this section exists to make impossible.
cm_root="$cm_tmp/repo"
mkdir -p "$cm_root/sub"
cat > "$cm_tmp/db.json" <<JSON
[{"file": "$cm_root/api/parent.cppm"},
 {"file": "$cm_root/sub/api/child.cppm"},
 {"file": "$cm_root/sub/tests/test_child.cpp"},
 {"file": "$cm_root/sub/benchmarks/bench_child.cpp"},
 {"file": "/usr/lib/llvm-21/share/libc++/v1/std.cppm"}]
JSON
cm_sum="$(_malf_commands_summary "$cm_tmp/db.json" "$cm_root" 2 0 0 3 2 1 "target $cm_root" "target $cm_root/sub" 2>&1)"
cm_parent_line="$(grep -E '^malf commands:   \.[[:space:]]' <<< "$cm_sum" | tr -s ' ')"
cm_child_line="$(grep -E '^malf commands:   sub[[:space:]]' <<< "$cm_sum" | tr -s ' ')"

check "entries are billed to the LONGEST member prefix, so a nested member is not absorbed" \
      "malf commands: . role=target 1 entries, 0 test/bench | malf commands: sub role=target 3 entries, 2 test/bench" \
      "$cm_parent_line | $cm_child_line"

check "the summary splits first-party from external — the half N146 moved while the total held" \
      "entries 5 = first-party 4 (under $cm_root) + external 1" \
      "$(grep -oE 'entries [0-9]+ = first-party [0-9]+ \(under [^)]*\) \+ external [0-9]+' <<< "$cm_sum")"

# THREE terms, because a member can fail to be configured for two reasons that promise different
# things: content-only has no CMakeLists.txt anywhere and contributes nothing, while a BUILDLESS
# member produced no CMakePresets.json and still contributes its test_package's units. Folding the
# second into the first would say "nothing to index" about a member whose only consumer-shaped
# translation unit IS indexed; leaving it out of both — the state until 2026-09-03 — printed an
# identity that did not sum: coderoast-server printed "7 selected = 5 configured + 1 content-only".
cm_id_re='members [0-9]+ selected = [0-9]+ configured \+ [0-9]+ content-only \+ [0-9]+ buildless'
check "the member line states the identity selected = configured + content-only + buildless" \
      "members 2 selected = 2 configured + 0 content-only + 0 buildless" \
      "$(grep -oE "$cm_id_re" <<< "$cm_sum")"

# The buildless term has to be a term and not a constant: fed a non-zero one, the line must print
# it, and the three parts must still sum to the selected count.
cm_sum_buildless="$(_malf_commands_summary "$cm_tmp/db.json" "$cm_root" 3 0 1 1 1 0 "target $cm_root" "target $cm_root/sub" 2>&1)"
check "a buildless member is counted on the member line, and the identity still sums" \
      "members 3 selected = 2 configured + 0 content-only + 1 buildless" \
      "$(grep -oE "$cm_id_re" <<< "$cm_sum_buildless")"

# --- A BUILDLESS MEMBER GETS A ROW, and `configured` is DERIVED from the rows -------------------
# Until 2026-09-05 the rows covered configured members only, so a buildless member's test_package
# units were in the database and in no row: measured that day at profile clang21-libcxx-release,
# coderoast-server printed five rows totalling 168 entries against first-party 170. The row is what
# accounts for one of those two (server-logging/test_package/test_package.cpp); the residual arm
# below is the other.
#
# `configured` on the member line is now COUNTED FROM THE ROWS (every row whose role is not
# `buildless`) rather than taken as `len(rows)`, which would have double-counted the new row and
# made the identity print 3 configured + 1 buildless for a 3-member repo. Fed one buildless row,
# the line must still read 2 configured + 1 buildless.
mkdir -p "$cm_root/hdrlib/test_package"
cat > "$cm_tmp/db_bl.json" <<JSON
[{"file": "$cm_root/api/parent.cppm"},
 {"file": "$cm_root/sub/api/child.cppm"},
 {"file": "$cm_root/hdrlib/test_package/test_package.cpp"},
 {"file": "/usr/lib/llvm-21/share/libc++/v1/std.cppm"}]
JSON
cm_sum_blrow="$(_malf_commands_summary "$cm_tmp/db_bl.json" "$cm_root" 3 0 1 1 1 0 \
                 "target $cm_root" "target $cm_root/sub" "buildless $cm_root/hdrlib" 2>&1)"
# WEAKER THAN IT LOOKS, and labelled for what it is: the summary was ALREADY role-blind when it
# attributed, so this arm is green against the unfixed malf too — verified 2026-09-05, it was the
# one of six new arms that did not red. The defect was in the CALLER, which never handed the
# summary a buildless row, and the arm that reds for it is the caller arm further down. What this
# one still pins is real but narrow: attribution must not start filtering rows by role, which is
# the obvious wrong way to keep the role column honest.
check "the summary attributes a row's entries regardless of its role label (buildless included)" \
      "malf commands: hdrlib role=buildless 1 entries, 1 test/bench" \
      "$(grep -E '^malf commands:   hdrlib[[:space:]]' <<< "$cm_sum_blrow" | tr -s ' ')"

check "the member line's configured term is derived from the rows, so a buildless row is not double-counted" \
      "members 3 selected = 2 configured + 0 content-only + 1 buildless" \
      "$(grep -oE "$cm_id_re" <<< "$cm_sum_blrow")"

# --- THE RESIDUAL: first-party entries under NO member -----------------------------------------
# The rows summing to first-party has to be CHECKABLE on the line, not assumed, and on 2026-09-05
# it did not hold for a reason no row can fix: coderoast-server's
# infra/tests_support/local_container.cpp is first-party, is compiled from infra/redis's build dir,
# and sits under none of the seven members — there is no member to give a row to. That is a
# legitimate shape (shared test-support source consumed by a sibling member's tests), so it is a
# NUMBER and never a fatal. Without the term the entries line reads as complete while the rows are
# short, which is the truncated-database-that-reads-as-complete failure this command exists to
# refuse.
# THE FIXTURE'S ROOT IS NOT A MEMBER, and that is the whole point: coderoast-server has no
# conanfile.py at its root, so no row's prefix covers infra/tests_support/. A fixture whose root IS
# a row (the one above) can never produce a residual — every first-party path is under it — so it
# would green this arm while measuring nothing.
cat > "$cm_tmp/db_ua.json" <<JSON
[{"file": "$cm_root/pkg_a/api/a.cppm"},
 {"file": "$cm_root/pkg_b/api/b.cppm"},
 {"file": "$cm_root/shared_support/helper.cpp"},
 {"file": "/usr/lib/llvm-21/share/libc++/v1/std.cppm"}]
JSON
cm_sum_ua="$(_malf_commands_summary "$cm_tmp/db_ua.json" "$cm_root" 2 0 0 1 1 0 \
              "target $cm_root/pkg_a" "target $cm_root/pkg_b" 2>&1)"
check "the first-party total is split into the member rows and the unattributed residual" \
      "first-party 3 = 2 in the member rows below + 1 unattributed" \
      "$(grep -oE 'first-party [0-9]+ = [0-9]+ in the member rows below \+ [0-9]+ unattributed' <<< "$cm_sum_ua")"

check "a non-zero residual NAMES where it lives, so the number is a lead rather than a mystery" \
      "malf commands: unattributed live under: shared_support" \
      "$(grep -E '^malf commands:   unattributed live under:' <<< "$cm_sum_ua" | tr -s ' ')"

# An EXTERNAL entry is not unattributed — it is already accounted for by the external term next
# door, and counting it twice would make the residual fire on every run. The db here carries one
# (libc++'s std.cppm) and every first-party path IS under a member, so the residual must read zero.
check "an external entry stays external and never lands in the residual" \
      "first-party 4 = 4 in the member rows below + 0 unattributed" \
      "$(grep -oE 'first-party [0-9]+ = [0-9]+ in the member rows below \+ [0-9]+ unattributed' <<< "$cm_sum")"

# A test_package is configured OUTSIDE the member's preset — a synthetic conan consumer and a
# direct `cmake -S` — so it leaves no CMakeCache and _malf_commands_tree_role is blind to it by
# construction. This line is where a dropped one becomes visible, and its DENOMINATOR is the census
# (every test_package/CMakeLists.txt under a selected member), never the number of configures
# attempted: measured 2026-09-03, coderoast-server holds FOUR test_package directories and the run
# attempted THREE, so a line counting attempts printed "3" and read as complete. The arm feeds
# 3 present / 2 configured / 1 failed so that `unreached` is DERIVED rather than echoed — an
# unreached count that were simply passed in could not go wrong and would measure nothing.
check "the test_package line is a CENSUS, and unreached is derived from it" \
      "test_package 3 present = 2 configured + 1 failed + 0 unreached" \
      "$(grep -oE 'test_package [0-9]+ present = [0-9]+ configured \+ [0-9]+ failed \+ [0-9]+ unreached' <<< "$cm_sum")"

cm_sum_unreached="$(_malf_commands_summary "$cm_tmp/db.json" "$cm_root" 2 0 0 4 3 0 "target $cm_root" "target $cm_root/sub" 2>&1)"
check "a test_package the run never reached is counted apart from one that configured and refused" \
      "test_package 4 present = 3 configured + 0 failed + 1 unreached" \
      "$(grep -oE 'test_package [0-9]+ present = [0-9]+ configured \+ [0-9]+ failed \+ [0-9]+ unreached' <<< "$cm_sum_unreached")"

# A database that was never written is not a zero-entry database, and the two must not print the
# same sentence: the merge failing leaves the previous file OR no file, and both read as "clean"
# from a count alone.
cm_nodb_out="$(_malf_commands_summary "$cm_tmp/does-not-exist.json" "$cm_root" 1 0 0 1 1 0 "target $cm_root" 2>&1)"; cm_nodb_rc=$?
check "a missing database reds and says so, rather than summarising a file that is not there" \
      "rc=1 named" \
      "rc=$cm_nodb_rc $([[ "$cm_nodb_out" == *"NO DATABASE WAS WRITTEN"* ]] && echo named || echo "GOT: $cm_nodb_out")"

rm -rf "$cm_tmp"

# --- the repair is WIRED, and it runs BEFORE the merge -----------------------------------------
# A repair that ran AFTER `_malf_merge_db_tree` would re-configure the trees having already
# published the wrong file — indistinguishable from no repair at all in every artifact except the
# trees themselves, which nobody reads.
cm_src="$(awk '/^cmd_compile_commands\(\) \{/{f=1} f{print} f&&/^\}$/{exit}' "$MALF_BIN")"
cm_reassert_ln="$(grep -n 'demoted_by_bootstrap\[\$pkg_dir\]' <<< "$cm_src" | head -1 | cut -d: -f1)"
cm_merge_ln="$(grep -n '_malf_merge_db_tree "\$build_root"' <<< "$cm_src" | head -1 | cut -d: -f1)"
check "the target-role re-assertion exists and runs BEFORE the merge reads the trees" \
      "wired before" \
      "$([[ -n "$cm_reassert_ln" ]] && echo wired || echo "NOT-WIRED") $([[ -n "$cm_reassert_ln" && -n "$cm_merge_ln" && "$cm_reassert_ln" -lt "$cm_merge_ln" ]] && echo before || echo "AFTER(reassert=${cm_reassert_ln:-none} merge=${cm_merge_ln:-none})")"

# The demotion set is recorded from the SAME loop that reads the dependency entries, so a member
# demoted by a bootstrap cannot be missed by the repair. Pinned because the two are one loop by
# choice, not by accident: a second enumeration would be a second chance to disagree.
check "the demotion set is recorded where the dependency entries are read (one enumeration)" \
      "same loop" \
      "$(awk '/while IFS=\$.\\t. read -r _dep_ref _dep_dir/{f=1} f&&/demoted_by_bootstrap\[/{print "same loop"; exit} f&&/^        done </{print "SEPARATE"; exit}' <<< "$cm_src")"

# --- the verdict reaches the exit status -------------------------------------------------------
# Without this the summary is a nicer-looking silence: a demoted member would be printed and the
# command would still exit 0, which is the state N146 was already in.
check "a member in dependency role is FATAL, and the verdict reaches the exit status" \
      "fatal wired" \
      "$(grep -q 'is in the database as a DEPENDENCY' <<< "$cm_src" && echo fatal || echo "NOT-FATAL") $(grep -q 'commands_rc=1' <<< "$cm_src" && grep -q 'exit "\$commands_rc"' <<< "$cm_src" && echo wired || echo "NOT-WIRED")"

# --- THE TESTED REFERENCE REACHES A DIRECTLY-CONFIGURED test_package ---------------------------
# `conan create` runs the test_package's own recipe, so its generate() can put anything derived
# from `self.tested_reference_str` into the CMake cache. `malf commands` does NOT run that recipe —
# it installs a synthetic consumer and configures the directory itself — so every such variable is
# absent, and a test_package that refuses without one does not configure at all. Measured
# 2026-09-03 in insight-metalog, whose test_package CMakeLists FATAL_ERRORs without the version of
# the package under test: its translation unit was missing from the merged database (108 entries
# where the complete database is 109) and `malf commands` still exited 0.
#
# The repair is ONE cache variable with TWO producers — `MALF_TESTED_VERSION`, set here by malf and
# by the test_package recipe's generate() under `conan create` — so the CMakeLists reads one name
# and neither producer can satisfy its refusal while the other does not. Pinned STRUCTURALLY
# because reproducing the symptom needs conan, a toolchain and a network, while the mechanism is
# one flag on one command line.
cm_tp_src="$(awk '/^_malf_configure_test_package\(\) \{/{f=1} f{print} f&&/^\}$/{exit}' "$MALF_BIN")"
check "a directly-configured test_package is handed the version of the package under test" \
      "passed derived-from-ref" \
      "$(grep -q -- '-DMALF_TESTED_VERSION=' <<< "$cm_tp_src" && echo passed || echo "NOT-PASSED") $(grep -q -- '-DMALF_TESTED_VERSION=${main_ref#\*/}' <<< "$cm_tp_src" && echo derived-from-ref || echo "NOT-DERIVED-FROM-REF")"

# The value has to come from the reference malf ALREADY requires the test_package against, never
# from a second reading of the conanfile: two derivations of one fact are two chances to disagree,
# and the disagreement would be a version assertion passing against the wrong oracle.
check "the version and the --requires come from the SAME reference reading" \
      "one reading" \
      "$([[ "$(grep -c 'main_ref="$(_malf_pkg_ref' <<< "$cm_tp_src")" == 1 ]] && echo "one reading" || echo "GOT $(grep -c 'main_ref="$(_malf_pkg_ref' <<< "$cm_tp_src") readings")"

# The function has to REPORT the failure, not just print it: before this it returned 0 on a failed
# configure and the caller had nothing to test.
check "a failed test_package configure is returned to the caller, not only printed" \
      "returns" \
      "$(awk '/test_package configure FAILED/{f=1} f&&/return 1/{print "returns"; exit} f&&/^\}$/{print "SWALLOWED"; exit}' <<< "$cm_tp_src")"

# --- and it reaches the EXIT STATUS -----------------------------------------------------------
# The same argument as the dependency-role verdict above: the database's one reader is an editor,
# which reports a missing TU as an unknown SYMBOL rather than as a missing TU. A truncation that
# only prints is a truncation nothing downstream can see. Both doors are pinned — the configure
# that ran and refused, and the member the loop left before the configure was attempted.
check "a test_package that did not configure is FATAL, and the verdict reaches the exit status" \
      "failed-fatal unreached-fatal" \
      "$(grep -q 'test_package did not configure' <<< "$cm_src" && echo failed-fatal || echo "NOT-FATAL") $(grep -q 'test_package was never configured' <<< "$cm_src" && echo unreached-fatal || echo "UNREACHED-NOT-FATAL")"

# The CENSUS has to be taken BEFORE the gates that can drop the member, or the denominator is the
# number of configures attempted and the line reads as complete on a repo that is missing one.
# Line arithmetic against the preset gate, for the same reason the re-assertion is pinned that way:
# the ordering IS the property.
cm_census_ln="$(grep -n 'tp_present_dirs+=("$subdir")' <<< "$cm_src" | head -1 | cut -d: -f1)"
cm_preset_gate_ln="$(grep -n 'CMakePresets.json" \]\]; then' <<< "$cm_src" | head -1 | cut -d: -f1)"
check "the test_package census is taken BEFORE the gate that can drop the member" \
      "counted before" \
      "$([[ -n "$cm_census_ln" ]] && echo counted || echo "NOT-COUNTED") $([[ -n "$cm_census_ln" && -n "$cm_preset_gate_ln" && "$cm_census_ln" -lt "$cm_preset_gate_ln" ]] && echo before || echo "AFTER(census=${cm_census_ln:-none} gate=${cm_preset_gate_ln:-none})")"

# The buildless rows have to be APPENDED BY THE COMMAND, and outside the role-gate loop: fed to
# _malf_commands_tree_role a buildless member answers `no-cache`, which is FATAL — so a row added
# in the wrong loop would red on a member whose recipe is behaving exactly as written.
check "the command appends a buildless row, and outside the role-gate loop" \
      "appended outside" \
      "$(grep -q 'rows+=("buildless \$m")' <<< "$cm_src" && echo appended || echo "NOT-APPENDED") $(awk '/for m in "\$\{configured\[@\]\}"; do/{f=1} f&&/rows\+=\("buildless/{print "INSIDE"; exit} f&&/^    done$/{f=0} /for m in "\$\{buildless\[@\]\}"; do/{g=1} g&&/rows\+=\("buildless/{print "outside"; exit}' <<< "$cm_src")"

# An unused predicate tests nothing, and the fixture arms above would still be green.
check "the role verdict and the scope summary are both CALLED by the command" \
      "verdict summary" \
      "$(grep -q '_malf_commands_tree_role "\$(_malf_build_dir "\$m")"' <<< "$cm_src" && echo verdict || echo "VERDICT-UNCALLED") $(grep -q '_malf_commands_summary "\$build_root/compile_commands.json"' <<< "$cm_src" && echo summary || echo "SUMMARY-UNCALLED")"

# --- A PRESET-LESS MEMBER STILL REACHES ITS test_package --------------------------------------
# MEASURED 2026-09-03 at profile clang21-libcxx-release: `malf commands` in coderoast-server
# reported "test_package 4 present = 3 configured + 0 failed + 1 unreached" and 392 entries, of
# which ZERO were coderoast-server/server-logging's. That member is a header-library recipe with
# no CMakeLists.txt, so its conan install generates no CMakeToolchain and writes no
# CMakePresets.json, and the member loop's preset gate `continue`d — past the dependency bootstrap
# AND past the test_package configure, which needs no preset at all: it installs its own synthetic
# conan consumer and runs `cmake -S <member>/test_package` against the toolchain that install
# wrote. The one translation unit proving that header-only seat's contract had never been in any
# editor database.
#
# THE PROPERTY IS AN ORDERING, so it is pinned as one: the preset gate comes first and records,
# the test_package configure runs unconditionally after it, and only then does the guard that
# skips the MEMBER's own preset-driven configure appear. Structural because reproducing the
# symptom needs conan, a toolchain and a network, while the mechanism is three line numbers.
cm_gate_ln="$(grep -n 'CMakePresets.json" \]\]; then' <<< "$cm_src" | sed -n '2p' | cut -d: -f1)"
cm_tpcfg_ln="$(grep -n '_malf_configure_test_package "\$subdir"' <<< "$cm_src" | head -1 | cut -d: -f1)"
cm_guard_ln="$(grep -n '\[\[ "\$member_has_preset" == true \]\] || continue' <<< "$cm_src" | head -1 | cut -d: -f1)"
check "the preset gate, the test_package configure and the member-configure guard are in that order" \
      "gate<tp<guard" \
      "$([[ -n "$cm_gate_ln" && -n "$cm_tpcfg_ln" && -n "$cm_guard_ln" \
            && "$cm_gate_ln" -lt "$cm_tpcfg_ln" && "$cm_tpcfg_ln" -lt "$cm_guard_ln" ]] \
         && echo "gate<tp<guard" \
         || echo "GOT(gate=${cm_gate_ln:-none} tp=${cm_tpcfg_ln:-none} guard=${cm_guard_ln:-none})")"

# The gate itself must RECORD and fall through. A `continue` anywhere inside it is the exact
# regression: it reads as a guard on the member and is in fact a guard on the whole iteration.
cm_gate_end="$(awk -v s="$cm_gate_ln" 'NR>s && /^        fi$/{print NR; exit}' <<< "$cm_src")"
cm_gate_block="$(sed -n "${cm_gate_ln},${cm_gate_end}p" <<< "$cm_src")"
check "the preset gate records a buildless member and does NOT leave the iteration" \
      "records falls-through" \
      "$(grep -q 'buildless+=("$subdir")' <<< "$cm_gate_block" && echo records || echo "DOES-NOT-RECORD") $(grep -q '\bcontinue\b' <<< "$cm_gate_block" && echo "CONTINUES(the test_package is dropped again)" || echo falls-through)"

# The member's own preset-driven configure must stay BEHIND the guard, _malf_write_user_presets
# included: that function writes a CMakeUserPresets.json whose only content is an include of the
# CMakePresets.json this member does not have, so running it for a buildless member would break
# `cmake --preset` in that source dir for every other reader of it.
cm_wup_ln="$(grep -n '_malf_write_user_presets "\$subdir" "\$subdir_build"' <<< "$cm_src" | head -1 | cut -d: -f1)"
cm_preset_cfg_ln="$(grep -n 'cmake --preset "\$preset" -S "\$subdir"' <<< "$cm_src" | head -1 | cut -d: -f1)"
check "the member's user-presets write and its own cmake --preset stay behind that guard" \
      "both behind" \
      "$([[ -n "$cm_wup_ln" && -n "$cm_guard_ln" && "$cm_wup_ln" -gt "$cm_guard_ln" ]] && echo both || echo "USER-PRESETS-AHEAD(wup=${cm_wup_ln:-none} guard=${cm_guard_ln:-none})") $([[ -n "$cm_preset_cfg_ln" && -n "$cm_guard_ln" && "$cm_preset_cfg_ln" -gt "$cm_guard_ln" ]] && echo behind || echo "CONFIGURE-AHEAD(cfg=${cm_preset_cfg_ln:-none} guard=${cm_guard_ln:-none})")"

# --- BOTH RESIDUALS ARE SET DIFFERENCES, WITH EXACTLY ONE PRODUCER EACH ------------------------
# `unreached` used to have TWO definitions that could disagree: the number PRINTED was derived
# (present - configured - failed) while the list that produced the FATAL was appended to by hand
# inside the loop. A skip path that forgot the append printed "1 unreached" and issued no refusal,
# and the exit status stayed 0 for that reason. Deriving both from the census closes it, and the
# pin is that there is exactly ONE producer of each residual — a second one is a second chance to
# disagree, which is the defect itself.
check "unreached and unaccounted are each produced in exactly ONE place, by set difference" \
      "1 derived 1 derived" \
      "$(grep -c 'tp_unreached+=' <<< "$cm_src") $(grep -q 'for m in "${tp_present_dirs\[@\]}"; do \[\[ -n "${tp_accounted\[\$m\]:-}" \]\] || tp_unreached+=' <<< "$cm_src" && echo derived || echo "NOT-DERIVED-FROM-CENSUS") $(grep -c 'unaccounted+=' <<< "$cm_src") $(grep -q 'for m in "${members\[@\]}"; do \[\[ -n "${member_accounted\[\$m\]:-}" \]\] || unaccounted+=' <<< "$cm_src" && echo derived || echo "NOT-DERIVED-FROM-MEMBERS")"

# --- A BUILDLESS MEMBER IS A SHAPE OR A FAILURE, AND ONE OF THEM IS FATAL ----------------------
# Having no CMakePresets.json is correct for a header-library recipe and catastrophic for a member
# that declares a CMake project — same empty build dir, same absence from the per-member rows, and
# in the second case every one of that member's translation units is gone. The CMakeLists.txt is
# the discriminator, and it is read HERE rather than at the gate, so the gate keeps the artifact
# predicate and this keeps the diagnosis.
check "a buildless member that declares a CMake project is FATAL, discriminated, and reaches the exit status" \
      "fatal discriminated rc" \
      "$(awk '/for bl_pkg in "\$\{buildless\[@\]\}"/{f=1} f&&/^    done$/{exit}
              f&&/declares a CMake project and produced no/{a=1}
              f&&/\[\[ -f "\$bl_pkg\/CMakeLists.txt" \]\] \|\| continue/{b=1}
              f&&/commands_rc=1/{c=1}
              END{printf "%s %s %s", a?"fatal":"NO-FATAL-TEXT", b?"discriminated":"NO-CMAKELISTS-DISCRIMINATOR", c?"rc":"RC-NOT-SET"}' <<< "$cm_src")"

# A member the loop accounts for under NONE of the three terms makes the member line's identity
# false. No path written today reaches it; it is here because the identity is what a reader
# checks, and a line that silently stops summing is the truncation-that-reads-as-complete failure
# this whole command exists to refuse.
check "a member accounted for by none of the three terms is FATAL, and reaches the exit status" \
      "fatal rc" \
      "$(awk '/for ua_pkg in "\$\{unaccounted\[@\]\}"/{f=1} f&&/^    done$/{exit}
              f&&/reached none of/{a=1} f&&/commands_rc=1/{c=1}
              END{printf "%s %s", a?"fatal":"NO-FATAL-TEXT", c?"rc":"RC-NOT-SET"}' <<< "$cm_src")"

echo
echo "[7r] the build slot carries an OWNERSHIP PROOF — a corpse and a holder between runs differ"

# THE INCIDENT, 2026-09-03. The workspace rule is that one LANE builds at a time, because
# concurrent malf runs share one editable conan tree. The protocol was hand-rolled and lived in no
# file: `mkdir /tmp/coderoast-build-slot`, plus a `holder` file carrying the CURRENT RUN's pid,
# re-stamped per run. A third party judged the slot stale and `rm -rf`'d it WHILE a `malf test`
# was live; the holder's next re-stamp failed and two gcc runs ran unprotected. Green, and
# consistent with their clang twins — a near miss, not a loss.
#
# THE ROOT CAUSE IS NOT THE DELETION. A bare mkdir carries no ownership proof, so a lane cannot
# tell a corpse from a holder that is merely BETWEEN RUNS — and with a per-run pid, "that pid is
# gone" is the NORMAL state, not evidence of anything. Every arm below is one of the judgements a
# lane has to make, and each was previously a guess.
#
# WHAT NO TOOL CAN DO, said plainly so nobody reads more into these arms than they prove: a
# literal `rm -rf` cannot be refused by anything. What is pinned is that every path malf offers
# for taking the slot from somebody refuses while its holder is provably alive, and that the
# directory itself survives each refusal — which is what removes the REASON to reach for `rm -rf`.

sl_tmp="$(mktemp -d)"
sl_dir="$sl_tmp/slot"
# EVERY invocation is pinned to the scratch slot. A test that touched the default path would
# reach into a live lane's slot on the developer's own box, which is the incident itself.
sl() {   # <args...>
    MALF_BUILD_SLOT_DIR="$sl_dir" bash "$MALF_BIN" slot "$@" 2>&1
}
sl_as() {   # <anchor pid> <args...>
    MALF_BUILD_SLOT_DIR="$sl_dir" MALF_BUILD_SLOT_ANCHOR="$1" bash "$MALF_BIN" slot "${@:2}" 2>&1
}
sl_dir_exists() { [[ -d "$sl_dir" ]] && echo present || echo GONE; }

sl_free_out="$(sl status)"; sl_free_rc=$?
check "a slot nobody holds reads FREE and exits 0" \
      "rc=0 FREE" \
      "rc=$sl_free_rc $([[ "$sl_free_out" == *"FREE"* ]] && echo FREE || echo "GOT: $sl_free_out")"

# The anchor is a process this suite owns and can kill, which is what makes ALIVE and GONE
# reachable states here rather than things to wait for.
sleep 300 & sl_anchor=$!
sl_acq_out="$(sl_as "$sl_anchor" acquire --label suite-lane-A)"; sl_acq_rc=$?
sl_token="$(grep -oE 'token [0-9a-f]{32}' <<< "$sl_acq_out" | head -1 | awk '{print $2}')"
check "acquire claims a free slot, names the holder, and mints a 32-hex token" \
      "rc=0 acquired 32" \
      "rc=$sl_acq_rc $([[ "$sl_acq_out" == *"ACQUIRED by 'suite-lane-A'"* ]] && echo acquired || echo "GOT: $sl_acq_out") ${#sl_token}"

sl_acq2_out="$(sl_as "$sl_anchor" acquire --label suite-lane-B)"; sl_acq2_rc=$?
check "a SECOND lane is refused while the holder's anchor is alive, and is told whose it is" \
      "rc=1 named present" \
      "rc=$sl_acq2_rc $([[ "$sl_acq2_out" == *"HELD by 'suite-lane-A'"* && "$sl_acq2_out" == *"is ALIVE"* ]] \
          && echo named || echo "GOT: $sl_acq2_out") $(sl_dir_exists)"

# THE THREE WAYS A THIRD PARTY REACHES FOR SOMEBODY ELSE'S SLOT. All three refuse, and — the arm
# that matters — the directory is still there afterwards.
sl_rel_none="$(sl release)"; sl_rel_none_rc=$?
check "release with NO token is refused while the holder is alive, and the slot survives" \
      "rc=1 refused present" \
      "rc=$sl_rel_none_rc $([[ "$sl_rel_none" == *"REFUSED"* ]] && echo refused || echo "GOT: $sl_rel_none") $(sl_dir_exists)"
sl_rel_bad="$(sl release --token 00000000000000000000000000000000)"; sl_rel_bad_rc=$?
check "release with the WRONG token is refused, says so, and the slot survives" \
      "rc=1 named present" \
      "rc=$sl_rel_bad_rc $([[ "$sl_rel_bad" == *"token given does not match"* ]] && echo named || echo "GOT: $sl_rel_bad") $(sl_dir_exists)"
# There is deliberately no --force for a LIVE holder. The escape is to kill the anchor, which is
# an act with a visible subject; a --force that worked here would be the incident with a flag on.
sl_rel_force="$(sl release --force)"; sl_rel_force_rc=$?
check "release --force is refused on a LIVE holder — the owner has to die first" \
      "rc=1 named present" \
      "rc=$sl_rel_force_rc $([[ "$sl_rel_force" == *"deliberately no --force for a LIVE holder"* ]] \
          && echo named || echo "GOT: $sl_rel_force") $(sl_dir_exists)"

sl_rel_ok="$(sl release --token "$sl_token")"; sl_rel_ok_rc=$?
check "the holder releases with its own token" \
      "rc=0 released GONE" \
      "rc=$sl_rel_ok_rc $([[ "$sl_rel_ok" == *"RELEASED"* ]] && echo released || echo "GOT: $sl_rel_ok") $(sl_dir_exists)"

# THE ANCHOR'S RESOLUTION IS A SESSION, NOT A LANE — and the tool now says so when, and only
# when, that costs the reader something. Measured twice on the real box (2026-09-03 and again
# 2026-09-05 from a third, unrelated lane): two lanes of one session derive the SAME anchor pid
# and the SAME start time, so "the anchor is ALIVE" proves the session is alive and nothing at
# all about the holding lane. A delegated lane is not an OS process — its ancestry is
# `bash -> claude`, the bash is per-run (the defect the anchor replaced) and the process above it
# is the whole session — so there is no per-lane pid to anchor on and this cannot be fixed by
# choosing a different ancestor. It is DECLARED instead, conditionally.
#
# BOTH DIRECTIONS ARE PINNED HERE, because a note that always fires is noise and a note that
# never fires is absent. Determinism comes from the harness's own `sleep` anchor: acquiring
# THROUGH it makes the holder's anchor foreign to the reader, and acquiring WITHOUT it makes the
# holder's anchor identical to the reader's, since both invocations walk from the same parentage.
sleep 300 & sl_anchor_far=$!
sl_as "$sl_anchor_far" acquire --label suite-other-session >/dev/null 2>&1
sl_far_out="$(sl status)"
check "a holder in ANOTHER session does NOT trip the shared-anchor note (no false positive)" \
      "silent" \
      "$([[ "$sl_far_out" != *"ANCHOR IS SHARED"* ]] && echo silent || echo "GOT: $sl_far_out")"
sl_far_tok="$(grep -oE 'token [0-9a-f]{32}' <<< "$sl_far_out" | head -1 | awk '{print $2}')"
sl release --token "$sl_far_tok" >/dev/null 2>&1
kill "$sl_anchor_far" 2>/dev/null; wait "$sl_anchor_far" 2>/dev/null

sl_same_acq="$(sl acquire --label suite-same-session)"
sl_same_out="$(sl status)"
check "a holder in THIS session DOES trip it, and withdraws the kill-the-anchor remedy" \
      "fires no-kill" \
      "$([[ "$sl_same_out" == *"ANCHOR IS SHARED"* ]] && echo fires || echo "GOT: $sl_same_out") $([[ "$sl_same_out" == *"DO NOT kill pid"* ]] && echo no-kill || echo KILL-STILL-ADVISED)"
# THE MACHINE FIELD MUST STAY SCRAPEABLE. `malf slot status` emits the token on a line beginning
# "malf slot: token ", and a caller extracting it with `sed -n 's/^malf slot: token //p'` gets a
# LIST, not a value, the moment any other line carries that prefix. Caught by writing exactly
# that extractor while testing the note above: an early wording ended a sentence with "the token
# above," on its own line, the extractor returned two lines, and the resulting comparison
# reported a token mismatch on the correct token. Prose near a machine field is a contract.
sl_tok_lines="$(grep -c '^malf slot: token ' <<< "$sl_same_out")"
check "exactly ONE status line carries the machine token prefix, note or no note" \
      "1" \
      "$sl_tok_lines"
sl_same_tok="$(grep -oE 'token [0-9a-f]{32}' <<< "$sl_same_acq" | head -1 | awk '{print $2}')"
sl_same_scraped="$(sed -n 's/^malf slot: token //p' <<< "$sl_same_out")"
check "the scraped token equals the minted one (a single clean value, not a list)" \
      "equal" \
      "$([[ -n "$sl_same_tok" && "$sl_same_scraped" == "$sl_same_tok" ]] && echo equal \
          || echo "MINTED:$sl_same_tok SCRAPED:$sl_same_scraped")"
sl release --token "$sl_same_tok" >/dev/null 2>&1

# A GENUINELY STALE SLOT. The anchor dies; nothing else changes. This is the judgement the old
# protocol could not make, and it is the whole reason the anchor is the lane's SESSION and not the
# run: the pid in the stamp is expected to have no malf process behind it.
sl_as "$sl_anchor" acquire --label suite-lane-A >/dev/null 2>&1
kill "$sl_anchor" 2>/dev/null; wait "$sl_anchor" 2>/dev/null
sl_stale_out="$(sl status)"; sl_stale_rc=$?
check "once the anchor dies the slot reads STALE and exits 2 (a distinct state, not just 'held')" \
      "rc=2 stale" \
      "rc=$sl_stale_rc $([[ "$sl_stale_out" == *"STALE"* && "$sl_stale_out" == *"is GONE"* ]] \
          && echo stale || echo "GOT: $sl_stale_out")"
sleep 300 & sl_anchor2=$!
sl_reclaim_out="$(sl_as "$sl_anchor2" acquire --label suite-lane-B)"; sl_reclaim_rc=$?
check "acquire RECLAIMS a provably dead holder by itself, and names whose slot it took" \
      "rc=0 reclaimed acquired" \
      "rc=$sl_reclaim_rc $([[ "$sl_reclaim_out" == *"reclaiming"* && "$sl_reclaim_out" == *"suite-lane-A"* ]] \
          && echo reclaimed || echo "GOT: $sl_reclaim_out") $([[ "$sl_reclaim_out" == *"ACQUIRED by 'suite-lane-B'"* ]] \
          && echo acquired || echo NOT-ACQUIRED)"
sl_tok2="$(grep -oE 'token [0-9a-f]{32}' <<< "$sl_reclaim_out" | head -1 | awk '{print $2}')"
check "the reclaimed slot mints a NEW token — the dead holder's does not still open it" \
      "different" \
      "$([[ -n "$sl_tok2" && "$sl_tok2" != "$sl_token" ]] && echo different || echo "REUSED: $sl_tok2")"
sl release --token "$sl_tok2" >/dev/null 2>&1
kill "$sl_anchor2" 2>/dev/null; wait "$sl_anchor2" 2>/dev/null

# THE LEGACY SLOT — the exact shape found on this box on 2026-09-03: a directory holding `holder`
# (a pid, already gone) and `owner` (a lane name). It carries no proof of anything, so the answer
# is UNKNOWN and the removal is a HUMAN's. Reporting it dead would be the incident automated;
# reporting it held forever would make the tool unusable. It is the one state that asks.
mkdir -p "$sl_dir"; echo 707217 > "$sl_dir/holder"; echo hephaistos-N147 > "$sl_dir/owner"
sl_unk_out="$(sl status)"; sl_unk_rc=$?
check "a hand-rolled slot reads UNKNOWN and exits 3 — never confirmed, never disproved" \
      "rc=3 unknown" \
      "rc=$sl_unk_rc $([[ "$sl_unk_out" == *"UNKNOWN"* ]] && echo unknown || echo "GOT: $sl_unk_out")"
sl_unk_acq="$(sl_as 1 acquire --label suite-lane-C)"; sl_unk_acq_rc=$?
check "acquire does NOT reclaim an unreadable stamp, and the directory survives" \
      "rc=1 refused present" \
      "rc=$sl_unk_acq_rc $([[ "$sl_unk_acq" == *"NOT reclaimed automatically"* ]] && echo refused || echo "GOT: $sl_unk_acq") $(sl_dir_exists)"
sl_unk_rel="$(sl release)"; sl_unk_rel_rc=$?
check "plain release refuses an unreadable stamp — there is no token to match and no anchor to test" \
      "rc=1 refused present" \
      "rc=$sl_unk_rel_rc $([[ "$sl_unk_rel" == *"no stamp this malf wrote"* ]] && echo refused || echo "GOT: $sl_unk_rel") $(sl_dir_exists)"
sl_unk_force="$(sl release --force)"; sl_unk_force_rc=$?
check "release --force removes it, and prints what it destroyed before doing so" \
      "rc=0 recorded GONE" \
      "rc=$sl_unk_force_rc $([[ "$sl_unk_force" == *"it contained"* && "$sl_unk_force" == *"holder"* ]] \
          && echo recorded || echo "GOT: $sl_unk_force") $(sl_dir_exists)"

# A PROCESS'S START IS FIELD 22 OF /proc/<pid>/stat, NEVER THE MTIME OF ITS /proc DIRECTORY.
# That mtime is the moment the kernel instantiated the entry's inode — its first lookup, not the
# process's start — and the entry is instantiated again whenever its inode is evicted. Measured
# 2026-09-29: a shell started 20:19:19 read 20:19:20. A stamp holding one reading and a check
# holding another read a LIVE anchor as gone, and `acquire` then deleted the holder's slot.
# WHEN the kernel instantiates the fixture's entry is not this suite's to choose — any `ps` on the
# machine instantiates every entry — so no arm depends on it. The arms below discriminate anyway:
# a true start is clock ticks after boot and an mtime is seconds since the epoch, so the former
# reading never equals a stamp holding the true start.
sleep 300 & sl_live=$!
sl_live_start="$(sed 's/.*) //' "/proc/$sl_live/stat" | awk '{print $20}')"
sl_live_mtime="$(stat -c %Y "/proc/$sl_live")"
sl_as "$sl_live" acquire --label suite-lane-start >/dev/null 2>&1
read -r sl_magic _ sl_live_tok _ _ _ sl_stamped < "$sl_dir/stamp"
check "acquire stamps the anchor's START (field 22 of /proc/<pid>/stat), in a stamp of format 2" \
      "malf-slot-2 $sl_live_start" "$sl_magic $sl_stamped"
sl_held_out="$(sl status)"; sl_held_rc=$?
check "a live anchor whose /proc mtime is not its start reads HELD (exit 1), never STALE" \
      "rc=1 held" \
      "rc=$sl_held_rc $([[ "$sl_held_out" == *"is ALIVE"* ]] && echo held || echo "GOT: $sl_held_out")"
sl_steal_out="$(sl_as 1 acquire --label suite-lane-thief)"; sl_steal_rc=$?
check "acquire never reclaims that holder's slot, and the slot survives" \
      "rc=1 refused present" \
      "rc=$sl_steal_rc $([[ "$sl_steal_out" != *"reclaiming"* ]] && echo refused || echo "GOT: $sl_steal_out") $(sl_dir_exists)"
sl release --token "$sl_live_tok" >/dev/null 2>&1
# THE DELETION ITSELF: a stamp holding its live anchor's TRUE start, in either format, is never
# reclaimed. Read against the /proc mtime, that start is a different number, the anchor reads GONE,
# and `acquire` deletes the slot and takes it — the failure, reproduced without waiting for the
# kernel to evict an inode.
mkdir -p "$sl_dir"
printf 'malf-slot-1 token %s anchor %s start %s\nlabel suite-lane-true\nsince now\n' \
       "$(printf 'b%.0s' {1..32})" "$sl_live" "$sl_live_start" > "$sl_dir/stamp"
sl_true_acq="$(sl_as 1 acquire --label suite-lane-thief)"; sl_true_acq_rc=$?
check "a stamp holding its live anchor's true start is never reclaimed, and the slot survives" \
      "rc=1 refused present" \
      "rc=$sl_true_acq_rc $([[ "$sl_true_acq" != *"reclaiming"* ]] && echo refused || echo "GOT: $sl_true_acq") $(sl_dir_exists)"
rm -rf "$sl_dir"
# A STAMP OF THE FORMER FORMAT holds a directory mtime, which says nothing about its anchor. Read as
# the current format it would compare an mtime against a start and call a live holder dead — so it
# is UNKNOWN: never confirmed, never reclaimed without a human.
mkdir -p "$sl_dir"
printf 'malf-slot-1 token %s anchor %s start %s\nlabel suite-lane-old\nsince now\n' \
       "$(printf 'a%.0s' {1..32})" "$sl_live" "$sl_live_mtime" > "$sl_dir/stamp"
sl_old_out="$(sl status)"; sl_old_rc=$?
check "a stamp of the former format reads UNKNOWN (exit 3), whatever its anchor" \
      "rc=3 unknown" \
      "rc=$sl_old_rc $([[ "$sl_old_out" == *"UNKNOWN"* ]] && echo unknown || echo "GOT: $sl_old_out")"
sl_old_acq="$(sl_as 1 acquire --label suite-lane-D)"; sl_old_acq_rc=$?
check "acquire does not reclaim a stamp of the former format, and the directory survives" \
      "rc=1 refused present" \
      "rc=$sl_old_acq_rc $([[ "$sl_old_acq" == *"NOT reclaimed automatically"* ]] && echo refused || echo "GOT: $sl_old_acq") $(sl_dir_exists)"
rm -rf "$sl_dir"
kill "$sl_live" 2>/dev/null; wait "$sl_live" 2>/dev/null

# THE PATH ITSELF IS A GUARD, because an `rm -rf` runs against it. A mis-set variable must red
# here rather than delete a level up.
for sl_bad in / /tmp relative/path; do
    sl_bad_out="$(MALF_BUILD_SLOT_DIR="$sl_bad" bash "$MALF_BIN" slot status 2>&1)"; sl_bad_rc=$?
    check "MALF_BUILD_SLOT_DIR='$sl_bad' is refused before anything runs (an rm -rf targets it)" \
          "rc=1 refused" \
          "rc=$sl_bad_rc $([[ "$sl_bad_out" == *"must be an absolute path"* ]] && echo refused || echo "GOT: $sl_bad_out")"
done

# STRUCTURAL. The symptom (a slot deleted out from under its holder) is downstream of the
# mechanism (an unconditional delete of that directory), and only the mechanism can be pinned
# without a second lane. Every `rm -rf` of the slot must live inside cmd_slot, where the state
# machine above decides whether it may run — a helper that grew its own would be invisible to
# every arm here. Comments are stripped first so this file's own prose cannot satisfy it.
sl_src="$(sed 's/#.*$//' "$MALF_BIN")"
sl_body="$(awk '/^cmd_slot\(\) \{/{f=1} f{print} f&&/^\}$/{exit}' <<< "$sl_src")"
sl_total="$(grep -cF 'rm -rf "$MALF_BUILD_SLOT_DIR"' <<< "$sl_src" || true)"
sl_inside="$(grep -cF 'rm -rf "$MALF_BUILD_SLOT_DIR"' <<< "$sl_body" || true)"
check "the slot is deleted only from inside cmd_slot, and it is deleted somewhere (guards a zero)" \
      "all inside, >0" \
      "$([[ "$sl_total" == "$sl_inside" ]] && echo "all inside" || echo "$((sl_total - sl_inside)) OUTSIDE cmd_slot"), $( ((sl_total > 0)) && echo ">0" || echo "ZERO — the fixture no longer finds the deletes")"

rm -rf "$sl_tmp"
echo

echo "[7r2] the slot's DEFAULT path is a machine fact: the shared root when it exists, else TMPDIR"

# On the box where the desk and the self-hosted runner are two accounts, /tmp cannot hold a slot
# both can reclaim (sticky bit, fs.protected_regular=2), so malf switches to a shared root whose
# EXISTENCE is the switch. The arms pin the three resolutions; the default path is the one almost
# every invocation takes and the one nobody passes a flag to, so it is pinned rather than trusted.
# Every arm steers BOTH candidates into scratch — the shared root through
# MALF_BUILD_SLOT_SHARED_ROOT, the fallback through TMPDIR — so no arm reads or truncates a live
# lane's slot or mutex on the developer's own box. What cannot run here is the cross-account half
# (two uids and a default ACL need root); malf/runner/isolate-runner-wsl.sh proves that on the box.
sd_tmp="$(mktemp -d)"
mkdir "$sd_tmp/shared" "$sd_tmp/tmp"
sd_dir() {   # <shared root> [MALF_BUILD_SLOT_DIR] -> the dir `status` reports
    env -u MALF_BUILD_SLOT_DIR TMPDIR="$sd_tmp/tmp" MALF_BUILD_SLOT_SHARED_ROOT="$1" \
        ${2:+MALF_BUILD_SLOT_DIR="$2"} bash "$MALF_BIN" slot status 2>&1 \
        | sed -n 's/^malf slot: dir //p'
}
check "shared root present, nothing set -> the slot lives under the shared root" \
      "$sd_tmp/shared/slot" "$(sd_dir "$sd_tmp/shared")"
check "shared root absent -> the slot falls back to \${TMPDIR}/coderoast-build-slot" \
      "$sd_tmp/tmp/coderoast-build-slot" "$(sd_dir "$sd_tmp/absent")"
check "MALF_BUILD_SLOT_DIR set -> it wins over a present shared root" \
      "$sd_tmp/explicit" "$(sd_dir "$sd_tmp/shared" "$sd_tmp/explicit")"
rm -rf "$sd_tmp"
echo

echo "[7s] a fan-out's width is derived from BOTH operands, the cores and the memory available NOW"

# width = max(1, min(cores, floor((MemAvailable − reserve) / cap))). MemAvailable and not MemTotal:
# a machine that hosts two builds at once hands the second one what the first already holds, where
# a width read off MemTotal gives both their full width together and sends the machine into swap,
# which reads as a slow build, never as a failure. Every figure below is KiB.
wd_gib=1048576
wd_cap="$MALF_BUILD_JOB_MEM_KB"; wd_reserve="$MALF_FANOUT_RESERVE_KB"
check "the declared operands: 1.1 GiB a build job, 3 GiB a sanitizer job, 2 GiB of reserve" \
      "1153434 3145728 2097152" "$MALF_BUILD_JOB_MEM_KB $MALF_SANITIZER_JOB_MEM_KB $MALF_FANOUT_RESERVE_KB"
check "an idle 16-core machine (20 GiB available) keeps all 16 jobs" \
      "16" "$(_malf_width 16 $(( 20 * wd_gib )) "$wd_reserve" "$wd_cap")"
check "beside a build holding 7.8 GiB (12.2 GiB available) the second build gets 9 jobs, not 16" \
      "9" "$(_malf_width 16 12792627 "$wd_reserve" "$wd_cap")"
check "the cores bind when memory is plentiful: 8 cores, 20 GiB available" \
      "8" "$(_malf_width 8 $(( 20 * wd_gib )) "$wd_reserve" "$wd_cap")"
check "less available than the reserve is one job, never zero and never negative" \
      "1" "$(_malf_width 16 $(( 1 * wd_gib )) "$wd_reserve" "$wd_cap")"
check "a sanitizer build's 3 GiB cap: 20 GiB available is 6 jobs" \
      "6" "$(_malf_width 16 $(( 20 * wd_gib )) "$wd_reserve" "$MALF_SANITIZER_JOB_MEM_KB")"
check "a machine that does not report MemAvailable keeps the cores alone" \
      "16" "$(_malf_width 16 "" "$wd_reserve" "$wd_cap")"
wd_tmp="$(mktemp -d)"
printf 'MemTotal:       24610264 kB\nMemFree:         1000000 kB\nMemAvailable:   12792627 kB\n' > "$wd_tmp/meminfo"
printf 'MemTotal:       24610264 kB\nMemFree:         1000000 kB\n' > "$wd_tmp/meminfo_old"
check "the memory operand is the MemAvailable line, never MemTotal or MemFree" \
      "12792627" "$(_malf_mem_available_kib "$wd_tmp/meminfo")"
check "a meminfo without MemAvailable answers nothing, and says so by its exit code" \
      "rc=1 []" "rc=$(_malf_mem_available_kib "$wd_tmp/meminfo_old" >/dev/null; echo $?) [$(_malf_mem_available_kib "$wd_tmp/meminfo_old")]"
check "the printed derivation carries the four operands and the width" \
      "malf build: -j9 = max(1, min(16 cores, floor((MemAvailable 12492 MiB - reserve 2048 MiB) / 1126 MiB a job)))" \
      "$(_malf_width_line "malf build" 9 16 12792627 "$wd_reserve" "$wd_cap")"
check "on a machine with no MemAvailable the derivation says the memory operand was not applied" \
      "malf build: -j16 = 16 cores; MemAvailable is not reported here, so memory does not bound the width" \
      "$(_malf_width_line "malf build" 16 16 "" "$wd_reserve" "$wd_cap")"
# THE CAP IS A MEASUREMENT AND IT AGES as translation units grow. A build whose largest process
# peaked above the cap the width was derived from says so — a note, never a failure: a width
# changes scheduling, never a verdict.
wd_out="$(_malf_run_measured "$wd_tmp/peak" bash -c 'exit 7' 2>&1)"; wd_rc=$?
check "a measured run returns its command's exit code and records a peak" \
      "rc=7 recorded" "rc=$wd_rc $([[ "$(cat "$wd_tmp/peak" 2>/dev/null)" =~ ^[0-9]+$ ]] && echo recorded || echo "GOT: $(cat "$wd_tmp/peak" 2>&1)")"
check "a largest process above the cap prints the note, naming both figures and the knob" \
      "noted" "$(_malf_cap_note "malf build" 2097152 "$wd_cap" MALF_BUILD_JOB_MEM_KB 2>&1 | tr '\n' ' ' | grep -q '2048 MiB.*1126 MiB.*MALF_BUILD_JOB_MEM_KB' && echo noted || echo "GOT: $(_malf_cap_note "malf build" 2097152 "$wd_cap" MALF_BUILD_JOB_MEM_KB 2>&1)")"
check "a largest process under the cap, or an unmeasured one, prints nothing" \
      "[] []" "[$(_malf_cap_note "malf build" 500000 "$wd_cap" MALF_BUILD_JOB_MEM_KB 2>&1)] [$(_malf_cap_note "malf build" "" "$wd_cap" MALF_BUILD_JOB_MEM_KB 2>&1)]"
rm -rf "$wd_tmp"
echo

echo "[8] invocation-point independence — malf VERB DIR == cd DIR then malf VERB"

# THE BUG THIS PINS, measured 2026-09-04: an explicit arg naming a package dir used to mean
# "that package ONLY", so the two spellings of one intent disagreed wherever a repo has a root
# recipe AND sub-recipes. It fails SILENTLY — the short build exits 0, so a green is read over a
# surface that was never compiled. The fixture is built here rather than pointed at a real repo,
# so the arms keep their teeth when the workspace's package layout changes.
ip_tmp="$(mktemp -d)"
mkdir -p "$ip_tmp/repo/leaf_a" "$ip_tmp/repo/leaf_b"
for d in "$ip_tmp/repo" "$ip_tmp/repo/leaf_a" "$ip_tmp/repo/leaf_b"; do
    name="$(basename "$d")"
    printf 'from conan import ConanFile\nclass P(ConanFile):\n    name = "%s"\n    version = "1.0"\n' \
           "${name//-/_}" > "$d/conanfile.py"
done

# `members` is what the sweep enumerates; both spellings must resolve the SAME root, so it is
# enough to prove the resolver treats them alike. Three recipes under the root => 3 members.
ip_members="$(python3 "$MALF_ROOT/malf_graph.py" members "$ip_tmp/repo" 2>/dev/null | grep -c . || true)"
check "the fixture really is multi-package (guards a vacuous arm)" "3" "$ip_members"

ip_body="$(sed -n '/^_malf_resolve_target_or_sweep() {/,/^}/p' "$MALF_BIN")"
check "the resolver no longer branches on the ARG being a package dir (the retired hatch)" \
      "0" "$(grep -cF 'if [[ -n "$arg" && -f "$root/conanfile.py" ]]' <<< "$ip_body" || true)"
check "the single-package escape is gated on --only instead" \
      "1" "$(grep -cF 'if [[ -n "${MALF_ONLY:-}" ]]' <<< "$ip_body" || true)"
check "--only refuses a dir carrying no recipe, rather than silently sweeping it" \
      "1" "$(grep -cF -- '--only needs a recipe AT' <<< "$ip_body" || true)"

# All three sweeping verbs accept --only, and none of them FORWARDS it: a forwarded --only would
# reach each sweep member, and a member is already a single package, so it would be noise that
# could only ever mislead a reader of the member's command line.
for v in build test bench; do
    vb="$(sed -n "/^cmd_$v() {/,/^}/p" "$MALF_BIN")"
    check "cmd_$v accepts --only" "1" "$(grep -cF -- '--only)' <<< "$vb" || true)"
    check "cmd_$v does not forward --only to sweep members" \
          "0" "$(grep -E -- '--only\)' <<< "$vb" | grep -c 'fwd+=' || true)"
done


# ── THE SWEEP MUST TERMINATE, AND ONLY A RUN CAN SAY SO ──────────────────────────────────────
#
# EVERY ARM ABOVE IS A grep OVER THE RESOLVER'S SOURCE TEXT, and that is exactly why they were all
# green while `malf build insight-eidos` ran away to 717 nested processes on 2026-09-04. A
# structural arm can say the function is SHAPED a certain way; it cannot say the function
# TERMINATES. The sweep re-invokes `malf <verb> <member>`, a member may BE the root, and the child
# then re-derived the same member list and swept again — unbounded, with no error and no compiler
# ever started, so it presented as a slow build.
#
# THE FIXTURE ABOVE ALREADY REPRODUCED IT AND WAS NEVER RUN AGAINST. `repo` carries a recipe AND
# holds `leaf_a`/`leaf_b`, which is the precise shape (`insight-eidos`, `coderoast-ipc`). So the
# arm costs one invocation, not a new fixture.
#
# WHAT IS ASSERTED IS THE ENUMERATION COUNT, not the build outcome. The header line is printed once
# per sweep, so under the defect it appears unboundedly and under the fix exactly once. The build
# itself is EXPECTED to fail (these recipes have no CMake project and their requirements resolve to
# nothing), and that is deliberate: the recursion happens during RESOLUTION, strictly before any
# build, so a fixture that cannot build still exercises the whole defect surface. `timeout` bounds
# a red rather than letting the selftest itself run away, and `setsid` puts the run in its own
# process group so the timeout reaps the descendants instead of orphaning them.
ip_run_log="$ip_tmp/sweep.log"
setsid timeout --kill-after=5 60 "$MALF_BIN" build "$ip_tmp/repo" > "$ip_run_log" 2>&1 || true
ip_headers="$(grep -c 'packages under' "$ip_run_log" || true)"
check "the sweep enumerates its members EXACTLY once — a member that is the root must not re-sweep" \
      "1" "$ip_headers"
# And the enumeration that happened must be the real one: an arm that passed because the sweep
# never started would also report 1... or 0. Pin the count the fixture declares.
check "the one enumeration covers all 3 fixture packages (guards a sweep that never ran)" \
      "1" "$(grep -c '3 packages under' "$ip_run_log" || true)"
# The runaway's own signature: the same member label announced more than once. Under the defect
# `[1/3]` is printed at every level of the recursion.
check "no member is announced twice (the runaway's signature)" \
      "1" "$(grep -c 'malf build \[1/3\]' "$ip_run_log" || true)"

rm -rf "$ip_tmp"
echo

echo "[9] the ONE test selection is malf_recipe_tests.py's: \`malf test\` reads it, a create runs it, and a run that selects NOTHING is a named failure (DN-142.D5)"

# ctest over a tree registering zero tests prints "No tests were found!!!" and exits 0, and
# `malf test` passed that exit through — measured on insight-eidos's root tree after a dependency
# configure left its tests unregistered. The guard reds an OWED run that selects nothing and names
# the tree's configure role; `--corpus` alone and a --filter inside a sweep member owe nothing.
# DN-142.D5 (5) moved the selection into the helper every create runs from `build()`: one
# definition, two callers. The desk arms drive `_malf_test_run` with a stub ctest that records its
# argv; the expected argv are the ones the inline selection of malf 8fbc11ef passed, byte for byte.
tp_tmp="$(realpath "$(mktemp -d)")"
mkdir -p "$tp_tmp/bin" "$tp_tmp/empty" "$tp_tmp/one" "$tp_tmp/never" "$tp_tmp/demoted"
: > "$tp_tmp/empty/CTestTestfile.cmake"
printf 'add_test(TheOneCase.Passes true)\n' > "$tp_tmp/one/CTestTestfile.cmake"
: > "$tp_tmp/demoted/CTestTestfile.cmake"
printf 'PKG_BUILD_TESTS:BOOL=OFF\n' > "$tp_tmp/demoted/CMakeCache.txt"
cat > "$tp_tmp/bin/ctest" <<'STUB'
#!/usr/bin/env bash
printf 'ARGV:' >> "$TP_LOG"; printf '[%s]' "$@" >> "$TP_LOG"; printf '\n' >> "$TP_LOG"
for argument in "$@"; do [[ "$argument" == -N ]] && echo "Total Tests: ${TP_TOTAL:-1}"; done
exit 0
STUB
chmod +x "$tp_tmp/bin/ctest"

check "premise — ctest itself exits 0 over a tree that registers no test (the guard's reason)" \
      "0" "$(ctest --test-dir "$tp_tmp/empty" -LE corpus >/dev/null 2>&1; echo $?)"

# <malf> <build_dir> <verbose> <regex> <corpus_only> <sweep or ""> -> the argv the stub recorded, then rc=<status>
tp_run() {
    local log="$tp_tmp/argv.log"; rm -f "$log"
    PATH="$tp_tmp/bin:$PATH" TP_LOG="$log" TP_TOTAL="${TP_TOTAL:-1}" bash -c '
        MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e
        [[ -n "$6" ]] && export MALF_SWEEP=1
        ( _malf_test_run "$2" "$3" "$4" "$5" ) 2>"$7"; echo "rc=$?"' _ "$@" "$tp_tmp/run.err" >> "$log"
    cat "$log"
}
tp_argv() { printf 'ARGV:'; printf '[%s]' "$@"; printf '\n'; }
E="$tp_tmp/empty"; N="$tp_tmp/never"
check "the default population: guarded by -N, then run with --no-tests=error" \
      "$(tp_argv --test-dir "$E" --output-on-failure -LE corpus -N; tp_argv --test-dir "$E" --output-on-failure -LE corpus --no-tests=error; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$E" false "" false "")"
check "--verbose rides after --output-on-failure" \
      "$(tp_argv --test-dir "$E" --output-on-failure --verbose -LE corpus -N; tp_argv --test-dir "$E" --output-on-failure --verbose -LE corpus --no-tests=error; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$E" true "" false "")"
check "a --filter is -R before the label, and is owed outside a sweep" \
      "$(tp_argv --test-dir "$E" --output-on-failure -R 'Suite\..*' -LE corpus -N; tp_argv --test-dir "$E" --output-on-failure -R 'Suite\..*' -LE corpus --no-tests=error; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$E" false 'Suite\..*' false "")"
check "--corpus alone selects the label and owes nothing: no guard, no --no-tests=error" \
      "$(tp_argv --test-dir "$E" --output-on-failure -L corpus; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$E" false "" true "")"
check "a tree that never enabled testing owes nothing" \
      "$(tp_argv --test-dir "$N" --output-on-failure -LE corpus; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$N" false "" false "")"
check "a --filter inside a sweep member owes nothing (the declared bound)" \
      "$(tp_argv --test-dir "$N" --output-on-failure -R 'A.b' -LE corpus; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$N" false 'A.b' false sweep)"
check "a --filter with --corpus outside a sweep is owed even with no CTestTestfile, and an argument with a space stays one argument" \
      "$(tp_argv --test-dir "$N" --output-on-failure --verbose -R 'X y' -L corpus -N; tp_argv --test-dir "$N" --output-on-failure --verbose -R 'X y' -L corpus --no-tests=error; echo rc=0)" \
      "$(tp_run "$MALF_BIN" "$N" true 'X y' true "")"
check "an owed selection of zero tests reds before ctest runs, stating the count and the selection" \
      "$(tp_argv --test-dir "$E" --output-on-failure -LE corpus -N; echo rc=1)|malf test: 0 test(s) selected in $E by: --test-dir $E --output-on-failure -LE corpus" \
      "$(TP_TOTAL=0 tp_run "$MALF_BIN" "$E" false "" false "")|$(head -1 "$tp_tmp/run.err")"
check "a demoted tree's red names the DEPENDENCY configure and its variable" \
      "rc=1 1" "$(TP_TOTAL=0 tp_run "$MALF_BIN" "$tp_tmp/demoted" false "" false "" | tail -1) $(grep -c 'DEPENDENCY (PKG_BUILD_TESTS OFF)' "$tp_tmp/run.err")"
check "with the real ctest: one registered test passes the guard, a regex matching no name reds" \
      "0 1" "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import malf_recipe_tests as t; d = sys.argv[2]
a, e = t.guarded(d, t.selection(d), owed=True); b, f = t.guarded(d, t.selection(d, regex="Nothing\\.Here"), owed=True)
print(0 if e is None and a[-1] == "--no-tests=error" else a, 1 if f is not None else b)' "$MALF_ROOT" "$tp_tmp/one")"
# The selection is READ from the helper, never restated in malf: a copy of malf beside a helper whose
# label is mutated passes the mutated label, so a second spelling in malf would leave this arm red.
mkdir -p "$tp_tmp/mutant"; cp "$MALF_BIN" "$MALF_ROOT/malf_recipe_tests.py" "$tp_tmp/mutant/"
sed -i 's/^CORPUS_LABEL = "corpus"$/CORPUS_LABEL = "kitchen"/' "$tp_tmp/mutant/malf_recipe_tests.py"
check "malf test reads the selection from the helper: a mutated label in the helper is the label ctest gets" \
      "$(tp_argv --test-dir "$N" --output-on-failure -LE kitchen; echo rc=0)" \
      "$(tp_run "$tp_tmp/mutant/malf" "$N" false "" false "")"
check "malf spells no ctest label of its own" \
      "0" "$(grep -cE '(-L|-LE) corpus' "$MALF_BIN")"

# Inside a create. A fixture recipe exports a CTestTestfile.cmake, which conan copies into its build folder (no compiler), and
# calls the helper as every first-party recipe does; the home is staged by malf's own conf sync.
rt_home="$tp_tmp/home"; rt_pkg="$tp_tmp/rt_probe"; mkdir -p "$rt_pkg"
CONAN_HOME="$rt_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$rt_home/profiles/fixture"
cat > "$rt_pkg/conanfile.py" <<'PYR'
import runpy

from conan import ConanFile


class RtProbe(ConanFile):
    name = "rt_probe"
    version = "0.0.1"
    exports_sources = "CTestTestfile.cmake"

    def build(self):
        runpy.run_path(self.conf.get("user.malf:recipe_tests"))["run_tests"](self)
PYR
rt_true="$(type -P true)"; rt_false="$(type -P false)"
rt_tests() {   # <test lines...> — each `name:command[:corpus]`
    local spec name command label
    : > "$rt_pkg/CTestTestfile.cmake"
    for spec in "$@"; do
        IFS=: read -r name command label <<< "$spec"
        printf 'add_test([=[%s]=] "%s")\n' "$name" "$command" >> "$rt_pkg/CTestTestfile.cmake"
        [[ -n "$label" ]] && printf 'set_tests_properties([=[%s]=] PROPERTIES LABELS %s)\n' "$name" "$label" >> "$rt_pkg/CTestTestfile.cmake"
    done
}
rt_create() {   # [conan -c args...] -> rc=<status>; the log in $tp_tmp/rt.log
    CONAN_HOME="$rt_home" conan create "$rt_pkg" -pr:a fixture --build="rt_probe/*" "$@" > "$tp_tmp/rt.log" 2>&1
    echo "rc=$?"
}
# note: the fixtures declare no setting and no option, so their package id is the empty one
rt_empty_id="da39a3ee5e6b4b0d3255bfef95601890afd80709"
rt_junit="$rt_home/malf-test-results/rt_probe/$rt_empty_id.xml"
check "global.conf names the helper and the results directory inside the home, and malf stages the helper there byte for byte" \
      "synced 1 1" "$(cmp -s "$MALF_ROOT/malf_recipe_tests.py" "$rt_home/malf_recipe_tests.py" && echo synced) $(grep -c "^user.malf:recipe_tests={{ os.path.join(conan_home_folder, 'malf_recipe_tests.py')" "$rt_home/global.conf") $(grep -c "^user.malf:test_results={{ os.path.join(conan_home_folder, 'malf-test-results')" "$rt_home/global.conf")"
check "every CI action that stages global.conf stages the test helper beside it" \
      "3" "$(grep -l 'malf_recipe_tests.py' "$MALF_ROOT"/.github/actions/setup-{build-env,proof-linux,proof-msvc}/action.yml | wc -l | tr -d ' ')"
rt_tests "RtProbe.Passes:$rt_true" "RtProbe.CorpusFails:$rt_false:corpus"
check "a create runs the selection: the default population passes, the corpus test is built and never run, and the JUnit file names exactly the test that ran" \
      "rc=0 1 0" "$(rt_create) $(grep -c 'name="RtProbe.Passes"' "$rt_junit" 2>/dev/null) $(grep -c 'RtProbe.CorpusFails' "$rt_junit" 2>/dev/null)"
check "the create's ctest argv is the desk selection plus the guard and --output-junit, nothing else" \
      "1" "$(grep -cE "^\S* ?.*ctest --test-dir \S+ --output-on-failure -LE corpus --no-tests=error --output-junit $rt_junit\$" "$tp_tmp/rt.log")"
rt_tests "RtProbe.Passes:$rt_true" "RtProbe.Fails:$rt_false"
check "a red test FAILS the create, and the log names it" \
      "rc=1 1" "$(rt_create) $(grep -cE '[0-9]+ - RtProbe.Fails \(Failed\)' "$tp_tmp/rt.log")"
rm -f "$rt_junit"
check "under tools.build:skip_test the same red recipe passes, says so, and writes no result" \
      "rc=0 1 absent" "$(rt_create -c tools.build:skip_test=True) $(grep -c 'tools.build:skip_test is set, so no test runs' "$tp_tmp/rt.log") $([[ -e "$rt_junit" ]] && echo present || echo absent)"
rt_tests "RtProbe.CorpusFails:$rt_false:corpus"
check "a create whose tree enabled testing and selects zero tests FAILS, never passes vacuously" \
      "rc=1 1" "$(rt_create) $(grep -c '0 test(s) selected in .* — a create that enabled testing must run at least one test' "$tp_tmp/rt.log")"
rt_tests "RtProbe.Passes:$rt_true"
check "the results directory is the conf's: a create given another one writes there" \
      "rc=0 1" "$(rt_create -c "user.malf:test_results=$tp_tmp/elsewhere") $(grep -c 'name="RtProbe.Passes"' "$tp_tmp/elsewhere/rt_probe/$rt_empty_id.xml" 2>/dev/null)"
mv "$rt_home/malf_recipe_tests.py" "$tp_tmp/helper.bak"
check "a home that lacks the helper FAILS the build, naming the file — it never skips the tests silently" \
      "rc=1 1" "$(rt_create) $(grep -c "No such file or directory: '$rt_home/malf_recipe_tests.py'" "$tp_tmp/rt.log")"
mv "$tp_tmp/helper.bak" "$rt_home/malf_recipe_tests.py"
rm -rf "$tp_tmp"
echo

echo "[9b] a create's two conf sets, its test_package population and the test-locator refusal are malf_recipe_tests.py's, each defined once (DN-142.D13)"

# DN-142.D13 (1): the consumer path runs no test and builds no test_package on conan's DEFAULT graph,
# so it computes the writer's package id; `tools.graph:skip_test` prunes the test requirements and
# gave metalog, eidos and sift another id. (2): a test_package runs in the writer's create through
# the same helper and writes a second result file. (3): a test locates its inputs by a compile
# definition, never by the translation unit's own name, which a create's path map makes an
# identifier. Each is driven through a real `conan create` of a fixture recipe whose build folder
# receives a CTestTestfile.cmake (no compiler), in a home staged by malf's own conf sync.
cs_tmp="$(realpath "$(mktemp -d)")"
check "the consumer conf set is skip_test plus an empty test folder, one argument per line" \
      "-c|tools.build:skip_test=True|--test-folder=" \
      "$(python3 "$MALF_ROOT/malf_recipe_tests.py" create-args consumer | paste -sd'|')"
check "the writer conf set is empty, and an unknown set is a usage error (exit 2)" \
      "rc=0 0 rc=2" \
      "$(out="$(python3 "$MALF_ROOT/malf_recipe_tests.py" create-args writer)"; echo "rc=$? ${#out}") $(python3 "$MALF_ROOT/malf_recipe_tests.py" create-args vendor 2>/dev/null; echo "rc=$?")"
check "no tracked malf file sets tools.graph:skip_test" \
      "0" "$(git -C "$MALF_ROOT" grep -cE 'tools\.graph:skip_test=' -- . ':!tests/test_malf.sh' | awk -F: '{n += $2} END {print n + 0}')"
check "the vendor create reads the consumer set from the helper and spells no skip conf or test folder of its own" \
      "1 0" "$(grep -c 'malf_recipe_tests.py" create-args consumer' "$MALF_ROOT/.github/actions/coderoast-vendor/ci_build_conan_package.sh") $(grep -vE '^\s*#' "$MALF_ROOT/.github/actions/coderoast-vendor/ci_build_conan_package.sh" | grep -cE 'skip_test|--test-folder')"

# The writers read their set from the helper, never spell it: a copy of malf beside a helper whose
# writer set is mutated hands the mutated set to its caller, and both writers splice what it read.
mkdir -p "$cs_tmp/mutant"; cp "$MALF_BIN" "$MALF_ROOT/malf_recipe_tests.py" "$cs_tmp/mutant/"
sed -i 's/^    "writer": (),$/    "writer": ("-c", "user.probe:writer=1"),/' "$cs_tmp/mutant/malf_recipe_tests.py"
check "the writers' conf set is READ from the helper: a mutated set in the helper is the set malf hands its creates" \
      "-c|user.probe:writer=1" \
      "$(bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e; _malf_writer_create_args && printf "%s\n" "${_MALF_WRITER_ARGS[@]}"' _ "$cs_tmp/mutant/malf" | paste -sd'|')"
check "store-create splices the set read, and malf spells no skip conf of its own" \
      "1 0" \
      "$(grep -c -- '-s build_type="$MALF_CONFIG" "${_MALF_WRITER_ARGS\[@\]}" "${build_args\[@\]}"' "$MALF_BIN") $(grep -vE '^\s*#' "$MALF_BIN" | grep -cE 'skip_test=|--test-folder')"

cs_home="$cs_tmp/home"; cs_pkg="$cs_tmp/cs_probe"; mkdir -p "$cs_pkg/test_package" "$cs_pkg/tests"
CONAN_HOME="$cs_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$cs_home/profiles/fixture"
cat > "$cs_pkg/conanfile.py" <<'PYR'
import runpy

from conan import ConanFile


class CsProbe(ConanFile):
    name = "cs_probe"
    version = "0.0.1"
    exports_sources = "CTestTestfile.cmake", "tests/*"

    def build(self):
        runpy.run_path(self.conf.get("user.malf:recipe_tests"))["run_tests"](self)
PYR
cat > "$cs_pkg/test_package/conanfile.py" <<'PYR'
import runpy

from conan import ConanFile


class CsProbeTestPackage(ConanFile):
    test_type = "explicit"

    def requirements(self):
        self.requires(self.tested_reference_str)

    def test(self):
        runpy.run_path(self.conf.get("user.malf:recipe_tests"))["run_test_package"](self)
PYR
cs_true="$(type -P true)"; cs_false="$(type -P false)"
cs_ctest() {   # <file> <name:command>... — a CTestTestfile registering each test
    local file="$1" spec name command; shift
    : > "$file"
    for spec in "$@"; do
        IFS=: read -r name command <<< "$spec"
        printf 'add_test([=[%s]=] "%s")\n' "$name" "$command" >> "$file"
    done
}
cs_create() {   # [conan args...] -> rc=<status>; the log in $cs_tmp/cs.log
    rm -rf "$cs_home/malf-test-results"
    CONAN_HOME="$cs_home" conan create "$cs_pkg" -pr:a fixture --build="cs_probe/*" "$@" > "$cs_tmp/cs.log" 2>&1
    echo "rc=$?"
}
cs_empty_id="da39a3ee5e6b4b0d3255bfef95601890afd80709"   # note: the fixture declares no setting or option
cs_results() {   # -> which result files the create left, by name
    local found
    found="$(ls "$cs_home/malf-test-results/cs_probe" 2>/dev/null | sed "s/^$cs_empty_id/cs_probe/" | paste -sd,)"
    echo "${found:-none}"
}
cs_ctest "$cs_pkg/CTestTestfile.cmake" "CsProbe.Passes:$cs_true"
cs_ctest "$cs_pkg/test_package/CTestTestfile.cmake" "CsProbeTp.Passes:$cs_true" "CsProbeTp.AlsoPasses:$cs_true"
check "the writer's create (no conf) runs the tests and the test_package, and leaves two result files, the second naming the test_package's tests" \
      "rc=0 cs_probe.test_package.xml,cs_probe.xml 2" \
      "$(cs_create) $(cs_results) $(grep -cE 'name="CsProbeTp\.(Passes|AlsoPasses)"' "$cs_home/malf-test-results/cs_probe/$cs_empty_id.test_package.xml" 2>/dev/null)"
mapfile -t cs_consumer < <(python3 "$MALF_ROOT/malf_recipe_tests.py" create-args consumer)
cs_ctest "$cs_pkg/CTestTestfile.cmake" "CsProbe.Fails:$cs_false"
cs_ctest "$cs_pkg/test_package/CTestTestfile.cmake" "CsProbeTp.Fails:$cs_false"
check "the consumer's create passes a red recipe and a red test_package: no test runs, no test_package is built, no result is written" \
      "rc=0 1 0 none" \
      "$(cs_create "${cs_consumer[@]}") $(grep -c 'tools.build:skip_test is set, so no test runs in this build' "$cs_tmp/cs.log") $(grep -c 'cs_probe/0.0.1 (test package)' "$cs_tmp/cs.log") $(cs_results)"
cs_ctest "$cs_pkg/CTestTestfile.cmake" "CsProbe.Passes:$cs_true"
check "a red test_package test FAILS the writer's create, after the package's own result was written" \
      "rc=1 1 cs_probe.test_package.xml,cs_probe.xml" \
      "$(cs_create) $(grep -cE '[0-9]+ - CsProbeTp.Fails \(Failed\)' "$cs_tmp/cs.log") $(cs_results)"
check "under tools.build:skip_test alone the test_package is built and its red test does not run, saying so, and nothing is written" \
      "rc=0 1 none" \
      "$(cs_create -c tools.build:skip_test=True) $(grep -c 'tools.build:skip_test is set, so no test_package test runs' "$cs_tmp/cs.log") $(cs_results)"
cs_ctest "$cs_pkg/test_package/CTestTestfile.cmake"
check "a test_package that registers no test FAILS the writer's create, never passes vacuously" \
      "rc=1 1" "$(cs_create) $(grep -c 'a test_package must run at least one test' "$cs_tmp/cs.log")"
cs_ctest "$cs_pkg/test_package/CTestTestfile.cmake" "CsProbeTp.Passes:$cs_true"

# DN-142.D13 (3). The three spellings of a translation unit's own name, each a red in a test source,
# none in a comment or a library source, and the compile-definition form green.
cs_tree="$cs_tmp/locators"; mkdir -p "$cs_tree/tests/deep" "$cs_tree/src" "$cs_tree/tests_support" "$cs_tree/test_package" "$cs_tree/build-x/tests"
printf 'const auto here = std::filesystem::path{__FILE__};\n' > "$cs_tree/tests/a.cpp"
printf 'int x;\nconst char* here = __builtin_FILE ();\n' > "$cs_tree/tests/deep/b.hpp"
printf '#include <source_location>\nauto f = std::source_location::current().file_name();\n' > "$cs_tree/tests_support/c.cpp"
printf 'auto g = __FILE__;\n' > "$cs_tree/test_package/d.cpp"
printf '// note: located through a compile definition, never __FILE__\n/* nor __builtin_FILE() */\nconst auto dir = std::filesystem::path{CS_PROBE_DATA_DIR};\n' > "$cs_tree/tests/e.cpp"
printf '#define LOG(x) log((x), __FILE__, __LINE__)\n' > "$cs_tree/src/log.hpp"
printf 'auto name = entry.file_name();\n' > "$cs_tree/tests/f.cpp"
printf 'auto h = __FILE__;\n' > "$cs_tree/build-x/tests/g.cpp"
printf 'auto h = __FILE__;\n' > "$cs_tree/tests/notes.txt"
check "locators: each spelling in a tests/, tests_support/ or test_package/ C++ source is one finding with its line; a comment, a library source, a file_name() in a file that never names source_location, a build tree and a non-C++ file are none" \
      "rc=1 $cs_tree/test_package/d.cpp:1: __FILE__|$cs_tree/tests/a.cpp:1: __FILE__|$cs_tree/tests/deep/b.hpp:2: __builtin_FILE()|$cs_tree/tests_support/c.cpp:2: std::source_location::file_name()" \
      "$(out="$(python3 "$MALF_ROOT/malf_recipe_tests.py" locators "$cs_tree" 2>/dev/null)"; echo "rc=$? $(paste -sd'|' <<< "$out")")"
check "locators: a tree with none exits 0, a path that is no directory exits 2" \
      "rc=0 rc=2" "$(python3 "$MALF_ROOT/malf_recipe_tests.py" locators "$cs_tree/src" > /dev/null 2>&1; echo "rc=$?") $(python3 "$MALF_ROOT/malf_recipe_tests.py" locators "$cs_tree/nowhere" > /dev/null 2>&1; echo "rc=$?")"
printf 'const auto here = std::filesystem::path{__FILE__}.parent_path();\n' > "$cs_pkg/tests/probe.cpp"
check "a writer's create whose exported test source spells __FILE__ FAILS before any test runs, naming the site" \
      "rc=1 1 1 none" \
      "$(cs_create) $(grep -c "tests/probe.cpp:1: __FILE__" "$cs_tmp/cs.log") $(grep -c 'locate a file through the translation unit' "$cs_tmp/cs.log") $(cs_results)"
check "the consumer's create of the same recipe passes: the check guards a run of the tests, and no test runs there" \
      "rc=0" "$(cs_create "${cs_consumer[@]}")"
printf 'const auto here = std::filesystem::path{CS_PROBE_DATA_DIR};\n' > "$cs_pkg/tests/probe.cpp"
printf 'auto g = __FILE__;\n' > "$cs_pkg/test_package/probe.cpp"
check "a test_package source spelling __FILE__ FAILS the writer's create, whatever directory it sits in" \
      "rc=1 1" "$(cs_create) $(grep -c "probe.cpp:1: __FILE__" "$cs_tmp/cs.log")"
rm -f "$cs_pkg/test_package/probe.cpp"
check "with every spelling gone the writer's create passes again" "rc=0" "$(cs_create)"

# DN-142.D14: a `stored: false` package is never created — its step is a conan build that stores no
# package, which store-create's walk and store-build run through one function — and store-build
# takes nothing else. The refusal is driven over a fixture workspace whose version_line answers the
# two lists and a conan stub that records every call it gets.
sf_ws="$cs_tmp/sfws"; mkdir -p "$sf_ws/scripts" "$cs_tmp/sfbin"
cat > "$sf_ws/scripts/version_line.py" <<'PYV'
import sys
print({"released": "rel_pkg\t/nowhere\t", "unstored": "leaf_pkg\t/nowhere/leaf"}[sys.argv[1]])
PYV
printf '#!/usr/bin/env bash\necho "$*" >> %s/sf-conan.log\nexit 0\n' "$cs_tmp" > "$cs_tmp/sfbin/conan"; chmod +x "$cs_tmp/sfbin/conan"
sf_malf() {   # <verb> <package> -> rc=<status> and the refusal's DN-142.D14 line count
    local out rc
    out="$(cd "$cs_tmp" && PATH="$cs_tmp/sfbin:$PATH" MALF_WORKSPACE_ROOT="$sf_ws" CONAN_HOME="$cs_tmp/sfhome" bash "$MALF_BIN" "$1" "$2" 2>&1)"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -c 'DN-142.D14' <<< "$out")"
}
# The merged act's walk (DN-142.D5 (7) M2): malf_graph's steps in walk order, a `stored: false`
# step marked `build` (a conan build, never a create), every other `create`; named packages select
# their closure through every first-party requirement, test ones included; an unknown name refuses.
sf_walk() {   # [<package>...] -> the walk rows, `|`-joined
    bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e; shift
        _malf_store_walk "$(printf "a_core/1.0\t/w/a\t\nb_lib/1.0\t/w/b\ta_core\nc_twin/1.0\t/w/c\ta_core,b_lib\nd_app/1.0\t/w/d\tb_lib\ne_side/1.0\t/w/e\t\n")" \
                         "$(printf "c_twin\t/w/c\n")" "$@"' _ "$MALF_BIN" "$@" 2>&1 | paste -sd'|'
}
check "the walk keeps malf's order, marks the stored: false step build and every other create, and carries each step's edges" \
      "$(printf 'a_core\t/w/a\t\tcreate|b_lib\t/w/b\ta_core\tcreate|c_twin\t/w/c\ta_core,b_lib\tbuild|d_app\t/w/d\tb_lib\tcreate|e_side\t/w/e\t\tcreate')" \
      "$(sf_walk)"
check "named packages select their closure in walk order; a name that is no step refuses, naming it" \
      "$(printf 'a_core\t/w/a\t\tcreate|b_lib\t/w/b\ta_core\tcreate|d_app\t/w/d\tb_lib\tcreate')|malf store-create: no step of malf's walk is named zz_none" \
      "$(sf_walk d_app)|$(sf_walk zz_none)"
check "the act never conan-creates a step it marked build: the create branch is the only create, and a build step goes to store-build's one function" \
      "1 1 0" "$(grep -c 'if \[\[ "$kind" == "build" \]\]; then' "$MALF_BIN") $(awk '/^cmd_store_create\(\)/,/^}/' "$MALF_BIN" | grep -c '^ *if ! _malf_store_create_step ') $(awk '/^cmd_store_create\(\)/,/^}/' "$MALF_BIN" | grep -cE '^ *(if ! )?conan create ')"
check "store-build REFUSES a package that is stored, naming store-create, before conan is ever called" \
      "rc=2 1 none" "$(sf_malf store-build rel_pkg) $([[ -s "$cs_tmp/sf-conan.log" ]] && echo called || echo none)"

# The CI module script bypasses malf and reads the one selection too (DN-142.D5 (5)): it asks the
# helper's desk-args for its ctest argv and spells no label of its own.
cm_script="$MALF_ROOT/.github/actions/conan-module/conan_module.sh"
check "conan_module.sh reads its ctest selection from the helper and spells no ctest label of its own" \
      "1 0" "$(grep -c 'malf_recipe_tests.py" desk-args' "$cm_script") $(grep -vE '^\s*#' "$cm_script" | grep -cE '(-L|-LE) corpus')"

# DN-142.D5 (4): a test input outside the recipe folder is exported by the helper, tracked files only.
ex_repo="$cs_tmp/exrepo"; mkdir -p "$ex_repo/pkg" "$ex_repo/support/sub" "$ex_repo/scripts"
git -C "$ex_repo" init -q
printf 'tracked\n' > "$ex_repo/support/sub/kept.hpp"; printf 'stray\n' > "$ex_repo/support/stray.hpp"
printf '#!/bin/sh\n' > "$ex_repo/scripts/driver.sh"; chmod +x "$ex_repo/scripts/driver.sh"
printf 'other\n' > "$ex_repo/scripts/other.sh"
mkdir -p "$ex_repo/empty"; printf 'untracked\n' > "$ex_repo/empty/only.txt"
cat > "$ex_repo/pkg/conanfile.py" <<'PYR'
import os
import runpy

from conan import ConanFile


class ExProbe(ConanFile):
    name = "ex_probe"
    version = "0.0.1"
    exports_sources = "conanfile.py"

    def export_sources(self):
        helper = runpy.run_path(self.conf.get("user.malf:recipe_exports"))
        helper["narrow_to_tracked"](self)
        helper["export_tracked"](self, os.environ["EX_SOURCE"], os.environ["EX_DEST"])
PYR
git -C "$ex_repo" add pkg/conanfile.py support/sub/kept.hpp scripts/driver.sh scripts/other.sh
git -C "$ex_repo" -c user.name=t -c user.email=t@invalid commit -qm fixture
ex_export() {   # <source> <destination> -> rc=<status> then the exported files under the destination
    local out rc folder
    out="$(EX_SOURCE="$1" EX_DEST="$2" CONAN_HOME="$cs_home" conan export "$ex_repo/pkg" 2>&1)"; rc=$?
    folder="$(CONAN_HOME="$cs_home" conan cache path ex_probe/0.0.1 --folder export_source 2>/dev/null)"
    printf 'rc=%s %s' "$rc" "$( (cd "$folder/$2" 2>/dev/null && find . -type f | sort | paste -sd,) )"
    [[ $rc -eq 0 ]] || grep -m1 -oE "git tracks no file there|does not exist" <<< "$out" | sed 's/^/ /'
}
check "export_tracked copies the TRACKED files under a directory outside the recipe folder, never an untracked one" \
      "rc=0 ./sub/kept.hpp" "$(ex_export ../support support)"
check "export_tracked copies one tracked file to the path named, keeping its executable bit, and nothing beside it" \
      "rc=0 ./scripts/driver.sh x" \
      "$(out="$(EX_SOURCE=../scripts/driver.sh EX_DEST=scripts/driver.sh CONAN_HOME="$cs_home" conan export "$ex_repo/pkg" 2>&1)"; rc=$?
         folder="$(CONAN_HOME="$cs_home" conan cache path ex_probe/0.0.1 --folder export_source)"
         printf 'rc=%s %s' "$rc" "$( (cd "$folder" && find . -type f -path './scripts/*' | sort | paste -sd,) )"
         [[ -x "$folder/scripts/driver.sh" ]] && printf ' x')"
check "export_tracked FAILS the export on a source holding no tracked file, and on a source that does not exist" \
      "rc=1  git tracks no file there|rc=1  does not exist" \
      "$(ex_export ../empty empty)|$(ex_export ../absent absent)"
rm -rf "$cs_tmp"
echo

echo "[9c] every linked ELF file a first-party package installs names itself by a GNU build-id, and the store indexes it from the stored bytes (DN-142.D15)"

# A binary carries no git state, path or time: it names itself by the NT_GNU_BUILD_ID note the
# linker writes over its own bytes (malf-path-map.cmake links every executable and shared object
# with --build-id=sha1), and the store's record indexes every linked ELF file of a package by it,
# READ FROM THE STORED TRANSPORT. Driven with no compiler: the fixture package installs a copy of
# the host's `true` (which carries a note) or the same copy with its note's type zeroed.
bi_tmp="$(realpath "$(mktemp -d)")"
check "malf's toolchain fragment links every target with --build-id=sha1, as a directory link option (which reaches an existing tree)" \
      "1" "$(grep -c '^add_link_options("LINKER:--build-id=sha1")$' "$MALF_ROOT/cmake/malf-path-map.cmake")"
# A build inside the conan home (a create) links with its install RPATH, so no install-time RUNPATH
# rewrite zero-fills a home path the linker already hashed into the build-id; a tree outside it (the
# desk's) keeps the build-tree RUNPATH. Driven by configuring a LANGUAGES NONE project through a copy
# of the fragment staged in a fixture home, once with its build tree inside that home and once outside.
bi_rpath() {   # <build dir> -> the CMAKE_BUILD_WITH_INSTALL_RPATH the fragment left
    mkdir -p "$bi_tmp/proj" "$bi_tmp/fhome"
    cp "$MALF_ROOT/cmake/malf-path-map.cmake" "$bi_tmp/fhome/malf-path-map.cmake"
    printf 'cmake_minimum_required(VERSION 3.28)\nproject(p LANGUAGES NONE)\nmessage(STATUS "RPATH_MODE=[${CMAKE_BUILD_WITH_INSTALL_RPATH}]")\n' > "$bi_tmp/proj/CMakeLists.txt"
    cmake -S "$bi_tmp/proj" -B "$1" -DCMAKE_TOOLCHAIN_FILE="$bi_tmp/fhome/malf-path-map.cmake" 2>&1 | sed -n 's/^-- RPATH_MODE=\[\(.*\)\]$/\1/p'
}
check "a build tree inside the conan home links with its install RPATH; one outside keeps the build-tree RUNPATH" \
      "ON|" "$(bi_rpath "$bi_tmp/fhome/p/b/pkg/b")|$(bi_rpath "$bi_tmp/desk/build")"
bi_true="$(type -P true)"
bi_py() { python3 -c "import sys; sys.path.insert(0, '$MALF_ROOT'); import artefact_store as a; $1"; }
bi_expected="$(readelf -n "$bi_true" | sed -n 's/^ *Build ID: //p')"
check "premise — the host's true carries a build-id readelf reads, 40 hex" \
      "40" "$(printf '%s' "$bi_expected" | wc -c | tr -d ' ')"
check "the reader parses the note through the program headers and agrees with readelf (a second producer)" \
      "$bi_expected" "$(bi_py "print(a.elf_build_id(open('$bi_true', 'rb').read()))")"
python3 - "$bi_true" "$bi_tmp/true-no-note" <<'PYZ'
import struct, sys
data = bytearray(open(sys.argv[1], "rb").read())
order = "<" if data[5] == 1 else ">"
phoff = struct.unpack_from(order + "Q", data, 32)[0]
phentsize, phnum = struct.unpack_from(order + "HH", data, 54)
for index in range(phnum):
    base = phoff + index * phentsize
    if struct.unpack_from(order + "I", data, base)[0] != 4:
        continue
    offset, size = struct.unpack_from(order + "Q", data, base + 8)[0], struct.unpack_from(order + "Q", data, base + 32)[0]
    cursor = offset
    while cursor + 12 <= offset + size:
        namesz, descsz, kind = struct.unpack_from(order + "III", data, cursor)
        if kind == 3:
            struct.pack_into(order + "I", data, cursor + 8, 0)
        cursor += 12 + ((namesz + 3) & ~3) + ((descsz + 3) & ~3)
open(sys.argv[2], "wb").write(data)
PYZ
chmod +x "$bi_tmp/true-no-note"
check "a linked ELF file whose note is gone is NoBuildId; a non-ELF file is not linked ELF at all" \
      "NoBuildId None" \
      "$(bi_py "
try:
    a.elf_build_id(open('$bi_tmp/true-no-note', 'rb').read()); print('found', end=' ')
except a.NoBuildId:
    print('NoBuildId', end=' ')
print(a.elf_build_id(b'#!/bin/sh\n'))")"

# A fixture first-party package (prefix bi_) installing one of the two copies, created in a home
# malf's conf sync staged, then its record written by conan-step into a fixture store.
bi_home="$bi_tmp/home"; bi_pkg="$bi_tmp/bi_tool"; mkdir -p "$bi_pkg"
CONAN_HOME="$bi_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$bi_home/profiles/fixture"
cat > "$bi_pkg/conanfile.py" <<'PYR'
import os
import shutil

from conan import ConanFile


class BiTool(ConanFile):
    name = "bi_tool"
    version = "0.0.1"
    exports_sources = "tool", "notes.txt"

    def package(self):
        os.makedirs(os.path.join(self.package_folder, "bin"))
        shutil.copy2(os.path.join(self.source_folder, "tool"), os.path.join(self.package_folder, "bin", "tool"))
        shutil.copy2(os.path.join(self.source_folder, "notes.txt"), self.package_folder)
PYR
printf 'not an ELF file\n' > "$bi_pkg/notes.txt"
printf '{}' > "$bi_tmp/toolchain.json"
bi_step() {   # <tool file> <store> [profile] -> rc=<status>; the step's output in $bi_tmp/step.log
    cp "$1" "$bi_pkg/tool"
    CONAN_HOME="$bi_home" conan create "$bi_pkg" -pr:a fixture --build="bi_tool/*" --format=json > "$bi_tmp/graph.json" 2> "$bi_tmp/create.log" || { echo "rc=create-failed"; return; }
    CONAN_HOME="$bi_home" python3 "$MALF_ROOT/artefact_store.py" conan-step "$2" "$bi_home" "$bi_tmp/graph.json" bi_tool "${3:-fixture}" "$MALF_ROOT" "$bi_tmp/toolchain.json" "bi_" > "$bi_tmp/step.log" 2>&1
    echo "rc=$?"
}
check "a package whose linked ELF file carries no build-id is REFUSED by the step, naming the file, and nothing is stored" \
      "rc=1 1 0" "$(bi_step "$bi_tmp/true-no-note" "$bi_tmp/store") $(grep -c 'REFUSED bi_tool: 1 linked ELF file(s) carry no GNU build-id.*bin/tool' "$bi_tmp/step.log") $(ls "$bi_tmp/store/records" 2>/dev/null | wc -l | tr -d ' ')"
check "a package whose linked ELF files all carry one is STORED, its record's build_ids naming each, and only those" \
      "rc=0 {\"bin/tool\": \"$bi_expected\"}" \
      "$(bi_step "$bi_true" "$bi_tmp/store") $(python3 -c "import json, glob; print(json.dumps(json.load(open(glob.glob('$bi_tmp/store/records/*.json')[0]))['outputs']['package']['build_ids']))")"
check "the record's build_ids are the stored transport's, read from the object the record names" \
      "same" \
      "$(bi_py "
import json, glob
from pathlib import Path
body = json.load(open(glob.glob('$bi_tmp/store/records/*.json')[0]))['outputs']['package']
ids, missing = a.build_ids_of(a.transport_files(Path('$bi_tmp/store/objects') / body['transport']))
print('same' if ids == body['build_ids'] and not missing else (ids, body['build_ids']))")"
check "a build_ids value handed in with the outputs is never kept: the record holds what the stored object says" \
      "derived" \
      "$(bi_py "
import json
from pathlib import Path
store = a.LocalStore(Path('$bi_tmp/store2'))
probe = Path('$bi_tmp/probe.bin'); probe.write_bytes(b'probe')
store.commit({'step': {'package': 'bi_probe'}, 'n': 1},
             {'package': {'alias': 'x', 'content': 'c', 'entries': [], 'build_ids': {'bin/forged': 'f' * 40}}},
             {'package': ('transport', lambda: probe)}, derived={'package': lambda obj: {'build_ids': {'from': obj.name}}})
body = json.load(open(next(Path('$bi_tmp/store2/records').glob('*.json'))))
print('derived' if body['outputs']['package']['build_ids'] == {'from': body['outputs']['package']['transport']} else body)" 2>&1 | tail -1)"
# The step's TEST VERDICT record (DN-142.D5 (3)): keyed by the build step's key and the selection,
# holding both populations' result set, the digests judged and the logs as an object; an equal key
# rebuilt compares the result set, and a step that wrote no result file has no verdict.
bi_junit() {   # <file> <name:outcome>... — a JUnit file of malf's helper's shape
    { printf '<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="x" tests="%s">\n' "$(($# - 1))"
      local spec; for spec in "${@:2}"; do
          case "${spec#*:}" in
              failed) printf '<testcase name="%s" status="run"><failure message="x"/></testcase>\n' "${spec%%:*}" ;;
              *) printf '<testcase name="%s" status="run"/>\n' "${spec%%:*}" ;;
          esac
      done; printf '</testsuite>\n'; } > "$1"
}
bi_verdict() {   # <results dir> -> rc=<status> and the step output's verdict lines
    CONAN_HOME="$bi_home" python3 "$MALF_ROOT/artefact_store.py" conan-step "$bi_tmp/vstore" "$bi_home" "$bi_tmp/graph.json" bi_tool fixture "$MALF_ROOT" "$bi_tmp/toolchain.json" "bi_" "$1" > "$bi_tmp/v.log" 2>&1
    printf 'rc=%s %s' "$?" "$(grep -oE '(STORED|MATCH|MISMATCH|NO VERDICT) bi_tool( verdict)?' "$bi_tmp/v.log" | paste -sd,)"
}
bi_empty_id="da39a3ee5e6b4b0d3255bfef95601890afd80709"
mkdir -p "$bi_tmp/res/bi_tool" "$bi_tmp/res/bi_tool.other"
bi_junit "$bi_tmp/res/bi_tool/$bi_empty_id.xml" BiTool.One:passed BiTool.Two:passed
bi_junit "$bi_tmp/res/bi_tool/$bi_empty_id.test_package.xml" BiToolTp.Links:passed
bi_junit "$bi_tmp/res/bi_tool/$(printf 'f%.0s' {1..40}).xml" BiTool.Variant:passed
check "a create's step with result files stores its package record AND a verdict record of both populations, judging the package's content digest" \
      "rc=0 STORED bi_tool,STORED bi_tool verdict [[\"build\", \"BiTool.One\", \"passed\"], [\"build\", \"BiTool.Two\", \"passed\"], [\"test_package\", \"BiToolTp.Links\", \"passed\"]] same" \
      "$(bi_verdict "$bi_tmp/res") $(python3 -c "
import json, glob
recs = [json.load(open(r)) for r in glob.glob('$bi_tmp/vstore/records/*.json')]
verdict = [r for r in recs if r['inputs']['step']['kind'] == 'test-verdict'][0]
package = [r for r in recs if r['inputs']['step']['kind'] == 'conan-create'][0]
print(json.dumps(verdict['outputs']['verdict']['results']), 'same' if verdict['outputs']['verdict']['judged'] == {'bi_tool': package['outputs']['package']['content']} and verdict['inputs']['build'] == package['key'] else 'differ')")"
check "the verdict reads the step's own binary's results, never a variant's under another package id, and links them under steps/" \
      "0 2 1" \
      "$(grep -c 'BiTool.Variant' "$bi_tmp/res/steps/bi_tool.xml") $(grep -c 'BiTool\.\(One\|Two\)' "$bi_tmp/res/steps/bi_tool.xml") $(grep -c 'BiToolTp.Links' "$bi_tmp/res/steps/bi_tool.test_package.xml")"
check "the same step again matches both records" "rc=0 MATCH bi_tool,MATCH bi_tool verdict" "$(bi_verdict "$bi_tmp/res")"
bi_junit "$bi_tmp/res/bi_tool/$bi_empty_id.xml" BiTool.One:passed BiTool.Renamed:passed
check "a rebuilt step at an equal key whose result set differs is a verdict MISMATCH (exit 1) naming the tests" \
      "rc=1 MATCH bi_tool,MISMATCH bi_tool verdict 1" \
      "$(bi_verdict "$bi_tmp/res") $(grep -c 'MISMATCH bi_tool verdict at an equal key .* 2 test(s): build:BiTool.Renamed, build:BiTool.Two' "$bi_tmp/v.log")"
mkdir -p "$bi_tmp/none"
check "a step that wrote no result file stores no verdict, saying so" \
      "rc=0 MATCH bi_tool,NO VERDICT bi_tool" "$(bi_verdict "$bi_tmp/none")"
# A content recipe (`package_type = "build-scripts"`) is created in the BUILD context alone; its
# step is recorded all the same (measured on insight_scenarios: the graph holds no host node).
bi_data="$bi_tmp/bi_data"; mkdir -p "$bi_data"; printf 'scenario\n' > "$bi_data/notes.txt"
cat > "$bi_data/conanfile.py" <<'PYR'
import os
import shutil

from conan import ConanFile


class BiData(ConanFile):
    name = "bi_data"
    version = "0.0.1"
    package_type = "build-scripts"
    exports_sources = "notes.txt"

    def package(self):
        shutil.copy2(os.path.join(self.source_folder, "notes.txt"), self.package_folder)
PYR
check "a content recipe created in the build context alone is recorded, its node found in that context" \
      "rc=0 1" \
      "$(CONAN_HOME="$bi_home" conan create "$bi_data" -pr:a fixture --build="bi_data/*" --format=json > "$bi_tmp/data.json" 2>/dev/null; CONAN_HOME="$bi_home" python3 "$MALF_ROOT/artefact_store.py" conan-step "$bi_tmp/dstore" "$bi_home" "$bi_tmp/data.json" bi_data fixture "$MALF_ROOT" "$bi_tmp/toolchain.json" "bi_" > "$bi_tmp/d.log" 2>&1; echo "rc=$?") $(grep -c 'STORED bi_data package' "$bi_tmp/d.log")"
cp "$bi_home/profiles/fixture" "$bi_home/profiles/fixture-b"
check "two records holding the same bytes under two keys both answer the build-id lookup, and an unknown id answers nothing (exit 1)" \
      "rc=0 2 rc=1" \
      "$(bi_step "$bi_true" "$bi_tmp/store" fixture-b | tr -d '\n') $(python3 "$MALF_ROOT/artefact_store.py" build-id "$bi_tmp/store" "$bi_expected" | grep -c ' bi_tool/0.0.1.* bin/tool$') $(python3 "$MALF_ROOT/artefact_store.py" build-id "$bi_tmp/store" "$(printf '0%.0s' {1..40})" > /dev/null; echo "rc=$?")"
rm -rf "$bi_tmp"
echo

echo "[7q3] a build SWEEP settles every member in target role and recomposes the repo database"

# note: a member's dependency bootstrap re-configures an earlier member with tests OFF, and a root
# recipe's tree IS the repo database — measured on insight-eidos 2026-09-21: 79 of 244 TUs covered.
# note: driven end to end through the real sweep with conan and cmake stubbed; the stub cmake writes
# a CMakeCache and a database whose tests/ entries exist only when a *_BUILD_TESTS define is ON.
sw_tmp="$(realpath "$(mktemp -d)")"
sw_bin="$sw_tmp/bin"; mkdir -p "$sw_bin" "$sw_tmp/ws"
cat > "$sw_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do
    [[ "$prev" == "-of" ]] && out="$a"
    [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"
    prev="$a"
done
if [[ "$1" == install && -n "$out" ]]; then
    mkdir -p "$out" && printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
fi
exit 0
STUB
cat > "$sw_bin/cmake" <<'STUB'
#!/usr/bin/env bash
src=""; build=""; prev=""; tests=OFF; defs=()
for a in "$@"; do
    [[ "$a" == --build ]] && exit 0
    case "$prev" in -S) src="$a" ;; -B) build="$a" ;; esac
    case "$a" in
        -D*BUILD_TESTS*=*) tests="${a##*=}"; defs+=("${a#-D}") ;;
        -D*BUILD_BENCH*=*) defs+=("${a#-D}") ;;
    esac
    prev="$a"
done
[[ -n "$src" && -n "$build" ]] || exit 0
mkdir -p "$build"
{ echo "CMAKE_BUILD_TYPE:STRING=Release"; for d in "${defs[@]}"; do echo "${d%%=*}:BOOL=${d#*=}"; done; } > "$build/CMakeCache.txt"
python3 - "$src" "$build" "$tests" <<'PY'
import json, pathlib, sys
src, build, tests = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "ON"
tus = sorted(src.glob("src/*.cpp")) + (sorted(src.glob("tests/*.cpp")) if tests else [])
json.dump([{"directory": build, "command": f"clang++-21 -c {t}", "file": str(t)} for t in tus],
          open(f"{build}/compile_commands.json", "w"))
PY
STUB
chmod +x "$sw_bin/conan" "$sw_bin/cmake"
sw_pkg() {   # <repo> <subdir> <name> [<requires>]
    local d="$sw_tmp/ws/$1/$2"
    mkdir -p "$d/src" "$d/tests"
    printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "%s"\n    version = "1.0"\n' "$3" > "$d/conanfile.py"
    [[ -n "${4:-}" ]] && printf '    requires = "%s"\n' "$4" >> "$d/conanfile.py"
    printf 'option(%s_BUILD_TESTS "t" ON)\n' "${3^^}" > "$d/CMakeLists.txt"
    printf 'int %s() { return 0; }\n' "$3" > "$d/src/$3.cpp"
    printf 'int test_%s() { return 0; }\n' "$3" > "$d/tests/test_$3.cpp"
}
# repo `rooted`: a root recipe two sub-recipes require (the insight-eidos shape).
sw_pkg rooted . sw_root; sw_pkg rooted sub sw_sub sw_root/1.0; sw_pkg rooted leaf sw_leaf sw_root/1.0
# repo `flat`: no root recipe, so its database is only ever merged into (the accumulation shape).
sw_pkg flat a sw_a; sw_pkg flat b sw_b sw_a/1.0
git -C "$sw_tmp/ws/rooted" init -q; git -C "$sw_tmp/ws/flat" init -q
sw_key="${MALF_DEFAULT_PROFILE#linux-}"
mkdir -p "$sw_tmp/ws/flat/build-$sw_key"
printf '[{"directory": "%s", "command": "clang++-21 -c gone.cpp", "file": "%s/gone/deleted.cpp"}]\n' \
    "$sw_tmp/ws/flat/build-$sw_key" "$sw_tmp/ws/flat" > "$sw_tmp/ws/flat/build-$sw_key/compile_commands.json"
sw_build() {   # <repo> -> the sweep's exit status, output in $sw_tmp/<repo>.log
    (cd "$sw_tmp/ws/$1" && PATH="$sw_bin:$PATH" MALF_WORKSPACE_ROOT="$sw_tmp/ws" MALF_SKIP_INVENTORY=1 \
        MALF_PROFILE_NAME="" bash "$MALF_BIN" build > "$sw_tmp/$1.log" 2>&1; echo $?)
}
sw_files() {   # <repo> — the repo database's files, repo-relative, sorted
    python3 -c "import json,sys; print(' '.join(sorted(e['file'].split('/$1/',1)[1] for e in json.load(open(sys.argv[1])))))" \
        "$sw_tmp/ws/$1/build-$sw_key/compile_commands.json" 2>&1
}
sw_role() { _malf_commands_tree_role "$1"; echo "$_MALF_TREE_ROLE"; }

sw_rc="$(sw_build rooted)"
check "a rooted sweep's database holds every member's TUs, tests included — the root's are the ones lost" \
      "rc=0 leaf/src/sw_leaf.cpp leaf/tests/test_sw_leaf.cpp src/sw_root.cpp sub/src/sw_sub.cpp sub/tests/test_sw_sub.cpp tests/test_sw_root.cpp" \
      "rc=$sw_rc $(sw_files rooted)"
check "the root recipe's tree ends the sweep in TARGET role, read back from its own CMakeCache" \
      "target" "$(sw_role "$sw_tmp/ws/rooted/build-$sw_key")"
sw_rc="$(sw_build flat)"
check "a flat sweep's database is recomposed, not accumulated — a stale entry for a deleted TU is gone" \
      "rc=0 a/src/sw_a.cpp a/tests/test_sw_a.cpp b/src/sw_b.cpp b/tests/test_sw_b.cpp" \
      "rc=$sw_rc $(sw_files flat)"
check "a member another member's bootstrap demoted ends the sweep in TARGET role" \
      "target" "$(sw_role "$sw_tmp/ws/flat/a/build-$sw_key")"
rm -rf "$sw_tmp"
echo

echo "[7q14] malf commands folds every built inventory cell's database, after the members"

# note: `malf commands` cleared the root database and merged member and dependency trees only, so
# after a `malf build` it dropped every cell-only TU — insight-metalog 2026-10-06: 125 entries with
# scripts/determinism_fixture.cpp became 127 without it, and lint --all-files refused on the hole.
# note: driven end to end through the real verb with conan and cmake stubbed; the cell's database is
# the one a prior `malf build` left, and it also carries the member's TU under the cell's own flags.
ic_tmp="$(realpath "$(mktemp -d)")"
ic_bin="$ic_tmp/bin"; ic_repo="$ic_tmp/ws/repo"; mkdir -p "$ic_bin" "$ic_repo/pkg/src" "$ic_repo/cell"
ic_key="${MALF_DEFAULT_PROFILE#linux-}"
cat > "$ic_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""
for a in "$@"; do [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"; done
if [[ "$1" == install && -n "$out" ]]; then
    mkdir -p "$out" && printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
fi
exit 0
STUB
cat > "$ic_bin/cmake" <<'STUB'
#!/usr/bin/env bash
src=""; prev=""; preset=false
for a in "$@"; do
    [[ "$a" == --preset ]] && preset=true
    [[ "$prev" == -S ]] && src="$a"
    prev="$a"
done
$preset && [[ -n "$src" ]] || exit 0
build="$src/build-$IC_KEY"; mkdir -p "$build"
echo "CMAKE_BUILD_TYPE:STRING=Release" > "$build/CMakeCache.txt"
printf '[{"directory": "%s", "command": "member-flags -c %s/src/lib.cpp", "file": "%s/src/lib.cpp"}]\n' \
    "$build" "$src" "$src" > "$build/compile_commands.json"
STUB
chmod +x "$ic_bin/conan" "$ic_bin/cmake"
printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "ic_pkg"\n    version = "1.0"\n' > "$ic_repo/pkg/conanfile.py"
printf 'project(ic_pkg)\n' > "$ic_repo/pkg/CMakeLists.txt"
printf 'int lib() { return 0; }\n' > "$ic_repo/pkg/src/lib.cpp"
printf 'int main() { return 0; }\n' > "$ic_repo/cell/fixture.cpp"
ic_cell="$ic_repo/cell/build-inventory-$ic_key"; mkdir -p "$ic_cell"
printf '[{"directory": "%s", "command": "cell-flags -c %s", "file": "%s"},\n {"directory": "%s", "command": "cell-flags -c %s", "file": "%s"}]\n' \
    "$ic_cell" "$ic_repo/cell/fixture.cpp" "$ic_repo/cell/fixture.cpp" \
    "$ic_cell" "$ic_repo/pkg/src/lib.cpp" "$ic_repo/pkg/src/lib.cpp" > "$ic_cell/compile_commands.json"
# A cell tree of ANOTHER profile is not this run's subject, and folding it would mix configurations.
mkdir -p "$ic_repo/cell/build-inventory-other-profile"
printf '[{"directory": "x", "command": "other-flags", "file": "%s/cell/other.cpp"}]\n' "$ic_repo" \
    > "$ic_repo/cell/build-inventory-other-profile/compile_commands.json"
git -C "$ic_repo" init -q
ic_out="$(cd "$ic_repo" && PATH="$ic_bin:$PATH" IC_KEY="$ic_key" MALF_WORKSPACE_ROOT="$ic_tmp/ws" \
    MALF_AUTO_WORKSPACE_DEPS=0 MALF_PROFILE_NAME="" setsid timeout --kill-after=5 120 bash "$MALF_BIN" commands 2>&1)"
ic_rc=$?
ic_db="$ic_repo/build-$ic_key/compile_commands.json"
check "malf commands exits 0 and says how many cell databases it folded" \
      "rc=0 folding 1" \
      "rc=$ic_rc $(grep -oE 'folding [0-9]+' <<< "$ic_out" || echo "GOT: $ic_out")"
check "the cell-only TU is in the root database, and no other profile's cell is" \
      "cell/fixture.cpp pkg/src/lib.cpp" \
      "$(python3 -c "import json,sys; print(' '.join(sorted(e['file'].split('/repo/',1)[1] for e in json.load(open(sys.argv[1])))))" "$ic_db" 2>&1)"
check "a source both the member and the cell compile keeps the MEMBER's command" \
      "member-flags" \
      "$(python3 -c "import json,sys; print(next(e['command'].split()[0] for e in json.load(open(sys.argv[1])) if e['file'].endswith('pkg/src/lib.cpp')))" "$ic_db" 2>&1)"
rm -rf "$ic_tmp"
echo

echo "[7q15] an object with no ninja dependency record never reaches a build: malf deletes it first"

# note: a tree configured in the dependency role drops its test objects' records at ninja's next
# recompaction; back in the target role ninja's dyndep re-visits let such an object link stale, and
# `malf test insight-metalog` passed 357/357 over a header edit it never compiled (W477, 2026-10-06).
# note: real ninja over a compiler-free manifest — `cp` with a depfile under `deps = gcc` — so the
# records, their loss by recompaction and the guard's reading are ninja's own, not a model of them.
dg_tmp="$(realpath "$(mktemp -d)")"
dg_bin="$dg_tmp/bin"; dg_pkg="$dg_tmp/ws/repo/pkg"; mkdir -p "$dg_bin" "$dg_pkg"
cat > "$dg_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""
for a in "$@"; do [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"; done
if [[ "$1" == install && -n "$out" ]]; then
    mkdir -p "$out" && printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
fi
exit 0
STUB
cat > "$dg_bin/cmake" <<'STUB'
#!/usr/bin/env bash
build=""; prev=""; compiling=false
for a in "$@"; do
    if $compiling && [[ -z "$build" ]]; then build="$a"; fi
    [[ "$a" == --build ]] && compiling=true
    [[ "$prev" == -B ]] && build="$a"
    prev="$a"
done
pkg="$(dirname "$build")"
if $compiling; then
    if [[ -e "$build/test.o" ]]; then echo present > "$pkg/AT_BUILD"; else echo absent > "$pkg/AT_BUILD"; fi
    ninja -C "$build" > /dev/null || exit 1
    if [[ -e "$pkg/DEPROLE" ]]; then ninja -C "$build" -t recompact || exit 1; fi
    exit 0
fi
mkdir -p "$build"
make_program="$(command -v ninja)"
[[ -e "$pkg/BADNINJA" ]] && make_program="$pkg/../../../bin/badninja"
printf 'CMAKE_BUILD_TYPE:STRING=Release\nCMAKE_MAKE_PROGRAM:FILEPATH=%s\n' "$make_program" > "$build/CMakeCache.txt"
echo '[]' > "$build/compile_commands.json"
mkdir -p "$build/CMakeFiles"
printf 'rule CXX_COMPILER__stub\n  depfile = $out.d\n  deps = gcc\n  command = cp $in $out && printf "%%s: %%s %%s\\n" $out $in %s/header.hpp > $out.d\n' \
    "$pkg" > "$build/CMakeFiles/rules.ninja"
printf 'rule CXX_EXECUTABLE_LINKER__stub\n  depfile = app.d\n  deps = gcc\n  command = cat $in > app && touch app_tests.cmake && echo "app: $in" > app.d\n' \
    >> "$build/CMakeFiles/rules.ninja"
{
    echo "include CMakeFiles/rules.ninja"
    echo "workdir = $build/"
    echo "build lib.o: CXX_COMPILER__stub $pkg/lib.cpp"
    if [[ ! -e "$pkg/DEPROLE" ]]; then
        echo "build test.o: CXX_COMPILER__stub $pkg/test.cpp"
        echo "build app app_tests.cmake | \${workdir}app_tests.cmake: CXX_EXECUTABLE_LINKER__stub lib.o test.o"
    fi
} > "$build/build.ninja"
STUB
printf '#!/usr/bin/env bash\necho "ninja: error: loading .ninja_deps: stub failure" >&2\nexit 1\n' > "$dg_bin/badninja"
chmod +x "$dg_bin/conan" "$dg_bin/cmake" "$dg_bin/badninja"
printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "dg_pkg"\n    version = "1.0"\n' > "$dg_pkg/conanfile.py"
printf 'project(dg_pkg)\n' > "$dg_pkg/CMakeLists.txt"
echo 'int lib();' > "$dg_pkg/lib.cpp"; echo 'int test();' > "$dg_pkg/test.cpp"; echo '// h' > "$dg_pkg/header.hpp"
git -C "$dg_tmp/ws/repo" init -q
dg_key="${MALF_DEFAULT_PROFILE#linux-}"
dg_tree="$dg_pkg/build-$dg_key"
dg_run() {   # -> rc=<status>; the log is $dg_tmp/log
    (cd "$dg_pkg" && PATH="$dg_bin:$PATH" MALF_WORKSPACE_ROOT="$dg_tmp/ws" MALF_SKIP_INVENTORY=1 \
        MALF_AUTO_WORKSPACE_DEPS=0 MALF_PROFILE_NAME="" timeout --kill-after=5 120 bash "$MALF_BIN" build --only \
        > "$dg_tmp/log" 2>&1; echo "rc=$?")
}
dg_record() { ninja -n -C "$dg_tree" -t deps test.o 2>&1 | head -1 | sed -E 's/, deps mtime.*//'; }
if ! command -v ninja > /dev/null; then
    check "ninja is on PATH, which this arm and every malf build need" "ninja" "absent"
else
    check "the target role builds and records test.o (guards a fixture that cannot record)" \
          "rc=0 test.o: #deps 2" "$(dg_run) $(dg_record)"
    touch "$dg_pkg/DEPROLE"
    check "a dependency-role build and its recompaction drop test.o's record and keep the file" \
          "rc=0 0 yes" \
          "$(dg_run) $(ninja -n -C "$dg_tree" -t deps | grep -c '^test.o:') $([[ -e "$dg_tree/test.o" ]] && echo yes)"
    rm -f "$dg_pkg/DEPROLE"
    dg_rc="$(dg_run)"
    check "back in the target role, the record-less test.o is gone before ninja starts, and malf says so" \
          "rc=0 absent 1" \
          "$dg_rc $(cat "$dg_pkg/AT_BUILD") $(grep -c '2 existing output(s) have no ninja dependency record' "$dg_tmp/log")"
    check "the build that follows recompiles it, and its record is back" "test.o: #deps 2" "$(dg_record)"
    dg_rc="$(dg_run)"
    check "a rebuild deletes nothing, and the link edge's second output, named twice, survives it" \
          "rc=0 0 present" \
          "$dg_rc $(grep -c 'no ninja dependency record' "$dg_tmp/log") $([[ -e "$dg_tree/app_tests.cmake" ]] && echo present)"
    touch "$dg_pkg/BADNINJA"
    check "a tree whose records its own ninja cannot read is refused, never built unchecked" \
          "rc=1 1" "$(dg_run) $(grep -c 'refusing to build' "$dg_tmp/log")"
    rm -f "$dg_pkg/BADNINJA"
fi
rm -rf "$dg_tmp"
echo

echo "[7q4] a WORKSPACE-ROOT sweep builds every member repository's inventory cells, and terminates"

# note: the inventory ran once per sweep, for the repository the sweep ROOT sits in — at the
# workspace root that is the superproject, which declares no inventory, so step 0's B3 compiled
# neither `canon_det_proof` nor `metalog_det_harness` and exited 0 (W211, 2026-09-26).
# note: conan and cmake are stubbed, the inventory tool is the real one: the stub `cmake --build`
# links an executable named after its `--target`, which is the artifact the tool requires.
iv_tmp="$(realpath "$(mktemp -d)")"
iv_bin="$iv_tmp/bin"; iv_ws="$iv_tmp/ws"; mkdir -p "$iv_bin" "$iv_ws"
cat > "$iv_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do
    [[ "$prev" == "-of" ]] && out="$a"
    [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"
    prev="$a"
done
if [[ "$1" == install && -n "$out" ]]; then
    mkdir -p "$out" && : > "$out/conan_toolchain.cmake"
    printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
fi
exit 0
STUB
cat > "$iv_bin/cmake" <<'STUB'
#!/usr/bin/env bash
build=""; target=""; prev=""; linking=false
for a in "$@"; do
    [[ "$a" == --build ]] && linking=true
    case "$prev" in --build) build="$a" ;; --target) target="$a" ;; -B) build="$a" ;; esac
    prev="$a"
done
mkdir -p "$build"
if $linking; then
    [[ -n "$target" ]] && { printf '#!/bin/sh\n' > "$build/$target"; chmod +x "$build/$target"; }
    exit 0
fi
echo "CMAKE_BUILD_TYPE:STRING=Release" > "$build/CMakeCache.txt"
echo "[]" > "$build/compile_commands.json"
STUB
chmod +x "$iv_bin/conan" "$iv_bin/cmake"
iv_repo() {   # <repo> <package> <cell target> [<define line>]
    local r="$iv_ws/$1"
    mkdir -p "$r/pkg" "$r/cell"
    printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "%s"\n    version = "1.0"\n' "$2" > "$r/pkg/conanfile.py"
    printf 'option(X "x" ON)\n' > "$r/pkg/CMakeLists.txt"
    printf 'project(%s)\n' "$3" > "$r/cell/CMakeLists.txt"
    printf 'inventory:\n  %s_cell:\n    path: cell\n    toolchain_from: pkg\n    target: %s\n' "$1" "$3" > "$r/packages.yml"
    [[ -n "${4:-}" ]] && printf '    defines:\n      %s\n' "$4" >> "$r/packages.yml"
    # COMMITTED, because the inventory lint reads the TRACKED CMakeLists.txt of each repository.
    git -C "$r" init -q && git -C "$r" add -A && \
        git -C "$r" -c user.name=fixture -c user.email=fixture@example.invalid commit -qm fixture
}
# The superproject shape: a workspace root that is itself a repository and declares no inventory,
# holding two sibling repositories, the second one's cell WORKSPACE-GRAIN (the metalog shape).
git -C "$iv_ws" init -q
iv_repo alpha iv_alpha alpha_tool
# A second package in `alpha`, so "once per repository" is measured against more than one member.
mkdir -p "$iv_ws/alpha/pkg2"
printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "iv_alpha2"\n    version = "1.0"\n' > "$iv_ws/alpha/pkg2/conanfile.py"
printf 'option(Y "y" ON)\n' > "$iv_ws/alpha/pkg2/CMakeLists.txt"
git -C "$iv_ws/alpha" add -A && \
    git -C "$iv_ws/alpha" -c user.name=fixture -c user.email=fixture@example.invalid commit -qm pkg2
iv_repo beta iv_beta beta_tool 'ALPHA_ROOT: ${workspace}/alpha'
iv_key="${MALF_DEFAULT_PROFILE#linux-}"
iv_log="$iv_tmp/sweep.log"
# `setsid timeout` bounds a runaway and reaps its descendants rather than orphaning them: the
# inventory is not a sweep, and TERMINATION is proven by running it, never by reading it.
(cd "$iv_ws" && PATH="$iv_bin:$PATH" MALF_WORKSPACE_ROOT="$iv_ws" MALF_PROFILE_NAME="" \
    setsid timeout --kill-after=5 120 bash "$MALF_BIN" build > "$iv_log" 2>&1; echo "rc=$?" >> "$iv_log")
check "the workspace-root sweep exits 0" "rc=0" "$(tail -1 "$iv_log")"
check "the sweep enumerates its members EXACTLY once over the workspace (it terminates)" \
      "1" "$(grep -c '3 packages under' "$iv_log" || true)"
check "the first repository's inventory cell is built and LINKED by the workspace-root sweep" \
      "yes" "$([[ -x "$iv_ws/alpha/cell/build-inventory-$iv_key/alpha_tool" ]] && echo yes || echo no)"
check "the second, workspace-grain cell is built and LINKED too" \
      "yes" "$([[ -x "$iv_ws/beta/cell/build-inventory-$iv_key/beta_tool" ]] && echo yes || echo no)"
check "each repository's inventory runs ONCE, however many of its packages the sweep held" \
      "1 1" "$(grep -c 'malf inventory: alpha/cell' "$iv_log") $(grep -c 'malf inventory: beta/cell' "$iv_log")"
check "the workspace lint runs once per sweep, not once per repository" \
      "1" "$(grep -c 'build inventory lint (ADR-3.D9)' "$iv_log")"
rm -rf "$iv_tmp"
echo

echo "[7q5] an inventory cell resolves its workspace dependencies in the leg's KEYED cache, whatever ran before"

# note: step 0 run 36405124004 (2026-09-28) linked canon's cell and died at `metalog_det_harness`:
# "insight_canon/1.10.5 not resolved". Its toolchain recipe requires a workspace package, and the
# cell's `conan install` saw a registry nothing had written — the sweep root never keyed its cache
# and the inventory never registered the cell's dependencies. The desk's persistent registry hid it.
# note: this conan stub keeps a real per-CONAN_HOME registry and fails an install whose recipe
# requires an unregistered ref, the one property the [7q4] stub (which resolves everything) lacks.
rg_tmp="$(realpath "$(mktemp -d)")"
rg_bin="$rg_tmp/bin"; rg_ws="$rg_tmp/ws"; rg_home="$rg_tmp/conan_home"; rg_trace="$rg_tmp/installs"
mkdir -p "$rg_bin" "$rg_ws" "$rg_home"
cat > "$rg_bin/conan" <<'STUB'
#!/usr/bin/env bash
reg="$CONAN_HOME/editable_packages.json"
case "$1 $2" in
    "editable add")
        name=""; version=""
        for a in "$@"; do
            case "$a" in --name=*) name="${a#--name=}" ;; --version=*) version="${a#--version=}" ;; esac
        done
        mkdir -p "$CONAN_HOME"; echo "$name/$version" >> "$reg"
        echo "Reference '$name/$version' in editable mode"; exit 0 ;;
    "editable list") [[ -f "$reg" ]] && cat "$reg"; exit 0 ;;
    "editable remove") exit 0 ;;
esac
[[ "$1" == install ]] || exit 0
out=""; prev=""
for a in "$@"; do
    [[ "$prev" == "-of" ]] && out="$a"
    [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"
    prev="$a"
done
echo "$2 $CONAN_HOME" >> "$RG_TRACE"
for ref in $(grep -oE '"[a-z_]+/[0-9.]+"' "$2/conanfile.py" | tr -d '"'); do
    if ! grep -qx "$ref" "$reg" 2>/dev/null; then
        echo "ERROR: Package '$ref' not resolved" >&2; exit 1
    fi
done
mkdir -p "$out" && : > "$out/conan_toolchain.cmake"
printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
exit 0
STUB
cat > "$rg_bin/cmake" <<'STUB'
#!/usr/bin/env bash
build=""; target=""; prev=""; linking=false
for a in "$@"; do
    [[ "$a" == --build ]] && linking=true
    case "$prev" in --build) build="$a" ;; --target) target="$a" ;; -B) build="$a" ;; esac
    prev="$a"
done
mkdir -p "$build"
if $linking; then
    [[ -n "$target" ]] && { printf '#!/bin/sh\n' > "$build/$target"; chmod +x "$build/$target"; }
    exit 0
fi
echo "CMAKE_BUILD_TYPE:STRING=Release" > "$build/CMakeCache.txt"
echo "[]" > "$build/compile_commands.json"
STUB
chmod +x "$rg_bin/conan" "$rg_bin/cmake"
rg_repo() {   # <repo> <package> <requires ref or ""> <cell target>
    local r="$rg_ws/$1"
    mkdir -p "$r/pkg" "$r/cell"
    printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "%s"\n    version = "1.0"\n' "$2" > "$r/pkg/conanfile.py"
    [[ -n "$3" ]] && printf '    def requirements(self):\n        self.requires("%s")\n' "$3" >> "$r/pkg/conanfile.py"
    printf 'option(X "x" ON)\n' > "$r/pkg/CMakeLists.txt"
    printf 'project(%s)\n' "$4" > "$r/cell/CMakeLists.txt"
    printf 'inventory:\n  %s_cell:\n    path: cell\n    toolchain_from: pkg\n    target: %s\n' "$1" "$4" > "$r/packages.yml"
    git -C "$r" init -q && git -C "$r" add -A && \
        git -C "$r" -c user.name=fixture -c user.email=fixture@example.invalid commit -qm fixture
}
# The CI shape in miniature: `lib` is canon (a cell needing no workspace package), `app` is metalog
# (a cell whose toolchain recipe requires `lib`), under a superproject declaring no inventory.
git -C "$rg_ws" init -q
rg_repo lib rg_lib "" lib_tool
rg_repo app rg_app "rg_lib/1.0" app_tool
rg_profile="linux-gcc16-release"
rg_key="$(MALF_PROFILE_NAME="$rg_profile" _malf_profile_key)"
rg_run() {   # <log> <malf args>... — a fresh conan home, every registry in it empty
    local log="$1"; shift
    rm -rf "$rg_home" "$rg_trace" "$rg_ws"/*/cell/build-inventory-*; mkdir -p "$rg_home"
    (cd "$rg_ws" && PATH="$rg_bin:$PATH" RG_TRACE="$rg_trace" CONAN_HOME="$rg_home" \
        MALF_WORKSPACE_ROOT="$rg_ws" setsid timeout --kill-after=5 120 bash "$MALF_BIN" "$@" \
        > "$log" 2>&1; echo "rc=$?" >> "$log")
}
rg_linked() { [[ -x "$rg_ws/$1/cell/build-inventory-$rg_key/$2" ]] && echo linked || echo absent; }

rg_log="$rg_tmp/inventory.log"
rg_run "$rg_log" inventory app --profile "$rg_profile"
check "malf inventory over a cell needing a workspace package, on a conan home nothing built into" \
      "rc=0 linked" "$(tail -1 "$rg_log") $(rg_linked app app_tool)"
check "the cell's dependency is registered in the KEYED cache, the one its install resolved in" \
      "rg_lib/1.0 $rg_home/$rg_key" \
      "$(cat "$rg_home/$rg_key/editable_packages.json" 2>&1 | sort -u | tr '\n' ' ')$(grep "^$rg_ws/app/pkg " "$rg_trace" | cut -d' ' -f2 | sort -u)"

rg_log="$rg_tmp/sweep.log"
rg_run "$rg_log" build --profile "$rg_profile"
check "a workspace-root sweep links BOTH cells — the one needing a workspace package included" \
      "rc=0 linked linked" "$(tail -1 "$rg_log") $(rg_linked lib lib_tool) $(rg_linked app app_tool)"
check "every install of the sweep — members' and cells' alike — ran in the leg's keyed cache" \
      "$rg_home/$rg_key" "$(cut -d' ' -f2 "$rg_trace" | sort -u | tr '\n' ' ' | sed 's/ $//')"
rm -rf "$rg_tmp"
echo

echo "[7q12] under \`malf test\` only, an inventory cell runs the tests its project registers (ADR-3.D9)"

# note: canon's showcase view gate is registered by its inventory project `proof/` and by no
# package, so `malf test` never ran it; `golden.yaml` runs on pull requests, the cut, a dispatch and
# Mondays, and this trunk commits to `main` — a showcase leak could stand green for a week.
# note: conan and cmake are stubbed, ctest and the inventory tool are real: the stub configure
# copies the cell's fixture CTestTestfile.cmake into the cell's build tree, which is what ctest reads.
ct_tmp="$(realpath "$(mktemp -d)")"
ct_bin="$ct_tmp/bin"; ct_ws="$ct_tmp/ws"; mkdir -p "$ct_bin" "$ct_ws"
cat > "$ct_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do
    [[ "$prev" == "-of" ]] && out="$a"
    prev="$a"
done
[[ "$1" == install && -n "$out" ]] && mkdir -p "$out" && : > "$out/conan_toolchain.cmake"
exit 0
STUB
cat > "$ct_bin/cmake" <<'STUB'
#!/usr/bin/env bash
source=""; build=""; target=""; prev=""; linking=false
for a in "$@"; do
    [[ "$a" == --build ]] && linking=true
    case "$prev" in --build) build="$a" ;; --target) target="$a" ;; -B) build="$a" ;; -S) source="$a" ;; esac
    prev="$a"
done
mkdir -p "$build"
if $linking; then
    printf '#!/bin/sh\n' > "$build/$target"; chmod +x "$build/$target"; exit 0
fi
rm -f "$build/CTestTestfile.cmake"
[[ -f "$source/tests.cmake" ]] && cp "$source/tests.cmake" "$build/CTestTestfile.cmake"
exit 0
STUB
chmod +x "$ct_bin/conan" "$ct_bin/cmake"
git -C "$ct_ws" init -q
ct_repo="$ct_ws/repo"; mkdir -p "$ct_repo/pkg" "$ct_repo/cell"
printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "ct_pkg"\n    version = "1.0"\n' > "$ct_repo/pkg/conanfile.py"
printf 'option(X "x" ON)\n' > "$ct_repo/pkg/CMakeLists.txt"
printf 'project(ct_tool)\n' > "$ct_repo/cell/CMakeLists.txt"
printf 'inventory:\n  ct_cell:\n    path: cell\n    toolchain_from: pkg\n    target: ct_tool\n' > "$ct_repo/packages.yml"
git -C "$ct_repo" init -q && git -C "$ct_repo" add -A && \
    git -C "$ct_repo" -c user.name=fixture -c user.email=fixture@example.invalid commit -qm fixture
ct_run() {   # <build|test> — the real inventory plumbing, sourced; prints its log, then rc=<status>
    rm -f "$ct_tmp/ran"
    (cd "$ct_ws" && PATH="$ct_bin:$PATH" MALF_WORKSPACE_ROOT="$ct_ws" MALF_AUTO_WORKSPACE_DEPS=0 \
        setsid timeout --kill-after=5 120 bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
            _malf_run_inventory "$2" "$3"' _ "$MALF_BIN" "$1" "$ct_repo" 2>&1; echo "rc=$?")
}
ct_ran() { [[ -e "$ct_tmp/ran" ]] && echo ran || echo not-run; }
# The registered test: it leaves a mark that it ran, and reds while the RED file exists.
printf 'add_test(ct_check "sh" "-c" "touch %s/ran; test ! -e %s/RED")\n' "$ct_tmp" "$ct_tmp" \
    > "$ct_repo/cell/tests.cmake"

ct_out="$(ct_run test)"
check "malf test: the cell's one registered test runs, and passes" \
      "rc=0 ran 1" "$(tail -1 <<< "$ct_out") $(ct_ran) $(grep -c 'the project registers 1' <<< "$ct_out")"
touch "$ct_tmp/RED"
ct_out="$(ct_run test)"
check "malf test: a red registered test reds the run, naming the project and the cell" \
      "rc=1 ran 1" \
      "$(tail -1 <<< "$ct_out") $(ct_ran) $(grep -c "tests FAILED for ct_cell (repo/cell) in the cell build-inventory-" <<< "$ct_out")"
ct_out="$(ct_run build)"
check "malf build: the same red test is never run — a build stays compile-and-link" \
      "rc=0 not-run 0" "$(tail -1 <<< "$ct_out") $(ct_ran) $(grep -c 'tests:' <<< "$ct_out")"
rm -f "$ct_tmp/RED" "$ct_repo/cell/tests.cmake"
ct_out="$(ct_run test)"
check "malf test: a project registering no test runs nothing, and says so" \
      "rc=0 not-run 1" "$(tail -1 <<< "$ct_out") $(ct_ran) $(grep -c 'registers none' <<< "$ct_out")"
printf 'add_test(\n' > "$ct_repo/cell/tests.cmake"
ct_out="$(ct_run test)"
check "malf test: a test listing ctest cannot read is a red, never a count of zero" \
      "rc=1 1" "$(tail -1 <<< "$ct_out") $(grep -c 'cannot count the tests ct_cell' <<< "$ct_out")"
rm -rf "$ct_tmp"
echo

echo "[7q13] a failed configure or compile reds build and test in every form, and a malf rewritten mid-run finishes its run"

# note: `_malf_configure_and_build` runs only under `||` or `if !`, which suspends `set -e` inside it,
# so a failed `cmake --preset` was discarded whenever `cmake --build` over the previous tree passed.
# note: an in-place rewrite of malf mid-run (2026-10-05, a python `open('w')` edit) killed a live build
# with "line 5542: syntax error": bash reads a script by offset, and resumed in the new bytes.
ex_tmp="$(realpath "$(mktemp -d)")"
ex_bin="$ex_tmp/bin"; ex_ws="$ex_tmp/ws"; mkdir -p "$ex_bin" "$ex_ws"
cat > "$ex_bin/conan" <<'STUB'
#!/usr/bin/env bash
out=""
for a in "$@"; do [[ "$a" == --output-folder=* ]] && out="${a#--output-folder=}"; done
if [[ "$1" == install && -n "$out" ]]; then
    mkdir -p "$out" && printf '{"version":4,"configurePresets":[{"name":"conan-release"}]}\n' > "$out/CMakePresets.json"
fi
exit 0
STUB
cat > "$ex_bin/cmake" <<'STUB'
#!/usr/bin/env bash
build=""; prev=""; compiling=false
for a in "$@"; do
    if $compiling && [[ -z "$build" ]]; then build="$a"; fi
    [[ "$a" == --build ]] && compiling=true
    [[ "$prev" == -B ]] && build="$a"
    prev="$a"
done
pkg="$(dirname "$build")"
if $compiling; then
    if [[ -e "$pkg/HOLD" ]]; then
        : > "$pkg/HELD"
        while [[ ! -e "$pkg/GO" ]]; do sleep 0.1; done
    fi
    [[ -e "$pkg/COMPILE_RED" ]] && { echo "FAILED: stub compile error in $pkg" >&2; exit 1; }
    exit 0
fi
mkdir -p "$build"
[[ -e "$pkg/CONFIGURE_RED" ]] && { echo "CMake Error: stub configure error in $pkg" >&2; exit 1; }
echo "CMAKE_BUILD_TYPE:STRING=Release" > "$build/CMakeCache.txt"
echo '[]' > "$build/compile_commands.json"
STUB
chmod +x "$ex_bin/conan" "$ex_bin/cmake"
ex_pkg() {   # <subdir> <name> [<requires>]
    local d="$ex_ws/repo/$1"
    mkdir -p "$d"
    printf 'from conan import ConanFile\nclass C(ConanFile):\n    name = "%s"\n    version = "1.0"\n' "$2" > "$d/conanfile.py"
    [[ -n "${3:-}" ]] && printf '    requires = "%s"\n' "$3" >> "$d/conanfile.py"
    printf 'project(%s)\n' "$2" > "$d/CMakeLists.txt"
}
# The insight-eidos shape: a root recipe that both sub-recipes require, swept root first.
ex_pkg . ex_root; ex_pkg sub ex_sub ex_root/1.0; ex_pkg leaf ex_leaf ex_root/1.0
git -C "$ex_ws/repo" init -q
ex_run() {   # <malf> <verb args...> -> rc=<status>; the log is $ex_tmp/log
    local malf="$1"; shift
    (cd "$ex_ws/repo" && PATH="$ex_bin:$PATH" MALF_WORKSPACE_ROOT="$ex_ws" MALF_SKIP_INVENTORY=1 \
        MALF_PROFILE_NAME="" timeout --kill-after=5 120 bash "$malf" "$@" > "$ex_tmp/log" 2>&1
     echo "rc=$?")
}
ex_forms() {   # <package dir for --only> -> the status of every form, one word each
    local out=""
    out+="$(ex_run "$MALF_BIN" build sub) "
    out+="$(ex_run "$MALF_BIN" test sub) "
    out+="$(ex_run "$MALF_BIN" build "$1" --only) "
    out+="$(ex_run "$MALF_BIN" build) "
    out+="$(ex_run "$MALF_BIN" test)"
    echo "$out"
}
check "a clean tree builds green in every form (guards a fixture that reds by itself)" \
      "rc=0 rc=0 rc=0 rc=0 rc=0" "$(ex_forms sub)"
touch "$ex_ws/repo/sub/CONFIGURE_RED"
check "a failed configure of the target reds build, test, --only and both sweeps, over a warm tree" \
      "rc=1 rc=1 rc=1 rc=1 rc=1" "$(ex_forms sub)"
mv "$ex_ws/repo/sub/CONFIGURE_RED" "$ex_ws/repo/CONFIGURE_RED"
check "a failed configure of a DEPENDENCY reds every form that builds it" \
      "rc=1 rc=1 rc=1 rc=1 rc=1" "$(ex_forms .)"
mv "$ex_ws/repo/CONFIGURE_RED" "$ex_ws/repo/sub/COMPILE_RED"
check "a failed compile of the target, the last member of the sweep, reds every form" \
      "rc=1 rc=1 rc=1 rc=1 rc=1" "$(ex_forms sub)"
mv "$ex_ws/repo/sub/COMPILE_RED" "$ex_ws/repo/COMPILE_RED"
check "a failed compile of a DEPENDENCY reds every form that builds it" \
      "rc=1 rc=1 rc=1 rc=1 rc=1" "$(ex_forms .)"
rm -f "$ex_ws/repo/COMPILE_RED"
# A private copy of the toolchain, so the rewrite never touches the malf every lane runs.
mkdir -p "$ex_tmp/toolchain"
for ex_entry in "$MALF_ROOT"/*; do
    [[ "$(basename "$ex_entry")" == tests ]] || cp -a "$ex_entry" "$ex_tmp/toolchain/"
done
touch "$ex_ws/repo/leaf/HOLD"
ex_run "$ex_tmp/toolchain/malf" build leaf --only > "$ex_tmp/rc" &
ex_pid=$!
for _ in $(seq 1 600); do [[ -e "$ex_ws/repo/leaf/HELD" ]] && break; sleep 0.1; done
python3 - "$ex_tmp/toolchain/malf" <<'PY'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
first, rest = text.split("\n", 1)
with open(path, "w", encoding="utf-8") as script:
    script.write(first + "\n" + "# a lane's in-place edit\n" * 40 + rest)
PY
: > "$ex_ws/repo/leaf/GO"
wait "$ex_pid"
check "a malf rewritten in place mid-run exits with its own run's status, never a syntax error" \
      "rc=0 0" "$(cat "$ex_tmp/rc") $(grep -c 'syntax error' "$ex_tmp/log")"
rm -rf "$ex_tmp"
echo

echo "[7j6] lint --all-files NAMES a TU the build gates off this platform, and refuses nothing else"

# note: two sift *_win32.cpp files are named only inside if(WIN32), so no Linux compile command can
# exist; --all-files treated them as a fatal coverage hole and the eidos gate could never pass.
# note: the verdict comes from the repo's CMake text evaluated by cmake -P, never a file name — so a
# *_win32.cpp no CMake file names, one under a project option, and one under if(UNIX) all stay fatal.
pg_tmp="$(realpath "$(mktemp -d)")"
pg_bin="$pg_tmp/bin"; mkdir -p "$pg_bin"
printf '#!/usr/bin/env bash\nexit 0\n' > "$pg_bin/clang-21"
printf '#!/usr/bin/env bash\nexit 0\n' > "$pg_bin/clang-tidy"
chmod +x "$pg_bin/clang-21" "$pg_bin/clang-tidy"
pg_repo="$pg_tmp/repo"; mkdir -p "$pg_repo/src"
cat > "$pg_repo/CMakeLists.txt" <<'CM'
option(PG_EXTRA "extra" OFF)
if(WIN32)
    set(PG_PLATFORM_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/term_win32.cpp")
else()
    set(PG_PLATFORM_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/term_posix.cpp")
endif()
if(PG_EXTRA)
    set(PG_EXTRA_SRC src/extra.cpp)
endif()
if(UNIX)
    set(PG_UNIX_SRC src/unix_only.cpp)
endif()
add_library(pg src/core.cpp ${PG_PLATFORM_SRC})
CM
for pg_f in core term_posix term_win32; do printf 'int %s() { return 0; }\n' "$pg_f" > "$pg_repo/src/$pg_f.cpp"; done
git -C "$pg_repo" init -q && git -C "$pg_repo" add -A
pg_key="${MALF_DEFAULT_PROFILE#linux-}"; mkdir -p "$pg_repo/build-$pg_key"
printf '[{"directory": "%s", "command": "clang++-21 -std=c++23 -c %s -o a.o", "file": "%s"},\n {"directory": "%s", "command": "clang++-21 -std=c++23 -c %s -o b.o", "file": "%s"}]\n' \
    "$pg_repo/build-$pg_key" "$pg_repo/src/core.cpp" "$pg_repo/src/core.cpp" \
    "$pg_repo/build-$pg_key" "$pg_repo/src/term_posix.cpp" "$pg_repo/src/term_posix.cpp" \
    > "$pg_repo/build-$pg_key/compile_commands.json"
pg_run() {
    (cd "$pg_repo" && PATH="$pg_bin:$PATH" MALF_PROFILE_NAME="" bash "$MALF_BIN" lint --all-files --console 2>&1)
}
pg_counts() { grep -oE 'checked [0-9]+, [0-9]+ finding\(s\), [0-9]+ not linted, [0-9]+ platform-refused' <<< "$1" | head -1; }

pg_out="$(pg_run)"; pg_rc=$?
check "a TU named only inside if(WIN32) is REFUSED, not fatal — rc 0, the other two checked" \
      "rc=0 checked 2, 0 finding(s), 0 not linted, 1 platform-refused" "rc=$pg_rc $(pg_counts "$pg_out")"
check "the refusal NAMES the file and the CMake branch that gates it" \
      "named" \
      "$(grep -qE '^  src/term_win32\.cpp — CMakeLists\.txt:3 sits in if\(WIN32\)' <<< "$pg_out" && echo named || echo "GOT: $pg_out")"

printf 'int orphan() { return 0; }\n' > "$pg_repo/src/orphan_win32.cpp"
pg_out="$(pg_run)"; pg_rc=$?
check "a *_win32.cpp NO CMake file names stays a fatal hole — the verdict never reads a file name" \
      "rc=1 missing" \
      "rc=$pg_rc $(grep -qE 'have no usable compile command' <<< "$pg_out" && grep -qE '^  src/orphan_win32\.cpp$' <<< "$pg_out" && echo missing || echo "GOT: $pg_out")"
rm -f "$pg_repo/src/orphan_win32.cpp"

printf 'int extra() { return 0; }\n' > "$pg_repo/src/extra.cpp"
pg_out="$(pg_run)"; pg_rc=$?
check "a TU gated by a PROJECT option is not a platform gate — fatal" \
      "rc=1 missing" \
      "rc=$pg_rc $(grep -qE '^  src/extra\.cpp$' <<< "$pg_out" && echo missing || echo "GOT: $pg_out")"
rm -f "$pg_repo/src/extra.cpp"

printf 'int unix_only() { return 0; }\n' > "$pg_repo/src/unix_only.cpp"
pg_out="$(pg_run)"; pg_rc=$?
check "a TU under if(UNIX) and absent from the database is fatal — the branch is evaluated, not assumed" \
      "rc=1 missing" \
      "rc=$pg_rc $(grep -qE '^  src/unix_only\.cpp$' <<< "$pg_out" && echo missing || echo "GOT: $pg_out")"
rm -rf "$pg_tmp"
echo

# --- malf run: it finds the executable, and a miss is a NAMED miss --------------------------------
# `cmd_run` used to hand find BOTH `<pkg>/build` and `<pkg>/build-*`. In the usual layout one of
# the two is absent, find exits 1 on it, and under malf's `set -e` + pipefail the assignment killed
# `malf run` at exit 1 with no message — for an executable that was there, and for one that was
# not. Measured 2026-09-24: every `malf run` in coderoast-ipc exited 1 silently. A fixture package
# with ONLY a `build-*` tree is the shape that reproduces it; the third arm adds a plain `build/`
# so both trees exist.
echo "--- malf run"
rn_tmp="$(mktemp -d)"
mkdir -p "$rn_tmp/pkg/build-fx"
printf 'from conan import ConanFile\nclass P(ConanFile):\n    name = "fxpkg"\n    version = "0.0.1"\n' \
    > "$rn_tmp/pkg/conanfile.py"
printf '#!/bin/sh\necho "RAN-FIXTURE $*"\n' > "$rn_tmp/pkg/build-fx/fixture_exe"
chmod +x "$rn_tmp/pkg/build-fx/fixture_exe"
rn_out="$(cd "$rn_tmp/pkg" && "$MALF_BIN" run fixture_exe a1 2>&1)"; rn_rc=$?
check "malf run finds an executable in the only build tree there is, and runs it with its arguments" \
      "rc=0 ran" \
      "rc=$rn_rc $(grep -qx 'RAN-FIXTURE a1' <<< "$rn_out" && echo ran || echo "GOT: $rn_out")"
rn_out="$(cd "$rn_tmp/pkg" && "$MALF_BIN" run absent_exe 2>&1)"; rn_rc=$?
check "malf run on an absent executable exits 1 and SAYS so — never a silent exit" \
      "rc=1 named" \
      "rc=$rn_rc $(grep -q "malf run: 'absent_exe' not found in any package build tree" <<< "$rn_out" && echo named || echo "GOT: $rn_out")"
mkdir -p "$rn_tmp/pkg/build"
rn_out="$(cd "$rn_tmp/pkg" && "$MALF_BIN" run fixture_exe a2 2>&1)"; rn_rc=$?
check "malf run still finds it when a plain build/ tree exists beside the build-* one" \
      "rc=0 ran" \
      "rc=$rn_rc $(grep -qx 'RAN-FIXTURE a2' <<< "$rn_out" && echo ran || echo "GOT: $rn_out")"
rm -rf "$rn_tmp"
echo

echo "[7q6] a job starts with NO editable registry in ANY conan home it restored — the base and every keyed one"

# note: setup-build-env restores $CONAN_HOME WHOLE, and malf keys a profile's home under it
# (<home>/gcc16-release, <home>/cut-verify). A registry saved in a keyed home re-registers its
# editables in every later job that restores the entry; the step cleared only the base's.
de_script="$MALF_ROOT/.github/actions/setup-build-env/drop-editable-registries.sh"
de_tmp="$(mktemp -d)"
de_home="$de_tmp/.conan2"
mkdir -p "$de_home/gcc16-release" "$de_home/cut-verify" "$de_home/p/pkgfolder"
for reg in "$de_home" "$de_home/gcc16-release" "$de_home/cut-verify"; do
    echo '{"insight_canon/1.10.5": {"path": "/stale/checkout/conanfile.py"}}' > "$reg/editable_packages.json"
done
touch "$de_home/gcc16-release/settings.yml" "$de_home/p/pkgfolder/conaninfo.txt"
de_out="$(CONAN_HOME="$de_home" bash "$de_script" 2>&1)"; de_rc=$?
check "every conan home's editable registry is gone after the step, the base's and each keyed one's" \
      "rc=0 left:" \
      "rc=$de_rc left:$(cd "$de_home" && find . -name editable_packages.json | sort | tr '\n' ' ')"
check "the step removes registries only: the keyed home's settings and a cached package stay" \
      "gcc16-release/settings.yml p/pkgfolder/conaninfo.txt" \
      "$(cd "$de_home" && find . -type f ! -name editable_packages.json | sed 's#^\./##' | sort | tr '\n' ' ' | sed 's/ $//')"
check "the step NAMES each registry it removed, so a job log says what the restore carried" \
      "3" "$(grep -c '^removing .*/editable_packages.json' <<< "$de_out")"
de_out="$(CONAN_HOME="$de_tmp/absent" bash "$de_script" 2>&1)"; de_rc=$?
check "a cache miss (no conan home yet) is exit 0, saying there was nothing to drop" \
      "rc=0 said" "rc=$de_rc $(grep -q 'no conan home at' <<< "$de_out" && echo said || echo "GOT: $de_out")"
check "setup-build-env's cleanup step RUNS this script — a tested script no step invokes is no gate" \
      "1" "$(grep -c 'bash "$ACTION_PATH/drop-editable-registries.sh"' "$MALF_ROOT/.github/actions/setup-build-env/action.yml")"
rm -rf "$de_tmp"
echo

echo "[7q7] the persistent conan home: per runner, outside the checkout, self-hosted only (DN-119.D2)"

# note: step 0 spent about 510 s of every warm run restoring and re-saving one 3.2 GB actions/cache
# entry, and about 1 800 s of every cold one rebuilding the third-party closure, because the conan
# home lived in the checkout that `actions/checkout` cleans. `conan-home.sh` is where the home is.
ch_script="$MALF_ROOT/.github/actions/setup-build-env/conan-home.sh"
# The release runner `true` is handed out on ([7q10] refuses every other shape): a `gh` that answers
# the jobs API from $CH_JOBS through the script's own jq filter, and the runner's systemd unit.
ch_fixture() {   # <dir>: <dir>/bin/gh and an empty <dir>/units
    mkdir -p "$1/bin" "$1/units"
    cat > "$1/bin/gh" <<'STUB'
#!/usr/bin/env bash
[[ -z "${CH_GH_FAIL:-}" ]] || { echo "HTTP 403: Resource not accessible by integration" >&2; exit 1; }
want="repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/attempts/$GITHUB_RUN_ATTEMPT/jobs"
[[ "$1" == api && "$2" == "$want" ]] || { echo "gh stub: unexpected call: $*" >&2; exit 2; }
filter=""
while (($#)); do [[ "$1" == --jq ]] && { filter="$2"; break; }; shift; done
jq -r "$filter" <<< "$CH_JOBS"
STUB
    chmod +x "$1/bin/gh"
}
ch_jobs() {   # <runner name> <runner group of its in-progress job>: the run's jobs, as the API lists them
    printf '{"jobs":[{"status":"completed","runner_name":"%s","runner_group_name":"default"},{"status":"in_progress","runner_name":"elsewhere","runner_group_name":"default"},{"status":"in_progress","runner_name":"%s","runner_group_name":"%s"}]}' "$1" "$1" "$2"
}
ch_tmp="$(realpath "$(mktemp -d)")"
ch_ws="$ch_tmp/work/coderoast/coderoast"; ch_user="$ch_tmp/home"; mkdir -p "$ch_ws"; mkdir -m 700 "$ch_user"
ch_fixture "$ch_tmp"
printf '[Service]\nUser=%s\n' "$(id -un)" > "$ch_tmp/units/actions.runner.CodeRoasted.malf-runner.service"
ch_run() {   # <persistent> <runner environment> <runner name>
    PATH="$ch_tmp/bin:$PATH" CONAN_HOME_RUNNER_UNITS="$ch_tmp/units" CH_JOBS="$(ch_jobs "$3" coderoast-release)" \
    GITHUB_REPOSITORY=CodeRoasted/coderoast GITHUB_RUN_ID=4242 GITHUB_RUN_ATTEMPT=1 \
    GITHUB_WORKSPACE="$ch_ws" HOME="$ch_user" RUNNER_ENVIRONMENT="$2" RUNNER_NAME="$3" \
        bash "$ch_script" "$1" 2>&1
}
ch_out="$(ch_run false github-hosted runner-1)"; ch_rc=$?
check "persistent=false keeps the home in the checkout, where actions/cache restores it" \
      "$ch_ws/.conan2 rc=0" "$ch_out rc=$ch_rc"
ch_out="$(ch_run true self-hosted malf-runner)"; ch_rc=$?
check "persistent=true on a self-hosted runner: one home per runner NAME, under the runner user's HOME" \
      "$ch_user/.cache/coderoast-build/conan/malf-runner rc=0" "$ch_out rc=$ch_rc"
check "the persistent home exists, outside the checkout, readable by its owner alone" \
      "700 outside" \
      "$(stat -c %a "$ch_user/.cache/coderoast-build/conan/malf-runner" 2>&1) $(case "$ch_out" in "$ch_ws"/*) echo inside ;; *) echo outside ;; esac)"
ch_out="$(ch_run true github-hosted runner-1)"; ch_rc=$?
check "persistent=true on a HOSTED runner refuses: nothing there outlives the job" \
      "rc=1 said" "rc=$ch_rc $(grep -q 'self-hosted' <<< "$ch_out" && echo said || echo "GOT: $ch_out")"
ch_out="$(ch_run true self-hosted '../escape')"; ch_rc=$?
check "a runner name that is not one path segment refuses rather than escaping the home's base" \
      "rc=1" "rc=$ch_rc"
check "setup-build-env derives CONAN_HOME through this script, and asks for no Actions cache for a persistent home" \
      "1 3" "$(grep -c 'bash "$ACTION_PATH/conan-home.sh"' "$MALF_ROOT/.github/actions/setup-build-env/action.yml") $(grep -c "if: \${{ inputs.persistent-conan-home != 'true' }}" "$MALF_ROOT/.github/actions/setup-build-env/action.yml")"
rm -rf "$ch_tmp"
echo

echo "[7q7b] no job on the coderoast-release runner group restores or saves an Actions cache (DN-142.D6, ROADMAP N367)"

# note: insight-eidos Release run 37725404832 (v1.10.6) restored three `conan-golden-*` Actions
# caches on that group, each saved by a `main` run on another runner.
ac_script="$MALF_ROOT/.github/actions/setup-build-env/actions-cache-allowed.sh"
ac_tmp="$(realpath "$(mktemp -d)")"
ch_fixture "$ac_tmp"
ac_run() {   # <runner environment> <runner name> <group of its in-progress job> [gh-fail]: "<allowed> rc=<rc>"
    : > "$ac_tmp/out"
    PATH="$ac_tmp/bin:$PATH" CH_JOBS="$(ch_jobs "$2" "$3")" CH_GH_FAIL="${4:-}" \
    GITHUB_REPOSITORY=CodeRoasted/insight-eidos GITHUB_RUN_ID=37725404832 GITHUB_RUN_ATTEMPT=1 \
    GITHUB_OUTPUT="$ac_tmp/out" RUNNER_ENVIRONMENT="$1" RUNNER_NAME="$2" \
        bash "$ac_script" > /dev/null 2>&1
    local rc=$?
    printf '%s rc=%s' "$(sed -n 's/^allowed=//p' "$ac_tmp/out")" "$rc"
}
check "a job on the coderoast-release group may not touch an Actions cache" \
      "false rc=0" "$(ac_run self-hosted malf-release coderoast-release)"
check "the group is read case-blind, as conan-home.sh reads it" \
      "false rc=0" "$(ac_run self-hosted malf-release Coderoast-Release)"
check "a self-hosted job in the default group may" "true rc=0" "$(ac_run self-hosted malf-runner Default)"
check "a hosted job may, and needs no read of the jobs API" \
      "true rc=0" "$(ac_run github-hosted 'GitHub Actions 7' coderoast-release)"
check "an unreadable jobs listing (no actions: read) answers false, never true" \
      "false rc=0" "$(ac_run self-hosted malf-runner Default fail)"
mkdir -p "$ac_tmp/nogh"; ln -s "$(command -v grep)" "$ac_tmp/nogh/grep"
: > "$ac_tmp/out"
PATH="$ac_tmp/nogh" GITHUB_OUTPUT="$ac_tmp/out" RUNNER_ENVIRONMENT=self-hosted RUNNER_NAME=malf-runner \
GITHUB_REPOSITORY=o/r GITHUB_RUN_ID=1 "$BASH" "$ac_script" > /dev/null 2>&1
check "no gh on a self-hosted runner answers false" "false" "$(sed -n 's/^allowed=//p' "$ac_tmp/out")"

# The hand-off to the save: what `read` answers for each record `write` can leave.
cs_script="$MALF_ROOT/.github/actions/setup-build-env/conan-cache-state.sh"
cs_read() {   # [home key hit allowed]: the save verdict, home and key read back
    rm -rf "$ac_tmp/rt"; mkdir -p "$ac_tmp/rt"; : > "$ac_tmp/cs"
    (($# == 0)) || RUNNER_TEMP="$ac_tmp/rt" bash "$cs_script" write "$@"
    RUNNER_TEMP="$ac_tmp/rt" GITHUB_OUTPUT="$ac_tmp/cs" bash "$cs_script" read > /dev/null 2>&1
    local rc=$?
    printf '%s rc=%s' "$(tr '\n' ' ' < "$ac_tmp/cs")" "$rc"
}
check "a restore that was allowed and missed is saved, under its home and key" \
      "save=true home=/h key=k1  rc=0" "$(cs_read /h k1 false true)"
check "an exact-key hit saves nothing" "save=false  rc=0" "$(cs_read /h k1 true true)"
check "a job that may not touch an Actions cache saves nothing" "save=false  rc=0" "$(cs_read /h k1 false false)"
check "no restore in the job saves nothing" "save=false  rc=0" "$(cs_read)"

# The wiring: every Actions cache restore in this repository sits behind the verdict, and nothing
# uses the combined `actions/cache` (its post step saves the whole home, first-party included).
ac_restores="$(grep -rl 'uses: actions/cache/restore@' "$MALF_ROOT/.github" | sort | tr '\n' ' ')"
ac_guarded="$(for f in $ac_restores; do \
              awk '/^    - name:/{guard=0} /if: .*steps\.guard\.outputs\.allowed == .true./{guard=1} /uses: actions\/cache\/restore@/{print (guard ? "guarded" : "UNGUARDED " FILENAME)}' "$f"; done | sort -u | tr '\n' ' ')"
check "every actions/cache/restore in malf is behind actions-cache-allowed.sh's verdict" \
      "guarded " "$ac_guarded"
check "no malf action or workflow uses the combined actions/cache (a post-step save of the whole home)" \
      "" "$(grep -rln 'uses: actions/cache@' "$MALF_ROOT/.github" | tr '\n' ' ')"
check "every restore records itself for conan-cache-save" \
      "3" "$(grep -rl 'conan-cache-state.sh" write' "$MALF_ROOT/.github/actions" | wc -l | tr -d ' ')"
# Both ends drop the first-party packages through one script: what it removes, extras included.
mkdir -p "$ac_tmp/conanbin"
printf '#!/usr/bin/env bash\necho "$CONAN_HOME $*" >> "%s/conan.log"\n' "$ac_tmp" > "$ac_tmp/conanbin/conan"
chmod +x "$ac_tmp/conanbin/conan"
PATH="$ac_tmp/conanbin:$PATH" bash "$MALF_ROOT/.github/actions/setup-build-env/drop-first-party.sh" /h $'extra_*\n' > /dev/null 2>&1
check "drop-first-party.sh removes insight_*, coderoast_*, logcraft_* and the extras, in the home it is given" \
      "/h remove insight_* --confirm|/h remove coderoast_* --confirm|/h remove logcraft_* --confirm|/h remove extra_* --confirm|/h list * --format=compact|" \
      "$(tr '\n' '|' < "$ac_tmp/conan.log")"
check "every restore drops the first-party packages it brought, and the save drops them too" \
      "4" "$(grep -rl --include=action.yml 'drop-first-party.sh' "$MALF_ROOT/.github/actions" | wc -l | tr -d ' ')"
rm -rf "$ac_tmp"
echo

echo "[7q7c] the vendor fetch restores the recipe revision the tag carries, never a cached package by version alone (ROADMAP N367)"

# Stubs: `gh release download` copies a prepared tarball; `conan` keeps the cached recipe
# revisions of the one ref in a state file (one per line), logging every call.
vf_tmp="$(realpath "$(mktemp -d)")"
mkdir -p "$vf_tmp/bin" "$vf_tmp/one" "$vf_tmp/two"
printf '{"pkg/1.0": {"revisions": {"tagrev": {}}}}' > "$vf_tmp/one/pkglist.json"
printf '{"pkg/1.0": {"revisions": {"tagrev": {}, "otherrev": {}}}}' > "$vf_tmp/two/pkglist.json"
tar -czf "$vf_tmp/one.tgz" -C "$vf_tmp/one" pkglist.json
tar -czf "$vf_tmp/two.tgz" -C "$vf_tmp/two" pkglist.json
cat > "$vf_tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$VF_LOG"
while [ "$#" -gt 0 ]; do [ "$1" = --dir ] && cp "$VF_TARBALL" "$2/pkg-1.0.tgz"; shift; done
STUB
cat > "$vf_tmp/bin/conan" <<'STUB'
#!/usr/bin/env bash
echo "conan $*" >> "$VF_LOG"
case "$1 $2" in
  "list pkg/1.0#*")
    python3 -I -c 'import json,sys; r=[l for l in open(sys.argv[1]).read().split() if l]; print(json.dumps({"Local Cache": {"pkg/1.0": {"revisions": {x: {} for x in r}}} if r else {}}))' "$VF_STATE" ;;
  "list pkg/1.0") [ -s "$VF_STATE" ] && printf 'Local Cache\n  pkg/1.0\n' ;;
  "remove pkg/1.0#"*) rev="${2#pkg/1.0#}"; grep -vx "$rev" "$VF_STATE" > "$VF_STATE.new" || true; mv "$VF_STATE.new" "$VF_STATE" ;;
  "cache restore") [ -n "${VF_RESTORE_NOOP:-}" ] || { grep -qx tagrev "$VF_STATE" || echo tagrev >> "$VF_STATE"; } ;;
esac
STUB
chmod +x "$vf_tmp/bin/gh" "$vf_tmp/bin/conan"
vf_run() {  # <cached revisions, space-separated> <tarball> [restore-noop]
    : > "$vf_tmp/state"; : > "$vf_tmp/log"
    for rev in $1; do echo "$rev" >> "$vf_tmp/state"; done
    VF_LOG="$vf_tmp/log" VF_STATE="$vf_tmp/state" VF_TARBALL="$vf_tmp/$2" VF_RESTORE_NOOP="${3:-}" \
        PATH="$vf_tmp/bin:$PATH" bash "$MALF_ROOT/.github/actions/coderoast-vendor/ci_fetch_conan_package.sh" \
        pkg 1.0 Owner/repo > /dev/null 2>&1
    echo "rc=$? cache=$(tr '\n' ' ' < "$vf_tmp/state" | sed 's/ $//') removed=$(grep -c '^conan remove' "$vf_tmp/log") downloads=$(grep -c '^gh release download' "$vf_tmp/log")"
}
check "a cached revision other than the tag's is removed and the tag's restored — the version alone is never trusted" \
      "rc=0 cache=tagrev removed=1 downloads=1" "$(vf_run staleold one.tgz)"
check "the tag's revision already cached is still checked against a download, and nothing is removed" \
      "rc=0 cache=tagrev removed=0 downloads=1" "$(vf_run tagrev one.tgz)"
check "an empty cache downloads and restores the tag's revision" \
      "rc=0 cache=tagrev removed=0 downloads=1" "$(vf_run '' one.tgz)"
check "a tarball carrying two recipe revisions of the ref is refused before the cache is touched" \
      "rc=1 cache=staleold removed=0 downloads=1" "$(vf_run staleold two.tgz)"
check "a restore that does not leave exactly the tag's revision fails the fetch" \
      "rc=1 cache= removed=1 downloads=1" "$(vf_run staleold one.tgz noop)"
rm -rf "$vf_tmp"
echo

echo "[7q7d] released packages are relocatable and path-independent: the path-map fragment, the twin compare, the relocation scan (ROADMAP N366)"

# The fragment, through a real configure and compile with whatever c++ and cmake the host has,
# included from a stand-in conan home as conan includes it: __FILE__ and std::source_location come
# out relative — a dependency header under the home included — and the binary names no path.
pm_tmp="$(realpath "$(mktemp -d)")"
mkdir -p "$pm_tmp/proj/src" "$pm_tmp/home/p/dep/p/include"
cp "$MALF_ROOT/cmake/malf-path-map.cmake" "$pm_tmp/home/malf-path-map.cmake"
printf 'inline const char* dep_file() { return __FILE__; }\n' > "$pm_tmp/home/p/dep/p/include/dep.hpp"
printf 'cmake_minimum_required(VERSION 3.20)\nproject(pm CXX)\nadd_executable(pm src/main.cpp)\ntarget_include_directories(pm PRIVATE "%s/home/p/dep/p/include")\nset_target_properties(pm PROPERTIES CXX_STANDARD 20)\nget_directory_property(options COMPILE_OPTIONS)\nmessage(STATUS "pm-options=[${options}] pm-flags=[${CMAKE_CXX_FLAGS}]")\n' "$pm_tmp" > "$pm_tmp/proj/CMakeLists.txt"
printf '#include <cstdio>\n#include <source_location>\n#include "dep.hpp"\nint main() { std::puts(__FILE__); std::puts(std::source_location::current().file_name()); std::puts(dep_file()); }\n' > "$pm_tmp/proj/src/main.cpp"
pm_conf="$(cmake -S "$pm_tmp/proj" -B "$pm_tmp/proj/b" -DCMAKE_TOOLCHAIN_FILE="$pm_tmp/home/malf-path-map.cmake" 2>&1)"
cmake --build "$pm_tmp/proj/b" > /dev/null 2>&1
check "the fragment maps __FILE__ and std::source_location out of the source directory and a dependency header out of the conan home" \
      "./src/main.cpp|./src/main.cpp|conan-home/p/dep/p/include/dep.hpp|" "$("$pm_tmp/proj/b/pm" 2>&1 | tr '\n' '|')"
check "the built binary names no path of its source directory, build directory or conan home" \
      "0" "$(grep -a -c "$pm_tmp" "$pm_tmp/proj/b/pm")"
# A directory compile option is exported into a module package's IMPORTED_CXX_MODULES_COMPILE_OPTIONS
# with the producer's folder in it; the language flags never are. So: flags carry it, options do not.
check "the fragment rides the language flags, never a directory compile option a package config would export" \
      "pm-options=[] flags-carry-map=1" \
      "$(grep -o 'pm-options=\[[^]]*\]' <<< "$pm_conf") flags-carry-map=$(grep -c -- "pm-flags=\[.*-fmacro-prefix-map=$pm_tmp/home=conan-home .*-fmacro-prefix-map=$pm_tmp/proj=\. .*-fmacro-prefix-map=$pm_tmp/proj/b=build" <<< "$pm_conf")"
rm -rf "$pm_tmp"

# The fragment reaches every home global.conf names it in: malf syncs it beside global.conf, and
# each CI action that stages global.conf stages it too.
pm_home="$(mktemp -d)"
CONAN_HOME="$pm_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
check "malf syncs the fragment into the conan home beside global.conf, byte for byte" \
      "synced" "$(cmp -s "$MALF_ROOT/cmake/malf-path-map.cmake" "$pm_home/malf-path-map.cmake" && cmp -s "$MALF_ROOT/global.conf" "$pm_home/global.conf" && echo synced)"
rm -rf "$pm_home"
check "global.conf attaches the fragment from inside the home it configures" \
      "1" "$(grep -c "^tools.cmake.cmaketoolchain:user_toolchain=\[\"{{ os.path.join(conan_home_folder, 'malf-path-map.cmake')" "$MALF_ROOT/global.conf")"
check "every CI action that stages global.conf stages the fragment beside it" \
      "3" "$(grep -l 'malf-path-map.cmake' "$MALF_ROOT"/.github/actions/setup-{build-env,proof-linux,proof-msvc}/action.yml | wc -l | tr -d ' ')"

# package_twin compare: identical digests pass; one file's bytes fail, naming the package and the file.
tw_tmp="$(mktemp -d)"
printf '%s\n' '{"name":"p1","rrev":"r","package_id":"i","prev":"v","content":"c","files":{"lib/a.a":"1"}}' \
              '{"name":"p2","rrev":"r","package_id":"i","prev":"v","content":"c","files":{"lib/b.a":"2"}}' > "$tw_tmp/a"
cp "$tw_tmp/a" "$tw_tmp/b"
tw_same="$(python3 "$MALF_ROOT/package_twin.py" compare "$tw_tmp/a" "$tw_tmp/b")"; tw_same_rc=$?
sed -i 's/"prev":"v","content":"c","files":{"lib\/b.a":"2"}/"prev":"w","content":"d","files":{"lib\/b.a":"3"}/' "$tw_tmp/b"
tw_diff="$(python3 "$MALF_ROOT/package_twin.py" compare "$tw_tmp/a" "$tw_tmp/b")"; tw_diff_rc=$?
check "package_twin: two equal digests are N of N identical, exit 0" \
      "rc=0 2 of 2" "rc=$tw_same_rc $(grep -o '[0-9]* of [0-9]*' <<< "$tw_same")"
check "package_twin: a differing package revision and file fail, naming both" \
      "rc=1 1 of 2|  DIFFER p2: prev, content; files: lib/b.a" \
      "rc=$tw_diff_rc $(grep -o '[0-9]* of [0-9]*' <<< "$tw_diff")|$(grep DIFFER <<< "$tw_diff")"
rm -rf "$tw_tmp"

# package_relocate's scan: a producer path is a finding; the variable-anchored prefix, CMake's own
# lone-root guard and a path inside a comment are not.
rs_tmp="$(mktemp -d)"
mkdir -p "$rs_tmp/lib/cmake/p"
cat > "$rs_tmp/lib/cmake/p/pTargets.cmake" <<'CMK'
# the host's /dev/shm is named in prose here
if(_IMPORT_PREFIX STREQUAL "/")
  set_target_properties(p PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_IMPORT_PREFIX}/include"
    IMPORTED_CXX_MODULES_INCLUDE_DIRECTORIES "/home/ghrunner/_work/p/.conan2/p/b/p0123/b/src"
    IMPORTED_CXX_MODULES_COMPILE_OPTIONS "-fmacro-prefix-map=/tmp/producer/b=.")
CMK
check "package_relocate's scan names each producer path, and nothing a variable, the lone root or a comment holds" \
      "p: lib/cmake/p/pTargets.cmake:5 names the absolute path /home/ghrunner/_work/p/.conan2/p/b/p0123/b/src|p: lib/cmake/p/pTargets.cmake:6 names the absolute path /tmp/producer/b=.|" \
      "$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import package_relocate as r; from pathlib import Path; print("".join(f + "|" for f in r.scan([("p", "p/1", Path(sys.argv[2]))])))' "$MALF_ROOT" "$rs_tmp")"
rm -rf "$rs_tmp"
echo

echo "[7q7e] a recipe exports its git-TRACKED files: the helper narrows the export, the gate compares desk and fresh clone (DN-142.D4 (b))"
# A git repository with one package: tracked sources under the declared root and one outside it, an
# ignored build tree and an untracked draft under the root — the two kinds of file insight-eidos's
# globs swept from the desk on 2026-10-08. The home is staged by malf's own conf sync, so the arms
# read the helper and global.conf exactly as a desk export does. Real conan, no network.
ex_tmp="$(realpath "$(mktemp -d)")"
ex_home="$ex_tmp/home"; ex_repo="$ex_tmp/repo"; ex_pkg="$ex_repo/pkg"
CONAN_HOME="$ex_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
mkdir -p "$ex_pkg/src/sub" "$ex_pkg/docs"
ex_recipe() {   # <with helper: yes|no> — the fixture recipe, the helper call spelled as every first-party recipe spells it
    printf 'import runpy\nfrom conan import ConanFile\n\n\nclass Probe(ConanFile):\n    name = "ex_probe"\n    version = "0.0.1"\n    exports_sources = "CMakeLists.txt", "src/*"\n'
    [[ "$1" == yes ]] && printf '\n    def export_sources(self):\n        runpy.run_path(self.conf.get("user.malf:recipe_exports"))["narrow_to_tracked"](self)\n'
}
ex_recipe yes > "$ex_pkg/conanfile.py"
printf 'cmake_minimum_required(VERSION 3.20)\n' > "$ex_pkg/CMakeLists.txt"
printf 'int a();\n' > "$ex_pkg/src/a.cpp"; printf 'int b();\n' > "$ex_pkg/src/sub/b.cpp"
printf 'outside the root\n' > "$ex_pkg/docs/x.md"; printf 'build-*/\n' > "$ex_pkg/.gitignore"
git -C "$ex_repo" init -q && git -C "$ex_repo" add -A \
    && git -C "$ex_repo" -c user.name=t -c user.email=t@t commit -q -m fixture
mkdir -p "$ex_pkg/src/build-x"; printf 'object\n' > "$ex_pkg/src/build-x/junk.o"; printf 'int draft();\n' > "$ex_pkg/src/draft.cpp"
ex_export() {   # <folder> — `conan export` into the fixture home; prints rc, the exported file list, the log
    local out rc ref
    out="$(CONAN_HOME="$ex_home" conan export "$1" --format=json 2>"$ex_tmp/log")"; rc=$?
    ref="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["reference"])' "$out" 2>/dev/null)"
    echo "rc=$rc"
    [[ -n "$ref" ]] && (cd "$(CONAN_HOME="$ex_home" conan cache path "$ref" --folder export_source)" \
        && find . \( -type f -o -type l \) | sed 's|^\./||' | sort | tr '\n' ' ')
}
check "conan is on PATH: every arm below runs a real export (never a skip)" \
      "yes" "$(command -v conan >/dev/null && echo yes || echo "no conan on PATH")"
check "malf stages the helper beside global.conf in the home, byte for byte, and global.conf names it there" \
      "synced 1" "$(cmp -s "$MALF_ROOT/malf_recipe_exports.py" "$ex_home/malf_recipe_exports.py" && echo synced) $(grep -c "^user.malf:recipe_exports={{ os.path.join(conan_home_folder, 'malf_recipe_exports.py')" "$ex_home/global.conf")"
check "every CI action that stages global.conf stages the helper beside it, and overwrites a restored home's global.conf" \
      "3 0" "$(grep -l 'malf_recipe_exports.py' "$MALF_ROOT"/.github/actions/setup-{build-env,proof-linux,proof-msvc}/action.yml | wc -l | tr -d ' ') $(grep -c -E 'global.conf.*(has none|Test-Path)|! -f "\$HOME_DIR/global.conf"' "$MALF_ROOT"/.github/actions/setup-proof-{linux,msvc}/action.yml | awk -F: '{n+=$2} END{print n+0}')"
check "the helper narrows the export to the tracked files under the declared roots: no ignored build file, no untracked draft, nothing outside the roots" \
      "rc=0
CMakeLists.txt src/a.cpp src/sub/b.cpp |1" \
      "$(ex_export "$ex_pkg")|$(grep -c 'exports narrowed to the tracked files — 3 kept, 2 untracked or ignored dropped' "$ex_tmp/log")"
check "the build tree the removal emptied is gone from the export, not left as an empty directory" \
      "0" "$(ex_src="$(CONAN_HOME="$ex_home" conan cache path ex_probe/0.0.1 --folder export_source 2>&1)" \
             && find "$ex_src" -type d -empty | wc -l | tr -d ' ' || echo "no export: $ex_src")"
ex_recipe no > "$ex_tmp/plain.py"; cp "$ex_pkg/conanfile.py" "$ex_tmp/helper.py"; cp "$ex_tmp/plain.py" "$ex_pkg/conanfile.py"
check "WITHOUT the helper the same recipe sweeps the desk: the ignored build file and the draft are exported (the defect, reproduced)" \
      "rc=0
CMakeLists.txt src/a.cpp src/build-x/junk.o src/draft.cpp src/sub/b.cpp " "$(ex_export "$ex_pkg")"
cp "$ex_tmp/helper.py" "$ex_pkg/conanfile.py"
mkdir -p "$ex_tmp/archive"; cp -r "$ex_pkg" "$ex_tmp/archive/pkg"
check "outside a git checkout the export keeps the declared globs as found on disk, and says so in a warning" \
      "rc=0
CMakeLists.txt src/a.cpp src/build-x/junk.o src/draft.cpp src/sub/b.cpp |1" \
      "$(ex_export "$ex_tmp/archive/pkg")|$(grep -c 'WARN: malf: .* is not in a git checkout' "$ex_tmp/log")"
mv "$ex_home/malf_recipe_exports.py" "$ex_tmp/helper.bak"
check "a home that lacks the helper FAILS the export, naming the file — it never exports the disk silently" \
      "rc=1|1" "$(ex_export "$ex_pkg")|$(grep -c "No such file or directory: '$ex_home/malf_recipe_exports.py'" "$ex_tmp/log")"
mv "$ex_tmp/helper.bak" "$ex_home/malf_recipe_exports.py"

# The gate: desk against a fresh clone of the commit. Equal with the helper; the helper-less recipe,
# committed, reds naming the swept files; a modified tracked file is not judged.
printf 'ex_probe\t%s\t\n' "$ex_pkg" > "$ex_tmp/released.tsv"
ex_gate() {   # <scratch name> — package_exports verify over the fixture
    local out rc
    out="$(CONAN_HOME="$ex_home" python3 "$MALF_ROOT/package_exports.py" verify "$ex_home" "$ex_tmp/released.tsv" "$ex_tmp/$1" 2>&1)"; rc=$?
    printf 'rc=%s %s|%s' "$rc" "$(grep -o '[0-9]* of [0-9]* recipe[^;]*; [0-9]* differ, [0-9]* not judged' <<< "$out")" "$(grep -E 'DIFFER|UNJUDGED' <<< "$out" | sed 's/ desk [0-9a-f]* != clone [0-9a-f]*//')"
}
check "exports-verify: the helper's recipe exports the same revision from the desk and from a fresh clone, exit 0" \
      "rc=0 1 of 1 recipe(s) export the same recipe revision from the desk and from a fresh clone; 0 differ, 0 not judged|" "$(ex_gate g1)"
cp "$ex_tmp/plain.py" "$ex_pkg/conanfile.py"; git -C "$ex_repo" -c user.name=t -c user.email=t@t commit -q -am plain
check "exports-verify: a recipe that sweeps the desk is red, naming the files only the desk exports" \
      "rc=1 0 of 1 recipe(s) export the same recipe revision from the desk and from a fresh clone; 1 differ, 0 not judged|  DIFFER ex_probe:; 2 file(s) differ, 2 exported by the desk alone: export_source/src/build-x/junk.o, export_source/src/draft.cpp" \
      "$(ex_gate g2)"
cp "$ex_tmp/helper.py" "$ex_pkg/conanfile.py"
check "exports-verify: a package whose tracked files are modified on the desk is not judged, exit 2" \
      "rc=2 0 of 1 recipe(s) export the same recipe revision from the desk and from a fresh clone; 0 differ, 1 not judged|  UNJUDGED ex_probe: tracked files modified on the desk —" \
      "$(ex_gate g3)"

# The verb FAILING is its own exit, 3, and never 1: step 0's B2 reads exit 1 as a recipe that
# differs. Three failures before any verdict — a command the helper runs exits non-zero (the folder
# is no git checkout), the helper raises (a malformed recipes row), and malf cannot derive the
# recipe list (a workspace root with no scripts/workspace_layout.py) — each exits 3 and says FAILED.
ex_failed() {   # <tsv body> <scratch name> — package_exports verify over a doctored recipes list
    local out rc
    printf '%b' "$1" > "$ex_tmp/doctored.tsv"
    out="$(CONAN_HOME="$ex_home" python3 "$MALF_ROOT/package_exports.py" verify "$ex_home" "$ex_tmp/doctored.tsv" "$ex_tmp/$2" 2>&1)"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -c '^package_exports: FAILED — ' <<< "$out")"
}
mkdir -p "$ex_tmp/not-a-repo" "$ex_tmp/empty-ws"
check "exports-verify: a command the helper runs that fails (the folder is no git checkout) exits 3, never the differ code 1" \
      "rc=3 1" "$(ex_failed "ex_probe\t$ex_tmp/not-a-repo\t\n" g4)"
check "exports-verify: the helper raising (a recipes row with no folder) exits 3, never Python's own 1" \
      "rc=3 1" "$(ex_failed "ex_probe\n" g5)"
check "exports-verify: malf failing to derive the recipe list exits 3, naming the failure" \
      "rc=3 1" "$(out="$(MALF_WORKSPACE_ROOT="$ex_tmp/empty-ws" bash "$MALF_BIN" exports-verify 2>&1)"; rc=$?
                  printf 'rc=%s %s' "$rc" "$(grep -c '^malf exports-verify: FAILED — could not derive the first-party recipe list' <<< "$out")")"

# The POPULATION is the tracked recipe set, never a spelling. Selecting the recipes that assign
# `exports_sources` judged 28 of the workspace's 30 on 2026-10-09 and read green over the two that
# export through an `export_sources()` method alone (insight_canon_proof, insight_scenarios). The
# fixture spells every way a recipe exports — the attribute, the annotated attribute, `exports`,
# the method, nothing — beside a test_package recipe, a file merely ending in conanfile.py and an
# untracked recipe, none of which is a first-party recipe of the repository.
ex_ws="$ex_tmp/pop-ws"; mkdir -p "$ex_ws/scripts" "$ex_ws/r"
printf 'def declared_repos(root):\n    return ["opt", "r", "s"]\n\n\ndef required_repos(root):\n    return ["r", "s"]\n' \
    > "$ex_ws/scripts/workspace_layout.py"
ex_pop() {   # <folder> <class body line> — a fixture recipe under the fixture repository
    mkdir -p "$ex_ws/r/$1"
    printf 'from conan import ConanFile\n\n\nclass Probe(ConanFile):\n    name = "pop"\n    version = "0.0.1"\n%s\n' "$2" > "$ex_ws/r/$1/conanfile.py"
}
ex_pop attr   '    exports_sources = "src/*"'
ex_pop ann    '    exports_sources: tuple = ("src/*",)'
ex_pop exp    '    exports = "data.txt"'
ex_pop method '    def export_sources(self):
        pass'
ex_pop bare   ''
ex_pop attr/test_package '    exports_sources = "src/*"'
printf 'from conan import ConanFile\n' > "$ex_ws/r/method/legacy_conanfile.py"
git -C "$ex_ws/r" init -q && git -C "$ex_ws/r" add -A \
    && git -C "$ex_ws/r" -c user.name=t -c user.email=t@t commit -q -m fixture
ex_pop untracked '    exports_sources = "src/*"'
mkdir -p "$ex_ws/s"; printf 'from conan import ConanFile\n' > "$ex_ws/s/conanfile.py"
git -C "$ex_ws/s" init -q && git -C "$ex_ws/s" add -A \
    && git -C "$ex_ws/s" -c user.name=t -c user.email=t@t commit -q -m fixture
check "exports-verify's population is every tracked first-party recipe, whatever the spelling of its exports: the attribute, the annotated attribute, \`exports\`, the export_sources() method and a recipe exporting nothing — never a test_package, a *_conanfile.py or an untracked recipe" \
      "rc=0 r/ann r/attr r/bare r/exp r/method s " \
      "$(out="$(python3 "$MALF_ROOT/package_exports.py" recipes "$ex_ws" 2>"$ex_tmp/pop.err")"; rc=$?
         printf 'rc=%s %s' "$rc" "$(cut -f1 <<< "$out" | tr '\n' ' ')")"

# A declared repository ABSENT from the disk was skipped in silence, so the gate read green over
# recipes it never saw. A repository the CLONE MANIFESTS declare (`required_repos`: repos.txt and
# the toolchain's postCreate clone, all checked out by step 0) refuses, exit 3, named; one the root
# .gitignore alone declares (`opt` here; coderoast-corpora, coderoast-gitlab-ci on the desk) is
# named as not swept and the run goes on. `s` stays on disk, so the old skip reads exit 0 over it.
check "exports-verify: a declared repository that is not a clone-manifest one and is absent is named as not swept, never silently" \
      "1" "$(grep -c '^package_exports: not swept, declared by the root .gitignore alone and absent: opt$' "$ex_tmp/pop.err")"
mv "$ex_ws/r" "$ex_ws/r.away"
check "exports-verify: a clone-manifest repository absent from the disk REFUSES the population, exit 3, naming it — never a green over recipes never judged" \
      "rc=3 1 0" \
      "$(out="$(python3 "$MALF_ROOT/package_exports.py" recipes "$ex_ws" 2>"$ex_tmp/pop.err")"; rc=$?
         printf 'rc=%s %s %s' "$rc" "$(grep -c '^package_exports: FAILED — 1 repository(ies) the clone manifests declare are not on disk, so their recipes cannot be judged: r$' "$ex_tmp/pop.err")" "$(grep -c . <<< "$out")")"
mv "$ex_ws/r.away" "$ex_ws/r"
rm -rf "$ex_tmp"
echo

echo "[7q7f] twin-verify REFUSES before any build when its seed lacks a third-party binary the graphs need (DN-142.D4 (a))"
# A seed lacking a binary made each twin home build it in a folder named per home, and that folder
# reached a dependent's bytes: measured 2026-10-09, coderoast_infra_postgres differed across the two
# homes by libpqxx header paths, 18 of 20 identical. The fixture: one third-party package, one
# first-party package requiring it, a seed holding the binary, and the same seed without it.
tm_tmp="$(realpath "$(mktemp -d)")"
for tm_home in seed full lacking; do CONAN_HOME="$tm_tmp/$tm_home" bash "$MALF_BIN" profiles > /dev/null 2>&1; done
mkdir -p "$tm_tmp/tpdep" "$tm_tmp/ex_top"
printf 'from conan import ConanFile\n\n\nclass TpDep(ConanFile):\n    name = "tpdep"\n    version = "0.1"\n    package_type = "header-library"\n' > "$tm_tmp/tpdep/conanfile.py"
printf 'from conan import ConanFile\n\n\nclass ExTop(ConanFile):\n    name = "ex_top"\n    version = "0.1"\n    package_type = "header-library"\n    requires = "tpdep/0.1"\n' > "$tm_tmp/ex_top/conanfile.py"
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$tm_tmp/profile"
CONAN_HOME="$tm_tmp/seed" conan create "$tm_tmp/tpdep" -pr:a "$tm_tmp/profile" > "$tm_tmp/create.log" 2>&1
CONAN_HOME="$tm_tmp/seed" conan lock create "$tm_tmp/ex_top" -pr:a "$tm_tmp/profile" --lockfile-out="$tm_tmp/conan.lock" > /dev/null 2>&1
printf '{"version": "0.5", "requires": [], "build_requires": [], "python_requires": [], "config_requires": []}\n' > "$tm_tmp/empty.lock"
printf 'ex_top\t%s\t\n' "$tm_tmp/ex_top" > "$tm_tmp/released.tsv"
python3 "$MALF_ROOT/package_twin.py" seed "$tm_tmp/seed" "ex_" "$tm_tmp/full" "$tm_tmp/lacking" > /dev/null
CONAN_HOME="$tm_tmp/lacking" conan remove "tpdep/0.1:*" -c > /dev/null 2>&1
tm_probe() {   # <home> [<lockfile>] — the probe's exit and its verdict lines
    local out rc
    out="$(python3 "$MALF_ROOT/package_twin.py" missing "$tm_tmp/$1" "$tm_tmp/released.tsv" "ex_" "$tm_tmp/profile" "$tm_tmp/profile" "${2:-$tm_tmp/conan.lock}" 2>&1)"; rc=$?
    printf 'rc=%s|%s' "$rc" "$(grep -E 'MISSING|lacks' <<< "$out" | sed -E 's/#[0-9a-f]+:[0-9a-f]+//; s|'"$tm_tmp"'/||' | tr '\n' '|')"
}
check "the probe passes a seed holding every third-party binary the graph needs, exit 0" \
      "rc=0|package_twin: full lacks 0 third-party binary(ies) the released graphs need|" "$(tm_probe full)"
check "the probe names the third-party binary a seed lacks, exit 3" \
      "rc=3|  MISSING tpdep/0.1 (host)|package_twin: lacking lacks 1 third-party binary(ies) the released graphs need|" "$(tm_probe lacking)"
check "the probe resolves strictly against the lockfile it is handed: one that names no tpdep fails the probe, exit 1, judging nothing" \
      "rc=1|" "$(tm_probe full "$tm_tmp/empty.lock")"
rm -rf "$tm_tmp"
# The verb: the probe runs before the first create, and its finding is a refusal at exit 2 that
# names the remedy; the sentence claiming a missing binary is "built in each home alike" is gone.
tm_body="$(sed -n '/^cmd_twin_verify()/,/^}/p' "$MALF_BIN")"
check "twin-verify probes the seed before its first store-create, refuses at exit 2 on its finding, and names 'malf store-create' as the remedy" \
      "probe-first refuse-2 remedy" \
      "$( (( $(grep -n 'package_twin.py" missing' <<< "$tm_body" | cut -d: -f1 | head -1) < $(grep -n 'malf" store-create "${released_names\[@\]}"' <<< "$tm_body" | cut -d: -f1 | head -1) )) && printf probe-first) $(grep -A3 'probe_rc -eq 3' <<< "$tm_body" | grep -q 'return 2' && printf refuse-2) $(grep -q "Refresh the seed with 'malf store-create'" <<< "$tm_body" && printf remedy)"
check "twin-verify's two homes each run store-create over the released set, into a store of their own" \
      "2 2" "$(grep -c 'store-create "${released_names\[@\]}"' <<< "$tm_body") $(grep -cE 'MALF_STORE_DIR="\$root/(store-a|second-account/store-b)"' <<< "$tm_body")"
check "no line of malf still claims a missing third-party binary is built in each home alike" \
      "0" "$(grep -c 'built in each home alike' "$MALF_BIN")"
echo

echo "[7q7g] a step's key is its inputs' canonical bytes, an output's identity its decoded tree, and an equal key with differing bytes fails (DN-142.D2, DN-142.D3; ROADMAP N358)"
# The key: two spellings of one document give one key, and a value that is not an input (a float,
# an absolute path) is refused rather than keyed. The identity: the same tree at two absolute paths
# gives one digest; the root conanmanifest.txt is the ONE excluded file, and every other byte, mode
# or link target moves it. The store: a new key is stored, an equal output matches, a differing one
# at the same key fails and names the file, and the record is never rewritten.
as_tmp="$(realpath "$(mktemp -d)")"
as_py() { python3 -c "import sys; sys.path.insert(0, '$MALF_ROOT'); import artefact_store as s; $1" 2>&1 || true; }
check "two spellings of one document give the same key bytes, and the key is SHA-256 of the canonical form" \
      "same canonical" \
      "$(as_py 'import hashlib, json; a = s.key_of(json.loads("{\"b\": 1, \"a\": [true, null, \"x\"]}")); b = s.key_of({"a": [True, None, "x"], "b": 1}); print("same" if a == b else f"{a} != {b}", "canonical" if a == hashlib.sha256(b"{\"a\":[true,null,\"x\"],\"b\":1}").hexdigest() else "not-canonical")')"
check "a float is refused, never keyed" \
      "refused" "$(as_py 's.key_of({"step": {"width": 1.5}})' | grep -q 'a float is not a key input' && echo refused)"
check "an absolute path is refused, never keyed, wherever it sits" \
      "refused refused" "$(as_py 's.key_of({"sources": ["/home/a/x"]})' | grep -q 'an absolute path is never a key input' && printf refused) $(as_py 's.key_of({"profile": "C:/x"})' | grep -q 'an absolute path is never a key input' && printf refused)"
mkdir -p "$as_tmp/one/lib/sub" "$as_tmp/a-much-longer-second-path/lib/sub"
for as_dir in "$as_tmp/one" "$as_tmp/a-much-longer-second-path"; do
    printf 'manifest 1\n' > "$as_dir/conanmanifest.txt"; printf 'info\n' > "$as_dir/conaninfo.txt"
    printf 'lib\n' > "$as_dir/lib/libx.a"; printf 'nested\n' > "$as_dir/lib/sub/conanmanifest.txt"
    ln -s libx.a "$as_dir/lib/libx.so"
done
as_digest() { python3 "$MALF_ROOT/artefact_store.py" tree "$1"; }
as_base="$(as_digest "$as_tmp/one")"
check "one tree at two absolute paths has one digest" "$as_base" "$(as_digest "$as_tmp/a-much-longer-second-path")"
as_moves() {   # <label> <mutation> — the digest after the mutation, compared with the base; then undone
    local after; eval "$2"; after="$(as_digest "$as_tmp/one")"
    rm -rf "$as_tmp/one"; cp -a "$as_tmp/a-much-longer-second-path" "$as_tmp/one"
    [[ "$after" == "$as_base" ]] && echo "unmoved" || echo "moved"
}
check "the root conanmanifest.txt is excluded: rewriting it leaves the digest" \
      "unmoved" "$(as_moves root-manifest 'printf "manifest 2\n" > "$as_tmp/one/conanmanifest.txt"')"
check "an empty directory is not part of the tree" \
      "unmoved" "$(as_moves empty-dir 'mkdir "$as_tmp/one/empty"')"
check "every other byte moves it: conaninfo.txt, a library, a NESTED conanmanifest.txt" \
      "moved moved moved" \
      "$(as_moves info 'printf "infO\n" > "$as_tmp/one/conaninfo.txt"') $(as_moves lib 'printf "liB\n" > "$as_tmp/one/lib/libx.a"') $(as_moves nested 'printf "nesteD\n" > "$as_tmp/one/lib/sub/conanmanifest.txt"')"
check "a mode, a link target, a rename and a new file move it" \
      "moved moved moved moved" \
      "$(as_moves mode 'chmod +x "$as_tmp/one/lib/libx.a"') $(as_moves link 'ln -sfn conaninfo.txt "$as_tmp/one/lib/libx.so"') $(as_moves rename 'mv "$as_tmp/one/lib/libx.a" "$as_tmp/one/lib/liby.a"') $(as_moves new 'printf "x" > "$as_tmp/one/new"')"

# The store over two real creates: a third-party package and a first-party one requiring it,
# created in two conan homes at two absolute paths — one key, a stored record and a match; a
# changed export moves the key; a tampered output at an equal key is a mismatch naming the file.
mkdir -p "$as_tmp/tpdep" "$as_tmp/ex_top/include"
printf 'from conan import ConanFile\n\n\nclass TpDep(ConanFile):\n    name = "tpdep"\n    version = "0.1"\n    package_type = "header-library"\n' > "$as_tmp/tpdep/conanfile.py"
cat > "$as_tmp/ex_top/conanfile.py" <<'PYR'
from conan import ConanFile
from conan.tools.files import copy


class ExTop(ConanFile):
    name = "ex_top"
    version = "0.1"
    package_type = "header-library"
    requires = "tpdep/0.1"
    exports_sources = "include/*"

    def package(self):
        copy(self, "*.h", self.source_folder, self.package_folder)
PYR
printf '#pragma once\n' > "$as_tmp/ex_top/include/top.h"
printf '{"compiler": "fixture"}' > "$as_tmp/toolchain.json"
as_create() {   # <home name> — the graph of a create of ex_top in that home
    local home="$as_tmp/$1"
    CONAN_HOME="$home" bash "$MALF_BIN" profiles > /dev/null 2>&1
    printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$home/profiles/fixture"
    CONAN_HOME="$home" conan create "$as_tmp/tpdep" -pr:a fixture > "$as_tmp/$1.tp.log" 2>&1
    CONAN_HOME="$home" conan create "$as_tmp/ex_top" -pr:a fixture --format=json > "$as_tmp/$1.graph.json" 2> "$as_tmp/$1.log"
}
as_step() {   # <home name> — the step's verdict line and exit, the key and digests elided
    local out rc
    out="$(python3 "$MALF_ROOT/artefact_store.py" conan-step "$as_tmp/store" "$as_tmp/$1" "$as_tmp/$1.graph.json" ex_top fixture "$MALF_ROOT" "$as_tmp/toolchain.json" "ex_" 2>&1)"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -oE 'artefact_store: [A-Z]+ ex_top package|[0-9]+ file\(s\): .*' <<< "$out" | sed 's/artefact_store: //' | tr '\n' ' ')"
}
as_key() { python3 -c 'import json, sys; print(*sorted(r.removesuffix(".json") for r in sys.argv[1:]))' $(ls "$as_tmp/store/records"); }
as_create home-a; as_create home-b-at-a-longer-path
check "the first create of a key is STORED" "rc=0 STORED ex_top package " "$(as_step home-a)"
as_first="$(as_key)"
check "the same step in a second home at another path has the same key, and its output MATCHES" \
      "rc=0 MATCH ex_top package |$as_first" "$(as_step home-b-at-a-longer-path)|$(as_key)"
as_folder="$(CONAN_HOME="$as_tmp/home-b-at-a-longer-path" conan cache path "$(python3 -c 'import json, sys; n = [v for k, v in json.load(open(sys.argv[1]))["graph"]["nodes"].items() if v["name"] == "ex_top"][0]; print(n["ref"].split("#")[0] + "#" + n["rrev"] + ":" + n["package_id"] + "#" + n["prev"])' "$as_tmp/home-b-at-a-longer-path.graph.json")")"
as_record="$(sha256sum "$as_tmp/store/records/$as_first.json")"
printf '#pragma once // tampered\n' > "$as_folder/include/top.h"
check "a differing output at an equal key is a MISMATCH, exit 1, naming the file" \
      "rc=1 MISMATCH ex_top package 1 file(s): include/top.h " "$(as_step home-b-at-a-longer-path)"
check "the mismatch is an event beside the record, and the record is not rewritten" \
      "1 $as_record" "$(ls "$as_tmp/store/mismatches" | wc -l | tr -d ' ') $(sha256sum "$as_tmp/store/records/$as_first.json")"
printf '#pragma once\n#define TOP 1\n' > "$as_tmp/ex_top/include/top.h"
as_create home-a
check "a changed exported source moves the key: a second record is STORED" \
      "rc=0 STORED ex_top package |2" "$(as_step home-a)|$(ls "$as_tmp/store/records" | wc -l | tr -d ' ')"

# DN-142.D16 (a): a store act's create builds from source only its own package and third-party
# ones. ex_mid is first-party (the `ex_` namespace) and exported with no binary: the old spelling
# (`--build=missing`) built it silently inside ex_low's create, the act's spelling fails naming it,
# and once ex_mid's own create made it the consumer's create passes, its third-party tpdep built.
# (b): a step whose graph links a first-party digest no record of that package's own step holds is
# REFUSED before anything is stored, and the read-only `upstreams` replay names such a record.
mkdir -p "$as_tmp/ex_mid" "$as_tmp/ex_low"
printf 'from conan import ConanFile\n\n\nclass ExMid(ConanFile):\n    name = "ex_mid"\n    version = "0.1"\n    package_type = "header-library"\n    requires = "tpdep/0.1"\n' > "$as_tmp/ex_mid/conanfile.py"
printf 'from conan import ConanFile\n\n\nclass ExLow(ConanFile):\n    name = "ex_low"\n    version = "0.1"\n    package_type = "header-library"\n    requires = "ex_mid/0.1"\n' > "$as_tmp/ex_low/conanfile.py"
as_build_args() { bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e; _malf_store_build_args "$2" "$3"' _ "$MALF_BIN" "$1" "$2"; }
as_fresh() {   # <home name> — tpdep and ex_mid exported, no binary of either
    local home="$as_tmp/$1"
    CONAN_HOME="$home" bash "$MALF_BIN" profiles > /dev/null 2>&1
    printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$home/profiles/fixture"
    CONAN_HOME="$home" conan export "$as_tmp/tpdep" > /dev/null 2>&1
    CONAN_HOME="$home" conan export "$as_tmp/ex_mid" > /dev/null 2>&1
}
as_act() {   # <home name> <package> [build args...] — the create's exit and a missing-binary line
    local name="$1" package="$2" rc; shift 2
    CONAN_HOME="$as_tmp/$name" conan create "$as_tmp/$package" -pr:a fixture "$@" --format=json \
        > "$as_tmp/$name.$package.graph.json" 2> "$as_tmp/$name.$package.log"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -oE "Missing prebuilt package for '[a-z_]+/0.1'" "$as_tmp/$name.$package.log" | sed -n 1p)"
}
check "the act's build arguments: the step's own package, then one missing-exclusion per owned namespace" \
      "--build=ex_low/*|--build=missing:~ex_*|--build=missing:~zz_*" "$(as_build_args ex_low 'ex_ zz_' | paste -sd'|')"
as_fresh home-old
check "RED FIRST — the old spelling --build=missing builds a missing first-party upstream inside the consumer's create" \
      "rc=0 " "$(as_act home-old ex_low --build=missing --build='ex_low/*')"
as_fresh home-act
mapfile -t as_low_args < <(as_build_args ex_low ex_)
mapfile -t as_mid_args < <(as_build_args ex_mid ex_)
check "(a) the act's spelling FAILS the create, naming the first-party package whose binary is missing" \
      "rc=1 Missing prebuilt package for 'ex_mid/0.1'" "$(as_act home-act ex_low "${as_low_args[@]}")"
check "(a) once ex_mid's own create made it, the consumer's create passes, its third-party upstream built from source" \
      "rc=0 |rc=0 " "$(as_act home-act ex_mid "${as_mid_args[@]}")|$(as_act home-act ex_low "${as_low_args[@]}")"
as_record_step() {   # <package> — the step's verdict word and exit over the upstream store
    local out rc
    out="$(python3 "$MALF_ROOT/artefact_store.py" conan-step "$as_tmp/ustore" "$as_tmp/home-act" "$as_tmp/home-act.$1.graph.json" "$1" fixture "$MALF_ROOT" "$as_tmp/toolchain.json" "ex_" 2>&1)"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -oE "(STORED|MATCH) $1 package|REFUSED $1: [0-9]+ upstream|ex_mid/host" <<< "$out" | tr '\n' ' ')"
}
check "(b) RED — a step linking a first-party upstream no record of that package's own step holds is REFUSED, naming it, and stores nothing" \
      "rc=1 REFUSED ex_low: 1 upstream ex_mid/host |0" "$(as_record_step ex_low)|$(ls "$as_tmp/ustore/records" 2>/dev/null | wc -l | tr -d ' ')"
check "(b) once the upstream's own step is recorded, the consumer's step is STORED" \
      "rc=0 STORED ex_mid package |rc=0 STORED ex_low package " "$(as_record_step ex_mid)|$(as_record_step ex_low)"
check "(b) the read-only replay is silent over a store whose every upstream is recorded" \
      "rc=0" "$(python3 "$MALF_ROOT/artefact_store.py" upstreams "$as_tmp/ustore" > /dev/null; echo "rc=$?")"
rm "$(grep -l '"package": "ex_mid"' "$as_tmp/ustore/records/"*.json)"
check "(b) the replay reds a record whose upstream no record holds any more, naming the package" \
      "rc=1 ex_low: 1 of 1 upstream(s) unrecorded" \
      "$(out="$(python3 "$MALF_ROOT/artefact_store.py" upstreams "$as_tmp/ustore")"; rc=$?; printf 'rc=%s %s' "$rc" "$(grep -oE 'ex_low: [0-9]+ of [0-9]+ upstream\(s\) unrecorded' <<< "$out")")"
rm -rf "$as_tmp"
echo

echo "[7q7h] a verdict is a keyed record binding the trees it judged; a fail is stored, a flip at an equal key fails, and a run that judged nothing writes no record (DN-142.D7; ROADMAP N358)"
# The predicate is `malf format --check`, judged in an export of the fixture's TRACKED tree beside
# an export of this malf's, so an untracked file is never judged. The definition a driving tool adds
# (MALF_STORE_DEFINITION) is part of the key, and a malformed one is refused rather than ignored.
sv_tmp="$(realpath "$(mktemp -d)")"
sv_repo="$sv_tmp/fixture-repo"; mkdir -p "$sv_repo/src"
git -C "$sv_repo" init -q && git -C "$sv_repo" config user.email t@t && git -C "$sv_repo" config user.name t
printf 'int main() { return 0; }\n' > "$sv_repo/src/main.cpp"
(cd "$sv_repo" && bash "$MALF_BIN" format > /dev/null 2>&1)   # the clean fixture is malf's own style
git -C "$sv_repo" add -A && git -C "$sv_repo" commit -qm clean
sv_step() {   # [env assignments...] — the verdict lines and the exit, keys and digests elided
    local out rc
    out="$(env "$@" MALF_STORE_DIR="$sv_tmp/store" bash "$MALF_BIN" store-verdict format "$sv_repo" 2>&1)"; rc=$?
    printf 'rc=%s %s' "$rc" "$(grep -oE 'artefact_store: (STORED|MATCH|MISMATCH|FAIL|UNJUDGED) fixture-repo (verdict|format)|verdict (pass|fail) findings|first src/[a-z]+\.cpp|refused — MALF_STORE_DEFINITION' <<< "$out" | sed 's/artefact_store: //' | tr '\n' ' ')"
}
sv_records() { ls "$sv_tmp/store/records" 2>/dev/null | wc -l | tr -d ' '; }
check "a clean tracked tree is judged: its pass is STORED under a new key" \
      "rc=0 STORED fixture-repo verdict verdict pass findings |1" "$(sv_step)|$(sv_records)"
printf 'int   bad(  ) {return 1;}\n' > "$sv_repo/src/untracked.cpp"
check "an untracked file is never judged: the same key, and the pass MATCHES" \
      "rc=0 MATCH fixture-repo verdict verdict pass findings |1" "$(sv_step)|$(sv_records)"
rm "$sv_repo/src/untracked.cpp"
check "a driving tool's tree joins the definition and moves the key" \
      "rc=0 STORED fixture-repo verdict verdict pass findings |2" "$(sv_step MALF_STORE_DEFINITION="driver=$MALF_ROOT")|$(sv_records)"
mkdir -p "$sv_tmp/driver/sub" && git -C "$sv_tmp/driver" init -q
printf 'a\n' > "$sv_tmp/driver/sub/code.py"; printf 'b\n' > "$sv_tmp/driver/outside.md"
git -C "$sv_tmp/driver" add -A && git -C "$sv_tmp/driver" -c user.email=t@t -c user.name=t commit -qm driver
sv_dir() { python3 -c "import sys; sys.path.insert(0, '$MALF_ROOT'); import artefact_store as s; from pathlib import Path; print(s.directory_tree_id(Path('$sv_tmp/driver/sub')))"; }
sv_sub="$(git -C "$sv_tmp/driver" rev-parse HEAD:sub)"
check "a driver directory's definition is its own tracked subtree, as on disk: a file outside it leaves it, a tracked edit inside it moves it" \
      "$sv_sub $sv_sub moved" \
      "$(sv_dir) $(printf 'c\n' > "$sv_tmp/driver/outside.md"; sv_dir) $(printf 'd\n' > "$sv_tmp/driver/sub/code.py"; [[ "$(sv_dir)" != "$sv_sub" ]] && echo moved)"
check "a malformed definition is refused, never ignored" \
      "rc=1 refused — MALF_STORE_DEFINITION |2" "$(sv_step MALF_STORE_DEFINITION="malf=$MALF_ROOT")|$(sv_records)"
printf 'int   bad(  ) {return 1;}\n' > "$sv_repo/src/bad.cpp"
git -C "$sv_repo" add -A && git -C "$sv_repo" commit -qm misformatted
check "a misformatted tracked file is a FAIL, stored under its own key, exit 1, naming the file" \
      "rc=1 STORED fixture-repo verdict verdict fail findings FAIL fixture-repo format first src/bad.cpp |3" \
      "$(sv_step)|$(sv_records)"
sv_fail="$(grep -l '"verdict": "fail"' "$sv_tmp"/store/records/*.json)"
python3 - "$sv_fail" <<'PYF'
import json, sys
record = json.load(open(sys.argv[1]))
record["outputs"]["verdict"]["verdict"] = "pass"
json.dump(record, open(sys.argv[1], "w"))
PYF
check "a verdict that flips at an equal key is a MISMATCH, exit 1, and an event is written beside" \
      "rc=1 MISMATCH fixture-repo verdict verdict pass findings verdict fail findings FAIL fixture-repo format first src/bad.cpp |1" \
      "$(sv_step)|$(ls "$sv_tmp/store/mismatches" | wc -l | tr -d ' ')"
git -C "$sv_repo" rm -rq src && printf 'no C++ here\n' > "$sv_repo/README" && git -C "$sv_repo" add README && git -C "$sv_repo" commit -qm empty
check "a tree with no C++ is UNJUDGED, exit 2, and writes no record" \
      "rc=2 UNJUDGED fixture-repo format |3" "$(sv_step)|$(sv_records)"
rm -rf "$sv_tmp"
echo

echo "[7q7i] every ship-leg act resolves against malf/conan.lock, strictly: never the home's own revision, never a requirement the lock does not name (W486 G2)"
# note: measured 2026-10-10, the first DN-142.D16 store-create resolved with no lockfile and took the
# home's own boost recipe revision, cmake, hiredis and libpq where malf/conan.lock names others. The
# fixture is that shape in miniature: a third-party package at two recipe revisions in one home, a
# lock naming the OLDER one, a lock naming one the home no longer holds, a requirement no lock names,
# and no remote, so nothing a run resolves can come from anywhere but the home and the lock.
ls_tmp="$(realpath "$(mktemp -d)")"; ls_home="$ls_tmp/home"
CONAN_HOME="$ls_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
CONAN_HOME="$ls_home" conan remote remove conancenter > /dev/null 2>&1
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$ls_home/profiles/fixture"
mkdir -p "$ls_tmp/tpdep" "$ls_tmp/tpextra" "$ls_tmp/ls_top" "$ls_tmp/ls_other" "$ls_tmp/lock-a" "$ls_tmp/lock-b" "$ls_tmp/no-lock"
ls_recipe() {   # <dir> <class> <name> <version> <extra line> — a header-library fixture recipe
    printf 'from conan import ConanFile\n\n\nclass %s(ConanFile):\n    name = "%s"\n    version = "%s"\n    package_type = "header-library"\n    %s\n' \
           "$2" "$3" "$4" "$5" > "$ls_tmp/$1/conanfile.py"
}
ls_conan() { CONAN_HOME="$ls_home" conan "$@" > /dev/null 2>&1; }
ls_recipe ls_top LsTop ls_top 0.1 'requires = "tpdep/[>=1.0 <2]"'
ls_recipe ls_other LsOther ls_other 0.1 'requires = "tpdep/[>=1.0 <2]", "tpextra/1.0"'
ls_recipe tpextra TpExtra tpextra 1.0 'description = "named by no lock"'
ls_recipe tpdep TpDep tpdep 1.0 'description = "revision a"'
ls_conan create "$ls_tmp/tpdep" -pr:a fixture
ls_conan lock create "$ls_tmp/ls_top" -pr:a fixture --lockfile-out="$ls_tmp/lock-a/conan.lock"
ls_recipe tpdep TpDep tpdep 1.0 'description = "revision b"'
ls_conan create "$ls_tmp/tpdep" -pr:a fixture
ls_conan lock create "$ls_tmp/ls_top" -pr:a fixture --lockfile-out="$ls_tmp/lock-b/conan.lock"
ls_conan create "$ls_tmp/tpextra" -pr:a fixture
for ls_dir in lock-a lock-b no-lock; do cp "$MALF_ROOT/malf_recipe_tests.py" "$ls_tmp/$ls_dir/"; done
ls_rev() { grep -o 'tpdep/1.0#[0-9a-f]*' "$ls_tmp/$1/conan.lock" | cut -d'#' -f2; }
ls_rev_a="$(ls_rev lock-a)"; ls_rev_b="$(ls_rev lock-b)"
check "the fixture: two distinct tpdep revisions, each lock naming one, and the home's latest is the second" \
      "distinct latest=b" \
      "$([[ -n "$ls_rev_a" && -n "$ls_rev_b" && "$ls_rev_a" != "$ls_rev_b" ]] && printf distinct) latest=$(CONAN_HOME="$ls_home" conan list 'tpdep/1.0#latest' --format=json 2>/dev/null | python3 -c 'import json, sys; revs = [r for v in json.load(sys.stdin)["Local Cache"].values() for r in v["revisions"]]; print("b" if revs == [sys.argv[1]] else revs)' "$ls_rev_b")"
# The act's own create, sourced from malf with the lockfile malf derives from MALF_DIR, as
# cmd_store_create runs it. Prints the exit status and the tpdep revision the create's graph holds.
ls_step() {   # <malf dir> <package>
    rm -f "$ls_tmp/graph.json" "$ls_tmp/create.log"
    CONAN_HOME="$ls_home" bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e
        MALF_DIR="$2"; MALF_PROFILE="$3"; MALF_BUILD_PROFILE="$3"; MALF_CONFIG=Release
        _malf_writer_create_args || exit 3
        _malf_ship_lock_args || exit 2
        _malf_store_create_step "$4" "$5" ls_ "$6/graph.json" "$6/create.log"' \
        _ "$MALF_BIN" "$ls_tmp/$1" "$ls_home/profiles/fixture" "$2" "$ls_tmp/$2" "$ls_tmp" 2> "$ls_tmp/refusal.log"
    local rc=$? revision
    revision="$(python3 -c 'import json, sys; print(" ".join(n["rrev"] for n in json.load(open(sys.argv[1]))["graph"]["nodes"].values() if n["name"] == "tpdep") or "none")' "$ls_tmp/graph.json" 2>/dev/null)"
    printf 'rc=%s tpdep=%s' "$rc" "${revision:-no-graph}"
}
ls_name() { sed "s/$ls_rev_a/a/g; s/$ls_rev_b/b/g"; }
check "a home holding a NEWER revision than the lock: the act resolves the lock's revision, never the home's latest" \
      "rc=0 tpdep=a" "$(ls_step lock-a ls_top | ls_name)"
check "a requirement no lock names FAILS the act, naming it; --lockfile-partial would have resolved it from the home" \
      "rc=1 1" "$(ls_step lock-b ls_other | cut -d' ' -f1) $(grep -c "Requirement 'tpextra/1.0' not in lockfile" "$ls_tmp/create.log")"
ls_conan remove "tpdep/1.0#$ls_rev_b" -c
check "a home holding only an OLDER revision than the lock: the act fails, naming the package, and never falls back to the home's" \
      "rc=1 1" "$(ls_step lock-b ls_top | cut -d' ' -f1) $(grep -c "Package 'tpdep/1.0' not resolved" "$ls_tmp/create.log")"
check "no malf/conan.lock: the act refuses at exit 2 naming the file, and no create runs" \
      "rc=2 tpdep=no-graph 1" "$(ls_step no-lock ls_top) $(grep -c "resolves only against $ls_tmp/no-lock/conan.lock" "$ls_tmp/refusal.log")"
# relocate-verify's closure is the graph the lock resolves, plus the package under judgement: the
# producer home holds both tpdep revisions, and the fresh home receives the lock's alone.
ls_conan remove "ls_top/*" -c
ls_conan create "$ls_tmp/tpdep" -pr:a fixture
ls_conan create "$ls_tmp/ls_top" -pr:a fixture --lockfile="$ls_tmp/lock-a/conan.lock"
printf 'ls_top\t%s\t\n' "$ls_tmp/ls_top" > "$ls_tmp/released.tsv"
check "relocate-verify's fresh home receives the locked tpdep revision, never the producer home's latest" \
      "a" "$(python3 -I -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import package_relocate as r
source, fresh, lock = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
rows = r.released_refs(source, Path(sys.argv[5]), "")
(fresh / "locks").mkdir(parents=True)
r.seed(source, fresh, "fixture", rows, r.subject_locks(source, lock, rows, fresh / "locks"))' "$MALF_ROOT" "$ls_home" "$ls_tmp/fresh" "$ls_tmp/lock-a/conan.lock" "$ls_tmp/released.tsv" > /dev/null 2>&1
         CONAN_HOME="$ls_tmp/fresh" conan list 'tpdep/1.0#*' --format=json 2>/dev/null | python3 -c 'import json, sys; print(" ".join(r for v in json.load(sys.stdin)["Local Cache"].values() for r in v.get("revisions", {})) or "none")' | ls_name)"
# Every other resolution of the ship leg splices the same arguments, read from malf's source: the
# store-build step, and the lockfile twin-verify's probe and relocate-verify
# resolve against. Each verb takes it before its first conan call and refuses without it.
ls_body() { awk "/^$1\\(\\)/,/^}/" "$MALF_BIN"; }
check "store-create's create and store-build's build splice the ship lockfile, and malf passes --lockfile-partial nowhere on the ship leg" \
      "1 1 0" \
      "$(ls_body _malf_store_create_step | grep -c '"${_MALF_SHIP_LOCK\[@\]}"') $(ls_body _malf_store_build_one | grep -c '"${_MALF_SHIP_LOCK\[@\]}"') $(for fn in cmd_store_create _malf_store_create_step _malf_store_build_one cmd_store_build cmd_twin_verify cmd_relocate_verify; do ls_body "$fn"; done | grep -c -- '--lockfile-partial')"
check "store-create, store-build, twin-verify and relocate-verify each take the ship lockfile and refuse without it" \
      "4" "$(for fn in cmd_store_create cmd_store_build cmd_twin_verify cmd_relocate_verify; do ls_body "$fn" | grep -cE '_malf_ship_lock_args \|\| (return|exit)'; done | awk '{n += $1} END {print n}')"
check "twin-verify's probe and relocate-verify resolve against the lockfile malf took" \
      "1 1" "$(ls_body cmd_twin_verify | grep -c 'package_twin.py" missing .*"${_MALF_SHIP_LOCK\[0\]#--lockfile=}"') $(ls_body cmd_relocate_verify | grep -c '"${_MALF_SHIP_LOCK\[0\]#--lockfile=}"')"
check "the reusable release workflow's create resolves strictly against the toolchain checkout's conan.lock" \
      "1 0" "$(grep -c -- '--lockfile="$MALF_TOOLCHAIN_DIR/conan.lock"' "$MALF_ROOT/.github/workflows/coderoast-release.yml") $(grep -vE '^\s*#' "$MALF_ROOT/.github/workflows/coderoast-release.yml" | grep -c -- '--lockfile-partial')"
rm -rf "$ls_tmp"
echo

echo "[7q7j] malf lock accumulates every root under every profile of malf/profiles/, so a requirement one OS alone adds is in the lock a strict resolve on that OS reads (W486 G2)"
# note: measured 2026-10-10, malf/conan.lock accumulated under the desk profile alone, and a strict
# resolve of the Windows sift graph failed on `Requirement 'nasm/2.16.01' not in lockfile
# 'build_requires'`: openssl's recipe adds nasm and strawberryperl on os=Windows only. The fixture is
# that shape in miniature: one root whose tool requirement exists only under a Windows profile, a
# registry holding a Linux and a Windows profile, and no remote.
lk_tmp="$(realpath "$(mktemp -d)")"; lk_home="$lk_tmp/home"
CONAN_HOME="$lk_home" bash "$MALF_BIN" profiles > /dev/null 2>&1
CONAN_HOME="$lk_home" conan remote remove conancenter > /dev/null 2>&1
mkdir -p "$lk_tmp/malf/profiles" "$lk_tmp/ws/lk_root" "$lk_tmp/wintool"
printf '[settings]\nos=Linux\narch=x86_64\nbuild_type=Release\n' > "$lk_tmp/malf/profiles/lk-linux"
printf '[settings]\nos=Windows\narch=x86_64\nbuild_type=Release\n' > "$lk_tmp/malf/profiles/lk-windows"
printf 'from conan import ConanFile\n\n\nclass WinTool(ConanFile):\n    name = "wintool"\n    version = "1.0"\n    package_type = "application"\n' > "$lk_tmp/wintool/conanfile.py"
printf 'from conan import ConanFile\n\n\nclass LkRoot(ConanFile):\n    name = "lk_root"\n    version = "0.1"\n    settings = "os"\n    package_type = "header-library"\n\n    def build_requirements(self):\n        if self.settings.os == "Windows":\n            self.tool_requires("wintool/1.0")\n' > "$lk_tmp/ws/lk_root/conanfile.py"
CONAN_HOME="$lk_home" conan export "$lk_tmp/wintool" > /dev/null 2>&1
# The verb as malf runs it, its root enumeration and its SBOM re-derivation replaced by the fixture's.
lk_run() {
    CONAN_HOME="$lk_home" bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e
        MALF_DIR="$2"; MALF_WORKSPACE_ROOT="$3"; MALF_PROFILE="$2/profiles/lk-linux"
        _malf_workspace_packages() { printf "lk_root\t%s\n" "$MALF_WORKSPACE_ROOT/lk_root"; }
        cmd_sbom() { :; }
        cmd_lock' _ "$MALF_BIN" "$lk_tmp/malf" "$lk_tmp/ws" > "$lk_tmp/lock.log" 2>&1
    printf 'rc=%s' "$?"
}
lk_strict() {   # <profile>: a strict resolve of the root under that profile, against the lock
    CONAN_HOME="$lk_home" conan graph info "$lk_tmp/ws/lk_root" -pr:a "$lk_tmp/malf/profiles/$1" \
        --lockfile="$lk_tmp/malf/conan.lock" > "$lk_tmp/$1.log" 2>&1
    printf 'rc=%s' "$?"
}
check "malf lock writes a lock naming the Windows-only tool requirement, and a strict Windows resolve passes against it" \
      "rc=0 1 rc=0 rc=0" \
      "$(lk_run) $(grep -c '"wintool/1.0#' "$lk_tmp/malf/conan.lock" 2>/dev/null) $(lk_strict lk-windows) $(lk_strict lk-linux)"
check "the control: a lock accumulated under the Linux profile alone fails the strict Windows resolve, naming the requirement" \
      "rc=0 rc=1 1" \
      "$(CONAN_HOME="$lk_home" conan lock create "$lk_tmp/ws/lk_root" -pr:a "$lk_tmp/malf/profiles/lk-linux" --lockfile-out="$lk_tmp/malf/conan.lock" > /dev/null 2>&1; printf 'rc=%s' "$?") $(lk_strict lk-windows) $(grep -c "Requirement 'wintool/1.0' not in lockfile" "$lk_tmp/lk-windows.log")"
rm -rf "$lk_tmp"
echo

echo "[7q8] at job end every conan home drops its build and temp folders and its superseded versions, and reports its own size"

# The script runs from a toolchain tree and reads that tree's conan.lock, three levels up, so the
# fixture is a copy of it inside a tree carrying a lock of its own.
cc_tmp="$(realpath "$(mktemp -d)")"
cc_tree="$cc_tmp/toolchain/.github/actions/setup-build-env"; mkdir -p "$cc_tree"
cp "$MALF_ROOT/.github/actions/setup-build-env/clean-conan-homes.sh" "$cc_tree/"
cc_script="$cc_tree/clean-conan-homes.sh"
cat > "$cc_tmp/toolchain/conan.lock" <<'LOCK'
{"version": "0.5",
 "requires": ["zlib/1.3.2#aaaa%1.0", "cc_first/2.0.0#bbbb%1.0"],
 "build_requires": ["cmake/4.4.3#cccc%1.0"],
 "python_requires": []}
LOCK
cc_bin="$cc_tmp/bin"; cc_home="$cc_tmp/conan"; mkdir -p "$cc_bin" "$cc_home"
# A conan whose cache is one line per recipe reference in <home>/refs, logging every call.
cat > "$cc_bin/conan" <<'STUB'
#!/usr/bin/env bash
echo "$CONAN_HOME :: $*" >> "$CC_LOG"
case "$1" in
    list)   python3 -c 'import json, sys; print(json.dumps({"Local Cache": {line.strip(): {} for line in open(sys.argv[1]) if line.strip()}}))' "$CONAN_HOME/refs" ;;
    remove) grep -vxF -- "$2" "$CONAN_HOME/refs" > "$CONAN_HOME/refs.next" || true; mv "$CONAN_HOME/refs.next" "$CONAN_HOME/refs" ;;
esac
exit 0
STUB
chmod +x "$cc_bin/conan"
for h in "$cc_home" "$cc_home/gcc16-release" "$cc_home/cut-verify"; do
    mkdir -p "$h/p/b/build1" && touch "$h/settings.yml"
    printf 'zlib/1.3.2\ncmake/4.4.3\n' > "$h/refs"
done
# The base holds two versions the lock no longer names, one it names, and a package the lock does
# not know at all; a keyed home holds a superseded version of its own.
printf 'zlib/1.3.1\ncc_first/1.9.0\nunlocked_tool/0.1\n' >> "$cc_home/refs"
printf 'cmake/3.31.0\n' >> "$cc_home/gcc16-release/refs"
# The base's own content is 2 MB; a keyed home nested under it weighs 40 MB.
head -c 2097152 /dev/zero > "$cc_home/p/b/build1/object"
head -c 41943040 /dev/zero > "$cc_home/gcc16-release/p/b/build1/object"
# conan's own structural children carry a settings.yml when a stray run seeded them (malf's
# `_malf_conan_homes` guard); they are not homes, and a clean pointed at one would seed it more.
mkdir -p "$cc_home/profiles" "$cc_home/p/pkg1" && touch "$cc_home/profiles/settings.yml"
cc_out="$(PATH="$cc_bin:$PATH" CC_LOG="$cc_tmp/log" CONAN_HOME="$cc_home" bash "$cc_script" 2>&1)"; cc_rc=$?
check "the base home and each keyed home run \`conan cache clean '*' --build --temp\`, no structural child" \
      "rc=0 $cc_home :: cache clean * --build --temp|$cc_home/cut-verify :: cache clean * --build --temp|$cc_home/gcc16-release :: cache clean * --build --temp" \
      "rc=$cc_rc $(grep ':: cache clean' "$cc_tmp/log" 2>/dev/null | sort | tr '\n' '|' | sed 's/|$//')"
check "each home's size is printed, its build folders apart" \
      "3" "$(grep -c '^conan home .* MB, of which p/b ' <<< "$cc_out")"
cc_base_mb="$(sed -n "s|^conan home $cc_home: \([0-9]*\) MB.*|\1|p" <<< "$cc_out")"
check "the base home's line is its OWN size: the keyed homes nested under it are not counted in it" \
      "own" "$([[ -n "$cc_base_mb" && "$cc_base_mb" -lt 10 ]] && echo own || echo "GOT: ${cc_base_mb:-no line} MB — $(grep "^conan home $cc_home:" <<< "$cc_out")")"
check "a version the lock no longer names is removed from the home that holds it — and only that" \
      "$cc_home :: remove cc_first/1.9.0 -c|$cc_home :: remove zlib/1.3.1 -c|$cc_home/gcc16-release :: remove cmake/3.31.0 -c" \
      "$(grep ':: remove ' "$cc_tmp/log" 2>/dev/null | sort | tr '\n' '|' | sed 's/|$//')"
check "the version the lock names, and a package the lock does not know, both stay" \
      "zlib/1.3.2 cmake/4.4.3 unlocked_tool/0.1" "$(tr '\n' ' ' < "$cc_home/refs" | sed 's/ $//')"
check "the prune says what it removed, per home" \
      "said" "$(grep -q "pruned 2 superseded version(s) from $cc_home: cc_first/1.9.0 zlib/1.3.1" <<< "$cc_out" && echo said || echo "GOT: $(grep -i 'superseded' <<< "$cc_out")")"
rm -f "$cc_tmp/toolchain/conan.lock"; : > "$cc_tmp/log"
cc_out="$(PATH="$cc_bin:$PATH" CC_LOG="$cc_tmp/log" CONAN_HOME="$cc_home" bash "$cc_script" 2>&1)"; cc_rc=$?
check "without a lock nothing is pruned, the clean still runs, and the output says which was skipped" \
      "rc=0 0 3 said" \
      "rc=$cc_rc $(grep -c ':: remove ' "$cc_tmp/log" || true) $(grep -c ':: cache clean' "$cc_tmp/log" || true) $(grep -q 'no conan.lock at' <<< "$cc_out" && echo said || echo "GOT: $cc_out")"
cc_out="$(PATH="$cc_bin:$PATH" CC_LOG="$cc_tmp/log" CONAN_HOME="$cc_tmp/absent" bash "$cc_script" 2>&1)"; cc_rc=$?
check "no conan home at all is exit 0, saying so" \
      "rc=0 said" "rc=$cc_rc $(grep -q 'no conan home at' <<< "$cc_out" && echo said || echo "GOT: $cc_out")"
rm -rf "$cc_tmp"
echo

echo "[7q9] store-create purges every first-party package from its home BEFORE its first create (DN-119.D2)"

# note: with a conan home that outlives the job, B2's "clean export" must hold by the cache's state,
# not by each recipe's revision mode: a first-party package a previous run created stays in the
# home. `--build=<name>/*` forces the package being created, never one it only requires.
cv_tmp="$(realpath "$(mktemp -d)")"
cv_bin="$cv_tmp/bin"; cv_ws="$cv_tmp/ws"; cv_home="$cv_tmp/conan/cut-verify"; cv_log="$cv_tmp/conan.log"
mkdir -p "$cv_bin" "$cv_ws/scripts" "$cv_home/refs"
# The workspace's owned namespaces, as the real module derives them from its package declarations.
# `cv_gamma` below is first-party but outside any released set: a copy an earlier run left in the
# home is consumed all the same, so the purge is by namespace.
cat > "$cv_ws/scripts/workspace_layout.py" <<'PY'
def owned_package_prefixes():
    return ("cv_",)
PY
# A conan whose cache is one file per recipe name under <home>/refs, logging every call in order.
cat > "$cv_bin/conan" <<'STUB'
#!/usr/bin/env bash
refs="$CONAN_HOME/refs"; mkdir -p "$refs"
echo "$*" >> "$CV_LOG"
case "$1" in
    list)   python3 -c 'import json, os, sys; print(json.dumps({"Local Cache": {f"{n}/1.0": {} for n in sorted(os.listdir(sys.argv[1]))}}))' "$refs" ;;
    remove) rm -f "$refs/${2%%/*}" ;;
esac
exit 0
STUB
chmod +x "$cv_bin/conan"
touch "$cv_home/refs/cv_alpha" "$cv_home/refs/cv_beta" "$cv_home/refs/cv_gamma" "$cv_home/refs/zlib"
cv_out="$(PATH="$cv_bin:$PATH" CV_LOG="$cv_log" CONAN_HOME="$cv_home" MALF_WORKSPACE_ROOT="$cv_ws" \
          bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1; set +e; _malf_purge_first_party' _ "$MALF_BIN" 2>&1)"; cv_rc=$?
check "the purge exits 0 over the stub" "rc=0" "rc=$cv_rc"
check "every first-party package is removed from the home, whatever set it belongs to" \
      "yes yes yes" \
      "$(awk '/^remove cv_alpha\/\*/ {a=1} /^remove cv_beta\/\*/ {b=1} /^remove cv_gamma\/\*/ {g=1} END {print (a?"yes":"no"), (b?"yes":"no"), (g?"yes":"no")}' "$cv_log")"
check "a third-party package is never removed: the purge is the owned namespaces, nothing wider" \
      "0" "$(grep -c '^remove zlib' "$cv_log" || true)"
check "the purge says what it removed, naming the home" \
      "said" "$(grep -q "purged 3 first-party package(s) from $cv_home" <<< "$cv_out" && echo said || echo "GOT: $(grep -i purge <<< "$cv_out")")"
cv_body="$(sed -n '/^cmd_store_create()/,/^}/p' "$MALF_BIN")"
check "store-create purges its home before its first create, and refuses when the purge fails" \
      "before refuses" \
      "$( (( $(grep -n '_malf_purge_first_party || return 2' <<< "$cv_body" | cut -d: -f1) < $(grep -n '_malf_store_create_step ' <<< "$cv_body" | cut -d: -f1 | head -1) )) && printf before) $(grep -q '_malf_purge_first_party || return 2' <<< "$cv_body" && printf refuses)"
check "no verb named cut-verify is left: no dispatch arm, no function, no usage line" \
      "0" "$(grep -cE 'cmd_cut_verify|^ *cut-verify\)|malf cut-verify \[' "$MALF_BIN")"
rm -rf "$cv_tmp"
echo

echo "[7q10] the persistent home is handed out only where every job its user runs is the release's"

# note: a runner runs every job as its one user, and its runner group sends it jobs from every
# repository the group admits; a package any of them writes into the home is what the next release
# build links. A planted binary with its manifest line rewritten passes `conan cache check-integrity`
# and keeps its package revision in `conan list` (measured 2026-09-29), so nothing inside the home
# tells the two apart: what refuses is the SHAPE of the runner — its group as GitHub records it, one
# runner per user — and an entry of the home another account owns.
cg_tmp="$(realpath "$(mktemp -d)")"
cg_ws="$cg_tmp/ws"; cg_user="$cg_tmp/home"; mkdir -p "$cg_ws"; mkdir -m 700 "$cg_user"
cg_name=malf-release
cg_home="$cg_user/.cache/coderoast-build/conan/$cg_name"
cg_pkg="$cg_home/p/zlibd1f4a2c3/p/lib/libz.a"
ch_fixture "$cg_tmp"
# ONE guarded call. The units are written by whoever runs it — inside a user namespace that is root —
# because the script compares a unit's `User=` with `id -un`; another user's runner unit is always
# there and must never refuse. CG_PLANT is bind-mounted over by a root-owned binary, which only a
# process inside `unshare --user --mount` can do (the plant arm's unprivileged route).
cat > "$cg_tmp/run.sh" <<'RUN'
#!/usr/bin/env bash
rm -f "$CONAN_HOME_RUNNER_UNITS"/*.service
printf '[Service]\nUser=someone-else\n' > "$CONAN_HOME_RUNNER_UNITS/actions.runner.CodeRoasted.malf-runner.service"
[[ -n "${CG_NO_OWN_UNIT:-}" ]] \
    || printf '[Service]\nUser=%s\n' "$(id -un)" > "$CONAN_HOME_RUNNER_UNITS/actions.runner.CodeRoasted.$RUNNER_NAME.service"
[[ -z "${CG_EXTRA_UNIT:-}" ]] \
    || printf '[Service]\nUser=%s\n' "$(id -un)" > "$CONAN_HOME_RUNNER_UNITS/actions.runner.CodeRoasted.$CG_EXTRA_UNIT.service"
if [[ -n "${CG_PLANT:-}" ]]; then mount --bind /usr/bin/true "$CG_PLANT" || exit 97; fi
exec bash "$CG_SCRIPT" true
RUN
cg_run() {   # [NAME=value ...]: one guarded call; cg_out is its stdout, cg_err its stderr, cg_rc its exit
    cg_out="$(env PATH="$cg_tmp/bin:$PATH" CONAN_HOME_RUNNER_UNITS="$cg_tmp/units" CG_SCRIPT="$ch_script" \
        CH_JOBS="$(ch_jobs "$cg_name" coderoast-release)" GITHUB_REPOSITORY=CodeRoasted/coderoast \
        GITHUB_RUN_ID=4242 GITHUB_RUN_ATTEMPT=1 GITHUB_WORKSPACE="$cg_ws" HOME="$cg_user" \
        RUNNER_ENVIRONMENT=self-hosted RUNNER_NAME="$cg_name" "$@" \
        ${CG_WRAP:-} bash "$cg_tmp/run.sh" 2>"$cg_tmp/err")"; cg_rc=$?
    cg_err="$(cat "$cg_tmp/err")"
}
cg_said() {   # <fixed string>: "said" when the refusal names it, else what it said instead
    grep -qF -- "$1" <<< "$cg_err" && echo said || echo "GOT: $cg_err"
}

cg_run
mkdir -p "$(dirname "$cg_pkg")" && printf 'GENUINE\n' > "$cg_pkg"
cg_run
check "the release runner's shape — its group, one runner for its user, a home it owns — is handed the home" \
      "rc=0 $cg_home" "rc=$cg_rc $cg_out"
cg_run CH_JOBS="$(ch_jobs "$cg_name" default)"
check "a runner in the group every repository reaches (the shared runner's shape) refuses, printing no home" \
      "rc=1 out= said" "rc=$cg_rc out=$cg_out $(cg_said "runs in runner group 'default', not 'coderoast-release'")"
cg_run CH_GH_FAIL=1
check "GitHub's record of the job unreadable refuses, naming the permission the job needs" \
      "rc=1 said" "rc=$cg_rc $(cg_said 'actions: read')"
cg_run CH_JOBS='{"jobs":[{"status":"completed","runner_name":"malf-release","runner_group_name":"coderoast-release"}]}'
check "no in-progress job of this run on this runner refuses: the group read would be some other job's" \
      "rc=1 said" "rc=$cg_rc $(cg_said "lists 0 in-progress job(s) of run 4242 on runner 'malf-release'")"
cg_run CG_EXTRA_UNIT=malf-runner-2
check "a second runner run by the same user refuses, naming it: its jobs write the same home" \
      "rc=1 said" "rc=$cg_rc $(cg_said "also runs actions.runner.CodeRoasted.malf-runner-2.service")"
cg_run CG_NO_OWN_UNIT=1
check "no unit of this runner refuses: which runners share the user is then unproven" \
      "rc=1 said" "rc=$cg_rc $(cg_said "no runner unit in $cg_tmp/units runs 'malf-release'")"
chmod 775 "$cg_user/.cache"
cg_run
check "a directory on the way to the home another account can write into refuses, naming it" \
      "rc=1 said" "rc=$cg_rc $(cg_said "$cg_user/.cache is mode 775")"
chmod 755 "$cg_user/.cache"

# THE PLANTED BINARY: the package file at the SAME path, different bytes, written by ANOTHER account.
# Unprivileged where the kernel lets a user namespace mount (the desk); by `sudo -n` where it does not
# and sudo needs no password (a hosted runner). A box with neither REDS here — never a skip.
cg_route=none
cg_probe="$cg_tmp/probe"; : > "$cg_probe"
if unshare --user --map-root-user --mount bash -c 'mount --bind /usr/bin/true "$1"' _ "$cg_probe" 2>/dev/null; then
    cg_route=userns
elif sudo -n chown 0:0 "$cg_probe" 2>/dev/null; then
    cg_route=sudo
fi
rm -f "$cg_probe"
check "this box can make a file of another account, so the plant arm below judges something" \
      "yes" "$([[ "$cg_route" != none ]] && echo yes || echo "no: neither an unprivileged user namespace with a mount nor a password-less sudo")"
if [[ "$cg_route" == userns ]]; then
    CG_WRAP="unshare --user --map-root-user --mount" cg_run
    check "the control, inside the same user namespace and with no plant, is handed the home" \
          "rc=0 $cg_home" "rc=$cg_rc $cg_out"
    CG_WRAP="unshare --user --map-root-user --mount" cg_run CG_PLANT="$cg_pkg"
else
    printf 'PLANTED\n' > "$cg_pkg"
    sudo -n chown 0:0 "$cg_pkg" 2>/dev/null
    cg_run
fi
check "a package file another account wrote into the home refuses, naming the file, and prints no home" \
      "rc=1 out= said" "rc=$cg_rc out=$cg_out $(cg_said "zlibd1f4a2c3/p/lib/libz.a")"
check "setup-build-env hands the job's token to conan-home.sh alone, and unsets it before any install" \
      "1 1" "$(grep -c 'GH_TOKEN="$JOB_TOKEN" bash "$ACTION_PATH/conan-home.sh"' "$MALF_ROOT/.github/actions/setup-build-env/action.yml") $(grep -c '^        unset JOB_TOKEN$' "$MALF_ROOT/.github/actions/setup-build-env/action.yml")"
rm -rf "$cg_tmp"
echo

echo "[7q11] conan is installed from a hash lock, into a job-scoped venv, never from whatever PyPI serves (N281)"
# The property is the INSTALL LINE and the LOCK together: `--require-hashes` refuses a file whose
# digest the lock does not list, and `--only-binary=:all:` keeps pip from fetching an unhashed build
# dependency. Read from the action and the lock as committed; the network install is proven by every
# job that runs setup-build-env, step 0's build and lint jobs first.
cl_action="$MALF_ROOT/.github/actions/setup-build-env/action.yml"
cl_default="$(awk '/^  conan-version:/{f=1} f && /default:/{gsub(/[^0-9.]/, "", $2); print $2; exit}' "$cl_action")"
cl_lock="$MALF_ROOT/.github/actions/setup-build-env/conan-$cl_default.txt"
check "setup-build-env runs no third-party conan installer" \
      "0" "$(grep -c 'uses: conan-io/setup-conan' "$cl_action")"
check "the install is --require-hashes --only-binary=:all: from the lock the version names" \
      "1 1" "$(grep -c -- '--require-hashes --only-binary=:all: -r "$lock"' "$cl_action") $(grep -c 'lock="$ACTION_PATH/conan-$CONAN_VERSION.txt"' "$cl_action")"
check "the default version ($cl_default) has its lock on disk" "yes" "$([[ -f "$cl_lock" ]] && echo yes || echo "no $cl_lock")"
cl_bad="$(grep -vE '^\s*(#|$)' "$cl_lock" | grep -vE '^\s+--hash=sha256:[0-9a-f]{64}( \\)?$' \
          | grep -vE '^[a-z0-9._-]+==[0-9A-Za-z.+!-]+( ; [^\\]+)? \\$' || true)"
check "every lock line is a \`name==version\` pin or a sha256 digest, nothing else" "" "$cl_bad"
cl_pins="$(grep -cE '^[a-z0-9._-]+==' "$cl_lock")"
cl_hashed="$(awk '/^[a-z0-9._-]+==/{name=$1; getline; if ($0 ~ /--hash=sha256:/) n++} END{print n+0}' "$cl_lock")"
check "every pin in the lock carries at least one digest ($cl_pins pins)" "$cl_pins" "$cl_hashed"
check "conan itself is pinned at the default version" "1" "$(grep -c "^conan==$cl_default " "$cl_lock")"
echo

echo
echo
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
