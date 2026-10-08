#!/usr/bin/env bash
# Checks coderoast-hub out into <dir> at the commit this run must test the shipped scenarios at, and
# prints that commit; exits non-zero naming why when there is none.
#
# RULED (the Founder, 2026-09-26; LEXICON § Rulings closed): a release serves the coderoast-hub
# commit it recorded, never hub `main`, so its tests must run against that same commit. At a tag the
# commit is the one step 0's build record verified for coderoast-hub, which the release workflow's
# `attest` job reads from the record it attested and hands on as its `hub` output. The hub's own tag
# of the release's name cannot serve here: the superproject's Release mints it, after every package
# tag (OPS-1.S13), so at this repository's tag it does not exist yet. Measured at v1.10.4: the record
# named hub b2360e1, the hub tag v1.10.4 is c9e3151, b2360e1 is its ancestor, and the two commits
# between them (the evidence and the showcase) change nothing under insight-playground/.
#
# Off a tag (a pull request, a dispatch on a branch) there is no release and no record, and the run
# tests hub `main`, where the next release's scenarios are written.
#
# At a tag, an absent or malformed commit is a refusal: there is no fallback to `main`, because a
# fallback is exactly the unrecorded content the ruling excludes.
#
# It lives beside the `coderoast-ci` composite, which runs it when a caller asks for the hub
# scenarios (coderoast-server and insight-eidos both test against them):
#
#   checkout_release_hub.sh tag b2360e15806d2609c47aec92e2e6eb9c50cf0f28 /tmp/coderoast-hub
#   checkout_release_hub.sh branch "" /tmp/coderoast-hub

set -euo pipefail

ref_type="${1:-}"
recorded="${2:-}"
dir="${3:-}"
if [[ -z "$ref_type" || -z "$dir" ]]; then
    echo "usage: checkout_release_hub.sh <github.ref_type> <the record's hub commit, or \"\"> <dir>" >&2
    exit 2
fi
hub="https://github.com/CodeRoasted/coderoast-hub.git"

if [[ "$ref_type" == "tag" ]]; then
    if ! [[ "$recorded" =~ ^[0-9a-f]{40}$ ]]; then
        echo "this is a tag run, and the hub commit its build record names is '$recorded', not a 40-hex commit id. A tag tests the scenario set its release ships, which is the coderoast-hub commit step 0 verified; the release workflow's attest job reads it from the record and passes it as its hub output. Refusing rather than testing hub main. If this run was not started by release.yaml, run it on a branch instead." >&2
        exit 1
    fi
    want="$recorded"
else
    want="main"
fi

rm -rf "$dir"
git clone --quiet --no-checkout --filter=blob:none "$hub" "$dir"
if [[ "$want" == "main" ]]; then
    git -C "$dir" checkout --quiet --detach origin/main
else
    if ! git -C "$dir" cat-file -e "$want^{commit}" 2>/dev/null; then
        echo "coderoast-hub has no commit $want, which the build record names for this release; refusing." >&2
        exit 1
    fi
    git -C "$dir" checkout --quiet --detach "$want"
fi
got="$(git -C "$dir" rev-parse HEAD)"
if [[ "$want" != "main" && "$got" != "$want" ]]; then
    echo "coderoast-hub checked out at $got, not the recorded $want; refusing." >&2
    exit 1
fi
printf 'coderoast-hub at %s (%s)\n' "$got" "$want"
