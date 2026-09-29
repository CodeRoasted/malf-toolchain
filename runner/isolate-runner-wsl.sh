#!/usr/bin/env bash
# isolate-runner-wsl.sh — put one self-hosted runner of this WSL box under an OS account of its own,
# inside a systemd sandbox, with a rootless Docker of its own. The instances are the table in
# runner-instances.sh: `ci` (malf-runner, ghrunner — ROADMAP N214) and `release` (malf-release,
# ghrelease — DN-119.D8).
#
#   sudo bash malf/runner/isolate-runner-wsl.sh --instance <ci|release>            # PLAN: changes nothing
#   sudo bash malf/runner/isolate-runner-wsl.sh --instance <ci|release> --apply    # do it
#   sudo bash malf/runner/isolate-runner-wsl.sh --instance <ci|release> --prove    # the boundary proof only
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh --instance <ci|release>   # undo it
#
# THE BOUNDARY PROOF (--prove, and the last step of every --apply). As every OTHER runner account,
# try to write what this runner executes, links and records: bin/Runner.Worker, venv/bin/conan, the
# persistent conan home, the job work directory, .path and .env — each by a real open for writing or
# a real file creation, judged by errno. Every one must be refused. The CONTROL is the same attempts as
# this runner's own account, which must succeed: a probe that cannot see a writable file proves
# nothing by refusing. Run against the ci instance on a box with no release runner, it shows the
# defect this instance closes: the account that runs every private repository's CI writes the
# runner that builds releases.
#
# THE PLAN IS ALSO THE DRIFT CHECK. It renders every unit and script this run would write and diffs
# each against what is installed, so a plan on an applied instance says whether --apply would change
# anything — the way to check this script against a runner that cannot be restarted.
#
# WHAT A JOB COULD READ BEFORE N214, measured 2026-09-26 on this box, each one a separate door:
#   1. the desk HOME itself — the job ran AS the desk user (gh token in ~/.config/gh/hosts.yml,
#      ~/.ssh, the workspace's .env.local), and wrote into the desk's Python venv through
#      conan-io/setup-conan's `python -m pip install`, because the runner's PATH began with it;
#   2. every drvfs drive (/mnt/c, /mnt/d, /mnt/e): WSL mounts them uid=1000 mode 0777 with no
#      umask, so ANY Linux account reads C:\Users\<desk>\.ssh — a new account alone closes nothing;
#   3. WSL interop: /run/WSL/*_interop sockets are mode 0777 and binfmt_misc hands any PE file to
#      /init, so any Linux account runs Windows programs AS the desk's Windows user — Credential
#      Manager, the Windows gh token, and `wsl.exe -u root` (root in this distro, no password);
#   4. the `docker` group: the rootful socket is root-equivalent (`docker run -v /:/host`);
#   5. /opt/gcc-16.2: 139 world-writable directories in the ship compiler, so a job could plant a
#      binary the DESK then executes — a door back INTO the desk account, not out of it.
# A dedicated account closes 1; the rest need the systemd sandbox (2, 3), rootless Docker (4) and
# a permission repair (5). The probe workflow (.github/workflows/runner-isolation-probe.yml in the
# superproject) tries every door from inside a job and passes only if each one is refused.
#
# WHAT THE RELEASE INSTANCE ADDS (DN-119.D8): a job of the ci runner must not write anything the
# release runner executes, links or takes a verdict from. The account is that boundary — its home is
# mode 700 with no ACL, and every file under it is its own. Its unit adds ProtectHome=tmpfs with its
# own home bound back (no other home is visible from a release job), ProtectSystem=full, a private
# /dev/shm and a private IPC namespace (step 0 runs the shared-memory transport's tests, and a
# segment another account created under the same name would feed them). Its runner carries no
# label but `coderoast-release` and registers into the group `coderoast-release`. It takes the build
# slot, which the ci account then loses entirely (runner-instances.sh, slot_holds).
#
# WHAT STAYS, stated so nobody reads more into this: localhost TCP services on this box (ollama,
# the desk's dev servers, and any port another runner's job listens on) remain reachable from a job —
# a network namespace would break the container fixtures, which publish on 127.0.0.1; an AF_VSOCK
# path to the Windows host that bypasses /init is not closable by any configuration here; the
# boundary between two runner accounts is a Unix account on one kernel, so a local privilege
# escalation crosses it; and the ci runner still sees every process's argv under /proc.
#
# IDEMPOTENT. A second run on an applied instance re-derives and re-applies every permission, mirror
# and unit, and moves nothing. REFUSES, changing nothing, on anything it did not expect: a job
# running on that instance, a runner in neither the before nor the after shape, an account of the
# runner's name that this script did not make.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runner-instances.sh
. "$HERE/runner-instances.sh"

OLD_SLOT="/tmp/coderoast-build-slot"
PACKAGES=(acl uidmap rootlesskit slirp4netns docker-buildx)
# Public mirror (no Docker Hub pull), the same digest coderoast-server's redis fixture pins.
SMOKE_IMAGE="ghcr.io/coderoasted/mirror/library/redis:7-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
SUBID_COUNT=65536

APPLY=false
PROVE_ONLY=false
REQUESTED=""
usage() { echo "usage: sudo bash $0 --instance <${INSTANCES[*]// /|}> [--apply | --prove]" >&2; exit 2; }
while (($#)); do
    case "$1" in
        --instance) REQUESTED="${2:-}"; shift 2 || usage ;;
        --apply) APPLY=true; shift ;;
        --prove) PROVE_ONLY=true; shift ;;
        *) usage ;;
    esac
done
$APPLY && $PROVE_ONLY && usage
[[ -n "$REQUESTED" ]] || usage
instance_select "$REQUESTED" || usage

