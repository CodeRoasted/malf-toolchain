#!/usr/bin/env bash
# Fetch a tagged cross-repo Conan cache artifact from a GitHub Release and restore it into the
# current CONAN_HOME. Intended for CI/release jobs; local dev normally uses sibling checkouts or
# /opt/coderoast/conan-stable.
#
# Single source of truth: this reconciles the three drifted per-repo copies that previously lived
# in logcraft / coderoast-server / insight-eidos (+ metalog). It ships with the coderoast-ci-setup
# composite action and is invoked by its vendor loop; it is NOT copied into any consumer repo.
#
# Workflow:
#   1. `gh release download <tag> -R <repo> -p '<pkg>-<ver>.tgz'` pulls the tarball produced by the
#      source repo's release-publish.yml — ALWAYS, whatever the cache holds.
#   2. The tarball's `pkglist.json` names the TAG's recipe revision (exactly one, or refuse).
#   3. Every other recipe revision of `<pkg>/<ver>` in CONAN_HOME is removed, the tarball is
#      restored, and the cache must then hold exactly the tag's revision, or the script fails.
#
# WHY NO FAST PATH (ROADMAP N367). A cached `<pkg>/<ver>` is not the tag's package: a persistent
# conan home or a restored Actions cache can hold one built from another commit under the same
# version, and conan resolves the newest revision it holds. Measured at the 1.10.6 cut: a cache
# held an `insight_canon/1.10.6` built from an older commit, and two publish jobs only ever passed
# from it. The version is a name; the recipe revision is the identity the tag is checked against.
#
# Usage:
#   bash ci_fetch_conan_package.sh <pkg-name> <version> <owner/repo> [release-tag]
#   bash ci_fetch_conan_package.sh logcraft_core 1.7.1 CodeRoasted/logcraft
#
# Requirements:
#   * `gh` CLI on PATH (pre-installed on GitHub-hosted runners).
#   * `GH_TOKEN` env with read access to the source repo's releases. For a PUBLIC source repo the
#     built-in Actions GITHUB_TOKEN suffices; a PRIVATE source repo needs a fine-grained PAT
#     (Contents:read) — the coderoast-ci-setup vendor loop selects the token by declared visibility.
#   * The same `linux-gcc16-release` profile already present in CONAN_HOME (otherwise the restored
#     binary's settings won't match a consumer install resolving against a different profile sha).

set -euo pipefail

PKG_NAME="${1:-}"
PKG_VERSION="${2:-}"
SOURCE_REPO="${3:-}"
# RELEASE_TAG: the GitHub Release tag holding the asset. Defaults to v${PKG_VERSION}, correct where
# the package version == the release tag; pass the 4th arg when they differ.
RELEASE_TAG="${4:-v${PKG_VERSION}}"

if [[ -z "$PKG_NAME" || -z "$PKG_VERSION" || -z "$SOURCE_REPO" ]]; then
    echo "usage: $0 <pkg-name> <version> <owner/repo> [release-tag]" >&2
    exit 2
fi

PKG_REF="${PKG_NAME}/${PKG_VERSION}"

if ! command -v gh >/dev/null 2>&1; then
    echo "ci_fetch_conan_package: gh CLI not found on PATH" >&2
    exit 1
fi

ASSET="${PKG_NAME}-${PKG_VERSION}.tgz"
WORKDIR="$(mktemp -d -t ci_fetch_conan_package.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "ci_fetch_conan_package: downloading $ASSET from $SOURCE_REPO@$RELEASE_TAG"
gh release download "$RELEASE_TAG" \
    --repo "$SOURCE_REPO" \
    --pattern "$ASSET" \
    --dir "$WORKDIR"

TARBALL="$WORKDIR/$ASSET"
[[ -f "$TARBALL" ]] || { echo "ci_fetch_conan_package: $ASSET missing after download" >&2; exit 1; }

# pre: stdin is `conan list --format=json` output or a tarball's pkglist.json.
# post: one recipe revision of $PKG_REF per line, sorted; nothing when the ref is absent.
revisions_of() {
    python3 -I -c '
import json, sys
ref, data = sys.argv[1], json.load(sys.stdin)
tree = data.get("Local Cache", data)
print("\n".join(sorted(tree.get(ref, {}).get("revisions", {}))))
' "$PKG_REF"
}

tar -xzf "$TARBALL" -C "$WORKDIR" pkglist.json
tag_rrev="$(revisions_of < "$WORKDIR/pkglist.json")"
if [[ -z "$tag_rrev" || "$tag_rrev" == *$'\n'* ]]; then
    echo "ci_fetch_conan_package: $ASSET from $SOURCE_REPO@$RELEASE_TAG must carry exactly one" \
         "recipe revision of $PKG_REF, carries: ${tag_rrev:-none}" >&2
    exit 1
fi

cached="$(conan list "$PKG_REF#*" --format=json | revisions_of)"
while IFS= read -r rrev; do
    [[ -n "$rrev" && "$rrev" != "$tag_rrev" ]] || continue
    echo "ci_fetch_conan_package: dropping $PKG_REF#$rrev — cached under the version, not the" \
         "recipe revision $RELEASE_TAG carries ($tag_rrev)"
    conan remove "$PKG_REF#$rrev" --confirm
done <<< "$cached"

echo "ci_fetch_conan_package: restoring $TARBALL into CONAN_HOME=${CONAN_HOME:-default}"
conan cache restore "$TARBALL" >/dev/null

held="$(conan list "$PKG_REF#*" --format=json | revisions_of)"
if [[ "$held" != "$tag_rrev" ]]; then
    echo "ci_fetch_conan_package: after the restore CONAN_HOME holds $PKG_REF at" \
         "'${held//$'\n'/ }', not exactly $RELEASE_TAG's recipe revision $tag_rrev" >&2
    exit 1
fi
echo "ci_fetch_conan_package: $PKG_REF#$tag_rrev vendored OK (the recipe revision $RELEASE_TAG carries)"
