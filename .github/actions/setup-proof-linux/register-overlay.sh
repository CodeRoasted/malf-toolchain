#!/usr/bin/env bash
# Register malf's recipe overlay as the leg home's FIRST remote, `malf-overlay`, by malf's own
# `_malf_sync_overlay_remote` — never a second copy of it (DN-142.D17 (8)).
#
#   register-overlay.sh <conan home> <malf checkout>
#
# WHY A PROOF LEG NEEDS IT. malf's conan.lock pins three overlay revisions (autoconf, libpq,
# drogon); a home without the remote cannot resolve them, and the strict lock refuses naming the
# first it meets (measured 2026-10-11: `Unable to find 'drogon/1.9.13#28d7de5b…'`). The overlay is
# taken from the leg's own malf checkout, the one whose conan.lock the leg resolves.
#
# WHY A SCRATCH HOME FOR THE SOURCE. Sourcing malf runs its whole home sync at load; this leg is
# RAW (global.conf only on request), so the load-time sync is pointed at a throwaway home and only
# the remote function touches the leg's.
set -euo pipefail

home="${1:?usage: register-overlay.sh <conan home> <malf checkout>}"
malf="$(cd "${2:?usage: register-overlay.sh <conan home> <malf checkout>}" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
# shellcheck source=/dev/null
CONAN_HOME="$scratch" MALF_SOURCE_ONLY=1 source "$malf/malf"
mkdir -p "$home"
CONAN_HOME="$home" _malf_sync_overlay_remote
echo "register-overlay: $home resolves malf-overlay ($malf/overlay) first"
