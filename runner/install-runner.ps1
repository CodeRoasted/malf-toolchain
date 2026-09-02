# install-runner.ps1 — register an ORG-level self-hosted *Windows* GitHub Actions runner
# (native Windows host, NOT WSL2) as a Windows SERVICE, so the PRIVATE eidos Windows
# Portability Probe stops consuming GitHub-hosted minutes. Linux twin: install-runner.sh.
#
# SECURITY (same rule as Linux): a self-hosted runner must NEVER serve a PUBLIC / fork-
# exposed repo — a fork PR would execute attacker code on this box. On GitHub Free an org
# runner is visible to ALL repos, so safety is enforced at the WORKFLOW layer: only the
# PRIVATE eidos windows-portability-probe carries
# `runs-on: ${{ vars.WIN_RUNS_ON || 'windows-2025' }}`; the canon + metalog Windows probes
# stay pinned to `windows-2025` (public = free + fork-safe). Do not add the `malf-windows`
# label to a public repo's workflow.
#
# A SERVICE, never a Scheduled Task and never a foreground helper. The task
# `\CodeRoast Runner Win` ran start-runner.ps1 over a \\wsl.localhost\… path at logon and
# died with exit 64 ERROR_NETNAME_DELETED: the launcher lived inside WSL while the task
# that needed it was a peer with no ordering. This installer therefore deletes that task,
# refuses a UNC runner directory, and hands the runner to the Windows Service Manager.
#
# Usage (elevated Windows PowerShell 5.1 or PowerShell 7, gh authed as an org admin):
#     pwsh -ExecutionPolicy Bypass -File malf\runner\install-runner.ps1
#   or with a token minted by hand:
#     $env:RUNNER_TOKEN='XXXX'; pwsh -ExecutionPolicy Bypass -File malf\runner\install-runner.ps1
#
# Toggle: set the org variable WIN_RUNS_ON=malf-windows to route the eidos Windows probe
# here; delete it to fall back to GitHub-hosted windows-2025.
#     gh variable set WIN_RUNS_ON --org CodeRoasted --body malf-windows --visibility private
#     gh variable delete WIN_RUNS_ON --org CodeRoasted

param(
    [string]$Org,
    [string]$Labels,
    [string]$RunnerName,
    [string]$RunnerDir,
    [string]$RunnerArch,
    [string]$Token,
    [string]$WindowsLogonAccount,
    [string]$WindowsLogonPassword,
    [string]$LegacyTaskName
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Defaults from environment / fallback values.
if (-not $Org) {
    $Org = if ($env:ORG) { $env:ORG } else { 'CodeRoasted' }
}

if (-not $Labels) {
    $Labels = if ($env:LABELS) { $env:LABELS } else { 'malf-windows' }
}

if (-not $RunnerName) {
    $RunnerName = if ($env:RUNNER_NAME) { $env:RUNNER_NAME } else { 'malf-runner-win' }
}

# Outside any user profile on purpose: the service runs as a machine account, and a
# profile-anchored _work tree ties the runner's lifetime to a user that may not be logged on.
if (-not $RunnerDir) {
    $RunnerDir = if ($env:RUNNER_DIR) {
        $env:RUNNER_DIR
    } else {
        'C:\actions-runner-malf-win'
    }
}

if (-not $RunnerArch) {
    $RunnerArch = if ($env:RUNNER_ARCH) { $env:RUNNER_ARCH } else { 'x64' }
}

if (-not $Token) {
    $Token = $env:RUNNER_TOKEN
}

if (-not $WindowsLogonAccount) {
    $WindowsLogonAccount = if ($env:WINDOWS_LOGON_ACCOUNT) {
        $env:WINDOWS_LOGON_ACCOUNT
    } else {
        'NT AUTHORITY\SYSTEM'
    }
}

# config.cmd's only password interface is a command-line argument, so a non-SYSTEM account's
# password is readable by any local user for the lifetime of that process (Win32_Process
# CommandLine). SYSTEM is the default precisely because it needs no password at all.
if (-not $WindowsLogonPassword) {
    $WindowsLogonPassword = $env:WINDOWS_LOGON_PASSWORD
}

if (-not $LegacyTaskName) {
    $LegacyTaskName = if ($env:LEGACY_TASK_NAME) {
        $env:LEGACY_TASK_NAME
    } else {
        'CodeRoast Runner Win'
    }
}

function Log([string]$Message) {
    Write-Host "[runner] $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw "[runner] $Message"
}

# Windows PowerShell 5.1 turns a native command's *stderr* into a terminating
# NativeCommandError while $ErrorActionPreference is 'Stop'. config.cmd and svc.cmd both
# write progress to stderr, so under 'Stop' they abort the script before their exit code is
# ever read, and the real failure is replaced by a wrapper error. Native calls therefore run
# under 'Continue' and are judged on $LASTEXITCODE alone.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FilePath @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
}

