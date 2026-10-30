#!/usr/bin/env bash
# test_attest_listing.sh — `coderoast-attest.yml`'s gating step, RUN against a stub `gh`.
#
# The cut (`cut.yml`) is dispatched AT the cut's candidate tag `candidate/<V>/<16 hex>` (DN-108.D36), and
# GitHub reports that tag as the run's `head_branch`. The attest step must find such a run, and refuse a
# run at any other ref, a record naming another candidate, and a run whose commit the superproject's
# default branch does not contain (DN-108.D36 (15)); and it hands on the candidate key the record names
# for the caller, refusing a caller the record names no candidate of, or one built at another commit
# (DN-142.D33 (3)). Those are behaviours of a bash program inside a
# YAML file, so this test extracts the step's `run:` text as the runner would execute it and runs it
# with `gh` replaced by a stub that serves a fixture: the run listing, the tag's commit, the default
# branch, the compare status of each run's commit, and each run's record artefact.
#
# Run: bash malf/tests/test_attest_listing.sh   (bash, python3 with PyYAML, jq; no network)

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="${1:-$root/.github/workflows/coderoast-attest.yml}"

pass_count=0
fail_count=0
check() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass_count=$((pass_count + 1))
        printf '  ok   %s\n' "$label"
    else
        fail_count=$((fail_count + 1))
        printf '  FAIL %s\n       expected: %q\n       actual:   %q\n' "$label" "$expected" "$actual"
    fi
}

for tool in python3 jq; do
    command -v "$tool" >/dev/null 2>&1 || { echo "test_attest_listing: $tool is not on PATH, and every arm needs it"; exit 1; }
done

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python3 - "$workflow" "$tmp/step.sh" <<'PY' || { echo "test_attest_listing: the gating step could not be extracted from $workflow"; exit 1; }
import sys
import yaml

steps = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["jobs"]["attest"]["steps"]
gating = [step for step in steps if step.get("id") == "judged"]
assert len(gating) == 1, f"expected one step with id 'judged', found {len(gating)}"
open(sys.argv[2], "w", encoding="utf-8").write(gating[0]["run"])
PY

mkdir -p "$tmp/bin"
cat > "$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
# The stub serves $FIXTURE: runs.json, tagged_sha, compare/<sha> (a status, or "403"), records/<id>.json.
set -uo pipefail
jq_expr=""
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --jq) jq_expr="$2"; shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
emit() { if [ -n "$jq_expr" ]; then jq -r "$jq_expr"; else cat; fi; }
if [ "${args[0]}" = "run" ] && [ "${args[1]}" = "download" ]; then
    id="${args[2]}" dir=""
    for ((i = 0; i < ${#args[@]}; i++)); do [ "${args[$i]}" = "--dir" ] && dir="${args[$((i + 1))]}"; done
    [ -f "$FIXTURE/records/$id.json" ] || { echo "no artifact for run $id" >&2; exit 1; }
    cp "$FIXTURE/records/$id.json" "$dir/cut-record.json"
    exit 0
fi
[ "${args[0]}" = "api" ] || { echo "stub gh: unexpected ${args[*]}" >&2; exit 2; }
path=""
for word in "${args[@]:1}"; do case "$word" in repos/*) path="$word" ;; esac; done
case "$path" in
    */actions/workflows/cut.yml/runs)
        for word in "${args[@]}"; do
            case "$word" in branch=*) echo "{\"workflow_runs\": []}" | emit; exit 0 ;; esac
        done
        emit < "$FIXTURE/runs.json" ;;
    */commits/*) echo "{\"sha\": \"$(cat "$FIXTURE/tagged_sha")\"}" | emit ;;
    */compare/*)
        sha="${path##*/compare/}"; sha="${sha%%...*}"
        status="$(cat "$FIXTURE/compare/$sha")"
        if [ "$status" = "403" ]; then echo "gh: Resource not accessible by personal access token (HTTP 403)" >&2; exit 1; fi
        echo "{\"status\": \"$status\"}" | emit ;;
    repos/CodeRoasted/coderoast) echo '{"default_branch": "main"}' | emit ;;
    *) echo "stub gh: unexpected path $path" >&2; exit 2 ;;
esac
GH
chmod +x "$tmp/bin/gh"

TAGGED="1111111111111111111111111111111111111111"
KEY="abababababababababababababababababababababababababababababababab"
GOOD="candidate/1.10.7/0123456789abcdef"