say()  { printf '\033[1;34m[isolate %s]\033[0m %s\n' "$INSTANCE" "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[isolate %s] REFUSED:\033[0m %s\n' "$INSTANCE" "$*" >&2; exit 1; }
UNDO="sudo bash $HERE/isolate-runner-wsl-rollback.sh --instance $INSTANCE --apply"

# ─── Preflight — reads only ──────────────────────────────────────────────────────────────────────

[[ "$(ps -o comm= -p 1)" == systemd ]] || die "PID 1 is not systemd — this box's runners are systemd units; /etc/wsl.conf needs [boot] systemd=true"
[[ $EUID -eq 0 ]] || die "run it as root, plan included (it reads $STATE_DIR, which is root's): sudo bash $0 --instance $INSTANCE"

unit_key() { sed -n "s/^$1=//p" "$UNIT_FILE" | head -n1; }
manifest_of() { sed -n "s/^$2=//p" "$1/manifest"; }

# prove_boundary — the boundary proof described in the header, for the selected instance. Creates and
# deletes only its own probe files; opens the other targets for appending and writes nothing. Returns 0
# only when every other account is refused everywhere and the owner succeeds everywhere.
prove_boundary() {
    local targets=("file:$NEW_DIR/bin/Runner.Worker" "file:$RUNNER_HOME/venv/bin/conan"
                   "dir:$RUNNER_HOME/.cache/coderoast-build/conan/$RUNNER_NAME" "dir:$NEW_DIR/_work"
                   "file:$NEW_DIR/.path" "file:$NEW_DIR/.env" "dir:$RUNNER_HOME")
    local writers=("$RUNNER_USER") other user verdict rc=0
    for other in "${INSTANCES[@]}"; do
        [[ "$other" == "$INSTANCE" ]] && continue
        user="$(instance_select "$other" && echo "$RUNNER_USER")"
        id "$user" >/dev/null 2>&1 && writers+=("$user")
    done
    (( ${#writers[@]} > 1 )) || say "no other runner account exists on this box — only the control runs"
    for user in "${writers[@]}"; do
        # Run from / : the writer may be unable to enter the caller's working directory.
        verdict="$(cd / && sudo -u "$user" -H python3 - "$user" "$RUNNER_USER" "${targets[@]}" <<'PY'
import errno, os, sys, tempfile
writer, owner, targets = sys.argv[1], sys.argv[2], sys.argv[3:]
expect_ok = writer == owner
bad = 0
for spec in targets:
    kind, path = spec.split(":", 1)
    shown = path
    try:
        if kind == "file":
            os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
        else:
            # A directory not created yet is probed at its nearest existing ancestor, and says so:
            # whoever can create entries there can create the directory itself.
            while not os.path.isdir(path) and os.path.dirname(path) != path:
                path = os.path.dirname(path)
                shown = f"{spec.split(':', 1)[1]} (absent; tested at {path})"
            fd, probe = tempfile.mkstemp(prefix=".boundary-probe.", dir=path)
            os.close(fd)
            os.unlink(probe)
        got = "WRITABLE"
    except OSError as err:
        # ETXTBSY: the kernel granted the permission and refused only because the file is running.
        got = "WRITABLE (busy)" if err.errno == errno.ETXTBSY else f"REFUSED ({errno.errorcode.get(err.errno, err.errno)})"
    refused_by_permission = got in ("REFUSED (EACCES)", "REFUSED (EPERM)", "REFUSED (EROFS)")
    ok = got.startswith("WRITABLE") if expect_ok else refused_by_permission
    bad += not ok
    print(f"  {'ok ' if ok else 'BAD'} as {writer:<10} {got:<22} {shown}")
sys.exit(1 if bad else 0)
PY
)" || rc=1
        printf '%s
' "$verdict"
    done
    return "$rc"
}

case "$INSTANCE" in
    ci)
        [[ -f "$UNIT_FILE" ]] || die "no unit file at $UNIT_FILE — install the runner as a service first (malf/runner/README.md)"
        UNIT_USER="$(unit_key User)"
        UNIT_WD="$(unit_key WorkingDirectory)"
        if [[ "$UNIT_USER" == "$RUNNER_USER" ]]; then
            STATE=applied
            [[ "$UNIT_WD" == "$NEW_DIR" ]] || die "the unit runs as $RUNNER_USER but from '$UNIT_WD', not $NEW_DIR — a shape this script never writes"
            [[ -f "$STATE_DIR/manifest" ]] || die "the unit runs as $RUNNER_USER but $STATE_DIR/manifest is absent — not this script's work"
            DESK_USER="$(manifest_of "$STATE_DIR" DESK_USER)"
            OLD_DIR="$(manifest_of "$STATE_DIR" OLD_DIR)"
        else
            STATE=fresh
            DESK_USER="$UNIT_USER"
            OLD_DIR="$UNIT_WD"
            [[ -n "$DESK_USER" && "$DESK_USER" != root ]] || die "the unit's User= is '${DESK_USER:-<unset>}' — expected the desk account"
            [[ "$(unit_key ExecStart)" == "$OLD_DIR/runsvc.sh" ]] || die "ExecStart is '$(unit_key ExecStart)', not $OLD_DIR/runsvc.sh"
            [[ -f "$OLD_DIR/.runner" && -x "$OLD_DIR/runsvc.sh" ]] || die "$OLD_DIR is not a configured runner (.runner or runsvc.sh missing)"
            [[ ! -e "$NEW_DIR" ]] || die "$NEW_DIR already exists — refusing to move a runner on top of it"
        fi
        ;;
    release)
        # The desk account and the venv every runner's pins derive from are the ci instance's record:
        # the release runner is installed on a box whose ci runner is already isolated, never before.
        ci_state="$(instance_select ci && echo "$STATE_DIR")"
        [[ -f "$ci_state/manifest" ]] || die "$ci_state/manifest is absent — isolate the ci runner first (--instance ci); its manifest names the desk account and the venv the release venv's pins come from"
        DESK_USER="$(manifest_of "$ci_state" DESK_USER)"
        DESK_VENV="$(manifest_of "$ci_state" DESK_VENV)"
        if [[ -f "$UNIT_FILE" ]]; then
            STATE=applied
            [[ "$(unit_key User)" == "$RUNNER_USER" && "$(unit_key WorkingDirectory)" == "$NEW_DIR" ]] \
                || die "$UNIT_FILE runs as '$(unit_key User)' from '$(unit_key WorkingDirectory)' — not the $RUNNER_USER runner at $NEW_DIR this script writes"
            [[ -f "$STATE_DIR/manifest" ]] || die "$UNIT_FILE exists but $STATE_DIR/manifest is absent — not this script's work"
        else
            STATE=fresh
            # A run stopped between the registration and the unit resumes; anything else in the way refuses.
            if [[ -e "$NEW_DIR" && ! -f "$NEW_DIR/.runner" ]]; then
                [[ -x "$NEW_DIR/config.sh" ]] || die "$NEW_DIR exists and is not a runner directory — refusing to install over it"
            fi
        fi
        ;;
esac
if $PROVE_ONLY; then
    printf '\n[isolate %s] boundary proof — %s (%s, %s), as the owner and as every other runner account:\n' "$INSTANCE" "$RUNNER_NAME" "$RUNNER_USER" "$NEW_DIR"
    if prove_boundary; then say "PROVEN: every other account is refused on every target, and the owner writes every one"; exit 0; fi
    die "the boundary does NOT hold (a BAD row above): another account can write this runner, or the control failed"
fi
DESK_UID="$(id -u "$DESK_USER")" || die "desk account '$DESK_USER' does not exist"
DESK_HOME="$(getent passwd "$DESK_USER" | cut -d: -f6)"
DESK_GROUP="$(id -gn "$DESK_USER")"

# The desk HOME is closed by plain permissions, and that is the control the whole design leans on,
# so it is checked rather than assumed: no bit for others, and a primary group holding the desk alone.
home_mode="$(stat -c %a "$DESK_HOME")"
(( (8#$home_mode & 8#007) == 0 )) || die "$DESK_HOME is mode $home_mode — others can traverse it; chmod o-rwx $DESK_HOME first"
group_members="$(getent group "$DESK_GROUP" | cut -d: -f4)"
[[ -z "$group_members" || "$group_members" == "$DESK_USER" ]] || die "group $DESK_GROUP (group of $DESK_HOME) also holds: $group_members"
# `ls` marks an ACL with a trailing '+', and needs no package the box may not have yet.
[[ "$(ls -ld "$DESK_HOME" | cut -c11)" != "+" ]] || die "$DESK_HOME carries an ACL — a grant its mode bits do not show; read it with getfacl before running this"

# No job may be running on THIS instance: the apply restarts it, and a job killed mid-step is a red
# with no cause. Another instance's job is none of this run's business.
if systemctl is-active --quiet "$UNIT"; then
    cg="$(systemctl show -p ControlGroup --value "$UNIT")"
    if [[ -n "$cg" && -r "/sys/fs/cgroup$cg/cgroup.procs" ]]; then
        while read -r pid; do
            [[ "$(cat "/proc/$pid/comm" 2>/dev/null)" == Runner.Worker ]] \
                && die "a job is running on $RUNNER_NAME (Runner.Worker pid $pid) — let it finish, then re-run"
        done < "/sys/fs/cgroup$cg/cgroup.procs"
    fi
fi

# The slot switch: once $SLOT_ROOT exists, every malf resolves the slot there. A lane holding the
# OLD /tmp slot at that moment would stop excluding anybody, so its release comes first.
if [[ ! -d "$SLOT_ROOT" && -e "$OLD_SLOT" ]]; then
    die "$OLD_SLOT exists — a desk lane holds the build slot ('malf slot status' names it). This script moves the slot to $SLOT_ROOT/slot; release it first ('malf slot release --token <t>'), then re-run"
fi

# The runner account: absent, or exactly the account this script makes.
if id "$RUNNER_USER" >/dev/null 2>&1; then
    ru_uid="$(id -u "$RUNNER_USER")"
    (( ru_uid < 1000 )) || die "account $RUNNER_USER exists with uid $ru_uid — not the system account this script creates"
    [[ "$(getent passwd "$RUNNER_USER" | cut -d: -f6,7)" == "$RUNNER_HOME:/usr/sbin/nologin" ]] \
        || die "account $RUNNER_USER exists with home/shell '$(getent passwd "$RUNNER_USER" | cut -d: -f6,7)'"
    other_users=()
    for other in "${INSTANCES[@]}"; do
        [[ "$other" == "$INSTANCE" ]] || other_users+=("$(instance_select "$other" && echo "$RUNNER_USER")")
    done
    for g in sudo docker adm admin wheel "$DESK_GROUP" "${other_users[@]}"; do
        id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qx "$g" && die "account $RUNNER_USER is in group '$g' — that membership is the hole this script closes"
    done
fi

# The Python venv every job's PATH puts first: its pins come from the desk's venv, which is what CI
# resolved until N214 — never from another runner's venv, which that runner's jobs can write.
if [[ "$INSTANCE" == ci && "$STATE" == fresh ]]; then
    # Moving by rename keeps the runner's registration and its work tree; a cross-device move would
    # be a 12 GB copy, so it is refused rather than attempted.
    [[ "$(stat -c %d "$(dirname "$OLD_DIR")")" == "$(stat -c %d /home)" ]] \
        || die "$OLD_DIR and /home are on different filesystems — a rename cannot move it"
    DESK_VENV=""
    while IFS= read -r entry; do
        [[ -f "$(dirname "$entry")/pyvenv.cfg" ]] && { DESK_VENV="$(dirname "$entry")"; break; }
    done < <(tr ':' '\n' < "$OLD_DIR/.path")
    [[ -n "$DESK_VENV" ]] || die "no Python venv on the runner's current PATH ($OLD_DIR/.path) — the parity baseline for the new venv is gone; this box is not in the measured shape"
elif [[ "$INSTANCE" == ci ]]; then
    DESK_VENV="$(manifest_of "$STATE_DIR" DESK_VENV)"
fi
[[ -x "$DESK_VENV/bin/python" && "$DESK_VENV" == "$DESK_HOME"/* ]] \
    || die "the pin source '$DESK_VENV' is not a Python venv under $DESK_HOME"

# The bank must be mounted: its permissions are set here, and an unmounted mountpoint would take them.
findmnt -rn "$BANK_ROOT" >/dev/null || die "$BANK_ROOT is not mounted — the E: disk mount (a Windows scheduled task) has not run"
for root in "${BANK_WRITABLE[@]}"; do
    [[ -d "$BANK_ROOT/$root" ]] || die "bank root $BANK_ROOT/$root does not exist"
done
for root in "$BANK_ROOT"/*/; do
    root="${root%/}"; [[ "$(basename "$root")" == lost+found ]] && continue
    m="$(stat -c %a "$root")"; (( (8#$m & 8#005) == 8#005 )) || die "bank root $root is mode $m — the runner could not read it"
done

# The desk-HOME corpus entries pharos and corpus_storage resolve through `~` (STORAGE.json declares
# "~/corpora-*" and "~/gcc-corpus-build"). Derived from the desk's home, never listed: a symlink is
# re-created in the runner's home; a real directory is bound read-only into it.
mirror_links=(); mirror_dirs=()
for entry in "$DESK_HOME"/corpora-* "$DESK_HOME"/gcc-corpus-build; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="$(basename "$entry")"
    if [[ -L "$entry" ]]; then
        target="$(readlink -f "$entry")"
        [[ "$target" != "$DESK_HOME"/* ]] || die "$entry points inside $DESK_HOME ($target), which the runner cannot reach"
        mirror_links+=("$name=$target")
    elif [[ -d "$entry" ]]; then
        mirror_dirs+=("$name")
    else
        die "$entry is neither a symlink nor a directory"
    fi
done

for bin in dockerd containerd runc iptables python3 curl jq sha256sum gh; do
    command -v "$bin" >/dev/null || die "$bin is not installed"
done
# Step 4 builds the venv's C extensions with the system compiler; one that cannot find its own headers
# refuses here rather than after three applied steps (measured 2026-09-26: gcc-13's
# /usr/lib/gcc/x86_64-linux-gnu/13 gone, `dpkg -V libgcc-13-dev` 174 files missing).
printf '#include <stddef.h>\n' | cc -E -x c - >/dev/null 2>&1 \
    || die "the system C compiler (cc) cannot preprocess <stddef.h>: its package is broken (dpkg -V on the gcc behind cc names the missing files; apt-get install --reinstall repairs it)"
missing=()
for pkg in "${PACKAGES[@]}"; do dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg"); done
for pkg in "${missing[@]}"; do
    [[ "$(apt-cache policy "$pkg" | sed -n 's/^ *Candidate: //p')" != "(none)" ]] || die "package $pkg has no install candidate"
done

# The release runner registers into its group, which must exist and admit no public repository. Who
# else it admits is read back from the desk (README § Runner groups): no job token can see it.
if [[ "$INSTANCE" == release ]]; then
    group_json="$(sudo -u "$DESK_USER" -H gh api "orgs/$ORG/actions/runner-groups" \
                  --jq ".runner_groups[] | select(.name == \"$RELEASE_GROUP\")")" \
        || die "the desk's gh could not read the organisation's runner groups — is it logged in as an owner of $ORG?"
    [[ -n "$group_json" ]] || die "the organisation has no runner group '$RELEASE_GROUP' — create it first (README § Runner groups)"
    [[ "$(jq -r .allows_public_repositories <<< "$group_json")" == false ]] \
        || die "runner group '$RELEASE_GROUP' allows public repositories — a fork PR would run on the release runner"
    [[ "$(jq -r .visibility <<< "$group_json")" == selected ]] \
        || die "runner group '$RELEASE_GROUP' is visible to '$(jq -r .visibility <<< "$group_json")' repositories, not a selected set"
    RELEASE_GROUP_ID="$(jq -r .id <<< "$group_json")"
    group_repos="$(sudo -u "$DESK_USER" -H gh api "orgs/$ORG/actions/runner-groups/$RELEASE_GROUP_ID/repositories" \
                   --paginate --jq '.repositories[].full_name' | sort | tr '\n' ' ')"
fi

# A subordinate id range for rootless Docker that overlaps nobody's.
subid_start() {   # <file> -> the first id past every existing range
    awk -F: 'BEGIN{m=100000} NF==3{e=$2+$3; if(e>m)m=e} END{print m}' "$1"
}

drvfs_mounts="$(awk '$3=="9p" && $4 ~ /aname=drvfs/ {print $2}' /proc/mounts | tr '\n' ' ')"

# ─── What this run writes — rendered once, diffed in the plan, installed by the apply ──────────────

# The sandbox both units of an instance carry. ONE definition, printed into each unit, so the runner
# and the daemon that runs its containers cannot drift apart: a container's bind mount is resolved in
# dockerd's namespace, so a daemon outside the sandbox would reopen every door it closes. Each unit
# gets its OWN private /tmp (and, for release, its own /dev/shm and IPC namespace): the two are not
# joined. N214's units carried `JoinsNamespaceOf=` under [Service], where systemd ignores it
# (systemd-analyze verify, 2026-09-29: "Unknown key name 'JoinsNamespaceOf' in section 'Service'"),
# so separate namespaces are the measured shape every CI run since 2026-09-26 has had.
sandbox_lines() {
    cat <<EOF
PrivateTmp=yes
TemporaryFileSystem=/mnt:ro
BindPaths=/mnt/wsl
InaccessiblePaths=/init /run/WSL -/tmp/.X11-unix
EOF
    if $PROTECT_HOME; then
        cat <<EOF
ProtectHome=tmpfs
BindPaths=$RUNNER_HOME
ProtectSystem=full
PrivateIPC=yes
TemporaryFileSystem=/dev/shm:mode=1777,nosuid,nodev
EOF
    fi
    for name in "${mirror_dirs[@]}"; do echo "BindReadOnlyPaths=$DESK_HOME/$name:$RUNNER_HOME/$name"; done
}

render_child() {
    cat <<'EOF'
#!/bin/sh
# The child half of rootless dockerd, run by rootlesskit inside the new user, mount and network
# namespaces (the same two lines as moby's contrib/dockerd-rootless.sh child branch): drop the
# parent's /run entries the daemon must own in its own namespace, then become dockerd.
set -e
rm -f /run/docker /run/containerd /run/xtables.lock
# cgroupfs, not the systemd driver dockerd picks on a systemd host: the systemd driver places a
# container under the user's slice, which a system unit with no login session never has (measured
# 2026-09-26: "user-995.slice/cgroup.controllers: no such file"); rootless dockerd then runs with no
# cgroup driver, so container resource limits are not enforced — the fixtures set none.
exec dockerd --host="unix://$XDG_RUNTIME_DIR/docker.sock" --exec-opt native.cgroupdriver=cgroupfs "$@"
EOF
}

render_docker_unit() {
    local why
    case "$INSTANCE" in ci) why="ROADMAP N214" ;; release) why="DN-119.D8" ;; esac
    cat <<EOF
# Written by malf/runner/isolate-runner-wsl.sh ($why). Rootless dockerd for $RUNNER_USER:
# a container's root is $RUNNER_USER's subordinate range, so a bind mount reaches only what
# $RUNNER_USER can, inside the same sandbox as the runner.
[Unit]
Description=Rootless Docker for the self-hosted runner ($RUNNER_USER)
After=network-online.target

[Service]
User=$RUNNER_USER
Group=$RUNNER_USER
Environment=XDG_RUNTIME_DIR=$DOCKER_RUNTIME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RuntimeDirectory=$(basename "$DOCKER_RUNTIME")
RuntimeDirectoryMode=0700
ExecStart=/usr/bin/rootlesskit --state-dir=$DOCKER_RUNTIME/rootlesskit --net=slirp4netns --mtu=65520 --slirp4netns-sandbox=auto --slirp4netns-seccomp=auto --disable-host-loopback --port-driver=builtin --copy-up=/etc --copy-up=/run --propagation=rslave $CHILD
ExecStartPost=/usr/bin/timeout 60 /bin/sh -c 'until [ -S $DOCKER_SOCK ]; do sleep 1; done'
Delegate=yes
KillMode=mixed
Restart=on-failure
RestartSec=10
$(sandbox_lines)

[Install]
WantedBy=multi-user.target
EOF
}

render_runner_unit() {
    case "$INSTANCE" in
        ci) echo "# Written by malf/runner/isolate-runner-wsl.sh (ROADMAP N214); the original is $STATE_DIR/unit.orig." ;;
        release) echo "# Written by malf/runner/isolate-runner-wsl.sh --instance release (DN-119.D8); no unit preceded it." ;;
    esac
    cat <<EOF
[Unit]
Description=GitHub Actions Runner ($ORG.$RUNNER_NAME) as $RUNNER_USER
After=network-online.target $DOCKER_UNIT
Wants=$DOCKER_UNIT

[Service]
ExecStart=$NEW_DIR/runsvc.sh
User=$RUNNER_USER
Group=$RUNNER_USER
WorkingDirectory=$NEW_DIR
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=5min
NoNewPrivileges=yes
$(sandbox_lines)

[Install]
WantedBy=multi-user.target
EOF
}

# drift <label> <installed path> <render function> — prints whether the installed file equals the
# rendering, and the diff when it does not. Reads only.
drift() {
    local diffout
    if [[ ! -e "$2" ]]; then
        printf '  %-20s absent — the apply writes it\n' "$1"
    elif diffout="$(diff -u --label "installed $2" --label "rendered by this run" "$2" <("$3"))"; then
        printf '  %-20s identical to %s — the apply changes nothing there\n' "$1" "$2"
    else
        printf '  %-20s DIFFERS from %s — the apply writes:\n      %s\n' "$1" "$2" "${diffout//$'\n'/$'\n'      }"
    fi
}

# ─── The plan ───────────────────────────────────────────────────────────────────────────────────

case "$INSTANCE" in
    ci) lifecycle="$OLD_DIR -> $NEW_DIR (rename, same filesystem; registration kept, no re-register)" ;;
    release) lifecycle="$NEW_DIR: the latest actions/runner, SHA-256 verified, registered as $RUNNER_NAME with ONLY the label $RELEASE_GROUP, in group $RELEASE_GROUP (id $RELEASE_GROUP_ID, admits: ${group_repos:-none}); the registration token is minted by the desk's gh and reaches config.sh through stdin and its environment, never an argv" ;;
esac
[[ "$STATE" == applied ]] && lifecycle="in place since $(manifest_of "$STATE_DIR" APPLIED) — nothing moved or registered again"
cat <<EOF

[isolate $INSTANCE] state: $STATE
  desk account        $DESK_USER (uid $DESK_UID, home $DESK_HOME, mode $home_mode)
  runner unit         $UNIT
  runner account      $RUNNER_USER (system uid, no password, shell /usr/sbin/nologin, home $RUNNER_HOME mode $HOME_MODE$($DESK_READS_HOME && echo ", ACL: $DESK_USER may read"))
  runner directory    $lifecycle
  packages to install ${missing[*]:-none}
  ship compiler       $GCC_PREFIX -> root:root, no group/other write
  Python venv         $RUNNER_HOME/venv, the exact pins of $DESK_VENV
  sandbox             TemporaryFileSystem=/mnt (hides drvfs: $drvfs_mounts and /mnt/wslg), /mnt/wsl bound
                      back (bank + resolv.conf), /init and /run/WSL inaccessible (no interop), private /tmp,
                      NoNewPrivileges on the runner$($PROTECT_HOME && printf '\n                      every other home hidden (ProtectHome=tmpfs, %s bound back), /usr /boot /etc\n                      read-only, private /dev/shm and IPC namespace' "$RUNNER_HOME")
  Docker              rootless dockerd for $RUNNER_USER ($DOCKER_UNIT, socket $DOCKER_SOCK), same sandbox;
                      $RUNNER_USER is NOT in the docker group
  bank                $BANK_ROOT: read for every root, write for ${BANK_WRITABLE[*]:-none} (ACL, not world-writable)
  home mirror         links: ${mirror_links[*]:-none}
                      read-only binds: ${mirror_dirs[*]:-none}
  build slot          $SLOT_ROOT, recomputed over every instance (runner-instances.sh, slot_holds)
EOF
echo
echo "[isolate $INSTANCE] drift — what the apply would write, against what is installed:"
drift "runner unit" "$UNIT_FILE" render_runner_unit
drift "docker unit" "/etc/systemd/system/$DOCKER_UNIT" render_docker_unit
drift "dockerd child" "$CHILD" render_child
echo "  slot root ACL       now: $(getfacl -cp "$SLOT_ROOT" 2>/dev/null | grep '^user:' | tr '\n' ' ')"

$APPLY || { printf '\n[isolate %s] PLAN ONLY — nothing was changed. Re-run with --apply to do it.\n' "$INSTANCE"; exit 0; }

# ─── Apply ──────────────────────────────────────────────────────────────────────────────────────

step "1/10 packages: ${missing[*]:-none missing}"
if ((${#missing[@]})); then
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
fi

step "2/10 ship compiler: $GCC_PREFIX owned by root, writable by root alone"
if [[ -d "$GCC_PREFIX" ]]; then
    chown -R root:root "$GCC_PREFIX"
    chmod -R u+rwX,go+rX,go-w "$GCC_PREFIX"
    left="$(find "$GCC_PREFIX" \( -not -user root -o \( -perm -o+w -not -type l \) -o \( -perm -g+w -not -type l \) \) | wc -l)"
    [[ "$left" == 0 ]] || die "$left entries under $GCC_PREFIX are still not root-owned or still group/other-writable"
    say "$GCC_PREFIX: every entry root-owned, none group/other-writable"
else
    say "$GCC_PREFIX absent — nothing to repair (setup-gcc provisions it root-owned on first use)"
fi

step "3/10 runner account $RUNNER_USER"
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir "$RUNNER_HOME" --shell /usr/sbin/nologin \
            --comment "CodeRoast self-hosted runner $RUNNER_NAME (malf/runner/runner-instances.sh)" "$RUNNER_USER"
fi
passwd -l "$RUNNER_USER" >/dev/null
loginctl disable-linger "$RUNNER_USER"
if ! grep -q "^$RUNNER_USER:" /etc/subuid; then
    s="$(subid_start /etc/subuid)"; usermod --add-subuids "$s-$((s + SUBID_COUNT - 1))" "$RUNNER_USER"
fi
if ! grep -q "^$RUNNER_USER:" /etc/subgid; then
    s="$(subid_start /etc/subgid)"; usermod --add-subgids "$s-$((s + SUBID_COUNT - 1))" "$RUNNER_USER"
fi
chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME"
chmod "$HOME_MODE" "$RUNNER_HOME"
# Mount points for the read-only binds of the desk's real corpus directories, made before either
# unit carrying the sandbox starts; root-owned, so the runner cannot swap one for a symlink.
for name in "${mirror_dirs[@]}"; do install -d -m 0755 -o root -g root "$RUNNER_HOME/$name"; done
if $DESK_READS_HOME; then
    # The desk reads the runner's work tree and logs (a failed leg is settled offline from them); the
    # direction of that grant is safe — the desk already owns everything the runner could hold.
    setfacl -m "u:$DESK_USER:rx" "$RUNNER_HOME"
else
    setfacl -b "$RUNNER_HOME"
fi
# The persistent conan home's directory (setup-build-env's conan-home.sh, DN-119.D2), made here so the
# boundary proof can test it before the first step 0 creates anything in it; mode 700, the runner's.
if [[ "$INSTANCE" == release ]]; then
    for d in .cache .cache/coderoast-build .cache/coderoast-build/conan ".cache/coderoast-build/conan/$RUNNER_NAME"; do
        install -d -m 0700 -o "$RUNNER_USER" -g "$RUNNER_USER" "$RUNNER_HOME/$d"
    done
fi
say "$RUNNER_USER: uid $(id -u "$RUNNER_USER"), groups '$(id -nG "$RUNNER_USER")', home $(stat -c %a "$RUNNER_HOME"), subuid $(grep "^$RUNNER_USER:" /etc/subuid | cut -d: -f2,3)"

step "4/10 Python venv $RUNNER_HOME/venv pinned to $DESK_VENV"
if [[ ! -x "$RUNNER_HOME/venv/bin/python" ]]; then
    sudo -u "$RUNNER_USER" -H python3 -m venv "$RUNNER_HOME/venv"
fi
reqs="$(mktemp)"
# shellcheck disable=SC2024  # the redirect is root writing its own temp file, by intent
sudo -u "$DESK_USER" "$DESK_VENV/bin/python" -m pip freeze --exclude-editable > "$reqs"
chmod 0644 "$reqs"
sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/python" -m pip install --disable-pip-version-check --quiet -r "$reqs"
rm -f "$reqs"
say "venv: $(sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/python" -m pip freeze | wc -l) packages; cmake $(sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/cmake" --version | head -n1 | awk '{print $3}')"

step "5/10 rootless Docker for $RUNNER_USER, proven BEFORE the runner is touched"
install -d -m 0755 "$(dirname "$CHILD")"
render_child > "$CHILD"
chmod 0755 "$CHILD"
render_docker_unit > "/etc/systemd/system/$DOCKER_UNIT"
systemctl daemon-reload
systemctl enable "$DOCKER_UNIT"
systemctl restart "$DOCKER_UNIT"
rdocker() { sudo -u "$RUNNER_USER" -H env DOCKER_HOST="unix://$DOCKER_SOCK" docker "$@"; }
rdocker info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless' \
    || die "dockerd for $RUNNER_USER is up but does not report rootless mode. The runner is untouched. Undo: $UNDO"
rdocker pull -q "$SMOKE_IMAGE" >/dev/null
cid="$(rdocker run -d --publish 127.0.0.1::6379 "$SMOKE_IMAGE")"
port="$(rdocker port "$cid" 6379/tcp | head -n1 | sed 's/.*://')"
reached=REFUSED
for _ in $(seq 20); do
    if timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && printf 'PING\r\n' >&3 && head -c 5 <&3" 2>/dev/null | grep -q PONG; then reached=PONG; break; fi
    sleep 0.5
done
rdocker rm -f "$cid" >/dev/null
[[ "$reached" == PONG ]] || die "a container published on 127.0.0.1:$port did not answer — the fixtures' shape does not work on rootless Docker here. The runner is untouched. Undo: $UNDO"
say "rootless Docker: container reached on 127.0.0.1:$port (the fixtures' shape)"
# The control first: a container CAN read through a bind mount the runner may read, so a
# REFUSED below is the sandbox answering and not an image that cannot run `cat`.
got="$(rdocker run --rm -v "$RUNNER_HOME/venv:/probe:ro" "$SMOKE_IMAGE" sh -c 'cat /probe/pyvenv.cfg >/dev/null && echo READ')" || got=FAILED
[[ "$got" == READ ]] || die "control failed: a container could not read $RUNNER_HOME/venv/pyvenv.cfg through a bind mount ($got) — the refusals below would prove nothing"
# Each source is one door: the desk home, a drvfs drive, the root (every path at once), and each
# other runner's home (its registration credentials). A `docker run` that fails outright — the
# daemon cannot even stat the source — is a refusal too.
creds="/probe/.config/gh/hosts.yml /probe/.ssh/id_* /probe/Users/*/.ssh/id_* /probe/home/$DESK_USER/.config/gh/hosts.yml /probe/mnt/c/Users/*/.ssh/id_*"
sources=("$DESK_HOME" /mnt/c /)
for other in "${INSTANCES[@]}"; do
    [[ "$other" == "$INSTANCE" ]] && continue
    other_home="$(instance_select "$other" && echo "$RUNNER_HOME")"
    other_dir="$(instance_select "$other" && echo "$RUNNER_DIRNAME")"
    sources+=("$other_home")
    creds+=" /probe/$other_dir/.credentials /probe${other_home}/$other_dir/.credentials"
done
for src in "${sources[@]}"; do
    got="$(rdocker run --rm -v "$src:/probe:ro" "$SMOKE_IMAGE" sh -c \
        "for f in $creds; do [ -r \"\$f\" ] && cat \"\$f\" >/dev/null 2>&1 && { echo READ; exit 0; }; done; echo REFUSED")" || got=REFUSED
    [[ "$got" == REFUSED ]] || die "a container bind-mounting $src READ a credential — the sandbox does not reach dockerd. Undo: $UNDO"
    say "rootless Docker: -v $src -> every credential REFUSED"
done

case "$INSTANCE:$STATE" in
    ci:fresh)
        step "6/10 stop the runner and record the rollback manifest"
        install -d -m 0700 "$STATE_DIR"
        cp -a "$UNIT_FILE" "$STATE_DIR/unit.orig"
        cp -a "$OLD_DIR/.path" "$STATE_DIR/path.orig"
        cp -a "$OLD_DIR/.env" "$STATE_DIR/env.orig" 2>/dev/null || : > "$STATE_DIR/env.orig"
        printf 'DESK_USER=%s\nOLD_DIR=%s\nDESK_VENV=%s\nAPPLIED=%s\n' "$DESK_USER" "$OLD_DIR" "$DESK_VENV" "$(date -Is)" > "$STATE_DIR/manifest"
        systemctl stop "$UNIT"
        say "runner stopped; rollback manifest in $STATE_DIR"

        step "7/10 move $OLD_DIR -> $NEW_DIR"
        mv "$OLD_DIR" "$NEW_DIR"
        # The runner's self-update writes bin/ and externals/ as ABSOLUTE links into its own directory.
        for link in bin externals; do
            t="$(readlink "$NEW_DIR/$link")"
            [[ "$t" == "$OLD_DIR"/* ]] && ln -sfn "${t#"$OLD_DIR"/}" "$NEW_DIR/$link"
        done
        # The tool cache holds interpreters with absolute paths into the old directory; it is a pure
        # cache (actions/setup-python and setup-buildx re-download on demand).
        rm -rf "$NEW_DIR/_work/_tool"
        ;;
    release:fresh)
        step "6/10 record the rollback manifest"
        install -d -m 0700 "$STATE_DIR"
        # The one variable the ci runner's .env carries besides the two this script states (read
        # 2026-09-29): the locale every job inherits.
        echo "LANG=C.UTF-8" > "$STATE_DIR/env.orig"
        printf 'DESK_USER=%s\nDESK_VENV=%s\nAPPLIED=%s\n' "$DESK_USER" "$DESK_VENV" "$(date -Is)" > "$STATE_DIR/manifest"

        step "7/10 download and register $RUNNER_NAME as $RUNNER_USER (group $RELEASE_GROUP, label $RELEASE_GROUP only)"
        if [[ -f "$NEW_DIR/.runner" ]]; then
            say "$NEW_DIR/.runner exists — registered by an earlier run; not registering again"
        else
            install -d -m 0700 -o "$RUNNER_USER" -g "$RUNNER_USER" "$NEW_DIR"
            token="$(sudo -u "$DESK_USER" -H gh api -X POST "orgs/$ORG/actions/runners/registration-token" --jq .token)" \
                || die "the desk's gh could not mint a registration token for $ORG. Nothing is registered. Undo: $UNDO"
            # The installer runs AS the runner account, which cannot read the desk's checkout: a
            # root-owned copy in a directory only root writes, run from the runner's own directory.
            # The token goes through stdin into the installer's environment: no argv carries it.
            installer="$(mktemp -d)"
            chmod 0755 "$installer"
            install -m 0644 "$HERE/install-runner.sh" "$installer/install-runner.sh"
            reg_rc=0
            (cd "$NEW_DIR" && sudo -u "$RUNNER_USER" -H env ORG="$ORG" RUNNER_NAME="$RUNNER_NAME" RUNNER_DIR="$NEW_DIR" \
                LABELS="$RELEASE_GROUP" RUNNER_GROUP="$RELEASE_GROUP" NO_DEFAULT_LABELS=true RUNNER_TOKEN_STDIN=true \
                bash "$installer/install-runner.sh" <<< "$token") || reg_rc=$?
            unset token
            rm -rf "$installer"
            (( reg_rc == 0 )) || die "the runner's registration failed (exit $reg_rc, above). Undo: $UNDO"
        fi
        ;;
    *:applied)
        step "6/10 and 7/10 — already in place; stopping the runner to re-apply its unit"
        systemctl stop "$UNIT"
        ;;
esac

# PATH: nothing from the desk, nothing from another runner. The runner's own venv first
# (conan-io/setup-conan runs `python -m pip`, so `python` must be a venv the runner owns), then the system.
printf '%s\n' "$RUNNER_HOME/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" > "$NEW_DIR/.path"
# .env reaches every job through Runner.Listener. MALF_BUILD_SLOT_DIR is stated here, not left to
# malf's default, because CI runs the malf-toolchain its workflows PIN, and a pin older than the
# shared-root default would resolve /tmp — private to this unit — and never see a desk holder.
{
    grep -vE '^(DOCKER_HOST|MALF_BUILD_SLOT_DIR)=' "$STATE_DIR/env.orig" || true
    echo "DOCKER_HOST=unix://$DOCKER_SOCK"
    echo "MALF_BUILD_SLOT_DIR=$SLOT_ROOT/slot"
} > "$NEW_DIR/.env"
chown -R "$RUNNER_USER:$RUNNER_USER" "$NEW_DIR"
if $DESK_READS_HOME; then
    for d in _work _diag; do
        [[ -d "$NEW_DIR/$d" ]] || continue
        setfacl -R -m "u:$DESK_USER:rX" "$NEW_DIR/$d"
        find "$NEW_DIR/$d" -type d -exec setfacl -d -m "u:$DESK_USER:rX" {} +
    done
    setfacl -m "u:$DESK_USER:rx" "$NEW_DIR"
else
    setfacl -R -b "$NEW_DIR"
    chmod -R go-rwx "$NEW_DIR"
fi

step "8/10 bank and home mirror"
for root in "${BANK_WRITABLE[@]}"; do
    setfacl -R -m "u:$RUNNER_USER:rwX,u:$DESK_USER:rwX" "$BANK_ROOT/$root"
    # A default ACL on every directory: what the runner creates stays writable by the desk (a desk
    # prune), and what the desk creates stays writable by the runner, whatever either one's umask.
    find "$BANK_ROOT/$root" -type d -exec setfacl -d -m "u:$RUNNER_USER:rwX,u:$DESK_USER:rwX" {} +
    say "bank $BANK_ROOT/$root: rw for $RUNNER_USER and $DESK_USER"
done
for pair in "${mirror_links[@]}"; do
    ln -sfn "${pair#*=}" "$RUNNER_HOME/${pair%%=*}"
    chown -h "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/${pair%%=*}"
done
for name in "${mirror_dirs[@]}"; do
    setfacl -R -m "u:$RUNNER_USER:rX" "$DESK_HOME/$name"
    find "$DESK_HOME/$name" -type d -exec setfacl -d -m "u:$RUNNER_USER:rX" {} +
done
say "home mirror: ${#mirror_links[@]} link(s), ${#mirror_dirs[@]} read-only bind(s) in $RUNNER_HOME"

step "9/10 build slot root $SLOT_ROOT"
# The unit is written first when this is the instance's first apply: slot_holds reads which
# instances are present from their unit files.
if [[ ! -f "$UNIT_FILE" ]]; then render_runner_unit > "$UNIT_FILE"; fi
say "slot root grants: $(apply_slot_acl "$DESK_USER")"
say "malf resolves the slot to $SLOT_ROOT/slot for every account (malf/malf, MALF_BUILD_SLOT_SHARED_ROOT)"

step "10/10 runner unit as $RUNNER_USER, sandboxed; start and wait for 'Listening for Jobs'"
render_runner_unit > "$UNIT_FILE"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
started="$(date +%s)"
systemctl start "$UNIT"
listening=""
for _ in $(seq 90); do
    log="$(ls -t "$NEW_DIR"/_diag/Runner_*.log 2>/dev/null | head -n1 || true)"
    if [[ -n "$log" ]] && (( $(stat -c %Y "$log") >= started )) && grep -q 'Listening for Jobs' "$log"; then listening="$log"; break; fi
    sleep 1
done
[[ -n "$listening" ]] || die "the runner did not reach 'Listening for Jobs' within 90 s — read $NEW_DIR/_diag and 'journalctl -u $UNIT'. Undo: $UNDO"
[[ "$(ps -o user= -p "$(systemctl show -p MainPID --value "$UNIT")")" == "$RUNNER_USER" ]] || die "the runner's main process is not $RUNNER_USER"

if [[ "$INSTANCE" == release ]]; then
    # GitHub's record, not the runner's own file: the group the organisation lists it in, and its labels.
    seen="$(sudo -u "$DESK_USER" -H gh api "orgs/$ORG/actions/runner-groups/$RELEASE_GROUP_ID/runners" \
            --jq ".runners[] | select(.name == \"$RUNNER_NAME\") | [.labels[].name] | sort | join(\",\")")" \
        || die "the desk's gh could not read group $RELEASE_GROUP's runners"
    [[ "$seen" == "$RELEASE_GROUP" ]] \
        || die "GitHub lists $RUNNER_NAME in group $RELEASE_GROUP with labels '${seen:-<not listed>}', not exactly '$RELEASE_GROUP'. Undo: $UNDO"
    say "GitHub lists $RUNNER_NAME in group $RELEASE_GROUP (id $RELEASE_GROUP_ID) with the one label $seen"
fi

step "the boundary proof"
prove_boundary || die "the boundary does NOT hold (a BAD row above) — the runner is up; stop it ('systemctl stop $UNIT') before any job reaches it. Undo: $UNDO"

cat <<EOF

[isolate $INSTANCE] DONE — $RUNNER_NAME listens as $RUNNER_USER ($listening).
  Check, as $DESK_USER:
    malf slot status                              # must print: malf slot: dir $SLOT_ROOT/slot
    gh api /orgs/$ORG/actions/runners -q '.runners[] | "\(.name) \(.status) \([.labels[].name])"'
  Undo: $UNDO
EOF
