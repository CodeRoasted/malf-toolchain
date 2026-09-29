#!/usr/bin/env bash
# conan-home.sh <persistent: true|false> — setup-build-env's CONAN_HOME, printed on stdout.
#
# false (every consumer but step 0): `<workspace>/.conan2`, which `actions/cache` restores and saves.
#
# true (DN-119.D2): ONE home per runner, OUTSIDE the checkout, under the runner user's own HOME, so it
# outlives the job and `actions/checkout`'s `git clean -ffdx` never empties it. Measured over step 0's
# `1.10.5` runs: a cold home cost about 1 800 s of third-party rebuilds, and a warm one still cost
# about 510 s to restore and re-save a 3.2 GB cache entry. malf keys every profile's home UNDER this
# one (`<home>/gcc16-release`, `<home>/cut-verify`), so all of them are warm at once.
#
# WHY UNDER $HOME AND KEYED BY RUNNER NAME. The runner service runs as its own user and cannot sudo
# (`NoNewPrivileges`), so a system path it cannot create is out; $HOME is the one directory that user
# owns by construction. Two runners never share a home: conan 2's cache is not safe under concurrent
# writers, and one runner runs one job at a time.
#
# A HOME THAT OUTLIVES THE JOB IS WRITTEN BY EVERY JOB ITS USER RUNS, SO IT IS HANDED OUT ONLY WHERE
# THOSE JOBS ARE THE RELEASE'S OWN. A runner runs every job as its one user, and it takes a
# job from every repository its runner group admits. A package any of those jobs writes into the home
# is what the next release build links, and nothing at the next job's start can tell it from one the
# release built: a planted binary whose manifest line is rewritten passes `conan cache check-integrity`,
# and `conan list` keeps reporting the package revision recorded at creation (measured 2026-09-29).
# So `true` REFUSES unless three facts hold:
#   1. GitHub's own record of THIS job places it in the runner group `coderoast-release`, the group the
#      organisation admits only the release to. It is read from the jobs API with the job's token, so
#      the caller grants `actions: read`. The runner's `.runner` file is not read: it is the runner's
#      own claim, and a runner moved between groups keeps the group it was registered in.
#   2. No other runner on this box runs as this user: among the runner units in /etc/systemd/system
#      (root's to write), the ones whose `User=` is this user are exactly this runner's. A second runner
#      under the same user shares the home with every repository ITS group admits.
#   3. The home is this user's alone: every directory from $HOME down to it is this user's and writable
#      by no other account, and every entry inside it is this user's. A file another account put there —
#      a package copied in with its ownership kept is one — is named and refused.
# WHAT THIS DOES NOT PROVE, stated so nobody reads more into it. That `coderoast-release` admits only the
# release: an organisation setting this job's token cannot read (malf/runner/README.md § Runner groups
# says what it must be). And anything about a runner whose user already runs a hostile job: every
# check here then runs inside that job's reach, so they refuse a MISCONFIGURED runner, and the boundary
# itself is the separate account and the group. A hosted runner REFUSES too: nothing on it outlives
# the job, and a "persistent" home there would be a claim the runner cannot keep.
set -euo pipefail

RELEASE_RUNNER_GROUP="coderoast-release"

refuse() {
    echo "::error::persistent-conan-home: $*" >&2
    exit 1
}

