#!/usr/bin/env bash
# isolate-runner-wsl-rollback.sh — undo isolate-runner-wsl.sh for one instance (runner-instances.sh).
#
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh --instance <ci|release>            # PLAN: changes nothing
#   sudo bash malf/runner/isolate-runner-wsl-rollback.sh --instance <ci|release> --apply    # undo it
#
#   ci       the runner goes back to the desk account, at its old directory, under its original unit,
#            and its rootless Docker is removed.
#   release  the runner is stopped, deregistered from the organisation and its units removed; its
#            rootless Docker is removed; the build slot goes back to the ci account (runner-instances.sh,
#            slot_holds), since step 0 then has no other runner.
#
# WHAT IT DOES NOT UNDO, on purpose:
#   * /opt/gcc-16.2 stays root-owned and not group/other-writable — the 0777 it had was a defect
#     (a door for any account into a compiler the desk executes), not a setting to restore;
#   * the instance's account and home stay, inert (no password, shell nologin, no process, no unit).
#     Removing it is one line this script prints, never runs: its home holds the rootless Docker
#     image store, the pip cache and (release) the persistent conan home, and deleting data is the
#     Founder's call;
#   * the packages installed (acl uidmap rootlesskit slirp4netns docker-buildx) stay.
# It works from any point the forward script stopped at: every step checks what is there.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runner-instances.sh
. "$HERE/runner-instances.sh"

APPLY=false
REQUESTED=""
usage() { echo "usage: sudo bash $0 --instance <${INSTANCES[*]// /|}> [--apply]" >&2; exit 2; }
while (($#)); do
    case "$1" in
        --instance) REQUESTED="${2:-}"; shift 2 || usage ;;
        --apply) APPLY=true; shift ;;
        *) usage ;;
    esac
done
[[ -n "$REQUESTED" ]] || usage
instance_select "$REQUESTED" || usage

say()  { printf '\033[1;34m[rollback %s]\033[0m %s\n' "$INSTANCE" "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[rollback %s] REFUSED:\033[0m %s\n' "$INSTANCE" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run it as root, plan included (it reads $STATE_DIR, which is root's): sudo bash $0 --instance $INSTANCE"

manifest_key() { [[ -f "$STATE_DIR/manifest" ]] && sed -n "s/^$1=//p" "$STATE_DIR/manifest"; return 0; }
DESK_USER="$(manifest_key DESK_USER)"
OLD_DIR="$(manifest_key OLD_DIR)"
moved=false
if [[ -f "$STATE_DIR/manifest" ]]; then
    [[ -n "$DESK_USER" ]] || die "$STATE_DIR/manifest is incomplete — it names no desk account"
    id "$DESK_USER" >/dev/null 2>&1 || die "the manifest's desk account '$DESK_USER' does not exist"
    if [[ "$INSTANCE" == ci ]]; then
        [[ -n "$OLD_DIR" ]] || die "$STATE_DIR/manifest names no old directory"
        [[ -f "$STATE_DIR/unit.orig" ]] || die "$STATE_DIR/unit.orig is missing — the original unit cannot be restored"
        if [[ -d "$NEW_DIR" ]]; then
            [[ ! -e "$OLD_DIR" ]] || die "both $NEW_DIR and $OLD_DIR exist — refusing to guess which one is the runner"
            moved=true
        else
            [[ -f "$OLD_DIR/.runner" ]] || die "neither $NEW_DIR nor a runner at $OLD_DIR exists"
        fi
    fi
else
    say "no rollback manifest ($STATE_DIR/manifest): the forward script never reached its runner step — only the steps before it are undone"
fi

# A job must not be running: the rollback stops the runner.
if systemctl is-active --quiet "$UNIT"; then
    cg="$(systemctl show -p ControlGroup --value "$UNIT")"
    if [[ -n "$cg" && -r "/sys/fs/cgroup$cg/cgroup.procs" ]]; then
        while read -r pid; do
            [[ "$(cat "/proc/$pid/comm" 2>/dev/null)" == Runner.Worker ]] \
                && die "a job is running on $RUNNER_NAME (Runner.Worker pid $pid) — let it finish, then re-run"
        done < "/sys/fs/cgroup$cg/cgroup.procs"
    fi
fi

# Every other instance still present on this box keeps what they share: the dockerd child script and
# the build slot root.
others_present=()
for other in "${INSTANCES[@]}"; do
    [[ "$other" != "$INSTANCE" ]] && instance_present "$other" && others_present+=("$other")
done
# The slot root goes away only when no instance remains and nobody holds a slot in it: malf would then
# resolve /tmp, and a holder in the shared root would stop excluding anyone.
slot_held=false
[[ -e "$SLOT_ROOT/slot" ]] && slot_held=true

case "$INSTANCE" in
    ci) runner_plan="$($moved && echo "$NEW_DIR -> $OLD_DIR, owner -> $DESK_USER, original unit restored" || echo "not moved — unit left as it is")" ;;
    release) runner_plan="$UNIT stopped, disabled and deleted; $RUNNER_NAME deregistered from $ORG (a removal token minted by the desk's gh, through stdin)" ;;
