#!/usr/bin/env bash
# Builds every row of a modules manifest in order through conan-module's conan_module.sh — the
# single-sourced per-module logic — and stops at the first failing module, which the script has
# already post-mortemed. The `coderoast-ci` composite runs it twice: over `modules`, and over
# `late-modules` after the hub export.
#
#   build_modules.sh <profile> <<< "$MANIFEST"     # one row: <module> [test] [create] [cmake-args...]

set -euo pipefail

profile="${1:?usage: build_modules.sh <conan profile name> < manifest}"
conan_module="$(cd "$(dirname "${BASH_SOURCE[0]}")/../conan-module" && pwd)/conan_module.sh"

while IFS= read -r row; do
  row="${row%%#*}"
  # shellcheck disable=SC2086
  set -- $row
  [ "$#" -ge 1 ] || continue
  module="$1"; test="${2:-true}"; create="${3:-true}"
  shift $(( $# < 3 ? $# : 3 ))   # any remaining fields are extra cmake configure flags
  cmake_args="$*"
  echo "::group::coderoast-ci: module $module (test=$test create=$create${cmake_args:+ cmake:$cmake_args})"
  bash "$conan_module" "$module" "$test" "$create" "$profile" "$cmake_args"
  echo "::endgroup::"
done
