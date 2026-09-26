#!/usr/bin/env bash
# isolate-runner-wsl.sh — move the WSL self-hosted runner (malf-runner) off the desk account onto
# a dedicated low-privilege account, so that no CI step can read a desk credential (ROADMAP N214).
#
#   sudo bash malf/runner/isolate-runner-wsl.sh            # PLAN: preflight + what it would do; changes nothing
#   sudo bash malf/runner/isolate-runner-wsl.sh --apply    # do it
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh   # undo it
#
# WHAT A JOB COULD READ BEFORE THIS, measured 2026-09-26 on this box, each one a separate door:
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
# WHAT STAYS, stated so nobody reads more into this: localhost TCP services on this box (ollama,
# the desk's dev servers) remain reachable from a job — a network namespace would break the
# container fixtures, which publish on 127.0.0.1; and an AF_VSOCK path to the Windows host that
# bypasses /init is not closable by any configuration here.
#
# IDEMPOTENT. A second run on an applied box re-derives and re-applies every permission, mirror
# and unit, and moves nothing. REFUSES, changing nothing, on anything it did not expect: a job
# running, a desk lane holding the /tmp build slot, a runner in neither the before nor the after
# shape, an account of the runner's name that this script did not make.
set -euo pipefail

UNIT="actions.runner.CodeRoasted.malf-runner.service"
RUNNER_USER="ghrunner"
RUNNER_HOME="/home/$RUNNER_USER"
NEW_DIR="$RUNNER_HOME/actions-runner-malf"
DOCKER_UNIT="coderoast-runner-docker.service"
DOCKER_RUNTIME="/run/ghrunner-docker"
DOCKER_SOCK="$DOCKER_RUNTIME/docker.sock"
CHILD="/usr/local/libexec/coderoast/dockerd-rootless-child.sh"
# The build slot's shared root. malf resolves its slot here whenever this directory exists
# (malf/malf, MALF_BUILD_SLOT_SHARED_ROOT) — the one machine fact both accounts read.
SLOT_ROOT="/var/lib/coderoast-build"
OLD_SLOT="/tmp/coderoast-build-slot"
BANK_ROOT="/mnt/wsl/corpora"
# The bank roots a CI job WRITES. corpus-longitudinal.yml writes CORPUS_LOGS_DIR and nothing else
# writes the bank from CI; every other root is read-only to the runner, so a job that starts
# writing one reds on EACCES, loudly, rather than being granted in advance.
BANK_WRITABLE=(corpora-longitudinal-logs)
GCC_PREFIX="/opt/gcc-16.2"
STATE_DIR="/var/lib/coderoast-runner-isolation"
PACKAGES=(acl uidmap rootlesskit slirp4netns docker-buildx)
# Public mirror (no Docker Hub pull), the same digest coderoast-server's redis fixture pins.
SMOKE_IMAGE="ghcr.io/coderoasted/mirror/library/redis:7-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
SUBID_COUNT=65536

APPLY=false
case "${1:-}" in
    "") ;;
    --apply) APPLY=true ;;
    *) echo "usage: sudo bash $0 [--apply]" >&2; exit 2 ;;
esac

