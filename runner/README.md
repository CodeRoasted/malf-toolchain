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

## Isolation: no job reads a desk credential (ROADMAP N214)

The safety rule above keeps foreign code off the box; this keeps OUR jobs — and every third-party
conan recipe, action and image they pull — away from the Founder's credentials. The installers
below register a runner under whichever account runs them, which on this box is the desk account;
the isolation scripts then move each runner onto an account of its own. **Run them after every
(re)install**: an installer run undoes them.

| Runner | Isolate (prints a plan; acts only with the flag) | Undo |
|---|---|---|
| WSL `malf-runner` (`ghrunner`) | `sudo bash malf/runner/isolate-runner-wsl.sh --instance ci --apply` | `isolate-runner-wsl-rollback.sh --instance ci --apply` |
| WSL `malf-release` (`ghrelease`) | `sudo bash malf/runner/isolate-runner-wsl.sh --instance release --apply` | `isolate-runner-wsl-rollback.sh --instance release --apply` |
| Windows `malf-runner-win` | elevated `pwsh -File malf\runner\isolate-runner-windows.ps1 -Instance ci -Apply` | `isolate-runner-windows-rollback.ps1 -Instance ci -Apply` |
| Windows `malf-release-win` | elevated `pwsh -File malf\runner\isolate-runner-windows.ps1 -Instance release -Apply` (it installs the runner too) | `isolate-runner-windows-rollback.ps1 -Instance release -Apply` |

The Windows instances are one table, `runner-instances.ps1`: runner, directory, data root, group,
labels, and whether the desk reads the runner's directory. A script run names its instance; there
is no default.

The WSL instances are one table, `runner-instances.sh`: account, directory, Docker unit, the bank
roots each writes, whether the desk reads its home, and who holds the build slot. The plan (no flag)
renders every unit and script the apply would write and diffs each against what is installed, so
it checks the script against a runner that cannot be restarted; `--prove` runs the boundary proof
alone (below).

Each script's header names every door it closes and what stays open. In short: the WSL runner runs
as the system account `ghrunner` inside a systemd sandbox that hides the Windows drives and WSL
interop, with a rootless Docker of its own (never the `docker` group); each Windows runner logs on
as its service's virtual account `NT SERVICE\<service>`, not the Founder. The proof is the
superproject's `runner-isolation-probe.yml`, one `workflow_dispatch` run after the scripts, one leg
per runner: it tries every door from inside a job and passes only if each is refused and every
control works.
The probe runs in a FRESH workspace, so it cannot see what earlier jobs left: the Windows script
also re-owns and resets the runner's job workspace (`_work`), and the parts of it that need no
elevation are proven on the Windows host by `pwsh -File malf\runner\isolate-runner-windows.selftest.ps1`.

The build slot is shared with the desk through `/var/lib/coderoast-build` (the WSL script creates
it; `malf` resolves its slot there whenever it exists), because `/tmp` cannot hold a slot two
accounts can both reclaim.

## The desk's Docker: rootless, and the desk account outside the `docker` group

The rootful daemon's socket is `root:docker 0660`, so membership of `docker` is root on the host
(`docker run -v /:/host`): every process of the desk account, every agent lane included, could read
the release runner account's store-writer credential whatever its home's mode (`DN-142.D6`, R1). The
desk therefore runs a rootless dockerd of its own, as the runners do, and leaves the group (the
Founder, 2026-10-10).

