#!/usr/bin/env bash
# isolate-runner-wsl-rollback.sh — undo isolate-runner-wsl.sh: the runner goes back to the desk
# account, at its old directory, under its original unit, and rootless Docker is removed.
#
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh            # PLAN: what it would undo; changes nothing
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh --apply    # undo it
#
# WHAT IT DOES NOT UNDO, on purpose:
#   * /opt/gcc-16.2 stays root-owned and not group/other-writable — the 0777 it had was a defect
#     (a door for any account into a compiler the desk executes), not a setting to restore;
#   * the account ghrunner and its home stay, inert (no password, shell nologin, no process, no
#     unit). Removing it is one line this script prints, never runs: its home holds the rootless
#     Docker image store and the runner's pip cache, and deleting data is the Founder's call;
#   * the packages installed (acl uidmap rootlesskit slirp4netns docker-buildx) stay.
# It works from any point the forward script stopped at: every step checks what is there.
set -euo pipefail

UNIT="actions.runner.CodeRoasted.malf-runner.service"
RUNNER_USER="ghrunner"
RUNNER_HOME="/home/$RUNNER_USER"
NEW_DIR="$RUNNER_HOME/actions-runner-malf"
DOCKER_UNIT="coderoast-runner-docker.service"
CHILD="/usr/local/libexec/coderoast/dockerd-rootless-child.sh"
SLOT_ROOT="/var/lib/coderoast-build"
BANK_ROOT="/mnt/wsl/corpora"
BANK_WRITABLE=(corpora-longitudinal-logs)
STATE_DIR="/var/lib/coderoast-runner-isolation"

APPLY=false
case "${1:-}" in
    "") ;;
    --apply) APPLY=true ;;
    *) echo "usage: sudo bash $0 [--apply]" >&2; exit 2 ;;
esac

