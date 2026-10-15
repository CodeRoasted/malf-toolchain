#!/usr/bin/env bash
# The store writer's public key, as the release job's verifier reads it (DN-142.D8, DN-142.D9):
# keys/store-writer.allowed_signers names the identity and namespace store_manifest.py verifies
# under, holds one ed25519 key, and refuses a signature that key did not make.
# Run: bash tests/test_store_key.sh   (its own file while tests/test_malf.sh is held)
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
allowed="$root/keys/store-writer.allowed_signers"
fail=0 passed=0
check() {   # <label> <expected> <actual>
    if [[ "$2" == "$3" ]]; then passed=$((passed + 1)); echo "  ok   $1"
    else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fi
}
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
check "one signer line, for the identity and namespace store_manifest.py verifies under" \
      "1 $(python3 -c "import sys; sys.path.insert(0, '$root'); import store_manifest as m; print(m.WRITER_IDENTITY, f'namespaces=\"{m.NAMESPACE}\"')")" \
      "$(grep -c . "$allowed") $(awk '{print $1, $2}' "$allowed")"
awk '{print $3, $4}' "$allowed" > "$scratch/writer.pub"
check "its key is one ed25519 public key ssh-keygen reads" "256 ED25519" \
      "$(ssh-keygen -l -f "$scratch/writer.pub" 2>&1 | awk '{print $1, $NF}' | tr -d '()')"
ssh-keygen -q -t ed25519 -N "" -C intruder -f "$scratch/intruder" < /dev/null
printf 'a slice\n' > "$scratch/slice.json"
ssh-keygen -Y sign -q -f "$scratch/intruder" -n coderoast-store "$scratch/slice.json" < /dev/null
check "the verifier, reading this file, refuses a slice signed by any other key" "refused" \
      "$(python3 -c "
import sys; from pathlib import Path; sys.path.insert(0, '$root'); import store_manifest as m
try:
    m.verify(Path('$scratch/slice.json').read_bytes(), Path('$scratch/slice.json.sig'), Path('$allowed'), 'r')
    print('accepted')
except m.Refused as refused:
    print('refused' if 'does not verify' in str(refused) else f'refused for another reason: {refused}')
")"
echo "store-key selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
