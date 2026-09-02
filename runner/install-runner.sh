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
# Two labels on this one box: `malf-local` routes general private-repo CI (org var
# CI_RUNS_ON); `corpora-runner` routes coderoast-corpora's longitudinal crawl (that
# repo's REPO var CORPUS_RUNNER=corpora-runner — kept distinct so the long crawl can
# move to a dedicated box later without disturbing general CI). config.sh --labels takes
# a comma list; baking both here is what survives a --replace re-register (an API-added
# label does not).
LABELS="${LABELS:-malf-local,corpora-runner}"
# NB: namespaced RUNNER_NAME, not NAME — `NAME` is commonly already exported in the
# environment (e.g. WSL2 sets NAME=<HOSTNAME>), which would silently override the default.
RUNNER_NAME="${RUNNER_NAME:-malf-runner}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-malf}"
RUNNER_ARCH="${RUNNER_ARCH:-x64}"   # x64 | arm64

log() { printf '\033[1;34m[runner]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[runner] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v curl      >/dev/null || die "curl is required"
command -v tar       >/dev/null || die "tar is required"
command -v jq        >/dev/null || die "jq is required — the release JSON is parsed for the download's SHA-256 (apt install jq)"
command -v sha256sum >/dev/null || die "sha256sum is required (coreutils) — the runner tarball is never unpacked unverified"

# 1) Registration token — minted via gh unless RUNNER_TOKEN is provided.
if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  command -v gh >/dev/null || die "gh not found and RUNNER_TOKEN unset — install gh or pass RUNNER_TOKEN"
  log "Minting an org registration token via gh (org: $ORG)…"
  RUNNER_TOKEN="$(gh api -X POST "/orgs/$ORG/actions/runners/registration-token" -q .token)" \
    || die "could not mint a token — is gh authed as an admin of org '$ORG'?"
fi

# 2) Download the runner once (skip if already extracted), and never unpack it unverified.
#
# WHAT THIS CHECK IS WORTH — stated here because a checksum invites more trust than this one
# earns, and over-trusting it is the real hazard. The digest comes from the same
# api.github.com response that names the download URL, so it is SELF-CERTIFYING: whoever can
# replace the release asset can replace the digest alongside it, and TLS already covers the
# wire. This is NOT a defence against a compromised upstream.
# What it does buy: it fails CLOSED on a truncated or CDN-corrupted body (a half-unpacked
# runner directory is worse than no runner) and on an asset/URL mismatch, instead of handing
# whatever arrived to `tar`.
# The corroboration is a SECOND PRODUCER. actions/runner's release notes carry a
# `## SHA-256 Checksums` table emitted by its release BUILD, while `.assets[].digest` is
# computed by the storage layer serving the bytes. Agreement means a blob swap had to edit
# both, through two different paths; disagreement is a loud stop. The notes are free-form
# prose, so a parse MISS degrades to UNCHECKED and never reds — failing the install on
# upstream's markdown would make a working box hostage to an editorial change, and these two
# boxes are ones the Founder depends on.
# THE WIDER HOLE IS `releases/latest` — this box installs whatever actions/runner published
# today, with no human in the loop — and the Founder RULED 2026-09-02 to keep it: *"latest is
# fine"*. Pinning the VERSION plus a reviewed digest as constants here was the alternative; it
# was raised, and it was declined for the maintenance burden of manual bumps on two boxes he
# depends on. DO NOT RE-PROPOSE IT. The residual risk is stated and accepted, not overlooked:
# a compromised upstream release reaches these boxes, and the control that bounds the blast
# radius is the workflow-layer rule in README.md — a self-hosted runner never runs a public or
# fork-exposed repo — not this check.
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
if [[ ! -x ./config.sh ]]; then
  log "Resolving the latest actions/runner release…"
  # ONE fetch, three reads off it: version, asset digest and notes checksum must describe the
  # SAME response — re-fetching would let the release move between reads. It also spends one
  # unauthenticated call rather than three, and the budget is 60/hour PER IP, shared with the
  # desk (this box is both).
  RELEASE_JSON="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)" \
    || die "could not read api.github.com/repos/actions/runner/releases/latest — rate limited? (unauthenticated: 60 requests/hour per IP, and the desk shares this box's IP)"

  VER="$(printf '%s' "$RELEASE_JSON" | jq -r '.tag_name // empty' | sed 's/^v//')"
  [[ -n "$VER" ]] || die "no tag_name in the release reply — rate limited, or the API shape changed"
  TARBALL="actions-runner-linux-${RUNNER_ARCH}-${VER}.tar.gz"

  # Primary: the digest GitHub publishes for the exact asset about to be downloaded. Absent or
  # malformed is a REFUSAL, never a skip — a soft-skip would print the same "downloading" in
  # the world where the check works and the world where it silently stopped checking.
  WANT="$(printf '%s' "$RELEASE_JSON" \
          | jq -r --arg n "$TARBALL" '.assets[] | select(.name == $n) | .digest // empty' \
          | sed 's/^sha256://')"
  [[ "$WANT" =~ ^[0-9a-f]{64}$ ]] \
    || die "no sha256 digest published for asset '$TARBALL' (RUNNER_ARCH=$RUNNER_ARCH) — refusing to download something that cannot be verified"

  # Corroboration from the release notes. The pattern is deliberately EXACT so that an upstream
  # format change misses and degrades to UNCHECKED, rather than half-matching and killing a
  # legitimate install.
  NOTES_SHA="$(printf '%s' "$RELEASE_JSON" \
               | jq -r --arg n "$TARBALL" '.body // "" | split("\n")[] | select(startswith("- " + $n + " "))' \
               | grep -oiE '\b[0-9a-f]{64}\b' | head -1 || true)"
  if [[ -z "$NOTES_SHA" ]]; then
    log "NOTE: release notes carry no SHA-256 line for $TARBALL — corroboration UNCHECKED, continuing on the published asset digest alone."
  elif [[ "${NOTES_SHA,,}" != "$WANT" ]]; then
    die "the release DISAGREES WITH ITSELF for $TARBALL: asset digest $WANT, release-notes checksum ${NOTES_SHA,,}. Two producers that should agree do not — refusing to download."
  fi

  log "Downloading $TARBALL (expecting sha256 $WANT)…"
  curl -fsSL -o "$TARBALL" \
    "https://github.com/actions/runner/releases/download/v${VER}/${TARBALL}" \
    || die "download failed for $TARBALL"

  GOT="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
  if [[ "$GOT" != "$WANT" ]]; then
    rm -f "$TARBALL"
    die "SHA-256 MISMATCH on $TARBALL — expected $WANT, got $GOT. The download was deleted and nothing was unpacked."
  fi
  log "SHA-256 verified ($WANT)."

  tar xzf "$TARBALL"
  rm -f "$TARBALL"
else
  log "Runner already extracted in $RUNNER_DIR — reconfiguring."
fi

# 3) Configure against the ORG (idempotent via --replace), with the malf-local label.
log "Configuring org runner '$RUNNER_NAME' (labels: $LABELS)…"
./config.sh \
  --url "https://github.com/$ORG" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$LABELS" \
  --unattended \
  --replace

# 4) Optionally install as a background service (opt-in). Default is FOREGROUND so you
#    run it from a terminal with start-runner.sh and Ctrl+C to stop — clearer on WSL2,
#    where the systemd-based svc.sh is unreliable.
if [[ "${AS_SERVICE:-false}" == "true" ]]; then
  log "AS_SERVICE=true → installing + starting the background service…"
  sudo ./svc.sh install
  sudo ./svc.sh start
  sudo ./svc.sh status || true
  log "Service installed. Stop/remove: sudo ./svc.sh stop && sudo ./svc.sh uninstall && ./config.sh remove --token <removal-token>"
fi

cat <<EOF

[runner] Registered '$RUNNER_NAME' (labels: $LABELS) in $RUNNER_DIR. Next:
  Start it in the foreground (watch jobs live; Ctrl+C to stop):
    $(dirname "$0")/start-runner.sh
  Route private CI + release to this box:
    gh variable set CI_RUNS_ON --org $ORG --body malf-local --visibility private
  (Corpus crawl routes separately via coderoast-corpora's repo var CORPUS_RUNNER=corpora-runner.)
  Fall back to GitHub-hosted:
    gh variable delete CI_RUNS_ON --org $ORG
  (Only PRIVATE-repo workflows read CI_RUNS_ON; public repos stay on ubuntu-latest.)
EOF
