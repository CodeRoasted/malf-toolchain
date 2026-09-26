# isolate-runner-windows.ps1 - move the Windows self-hosted runner (malf-runner-win) off the
# Founder's own account onto its service's VIRTUAL account, NT SERVICE\<service> - an account with
# no password to hand around, not an administrator, with a profile of its own (ROADMAP N214).
#
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows.ps1          # PLAN only
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows.ps1 -Apply   # do it
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 # undo it
# From an ELEVATED PowerShell (Run as Administrator).
#
# WHAT A JOB COULD DO BEFORE THIS, measured 2026-09-26 on this host: the service logged on as the
# desk account, which is a member of Administrators, and a service token is never UAC-filtered. So
# every job ran as the Founder with full administrative rights - his %USERPROFILE%\.ssh key, the
# Credential Manager entries git:https://github.com and gh:github.com, the Windows gh config, and
# `wsl.exe -u root` into his WSL distro. The virtual account holds none of that.
#
# WHAT THE JOBS STILL NEED, and how each is given WITHOUT reopening the Founder's profile:
#   * Python + pip: every Windows job runs `python -m pip install cmake ninja conan`. Python was a
#     PER-USER install in the Founder's profile, invisible to any other account. A runner-owned
#     copy is unpacked from the NuGet `python` package (a relocatable build, no installer, no
#     registry), pinned by SHA-256 and SHA-512 from two producers, signature checked.
#   * MSVC 14.52: setup-msvc1452 looks under %LOCALAPPDATA%\malf-msvc1452 and installs there when
#     absent - which needs elevation a service cannot get. The runner's LOCALAPPDATA is pointed at a
#     runner-owned directory holding a junction to the EXISTING install, and the virtual account is
#     granted read+execute on that one directory (traversal needs no grant: every account holds
#     SeChangeNotifyPrivilege).
#   * pwsh 7 and git: already machine-wide on this host (checked below; the installer's own step
#     10-bis explains why a per-user pwsh fails a service account).
#
# IDEMPOTENT; REFUSES, changing nothing, on anything it did not expect: a job running, a service in
# neither the before nor the after shape, a missing MSVC install, a pin that does not verify.
# Uses SIDs, never group NAMES: this host's Windows is localized (Administrateurs, Utilisateurs).

param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RunnerDir    = 'C:\actions-runner-malf-win'
$DataRoot     = 'C:\malf-runner-win'
$PythonDir    = Join-Path $DataRoot 'python312'
$LocalAppData = Join-Path $DataRoot 'LocalAppData'
$StateDir     = Join-Path $DataRoot 'isolation'
$MsvcName     = 'malf-msvc1452'

# NuGet `python` 3.12.10 - the version the Founder's per-user Python runs, so CI resolves the same
# interpreter it resolved until now. TWO PRODUCERS, as the runner installer does for its own
# download: the SHA-512 is nuget.org's catalog entry (packageHash, published 2025-04-08T13:56:44Z),
# the SHA-256 was measured on the downloaded bytes on 2026-09-26, and the two describe the same
# 14 515 433 bytes. python.exe inside is Authenticode-signed by the Python Software Foundation,
# checked after unpacking.
$PyUrl    = 'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg'
$PySha256 = '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8'
$PySha512 = 'u9pNz2iKlCEbYtUJaKkbOPMF0LjR7NkCafdKhvigpPzrt8oWKgdTpHaR6z3wyWQAm9PYGUxv0Zr66NX9AeHMDw=='
$PySigner = 'CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US'
$PyVersion = 'Python 3.12.10'

$SidSystem = 'S-1-5-18'
$SidAdmins = 'S-1-5-32-544'

function Say([string]$m)  { Write-Host "[isolate] $m" -ForegroundColor Cyan }
function Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Refuse([string]$m) { throw "[isolate] REFUSED: $m" }

# Native commands are judged on their exit code; PowerShell 5.1 turns their stderr into a
# terminating error under 'Stop' (the installer's Invoke-Native says why).
function Native([string]$exe, [string[]]$argv) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @argv } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { Refuse "$exe $($argv -join ' ') exited $LASTEXITCODE" }
}

# --- Preflight - reads only ---------------------------------------------------------------------

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Refuse 'run this from an ELEVATED PowerShell (Run as Administrator)'
}
if (-not (Test-Path -LiteralPath (Join-Path $RunnerDir '.runner'))) { Refuse "$RunnerDir is not a configured runner (.runner missing)" }

