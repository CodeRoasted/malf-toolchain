# shellcheck shell=bash
# shellcheck disable=SC2034  # every variable here is read by the scripts that source this table
# runner-instances.sh — sourced by isolate-runner-wsl.sh and isolate-runner-wsl-rollback.sh: the one
# table of the self-hosted runners this WSL box carries, each under an OS account of its own.
#
# WHY TWO ACCOUNTS (DN-119.D8, option A, ruled by the Founder 2026-09-29). A runner runs every job as
# its one account, and that account owns what the next job executes (its Runner.Worker, the venv its
# PATH puts first, its HOME). The runner GROUP decides whose jobs those are. `ci` sits in `Default`,
# which admits every private repository of the organisation; `release` sits in `coderoast-release`,
# which admits only the repositories whose release jobs are routed to it, and carries no label but
# its own, so no job reaches it without naming it. The account is the boundary between the two: a
# `ci` job cannot write anything the `release` runner executes, links or reads a verdict from.
#
#   ci       malf-runner, account ghrunner  — every private repository's CI, the corpus collector.
#            Moved off the desk account (ROADMAP N214); its lifecycle is a MOVE of a runner the
#            desk registered.
#   release  malf-release, account ghrelease — step 0 (pharos-build.yml) and every release job.
#            Its lifecycle is a fresh INSTALL: this box never had a runner under that account.

ORG="CodeRoasted"
RELEASE_GROUP="coderoast-release"
# Shared by every instance; root's to write.
CHILD="/usr/local/libexec/coderoast/dockerd-rootless-child.sh"
SLOT_ROOT="/var/lib/coderoast-build"
BANK_ROOT="/mnt/wsl/corpora"
GCC_PREFIX="/opt/gcc-16.2"
INSTANCES=(ci release)

# instance_select <name> — sets every per-instance variable; returns 1 on an unknown name.
instance_select() {
    INSTANCE="$1"
    case "$INSTANCE" in
        ci)
            RUNNER_NAME="malf-runner"
            RUNNER_USER="ghrunner"
            RUNNER_DIRNAME="actions-runner-malf"
            DOCKER_UNIT="coderoast-runner-docker.service"
            STATE_DIR="/var/lib/coderoast-runner-isolation"
            # The bank roots a CI job WRITES. corpus-longitudinal.yml writes CORPUS_LOGS_DIR and
            # nothing else writes the bank from CI; every other root stays read-only, so a job that
            # starts writing one reds on EACCES, loudly, rather than being granted in advance.
            BANK_WRITABLE=(corpora-longitudinal-logs)
            # The desk reads this runner's work tree and logs (a failed leg is settled offline).
            DESK_READS_HOME=true
            HOME_MODE=0750
            # The ci unit is the one N214 wrote; it hides nothing under /home (ROADMAP N214 left
            # that open). Re-rendering it therefore reproduces it byte for byte.
            PROTECT_HOME=false
            ;;
        release)
            RUNNER_NAME="malf-release"
            RUNNER_USER="ghrelease"
            RUNNER_DIRNAME="actions-runner-release"
            DOCKER_UNIT="coderoast-release-docker.service"
            STATE_DIR="/var/lib/coderoast-runner-isolation-release"
            # Step 0 and the release jobs write no bank root.
            BANK_WRITABLE=()
            # Mode 700 and no ACL: no other account reads what the release built before it ships,
            # the desk included; the desk reads a release job's log on GitHub, or as root.
            DESK_READS_HOME=false
            HOME_MODE=0700
            # ProtectHome=tmpfs with the runner's own home bound back: every other home is absent
            # from the release runner's view, so nothing another account planted there (a venv, a
            # tool, a PATH entry) can be reached from a release job even by mistake.
            PROTECT_HOME=true
            ;;
        *) return 1 ;;
    esac
    UNIT="actions.runner.$ORG.$RUNNER_NAME.service"
    UNIT_FILE="/etc/systemd/system/$UNIT"
    RUNNER_HOME="/home/$RUNNER_USER"
    NEW_DIR="$RUNNER_HOME/$RUNNER_DIRNAME"
    DOCKER_RUNTIME="/run/$RUNNER_USER-docker"
    DOCKER_SOCK="$DOCKER_RUNTIME/docker.sock"
}

# instance_present <name> — the instance's runner unit exists on this box.
instance_present() {
    local saved="${INSTANCE:-}" present=1
    instance_select "$1" && [[ -f "$UNIT_FILE" ]] && present=0
    [[ -n "$saved" ]] && instance_select "$saved"
    return "$present"
}

# slot_holds <name> — whether that instance's account may take the build slot. The slot is taken by
# step 0 (`pharos build`, `malf slot acquire`) and by nothing else a runner runs, and step 0 runs on
# the release runner once one is present. Then ci gets NO entry on the slot root, not a read one:
# `malf slot` flocks `slot.lock`, and flock works on a read-only descriptor, so a ci job able to open
# that file could hold the mutex and stall every `malf slot` step 0 runs (WIP W284). With no release
# runner, step 0 is ci's and ci holds the slot.
slot_holds() {
    case "$1" in
        release) return 0 ;;
        ci) ! instance_present release ;;
    esac
}

# apply_slot_acl <desk user> — the slot root's named entries, recomputed from the table: the desk and
# each present instance allowed to hold the slot get rw (X for directories), on the root, on every
# entry under it (the lock file, the slot directory a holder left) and as the default for what is
# created later. Every other runner account's entries are removed. Idempotent. Prints the grant.
apply_slot_acl() {
    local spec="u:$1:rwX" dspec="u::rwX,g::---,o::---,u:$1:rwX" name user saved="${INSTANCE:-}"
    local removed=()
    for name in "${INSTANCES[@]}"; do
        instance_select "$name"
        user="$RUNNER_USER"
        id "$user" >/dev/null 2>&1 || continue
        if [[ -f "$UNIT_FILE" ]] && slot_holds "$name"; then
            spec+=",u:$user:rwX"
            dspec+=",u:$user:rwX"
        else
            removed+=("$user")
        fi
    done
    [[ -n "$saved" ]] && instance_select "$saved"
    install -d -m 0770 -o root -g root "$SLOT_ROOT"
    for user in "${removed[@]}"; do
        if getfacl -p -R "$SLOT_ROOT" | grep -q "user:$user:"; then
            setfacl -R -x "u:$user" "$SLOT_ROOT"
            find "$SLOT_ROOT" -type d -exec setfacl -d -x "u:$user" {} +
        fi
    done
    setfacl -R -m "$spec,m::rwX" "$SLOT_ROOT"
    find "$SLOT_ROOT" -type d -exec setfacl -d -m "$dspec,m::rwX" {} +
    echo "$spec${removed[*]:+ (no entry: ${removed[*]})}"
}
