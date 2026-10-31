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
#   release  malf-release, account ghrelease — the cut (cut.yml), whose runner group admits nothing else.
#            Its lifecycle is a fresh INSTALL: this box never had a runner under that account.

ORG="CodeRoasted"
RELEASE_GROUP="coderoast-release"
# Shared by every instance; root's to write.
CHILD="/usr/local/libexec/coderoast/dockerd-rootless-child.sh"
PARTITION_SCRIPT="/usr/local/libexec/coderoast/seat-partition.py"
PARTITION_TEMPLATE="seat-partition@.service"
# The shared slot root earlier versions of these scripts made: retired by DN-119.D9 (3), each seat's
# slot now lives in its own account's state. The plan names it while it is still on the box, and the
# runbook (README § Seats) removes it once every instance is re-applied.
RETIRED_SLOT_ROOT="/var/lib/coderoast-build"
BANK_ROOT="/mnt/wsl/corpora"
GCC_PREFIX="/opt/gcc-16.2"
INSTANCES=(ci release)

# THE SEATS (DN-119.D9, RUL-3). Each runner is a SEAT: a top-level slice that is a cpuset PARTITION
# ROOT with a hard memory cap. The partition removes the seat's CPUs from every other cgroup's
# effective set, so the desk (everything outside the seat slices: init.scope, user.slice and
# system.slice) is the kernel-enforced remainder and needs no configuration of its own.
#   SEAT_SLICE     a direct child of -.slice, no dash in the name (a dash nests a slice, and a
#                  partition root's parent must itself be one: the root cgroup is, system.slice is not)
#   SEAT_CPUS      whole SMT pairs (2k, 2k+1 on this VM); release on the highest pairs, away from cpu 0
#   SEAT_MEM_MAX   the hard cap (MemoryMax=)
#   SEAT_MEM_MIN   a guarantee the kernel never reclaims (MemoryMin=), release only
#   SEAT_MEM_LOW   a best-effort protection (MemoryLow=), ci only
#                  Both reach the leaves that hold the seat's pages (the runner, its docker unit,
#                  their children) only through memory_recursiveprot on the cgroup2 mount, which the
#                  seat's partition script adds: WSL mounts cgroup2 before systemd, without it.
#   SEAT_SWAP_MAX  MemorySwapMax=; 0 on release, so an overshoot is an OOM kill inside the seat, a
#                  loud red and never a slow thrash; empty leaves the seat's swap unbounded
# THE SIZES ARE THE FOUNDER'S RULING (RUL-3, 2026-10-14: the desk gets the larger share, a slower
# cut is accepted; the ci seat raised to 7 GiB on 2026-10-30). Of 16 vCPUs and 23.47 GiB: desk 0-7
# (8 vCPUs, the remainder, about 6.5 GiB guaranteed), ci 8-9, release 10-15. A change to .wslconfig `processors` or `memory` changes this
# table in the same act: a cpuset naming a CPU the VM no longer has is refused by the partition unit,
# and the seat stays offline.
# The release cap holds only once malf derives its width from the seat (DN-119.D9 (1), build row
# R5): a width read off the VM-wide MemAvailable plans for about 21 GiB inside a 10 GiB seat with no
# swap, and every heavy build is OOM-killed.

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
            SEAT_CPUS="8-9"
            SEAT_MEM_MAX="7G"
            SEAT_MEM_MIN=""
            SEAT_MEM_LOW="7G"
            SEAT_SWAP_MAX=""
            ;;
        release)
            RUNNER_NAME="malf-release"
            RUNNER_USER="ghrelease"
            RUNNER_DIRNAME="actions-runner-release"
            DOCKER_UNIT="coderoast-release-docker.service"
            STATE_DIR="/var/lib/coderoast-runner-isolation-release"
            # The cut's jobs and the release jobs write no bank root.
            BANK_WRITABLE=()
            # Mode 700 and no ACL: no other account reads what the release built before it ships,
            # the desk included; the desk reads a release job's log on GitHub, or as root.
            DESK_READS_HOME=false
            HOME_MODE=0700
            # ProtectHome=tmpfs with the runner's own home bound back: every other home is absent
            # from the release runner's view, so nothing another account planted there (a venv, a
            # tool, a PATH entry) can be reached from a release job even by mistake.
            PROTECT_HOME=true
            SEAT_CPUS="10-15"
            SEAT_MEM_MAX="10G"
            SEAT_MEM_MIN="10G"
            SEAT_MEM_LOW=""
            SEAT_SWAP_MAX="0"
            ;;
        *) return 1 ;;
    esac
    UNIT="actions.runner.$ORG.$RUNNER_NAME.service"
    UNIT_FILE="/etc/systemd/system/$UNIT"
    RUNNER_HOME="/home/$RUNNER_USER"
    NEW_DIR="$RUNNER_HOME/$RUNNER_DIRNAME"
    DOCKER_RUNTIME="/run/$RUNNER_USER-docker"
    DOCKER_SOCK="$DOCKER_RUNTIME/docker.sock"
    SEAT_SLICE="seat_$INSTANCE.slice"
    PARTITION_UNIT="seat-partition@$INSTANCE.service"
    # The seat's build slot, in its own account's state (DN-119.D9 (3)): no other account can open
    # it, so no job of another seat can hold its mutex.
    SLOT_DIR="$RUNNER_HOME/.local/state/coderoast-build/slot"
}

# instance_present <name> — the instance's runner unit exists on this box.
instance_present() {
    local saved="${INSTANCE:-}" present=1
    instance_select "$1" && [[ -f "$UNIT_FILE" ]] && present=0
    [[ -n "$saved" ]] && instance_select "$saved"
    return "$present"
}
