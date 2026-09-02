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

Host tools it needs beyond `gh`: `curl`, `tar`, **`jq`** and **`sha256sum`** — the last two
because the download is SHA-256-verified before it is unpacked, and the script refuses to
run rather than skip that check. It fails at the preflight, naming the missing one.

`install-runner.sh` mints an org registration token (via `gh`), downloads the latest
runner into **`~/actions-runner-malf`** — **verifying its SHA-256 against the digest the
release publishes, and unpacking nothing on a mismatch** — and configures it against `github.com/CodeRoasted`
with name **`malf-runner`** and label **`malf-local`**. It does **not** start anything —
`start-runner.sh` runs it in the foreground so you watch jobs stream and `Ctrl+C` to stop.
**Foreground is for watching a job, not for keeping a runner up.** A foreground listener is a child
of the terminal that launched it and dies with it — under a VS Code remote that is every server
restart, mid-job included. For a runner that stays up use `AS_SERVICE=true`. **The parenthetical
that stood here — *"cleaner than a service under WSL2"* — was FALSE and is withdrawn** (measured
2026-09-02: `actions.runner.CodeRoasted.malf-runner.service` is `enabled` and `active`, `runsvc.sh`
is parented to PID 1, and GitHub reports the runner online).

**Under WSL2 the unit is only HALF the fix, and the half it is not is the one that decides.** A
system unit runs only while the distro is up, and Windows stops the distro when its last client
detaches — so the unit cures the terminal-restart death and not the distro-shutdown death. The other
half is a Windows-side keepalive: a logon task holding `wsl.exe -d <distro> -u root --exec
/usr/bin/sleep infinity`. Verify BOTH, separately: `systemctl is-active …` after killing the VS Code
server, and again after a `wsl --shutdown`.

Override via env: `ORG`, `LABELS`, `RUNNER_NAME`, `RUNNER_DIR`, `RUNNER_ARCH`, `RUNNER_TOKEN=…`
(skip the gh mint), or `AS_SERVICE=true`.

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

## Windows runner (eidos Windows Portability Probe)

MSVC needs a **native Windows** host — the Linux runner above (in WSL2) can't serve it.
`install-runner.ps1` is the Windows twin: run it from an **elevated** PowerShell on the host
(same machine, outside WSL2) to register an org runner with the label **`malf-windows`** **as
a Windows service**, then route the eidos probe to it:

**Host prerequisites** — the GitHub-hosted `windows-2025` image pre-bakes these; a fresh
host doesn't. The probe steps use `shell: pwsh` (**PowerShell 7**, not the built-in
Windows PowerShell 5.1) and Python (its CMake 4.3 step is `python -m pip install`). Install
once (git + gh you already have if you registered the runner):

```powershell
winget install --id Microsoft.PowerShell --source winget   # pwsh 7 — REQUIRED (shell: pwsh)
winget install --id Python.Python.3.12   --source winget   # Python — REQUIRED (CMake pip step)
```
Without `pwsh` the very first probe step fails with `pwsh: command not found`. setup-msvc1452
installs the MSVC 14.52 toolset itself, so you do **not** pre-install Visual Studio.

```powershell
# ELEVATED PowerShell (Run as Administrator) — this installs a Windows SERVICE.
pwsh -ExecutionPolicy Bypass -File malf\runner\install-runner.ps1     # registers "malf-runner-win" (label malf-windows)
```
The installer configures with `--runasservice` under `NT AUTHORITY\SYSTEM` (override with
`WINDOWS_LOGON_ACCOUNT` + `WINDOWS_LOGON_PASSWORD`) and refuses a `\\wsl.localhost\…` runner
directory. On Windows that one `config.cmd` call *is* the whole service lifecycle — it grants
file permissions, registers the service, sets delayed auto-start and recovery, and starts it.
There is no `svc.cmd` in the Windows layout (that is the Linux `svc.sh` wrapper), so nothing
here calls one.

A second run on a working box is the normal case, and `--replace` does not cover it:
`--replace` settles the *server-side* name collision, while a runner **directory** that is
already configured is refused outright. So the installer first runs `config.cmd remove
--local` — the token-free half of removal, which deletes `.runner` and `.credentials` without
contacting GitHub — then deletes any `actions.runner.*` service executing from that
directory. Afterwards it confirms exactly one such service runs from it, matched on the
binary path rather than the name so a co-resident runner is never mistaken for this one, and
waits up to 60 s for `Running` before reporting success. It also deletes the legacy logon
Scheduled Task `CodeRoast Runner Win` (override with `LEGACY_TASK_NAME`), which would
otherwise claim the same registration at logon.
**Do not run `start-runner.ps1` against a service-installed runner** — that is the foreground
launcher, and only for a runner you deliberately configured without a service.

