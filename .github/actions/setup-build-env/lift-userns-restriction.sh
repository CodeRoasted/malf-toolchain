#!/usr/bin/env bash
# A HOSTED ubuntu-24.04 image restricts unprivileged user namespaces through AppArmor
# (kernel.apparmor_restrict_unprivileged_userns=1). Tests that need one (coderoast-ipc's private-pid
# and private-tmpfs segment tests) then SKIP there, and since DN-142.D23 an undeclared skip is a red.
# The hosted VM is the job's own and is discarded with it, so the restriction is lifted for the job
# and the tests RUN, rather than being declared away. A self-hosted runner (no sudo,
# NoNewPrivileges) is left exactly as it is; a skip there stays the package's to declare.
#
# WHY IN setup-build-env, not in a per-module script, where it first landed (malf 146c811): every
# job that runs a package's tests passes through this action. A workflow's own bare `conan create`
# in a `run:` block runs test_package and the declared-skip judgment through no other shared step: the retired tag-time release build did exactly
# that, and coderoast-ipc's v1.10.7 Release (run 38738860055, job `release / publish`) refused the
# same four skips its CI job in that very run had lifted and run. MALF_TOOLCHAIN_DIR moved here for
# the identical reason (this action's own step says so).
set -euo pipefail
knob=kernel.apparmor_restrict_unprivileged_userns
[ "${RUNNER_ENVIRONMENT:-}" = github-hosted ] || { echo "lift-userns: not a hosted runner (RUNNER_ENVIRONMENT='${RUNNER_ENVIRONMENT:-}'); nothing touched"; exit 0; }
value="$(sysctl -n "$knob" 2>/dev/null || true)"
[ "$value" = 1 ] || { echo "lift-userns: $knob is '${value:-absent}' on this hosted runner; nothing to lift"; exit 0; }
sudo sysctl -q -w "$knob=0"
echo "lift-userns: $knob lifted from 1 to $(sysctl -n "$knob") for this hosted job"
