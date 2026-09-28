#!/usr/bin/env bash
# drop-editable-registries.sh — setup-build-env's cleanup: when a job starts, NO conan home under
# $CONAN_HOME holds an editable registry, the base's and every keyed one's alike.
#
# WHY EVERY HOME. The cache step restores $CONAN_HOME WHOLE, and editable_packages.json decides
# how first-party requirements RESOLVE: a home saved while an editable was registered re-registers
# it in every later job restoring the entry — across runs and across repos, the restore-key being
# a prefix. malf keys a profile's home UNDER the base (<home>/gcc16-release, <home>/cut-verify;
# `_malf_use_keyed_cache`), and each keyed home keeps a registry of its own. Until 2026-09-29 this
# step cleared only the base's, so a keyed registry rode the cache; the base alone was measured at
# the v1.10.3 cut (coderoast-ipc run 33558688226, insight-canon run 33558684800: their own sibling
# module resolved `- Editable` instead of `- Cache`, and an editable emits no CMake package config).
#
# WHY HERE, AT THE POINT OF USE. Clearing a registry where it is WRITTEN is not sufficient, and that
# was proven: coderoast-lint-tidy's teardown cleared the base registry at 21:29:53 and run
# 33561230249's build job resolved `- Editable` at 21:31:54, restored from the cache in between.
# The invariant is asserted where it cannot depend on what an earlier job, run or cache entry left.
# It holds for a persistent conan home as well as a restored one: the check is on the state.
#
# Safe for the lint path, which also calls setup-build-env: malf registers the editables it needs
# AFTER this step, so this drops a stale registry and never the one a build is about to create.
set -euo pipefail

home="${CONAN_HOME:?CONAN_HOME must be set by the step before this one}"
if [[ ! -d "$home" ]]; then
    echo "no conan home at $home yet (a cache miss) — no editable registry to drop"
    exit 0
fi
# The base home's registry is at depth 1 and a keyed home's at depth 2; malf keys one level deep.
mapfile -t registries < <(find "$home" -mindepth 1 -maxdepth 2 -type f -name editable_packages.json | sort)
if ((${#registries[@]} == 0)); then
    echo "no editable registry in any conan home under $home (expected)"
    exit 0
fi
for reg in "${registries[@]}"; do
    echo "removing $reg — it registered:"
    cat "$reg"
    echo
    rm -f "$reg"
done
