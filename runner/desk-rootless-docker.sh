#!/usr/bin/env bash
# desk-rootless-docker.sh — give the desk account a rootless Docker of its own, so it can leave the
# root `docker` group (the Founder, 2026-10-10). Run AS the desk account, never with sudo: every file
# this writes is the account's own, and the daemon it starts runs with the account's privileges.
#
#   bash malf/runner/desk-rootless-docker.sh            # PLAN: renders, diffs, changes nothing
#   bash malf/runner/desk-rootless-docker.sh --apply    # install, start, smoke-test the daemon
#   bash malf/runner/desk-rootless-docker.sh --prove    # the boundary proof only
#
# WHY. The rootful daemon's socket is root:docker 0660, and `docker run -v /:/host` is root on the
# host, so membership of `docker` made every process of the desk account — every agent lane
# included — root without a password: it could read the release runner's store-writer credential
# in ~ghrelease, whatever that home's mode (DN-142.D6, R1). A rootless daemon maps a container's root
# to this account and its subordinate ids, so a bind mount reaches only what the account can read.
#
# WHAT THIS DOES NOT CLOSE, measured 2026-10-10 and printed by --prove as RESIDUAL: WSL interop.
# `wsl.exe -u root` from any process of the desk account answers uid 0 with no password, because
# the Windows user owns the distro. Leaving `docker` closes one root door of the desk, not all.
#
# THE TWO STEPS THAT NEED ROOT are not taken here; README.md § "The desk's Docker" lists them for
# the Founder: removing the account from `docker`, and (recommended) disabling the rootful daemon.
#
# WHAT IT WRITES (all under the account's home; the unit is a USER unit, kept alive by lingering):
#   ~/.local/libexec/coderoast/dockerd-rootless-child.sh   the child half run inside rootlesskit
#   ~/.config/systemd/user/coderoast-desk-docker.service   rootlesskit + dockerd, socket $XDG_RUNTIME_DIR/docker.sock
#   ~/.config/environment.d/50-coderoast-docker.conf       DOCKER_HOST for every user unit
#   a marked block in ~/.bashrc                            DOCKER_HOST for every shell, malf, pharos, ctest
# DOCKER_HOST and NEVER a `docker context`: with both set, the CLI warns on stderr at every call,
# and the infra test fixtures (coderoast-server infra/tests_support) read stdout and stderr as one
# stream, so a warning ahead of a container id reds them.
# The image store starts empty (~/.local/share/docker); the rootful one under /var/lib/docker is not
# migrated. Containers cannot reach the host's loopback (--disable-host-loopback), the same as the
# runner daemons: a published port is reachable FROM the host, the host's services are not from a
# container.
set -euo pipefail

MODE=plan
case "${1:-}" in
    "") ;;
    --apply) MODE=apply ;;
    --prove) MODE=prove ;;
    *) echo "usage: bash $0 [--apply | --prove]" >&2; exit 2 ;;
esac

