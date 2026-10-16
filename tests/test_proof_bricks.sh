#!/usr/bin/env bash
# The proof bricks register malf's recipe overlay as the leg home's first remote (DN-142.D17 (8)):
# the Linux brick by malf's own `_malf_sync_overlay_remote`, the MSVC brick by its one pwsh
# spelling, held equal here; and the MSVC brick activates EXACTLY the pinned toolset, spelled alike
# in its three places (RUL-154). Run: bash tests/test_proof_bricks.sh
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

# RUL-154: the MSVC leg activates EXACTLY one toolset, spelled in three places that must agree --
# setup-msvc1452's `toolset` (what vcvars activates), setup-proof-msvc's equality check, and the
# profile's compiler.version + tools.microsoft:msvc_update (what conan's own vcvars activates).
check "the MSVC toolset is pinned EXACTLY and spelled alike: setup-msvc1452, setup-proof-msvc, the profile" \
      "ok" "$(python3 - "$root/.github/actions/setup-msvc1452/action.yml" "$root/.github/actions/setup-proof-msvc/action.yml" "$root/profiles/windows-msvc-release" <<'PY'
import re, sys
import yaml
action = yaml.safe_load(open(sys.argv[1]))
proof = open(sys.argv[2]).read()
profile = open(sys.argv[3]).read()
problems = []
inputs = action.get("inputs", {})
toolset = str(inputs.get("toolset", {}).get("default", ""))
if not re.fullmatch(r"\d+\.\d+", toolset):
    problems.append(f"setup-msvc1452 toolset default {toolset!r} is not a major.minor")
floor = str(inputs.get("min-build", {}).get("default", ""))
if not floor.startswith(toolset + "."):
    problems.append(f"min-build {floor!r} is not a build of toolset {toolset!r}")
if "min-version" in inputs:
    problems.append("setup-msvc1452 still declares the floor-only min-version input")
gather = next((step.get("run", "") for step in action["runs"]["steps"] if step.get("id") == "gather"), "")
if "$toolset = '${{ inputs.toolset }}'" not in gather:
    problems.append("the activation step does not read the pinned toolset input")
if not re.search(r"-vcvars_ver=\$toolset\b", gather):
    problems.append("vcvarsall is not called with -vcvars_ver=$toolset")
if not re.search(r"\$active\.StartsWith\(\"\$toolset\.\"\)", gather):
    problems.append("the activated VCToolsVersion is not checked against the pinned toolset")
equality = re.findall(r"if \(\$active -ne '([\d.]+)'\)", proof)
if equality != [toolset]:
    problems.append(f"setup-proof-msvc equality check {equality} != [{toolset!r}]")
if re.search(r"VCToolsVersion\s*-lt", proof):
    problems.append("setup-proof-msvc still carries a minimum (-lt) check")
version = re.search(r"^compiler\.version=(\d+)$", profile, re.M)
update = re.search(r"^tools\.microsoft:msvc_update=(\d)$", profile, re.M)
conan_vcvars = f"14.{version.group(1)[-1]}{update.group(1)}" if version and update else None
if conan_vcvars != toolset:
    problems.append(f"the profile makes conan's vcvars_ver {conan_vcvars!r}, not {toolset!r}")
print("ok" if not problems else "; ".join(problems))
PY
)"

echo "proof-bricks selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
