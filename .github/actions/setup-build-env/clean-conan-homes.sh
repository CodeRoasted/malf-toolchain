#!/usr/bin/env bash
# clean-conan-homes.sh — at job end, every conan home under $CONAN_HOME drops its build and temp
# folders, and says how large it is (DN-119.D2 invariant 3, and gate G2's number).
#
# WHY. A home that outlives the job (`conan-home.sh true`) keeps every package it built, which is
# the point, and every BUILD folder, which is not: the desk's four equivalent homes weighed 37.6 GB
# on 2026-09-28, 27 GB of it `p/b` build folders. Without this step the home grows without bound and
# fails late and loudly. `conan cache clean '*' --build --temp` keeps the packages.
#
# WHICH HOMES: the base, and each keyed home malf puts one level under it — a directory carrying
# conan's `settings.yml` marker, minus conan's own structural children, which is the rule of malf's
# `_malf_conan_homes` (a stray conan run can seed a marker into `p/` or `profiles/`, and a clean
# pointed at one of those would seed it further).
set -euo pipefail

base="${CONAN_HOME:?CONAN_HOME must be set by setup-build-env}"
if [[ ! -d "$base" ]]; then
    echo "no conan home at $base — nothing to clean"
    exit 0
fi
homes=("$base")
for dir in "$base"/*/; do
    name="$(basename "$dir")"
    case "$name" in p|profiles|extensions|migrations) continue ;; esac
    [[ -f "${dir}settings.yml" ]] && homes+=("${dir%/}")
done
for home in "${homes[@]}"; do
    before="$(du -sm "$home" | cut -f1)"
    builds=0
    [[ -d "$home/p/b" ]] && builds="$(du -sm "$home/p/b" | cut -f1)"
    CONAN_HOME="$home" conan cache clean '*' --build --temp
    after="$(du -sm "$home" | cut -f1)"
    echo "conan home $home: $before MB, of which p/b $builds MB; $after MB after the clean"
done
