#!/usr/bin/env bash
# conan-home.sh <persistent: true|false> — setup-build-env's CONAN_HOME, printed on stdout.
#
# false (every consumer but step 0): `<workspace>/.conan2`, which `actions/cache` restores and saves.
#
# true (DN-119.D2): ONE home per runner, OUTSIDE the checkout, under the runner user's own HOME, so it
# outlives the job and `actions/checkout`'s `git clean -ffdx` never empties it. Measured over step 0's
# `1.10.5` runs: a cold home cost about 1 800 s of third-party rebuilds, and a warm one still cost
# about 510 s to restore and re-save a 3.2 GB cache entry. malf keys every profile's home UNDER this
# one (`<home>/gcc16-release`, `<home>/cut-verify`), so all of them are warm at once.
#
# WHY UNDER $HOME AND KEYED BY RUNNER NAME. The runner service runs as its own user and cannot sudo
# (`NoNewPrivileges`), so a system path it cannot create is out; $HOME is the one directory that user
# owns by construction. Two runners never share a home: conan 2's cache is not safe under concurrent
# writers, and one runner runs one job at a time. The home is mode 700 — the runner user's alone.
#
# THE TRUST DOMAIN IS THE RUNNER'S, AND IT IS STATED RATHER THAN ASSUMED. Every job that runs as this
# runner user can write the home, so a package planted by one job is used by the next. A self-hosted
# runner serves private repositories only (a public repository hard-pins hosted runners), so that
# domain is this organisation's own repositories. A hosted runner REFUSES here: nothing on it outlives
# the job, and a "persistent" home there would be a claim the runner cannot keep.
set -euo pipefail

persistent="${1:?usage: conan-home.sh <true|false>}"
case "$persistent" in
    false)
        printf '%s\n' "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is not set — not an Actions step}/.conan2"
        ;;
    true)
        if [[ "${RUNNER_ENVIRONMENT:-}" != self-hosted ]]; then
            echo "::error::persistent-conan-home asks for a conan home that outlives the job, and this runner is '${RUNNER_ENVIRONMENT:-unknown}', not self-hosted: nothing on it outlives the job" >&2
            exit 1
        fi
        name="${RUNNER_NAME:?RUNNER_NAME is not set — not an Actions step}"
        if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
            echo "::error::the runner name '$name' is not one plain path segment, so it cannot key a conan home without escaping the home's base" >&2
            exit 1
        fi
        base="${HOME:?HOME is not set}/.cache/coderoast-build/conan"
        home="$base/$name"
        case "$home/" in
            "${GITHUB_WORKSPACE:-/nonexistent}"/*)
                echo "::error::the persistent conan home $home is inside the checkout $GITHUB_WORKSPACE, which actions/checkout cleans" >&2
                exit 1 ;;
        esac
        mkdir -p "$home"
        chmod 700 "$base" "$home"
        printf '%s\n' "$home"
        ;;
    *)
        echo "::error::conan-home.sh takes true or false, not '$persistent'" >&2
        exit 1
        ;;
esac