say()  { printf '\033[1;34m[isolate]\033[0m %s\n' "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[isolate] REFUSED:\033[0m %s\n' "$*" >&2; exit 1; }

# ─── Preflight — reads only ──────────────────────────────────────────────────────────────────────

[[ "$(ps -o comm= -p 1)" == systemd ]] || die "PID 1 is not systemd — this box's runner is a systemd unit; /etc/wsl.conf needs [boot] systemd=true"
if $APPLY; then [[ $EUID -eq 0 ]] || die "--apply needs root: sudo bash $0 --apply"; fi

UNIT_FILE="/etc/systemd/system/$UNIT"
[[ -f "$UNIT_FILE" ]] || die "no unit file at $UNIT_FILE — install the runner as a service first (malf/runner/README.md)"
unit_key() { sed -n "s/^$1=//p" "$UNIT_FILE" | head -n1; }
UNIT_USER="$(unit_key User)"
UNIT_WD="$(unit_key WorkingDirectory)"

if [[ "$UNIT_USER" == "$RUNNER_USER" ]]; then
    STATE=applied
    [[ "$UNIT_WD" == "$NEW_DIR" ]] || die "the unit runs as $RUNNER_USER but from '$UNIT_WD', not $NEW_DIR — a shape this script never writes"
    [[ -f "$STATE_DIR/manifest" ]] || die "the unit runs as $RUNNER_USER but $STATE_DIR/manifest is absent — not this script's work"
    DESK_USER="$(sed -n 's/^DESK_USER=//p' "$STATE_DIR/manifest")"
    OLD_DIR="$(sed -n 's/^OLD_DIR=//p' "$STATE_DIR/manifest")"
else
    STATE=fresh
    DESK_USER="$UNIT_USER"
    OLD_DIR="$UNIT_WD"
    [[ -n "$DESK_USER" && "$DESK_USER" != root ]] || die "the unit's User= is '${DESK_USER:-<unset>}' — expected the desk account"
    [[ "$(unit_key ExecStart)" == "$OLD_DIR/runsvc.sh" ]] || die "ExecStart is '$(unit_key ExecStart)', not $OLD_DIR/runsvc.sh"
    [[ -f "$OLD_DIR/.runner" && -x "$OLD_DIR/runsvc.sh" ]] || die "$OLD_DIR is not a configured runner (.runner or runsvc.sh missing)"
    [[ ! -e "$NEW_DIR" ]] || die "$NEW_DIR already exists — refusing to move a runner on top of it"
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

# No job may be running: the move stops the runner, and a job killed mid-step is a red with no cause.
if systemctl is-active --quiet "$UNIT"; then
    cg="$(systemctl show -p ControlGroup --value "$UNIT")"
    if [[ -n "$cg" && -r "/sys/fs/cgroup$cg/cgroup.procs" ]]; then
        while read -r pid; do
            [[ "$(cat "/proc/$pid/comm" 2>/dev/null)" == Runner.Worker ]] \
                && die "a job is running on the runner (Runner.Worker pid $pid) — let it finish, then re-run"
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
    for g in sudo docker adm admin wheel "$DESK_GROUP"; do
        id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qx "$g" && die "account $RUNNER_USER is in group '$g' — that membership is the hole this script closes"
    done
fi

# Moving by rename keeps the runner's registration and its work tree; a cross-device move would be
# a 12 GB copy, so it is refused rather than attempted.
if [[ "$STATE" == fresh ]]; then
    [[ "$(stat -c %d "$(dirname "$OLD_DIR")")" == "$(stat -c %d /home)" ]] \
        || die "$OLD_DIR and /home are on different filesystems — a rename cannot move it"
    old_venv=""
    while IFS= read -r entry; do
        [[ -f "$(dirname "$entry")/pyvenv.cfg" ]] && { old_venv="$(dirname "$entry")"; break; }
    done < <(tr ':' '\n' < "$OLD_DIR/.path")
    [[ -n "$old_venv" ]] || die "no Python venv on the runner's current PATH ($OLD_DIR/.path) — the parity baseline for the new venv is gone; this box is not in the measured shape"
else
    old_venv="$(sed -n 's/^DESK_VENV=//p' "$STATE_DIR/manifest")"
fi

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

for bin in dockerd containerd runc iptables python3; do
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

# A subordinate id range for rootless Docker that overlaps nobody's.
subid_start() {   # <file> -> the first id past every existing range
    awk -F: 'BEGIN{m=100000} NF==3{e=$2+$3; if(e>m)m=e} END{print m}' "$1"
}

drvfs_mounts="$(awk '$3=="9p" && $4 ~ /aname=drvfs/ {print $2}' /proc/mounts | tr '\n' ' ')"

# ─── The plan ───────────────────────────────────────────────────────────────────────────────────

cat <<EOF

[isolate] state: $STATE
  desk account        $DESK_USER (uid $DESK_UID, home $DESK_HOME, mode $home_mode)
  runner unit         $UNIT  (User=$UNIT_USER, WorkingDirectory=$UNIT_WD)
  runner account      $RUNNER_USER (system uid, no password, shell /usr/sbin/nologin, home $RUNNER_HOME)
  runner directory    $OLD_DIR -> $NEW_DIR (rename, same filesystem; registration kept, no re-register)
  packages to install ${missing[*]:-none}
  ship compiler       $GCC_PREFIX -> root:root, no group/other write
  Python venv         $RUNNER_HOME/venv, the exact pins of $old_venv (what CI resolved until now)
  sandbox             TemporaryFileSystem=/mnt (hides drvfs: $drvfs_mounts and /mnt/wslg), /mnt/wsl bound
                      back (bank + resolv.conf), /init and /run/WSL inaccessible (no interop), private /tmp,
                      NoNewPrivileges on the runner
  Docker              rootless dockerd for $RUNNER_USER ($DOCKER_UNIT, socket $DOCKER_SOCK), same sandbox;
                      $RUNNER_USER is NOT in the docker group
  bank                $BANK_ROOT: read for every root, write for ${BANK_WRITABLE[*]} (ACL, not world-writable)
  home mirror         links: ${mirror_links[*]:-none}
                      read-only binds: ${mirror_dirs[*]:-none}
  build slot          $SLOT_ROOT (ACL rw for $DESK_USER and $RUNNER_USER) — malf's slot moves there
EOF

$APPLY || { printf '\n[isolate] PLAN ONLY — nothing was changed. Re-run with --apply to do it.\n'; exit 0; }

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
            --comment "CodeRoast self-hosted runner (ROADMAP N214)" "$RUNNER_USER"
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
chmod 0750 "$RUNNER_HOME"
# Mount points for the read-only binds of the desk's real corpus directories, made before either
# unit carrying the sandbox starts; root-owned, so the runner cannot swap one for a symlink.
for name in "${mirror_dirs[@]}"; do install -d -m 0755 -o root -g root "$RUNNER_HOME/$name"; done
# The desk reads the runner's work tree and logs (a failed leg is settled offline from them); the
# direction of that grant is safe — the desk already owns everything the runner could hold.
setfacl -m "u:$DESK_USER:rx" "$RUNNER_HOME"
say "$RUNNER_USER: uid $(id -u "$RUNNER_USER"), groups '$(id -nG "$RUNNER_USER")', subuid $(grep "^$RUNNER_USER:" /etc/subuid | cut -d: -f2,3)"

step "4/10 Python venv $RUNNER_HOME/venv pinned to $old_venv"
if [[ ! -x "$RUNNER_HOME/venv/bin/python" ]]; then
    sudo -u "$RUNNER_USER" -H python3 -m venv "$RUNNER_HOME/venv"
fi
reqs="$(mktemp)"
# shellcheck disable=SC2024  # the redirect is root writing its own temp file, by intent
sudo -u "$DESK_USER" "$old_venv/bin/python" -m pip freeze --exclude-editable > "$reqs"
chmod 0644 "$reqs"
sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/python" -m pip install --disable-pip-version-check --quiet -r "$reqs"
rm -f "$reqs"
say "venv: $(sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/python" -m pip freeze | wc -l) packages; cmake $(sudo -u "$RUNNER_USER" -H "$RUNNER_HOME/venv/bin/cmake" --version | head -n1 | awk '{print $3}')"

