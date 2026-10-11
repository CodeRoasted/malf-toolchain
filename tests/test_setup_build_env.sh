#!/usr/bin/env bash
# setup-build-env installs malf's home files and EVERY profile of malf's registry into the job's
# conan home, by malf's own sync (DN-142.D23: a create's test helper reads skip declarations'
# profile names from the home's profiles/). Run: bash tests/test_setup_build_env.sh
# Its own file while tests/test_malf.sh is held by another lane; it joins that file afterwards.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
action="$root/.github/actions/setup-build-env"
fail=0 passed=0
check() {   # <label> <expected> <actual>
    if [[ "$2" == "$3" ]]; then passed=$((passed + 1)); echo "  ok   $1"
    else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fi
}
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

home="$scratch/fresh"
bash "$action/install-home.sh" "$home" > "$scratch/fresh.log" 2>&1
check "a fresh home receives every profile of malf's registry, byte-equal" \
      "$(cd "$root/profiles" && for f in *; do printf '%s %s\n' "$f" "$(sha256sum < "$f" | cut -c1-64)"; done)" \
      "$( [[ -d "$home/profiles" ]] && cd "$home/profiles" && for f in *; do printf '%s %s\n' "$f" "$(sha256sum < "$f" | cut -c1-64)"; done)"
check "and malf's five home files, byte-equal" "equal equal equal equal equal" \
      "$(for f in global.conf settings_user.yml cmake/malf-path-map.cmake malf_recipe_exports.py malf_recipe_tests.py; do cmp -s "$root/$f" "$home/${f##*/}" && printf 'equal ' || printf 'differs(%s) ' "${f##*/}"; done | sed 's/ $//')"

home="$scratch/restored"
mkdir -p "$home/profiles"
echo "a stale policy" > "$home/global.conf"
echo "a stale profile" > "$home/profiles/linux-gcc16-release"
bash "$action/install-home.sh" "$home" > "$scratch/restored.log" 2>&1
check "a cache-restored home's stale global.conf and stale profile are overwritten" "equal equal" \
      "$(cmp -s "$root/global.conf" "$home/global.conf" && printf equal || printf differs) $(cmp -s "$root/profiles/linux-gcc16-release" "$home/profiles/linux-gcc16-release" && printf equal || printf differs)"

check "the action installs through install-home.sh and copies no profile of its own" "1 0" \
      "$(grep -c 'run: bash "\$ACTION_PATH/install-home.sh" "\$CONAN_HOME"' "$action/action.yml") $(grep -cE 'cp .*\$CONAN_HOME/profiles' "$action/action.yml")"

echo "setup-build-env selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
