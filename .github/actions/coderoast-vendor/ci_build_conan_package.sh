#!/usr/bin/env bash
# Build one cross-repo Conan dependency FROM SOURCE at its repository's `main` and create it into
# the current CONAN_HOME. The `deps: build` counterpart of ci_fetch_conan_package.sh: both leave
# the same reference (`<pkg>/<ver>`) in the local cache, one restored from a release tarball, this
# one created from the source the release would be cut from.
#
# Why it exists: a consumer's CI on `main` runs while the workspace version is OPEN, and the open
# version has no release until the cut, so the release download 404s ("release not found") before
# a single line compiles. The release workflow keeps `deps: vendor` (the tarballs of the waves cut
# before it); every other run builds what it depends on.
#
# Usage: ci_build_conan_package.sh <pkg-name> <version> <owner/repo>
#   GH_TOKEN  token able to clone <owner/repo> (the vendor row's visibility picks it)
#   PROFILE_NAME  the conan profile staged by setup-build-env under $CONAN_HOME/profiles/
#   MALF_TOOLCHAIN_DIR  exported by setup-build-env; its conan.lock pins the third-party graph
#   DEPS_SRC_ROOT  directory holding one shallow clone per repository, shared across rows
#
# Rows are processed in manifest order and nothing here reorders them: a row whose first-party
# dependency is not yet in the cache fails at conan's resolve (no remote carries first-party
# binaries), loudly, naming the missing reference.

set -euo pipefail

PKG_NAME="${1:-}"
PKG_VERSION="${2:-}"
SOURCE_REPO="${3:-}"

if [[ -z "$PKG_NAME" || -z "$PKG_VERSION" || -z "$SOURCE_REPO" ]]; then
    echo "usage: $0 <pkg-name> <version> <owner/repo>" >&2
    exit 2
fi
: "${GH_TOKEN:?ci_build_conan_package: GH_TOKEN is required to clone $SOURCE_REPO}"
: "${PROFILE_NAME:?ci_build_conan_package: PROFILE_NAME is required}"
: "${MALF_TOOLCHAIN_DIR:?ci_build_conan_package: MALF_TOOLCHAIN_DIR is unset - setup-build-env must run first}"
: "${DEPS_SRC_ROOT:?ci_build_conan_package: DEPS_SRC_ROOT is required}"

PKG_REF="${PKG_NAME}/${PKG_VERSION}"
PROFILE="$CONAN_HOME/profiles/$PROFILE_NAME"
LOCKFILE="$MALF_TOOLCHAIN_DIR/conan.lock"
[[ -f "$PROFILE" ]] || { echo "::error::ci_build_conan_package: profile '$PROFILE' not staged"; exit 1; }
[[ -f "$LOCKFILE" ]] || { echo "::error::ci_build_conan_package: lockfile '$LOCKFILE' missing"; exit 1; }

CLONE="$DEPS_SRC_ROOT/$SOURCE_REPO"
if [[ ! -d "$CLONE/.git" ]]; then
    mkdir -p "$(dirname -- "$CLONE")"
    # The token rides in git's environment configuration, never on a command line.
    basic="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" \
    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $basic" \
        git clone --quiet --depth 1 --branch main --single-branch \
            "https://github.com/$SOURCE_REPO.git" "$CLONE"
fi
echo "ci_build_conan_package: $SOURCE_REPO main at $(git -C "$CLONE" rev-parse HEAD)"

# The package's directory is the one its repository DECLARES in packages.yml, read with conan's
# own interpreter (PyYAML is a conan dependency, so it is present wherever conan runs).
CONAN_PYTHON="$(dirname -- "$(readlink -f -- "$(command -v conan)")")/python"
declared="$("$CONAN_PYTHON" - "$CLONE/packages.yml" "$PKG_NAME" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as handle:
    entry = (yaml.safe_load(handle) or {}).get("packages", {}).get(sys.argv[2])
if not entry:
    sys.exit(f"{sys.argv[2]} is not declared in {sys.argv[1]}")
print(entry["path"], entry["version"])
PY
)"
read -r PKG_PATH DECLARED_VERSION <<<"$declared"
if [[ "$DECLARED_VERSION" != "$PKG_VERSION" ]]; then
    echo "::error::ci_build_conan_package: the row asks $PKG_REF but $SOURCE_REPO main declares $PKG_NAME/$DECLARED_VERSION"
    exit 1
fi

echo "ci_build_conan_package: creating $PKG_REF from $SOURCE_REPO/$PKG_PATH"
conan create "$CLONE/$PKG_PATH" \
    --build=missing \
    --test-folder="" \
    --profile:host="$PROFILE" \
    --profile:build="$PROFILE" \
    --lockfile="$LOCKFILE"

echo "ci_build_conan_package: $PKG_REF built from source OK"
