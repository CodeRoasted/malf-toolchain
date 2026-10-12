#!/usr/bin/env bash
# Install CMake and Conan from their hash locks into a venv that dies with the job, and put only
# their launchers on the job's PATH. The ONE install path of both on every Linux leg:
# setup-build-env and setup-proof-linux call it.
#
#   tool-venv.sh <conan version>
#
# EVERY FILE PIP INSTALLS IS ONE OF THE DIGESTS cmake-4.3.1.txt AND conan-<version>.txt PIN
# (`--require-hashes`), AND NOTHING IS BUILT FROM SOURCE (`--only-binary=:all:`), so no unhashed
# build dependency is fetched either (W295, ROADMAP N281). The venv lives in RUNNER_TEMP: an install
# into the runner's own Python persists in its account, and on a self-hosted runner every later job
# of every repository executes it; on the release runner that account holds the store writer's
# signing key. This replaced conan-io/setup-conan in setup-proof-linux, whose `pip install --upgrade
# pip` and `pip install conan==<v>` took whatever PyPI served into whatever `python` came first on
# PATH, and the `sudo pip install cmake==4.3.1` both actions ran when the seat's cmake was not 4.3.x,
# which kept whichever 4.3.x a seat already had. cmake, ctest, cpack and conan are linked into a
# directory of their own, so the job's `python3` is still the runner's. A conan version with no
# lock refuses: there is no unhashed fallback. CMake 4.3.x because the import-std gate UUID
# 451f2fe2 lives only there.
# The venv is made by `python3 -S`: the interpreter that makes it may itself be a runner's
# persistent venv, whose site-packages' .pth files run at every start without -S (measured: a planted
# .pth ran twice under `-m venv`, never under `-S -m venv`, and the venv made was based on /usr).
set -euo pipefail

version="${1:?usage: tool-venv.sh <conan version>}"
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cmake_lock="$here/cmake-4.3.1.txt"
conan_lock="$here/conan-$version.txt"
for lock in "$cmake_lock" "$conan_lock"; do
    if [[ ! -f "$lock" ]]; then
        echo "::error::no hash lock at $lock: write it as conan-2.28.1.txt's header says, never install unhashed"
        exit 1
    fi
done
venv="$RUNNER_TEMP/build-tools-venv"
bin="$RUNNER_TEMP/build-tools-bin"
rm -rf "$venv" "$bin"
python3 -S -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check --no-input \
    --require-hashes --only-binary=:all: -r "$cmake_lock" -r "$conan_lock"
mkdir -p "$bin"
for tool in cmake ctest cpack conan; do
    ln -s "$venv/bin/$tool" "$bin/$tool"
done
echo "$bin" >> "$GITHUB_PATH"
banner="$("$bin/cmake" --version)"
case "${banner%%$'\n'*}" in
    *" 4.3."*) echo "${banner%%$'\n'*} ($bin/cmake)" ;;
    *) echo "::error::expected CMake 4.3.x from cmake-4.3.1.txt (got: ${banner%%$'\n'*})"; exit 1 ;;
esac
"$bin/conan" --version
