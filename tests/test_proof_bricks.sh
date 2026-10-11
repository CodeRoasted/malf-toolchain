#!/usr/bin/env bash
# The proof bricks register malf's recipe overlay as the leg home's first remote (DN-142.D17 (8)):
# the Linux brick by malf's own `_malf_sync_overlay_remote`, the MSVC brick by its one pwsh
# spelling, held equal here. Run: bash tests/test_proof_bricks.sh
# Its own file while tests/test_malf.sh is held by another lane; it joins that file afterwards.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0 passed=0
check() {   # <label> <expected> <actual>
    if [[ "$2" == "$3" ]]; then passed=$((passed + 1)); echo "  ok   $1"
    else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fi
}
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

home="$scratch/leg"
bash "$root/.github/actions/setup-proof-linux/register-overlay.sh" "$home" "$root" > "$scratch/linux.log" 2>&1
check "the Linux brick writes malf-overlay FIRST, at <malf checkout>/overlay, as a local recipes index" \
      "malf-overlay|$root/overlay|local-recipes-index" \
      "$(python3 -c 'import json, sys; first = json.load(open(sys.argv[1]))["remotes"][0]; print(first["name"], first["url"], first.get("remote_type"), sep="|")' "$home/remotes.json" 2>&1)"
check "the leg stays RAW: no global.conf, profile or helper reaches its home from the sync" \
      "remotes.json" "$(ls "$home" | paste -sd' ')"
if command -v conan > /dev/null; then
    check "conan itself lists malf-overlay first in that home" "malf-overlay" \
          "$(CONAN_HOME="$home" conan remote list --format=json 2>&1 | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["name"])' 2>&1)"
fi

check "the MSVC brick's one pwsh spelling equals malf's function: name, type, <malf checkout>/overlay, first" \
      "ok" "$(python3 - "$root/malf" "$root/.github/actions/setup-proof-msvc/action.yml" <<'PY'
import re, sys
bash = open(sys.argv[1]).read()
body = bash[bash.index("_malf_sync_overlay_remote() {"):]
body = body[:body.index("\n}\n")]
func = {
    "name": re.search(r'"name": "([^"]+)"', body).group(1),
    "type": re.search(r'"remote_type": "([^"]+)"', body).group(1),
    "url": re.search(r'"\$MALF_DIR/(\w+)" <<', body).group(1),
    "first": "wanted = [overlay] +" in body,
}
msvc = open(sys.argv[2]).read()
line = re.search(r"conan remote add (\S+) \$overlay --type (\S+) --index (\d+) --force", msvc)
joined = re.search(r"\$overlay = Join-Path \(Resolve-Path \$env:MALF_DIR\)\.Path '(\w+)'", msvc)
pwsh = {"name": line.group(1), "type": line.group(2), "url": joined.group(1),
        "first": line.group(3) == "0"} if line and joined else {}
print("ok" if func == pwsh else f"bash {func} != pwsh {pwsh}")
PY
)"

echo "proof-bricks selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
