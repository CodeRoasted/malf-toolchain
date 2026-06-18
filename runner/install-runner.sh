#!/usr/bin/env bash
# install-runner.sh — register an ORG-level self-hosted GitHub Actions runner that
# serves CodeRoast's PRIVATE repos, so their CI + release-publish stop consuming
# GitHub-hosted minutes (self-hosted runners are unmetered).
#
# ⛔ SECURITY (read once): self-hosted runners must NEVER run a PUBLIC / fork-exposed
#    repo — a fork PR would execute attacker code on this box. On GitHub Free an org
#    runner is visible to ALL repos, so safety is enforced at the WORKFLOW layer: only
#    PRIVATE repos carry `runs-on: ${{ vars.CI_RUNS_ON || 'ubuntu-latest' }}`; every
#    public repo (canon, metalog, ipc, web, sift-action, malf-toolchain) stays pinned
#    to ubuntu-latest and can never target this runner. Do not add the `malf-local`
#    label to a public repo's workflow.
#
# Usage (on the warehouse box, while `gh` is authenticated as an org admin):
#     ./install-runner.sh                 # mints an org registration token via gh
#   or, with a token you minted yourself:
#     RUNNER_TOKEN=XXXX ./install-runner.sh
#
# Toggle (after the runner is up): set the org variable CI_RUNS_ON=malf-local to route
# every private repo's CI + release to this box; unset it to fall back to GitHub-hosted.
#     gh variable set CI_RUNS_ON --org CodeRoasted --body malf-local --visibility private
#     gh variable delete CI_RUNS_ON --org CodeRoasted
set -euo pipefail

ORG="${ORG:-CodeRoasted}"
LABELS="${LABELS:-malf-local}"
NAME="${NAME:-$(hostname)-malf}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-malf}"
RUNNER_ARCH="${RUNNER_ARCH:-x64}"   # x64 | arm64

log() { printf '\033[1;34m[runner]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[runner] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl is required"
command -v tar  >/dev/null || die "tar is required"

# 1) Registration token — minted via gh unless RUNNER_TOKEN is provided.
if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  command -v gh >/dev/null || die "gh not found and RUNNER_TOKEN unset — install gh or pass RUNNER_TOKEN"
  log "Minting an org registration token via gh (org: $ORG)…"
  RUNNER_TOKEN="$(gh api -X POST "/orgs/$ORG/actions/runners/registration-token" -q .token)" \
    || die "could not mint a token — is gh authed as an admin of org '$ORG'?"
fi

# 2) Download the runner once (skip if already extracted).
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
if [[ ! -x ./config.sh ]]; then
  log "Resolving the latest actions/runner release…"
  VER="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
         | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)"
  [[ -n "$VER" ]] || die "could not resolve the latest runner version"
  TARBALL="actions-runner-linux-${RUNNER_ARCH}-${VER}.tar.gz"
  log "Downloading $TARBALL…"
  curl -fsSL -o "$TARBALL" \
    "https://github.com/actions/runner/releases/download/v${VER}/${TARBALL}"
  tar xzf "$TARBALL"
  rm -f "$TARBALL"
else
  log "Runner already extracted in $RUNNER_DIR — reconfiguring."
fi

# 3) Configure against the ORG (idempotent via --replace), with the malf-local label.
log "Configuring org runner '$NAME' (labels: $LABELS)…"
./config.sh \
  --url "https://github.com/$ORG" \
  --token "$RUNNER_TOKEN" \
  --name "$NAME" \
  --labels "$LABELS" \
  --unattended \
  --replace

# 4) Install + start as a service so it survives reboots (run.sh for a foreground run).
if [[ "${AS_SERVICE:-true}" == "true" ]]; then
  log "Installing + starting the runner service…"
  sudo ./svc.sh install
  sudo ./svc.sh start
  sudo ./svc.sh status || true
  log "Service installed. Stop/remove later with: sudo ./svc.sh stop && sudo ./svc.sh uninstall && ./config.sh remove --token <removal-token>"
else
  log "AS_SERVICE=false → start it in the foreground with: ./run.sh"
fi

cat <<EOF

[runner] Done. Next:
  Route private CI + release to this box:
    gh variable set CI_RUNS_ON --org $ORG --body $LABELS --visibility private
  Fall back to GitHub-hosted:
    gh variable delete CI_RUNS_ON --org $ORG
  (Only PRIVATE-repo workflows read CI_RUNS_ON; public repos stay on ubuntu-latest.)
EOF
