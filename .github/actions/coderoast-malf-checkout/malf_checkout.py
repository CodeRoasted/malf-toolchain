#!/usr/bin/env python3
"""Materialize malf-toolchain as a git checkout at the commit of the malf reusable workflow this job runs.

DN-142.D31 (4), slice S4. A candidate's records name malf by its git tree (`definition.malf`, DN-142.D29), its
index names malf's commit, and a reader accepts both only when they equal the commit its attestation's
certificate names as the signer, the commit of malf's reusable workflow (`job_workflow_sha`). The runner
fetches a composite action as a TARBALL of the commit its `uses:` line pins, which is no git checkout, and
under the layered re-pin (`OPS-1.S15`) that commit is the ACTIONS layer, never the workflows layer the
caller pins the reusable at. So the job's malf is fetched here, as git, at the reusable's own commit.

WHERE THE TWO COMMITS ARE READ, each from a fact the runner produced, never from a literal in a file (a SHA
written in a workflow is a self-reference the layered re-pin cannot move):
* the reusable's commit: the run's `referenced_workflows` (GET /repos/{repo}/actions/runs/{id}), every entry
  of malf's repository; they must name ONE commit, since a candidate runs one malf;
* malf's repository and the action's commit: the action's own path, `<_actions>/<owner>/<repo>/<sha>/...`.
Both are DERIVED, so a fork of the caller and of malf runs the same computation over its own repositories (P7).

THE CROSS-CHECK: the code the job's actions execute is the action commit's, the code the key names is the
workflow commit's, and they must be the same bytes outside `.github/` (the layers differ only in `uses:`
lines there); a difference is refused before anything runs.

    malf_checkout.py checkout <workflow file name> <destination>
        reads GITHUB_ACTION_PATH, GITHUB_API_URL, GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID and
        GITHUB_TOKEN; writes `dir`, `commit`, `repository` and `signer-workflow` to GITHUB_OUTPUT and
        MALF_CHECKOUT to GITHUB_ENV
    malf_checkout.py selftest
        every refusal and the green path over a local repository and a fixture run; no network

Exit 0 done, 1 refused (the reason on stderr), 2 a usage error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
ACTION_PATH = re.compile(r"/_actions/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/([0-9a-f]{40})/\.github/actions/[^/]+/?$")
SIGNER_WORKFLOW = ".github/workflows/coderoast-candidate.yml"
OUTSIDE_GITHUB = ":(exclude).github"


class Refused(Exception):
    pass


def action_coordinate(action_path: str) -> tuple[str, str, str]:
    """(owner, repository, commit) of the action the runner fetched, read from its path."""
    found = ACTION_PATH.search(action_path)
    if not found:
        raise Refused(f"the action path {action_path!r} is not <_actions>/<owner>/<repo>/<40-hex commit>/.github/"
                      f"actions/<name>: this action runs only as a remote action pinned by commit")
    return found.group(1), found.group(2), found.group(3)


def workflow_commits(run: dict, owner: str, repository: str, workflow: str) -> tuple[str, list[str]]:
    """(the one commit at which this run references `workflow`, every commit at which it references malf's
    reusable workflows). A malf workflow calling another malf workflow pins it at the previous re-pin layer
    (coderoast-golden-proof.yml calls coderoast-script-gate.yml), so a run may reference several commits;
    `materialize` holds them to one malf by their bytes outside .github/."""
    prefix = f"{owner}/{repository}/.github/workflows/".lower()
    entries = [entry for entry in run.get("referenced_workflows") or []
               if str(entry.get("path", "")).lower().startswith(prefix)]
    names = {str(entry["path"]).split("@", 1)[0].rsplit("/", 1)[-1] for entry in entries}
    if workflow not in names:
        raise Refused(f"run {run.get('id')} references no {owner}/{repository}/.github/workflows/{workflow} "
                      f"(of malf's, it references {sorted(names) or 'none'})")
    named = sorted({str(entry.get("sha", "")) for entry in entries
                    if str(entry["path"]).split("@", 1)[0].rsplit("/", 1)[-1] == workflow})
    commits = sorted({str(entry.get("sha", "")) for entry in entries})
    if len(named) != 1 or not all(HEX40.match(commit) for commit in commits):
        raise Refused(f"run {run.get('id')} references {owner}/{repository}'s {workflow} at {named} and its workflows "
                      f"at {commits}: one reusable runs at one commit, and every reference is a 40-hex commit")
    return named[0], commits


def _git(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", str(directory), *argv], capture_output=True, text=True, env=env, check=False)


def _must(done: subprocess.CompletedProcess, what: str) -> str:
    if done.returncode != 0:
        raise Refused(f"{what} failed (git exit {done.returncode}): {done.stderr.strip()[-600:]}")
    return done.stdout.strip()


def materialize(url: str, workflow_sha: str, others: list[str], destination: Path) -> None:
    """`destination` becomes a checkout of `url` at `workflow_sha`, refused unless its bytes outside
    `.github/` equal each of `others`' (the actions' commit, every other malf commit the run references):
    a candidate runs ONE malf's code, whichever re-pin layer names it."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _must(_git(destination, "init", "--quiet"), "git init")
    _must(_git(destination, "remote", "add", "origin", url), "git remote add")
    wanted = sorted({workflow_sha, *others})
    _must(_git(destination, "fetch", "--quiet", "--depth", "1", "origin", *wanted), f"fetching {wanted} from {url}")
    _must(_git(destination, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", workflow_sha),
          f"checking out {workflow_sha}")
    for other in sorted(set(others) - {workflow_sha}):
        differ = _git(destination, "diff", "--name-only", other, workflow_sha, "--", ".", OUTSIDE_GITHUB)
        files = _must(differ, f"comparing {other} with {workflow_sha}").splitlines()
        if files:
            raise Refused(f"this run's malf is {workflow_sha} (the reusable workflow) and also {other}, and they "
                          f"differ outside .github/ in {len(files)} file(s) ({', '.join(files[:5])}): the code that "
                          f"runs would not be the code the key names; pin every reference at one re-pin's layers")
    head = _must(_git(destination, "rev-parse", "HEAD"), "rev-parse HEAD")
    if head != workflow_sha:
        raise Refused(f"{destination} is at {head}, not {workflow_sha}")


def checkout(run: dict, action_path: str, server: str, workflow: str, destination: Path) -> dict[str, str]:
    owner, repository, action_sha = action_coordinate(action_path)
    workflow_sha, referenced = workflow_commits(run, owner, repository, workflow)
    materialize(f"{server.rstrip('/')}/{owner}/{repository}.git", workflow_sha, [action_sha, *referenced], destination)
    return {"dir": str(destination), "commit": workflow_sha, "repository": f"{owner}/{repository}",
            "signer-workflow": f"{owner}/{repository}/{SIGNER_WORKFLOW}"}


def live_run() -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = f"{api}/repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except OSError as unread:
        raise Refused(f"the run {url} could not be read ({unread}): the job needs `actions: read`") from unread


def _append(variable: str, lines: list[str]) -> None:
    target = os.environ.get(variable)
    if not target:
        raise Refused(f"{variable} is unset: this verb runs as a GitHub Actions step")
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("".join(f"{line}\n" for line in lines))


def main_checkout(workflow: str, destination: str) -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve()
    target = Path(destination).resolve()
    if target == workspace or workspace in target.parents:
        raise Refused(f"{target} is inside the caller's checkout {workspace}, which would read it as untracked files")
    found = checkout(live_run(), os.environ.get("GITHUB_ACTION_PATH", ""),
                     os.environ.get("GITHUB_SERVER_URL", "https://github.com"), workflow, target)
    _append("GITHUB_OUTPUT", [f"{name}={value}" for name, value in found.items()])
    _append("GITHUB_ENV", [f"MALF_CHECKOUT={found['dir']}"])
    print(f"malf_checkout: {found['repository']} at {found['commit']} (the commit of {workflow}) in {found['dir']}; "
          f"its bytes outside .github/ equal the actions' commit")
    return 0


def selftest() -> int:
    failures = 0

    def expect(name: str, want, got) -> None:
        nonlocal failures
        if want == got:
            print(f"  ok  {name}")
        else:
            failures += 1
            print(f"  FAIL {name}: expected {want!r}, got {got!r}")

    def refused(call) -> str:
        try:
            call()
        except Refused as why:
            return str(why)
        return "NOT REFUSED"

    with tempfile.TemporaryDirectory(prefix="malf_checkout.") as scratch:
        root = Path(scratch)
        origin = root / "owner" / "malf-toolchain.git"
        work = root / "work"
        work.mkdir()
        _must(_git(work, "init", "--quiet"), "init")

        def commit(path: str, text: str) -> str:
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).write_text(text)
            _must(_git(work, "add", "--", path), "add")
            _must(_git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", path), "commit")
            return _must(_git(work, "rev-parse", "HEAD"), "rev-parse")

        commit("malf", "code one\n")
        actions = commit(".github/actions/x/action.yml", "uses: a@head\n")
        workflows = commit(".github/workflows/coderoast-build.yml", "uses: x@actions\n")
        drifted = commit("malf", "code two\n")
        origin.parent.mkdir(parents=True)
        _must(_git(root, "clone", "--quiet", "--bare", str(work), str(origin)), "clone --bare")
        _must(_git(origin, "config", "uploadpack.allowAnySHA1InWant", "true"), "config")
        server = f"file://{root}"
        path = f"/runner/_actions/owner/malf-toolchain/{actions}/.github/actions/coderoast-malf-checkout"

        def run_of(*entries: tuple[str, str]) -> dict:
            return {"id": 7, "referenced_workflows": [{"path": p, "sha": s, "ref": "x"} for p, s in entries]}

        build = "owner/malf-toolchain/.github/workflows/coderoast-build.yml"
        local = "caller/repo/.github/workflows/ci.yml"
        green = checkout(run_of((f"{build}@{workflows}", workflows), (f"{local}@abc", "c" * 40)), path, server,
                         "coderoast-build.yml", root / "malf")
        expect("the green path checks out the reusable's commit and names the signer workflow from the action's path",
               (workflows, "owner/malf-toolchain/.github/workflows/coderoast-candidate.yml", workflows),
               (green["commit"], green["signer-workflow"], _git(root / "malf", "rev-parse", "HEAD").stdout.strip()))
        expect("the checkout is git, clean, with the reusable's tree",
               ("", _git(work, "rev-parse", f"{workflows}^{{tree}}").stdout.strip()),
               (_git(root / "malf", "status", "--porcelain").stdout.strip(),
                _git(root / "malf", "rev-parse", "HEAD^{tree}").stdout.strip()))
        expect("RED: a reusable whose code differs from the actions' outside .github/ is refused, naming the file",
               True, "outside .github/ in 1 file(s) (malf)" in refused(lambda: checkout(
                   run_of((f"{build}@{drifted}", drifted)), path, server, "coderoast-build.yml", root / "m2")))
        nested = "owner/malf-toolchain/.github/workflows/coderoast-script-gate.yml"
        layered = checkout(run_of((f"{build}@{workflows}", workflows), (f"{nested}@{actions}", actions)), path, server,
                           "coderoast-build.yml", root / "m3")
        expect("a malf workflow calling another at the previous re-pin layer (two commits, equal outside .github/) "
               "is one malf: the named reusable's commit is checked out", workflows, layered["commit"])
        expect("RED: a second referenced malf commit whose code differs outside .github/ is refused, naming the file",
               True, "outside .github/ in 1 file(s) (malf)" in refused(lambda: checkout(
                   run_of((f"{build}@{workflows}", workflows), (f"{nested}@{drifted}", drifted)), path, server,
                   "coderoast-build.yml", root / "m4")))
        expect("RED: one reusable referenced at two commits is refused",
               True, "one reusable runs at one commit" in refused(lambda: workflow_commits(
                   run_of((f"{build}@a", "a" * 40), (f"{build}@b", "b" * 40)), "owner", "malf-toolchain",
                   "coderoast-build.yml")))
        expect("RED: a run that does not reference the named workflow is refused",
               True, "references no owner/malf-toolchain/.github/workflows/coderoast-candidate.yml" in refused(
                   lambda: workflow_commits(run_of((f"{build}@a", "a" * 40)), "owner", "malf-toolchain",
                                           "coderoast-candidate.yml")))
        expect("RED: another owner's workflow of the same name is not malf's",
               True, "references no" in refused(lambda: workflow_commits(
                   run_of(("fork/malf-toolchain/.github/workflows/coderoast-build.yml@a", "a" * 40)),
                   "owner", "malf-toolchain", "coderoast-build.yml")))
        expect("RED: an action path that is no pinned remote action is refused",
               True, "is not <_actions>" in refused(lambda: action_coordinate("/home/x/malf/.github/actions/y")))
        expect("the owner and repository come from the action's path, never a literal",
               ("fork", "tools", "d" * 40),
               action_coordinate(f"/r/_actions/fork/tools/{'d' * 40}/.github/actions/coderoast-malf-checkout"))
    print(f"malf_checkout selftest: {'FAILED ' + str(failures) if failures else 'all green'}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["selftest"] and len(argv) == 1:
            return selftest()
        if argv[:1] == ["checkout"] and len(argv) == 3:
            return main_checkout(argv[1], argv[2])
    except Refused as why:
        print(f"malf_checkout: REFUSED — {why}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
