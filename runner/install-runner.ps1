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

# The actions.runner.* Windows service(s) executing from $Directory, as an array. Identified
# by the binary path each one RUNS, never by its name: the runner sanitizes a service name
# by rules this script would otherwise have to reproduce, and a bare `actions.runner.*` glob
# would match a co-resident runner installed from another directory.
function Get-RunnerService {
    param([Parameter(Mandatory = $true)][string]$Directory)

    Get-CimInstance -ClassName Win32_Service -Filter "Name LIKE 'actions.runner.%'" |
        Where-Object {
            $_.PathName -and
            $_.PathName.IndexOf($Directory, [StringComparison]::OrdinalIgnoreCase) -ge 0
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

    # Named separately because gh's own 403 conflates them: being an org OWNER is not
    # enough if the stored token was granted without admin:org, and gh's first line
    # ("you must be an org admin") sends an owner looking at the wrong thing.
    if ($LASTEXITCODE -ne 0 -or -not $Token) {
        Fail @"
Could not mint a registration token for org '$Org' (gh exit $LASTEXITCODE).

gh's output above names the cause. The usual one is a stored token without the
admin:org scope, which an org OWNER hits too:

    gh auth refresh -h github.com -s admin:org

Otherwise mint the registration token yourself and pass it in:

    `$env:RUNNER_TOKEN='XXXX'
"@
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

# Resolved once, after creation: every service lookup below compares against this exact
# string, so a relative or trailing-slash -RunnerDir cannot make a service invisible.
$runnerDirFull = (Resolve-Path -LiteralPath $RunnerDir).ProviderPath.TrimEnd('\')

Set-Location $runnerDirFull

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
        Fail "Could not resolve the latest actions/runner version (no tag_name in the API reply - rate limited?)."
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
# 6) Remove any service already serving this runner directory
#
# What config.cmd --runasservice does when its service already exists is not something this
# script should depend on: whichever way it behaves, deleting first makes re-registration
# deterministic, and this is the second run of the installer on a working box — the normal
# case, not the exotic one.
# ---------------------------------------------------------------------------

$stale = @(Get-RunnerService -Directory $runnerDirFull)

foreach ($staleService in $stale) {
    Log "Removing the existing service '$($staleService.Name)'..."

    Stop-Service -Name $staleService.Name -Force -ErrorAction SilentlyContinue

    Invoke-Native 'sc.exe' @('delete', $staleService.Name)

    if ($LASTEXITCODE -ne 0) {
        Fail "sc.exe delete $($staleService.Name) failed ($LASTEXITCODE)"
    }
}

if ($stale.Count -gt 0) {
    # sc.exe delete only MARKS a service for deletion while any handle to it is still open,
    # and a create against a marked service fails with "marked for deletion". Wait the mark
    # out here rather than hand config.cmd that failure.
    $removalDeadlineSeconds = 30
    $removalDeadline = (Get-Date).AddSeconds($removalDeadlineSeconds)

    while (@(Get-RunnerService -Directory $runnerDirFull).Count -gt 0 -and
           (Get-Date) -lt $removalDeadline) {
        Start-Sleep -Seconds 1
    }

    $remaining = @(Get-RunnerService -Directory $runnerDirFull)

    if ($remaining.Count -gt 0) {
        Fail @"
Service '$($remaining[0].Name)' is still registered ${removalDeadlineSeconds}s after
sc.exe delete - Windows has it marked for deletion with a handle still open.

Close any Services console or running runner process and re-run the installer.
"@
    }

    Log "Existing service removed."
}

# ---------------------------------------------------------------------------
# 7) Configure runner
#
# On Windows --runasservice is the whole service lifecycle: config.cmd grants file
# permissions to the logon account, registers the service with sc.exe, sets its recovery
# options and delayed auto-start, and starts it. So there is no install step after this one
# — measured 2026-09-02 on the target host, where this single call printed "successfully
# installed" and "started successfully". --replace covers a runner already registered under
# the same name; step 6 covered the service.
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
# 8) Locate the Windows service that configuration created
#
# NOT by file, and NOT by name. `svc.cmd` and `.service` are the Linux/macOS svc.sh
# artifacts and are never written by the Windows layout — measured 2026-09-02, where a
# config.cmd run that printed "Service actions.runner.CodeRoasted.malf-runner-win
# successfully installed" and "started successfully" left neither file behind, so an
# earlier file-existence check here failed a working install after the fact.
#
# The service is identified by what it EXECUTES: the one actions.runner.* service whose
# binary path lies inside $RunnerDir. That holds under whatever sanitization the runner
# applies to the service name, and it cannot match a co-resident runner installed from a
# different directory — which a bare `actions.runner.*` glob would.
# ---------------------------------------------------------------------------

$candidates = @(Get-RunnerService -Directory $runnerDirFull)

if ($candidates.Count -eq 0) {
    Fail @"
config.cmd reported success, but no actions.runner.* Windows service executes from

    $runnerDirFull

The runner was NOT installed as a service. Refusing to continue.
"@
}

if ($candidates.Count -gt 1) {
    Fail @"
$($candidates.Count) actions.runner.* services execute from ${runnerDirFull}:

    $($candidates.Name -join "`n    ")

One runner directory serves one service. Remove the stale ones before re-running.
"@
}

$serviceName = $candidates[0].Name

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if (-not $service) {
    Fail "Win32_Service reports '$serviceName' but Get-Service cannot open it."
}

Log "Windows service: $serviceName"

# ---------------------------------------------------------------------------
# 9) Start the service if configuration left it stopped
#
# config.cmd starts it itself, so this is the path for a service that was already
# registered and down. Start-Service is the Windows primitive; there is no svc.cmd to call.
# ---------------------------------------------------------------------------

if ($service.Status -ne 'Running') {
    Log "Service is $($service.Status) - starting it..."
    Start-Service -Name $serviceName
}

# ---------------------------------------------------------------------------
# 10) Verify service state
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
# 11) Final state
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
