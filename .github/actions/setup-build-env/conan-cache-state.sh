#!/usr/bin/env bash
# The one hand-off between a conan cache RESTORE (`setup-build-env`, `setup-proof-linux`,
# `setup-proof-msvc`) and its SAVE (`conan-cache-save`, the job's last step), through one file in
# the job's RUNNER_TEMP, so no caller threads a home, a key and a hit through its own steps.
#
#   conan-cache-state.sh write <conan home> <cache key> <cache hit> <allowed>
#   conan-cache-state.sh read     # writes save=true|false, home= and key= to $GITHUB_OUTPUT
#
# `read` answers save=false when no restore wrote the file, when the restore was not allowed
# (`actions-cache-allowed.sh`: a `coderoast-release` job saves no Actions cache either), and on an
# exact-key hit (an entry is immutable).
set -euo pipefail

state="${RUNNER_TEMP:?RUNNER_TEMP is not set — not an Actions step}/coderoast-conan-cache.env"

case "${1:-}" in
    write)
        (($# == 5)) || { echo "::error::conan-cache-state.sh write <home> <key> <hit> <allowed>" >&2; exit 2; }
        printf 'home=%s\nkey=%s\nhit=%s\nallowed=%s\n' "$2" "$3" "$4" "$5" > "$state"
        ;;
    read)
        out="${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set — not an Actions step}"
        if [[ ! -f "$state" ]]; then
            echo "conan-cache-save: no conan cache restore ran in this job; nothing is saved"
            echo "save=false" >> "$out"
            exit 0
        fi
        home="$(sed -n 's/^home=//p' "$state")"
        key="$(sed -n 's/^key=//p' "$state")"
        hit="$(sed -n 's/^hit=//p' "$state")"
        allowed="$(sed -n 's/^allowed=//p' "$state")"
        if [[ "$allowed" != true ]]; then
            echo "conan-cache-save: this job may not touch an Actions cache; nothing is saved"
            echo "save=false" >> "$out"
        elif [[ "$hit" == true ]]; then
            echo "conan-cache-save: the exact key $key was restored, and an entry is immutable; nothing is saved"
            echo "save=false" >> "$out"
        elif [[ -z "$home" || -z "$key" ]]; then
            echo "::error::conan-cache-save: the restore recorded no home or no key ($state)" >&2
            exit 1
        else
            printf 'save=true\nhome=%s\nkey=%s\n' "$home" "$key" >> "$out"
        fi
        ;;
    *)
        echo "::error::conan-cache-state.sh takes write or read, not '${1:-}'" >&2
        exit 2
        ;;
esac