```bash
gh variable set WIN_RUNS_ON --org CodeRoasted --body malf-windows --visibility private   # → local
gh variable delete WIN_RUNS_ON --org CodeRoasted                                          # → windows-2025
```

**Only the PRIVATE `insight-eidos` probe reads `WIN_RUNS_ON`.** canon + metalog Windows
probes stay hard-pinned to `windows-2025` (public = free + fork-safe). First run installs
MSVC 14.52 (Insiders Preview, ~GBs) on the host via `setup-msvc1452`; needs git + python +
gh on Windows. (The eidos probe also needs Heph's `provision.cpp` Win32 port to go green —
the runner solves *minutes*, not that source blocker.)

## Notes

- **Host-tool invariant: `patchelf` is installed on the Linux runner.** The eidos release
  packaging strips the build-host runpath off the published `sift-linux-x64` with it
  (`.github/workflows/release.yaml`, the consumer-runnability gate) — same packaging class
  as `strip`. Provisioned 2026-08-16 (patchelf 0.18.0); a rebuilt host must restore it or
  that release step reds loudly.
- **Warm caches = faster than hosted.** A persistent runner keeps the conan cache,
  `/opt/gcc-16.2`, and apt state between jobs (the in-job `setup-*` actions are
  idempotent — they **detect-and-skip** when the toolchain is already present at the
  required version), so after the first run, builds skip the cold-cache dependency rebuild
  that dominates the GitHub-hosted runs.
- **The host must not squat a CI service-container port.** Some private workflows attach
  GitHub `services:` containers that publish a **fixed host port** — `coderoast-server`
  `ci.yml` runs `postgres:16-alpine` on host **5432** and `redis:7-alpine` on host **6379**.
  If a boot-enabled host service already holds that port, the service container can't bind it
  and the job fails (cryptic "port already allocated" / connection-refused). The classic trap:
  a hand-`apt install postgresql` leaves a Debian cluster that `postgresql-common`
  `systemctl enable`s, so it **auto-starts on every boot** and squats 5432 — colliding with
  CI's own ephemeral postgres. Keep these ports free on the host: `sudo systemctl disable --now
  postgresql` (the cluster's data is preserved; it just won't auto-start). Local dev gets its
  DB from the repo's **docker** container, not the host cluster — `bash
  coderoast-server/scripts/start_postgres_dev.sh` (`pg_coderoast_dev`, host port overridable via
  `POSTGRES_HOST_PORT`). CI owns 5432/6379 via its service containers; the host owns neither.
- **Elevation is one-time, not per-job — so the cleanest fix needs no standing grant.** The
  `setup-*` actions provision the toolchain *once* (they need root: `apt`/`tar`/`update-alternatives`
  on Linux, the VS Build Tools installer — which self-elevates → a UAC prompt — on Windows; MSVC
  lands in a **persistent** `%LOCALAPPDATA%\malf-msvc1452`). After that first provision they
  **detect-and-skip**, so **no password prompt / no UAC** on any later job. The recommended model
  is therefore: **bake the toolchain once** — accept the few sudo prompts (or the one UAC) on the
  first job, or pre-run the install commands by hand once — then every subsequent job skips with
  zero elevation. No persistent passwordless-root grant required.
- **If you want even the first provision non-interactive:** add `NOPASSWD` sudo for the runner user.
  Be honest about what that buys: `apt-get` / `tar` / `python3` *as root* are each effectively root,
  so a "scoped" command list is tidiness, **not** a real boundary against malicious code — the actual
  guarantee is the ⛔ safety rule (this box never runs public / fork code). Grant it *only* because
  that rule holds, keep it command-scoped (never `NOPASSWD: ALL`), and prefer bake-once above when you can:

  ```bash
  # /etc/sudoers.d/malf-runner  (edit with `visudo -f`):
  <runner-user> ALL=(root) NOPASSWD: /usr/bin/apt-get, /usr/bin/tar, /usr/bin/update-alternatives, /usr/bin/mkdir, /usr/bin/ln, /usr/bin/python3
  ```

  On Windows the first VS install shows UAC once (accept it) or run the provisioning job from an
  already-elevated runner shell; it does not recur once `malf-msvc1452` exists.
- **Determinism/fuzz gates** clone fresh and use their own build dirs, so a persistent
  workspace is fine. If you ever want clean-room fidelity, re-run `install-runner.sh`
  with the runner reconfigured `--ephemeral` (one job per registration).
