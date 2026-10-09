#!/usr/bin/env bash
# Whether THIS job may restore or save an Actions cache, written as `allowed=true|false` to
# $GITHUB_OUTPUT and said on stdout.
#
# A JOB ON THE `coderoast-release` RUNNER GROUP TOUCHES NO ACTIONS CACHE (DN-142.D6; ROADMAP N367).
# An Actions cache is written by any workflow of the repository, on any runner — the general
# self-hosted runner, a hosted one — so a release job that restores it builds against bytes a
# job outside the release account produced. Measured at `v1.10.6`: insight-eidos Release run
# 37725404832's golden legs on that group restored `conan-golden-x86-gcc-Linux-…`,
# `conan-golden-x86-clang-Linux-…` and `conan-golden-msvc1452-Windows-…`, each saved by a `main`
# run. The release account's own persistent home, or a cold build, is what such a job gets.
#
# The group is read from GitHub's record of this run's jobs (the job's token, which then needs
# `actions: read`). A hosted runner is in no self-hosted group and needs no read. Anything
# unproven — no token scope, no `gh`, a listing that does not name exactly one in-progress job on
# this runner — answers `false`: a skipped restore costs a cold build, a wrong one costs the
# release's provenance.
set -uo pipefail

RELEASE_RUNNER_GROUP="coderoast-release"

answer() {   # <true|false> <why>
    printf 'allowed=%s\n' "$1" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set — not an Actions step}"
    echo "actions-cache-allowed: $1 — $2"
}

if [[ "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]; then
    answer true "a hosted runner belongs to no self-hosted runner group"
    exit 0
fi
name="${RUNNER_NAME:-}"
if [[ -z "$name" ]]; then
    echo "::warning::actions-cache-allowed: RUNNER_NAME is not set, so this job's runner group is unproven; no Actions cache is restored or saved"
    answer false "the runner is unnamed"
    exit 0
fi
if ! command -v gh >/dev/null 2>&1; then
    echo "::warning::actions-cache-allowed: gh is not installed on '$name', so this job's runner group is unproven; no Actions cache is restored or saved"
    answer false "no gh to read the runner group with"
    exit 0
fi
run="repos/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}/actions/runs/${GITHUB_RUN_ID:?GITHUB_RUN_ID is not set}"
if ! groups="$(gh api "$run/attempts/${GITHUB_RUN_ATTEMPT:-1}/jobs" --paginate \
                --jq ".jobs[] | select(.status == \"in_progress\" and .runner_name == \"$name\") | .runner_group_name")"; then
    echo "::warning::actions-cache-allowed: GitHub's record of run ${GITHUB_RUN_ID}'s jobs could not be read, so this job's runner group is unproven; no Actions cache is restored or saved. Grant the job \`permissions: actions: read\` to keep its cache"
    answer false "the jobs listing could not be read"
    exit 0
fi
count="$(grep -c . <<< "$groups" || true)"
if [[ "$count" != 1 ]]; then
    echo "::warning::actions-cache-allowed: GitHub lists $count in-progress job(s) of run $GITHUB_RUN_ID on runner '$name', not exactly one, so this job's runner group is unproven; no Actions cache is restored or saved"
    answer false "$count in-progress jobs on '$name'"
    exit 0
fi
if [[ "${groups,,}" == "$RELEASE_RUNNER_GROUP" ]]; then
    echo "::notice::actions-cache-allowed: runner '$name' is in the '$RELEASE_RUNNER_GROUP' group, which restores and saves no Actions cache (DN-142.D6)"
    answer false "runner group '$groups'"
    exit 0
fi
answer true "runner group '$groups'"
