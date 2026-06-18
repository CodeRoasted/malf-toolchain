#!/usr/bin/env bash
# start-runner.sh — run the malf-local self-hosted runner in the FOREGROUND so you can
# watch jobs stream live and Ctrl+C to stop. Pairs with install-runner.sh.
#
#   malf/runner/start-runner.sh           # uses ~/actions-runner-malf
#   RUNNER_DIR=/path/to/runner malf/runner/start-runner.sh
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-malf}"

if [[ ! -x "$RUNNER_DIR/run.sh" ]]; then
  printf '\033[1;31m[runner] ERROR:\033[0m no runner at %s — run install-runner.sh first (or set RUNNER_DIR).\n' "$RUNNER_DIR" >&2
  exit 1
fi

cd "$RUNNER_DIR"
printf '\033[1;34m[runner]\033[0m starting in the foreground from %s\n' "$RUNNER_DIR"
printf '\033[1;34m[runner]\033[0m jobs stream below; press Ctrl+C to stop (the runner deregisters its session cleanly).\n'
# exec so Ctrl+C (SIGINT) goes straight to run.sh, which shuts the listener down gracefully.
exec ./run.sh
