#!/usr/bin/env bash
# Remove every first-party package (`insight_*`, `coderoast_*`, `logcraft_*`, plus the caller's
# newline-separated extra patterns) from a conan home — after a restore and before a save.
#
#   drop-first-party.sh <conan home> [extra patterns]
#
# WHY BOTH ENDS (ROADMAP N367). `conan create --build=missing` reuses any cached binary whose
# package id matches, so a first-party binary in a home lets a run skip the compile it exists to
# prove. The save keeps one out of every NEW entry; the restore end keeps one out of the home
# whatever an entry already holds, so an entry saved before the save dropped them is never
# consumed with them, even on an exact-key hit, which is never re-saved.
set -euo pipefail

export CONAN_HOME="${1:?usage: drop-first-party.sh <conan home> [extra patterns]}"
patterns=("insight_*" "coderoast_*" "logcraft_*")
while IFS= read -r pattern; do
    [[ -n "$pattern" ]] && patterns+=("$pattern")
done <<< "${2:-}"
for pattern in "${patterns[@]}"; do
    conan remove "$pattern" --confirm
done
conan list "*" --format=compact