`bash malf/runner/desk-rootless-docker.sh` (as the desk account, never sudo) prints the plan and
the drift; `--apply` installs a USER unit, `coderoast-desk-docker.service` (rootlesskit + dockerd,
socket `$XDG_RUNTIME_DIR/docker.sock`, kept up by lingering), sets `DOCKER_HOST` for every new shell
(a marked block in `~/.bashrc`) and every user unit (`~/.config/environment.d`), and smoke-tests the
fixtures' shape — a container published on `127.0.0.1` answering. `DOCKER_HOST`, never a `docker
context`: with both set the CLI warns on stderr, and the infra test fixtures read stderr and stdout
as one stream. The rootless image store starts empty; nothing under `/var/lib/docker` is migrated.

The two steps that need root, in this order:

1. `sudo gpasswd -d windows docker` — harmless to a live run: a process keeps the groups it started
   with, so nothing loses the group before the next login.
2. When no lane and no runner job is live: `sudo systemctl disable --now docker.socket docker.service`
   (with the desk out of the group the rootful daemon serves nobody — each runner has its own), then
   `wsl.exe --shutdown` from Windows. Lingering keeps the user manager, and every process under
   it, alive with the OLD groups until the distro restarts; a new terminal is not enough.

Then, from a fresh terminal, `bash malf/runner/desk-rootless-docker.sh --prove`: no `docker` group,
the rootful socket refuses the account, the rootless daemon answers, and a container bind-mounting
`/home/ghrelease` or `/` — unprivileged and `--privileged` — reads neither the writer's credential
nor `/etc/shadow`, each refusal paired with a control read of the account's own file.

**What this does not close, and the proof prints it as RESIDUAL:** WSL interop. `wsl.exe -u root`
answers uid 0, with no password, to any process of the desk account (measured 2026-10-10), because
the Windows user owns the distro. Leaving `docker` closes one root door of the desk, not the last.

## Runner groups: what one job leaves for the next

Isolation keeps a job away from the DESK; it does not keep one job away from the NEXT. A runner runs
every job as its one account, and that account owns what the next job executes: `malf-runner`'s
`ghrunner` owns the runner's own binaries (`~/actions-runner-malf/bin/`), the venv its `.path` puts
first on every job's PATH (`conan`, `cmake`, `ninja`, `pip`), and everything under its HOME. The
runner group decides whose jobs those are. Both runners sit in `Default`, which admits every private
repository of the organisation (read it back below), so a dependency any of them runs in CI can
leave behind what a later job builds a release with.

The one thing the build tooling hands out on that basis is the conan home that outlives the job
(`setup-build-env`'s `persistent-conan-home`, `DN-119.D2`), and `conan-home.sh` refuses it unless the
job's runner is a RELEASE runner, three facts it checks at every job start:

1. GitHub's record of the job (the jobs API, the job's token with `actions: read`) puts it in the
   runner group **`coderoast-release`**;
2. among the runner units in `/etc/systemd/system`, the ones run by the job's account are exactly
   its own runner's;
3. every directory from that account's HOME down to the home is the account's and writable by no
   other, and every entry in the home is the account's.

What no job can read is who the group admits, so it is set by an organisation owner and read back
from the desk. **The setting** (Settings → Actions → Runner groups → New runner group):

| Field | Value |
|---|---|
| Name | `coderoast-release` |
| Repository access | **Selected repositories**: `coderoast` — and each repository whose release job is routed to it, no other |
| Allow public repositories | off |
| Workflow access | **All workflows** — the plan offers the restriction, but only over refs that already exist, so it cannot admit a tag release before its tag is pushed (measured below) |

and the runner registered into it (`config.sh --runnergroup coderoast-release`, or moved in the
runners page) under an account that runs no other runner. **The read-back**, as the desk:

```bash
gh api orgs/CodeRoasted/actions/runner-groups \
  --jq '.runner_groups[] | {id, name, visibility, allows_public_repositories, restricted_to_workflows, selected_workflows}'