persistent="${1:?usage: conan-home.sh <true|false>}"
case "$persistent" in
    false)
        printf '%s\n' "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is not set — not an Actions step}/.conan2"
        ;;
    true)
        [[ "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] \
            || refuse "this asks for a conan home that outlives the job, and this runner is '${RUNNER_ENVIRONMENT:-unknown}', not self-hosted: nothing on it outlives the job"
        name="${RUNNER_NAME:?RUNNER_NAME is not set — not an Actions step}"
        [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
            || refuse "the runner name '$name' is not one plain path segment, so it cannot key a conan home without escaping the home's base"
        base="${HOME:?HOME is not set}/.cache/coderoast-build/conan"
        home="$base/$name"
        case "$home/" in
            "${GITHUB_WORKSPACE:-/nonexistent}"/*)
                refuse "the home $home is inside the checkout $GITHUB_WORKSPACE, which actions/checkout cleans" ;;
        esac

        # 1. The runner group, as GitHub records it for this job. The name is one plain path segment
        # (checked above), so it is safe inside the jq string.
        command -v gh >/dev/null \
            || refuse "gh is not installed, so GitHub's record of this job's runner group cannot be read"
        run="repos/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}/actions/runs/${GITHUB_RUN_ID:?GITHUB_RUN_ID is not set}"
        groups="$(gh api "$run/attempts/${GITHUB_RUN_ATTEMPT:-1}/jobs" --paginate \
                  --jq ".jobs[] | select(.status == \"in_progress\" and .runner_name == \"$name\") | .runner_group_name")" \
            || refuse "GitHub's record of run ${GITHUB_RUN_ID}'s jobs could not be read, so this job's runner group is unproven; the job's token needs \`permissions: actions: read\`"
        count="$(grep -c . <<< "$groups" || true)"
        [[ "$count" == 1 ]] \
            || refuse "GitHub lists $count in-progress job(s) of run $GITHUB_RUN_ID on runner '$name', not exactly one, so this job's runner group is unproven"
        [[ "${groups,,}" == "$RELEASE_RUNNER_GROUP" ]] \
            || refuse "this job runs in runner group '$groups', not '$RELEASE_RUNNER_GROUP': every repository that group admits sends jobs that run as this runner's user and can write the home, and the next release would build against what they wrote. Route this job to the release runner, or ask for persistent-conan-home: 'false'"

        # 2. This user runs this runner and no other.
        me="$(id -un)"
        units="${CONAN_HOME_RUNNER_UNITS:-/etc/systemd/system}"
        own=""
        others=()
        for unit in "$units"/actions.runner.*.service; do
            [[ -f "$unit" ]] || continue
            [[ "$(sed -n 's/^User=//p' "$unit" | head -n 1)" == "$me" ]] || continue
            case "$(basename "$unit")" in
                actions.runner.*."$name".service) own="$unit" ;;
                *) others+=("$(basename "$unit")") ;;
            esac
        done
        [[ -n "$own" ]] \
            || refuse "no runner unit in $units runs '$name' as $me, so which other runners share this account is unproven"
        (( ${#others[@]} == 0 )) \
            || refuse "$me also runs ${others[*]}: every job those runners take runs as $me and can write the home"

        # 3. The home is this user's alone. The three directories this script makes are made and kept
        # mode 700; $HOME and $HOME/.cache are the account's, so they are judged and never changed.
        uid="$(id -u)"
        dir=""
        for part in "$HOME" .cache coderoast-build conan "$name"; do
            dir="${dir:+$dir/}$part"
            [[ ! -L "$dir" ]] || refuse "$dir is a symlink, so where the home lives is whoever last wrote that link"
            [[ -d "$dir" ]] || mkdir -m 700 "$dir"
            owner="$(stat -c %u "$dir")"
            [[ "$owner" == "$uid" ]] || refuse "$dir belongs to uid $owner, not to $me (uid $uid)"
            case "$dir" in "$HOME/.cache/coderoast-build"|"$base"|"$home") chmod 700 "$dir" ;; esac
            mode="$(stat -c %a "$dir")"
            (( (8#$mode & 8#022) == 0 )) \
                || refuse "$dir is mode $mode, so another account can create or rename entries in it (chmod go-w $dir)"
        done
        walked=true
        foreign="$(find "$home" ! -uid "$uid" -printf '%U %p\n')" || walked=false
        [[ -z "$foreign" ]] \
            || refuse "$(grep -c . <<< "$foreign") entr(ies) in $home belong to another account than $me (uid $uid), so a release would build against what that account wrote; the first (uid path): $(head -n 5 <<< "$foreign" | tr '\n' ';' | sed 's/;$//'). Reset: delete $home (as root if one of them is a directory), and the next job builds it cold"
        $walked || refuse "the walk of $home failed, so whether another account wrote into it is unproven"
        printf '%s\n' "$home"
        ;;
    *)
        refuse "conan-home.sh takes true or false, not '$persistent'"
        ;;
esac