# ---------------------------------------------------------------------------
# 0) Preconditions
# ---------------------------------------------------------------------------

# $IsWindows exists only in PowerShell 6+; in Windows PowerShell 5.1 it is $null, and a bare
# `-not $IsWindows` there rejects the very host this installer targets. 5.1 ships on Windows
# and nowhere else, so an absent variable *is* the Windows answer.
$onWindows = if ($null -eq $IsWindows) { $true } else { $IsWindows }

if (-not $onWindows) {
    Fail "This installer must run on native Windows, not inside WSL."
}

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)

if (-not $currentPrincipal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    Fail "Run this script from an elevated PowerShell (Run as Administrator)."
}

if (-not $Token -and -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail "gh was not found and RUNNER_TOKEN is not set."
}

# Windows PowerShell 5.1 negotiates SSL3/TLS1.0 by default on unpatched hosts; github.com
# and api.github.com require TLS 1.2+, and the failure surfaces as a bare connection reset.
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------------------
# 1) Registration token
# ---------------------------------------------------------------------------

if (-not $Token) {
    Log "Minting an org registration token via gh (org: $Org)..."

    $Token = Invoke-Native gh @(
        'api'
        '-X', 'POST'
        "/orgs/$Org/actions/runners/registration-token"
        '-q', '.token'
    )

    if ($LASTEXITCODE -ne 0 -or -not $Token) {
        Fail "Could not mint a registration token. Is gh authenticated as an org admin?"
    }
}

# ---------------------------------------------------------------------------
# 2) Service account validation
# ---------------------------------------------------------------------------

if ($WindowsLogonAccount -ne 'NT AUTHORITY\SYSTEM' -and
    -not $WindowsLogonPassword) {

    Fail @"
A non-SYSTEM service account was requested:

    $WindowsLogonAccount

Set WINDOWS_LOGON_PASSWORD before running the installer.
"@
}

Log "Windows service account: $WindowsLogonAccount"

# ---------------------------------------------------------------------------
# 3) Runner directory
# ---------------------------------------------------------------------------

if ($RunnerDir -like '\\wsl.localhost\*' -or
    $RunnerDir -like '\\wsl$\*') {

    Fail "RunnerDir must be a native Windows path. WSL UNC paths are forbidden: $RunnerDir"
}

Log "Runner directory: $RunnerDir"

New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null

Set-Location $RunnerDir

# ---------------------------------------------------------------------------
# 4) Delete the legacy logon Scheduled Task
#
# It and the service would both claim the same runner registration at logon, and the task is
# the failing arrangement this installer replaces.
# ---------------------------------------------------------------------------

$legacyTask = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue

if ($legacyTask) {
    Log "Deleting the legacy logon Scheduled Task '$LegacyTaskName'..."
    Stop-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false
} else {
    Log "No legacy Scheduled Task '$LegacyTaskName' registered."
}

# ---------------------------------------------------------------------------
# 5) Download/extract runner if needed
# ---------------------------------------------------------------------------

$configCmd = Join-Path $RunnerDir 'config.cmd'

if (-not (Test-Path $configCmd)) {

    Log "Resolving latest actions/runner release..."

    $release = Invoke-RestMethod `
        -Uri 'https://api.github.com/repos/actions/runner/releases/latest' `
        -UseBasicParsing

    # An unauthenticated rate-limit reply is a 200 with a *different* JSON shape, so the
    # missing tag is checked before it is trimmed — otherwise the report is a null-method
    # error naming neither the rate limit nor the URL.
    if (-not $release.tag_name) {
        Fail "Could not resolve the latest actions/runner version (no tag_name in the API reply — rate limited?)."
    }

    $ver = $release.tag_name.TrimStart('v')

    $zip = "actions-runner-win-$RunnerArch-$ver.zip"
    $zipPath = Join-Path $RunnerDir $zip
    $downloadUrl =
        "https://github.com/actions/runner/releases/download/v$ver/$zip"

    Log "Downloading $zip..."

    Invoke-WebRequest `
        -Uri $downloadUrl `
        -OutFile $zipPath `
        -UseBasicParsing

    Log "Extracting runner..."

    Expand-Archive `
        -Path $zipPath `
        -DestinationPath $RunnerDir `
        -Force

    Remove-Item $zipPath -Force

    if (-not (Test-Path $configCmd)) {
        Fail "config.cmd is absent after extracting $zip."
    }

} else {

    Log "Runner already extracted in $RunnerDir."
}

# ---------------------------------------------------------------------------
# 6) Remove an existing service before reconfiguration
#
# This makes re-registration deterministic.
# ---------------------------------------------------------------------------

$svcCmd = Join-Path $RunnerDir 'svc.cmd'

