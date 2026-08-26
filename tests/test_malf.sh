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
            --build-key probe --profile probe 2>&1)"; solo_rc=$?
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
          --build-key probe --profile probe 2>&1)"; ws_rc=$?
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
ws_sat_out="$(PATH="$stub_bin:$PATH" python3 -B "$BI" build --workspace "$ws" \
              --repo "$ws/repoA" --build-key probe --profile probe 2>&1)"; ws_sat_rc=$?
check "D: workspace shape + sibling present -> the cell configures and links (exit 0)" \
      "rc=0 linked" \
      "rc=$ws_sat_rc $(grep -qF "linked:" <<< "$ws_sat_out" && echo linked || echo "GOT: $ws_sat_out")"
check "D: no skip line when the path is satisfiable (workspace shape)" \
      "absent" "$(grep -qF "$skip_needle" <<< "$ws_sat_out" && echo "LEAKED: $ws_sat_out" || echo absent)"
solo_sat_out="$(PATH="$stub_bin:$PATH" python3 -B "$BI" build --workspace "$solo" \
              --repo "$solo" --build-key probe --profile probe 2>&1)"; solo_sat_rc=$?
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

echo
echo "malf selftest: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
