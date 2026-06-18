# install-runner.ps1 — register an ORG-level self-hosted *Windows* GitHub Actions runner
# (native Windows host, NOT WSL2) so the PRIVATE eidos Windows Portability Probe stops
# consuming GitHub-hosted minutes. The Linux counterpart is install-runner.sh.
#
# SECURITY (same rule as Linux): a self-hosted runner must NEVER serve a PUBLIC / fork-
# exposed repo. Only the PRIVATE eidos windows-portability-probe carries
# `runs-on: ${{ vars.WIN_RUNS_ON || 'windows-2025' }}`; canon + metalog Windows probes
# stay pinned to `windows-2025` (public = free + fork-safe). Do not add the malf-windows
# label to a public repo's workflow.
#
# Usage (in a Windows PowerShell on the host, with gh authenticated as an org admin):
#     pwsh malf\runner\install-runner.ps1          # mints an org token via gh
#     pwsh malf\runner\start-runner.ps1            # run it foreground; Ctrl+C to stop
#   or pass a token: $env:RUNNER_TOKEN='XXXX'; pwsh malf\runner\install-runner.ps1
#
# Toggle: set the org variable WIN_RUNS_ON=malf-windows to route the eidos Windows probe
# here; delete it to fall back to GitHub-hosted windows-2025.
#     gh variable set WIN_RUNS_ON --org CodeRoasted --body malf-windows --visibility private
#     gh variable delete WIN_RUNS_ON --org CodeRoasted
param(
  [string]$Org        = $(if ($env:ORG)         { $env:ORG }         else { 'CodeRoasted' }),
  [string]$Labels     = $(if ($env:LABELS)      { $env:LABELS }      else { 'malf-windows' }),
  [string]$RunnerName = $(if ($env:RUNNER_NAME) { $env:RUNNER_NAME } else { 'malf-runner-win' }),
  [string]$RunnerDir  = $(if ($env:RUNNER_DIR)  { $env:RUNNER_DIR }  else { Join-Path $env:USERPROFILE 'actions-runner-malf-win' }),
  [string]$RunnerArch = $(if ($env:RUNNER_ARCH) { $env:RUNNER_ARCH } else { 'x64' }),
  [string]$Token      = $env:RUNNER_TOKEN,
  [switch]$AsService  = ($env:AS_SERVICE -eq 'true')
)
$ErrorActionPreference = 'Stop'
function Log($m) { Write-Host "[runner] $m" -ForegroundColor Cyan }

# 1) Registration token — minted via gh unless RUNNER_TOKEN was provided.
if (-not $Token) {
  if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "gh not found and RUNNER_TOKEN unset, install gh or pass a token" }
  Log "Minting an org registration token via gh (org: $Org)…"
  $Token = (gh api -X POST "/orgs/$Org/actions/runners/registration-token" -q .token)
  if (-not $Token) { throw "could not mint a token, is gh authed as an admin of org '$Org'?" }
}

# 2) Download the runner once (skip if already extracted).
New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null
Set-Location $RunnerDir
if (-not (Test-Path (Join-Path $RunnerDir 'config.cmd'))) {
  Log "Resolving the latest actions/runner release…"
  $ver = ((Invoke-RestMethod 'https://api.github.com/repos/actions/runner/releases/latest').tag_name).TrimStart('v')
  if (-not $ver) { throw "could not resolve the latest runner version" }
  $zip = "actions-runner-win-$RunnerArch-$ver.zip"
  Log "Downloading $zip…"
  Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v$ver/$zip" -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath $RunnerDir -Force
  Remove-Item $zip -Force
} else {
  Log "Runner already extracted in $RunnerDir, reconfiguring."
}

# 3) Configure against the ORG (idempotent via --replace), with the malf-windows label.
Log "Configuring org runner '$RunnerName' (labels: $Labels)…"
& .\config.cmd --url "https://github.com/$Org" --token $Token --name $RunnerName --labels $Labels --unattended --replace
if ($LASTEXITCODE -ne 0) { throw "config.cmd failed ($LASTEXITCODE)" }

# 4) Optional background service (opt-in; needs an elevated shell). Default = foreground.
if ($AsService) {
  Log "AS_SERVICE=true → installing + starting the Windows service (requires admin)…"
  & .\svc.cmd install
  & .\svc.cmd start
}

Write-Host ""
Log "Registered '$RunnerName' (labels: $Labels) in $RunnerDir. Next:"
Write-Host "  Start it foreground (watch jobs; Ctrl+C to stop):  pwsh $(Split-Path $PSCommandPath)\start-runner.ps1"
Write-Host "  Route the eidos Windows probe here:                gh variable set WIN_RUNS_ON --org $Org --body $Labels --visibility private"
Write-Host "  Fall back to GitHub-hosted:                        gh variable delete WIN_RUNS_ON --org $Org"
Write-Host "  (Only the PRIVATE eidos probe reads WIN_RUNS_ON; canon/metalog stay on windows-2025.)"
