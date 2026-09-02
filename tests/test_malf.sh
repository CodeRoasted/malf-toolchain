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
printf '%s\n' "$@" >> "$LK_TIDY_LOG"
last="${*: -1}"
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

printf 'int ghost() { return 1; }\n' > "$lk_repo/core/src/ghost.cpp"
git -C "$lk_repo" add core/src/ghost.cpp
rm "$lk_repo/core/src/ghost.cpp"                    # staged, then gone: listed by the index leg, unresolvable
lk_gh_out="$(lk_run "$lk_repo/core" "$lk_tmp/tidy.ghost.log")"; lk_gh_rc=$?
check "a listed path that does not resolve REFUSES by name instead of being skipped" \
      "rc=1 refused:src/ghost.cpp" "rc=$lk_gh_rc $([[ "$lk_gh_out" == *"git lists 'src/ghost.cpp' as changed"* ]] && echo refused:src/ghost.cpp || echo "GOT: $lk_gh_out")"

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


echo
echo
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