gh api orgs/CodeRoasted/actions/runner-groups/<id>/repositories --jq '.repositories[].full_name'
gh api orgs/CodeRoasted/actions/runners --jq '.runners[] | {name, labels: [.labels[].name]}'
```

`Default` reads `"visibility": "all"` (2026-09-29), with `malf-runner` and `malf-runner-win` in it;
`coderoast-release` holds `malf-release` and `malf-release-win` (2026-09-30).

### The release runner (`DN-119.D8`, option A)

`malf-release` runs as `ghrelease`, a second OS account on this box, and sits in `coderoast-release`
(id 3). It carries **one label, `coderoast-release`, and none of the defaults** (`self-hosted`,
`Linux`, `X64`): a job reaches it only by naming that label, and only from a repository the group
admits. What it adds over the ci instance, and why:

| Control | Why |
|---|---|
| home mode 700, no ACL, every file `ghrelease`'s | the account is the boundary: no ci job writes what a release executes, links or takes a verdict from |
| `ProtectHome=tmpfs` + its own home bound back | no other home is visible from a release job, so nothing planted there is reachable even by a PATH mistake |
| `ProtectSystem=full`, `PrivateIPC=yes`, a private `/dev/shm` | step 0 runs the shared-memory transport's tests; a segment another account created under the same name would feed them |
| its own venv, pinned from the desk's venv (never `ghrunner`'s) | a ci job can write `ghrunner`'s venv, so its package list is not a trusted source |
| the registration token through stdin into `config.sh`'s environment | `/proc/<pid>/cmdline` is world-readable here; a token read from an argv enrols any runner into any group for an hour |
| the build slot: `ghrelease` rw, `ghrunner` **no entry** | step 0 takes the slot; `malf slot` flocks `slot.lock` and flock works on a read-only descriptor, so read alone would let a ci job stall step 0 |

**The group's scope (Argos, 2026-09-29).** It admits `coderoast`, `coderoast-security`,
`coderoast-server`, `insight-eidos` and `logcraft`, and no other: `insight-canon`,
`insight-metalog` and `coderoast-ipc` are PUBLIC repositories, which the group refuses
(`allows_public_repositories: false`) and the safety rule above forbids, and every one of their
release jobs runs on GitHub-hosted `ubuntu-latest` — a fresh machine per job, never this box.

**Workflow restriction: available, and not usable here (measured 2026-09-29).** The organisation's
plan accepts `restricted_to_workflows` (`workflow_restrictions_read_only: false`), but every entry must
name a ref that EXISTS: `release.yaml@refs/tags/v*` and `release.yaml@*` answer HTTP 400 ("was not
found"), and an entry with no ref answers 400 ("must be pinned"). A tag release runs at a tag the cut
has not pushed yet, and the restriction applies to the whole group, so turning it on would lock every
tag release out. It stays off; the routing lives in the committed workflows, where only the declared
release jobs and the two jobs holding the production SSH key name `coderoast-release`: coderoast-server's
deploy and the weekly `Security` workflow's production reads, both held to that one label by the
superproject's `action_pins` check (G8), which refuses any runner for a job naming `SSH_PRIVATE_KEY`
or `CODEROAST_SERVER_ENV` but this one.

**Applied 2026-09-29 by the Founder** (`--instance release --apply`, malf-toolchain `9e68ff9` + `868c538`),
and each undone by `isolate-runner-wsl-rollback.sh --instance release --apply` unless marked kept:

| Host change | As measured | Undo |
|---|---|---|
| account `ghrelease` | uid 994, group `ghrelease` only, nologin, locked, subuid/subgid 231072:65536 | kept (inert); the rollback prints the `userdel -r` line |
| `/home/ghrelease` | mode 700, no ACL; venv of 61 packages pinned from `/home/windows/venvs/common` (cmake 4.3.1); persistent conan home dir `.cache/coderoast-build/conan/malf-release` mode 700 | kept with the account |
| runner | actions/runner 2.337.0, SHA-256 `70920811…6613` verified; registered `malf-release`, group `coderoast-release` (id 3), label `coderoast-release` only | deregistered (removal token, stdin) |
| units | `actions.runner.CodeRoasted.malf-release.service`, `coderoast-release-docker.service` (enabled) | stopped, disabled, deleted |
| rootless Docker | a container published on 127.0.0.1 answered PONG; `-v` of `/home/windows`, `/mnt/c`, `/` and `/home/ghrunner` each refused | with its unit |
| build slot root | `u:windows:rwX,u:ghrelease:rwX`, no entry for `ghrunner` (was `ghrunner:rwx`) | recomputed: `ghrunner` rw again |
| home-mirror ACLs | `u:ghrelease:rX` on the three `corpora-*-backup` directories | removed |

**The boundary proof, as run** (`--prove`). Before the release runner existed, on `malf-runner`:
`ghrunner` wrote all 7 targets of the runner that then built every release (`Runner.Worker` busy but
permitted). After: as `ghrunner`, all 7 targets of `malf-release` refused `EACCES`, the owner's 7
control writes succeeded; and as `ghrelease`, all 7 targets of `malf-runner` refused `EACCES`.

**What the job-side refusal cannot see** (`setup-build-env`'s `conan-home.sh`): who the group admits.
Read it back from the desk with the three commands above after any change to the group.

The ci instance's live unit carries `JoinsNamespaceOf=` under `[Service]`, where systemd ignores it
(`systemd-analyze verify`: "Unknown key name"); the runner and its dockerd have never shared a
namespace. The script no longer writes the line, so the ci plan shows exactly that one line as drift
until the ci instance is next applied — which changes no behaviour.

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

**THE WINDOWS RUNNER SWITCH** (the Founder, 2026-10-08: the self-hosted Windows runners stay, and
every Windows leg keeps a runner switch — hosted by default, our own runners selectable, *"if
someday we lack github action minutes, we will end up stuck"*). ONE org variable flips every
private Windows leg at once:

```bash
gh variable set WINDOWS_RUNNER --org CodeRoasted --body self-hosted --visibility private  # → our runners
gh variable set WINDOWS_RUNNER --org CodeRoasted --body hosted --visibility private       # → windows-2025
```

Unset reads as `hosted`. Under `self-hosted`, a leg at a release coordinate (the release, and the
pre-tag reading of step 0's record) runs on the Windows release runner, `coderoast-release-windows`,
and every other run (the weekly schedule, a plain dispatch) on the label `WIN_RUNS_ON` names, this
runner (`malf-windows`); the superproject's `runner-isolation-probe.yml` reads `WIN_RUNS_ON` too.
The legs: insight-eidos's `golden.yaml` `proof-msvc` and `sift-windows.yml` `build`. The
superproject's `workflow_source` check module reds on a private repository's Windows leg that
hard-codes a hosted image. canon + metalog (and sift-action) Windows legs stay on hosted runners
with no switch: public = free minutes, and the runner groups refuse public repositories. First run installs
MSVC 14.52 (Insiders Preview, ~GBs) on the host via `setup-msvc1452`; needs git + python +
gh on Windows. (The eidos probe also needs Heph's `provision.cpp` Win32 port to go green —
the runner solves *minutes*, not that source blocker.)

### The Windows release runner (`DN-119.D8`)

`malf-release-win` is a second service on the same host, so a second virtual account:
`NT SERVICE\actions.runner.CodeRoasted.malf-release-win`, from `C:\actions-runner-release-win`, in the
group `coderoast-release`. It carries **one label, `coderoast-release-windows`, and none of the
defaults**. It builds what a release publishes or is gated on from Windows: the public
`sift-windows-x64.exe` (insight-eidos `sift-windows.yml`, job `build`) and the golden proof's MSVC
leg (`golden.yaml`, job `proof-msvc`). Until 2026-09-30 both ran on `malf-runner-win`, as the one
account every private repository's Windows CI runs as.

| Control | Why |
|---|---|
| a service of its own | the virtual account is derived from the service's name, so no ci job runs as it |
| its directory and its data root (`C:\malf-release-win`) name SYSTEM, Administrators and its own account, no other | no ci job writes its `Runner.Worker.exe`, its Python or its job workspace; the desk account, non-elevated, is refused too |
| its own NuGet `python` 3.12.10, the same two-producer pin | a ci job can write the ci runner's Python, so that copy is not a trusted source |
| the same MSVC 14.52 install, through a junction of its own, read+execute only | one toolset for both runners, in a directory neither account can write |
| the registration token through `ACTIONS_RUNNER_INPUT_TOKEN`, never an argument | a process's command line is readable on the host for as long as it runs; the token enrols a runner into any group for an hour |

**What `config.cmd` does with its local group (read 2026-09-30).** It makes one group PER RUNNER
DIRECTORY and grants it FullControl there: `GITHUB_ActionsRunner_Gb37c3` for the ci runner (members
SYSTEM and the desk account, which is how the desk reads that runner's logs) and
`GITHUB_ActionsRunner_G51f72` for the release runner (member SYSTEM alone). Neither virtual account
is a member of either. The release instance's directory drops its group's entry all the same; the
group itself stays, granted nothing (whether `config.cmd remove` needs to find it was not tried).

**The install runs as LocalSystem for a few seconds.** `config.cmd` can only register a service
under an account that exists, and the virtual account exists once the service does. So step 0
installs under LocalSystem, step 1 stops the service, and step 6 moves it. In between it is
reachable only by a job naming `coderoast-release-windows`, which no workflow named before the
runner was proven.

**Applied 2026-09-30 by the Founder** (`-Instance release -Apply`), each undone by
`isolate-runner-windows-rollback.ps1 -Instance release -Apply` unless marked kept:

| Host change | As measured | Undo |
|---|---|---|
| runner | actions/runner 2.337.0, SHA-256 `1150692a…5cfc` verified; registered `malf-release-win` (id 27), group `coderoast-release` (id 3), label `coderoast-release-windows` only | deregistered (`config.cmd remove`, removal token through the environment) |
| service | `actions.runner.CodeRoasted.malf-release-win`, auto start, logon its virtual account (SID `S-1-5-80-1724857083-…-2950988242`), SID type unrestricted | deleted with the registration |
| `C:\actions-runner-release-win` | owner Administrators, inheritance from `C:\` cut; SYSTEM and Administrators full, the virtual account modify; the entry of `GITHUB_ActionsRunner_G51f72` removed | kept, unconfigured |
| `C:\malf-release-win` | same three entries; `python312` (Python 3.12.10, pip 25.0.1), `LocalAppData`, `isolation\manifest.json` | kept (the last line the rollback prints deletes it) |
| MSVC | read+execute for the virtual account on `C:\Users\Windows\AppData\Local\malf-msvc1452`; junction `C:\malf-release-win\LocalAppData\malf-msvc1452` to it | grant and junction removed |
| local group | `GITHUB_ActionsRunner_G51f72` (made by `config.cmd`), member SYSTEM, no grant left | left to `config.cmd remove` (not tried) |

**The boundary proof, as run** (superproject `runner-isolation-probe.yml` at `129aa894`), from
inside a job and red first. Each Windows leg tries to write eight targets of the other instance
(its `bin\Runner.Worker.exe`, `bin`, the runner directory, `_work`, `python.exe`, the Python
directory, `LocalAppData`, the data root), then the MSVC install and every directory of the machine
PATH. Only an access refusal counts: a target that does not exist is `BROKEN`, and a busy file is a
`LEAK`, since Windows checks sharing after access.

* **Red first, run 36687835154** (`-f windows-red-first=true`, the ci leg aimed at the ci runner
  itself, then the builder of every release): 8 of 8 targets written; the MSVC install, its
  `cl.exe` and the 13 machine PATH directories refused; 9 of 9 controls.
* **After, run 36688880978**, four legs green. Windows `ci`, as the ci virtual account: 8 of 8
  targets of `malf-release-win` refused. Windows `release`, as the release virtual account: 8 of 8
  targets of `malf-runner-win` refused. Each: 40 attempts, 9 controls, 0 failed.

**The routing** (insight-eidos), behind the switch above. With `WINDOWS_RUNNER=self-hosted`,
`sift-windows.yml`'s `build` (which runs only at the release and on the pre-tag reading) and
`golden.yaml`'s `proof-msvc` at those two coordinates run here, so the release runner's MSVC build
is read before the first tag; by default they run on `windows-2025`, the image the release then
uses too. The label appears only inside expressions, which actionlint does not check as runner
labels, so no `actionlint.yaml` names it.

**What the proof cannot see:** who the group admits (read it back with the three commands above),
and a directory ADDED to the machine PATH later: the probe tries the PATH as it is on the day it
runs, and each runner's `.env` carries the PATH as it was when its script last ran.

## The store access probe (`DN-142.D6`, R5)

The reader's side of the artefact store's ghcr replica: a repository's `GITHUB_TOKEN` reads the
store package it is granted, is refused on one it is not, and cannot write its own. The instrument is
malf-toolchain's `.github/workflows/store-access-probe.yml`; three repositories call it, each a
dispatch-only `store-access-probe.yml`, and the callers are crossed so every refusal is answered by
another run reading the same package:

| Caller | Granted (must read, byte-equal) | Not granted (must be refused) |
|---|---|---|
| `coderoast-ipc` (public) | `store-coderoast-ipc` | `store-insight-canon` |
| `insight-canon` (public) | `store-insight-canon` | `store-coderoast-ipc` |
| `coderoast` (private) | `store-coderoast` | `store-coderoast-ipc` |

Each run also PUTs a manifest into its granted package with a `packages: write` token; the refusal
must come at the manifest (a blob upload may be accepted first, as R4 measured, and is no object).

**1. Seed each package, as the writer account** (the Founder's terminal; the token is read inside
`ghrelease`'s process and reaches curl on stdin, never an argument vector):

```bash
seed() { sudo -u ghrelease -H bash -s -- "$1" <<'SEED'
set -euo pipefail
pkg=$1; api=https://ghcr.io/v2/coderoasted/$pkg; w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
printf 'coderoast store access probe\n' > "$w/layer"; printf '{}' > "$w/config"
printf 'user = "coderoast-dev:%s"\n' "$(cat ~/.config/coderoast/STORE_WRITER_TOKEN)" \
  | curl -fsS -K - -o "$w/t.json" "https://ghcr.io/token?service=ghcr.io&scope=repository:coderoasted/$pkg:pull,push"
auth() { printf 'header = "Authorization: Bearer %s"\n' "$(jq -r .token "$w/t.json")"; }
put_blob() { local d loc s; d=sha256:$(sha256sum "$1" | cut -d' ' -f1)
  loc=$(auth | curl -fsS -K - -X POST -o /dev/null -D - "$api/blobs/uploads/" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p')
  [[ $loc == http* ]] || loc=https://ghcr.io$loc; [[ $loc == *\?* ]] && s='&' || s='?'
  auth | curl -fsS -K - -X PUT -H 'Content-Type: application/octet-stream' --data-binary "@$1" "$loc${s}digest=$d" -o /dev/null
  echo "$d"; }
l=$(put_blob "$w/layer"); c=$(put_blob "$w/config")
jq -nc --arg l "$l" --arg c "$c" '{schemaVersion:2, mediaType:"application/vnd.oci.image.manifest.v1+json",
  artifactType:"application/vnd.coderoast.access-probe.v1",
  config:{mediaType:"application/vnd.oci.empty.v1+json",digest:$c,size:2},
  layers:[{mediaType:"application/octet-stream",digest:$l,size:29}]}' > "$w/m.json"
auth | curl -fsS -K - -X PUT -H 'Content-Type: application/vnd.oci.image.manifest.v1+json' \
  --data-binary "@$w/m.json" "$api/manifests/access-probe" -o /dev/null -w "$pkg:access-probe HTTP %{http_code}\n"
SEED
}
seed store-coderoast-ipc; seed store-insight-canon; seed store-coderoast
```

Each line must print `HTTP 201`; the layer's digest is `sha256:5e8e719d…f403`, which the probe
checks byte-for-byte. The push mechanics were exercised against a local `registry:2` on
2026-10-10 (manifest 201, layer read back at that digest); against ghcr they are first run here.

**2. In each package's settings** (`https://github.com/orgs/CodeRoasted/packages/container/<package>/settings`):
*Danger Zone → Change visibility → Private* (a new package came up `internal` in R4, readable by
every organisation member); *Manage Actions access → Add repository →* the package's own caller from
the table, role **Read**, and no other repository. The page must show no linked repository.

