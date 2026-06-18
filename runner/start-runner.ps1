# start-runner.ps1 — run the malf-windows self-hosted runner in the FOREGROUND so you
# can watch jobs stream and Ctrl+C to stop. Pairs with install-runner.ps1.
#
#   pwsh malf\runner\start-runner.ps1
#   $env:RUNNER_DIR='C:\path\to\runner'; pwsh malf\runner\start-runner.ps1
param(
  [string]$RunnerDir = $(if ($env:RUNNER_DIR) { $env:RUNNER_DIR } else { Join-Path $env:USERPROFILE 'actions-runner-malf-win' })
)
$ErrorActionPreference = 'Stop'

$runCmd = Join-Path $RunnerDir 'run.cmd'
if (-not (Test-Path $runCmd)) {
  Write-Host "[runner] ERROR: no runner at $RunnerDir, run install-runner.ps1 first (or set RUNNER_DIR)." -ForegroundColor Red
  exit 1
}

Set-Location $RunnerDir
Write-Host "[runner] starting in the foreground from $RunnerDir" -ForegroundColor Cyan
Write-Host "[runner] jobs stream below; press Ctrl+C to stop (the runner deregisters its session cleanly)." -ForegroundColor Cyan
& $runCmd