# The sandbox both units carry. ONE definition, printed into each unit, so the runner and the
# daemon that runs its containers cannot drift apart: a container's bind mount is resolved in
# dockerd's namespace, so a daemon outside the sandbox would reopen every door it closes.
sandbox_lines() {   # <the other unit>
    cat <<EOF
PrivateTmp=yes
JoinsNamespaceOf=$1
TemporaryFileSystem=/mnt:ro
BindPaths=/mnt/wsl
InaccessiblePaths=/init /run/WSL -/tmp/.X11-unix
EOF
    for name in "${mirror_dirs[@]}"; do echo "BindReadOnlyPaths=$DESK_HOME/$name:$RUNNER_HOME/$name"; done
}

step "5/10 rootless Docker for $RUNNER_USER, proven BEFORE the runner is touched"
install -d -m 0755 "$(dirname "$CHILD")"
cat > "$CHILD" <<'EOF'
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
chmod 0755 "$CHILD"
cat > "/etc/systemd/system/$DOCKER_UNIT" <<EOF
# Written by malf/runner/isolate-runner-wsl.sh (ROADMAP N214). Rootless dockerd for $RUNNER_USER:
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
$(sandbox_lines "$UNIT")

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$DOCKER_UNIT"
systemctl restart "$DOCKER_UNIT"
rdocker() { sudo -u "$RUNNER_USER" -H env DOCKER_HOST="unix://$DOCKER_SOCK" docker "$@"; }
rdocker info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless' \
    || die "dockerd for $RUNNER_USER is up but does not report rootless mode. The runner is untouched. Undo: sudo bash $(dirname "$0")/isolate-runner-wsl-rollback.sh"
