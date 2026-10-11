#!/usr/bin/env bash
# conan_module.sh lifts a hosted runner's AppArmor restriction on unprivileged user namespaces for
# the job, and touches nothing on a self-hosted runner. Run: bash tests/test_conan_module.sh
# Its own file while tests/test_malf.sh is held by another lane; it joins that file afterwards.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/.github/actions/conan-module/conan_module.sh"
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
lift() {   # <runner environment> <value> -> the stub log, `|`-joined
    rm -f "$scratch/log" "$scratch/state"
    PATH="$scratch/bin:$PATH" RUNNER_ENVIRONMENT="$1" STUB_VALUE="$2" STUB_LOG="$scratch/log" STUB_STATE="$scratch/state" \
        bash -c 'eval "$(sed -n "/^lift_userns_restriction()/,/^}/p" "$1")"; lift_userns_restriction > /dev/null' _ "$script"
    paste -sd'|' "$scratch/log" 2>/dev/null
}
check "a hosted runner restricting user namespaces has the restriction lifted, through sudo" \
      "sudo sysctl -q -w kernel.apparmor_restrict_unprivileged_userns=0|kernel.apparmor_restrict_unprivileged_userns=0" \
      "$(lift github-hosted 1)"
check "a hosted runner without the restriction is left alone" "" "$(lift github-hosted 0)"
check "a hosted runner whose kernel has no such knob is left alone" "" "$(lift github-hosted '')"
check "a self-hosted runner is never touched, restricted or not" "" "$(lift self-hosted 1)"
check "the lift runs before the test phase builds anything" "1" \
      "$(awk '/^if \[ "\$TEST" = "true" \]; then/{getline; print ($1 == "lift_userns_restriction") ? 1 : 0; exit}' "$script")"

echo "conan-module selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
