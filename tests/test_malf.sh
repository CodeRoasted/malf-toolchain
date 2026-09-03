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
verdict_fn="$(sed -n '/^_malf_lint_db_verdict() {/,/^}/p' "$MALF_BIN")"
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
  && sed -n "$((refuse_line - 3)),${refuse_line}p" "$MALF_BIN" | grep -q 'lint_status=1' \
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
lk_counts() { grep -oE 'selected [0-9]+, checked [0-9]+, [0-9]+ finding\(s\), [0-9]+ not linted' <<< "$1" | head -1; }
lk_af_run() {   # <log> [env...] — malf lint --all-files from core/ with the fake toolchain
    (cd "$lk_repo/core" && PATH="$lk_bin:$PATH" LK_TIDY_LOG="$1" MALF_PROFILE_NAME="" \
        env "${@:2}" bash "$MALF_BIN" lint --all-files --console 2>&1)
}

git -C "$lk_repo" reset -q                                    # drop the ghost arm's staged path
git -C "$lk_repo" -c user.email=t@t -c user.name=t commit -aq -m clean
lk_clean_out="$(lk_run "$lk_repo/core" "$lk_tmp/tidy.clean.log")"; lk_clean_rc=$?
check "STATE 1/3 — a CLEAN tree checks nothing, and the summary says so instead of reading as a pass" \
      "rc=0 declared" \
      "rc=$lk_clean_rc $([[ "$lk_clean_out" == *"selected 0, CHECKED 0 — NOTHING WAS INSPECTED"* \
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
      "rc=0 selected 1, checked 1, 0 finding(s), 0 not linted" \
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
check "STATE 3/3 — a run WITH findings counts them, and a diagnostic's note lines are not findings" \
      "rc=0 selected 1, checked 1, 1 finding(s), 0 not linted" \
      "rc=$lk_warn_rc $(lk_counts "$lk_warn_out")"

# A TU THE CHECKER NEVER READ IS ITS OWN COLUMN. Clean, dirty and UNREAD are three states and the
# summary must not fold the third into either of the first two — 1 finding and 1 not-linted are
# opposite facts about coverage. [7j3] pins the NOT LINTED block itself; this pins that the
# one-line summary carries the same count, since that line is what a reader stops at.
lk_nl_out="$(lk_af_run "$lk_tmp/tidy.nl.log" LK_TIDY_DIE_ON=engine.cpp)"; lk_nl_rc=$?
check "a TU clang-tidy never read is counted NOT LINTED in the summary, not as 0 findings, rc 1" \
      "rc=1 selected 1, checked 1, 0 finding(s), 1 not linted" \
      "rc=$lk_nl_rc $(lk_counts "$lk_nl_out")"

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

echo "[7i] cmd_bump — the chain survives its own coherence check (the INV-14 self-defeat)"

# Post-bump, the FULL pin-coherence verification is structurally RED until the lockfile is
# re-derived: INV-14 compares conan.lock's first-party pins against the recipes the bump just
# moved. Measured 2026-08-15 (the 1.9.4 bump): with the verification mid-chain, cmd_bump exited
# at that check and STRANDED the editable re-sync, the stale prune and the SBOM — the caches
# stayed one version behind and the next `malf lock --update` refused 19 roots. The contract
# this section pins: every hygiene step the ceremony owns runs BEFORE the check that judges the
# result, with the plain (behaviour-neutral, first-party-only) lock chained where the lock doc
# already prescribed it, so a green bump means the whole post-bump state is coherent — and a red
# one indicts the state, not the ordering.
bump_tmp="$(mktemp -d)"
mkdir -p "$bump_tmp/ws/scripts"
: > "$bump_tmp/ws/scripts/pin_coherence.py"   # existence-checked by cmd_bump; python3 is stubbed
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
check "bump chain — rc=0 and every hygiene step precedes the verification" \
      "rc=0 order=rewrite sync lock clean verify " \
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
      '(?:.*/)?alpha/|(?:.*/)?be\.ta/' \
      "$(MALF_LINT_EXCLUDE_EXTRA='alpha be.ta' _malf_lint_header_filter)"

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
      "yes" "$(grep -q "stat -c %Y /proc/" "$sc_dir/_owner" && echo yes || echo no)"

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
bt_derived_mismatch=()
for bt_name in "${bt_profiles[@]}"; do
    bt_expect="$(sed -nE 's/^[[:space:]]*build_type[[:space:]]*=[[:space:]]*([A-Za-z]+).*/\1/p' \
                 "$MALF_ROOT/profiles/$bt_name" | head -n1)"
    bt_got="$(bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
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
bt_nb_out="$(MALF_DIR="$bt_tmp" bash -c '
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
    out="$(bash -c 'MALF_SOURCE_ONLY=1 source "$1" >/dev/null 2>&1
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
      "$(grep -c 'main_ref="$(_malf_pkg_ref' <<< "$cm_tp_src" | grep -q '^1$' && echo "one reading" || echo "GOT $(grep -c 'main_ref="$(_malf_pkg_ref' <<< "$cm_tp_src") readings")"

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

echo
echo
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
