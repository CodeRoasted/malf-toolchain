# malf-local self-hosted runner

A single **org-level** self-hosted GitHub Actions runner that serves CodeRoast's
**private** repos, so their CI + `release-publish` stop burning GitHub-hosted minutes.
Self-hosted runners are **unmetered** — you supply the compute, GitHub bills nothing.

Public repos (canon, metalog, ipc, web, sift-action, malf-toolchain) already get
**free** unlimited GitHub-hosted minutes, so they are *not* moved here — and **must not
be**, see the safety rule.

## ⛔ Safety rule (non-negotiable)

A self-hosted runner must **never** run a **public / fork-exposed** repo: a fork PR would
execute attacker-controlled code on this box (RCE on your network). On GitHub Free an
org runner is visible to *all* repos, so this is enforced at the **workflow layer**:

- **Private** repos use `runs-on: ${{ vars.CI_RUNS_ON || 'ubuntu-latest' }}`.
- **Public** repos stay hard-pinned to `ubuntu-latest`. Never add the `malf-local`
  label to a public repo's workflow.

## Setup (on the warehouse box)

```bash
# as an org admin, with gh authenticated:
malf/runner/install-runner.sh        # registers a runner named "malf-runner"
malf/runner/start-runner.sh          # run it in the foreground — Ctrl+C to stop
```

`install-runner.sh` mints an org registration token (via `gh`), downloads the latest
runner into **`~/actions-runner-malf`**, and configures it against `github.com/CodeRoasted`
with name **`malf-runner`** and label **`malf-local`**. It does **not** start anything —
`start-runner.sh` runs it in the foreground so you watch jobs stream and `Ctrl+C` to stop
(cleaner than a service under WSL2). Override via env: `ORG`, `LABELS`, `NAME`,
`RUNNER_DIR`, `RUNNER_ARCH`, `RUNNER_TOKEN=…` (skip the gh mint), or `AS_SERVICE=true`
(install a background systemd service instead of running foreground).

## Run / stop

```bash
malf/runner/start-runner.sh    # foreground; jobs stream in the terminal
# Ctrl+C                       # stops the runner (deregisters its session cleanly)
```
While stopped, queued jobs simply wait; start it again to drain them. (It only does work
while running, so "pause" = Ctrl+C, "resume" = start-runner.sh.)

## Rename an existing runner (e.g. the auto-named DESKTOP-… → malf-runner)

There's no in-place rename — remove the old registration and re-register:

```bash
cd ~/actions-runner-malf
# if a background service was installed, remove it first:
sudo ./svc.sh stop 2>/dev/null; sudo ./svc.sh uninstall 2>/dev/null || true
# deregister the current runner:
./config.sh remove --token "$(gh api -X POST /orgs/CodeRoasted/actions/runners/remove-token -q .token)"
cd -                                   # back to the workspace
malf/runner/install-runner.sh          # re-registers as "malf-runner"
malf/runner/start-runner.sh            # foreground
```
(Or just remove it from the org runners UI — the ⋯ menu → Remove — then re-run install.)

## Toggle: hosted ⇄ local (one variable, no code edits)

```bash
# route ALL private CI + releases to this box (when minutes are low / the runner is up):
gh variable set CI_RUNS_ON --org CodeRoasted --body malf-local --visibility private

# back to GitHub-hosted:
gh variable delete CI_RUNS_ON --org CodeRoasted
```

When `CI_RUNS_ON` is unset, `runs-on` falls back to `ubuntu-latest` — so the default is
unchanged and nothing breaks if the runner is offline. Set it only while the runner is
running (jobs queue until a matching runner is online).

**Which workflows obey it:** the private repos `coderoast-security`, `coderoast-server`,
`insight-eidos`, `logcraft` (`ci.yml` + `release-publish.yml`) and the private
superproject's gates + lints (`determinism-gate`, `fuzz-asan-gate`, and the `*-lint`
workflows). The heavy cross-package gates are the biggest savings.

## Notes

- **Warm caches = faster than hosted.** A persistent runner keeps the conan cache,
  `/opt/gcc-15.3`, and apt state between jobs (the in-job `setup-*` actions are
  idempotent), so after the first run, builds skip the cold-cache dependency rebuild
  that dominates the GitHub-hosted runs.
- **Determinism/fuzz gates** clone fresh and use their own build dirs, so a persistent
  workspace is fine. If you ever want clean-room fidelity, re-run `install-runner.sh`
  with the runner reconfigured `--ephemeral` (one job per registration).
- **Windows/MSVC** jobs (`windows-portability-probe`) are a separate concern — they need
  a Windows runner and the `setup-msvc1452` toolchain; this Linux runner does not serve
  them. They run rarely (`workflow_dispatch`) and canon/metalog are public (free).