rdocker pull -q "$SMOKE_IMAGE" >/dev/null
cid="$(rdocker run -d --publish 127.0.0.1::6379 "$SMOKE_IMAGE")"
port="$(rdocker port "$cid" 6379/tcp | head -n1 | sed 's/.*://')"
reached=REFUSED
for _ in $(seq 20); do
    if timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && printf 'PING\r\n' >&3 && head -c 5 <&3" 2>/dev/null | grep -q PONG; then reached=PONG; break; fi
    sleep 0.5
done
rdocker rm -f "$cid" >/dev/null
[[ "$reached" == PONG ]] || die "a container published on 127.0.0.1:$port did not answer — the fixtures' shape does not work on rootless Docker here. The runner is untouched. Undo: sudo bash $(dirname "$0")/isolate-runner-wsl-rollback.sh"
say "rootless Docker: container reached on 127.0.0.1:$port (the fixtures' shape)"
# The control first: a container CAN read through a bind mount the runner may read, so a
# REFUSED below is the sandbox answering and not an image that cannot run `cat`.
got="$(rdocker run --rm -v "$RUNNER_HOME/venv:/probe:ro" "$SMOKE_IMAGE" sh -c 'cat /probe/pyvenv.cfg >/dev/null && echo READ')" || got=FAILED
[[ "$got" == READ ]] || die "control failed: a container could not read $RUNNER_HOME/venv/pyvenv.cfg through a bind mount ($got) — the refusals below would prove nothing"
# Each source is one door: the desk home, a drvfs drive, and the root (every path at once). A
# `docker run` that fails outright — the daemon cannot even stat the source — is a refusal too.
creds="/probe/.config/gh/hosts.yml /probe/.ssh/id_* /probe/Users/*/.ssh/id_* /probe/home/$DESK_USER/.config/gh/hosts.yml /probe/mnt/c/Users/*/.ssh/id_*"
for src in "$DESK_HOME" /mnt/c /; do
    got="$(rdocker run --rm -v "$src:/probe:ro" "$SMOKE_IMAGE" sh -c \
        "for f in $creds; do [ -r \"\$f\" ] && cat \"\$f\" >/dev/null 2>&1 && { echo READ; exit 0; }; done; echo REFUSED")" || got=REFUSED
    [[ "$got" == REFUSED ]] || die "a container bind-mounting $src READ a desk credential — the sandbox does not reach dockerd. Undo: sudo bash $(dirname "$0")/isolate-runner-wsl-rollback.sh"
    say "rootless Docker: -v $src -> every desk credential REFUSED"
done

