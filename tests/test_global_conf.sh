#!/usr/bin/env bash
# malf's global.conf renders the way conan renders it (jinja2 with `os`, `platform` and
# `conan_home_folder`) both with HOME set and with HOME unset, as on a Windows runner, and keys the
# source backup off the user's home either way, unless malf names it by its absolute path
# (MALF_CONAN_SOURCES_CACHE, DN-142.D30 (9) R8). Run: bash tests/test_global_conf.sh
# Its own file while tests/test_malf.sh is held by another lane; it joins that file afterwards.
set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0 passed=0
check() {   # <label> <expected> <actual>
    if [[ "$2" == "$3" ]]; then passed=$((passed + 1)); echo "  ok   $1"
    else fail=$((fail + 1)); echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fi
}
render() {   # <HOME value or empty to unset> [<MALF_CONAN_SOURCES_CACHE>] -> the rendered download_cache line, or the error
    local env_home=(env -u MALF_CONAN_SOURCES_CACHE)
    [[ -n "$1" ]] && env_home+=("HOME=$1") || env_home+=(-u HOME)
    [[ -z "${2:-}" ]] || env_home+=("MALF_CONAN_SOURCES_CACHE=$2")
    "${env_home[@]}" python3 - "$root/global.conf" <<'PY' 2>&1
import os, platform, sys
from jinja2 import Environment
text = open(sys.argv[1]).read()
try:
    out = Environment().from_string(text).render({"platform": platform, "os": os, "distro": None,
                                                  "conan_home_folder": "/h"})
except Exception as error:
    print(f"RENDER FAILED: {type(error).__name__}: {error}")
    sys.exit(0)
print(next(line for line in out.splitlines() if line.startswith("core.sources:download_cache=")))
PY
}
check "with HOME set, the source backup sits under HOME" \
      "core.sources:download_cache=/tmp/fixture-home/.conan2-sources-backup" "$(render /tmp/fixture-home)"
check "with HOME unset (a Windows runner), global.conf still renders, under the user's home" \
      "core.sources:download_cache=$(python3 -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')/.conan2-sources-backup" \
      "$(render '')"
check "RED: named by malf, the source backup is that absolute path whatever the act's HOME (DN-142.D30 (9) R8)" \
      "core.sources:download_cache=/srv/sources-backup" "$(render /tmp/an-act-home /srv/sources-backup)"
echo "global-conf selftest: $passed passed, $fail failed"
[[ $fail -eq 0 ]]
