#!/usr/bin/env bash
# Install malf's home files into a conan home BY MALF'S OWN SYNC, never by a second copy of it:
# global.conf, settings_user.yml, the path-map fragment, the two recipe helpers, and EVERY profile
# of malf's registry (`_malf_sync_conan_conf`, malf acb4654).
#
#   install-home.sh <conan home>
#
# WHY EVERY PROFILE, AND NOT THE JOB'S ONE (DN-142.D23). Inside a create, malf's test helper checks
# each skip declaration's profile names against the `profiles/` of the home it was staged into. A
# home holding the job's profile alone fails every create whose tests run for a package declaring a
# skip on another registry profile — logcraft_core's `linux-clang21-asan` row, found by the W500
# cold walk. A cache-restored home may also carry stale copies; the sync overwrites any file that
# differs from this revision's.
#
# WHY SOURCE MALF. The sync is a malf function; this tree beside the action IS malf-toolchain at the
# revision the caller's `uses:` pins, and `MALF_SOURCE_ONLY=1` stops malf before it dispatches.
set -euo pipefail

export CONAN_HOME="${1:?usage: install-home.sh <conan home>}"
toolchain="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=/dev/null
MALF_SOURCE_ONLY=1 source "$toolchain/malf"
_malf_sync_conan_conf
echo "install-home: $CONAN_HOME holds malf's home files and its $(find "$CONAN_HOME/profiles" -maxdepth 1 -type f | wc -l) profile(s) from $toolchain"
