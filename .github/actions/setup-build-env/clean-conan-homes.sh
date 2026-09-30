#!/usr/bin/env bash
# clean-conan-homes.sh — at job end, every conan home under $CONAN_HOME drops the versions its
# lock superseded and its build and temp folders, and says how large it is.
#
# WHY. A home that outlives the job (`conan-home.sh true`) keeps every package it built, which is
# the point, and two things that are not. ITS BUILD AND TEMP FOLDERS: the desk's four equivalent
# homes weighed 37.6 GB on 2026-09-28, 27 GB of it under `p/b`. That directory is not build folders
# alone — conan 2 also keeps there the package folders of the packages it built from source — so
# the clean removes the build-folder share, not all of it: measured on one run, 7 045 of 9 052 MB,
# about 2 007 MB of packages staying. `conan cache clean '*' --build --temp` keeps the packages.
# AND EVERY VERSION A DEPENDENCY EVER HAD: when the lock moves a package to another version, the
# old one stays, referenced by nothing. Without both, the home grows without bound and fails late.
#
# WHICH VERSIONS ARE SUPERSEDED: a recipe `<name>/<version>` in a home is superseded when the
# toolchain's conan.lock names `<name>` and does not name that version. A package the lock does not
# know is left alone — the lock says nothing about it. The lock is the one three levels above this
# script, which is the lock the builds in these homes resolved against when the script is run from
# the same toolchain checkout they used. With no lock there, nothing is pruned, and the output says
# so.
#
# WHICH HOMES: the base, and each keyed home malf puts one level under it — a directory carrying
# conan's `settings.yml` marker, minus conan's own structural children, which is the rule of malf's
# `_malf_conan_homes` (a stray conan run can seed a marker into `p/` or `profiles/`, and a clean
# pointed at one of those would seed it further).
#
# THE SIZES ARE EACH HOME'S OWN. The keyed homes live inside the base, so a plain `du` of the base
# counts them twice over the listing; the base's line excludes them, and the last line is the sum.
set -euo pipefail

base="${CONAN_HOME:?CONAN_HOME must be set by setup-build-env}"
if [[ ! -d "$base" ]]; then
    echo "no conan home at $base — nothing to clean"
    exit 0
fi
base="${base%/}"
homes=("$base")
for dir in "$base"/*/; do
    name="$(basename "$dir")"
    case "$name" in p|profiles|extensions|migrations) continue ;; esac
    [[ -f "${dir}settings.yml" ]] && homes+=("${dir%/}")
done

# A home's own size in MB: the base without the keyed homes nested under it.
own_mb() {   # <home>
    local excluded=() keyed
    if [[ "$1" == "$base" ]]; then
        for keyed in "${homes[@]:1}"; do excluded+=(--exclude="$keyed"); done
    fi
    du -sm "${excluded[@]}" "$1" | cut -f1
}

lock="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)/conan.lock"
[[ -f "$lock" ]] || echo "no conan.lock at $lock — superseded versions are not pruned"

# The references in the current CONAN_HOME that the lock superseded, one a line; non-zero when
# conan cannot list the home or prints no readable JSON.
superseded() {
    local listed
    listed="$(conan list '*' --format=json)" || return 1
    python3 -c 'import json, sys
lock = json.load(open(sys.argv[1], encoding="utf-8"))
named = {ref.split("#")[0] for key in ("requires", "build_requires", "python_requires")
         for ref in lock.get(key, [])}
names = {ref.split("/")[0] for ref in named}
held = sorted(json.loads(sys.argv[2]).get("Local Cache", {}))
print("\n".join(ref for ref in held if ref.split("/")[0] in names and ref not in named))' \
        "$lock" "$listed"
}

total_before=0
total_after=0
for home in "${homes[@]}"; do
    before="$(own_mb "$home")"
    builds=0
    [[ -d "$home/p/b" ]] && builds="$(du -sm "$home/p/b" | cut -f1)"
    if [[ -f "$lock" ]]; then
        pruned=()
        stale="$(CONAN_HOME="$home" superseded)" \
            || { echo "could not list $home against $lock — its superseded versions are unknown" >&2; exit 1; }
        while IFS= read -r ref; do
            [[ -n "$ref" ]] || continue
            CONAN_HOME="$home" conan remove "$ref" -c >/dev/null
            pruned+=("$ref")
        done <<< "$stale"
        if [[ ${#pruned[@]} -gt 0 ]]; then
            echo "pruned ${#pruned[@]} superseded version(s) from $home: ${pruned[*]}"
        fi
    fi
    CONAN_HOME="$home" conan cache clean '*' --build --temp
    after="$(own_mb "$home")"
    total_before=$(( total_before + before ))
    total_after=$(( total_after + after ))
    echo "conan home $home: $before MB, of which p/b $builds MB; $after MB after the clean"
done
echo "conan homes, ${#homes[@]} in all: $total_before MB before, $total_after MB after the clean"
