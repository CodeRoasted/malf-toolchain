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
#      source repo's release-publish.yml.
#   2. `conan cache restore <tarball>` injects the recipe + binary into the consumer's CONAN_HOME,
#      identical to `cache save`/`restore` on the local shared cache.
#
# Idempotent: if the package is already in the local cache (same recipe revision) this is a no-op
# and exits 0 without contacting GitHub.
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
#   * The same `linux-gcc15-release` profile already present in CONAN_HOME (otherwise the restored
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

# Fast path: already vendored.
if conan list "$PKG_REF" --format=compact 2>/dev/null | grep -q "$PKG_REF"; then
    echo "ci_fetch_conan_package: $PKG_REF already in local cache, skipping download."
    exit 0
fi

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

echo "ci_fetch_conan_package: restoring $TARBALL into CONAN_HOME=${CONAN_HOME:-default}"
conan cache restore "$TARBALL" >/dev/null

echo "ci_fetch_conan_package: $PKG_REF vendored OK"
