#!/usr/bin/env bash
# The golden's agreement is the compare job's verdict (DN-142.D25 (5), call A): step 0 ingests the
# `determinism-golden` artifact of a golden run and adds no agreement verdict of its own, so the
# ruling rests on the SHAPE of .github/workflows/coderoast-golden-compare.yml. In its `compare` job:
#   * the upload of `determinism-golden` comes after the compare action and after the conformance
#     gate, so the artifact exists in a run only when both passed;
#   * the compare action runs unconditionally and receives the caller's `min-legs`;
#   * no step, and not the job, carries `continue-on-error`, which would let the upload follow a
#     failed compare.
# Red first: each defect is planted in a copy of the live file, and each copy must be refused.
# Run: bash tests/test_golden_compare_shape.sh
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$root/.github/workflows/coderoast-golden-compare.yml" <<'PY'
import copy
import sys

try:
    import yaml
except ImportError:
    print("golden-compare shape: REFUSED, python3 has no yaml module, so nothing was judged")
    sys.exit(2)

COMPARE_ACTION = "CodeRoasted/malf-toolchain/.github/actions/coderoast-golden-compare@"
UPLOAD_ACTION = "actions/upload-artifact@"
GATE_SCRIPT = "scripts/spec_conformance_gate.sh"
ARTIFACT = "determinism-golden"


def problems(workflow: dict) -> list[str]:
    """Every way the compare job's shape lets `determinism-golden` exist without a passed compare."""
    job = (workflow.get("jobs") or {}).get("compare")
    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        return ["the workflow has no `compare` job with steps"]
    found = []
    steps = job["steps"]
    if "continue-on-error" in job:
        found.append("the `compare` job carries continue-on-error")
    for index, step in enumerate(steps):
        if "continue-on-error" in step:
            found.append(f"step {index} ({step.get('name', '?')}) carries continue-on-error")
    compare = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(COMPARE_ACTION)]
    gate = [i for i, s in enumerate(steps) if GATE_SCRIPT in str(s.get("run", ""))]
    upload = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(UPLOAD_ACTION)
              and (s.get("with") or {}).get("name") == ARTIFACT]
    if len(compare) != 1:
        found.append(f"{len(compare)} compare step(s), not one")
    if len(gate) != 1:
        found.append(f"{len(gate)} conformance gate step(s), not one")
    if len(upload) != 1:
        found.append(f"{len(upload)} upload(s) of `{ARTIFACT}`, not one")
    if found:
        return found
    if "if" in steps[compare[0]]:
        found.append("the compare step is conditional: a skipped compare would still be followed by the upload")
    if (steps[compare[0]].get("with") or {}).get("min-legs") != "${{ inputs.min-legs }}":
        found.append("the compare step does not receive the caller's min-legs")
    if upload[0] < compare[0]:
        found.append(f"the upload (step {upload[0]}) precedes the compare (step {compare[0]})")
    if upload[0] < gate[0]:
        found.append(f"the upload (step {upload[0]}) precedes the conformance gate (step {gate[0]})")
    return found


live = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
failed = 0
passed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed, passed
    if ok:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))


def planted(mutate) -> dict:
    fixture = copy.deepcopy(live)
    mutate(fixture["jobs"]["compare"]["steps"], fixture["jobs"]["compare"])
    return fixture


def index_of(steps: list, prefix: str) -> int:
    return next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(prefix))


def move_upload_first(steps, _job):
    steps.insert(0, steps.pop(index_of(steps, UPLOAD_ACTION)))


def move_gate_last(steps, _job):
    steps.append(steps.pop(next(i for i, s in enumerate(steps) if GATE_SCRIPT in str(s.get("run", "")))))


def tolerate_compare(steps, _job):
    steps[index_of(steps, COMPARE_ACTION)]["continue-on-error"] = True


def tolerate_job(_steps, job):
    job["continue-on-error"] = True


def skip_compare(steps, _job):
    steps[index_of(steps, COMPARE_ACTION)]["if"] = "${{ false }}"


def fix_legs(steps, _job):
    steps[index_of(steps, COMPARE_ACTION)]["with"]["min-legs"] = "1"


now = problems(live)
check("the live compare job: the upload follows the compare and the gate, nothing tolerates a failure",
      now == [], "; ".join(now))
for label, mutate, expected in (
        ("the upload moved before the compare", move_upload_first, "precedes the compare"),
        ("the conformance gate moved after the upload", move_gate_last, "precedes the conformance gate"),
        ("continue-on-error on the compare step", tolerate_compare, "carries continue-on-error"),
        ("continue-on-error on the job", tolerate_job, "job carries continue-on-error"),
        ("the compare step made conditional", skip_compare, "is conditional"),
        ("min-legs spelled in place of the caller's", fix_legs, "min-legs")):
    found = problems(planted(mutate))
    check(f"red first: {label} is refused", any(expected in p for p in found),
          f"found: {found or 'nothing'}")
print(f"golden-compare shape: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