# The service is identified by the binary it EXECUTES, never by a name glob (install-runner.ps1,
# Get-RunnerService, says why).
$svc = @(Get-CimInstance -ClassName Win32_Service | Where-Object {
    $_.Name -like 'actions.runner.*' -and $_.PathName -and
    $_.PathName.IndexOf($RunnerDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
if ($svc.Count -ne 1) { Refuse "expected exactly one actions.runner.* service executing from $RunnerDir, found $($svc.Count)" }
$svc = $svc[0]
$ServiceName = $svc.Name
$Virtual = "NT SERVICE\$ServiceName"
$VirtualSid = (New-Object Security.Principal.NTAccount($Virtual)).Translate([Security.Principal.SecurityIdentifier]).Value

$manifestPath = Join-Path $StateDir 'manifest.json'
if ($svc.StartName -ieq $Virtual) {
    $State = 'applied'
    if (-not (Test-Path -LiteralPath $manifestPath)) { Refuse "the service already logs on as $Virtual but $manifestPath is absent - not this script's work" }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $DeskAccount = $manifest.DeskAccount
} elseif (Test-Path -LiteralPath $manifestPath) {
    # A run that stopped after step 1: the manifest holds the pre-isolation state and is never re-recorded.
    $State = 'partial'
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.DeskAccount -ine $svc.StartName) { Refuse "the service logs on as $($svc.StartName) but $manifestPath records $($manifest.DeskAccount) - not this script's work" }
    $DeskAccount = $manifest.DeskAccount
} else {
    $State = 'fresh'
    $DeskAccount = $svc.StartName
    if ($DeskAccount -in @('LocalSystem', 'NT AUTHORITY\SYSTEM', 'NT AUTHORITY\LocalService', 'NT AUTHORITY\NetworkService')) {
        Refuse "the service logs on as $DeskAccount, not a desk account - this script moves a runner off a HUMAN account; to move it off $DeskAccount, change the logon by hand with the same grants"
    }
}
$deskName = $DeskAccount -replace '^\.\\', "$env:COMPUTERNAME\"
$DeskSid = (New-Object Security.Principal.NTAccount($deskName)).Translate([Security.Principal.SecurityIdentifier]).Value
$DeskProfile = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$DeskSid").ProfileImagePath
$MsvcSource = Join-Path $DeskProfile "AppData\Local\$MsvcName"
if (-not (Test-Path -LiteralPath (Join-Path $MsvcSource 'VC\Tools\MSVC'))) {
    Refuse "no MSVC install at $MsvcSource - setup-msvc1452 needs elevation to install one, which the virtual account will not have. Provision it first (one run of the probe job under the current account does it), then re-run"
}

# A job must not be running: the logon change restarts the service.
$worker = @(Get-Process -Name 'Runner.Worker' -ErrorAction SilentlyContinue)
if ($worker.Count -gt 0) { Refuse "a job is running (Runner.Worker pid $($worker.Id -join ', ')) - let it finish, then re-run" }

# What the jobs resolve from the MACHINE path, the only path the virtual account has.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
function Resolve-Machine([string]$exe) {
    foreach ($dir in ($machinePath -split ';' | Where-Object { $_ })) {
        $c = Join-Path $dir $exe
        if ((Test-Path -LiteralPath $c -PathType Leaf) -and $c -notmatch '\\AppData\\Local\\Microsoft\\WindowsApps\\') { return $c }
    }
    return $null
}
foreach ($exe in 'pwsh.exe', 'git.exe') {
    if (-not (Resolve-Machine $exe)) { Refuse "$exe is not on the MACHINE path - the virtual account could not run it (install-runner.ps1 step 10-bis names the fix for pwsh)" }
}
if ($machinePath -match '\\Users\\') { Refuse "the machine PATH names a directory under a user profile - the runner's PATH is built from it: $machinePath" }

@"

[isolate] state: $State
  service           $ServiceName  (logon now: $($svc.StartName))
  new logon         $Virtual  (SID $VirtualSid) - no password, not an administrator
  runner directory  $RunnerDir - owner Administrators; SYSTEM and Administrators full, $Virtual modify;
                    inheritance from C:\ cut (it gave Authenticated Users MODIFY on the runner's own binaries)
  Python            $PythonDir - NuGet python 3.12.10, SHA-256 + SHA-512 pinned, signer checked
  LOCALAPPDATA      $LocalAppData, holding a junction $MsvcName -> $MsvcSource
                    ($Virtual granted read+execute on that directory only)
  runner .env       LOCALAPPDATA and PATH ($PythonDir, its Scripts, then the machine PATH)
  kept              the runner registration (no re-register: its RSA key is DPAPI LocalMachine-scoped)
"@ | Write-Host

if (-not $Apply) { Write-Host "`n[isolate] PLAN ONLY - nothing was changed. Re-run with -Apply to do it."; exit 0 }

# --- Apply --------------------------------------------------------------------------------------

Step "1/6 stop the service and record the rollback manifest"
Stop-Service -Name $ServiceName -Force
(Get-Service -Name $ServiceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
if ($State -eq 'fresh') {
    $envFile = Join-Path $RunnerDir '.env'
    $envOrig = if (Test-Path -LiteralPath $envFile) { Get-Content -Raw -LiteralPath $envFile } else { '' }
    Set-Content -LiteralPath (Join-Path $StateDir 'env.orig') -Value $envOrig -NoNewline
    $aclSave = Join-Path $StateDir 'runner-acl.icacls'
    Native 'icacls.exe' @($RunnerDir, '/save', $aclSave, '/T', '/C', '/Q')
    $sidType = ((sc.exe qsidtype $ServiceName) -match 'SERVICE_SID_TYPE' | Select-Object -First 1) -replace '.*:\s*', ''
    @{ DeskAccount = $svc.StartName; ServiceName = $ServiceName; SidType = $sidType.Trim();
       MsvcSource = $MsvcSource; Applied = (Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -LiteralPath $manifestPath
}
Say "service stopped; rollback manifest in $StateDir"

Step "2/6 data root $DataRoot - Administrators and SYSTEM full, $Virtual modify, nothing inherited"
New-Item -ItemType Directory -Force -Path $DataRoot, $LocalAppData | Out-Null
Native 'icacls.exe' @($DataRoot, '/setowner', "*$SidAdmins", '/Q')
Native 'icacls.exe' @($DataRoot, '/inheritance:r', '/grant:r',
    "*${SidSystem}:(OI)(CI)F", "*${SidAdmins}:(OI)(CI)F", "*${VirtualSid}:(OI)(CI)M", '/Q')
# The rollback manifest and the saved ACLs are the Founder's, not the runner's.
Native 'icacls.exe' @($StateDir, '/inheritance:r', '/grant:r', "*${SidSystem}:(OI)(CI)F", "*${SidAdmins}:(OI)(CI)F", '/Q')

Step "3/6 Python 3.12.10 at $PythonDir"
$py = Join-Path $PythonDir 'python.exe'
$have = if (Test-Path -LiteralPath $py) { (& $py --version) 2>&1 } else { '' }
if ($have -ne $PyVersion) {
    $nupkg = Join-Path $env:TEMP 'python.3.12.10.nupkg.zip'
    Invoke-WebRequest -Uri $PyUrl -OutFile $nupkg -UseBasicParsing
    $got256 = (Get-FileHash -LiteralPath $nupkg -Algorithm SHA256).Hash.ToLowerInvariant()
    $sha = [Security.Cryptography.SHA512]::Create()
    $stream = [IO.File]::OpenRead($nupkg)
    try { $got512 = [Convert]::ToBase64String($sha.ComputeHash($stream)) } finally { $stream.Dispose() }
    if ($got256 -ne $PySha256 -or $got512 -ne $PySha512) {
        Remove-Item -LiteralPath $nupkg -Force
        Refuse "the Python package does not match its pins (sha256 $got256, sha512 $got512) - deleted, nothing unpacked"
    }
    $unpack = Join-Path $env:TEMP 'python.3.12.10.unpack'
    if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
    Expand-Archive -LiteralPath $nupkg -DestinationPath $unpack
    $sig = Get-AuthenticodeSignature -LiteralPath (Join-Path $unpack 'tools\python.exe')
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -ne $PySigner) {
        Refuse "tools\python.exe is not validly signed by the Python Software Foundation ($($sig.Status), $($sig.SignerCertificate.Subject))"
    }
    if (Test-Path -LiteralPath $PythonDir) { Remove-Item -LiteralPath $PythonDir -Recurse -Force }
    Move-Item -LiteralPath (Join-Path $unpack 'tools') -Destination $PythonDir
    Remove-Item -LiteralPath $unpack, $nupkg -Recurse -Force
}
# note: a same-volume Move-Item keeps the ACL the tree had under the elevated user's %TEMP% (SYSTEM,
# Administrators, that user), so the virtual account could not read python312 and no job resolved
# `python` - measured by the probe, run 36257355640. Re-inherit from the data root on every run.
Native 'icacls.exe' @($PythonDir, '/setowner', "*$SidAdmins", '/T', '/C', '/Q')
Native 'icacls.exe' @($PythonDir, '/reset', '/T', '/C', '/Q')
$pyGrant = @((Get-Acl -LiteralPath $py).Access | Where-Object {
    $_.AccessControlType -eq 'Allow' -and
    $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $VirtualSid })
if ($pyGrant.Count -eq 0) { Refuse "$py grants $Virtual nothing after the reset - the runner could not run its own Python" }
$pipVersion = (& $py -m pip --version) 2>&1
if ($LASTEXITCODE -ne 0) { Refuse "the runner's Python has no working pip: $pipVersion" }
Say "$((& $py --version) 2>&1) at $PythonDir; $pipVersion"

Step "4/6 MSVC: $Virtual reads $MsvcSource, reached as %LOCALAPPDATA%\$MsvcName"
Native 'icacls.exe' @($MsvcSource, '/grant', "*${VirtualSid}:(OI)(CI)RX", '/Q')
$junction = Join-Path $LocalAppData $MsvcName
if (Test-Path -LiteralPath $junction) {
    $item = Get-Item -LiteralPath $junction -Force
    if ($item.LinkType -ne 'Junction' -or @($item.Target)[0] -ne $MsvcSource) { Refuse "$junction exists and is not a junction to $MsvcSource" }
} else {
    New-Item -ItemType Junction -Path $junction -Target $MsvcSource | Out-Null
}
Say "junction $junction -> $MsvcSource"

Step "5/6 runner directory $RunnerDir - cut inheritance from C:\, grant $Virtual modify"
Native 'icacls.exe' @($RunnerDir, '/setowner', "*$SidAdmins", '/Q')
Native 'icacls.exe' @($RunnerDir, '/inheritance:r', '/grant:r',
    "*${SidSystem}:(OI)(CI)F", "*${SidAdmins}:(OI)(CI)F", "*${VirtualSid}:(OI)(CI)M", '/Q')
# .env reaches every job through Runner.Listener (LoadAndSetEnv). PATH is written WHOLE because a
# .env value is not expanded: the runner's Python first, then the machine PATH as of this run -
# a later machine-PATH change reaches the runner by re-running this script, as it would only reach
# a running service at its next start anyway.
$envOrig = Get-Content -Raw -LiteralPath (Join-Path $StateDir 'env.orig')
$kept = @($envOrig -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^(LOCALAPPDATA|PATH)=' })
$lines = $kept + @("LOCALAPPDATA=$LocalAppData", "PATH=$PythonDir;$(Join-Path $PythonDir 'Scripts');$machinePath")
Set-Content -LiteralPath (Join-Path $RunnerDir '.env') -Value ($lines -join "`r`n") -Encoding ASCII

Step "6/6 log the service on as $Virtual and start it"
Native 'sc.exe' @('sidtype', $ServiceName, 'unrestricted')
# note: sc.exe, never Win32_Service.Change: Change refuses a virtual account with 22 (measured 2026-09-26), and a virtual account has no password to keep off a command line.
Native 'sc.exe' @('config', $ServiceName, 'obj=', $Virtual)
$now = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").StartName
if ($now -ine $Virtual) { Refuse "the service logs on as $now after sc.exe config, not $Virtual" }
$started = Get-Date
Start-Service -Name $ServiceName
(Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
$listening = $null
foreach ($i in 1..90) {
    $log = Get-ChildItem -LiteralPath (Join-Path $RunnerDir '_diag') -Filter 'Runner_*.log' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $started } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($log -and (Select-String -LiteralPath $log.FullName -Pattern 'Listening for Jobs' -Quiet)) { $listening = $log.FullName; break }
    Start-Sleep -Seconds 1
}
if (-not $listening) { Refuse "the runner did not reach 'Listening for Jobs' within 90 s - read $RunnerDir\_diag. Undo: malf\runner\isolate-runner-windows-rollback.ps1 -Apply" }
$listener = Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" | Select-Object -First 1
$owner = Invoke-CimMethod -InputObject $listener -MethodName GetOwner
if ("$($owner.Domain)\$($owner.User)" -ne $Virtual) { Refuse "Runner.Listener runs as $($owner.Domain)\$($owner.User), not $Virtual" }

@"

[isolate] DONE - the runner listens as $Virtual ($listening).
  Once the WSL runner is moved too, the probe (one run on each runner):
    gh workflow run runner-isolation-probe.yml -R CodeRoasted/coderoast
  Undo: pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Apply
"@ | Write-Host