if (Test-Path $svcCmd) {

    Log "Existing runner service script found — stopping and uninstalling it."

    # Both are best-effort: the service may be stopped, or never have been installed. The
    # exit code is read and discarded on purpose, so that a later real failure is not
    # attributed to this teardown.
    Invoke-Native $svcCmd @('stop') | Out-Null
    Invoke-Native $svcCmd @('uninstall') | Out-Null
}

# ---------------------------------------------------------------------------
# 7) Configure runner
# ---------------------------------------------------------------------------

Log "Configuring org runner '$RunnerName'..."
Log "Labels: $Labels"

$configArgs = @(
    '--url', "https://github.com/$Org"
    '--token', $Token
    '--name', $RunnerName
    '--labels', $Labels
    '--unattended'
    '--replace'
    '--runasservice'
    '--windowslogonaccount', $WindowsLogonAccount
)

if ($WindowsLogonAccount -ne 'NT AUTHORITY\SYSTEM') {
    $configArgs += @(
        '--windowslogonpassword', $WindowsLogonPassword
    )
}

Invoke-Native $configCmd $configArgs

if ($LASTEXITCODE -ne 0) {
    Fail "config.cmd failed ($LASTEXITCODE)"
}

# ---------------------------------------------------------------------------
# 8) Verify that service support was actually generated
#
# `.service` holds the Windows service name and is what svc.cmd itself reads; without it
# there is no service identity to install, start or check, whatever config.cmd reported.
# ---------------------------------------------------------------------------

$serviceNameFile = Join-Path $RunnerDir '.service'

if (-not (Test-Path $svcCmd) -or -not (Test-Path $serviceNameFile)) {
    Fail @"
Runner configuration succeeded, but svc.cmd and/or .service was not generated in

    $RunnerDir

The runner was NOT accepted as a service installation. Refusing to continue.
"@
}

$serviceName = (Get-Content -Path $serviceNameFile -Raw).Trim()

if (-not $serviceName) {
    Fail "$serviceNameFile is empty — no Windows service name to install."
}

Log "Service support generated: $serviceName"

# ---------------------------------------------------------------------------
# 9) Install the service if configuration did not already do it
#
# config.cmd --runasservice registers the service itself on Windows; svc.cmd install is the
# path for a runner configured without it. Installing twice fails on an existing service, so
# the service registry — not the configuration flag — decides which step is still owed.
# ---------------------------------------------------------------------------

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if (-not $service) {
    Log "Installing Windows service..."

    Invoke-Native $svcCmd @('install')

    if ($LASTEXITCODE -ne 0) {
        Fail "svc.cmd install failed ($LASTEXITCODE)"
    }

    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

    if (-not $service) {
        Fail "svc.cmd install reported success but service '$serviceName' does not exist."
    }
} else {
    Log "Windows service '$serviceName' already registered by config.cmd."
}

# ---------------------------------------------------------------------------
# 10) Start the service
# ---------------------------------------------------------------------------

if ($service.Status -ne 'Running') {
    Log "Starting Windows runner service..."

    Invoke-Native $svcCmd @('start')

    if ($LASTEXITCODE -ne 0) {
        Fail "svc.cmd start failed ($LASTEXITCODE)"
    }
}

# ---------------------------------------------------------------------------
# 11) Verify service state
#
# A bounded wait, not a fixed sleep: a service reporting StartPending is starting, and
# asserting Running against a single 2 s sample fails an install that is merely slow.
# ---------------------------------------------------------------------------

$startDeadlineSeconds = 60
$deadline = (Get-Date).AddSeconds($startDeadlineSeconds)

$service.Refresh()

while ($service.Status -ne 'Running' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $service.Refresh()
}

Log "Service: $($service.Name)"
Log "Status : $($service.Status)"

if ($service.Status -ne 'Running') {
    Fail "Runner service '$($service.Name)' is $($service.Status) after ${startDeadlineSeconds}s, not Running."
}

# ---------------------------------------------------------------------------
# 12) Final state
# ---------------------------------------------------------------------------

Write-Host ""
Log "============================================================"
Log "Runner installation complete."
Log "============================================================"
Log "Runner name : $RunnerName"
Log "Labels      : $Labels"
Log "Directory   : $RunnerDir"
Log "Service     : $serviceName (RUNNING)"
Log "Service user: $WindowsLogonAccount"
Write-Host ""
Log "The runner is now launched by the Windows Service Manager."
Log "Do NOT create a Scheduled Task for this runner."
Log "Do NOT launch start-runner.ps1 for this runner."
Log "Do NOT use a \\wsl.localhost path for the runner launcher."
Write-Host ""
Log "Route the eidos Windows probe here:"
Write-Host "  gh variable set WIN_RUNS_ON --org $Org --body $Labels --visibility private"
Log "Fall back to GitHub-hosted windows-2025:"
Write-Host "  gh variable delete WIN_RUNS_ON --org $Org"
Log "(Only the PRIVATE eidos probe reads WIN_RUNS_ON; canon/metalog stay on windows-2025.)"
Write-Host ""
