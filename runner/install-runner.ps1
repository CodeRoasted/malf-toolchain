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

    # A .cmd is not an executable: PowerShell hands it to cmd.exe, which RE-PARSES the command
    # line before the batch file ever sees it. An argument carrying `&` (or | < > ^ ( )) is
    # therefore split at that character and its tail is run as a COMMAND. Measured 2026-09-04
    # against a two-line echo harness: the value `abc&def` arrived as `abc`, and `def` was
    # executed -- the runner install died on `& était inattendu` from cmd.exe, in a script whose
    # own error text then blamed config.cmd. Passwords are where this bites, because they are the
    # one argument here whose alphabet nobody chose.
    #
    # Double quotes fix it: cmd does not interpret a metacharacter inside them, and both the
    # batch `%~n` expansion and Runner.Listener's argv parse strip them back off. Verified on
    # all three of the quoted, caret-escaped and Legacy-passing forms; quoting is kept as the
    # one that needs no per-character table.
    if ($FilePath -match '\.(cmd|bat)$') {
        $Arguments = $Arguments | ForEach-Object {
            if ($_ -match '"') {
                # Refused rather than mangled: escaping an embedded quote through PowerShell AND
                # cmd AND the batch re-parse has no spelling that survives all three, so a value
                # containing one would be silently corrupted into a wrong password and a failed
                # logon nobody would trace back here. The value itself is never printed.
                Fail "An argument passed to $FilePath contains a double quote, which cannot be passed through cmd.exe safely. Use a value without `" characters (a service account password is the usual source)."
            }
            if ($_ -match '[&|<>^()\s]') { '"' + $_ + '"' } else { $_ }
        }
    }

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

    # WHAT THIS CHECK IS WORTH — same reasoning as the Linux twin, kept here rather than
    # cross-referenced because whoever reads one of these scripts is on that box.
    # The digest comes from the same api.github.com response that named $downloadUrl, so it is
    # SELF-CERTIFYING: whoever can replace the asset can replace the digest with it, and TLS
    # already covers the wire. It is NOT a defence against a compromised upstream.
    # What it buys: Expand-Archive is never handed a truncated or CDN-corrupted body, and an
    # asset/URL mismatch stops here instead of half-populating $RunnerDir.
    # Corroboration is a SECOND PRODUCER: the `## SHA-256 Checksums` table that actions/runner's
    # release BUILD writes into the notes, versus the storage layer that computes
    # .assets[].digest. Agreement means a blob swap had to edit both. The notes are free-form
    # prose, so a parse MISS degrades to UNCHECKED and never fails the install — a working
    # runner box must not be hostage to an upstream markdown edit.
    # The wider hole is `releases/latest` — this box installs whatever shipped today — and the
    # Founder RULED 2026-09-02 to keep it: "latest is fine". Pinning the VERSION plus a reviewed
    # digest as constants here was the alternative, raised and declined for the maintenance
    # burden of manual bumps. DO NOT RE-PROPOSE IT. The residual is accepted, not overlooked:
    # what bounds the blast radius is README.md's workflow-layer rule — a self-hosted runner
    # never runs a public or fork-exposed repo — not this check.
    $asset = $release.assets | Where-Object { $_.name -eq $zip } | Select-Object -First 1
    # Lowercased on every side below. PowerShell's -eq/-ne on strings are case-INSENSITIVE, so
    # this comparison would pass without it — but a security comparison must not depend on that
    # default surviving someone later "tightening" it to -cne. Get-FileHash returns UPPERCASE.
    $wantSha = if ($asset -and $asset.digest) { ($asset.digest -replace '^sha256:', '').ToLowerInvariant() } else { $null }

    # Absent or malformed is a REFUSAL, never a skip: a soft-skip prints the same "Downloading"
    # in the world where the check works and the world where it silently stopped checking.
    if (-not ($wantSha -and $wantSha -match '^[0-9a-fA-F]{64}$')) {
        Fail "No sha256 digest published for asset '$zip' (RunnerArch=$RunnerArch) - refusing to download something that cannot be verified."
    }

    # Deliberately EXACT so an upstream format change misses and degrades to UNCHECKED, rather
    # than half-matching and killing a legitimate install.
    $notesLine = ($release.body -split "`n") |
        Where-Object { $_.StartsWith("- $zip ") } |
        Select-Object -First 1
    $notesSha = if ($notesLine -and $notesLine -match '\b([0-9a-fA-F]{64})\b') { $Matches[1].ToLowerInvariant() } else { $null }

    if (-not $notesSha) {
        Log "NOTE: release notes carry no SHA-256 line for $zip - corroboration UNCHECKED, continuing on the published asset digest alone."
    } elseif ($notesSha -ne $wantSha) {
        Fail "The release DISAGREES WITH ITSELF for ${zip}: asset digest $wantSha, release-notes checksum $notesSha. Two producers that should agree do not - refusing to download."
    }

    Log "Downloading $zip (expecting sha256 $wantSha)..."

    Invoke-WebRequest `
        -Uri $downloadUrl `
        -OutFile $zipPath `
        -UseBasicParsing

    $gotSha = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()

    if ($gotSha -ne $wantSha) {
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Fail "SHA-256 MISMATCH on ${zip} - expected $wantSha, got $gotSha. The download was deleted and nothing was extracted."
    }

    Log "SHA-256 verified ($wantSha)."

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
# 6) Unconfigure whatever is already installed here
#
# `--replace` covers only the SERVER-side name collision. A runner DIRECTORY that is already
# configured is refused outright — ConfigurationManager.ConfigureAsync throws "Cannot
# configure the runner because it is already configured" whenever `.runner` exists (measured
# on the target host, and the guard is ConfigurationManager.cs:124-127 at v2.337.0). So a
# second run of this installer on a working box — the normal case — must unconfigure first.
#
# `remove --local` is the token-free half of removal: Runner.cs:170-175 routes it straight to
# DeleteLocalRunnerConfig(), which deletes `.credentials`, the RSA key and `.runner` and
# returns success without contacting GitHub. Plain `remove` would instead need a *deletion*
# token, a second API call and a second scope; the server-side registration it would clean up
# is exactly what `--replace` overwrites on the next line anyway.
#
# It leaves the Windows service alone, which is why the sc.exe sweep below is a separate act
# and not a belt-and-braces duplicate.
# ---------------------------------------------------------------------------

$runnerConfigFile = Join-Path $runnerDirFull '.runner'

if (Test-Path -LiteralPath $runnerConfigFile) {
    Log "Runner directory is already configured - unconfiguring it locally..."

    Invoke-Native $configCmd @('remove', '--local')

    if ($LASTEXITCODE -ne 0) {
        Fail "config.cmd remove --local failed ($LASTEXITCODE)"
    }

    if (Test-Path -LiteralPath $runnerConfigFile) {
        Fail "config.cmd remove --local reported success but $runnerConfigFile is still present."
    }
}

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
# installed" and "started successfully". --replace covers the runner already registered
# SERVER-side under the same name; step 6 covered the local configuration and the service.
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
# NOT by file, and NOT by name. `svc.cmd` does not exist in the Windows layout at all — it
# is the Linux/macOS svc.sh wrapper — so an earlier check for it here failed a working
# install after the fact. (`.service` IS written on Windows, as a HIDDEN file, by
# WindowsServiceControlManager.SaveServiceSettings at v2.337.0; only svc.cmd was missing.
# It is still not what is read below: it holds a name, and a name cannot say which directory
# the service actually serves.)
#
# The service is identified by what it EXECUTES: the one actions.runner.* service whose
# binary path lies inside $RunnerDir. That holds under whatever sanitization the runner
# applies to the service name, it survives a `.service` deleted by hand, and it cannot match
# a co-resident runner installed from a different directory — which a bare
# `actions.runner.*` glob would.
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
# 10-bis) The service account can resolve the shells the workflows ask for
#
# WHY THIS EXISTS, measured 2026-09-04. Every `shell: pwsh` step in insight-eidos's
# golden.yaml died with "pwsh: command not found" on THIS runner, four days after the same
# runner had been green. Nothing was uninstalled: PowerShell 7.6.5 was present and working,
# as a PER-USER Store (Appx) package whose only entry point is the execution alias in
# %LOCALAPPDATA%\Microsoft\WindowsApps. That directory is on the interactive USER's PATH and
# is NOT on the machine PATH -- and step 2 above puts this service on a MACHINE account by
# design, whose environment is the machine PATH. So the install is fine, the account is fine,
# and the pairing is not.
#
# THE FAILURE MODE IS WHAT MAKES A CHECK WORTH ITS LINES, not the defect. Without it the
# install reports success, the service reports Running, and the break surfaces hours later
# inside somebody's job, on a step that names a compiler -- so it reads as a code defect on
# the branch under test. It cost exactly that: a probe branch's red was nearly filed as
# "MSVC rejects this shape" when MSVC had never been reached.
#
# Resolved against the MACHINE path only, deliberately: this script runs as the interactive
# user, and asking `Get-Command` here would answer for the WRONG account and pass. A hit
# inside a per-user WindowsApps directory is refused for the same reason -- it is exactly the
# shape that resolves for the installer and not for the service.
# ---------------------------------------------------------------------------

$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$machineDirs = $machinePath -split ';' | Where-Object { $_ }

function Resolve-ForServiceAccount {
    param([string]$Exe)
    foreach ($dir in $machineDirs) {
        $candidate = Join-Path $dir $Exe
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            if ($candidate -match '\\AppData\\Local\\Microsoft\\WindowsApps\\') { continue }
            return $candidate
        }
    }
    return $null
}

# `pwsh` only. Kept to what the workflows actually demand rather than a wishlist: a list that
# outgrows its evidence is the hand-kept enumeration this repo kills everywhere else.
$pwshPath = Resolve-ForServiceAccount 'pwsh.exe'

if ($null -eq $pwshPath) {
    $userVisible = $null
    try { $userVisible = (Get-Command pwsh -ErrorAction SilentlyContinue).Source } catch { }
    $diagnosis = if ($userVisible) {
        "It IS installed and resolvable for YOU, at '$userVisible' -- so this is a per-user " +
        "install (typically the Store/Appx package) that the '$WindowsLogonAccount' service " +
        "account cannot see."
    } else {
        "It is not resolvable for this user either."
    }
    Fail @"
The runner service account cannot resolve 'pwsh'.

$diagnosis

Every ``shell: pwsh`` step in a workflow that lands on this runner will fail with
"pwsh: command not found" -- AFTER a green install, inside a job, on a step that looks
like it is about something else.

Fix: install PowerShell 7 from the MSI, which is the only MACHINE-WIDE package -- it lands
under 'C:\Program Files\PowerShell\7' and puts itself on the machine PATH. Then re-run this
script:

    msiexec.exe /i https://github.com/PowerShell/PowerShell/releases/download/v7.6.5/PowerShell-7.6.5-win-x64.msi /qb
    (from an ELEVATED prompt; bump the version to the current release)

DO NOT reach for 'winget install --scope machine --id Microsoft.PowerShell'. Measured
2026-09-04: that id's manifest advertises an **msix** even under --scope machine, so winget
sees the per-user Store package already present, reports success, and changes nothing --
leaving this exact failure in place while looking like it was fixed.

Do NOT instead change the workflows to ``shell: powershell``: Windows PowerShell 5.1
ignores `$PSNativeCommandUseErrorActionPreference`, so native-command failures would stop
failing the step -- a leg that cannot run becomes a leg that cannot fail.

AND INSTALLING IT IS NOT ENOUGH ON ITS OWN -- re-running this script is the half that
finishes the job, which is why the line above says to. A Windows service captures its
environment block when it STARTS, so a machine PATH written afterwards never reaches the
running runner: pwsh then resolves perfectly for you at a prompt while every job still dies
on "command not found", and the box looks fixed. Measured 2026-09-04, one dispatch spent on
exactly that. Re-running this script recreates the service and the new environment comes
with it; an elevated 'Restart-Service <this runner service>' does the same thing faster.
"@
}

Log "Service account can resolve pwsh: $pwshPath"

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