say()  { printf '\033[1;34m[desk-docker]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[desk-docker] REFUSED:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" != 0 ]] || die "run as the desk account, not as root: a rootless daemon is the account's own"
RUNTIME="/run/user/$(id -u)"
SOCK="$RUNTIME/docker.sock"
UNIT_NAME=coderoast-desk-docker.service
CHILD="$HOME/.local/libexec/coderoast/dockerd-rootless-child.sh"
UNIT="$HOME/.config/systemd/user/$UNIT_NAME"
ENVD="$HOME/.config/environment.d/50-coderoast-docker.conf"
BASHRC="$HOME/.bashrc"
MARK_BEGIN="# >>> coderoast desk rootless docker (malf/runner/desk-rootless-docker.sh) >>>"
MARK_END="# <<< coderoast desk rootless docker <<<"
ROOTFUL_SOCK=/var/run/docker.sock
WRITER_TOKEN=/home/ghrelease/.config/coderoast/STORE_WRITER_TOKEN
# Public mirror (no Docker Hub pull), the digest coderoast-server's redis fixture and the runner
# isolation script pin.
SMOKE_IMAGE="ghcr.io/coderoasted/mirror/library/redis:7-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"

render_child() {
    cat <<'EOF'
#!/bin/sh
# Written by malf/runner/desk-rootless-docker.sh. The child half of rootless dockerd, run by
# rootlesskit inside the new user, mount and network namespaces (moby's contrib/dockerd-rootless.sh
# child branch): drop the parent's /run entries the daemon must own, then become dockerd. A user unit
# has a login session's cgroup, so dockerd keeps its default systemd cgroup driver here.
set -e
rm -f /run/docker /run/containerd /run/xtables.lock
exec dockerd --host="unix://$XDG_RUNTIME_DIR/docker.sock" "$@"
EOF
}

render_unit() {
    cat <<EOF
# Written by malf/runner/desk-rootless-docker.sh. Rootless dockerd for the desk account: a
# container's root is this account, so a bind mount reaches only what this account can read.
[Unit]
Description=Rootless Docker for the desk account
After=default.target

[Service]
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/rootlesskit --state-dir=%t/dockerd-rootless --net=slirp4netns --mtu=65520 --slirp4netns-sandbox=auto --slirp4netns-seccomp=auto --disable-host-loopback --port-driver=builtin --copy-up=/etc --copy-up=/run --propagation=rslave %h/.local/libexec/coderoast/dockerd-rootless-child.sh
ExecStartPost=/usr/bin/timeout 60 /bin/sh -c 'until [ -S %t/docker.sock ]; do sleep 1; done'
Delegate=yes
KillMode=mixed
LimitNOFILE=1048576
TasksMax=infinity
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
}

render_envd() {
    # shellcheck disable=SC2016  # environment.d expands it, not this shell
    printf 'DOCKER_HOST=unix://${XDG_RUNTIME_DIR}/docker.sock\n'
}

render_bashrc_block() {
    printf '%s\n' "$MARK_BEGIN"
    # shellcheck disable=SC2016  # expanded by each shell that reads ~/.bashrc
    printf '%s\n' 'export DOCKER_HOST="unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"'
    printf '%s\n' "$MARK_END"
}

installed_bashrc_block() {
    [[ -f "$BASHRC" ]] && sed -n "\\|^$MARK_BEGIN\$|,\\|^$MARK_END\$|p" "$BASHRC"
}

# drift <label> <installed path> <render function> — reads only.
drift() {
    if [[ ! -e "$2" ]]; then say "$1: ABSENT ($2)"; return; fi
    if diff -u "$2" <("$3") >/dev/null; then say "$1: in sync ($2)"; else
        say "$1: DIFFERS ($2)"; diff -u "$2" <("$3") || true; fi
}

rdocker() { DOCKER_HOST="unix://$SOCK" docker "$@"; }

# prove — the boundary, judged from this account. Every refusal is paired with a control that the
# same instrument succeeds where it may, so a dead daemon or an image without `cat` cannot pass.
prove() {
    local fail=0 got
    say "account $(id -un): groups '$(id -nG)'"
    if id -nG | tr ' ' '\n' | grep -qx docker; then
        say "OPEN: this process still carries the docker group (a group change needs a NEW login session)"; fail=1
    else say "closed: no docker group on this process"; fi
    if DOCKER_HOST="unix://$ROOTFUL_SOCK" docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
        say "OPEN: the rootful socket $ROOTFUL_SOCK ANSWERS this account"; fail=1
    else say "closed: the rootful socket $ROOTFUL_SOCK refuses this account"; fi
    if rdocker info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'; then
        say "control: the rootless daemon at $SOCK answers, in rootless mode"
    else say "FAILED control: no rootless daemon answers at $SOCK"; return 1; fi
    rdocker image inspect "$SMOKE_IMAGE" >/dev/null 2>&1 || rdocker pull -q "$SMOKE_IMAGE" >/dev/null
    local own; own="$(mktemp -d)"; printf 'x\n' > "$own/readable"; chmod 755 "$own"; chmod 644 "$own/readable"
    got="$(rdocker run --rm -v "$own:/probe:ro" "$SMOKE_IMAGE" sh -c 'cat /probe/readable >/dev/null && echo READ' 2>&1)" || got="FAILED: $got"
    rm -rf "$own"
    [[ "$got" == READ ]] || { say "FAILED control: a container could not read the account's own file through a bind mount ($got)"; return 1; }
    say "control: a container reads the account's own file through a bind mount"
    # Each source is one door; the writer's credential and the shadow file are the targets. Nothing
    # read is printed: a success prints its byte count only.
    local src priv
    for src in /home/ghrelease /; do
        for priv in unprivileged privileged; do
            local flags=(--rm -v "$src:/probe:ro"); [[ "$priv" == privileged ]] && flags+=(--privileged)
            # shellcheck disable=SC2016  # expanded by the container's shell
            got="$(rdocker run "${flags[@]}" "$SMOKE_IMAGE" sh -c \
                'for f in "$@"; do n=$(cat "$f" 2>/dev/null | wc -c); [ "$n" -gt 0 ] && { echo "READ $f ($n bytes)"; exit 0; }; done; echo REFUSED' \
                probe /probe/.config/coderoast/STORE_WRITER_TOKEN "/probe$WRITER_TOKEN" /probe/etc/shadow 2>&1)" \
                || got="REFUSED (docker run failed: ${got//$'\n'/ })"
            if [[ "$got" == REFUSED* ]]; then say "closed: -v $src:/probe ($priv) -> $got"
            else say "OPEN: -v $src:/probe ($priv) -> $got"; fail=1; fi
        done
    done
    if command -v wsl.exe >/dev/null 2>&1 && [[ "$(timeout 30 wsl.exe -u root -e id -u 2>/dev/null | tr -d '\0\r')" == 0 ]]; then
        say "RESIDUAL (not this script's door): \`wsl.exe -u root\` answers uid 0 with no password from this account"
    else say "residual: \`wsl.exe -u root\` did not answer uid 0"; fi
    return "$fail"
}

case "$MODE" in
    plan)
        drift "dockerd child" "$CHILD" render_child
        drift "user unit" "$UNIT" render_unit
        drift "environment.d" "$ENVD" render_envd
        if [[ "$(installed_bashrc_block)" == "$(render_bashrc_block)" ]]; then say "$BASHRC block: in sync"
        else say "$BASHRC block: ABSENT or DIFFERS"; fi
        say "lingering: $(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo unknown)"
        say "unit: $(systemctl --user is-active "$UNIT_NAME" 2>/dev/null || true)"
        say "plan only; --apply installs, --prove judges"
        ;;
    apply)
        for bin in rootlesskit slirp4netns newuidmap dockerd docker; do
            command -v "$bin" >/dev/null || die "missing $bin (packages: rootlesskit slirp4netns uidmap docker.io)"
        done
        for idmap in /etc/subuid /etc/subgid; do
            grep -q "^$(id -un):" "$idmap" || die "no subordinate id range for $(id -un) in $idmap"
        done
        install -d -m 0755 "$(dirname "$CHILD")" "$(dirname "$UNIT")" "$(dirname "$ENVD")"
        render_child > "$CHILD"; chmod 0755 "$CHILD"
        render_unit > "$UNIT"
        render_envd > "$ENVD"
        if [[ -z "$(installed_bashrc_block)" ]]; then { printf '\n'; render_bashrc_block; } >> "$BASHRC"
        elif [[ "$(installed_bashrc_block)" != "$(render_bashrc_block)" ]]; then
            die "$BASHRC carries a differing block between the markers; fix it by hand, then re-run"
        fi
        systemd-analyze --user verify "$UNIT"
        loginctl enable-linger "$(id -un)"
        systemctl --user daemon-reload
        systemctl --user enable "$UNIT_NAME"
        systemctl --user restart "$UNIT_NAME"
        rdocker info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless' \
            || die "the daemon at $SOCK is up but does not report rootless mode"
        rdocker pull -q "$SMOKE_IMAGE" >/dev/null
        cid="$(rdocker run -d --publish 127.0.0.1::6379 "$SMOKE_IMAGE")"
        port="$(rdocker port "$cid" 6379/tcp | head -n1 | sed 's/.*://')"
        reached=REFUSED
        for _ in $(seq 20); do
            if timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && printf 'PING\r\n' >&3 && head -c 5 <&3" 2>/dev/null | grep -q PONG; then reached=PONG; break; fi
            sleep 0.5
        done
        rdocker rm -f "$cid" >/dev/null
        [[ "$reached" == PONG ]] || die "a container published on 127.0.0.1:$port did not answer — the fixtures' shape does not work here"
        say "rootless daemon up at $SOCK; a container answered on 127.0.0.1:$port (the fixtures' shape)"
        say "new shells get DOCKER_HOST; running processes keep what they started with"
        ;;
    prove)
        prove
        ;;
esac