**3. Dispatch, then read each run:**

```bash
gh workflow run store-access-probe.yml -R CodeRoasted/coderoast-ipc
gh workflow run store-access-probe.yml -R CodeRoasted/insight-canon
gh workflow run store-access-probe.yml -R CodeRoasted/coderoast
gh run list -w store-access-probe.yml -L 1 -R CodeRoasted/<repository>   # then: gh run view <id> --log
```

**Expected:** each run green, with three `pass` lines — the granted read (`HTTP 200`, 29 bytes,
content `sha256:5e8e719d…`), the other package's read refused (`401`/`403`/`404` at the manifest),
the write refused at the manifest (`401`/`403`). A `LEAK` line is an access-control finding against
R5: report it, do not re-run around it; a successful write leaves the tag `access-probe-write-<run
id>`, deleted by hand. A `FAIL` on the granted read means the seed or the grant is wrong, and every
refusal in that run is then meaningless. `UNCLEAR` is an answer the probe does not classify.

## Notes

- **Host-tool invariant: `patchelf` is installed on every Linux build seat.** Three consumers:
  the `package()` of `coderoast_server` and `insight_sift_tools` (`patchelf --remove-rpath` on
  the published executable, `DN-142.D20` (3)), `artefact_store`'s key, which measures it in the
  toolchain member, and the eidos release packaging of `sift-linux-x64`. `setup-build-env`
  provisions it in its apt base and prints the one that runs; on these self-hosted accounts the
  apt state is the host's, so it is already present (patchelf 0.18.0 since 2026-08-16) and no job
  needs sudo for it. A rebuilt host restores it with `sudo apt-get install -y patchelf`, or the
  first job reaching `setup-build-env` asks for sudo and fails.