esac
if ((${#others_present[@]})); then
    slot_plan="KEPT for ${others_present[*]}; entries recomputed (runner-instances.sh, slot_holds)"
elif $slot_held; then
    slot_plan="KEPT — $SLOT_ROOT/slot exists (a lane holds it); re-run after its release"
else
    slot_plan="removed — malf falls back to /tmp/coderoast-build-slot"
fi

cat <<EOF

[rollback $INSTANCE] plan
  runner              $runner_plan
  rootless Docker     $DOCKER_UNIT stopped, disabled and deleted; $CHILD $( ((${#others_present[@]})) && echo "KEPT (used by ${others_present[*]})" || echo deleted)
  bank ACLs           named entries for $RUNNER_USER removed from ${BANK_WRITABLE[*]:-no root (it wrote none)}
  home-mirror ACLs    named entries for $RUNNER_USER removed from ${DESK_USER:-the desk}'s corpora-* directories
  build slot root     $SLOT_ROOT $slot_plan
  kept on purpose     /opt/gcc-16.2 permissions, the $RUNNER_USER account and home, the packages
EOF
$APPLY || { printf '\n[rollback %s] PLAN ONLY — nothing was changed. Re-run with --apply to do it.\n' "$INSTANCE"; exit 0; }

step "1/5 stop the runner and rootless Docker"
systemctl stop "$UNIT" || true
if systemctl list-unit-files "$DOCKER_UNIT" >/dev/null 2>&1; then
    systemctl disable --now "$DOCKER_UNIT" || true
fi
rm -f "/etc/systemd/system/$DOCKER_UNIT"
if ! ((${#others_present[@]})); then
    rm -f "$CHILD"
    rmdir "$(dirname "$CHILD")" 2>/dev/null || true
fi

case "$INSTANCE" in
    ci)
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
        ;;
    release)
        step "2/5 deregister $RUNNER_NAME and delete its unit"
        if [[ -f "$NEW_DIR/.runner" && -n "$DESK_USER" ]]; then
            token="$(sudo -u "$DESK_USER" -H gh api -X POST "orgs/$ORG/actions/runners/remove-token" --jq .token)" \
                || die "the desk's gh could not mint a removal token; the runner is stopped. Remove it from the organisation's runners page, then re-run"
            rm_rc=0
            (cd "$NEW_DIR" && sudo -u "$RUNNER_USER" -H bash -c \
                'IFS= read -r ACTIONS_RUNNER_INPUT_TOKEN; export ACTIONS_RUNNER_INPUT_TOKEN; exec ./config.sh remove' <<< "$token") || rm_rc=$?
            unset token
            (( rm_rc == 0 )) || die "config.sh remove exited $rm_rc (above); the runner is stopped. Remove $RUNNER_NAME from the organisation's runners page, then re-run"
            say "$RUNNER_NAME deregistered"
        else
            say "no registration at $NEW_DIR/.runner — nothing to deregister"
        fi
        systemctl disable "$UNIT" 2>/dev/null || true
        rm -f "$UNIT_FILE"
        rm -rf "/etc/systemd/system/$UNIT.d/"
        ;;
esac

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
if ((${#others_present[@]})); then
    # The unit is gone, so this instance no longer counts as present: its account loses its entries,
    # and the remaining instances get theirs (ci takes the slot back when release goes).
    say "slot root grants: $(apply_slot_acl "${DESK_USER:?the manifest names no desk account to keep on the slot root}")"
elif $slot_held; then
    say "$SLOT_ROOT/slot exists — KEPT; release that slot, then re-run this script to remove $SLOT_ROOT"
else
    rm -rf "$SLOT_ROOT"
    say "$SLOT_ROOT removed; malf resolves /tmp/coderoast-build-slot again"
fi

step "5/5 restore the original unit and start the runner"
case "$INSTANCE" in
    ci)
        if [[ -f "$STATE_DIR/manifest" ]]; then
            cp -a "$STATE_DIR/unit.orig" "$UNIT_FILE"
            rm -rf "/etc/systemd/system/$UNIT.d/"
        fi
        systemctl daemon-reload
        systemctl start "$UNIT"
        sleep 5
        systemctl is-active --quiet "$UNIT" || die "the restored runner unit did not stay active — 'journalctl -u $UNIT'"
        now_user="$(ps -o user= -p "$(systemctl show -p MainPID --value "$UNIT")")"
        ;;
    release)
        systemctl daemon-reload
        say "no unit preceded $RUNNER_NAME — nothing to restore or start"
        now_user="nobody (the runner is removed)"
        ;;
esac
[[ -f "$STATE_DIR/manifest" ]] && mv "$STATE_DIR" "$STATE_DIR.rolled-back.$(date +%Y%m%dT%H%M%S)"

cat <<EOF

[rollback $INSTANCE] DONE — $RUNNER_NAME runs as $now_user.
  The $RUNNER_USER account is still present and inert. To delete it and its data (the rootless
  Docker image store, the pip cache, any conan home), which this script never does:
    sudo userdel -r $RUNNER_USER && sudo sed -i '/^$RUNNER_USER:/d' /etc/subuid /etc/subgid
EOF
