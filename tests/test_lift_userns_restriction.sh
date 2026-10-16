#!/usr/bin/env bash
# setup-build-env lifts a hosted runner's AppArmor restriction on unprivileged user namespaces for
# the job, touches nothing on a self-hosted runner, and is the ONE seat of that lift, so every test
# path behind the action gets it (a workflow's own bare `conan create` included).
# Run: bash tests/test_lift_userns_restriction.sh
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/.github/actions/setup-build-env/lift-userns-restriction.sh"
action="$root/.github/actions/setup-build-env/action.yml"
fail=0 passed=0
check() {   # <label> <expected> <actual>
    if [[ "$2" == "$3" ]]; then passed=$((passed + 1)); echo "  ok   $1"
    else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fi
}
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bin"
# Stubs: sysctl answers $STUB_VALUE (absent when empty) and records a write; sudo records and runs.
cat > "$scratch/bin/sysctl" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == -n ]]; then [[ -n "${STUB_VALUE:-}" ]] && { cat "$STUB_STATE" 2>/dev/null || echo "$STUB_VALUE"; exit 0; }; exit 255; fi
echo "${@: -1}" >> "$STUB_LOG"; echo 0 > "$STUB_STATE"
STUB
printf '#!/usr/bin/env bash\necho "sudo $*" >> "$STUB_LOG"\n"$@"\n' > "$scratch/bin/sudo"
chmod +x "$scratch/bin/sysctl" "$scratch/bin/sudo"
lift() {   # <runner environment> <value> -> the stub log, `|`-joined, then the exit code
    rm -f "$scratch/log" "$scratch/state"
    PATH="$scratch/bin:$PATH" RUNNER_ENVIRONMENT="$1" STUB_VALUE="$2" STUB_LOG="$scratch/log" STUB_STATE="$scratch/state" \
        bash "$script" > /dev/null
    local code=$?
    echo "$(paste -sd'|' "$scratch/log" 2>/dev/null) rc=$code"
}
check "a hosted runner restricting user namespaces has the restriction lifted, through sudo" \
      "sudo sysctl -q -w kernel.apparmor_restrict_unprivileged_userns=0|kernel.apparmor_restrict_unprivileged_userns=0 rc=0" \
      "$(lift github-hosted 1)"
check "a hosted runner without the restriction is left alone" " rc=0" "$(lift github-hosted 0)"
check "a hosted runner whose kernel has no such knob is left alone" " rc=0" "$(lift github-hosted '')"
check "a self-hosted runner is never touched, restricted or not" " rc=0" "$(lift self-hosted 1)"
check "setup-build-env runs the lift as a step of its own" "1" \
      "$(grep -c '^      run: bash "\$ACTION_PATH/lift-userns-restriction.sh"$' "$action")"
check "the lift has ONE seat: no other script under .github/ names the knob" "" \
      "$(grep -rl --include='*.sh' 'apparmor_restrict_unprivileged_userns' "$root/.github" | grep -vxF "$script" || true)"

echo "lift-userns selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