if [[ "$STATE" == fresh ]]; then
    step "6/10 stop the runner and record the rollback manifest"
    install -d -m 0700 "$STATE_DIR"
    cp -a "$UNIT_FILE" "$STATE_DIR/unit.orig"
    cp -a "$OLD_DIR/.path" "$STATE_DIR/path.orig"
    cp -a "$OLD_DIR/.env" "$STATE_DIR/env.orig" 2>/dev/null || : > "$STATE_DIR/env.orig"
    printf 'DESK_USER=%s\nOLD_DIR=%s\nDESK_VENV=%s\nAPPLIED=%s\n' "$DESK_USER" "$OLD_DIR" "$old_venv" "$(date -Is)" > "$STATE_DIR/manifest"
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
else
    step "6/10 and 7/10 — already moved; stopping the runner to re-apply its unit"
    systemctl stop "$UNIT"
fi

# PATH: nothing from the desk. The runner's own venv first (conan-io/setup-conan runs
# `python -m pip`, so `python` must be a venv the runner owns), then the system.
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
for d in _work _diag; do
    [[ -d "$NEW_DIR/$d" ]] || continue
    setfacl -R -m "u:$DESK_USER:rX" "$NEW_DIR/$d"
    find "$NEW_DIR/$d" -type d -exec setfacl -d -m "u:$DESK_USER:rX" {} +
done
setfacl -m "u:$DESK_USER:rx" "$NEW_DIR"

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
install -d -m 0770 -o root -g root "$SLOT_ROOT"
setfacl -m "u:$DESK_USER:rwx,u:$RUNNER_USER:rwx" "$SLOT_ROOT"
setfacl -d -m "u::rwX,g::---,o::---,u:$DESK_USER:rwX,u:$RUNNER_USER:rwX,m::rwX" "$SLOT_ROOT"
say "malf now resolves the slot to $SLOT_ROOT/slot for every account (malf/malf, MALF_BUILD_SLOT_SHARED_ROOT)"

step "10/10 runner unit as $RUNNER_USER, sandboxed; start and wait for 'Listening for Jobs'"
cat > "$UNIT_FILE" <<EOF
# Written by malf/runner/isolate-runner-wsl.sh (ROADMAP N214); the original is $STATE_DIR/unit.orig.
[Unit]
Description=GitHub Actions Runner (CodeRoasted.malf-runner) as $RUNNER_USER
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
$(sandbox_lines "$DOCKER_UNIT")

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
started="$(date +%s)"
systemctl start "$UNIT"
listening=""
for _ in $(seq 90); do
    log="$(ls -t "$NEW_DIR"/_diag/Runner_*.log 2>/dev/null | head -n1 || true)"
    if [[ -n "$log" ]] && (( $(stat -c %Y "$log") >= started )) && grep -q 'Listening for Jobs' "$log"; then listening="$log"; break; fi
    sleep 1
done
[[ -n "$listening" ]] || die "the runner did not reach 'Listening for Jobs' within 90 s — read $NEW_DIR/_diag and 'journalctl -u $UNIT'. Undo: sudo bash $(dirname "$0")/isolate-runner-wsl-rollback.sh"
[[ "$(ps -o user= -p "$(systemctl show -p MainPID --value "$UNIT")")" == "$RUNNER_USER" ]] || die "the runner's main process is not $RUNNER_USER"

cat <<EOF

[isolate] DONE — the runner listens as $RUNNER_USER ($listening).
  Check, as $DESK_USER:
    malf slot status                              # must print: malf slot: dir $SLOT_ROOT/slot
    gh api /orgs/CodeRoasted/actions/runners -q '.runners[] | "\(.name) \(.status)"'   # malf-runner online
  Then, once the Windows runner is moved too, the probe (it dispatches ONE run on each runner):
    gh workflow run runner-isolation-probe.yml -R CodeRoasted/coderoast
  Undo: sudo bash $(dirname "$0")/isolate-runner-wsl-rollback.sh
EOF