say()  { printf '\033[1;34m[rollback]\033[0m %s\n' "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[rollback] REFUSED:\033[0m %s\n' "$*" >&2; exit 1; }

if $APPLY; then [[ $EUID -eq 0 ]] || die "--apply needs root: sudo bash $0 --apply"; fi
UNIT_FILE="/etc/systemd/system/$UNIT"

manifest_key() { [[ -f "$STATE_DIR/manifest" ]] && sed -n "s/^$1=//p" "$STATE_DIR/manifest"; return 0; }
DESK_USER="$(manifest_key DESK_USER)"
OLD_DIR="$(manifest_key OLD_DIR)"
moved=false
if [[ -f "$STATE_DIR/manifest" ]]; then
    [[ -n "$DESK_USER" && -n "$OLD_DIR" ]] || die "$STATE_DIR/manifest is incomplete — it names no desk account or old directory"
    id "$DESK_USER" >/dev/null 2>&1 || die "the manifest's desk account '$DESK_USER' does not exist"
    [[ -f "$STATE_DIR/unit.orig" ]] || die "$STATE_DIR/unit.orig is missing — the original unit cannot be restored"
    if [[ -d "$NEW_DIR" ]]; then
        [[ ! -e "$OLD_DIR" ]] || die "both $NEW_DIR and $OLD_DIR exist — refusing to guess which one is the runner"
        moved=true
    else
        [[ -f "$OLD_DIR/.runner" ]] || die "neither $NEW_DIR nor a runner at $OLD_DIR exists"
    fi
else
    say "no rollback manifest ($STATE_DIR/manifest): the runner was never stopped or moved — only the pre-move steps are undone"
fi

# A job must not be running: the rollback stops the runner.
if systemctl is-active --quiet "$UNIT"; then
    cg="$(systemctl show -p ControlGroup --value "$UNIT")"
    if [[ -n "$cg" && -r "/sys/fs/cgroup$cg/cgroup.procs" ]]; then
        while read -r pid; do
            [[ "$(cat "/proc/$pid/comm" 2>/dev/null)" == Runner.Worker ]] \
                && die "a job is running on the runner (Runner.Worker pid $pid) — let it finish, then re-run"
        done < "/sys/fs/cgroup$cg/cgroup.procs"
    fi
fi

# The slot root goes away only when nobody holds a slot in it: malf would then resolve /tmp, and a
# holder in the shared root would stop excluding anyone.
slot_held=false
[[ -e "$SLOT_ROOT/slot" ]] && slot_held=true

cat <<EOF

[rollback] plan
  runner              $($moved && echo "$NEW_DIR -> $OLD_DIR, owner -> $DESK_USER, original unit restored" || echo "not moved — unit left as it is")
  rootless Docker     $DOCKER_UNIT stopped, disabled and deleted; $CHILD deleted
  bank ACLs           named entries for $RUNNER_USER removed from ${BANK_WRITABLE[*]}
  home-mirror ACLs    named entries for $RUNNER_USER removed from ${DESK_USER:-the desk}'s corpora-* directories
  build slot root     $SLOT_ROOT $($slot_held && echo "KEPT — $SLOT_ROOT/slot exists (a lane holds it); re-run after its release" || echo "removed — malf falls back to /tmp/coderoast-build-slot")
  kept on purpose     /opt/gcc-16.2 permissions, the $RUNNER_USER account and home, the packages
EOF
$APPLY || { printf '\n[rollback] PLAN ONLY — nothing was changed. Re-run with --apply to do it.\n'; exit 0; }

step "1/5 stop the runner and rootless Docker"
systemctl stop "$UNIT" || true
if systemctl list-unit-files "$DOCKER_UNIT" >/dev/null 2>&1; then
    systemctl disable --now "$DOCKER_UNIT" || true
fi
rm -f "/etc/systemd/system/$DOCKER_UNIT" "$CHILD"
rmdir "$(dirname "$CHILD")" 2>/dev/null || true

if $moved; then
    step "2/5 move $NEW_DIR -> $OLD_DIR and hand it back to $DESK_USER"
    mv "$NEW_DIR" "$OLD_DIR"
    for link in bin externals; do
        t="$(readlink "$OLD_DIR/$link")"
        [[ "$t" == /* ]] || ln -sfn "$OLD_DIR/$t" "$OLD_DIR/$link"
    done
    cp -a "$STATE_DIR/path.orig" "$OLD_DIR/.path"
    cp -a "$STATE_DIR/env.orig" "$OLD_DIR/.env"
    chown -R "$DESK_USER:$(id -gn "$DESK_USER")" "$OLD_DIR"
    setfacl -R -b "$OLD_DIR"
    rm -rf "$OLD_DIR/_work/_tool"
else
    step "2/5 runner not moved — nothing to move back"
fi

step "3/5 bank and home-mirror ACLs"
for root in "${BANK_WRITABLE[@]}"; do
    [[ -d "$BANK_ROOT/$root" ]] || continue
    setfacl -R -x "u:$RUNNER_USER" "$BANK_ROOT/$root" || true
    find "$BANK_ROOT/$root" -type d -exec setfacl -d -x "u:$RUNNER_USER" {} + || true
done
if [[ -n "$DESK_USER" ]]; then
    desk_home="$(getent passwd "$DESK_USER" | cut -d: -f6)"
    for entry in "$desk_home"/corpora-* "$desk_home"/gcc-corpus-build; do
        [[ -d "$entry" && ! -L "$entry" ]] || continue
        setfacl -R -x "u:$RUNNER_USER" "$entry" || true
        find "$entry" -type d -exec setfacl -d -x "u:$RUNNER_USER" {} + || true
    done
fi

step "4/5 build slot root"
if $slot_held; then
    say "$SLOT_ROOT/slot exists — KEPT; release that slot, then re-run this script to remove $SLOT_ROOT"
else
    rm -rf "$SLOT_ROOT"
    say "$SLOT_ROOT removed; malf resolves /tmp/coderoast-build-slot again"
fi

step "5/5 restore the original unit and start the runner"
if [[ -f "$STATE_DIR/manifest" ]]; then
    cp -a "$STATE_DIR/unit.orig" "$UNIT_FILE"
    rm -rf "/etc/systemd/system/$UNIT.d/"
fi
systemctl daemon-reload
systemctl start "$UNIT"
sleep 5
systemctl is-active --quiet "$UNIT" || die "the restored runner unit did not stay active — 'journalctl -u $UNIT'"
[[ -f "$STATE_DIR/manifest" ]] && mv "$STATE_DIR" "$STATE_DIR.rolled-back.$(date +%Y%m%dT%H%M%S)"

cat <<EOF

[rollback] DONE — the runner runs as $(ps -o user= -p "$(systemctl show -p MainPID --value "$UNIT")") again.
  The $RUNNER_USER account is still present and inert. To delete it and its data (the rootless
  Docker image store, the pip cache), which this script never does:
    sudo userdel -r $RUNNER_USER && sudo sed -i '/^$RUNNER_USER:/d' /etc/subuid /etc/subgid
EOF