- **Warm caches = faster than hosted.** A persistent runner keeps the conan cache,
  `/opt/gcc-16.2`, and apt state between jobs (the in-job `setup-*` actions are
  idempotent — they **detect-and-skip** when the toolchain is already present at the
  required version), so after the first run, builds skip the cold-cache dependency rebuild
  that dominates the GitHub-hosted runs.
- **CI binds no fixed host port for its backends.** No workflow attaches GitHub `services:`
  containers: the `coderoast-server` infra tests start their own digest-pinned containers
  (`infra/tests_support/local_container.hpp`) on a host port the **docker daemon allocates** and
  the test reads back, so a host service on 5432/6379 cannot collide with a CI job. The port
  preference belongs to local dev only — `bash coderoast-server/scripts/start_postgres_dev.sh`
  (`pg_coderoast_dev`) prefers host 5432 and `start_redis_dev.sh` prefers 6379; each takes
  `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` when set, otherwise falls back to the next free port,
  and prints the `*_HOST`/`*_PORT` it chose. The trap that remains is a **silent move, not a
  failure**: a hand-`apt install postgresql` leaves a Debian cluster that `postgresql-common`
  `systemctl enable`s, so it **auto-starts on every boot** and holds 5432 — the dev container
  lands on another port, and a dev server left on the default `POSTGRES_PORT` talks to the host
  cluster instead. Local dev gets its DB from the repo's **docker** container, not the host
  cluster: `sudo systemctl disable --now postgresql` (the cluster's data is preserved; it just
  won't auto-start).
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