# fixture <name> — a fresh fixture directory; runs are added with `run_at`.
fixture() {
    local dir="$tmp/fx/$1"
    rm -rf "$dir"; mkdir -p "$dir/compare" "$dir/records"
    echo "$TAGGED" > "$dir/tagged_sha"
    echo '{"workflow_runs": []}' > "$dir/runs.json"
    echo "$dir"
}
# run_at <fixture> <id> <head_branch> <head_sha> <compare status> <record version> <record candidate name|null>
#        [<canon's candidate commit|none>]
run_at() {
    local dir="$1" id="$2" branch="$3" sha="$4" status="$5" version="$6" named="$7" built="${8:-$TAGGED}"
    jq --argjson id "$id" --arg branch "$branch" --arg sha "$sha" \
        '.workflow_runs += [{"id": $id, "head_branch": $branch, "head_sha": $sha}]' "$dir/runs.json" > "$dir/runs.tmp" \
        && mv "$dir/runs.tmp" "$dir/runs.json"
    echo "$status" > "$dir/compare/$sha"
    jq -n --arg version "$version" --arg tagged "$TAGGED" --arg named "$named" --arg built "$built" --arg key "$KEY" \
        '{schema: "pharos-cut-record/1", version: $version, cuttable: true,
          repos: {"insight-canon": $tagged, "coderoast-hub": "2222222222222222222222222222222222222222"},
          hub: "2222222222222222222222222222222222222222",
          candidates: (if $built == "none" then {} else {"insight-canon": {commit: $built, key: $key}} end),
          candidate: (if $named == "null" then {name: null, digest: null} else {name: $named, digest: "x"} end)}' \
        > "$dir/records/$id.json"
}
# attest <fixture> [phrase] -> rc=<exit> <the run it chose, or nothing> <how many output lines carry phrase>
attest() {
    local dir="$1" phrase="${2:-attested:}" out rc
    rm -rf "$dir/runner"; mkdir -p "$dir/runner"; : > "$dir/output"
    out="$(cd "$dir" && PATH="$tmp/bin:$PATH" FIXTURE="$dir" RUNNER_TEMP="$dir/runner" GITHUB_OUTPUT="$dir/output" \
        OWN_TOKEN=own RECORD_REPO=CodeRoasted/coderoast RECORD_TOKEN=record TAG=v1.10.7 \
        REPO=CodeRoasted/insight-canon DISPATCHED_FROM="$TAGGED" bash "$tmp/step.sh" 2>&1)"; rc=$?
    printf 'rc=%s %s%s %s' "$rc" "$(sed -n 's/^run=//p' "$dir/output")" \
        "$(sed -n 's/^key=\(.\{8\}\).*/ key \1/p' "$dir/output")" "$(grep -cF -- "$phrase" <<<"$out")"
}

echo "[attest] the run listing is bound to this version's candidates and to main's history (DN-108.D36 (15))"

fx="$(fixture found)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 "$GOOD"
check "a successful run at candidate/<V>/<d16> whose record names that candidate, its commit on main, is FOUND" \
      "rc=0 500 key abababab 1" "$(attest "$fx")"

fx="$(fixture main-newer)"
run_at "$fx" 500 "$GOOD" aaaa identical 1.10.7 "$GOOD"
run_at "$fx" 600 main bbbb identical 1.10.7 null
check "a NEWER successful run at main is never admitted: the candidate's run is chosen" \
      "rc=0 500 key abababab 1" "$(attest "$fx")"

fx="$(fixture main-only)"
run_at "$fx" 600 main bbbb identical 1.10.7 null
run_at "$fx" 601 feature/x cccc identical 1.10.7 "$GOOD"
run_at "$fx" 602 candidate/1.10.8/0123456789abcdef dddd identical 1.10.7 candidate/1.10.8/0123456789abcdef
run_at "$fx" 603 candidate/1.10.7/0123456789ABCDEF eeee identical 1.10.7 candidate/1.10.7/0123456789ABCDEF
check "REFUSED: runs at main, at a branch, at another version's candidate and at a non-hex name are never admitted" \
      "rc=1  1" "$(attest "$fx" "none of the 4 successful \`cut\` run(s) in CodeRoasted/coderoast's window of at most 50 ran at a candidate of 1.10.7")"

fx="$(fixture other-name)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 candidate/1.10.7/fedcba9876543210
check "REFUSED: a record whose candidate.name differs from its run's head_branch" \
      "rc=1  1" "$(attest "$fx" "cut run 500 ran at $GOOD, and its cut record for 1.10.7 names the candidate")"

fx="$(fixture desk-record)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 null
check "REFUSED: a record that names no candidate, at a run admitted by its ref" \
      "rc=1  1" "$(attest "$fx" "cut run 500 ran at $GOOD, and its cut record for 1.10.7 names the candidate")"

fx="$(fixture off-main)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 "$GOOD"
run_at "$fx" 700 candidate/1.10.7/fedcba9876543210 ffff diverged 1.10.7 candidate/1.10.7/fedcba9876543210
check "REFUSED, never passed over: a newer run whose commit is not an ancestor of the default branch" \
      "rc=1  1" "$(attest "$fx" "run 700 ran at candidate/1.10.7/fedcba9876543210, commit ffff, and that commit is not an ancestor of main")"

fx="$(fixture no-contents)"
run_at "$fx" 500 "$GOOD" aaaa 403 1.10.7 "$GOOD"
check "REFUSED, naming the permission: a compare the record token may not read (Contents: Read-only)" \
      "rc=1  1" "$(attest "$fx" "Contents: Read-only on CodeRoasted/coderoast, so an HTTP 403")"

fx="$(fixture no-candidate)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 "$GOOD" none
check "REFUSED: a record naming no candidate of the caller, which has nothing to publish from (DN-142.D33 (3))" \
      "rc=1  1" "$(attest "$fx" "names no candidate of insight-canon, and a release publishes from its repository's candidate")"

fx="$(fixture candidate-elsewhere)"
run_at "$fx" 500 "$GOOD" aaaa ahead 1.10.7 "$GOOD" 3333333333333333333333333333333333333333
check "REFUSED: a candidate built at another commit than the tag's" \
      "rc=1  1" "$(attest "$fx" "candidate of insight-canon was built at 3333333333333333333333333333333333333333")"

echo
echo "attest listing: $pass_count passed, $fail_count failed"
[[ $fail_count -eq 0 ]] || exit 1
