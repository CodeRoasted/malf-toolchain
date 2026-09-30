# isolate-runner-windows.ps1 - put a Windows self-hosted runner on its service's VIRTUAL account,
# NT SERVICE\<service> - an account with no password to hand around, not an administrator, with a
# profile of its own - with a Python, a LOCALAPPDATA and a directory ACL of its own. One instance
# per run, from the table runner-instances.ps1:
#
#   ci       malf-runner-win: MOVED off the Founder's own account, which registered it (ROADMAP N214).
#   release  malf-release-win: INSTALLED here, in the runner group `coderoast-release`, carrying the
#            one label `coderoast-release-windows` (DN-119.D8). A second service is a second virtual
#            account, so nothing a ci job runs as can write what a release job executes.
#
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows.ps1 -Instance release          # PLAN only
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows.ps1 -Instance release -Apply   # do it
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Instance release # undo it
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
#   * The job workspace (.runner's workFolder, _work): folders jobs made BEFORE the isolation keep
#     the ACLs their account gave them, and the virtual account could not delete them - measured on
#     the 1.10.5 cut (2026-09-28); the Founder re-owned and reset the tree by hand that night. Step
#     5 does it: owner Administrators, then every entry reset to inherit the runner directory's ACL.
#
#   * The release instance's directory names SYSTEM, Administrators and its own virtual account and
#     NO OTHER: config.cmd grants FullControl to a local group it makes (GITHUB_ActionsRunner_*),
#     and step 5 removes every explicit entry but those three, whoever the group's members are.
#
# THE RELEASE INSTANCE'S INSTALL (step 0, only while its directory holds no runner): the installer
# beside this script downloads the latest actions/runner, verified against two producers, and
# registers it as a service under LocalSystem - the one account config.cmd can name before the
# service, and so its virtual account, exists. The registration token reaches config.cmd through
# the environment, never an argument. The service then runs as LocalSystem for the seconds until
# step 1 stops it, reachable only by a job naming a label no workflow names before this script has
# finished; step 6 moves it to the virtual account.
#
# IDEMPOTENT; REFUSES, changing nothing, on anything it did not expect: a job running on this
# instance, a service in neither the before nor the after shape, a missing MSVC install, a pin that
# does not verify, a reparse point under the job workspace (a workspace folder it cannot list is
# re-owned, alone, and walked before any /T runs).
# Uses SIDs, never group NAMES: this host's Windows is localized (Administrateurs, Utilisateurs).

param([string]$Instance, [switch]$Apply)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'runner-instances.ps1')
$inst = Select-RunnerInstance $Instance
$RunnerDir    = $inst.RunnerDir
$DataRoot     = $inst.DataRoot
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

# Every reparse point under $root, found WITHOUT descending into one, and every folder that could
# not be listed. note: icacls /T reaches THROUGH a junction and rewrites its target - measured
# 2026-09-29 on this host, /reset /T with and without /L reset a folder outside the tree reached by
# a junction inside it - so step 5's /T runs only over a tree this walk found none in. A folder
# this account cannot list is handed to $repair (never a reparse point: the walk enqueues none),
# then listed once more; without $repair, or still unlisted, it is reported.
function Find-ReparsePoint([string]$root, [scriptblock]$repair = $null) {
    $reparse = New-Object 'Collections.Generic.List[string]'
    $unlisted = New-Object 'Collections.Generic.List[string]'
    $walked = 0
    $queue = New-Object 'Collections.Generic.Queue[IO.DirectoryInfo]'
    $queue.Enqueue((New-Object IO.DirectoryInfo($root)))
    while ($queue.Count -gt 0) {
        $dir = $queue.Dequeue()
        $entries = $null
        foreach ($attempt in 1, 2) {
            try { $entries = $dir.GetFileSystemInfos(); break } catch { $denied = $_.Exception.Message }
            if ($attempt -eq 1 -and $repair) { & $repair $dir.FullName } else { break }
        }
        if ($null -eq $entries) { $unlisted.Add("$($dir.FullName): $denied"); continue }
        foreach ($entry in $entries) {
            $walked++
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { $reparse.Add($entry.FullName) }
            elseif ($entry -is [IO.DirectoryInfo]) { $queue.Enqueue($entry) }
        }
    }
    [pscustomobject]@{ Walked = $walked; Reparse = @($reparse); Unlisted = @($unlisted) }
}

# --- Preflight - reads only ---------------------------------------------------------------------

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Refuse 'run this from an ELEVATED PowerShell (Run as Administrator)'
}
# The service is identified by the binary it EXECUTES, never by a name glob (install-runner.ps1,
# Get-RunnerService, says why).
function Get-InstanceService {
    @(Get-CimInstance -ClassName Win32_Service | Where-Object {
        $_.Name -like 'actions.runner.*' -and $_.PathName -and
        $_.PathName.IndexOf("$RunnerDir\", [StringComparison]::OrdinalIgnoreCase) -ge 0 })
}
$SystemLogons = @('LocalSystem', 'NT AUTHORITY\SYSTEM')
$manifestPath = Join-Path $StateDir 'manifest.json'
$configured = Test-Path -LiteralPath (Join-Path $RunnerDir '.runner')
$svc = @(Get-InstanceService)

if ($inst.Lifecycle -eq 'install' -and -not $configured -and $svc.Count -eq 0) {
    # Nothing of this instance exists yet: step 0 installs it. The service name is the one config.cmd
    # derives (actions.runner.<org>.<runner>), read back from the service itself after the install.
    $State = 'absent'
    $ServiceName = "actions.runner.$RunnerOrg.$($inst.RunnerName)"
    $Virtual = "NT SERVICE\$ServiceName"
    $VirtualSid = '(known once the service exists)'
    $logonNow = '(no service yet)'
} else {
    if (-not $configured) { Refuse "$RunnerDir is not a configured runner (.runner missing)" }
    if ($svc.Count -ne 1) { Refuse "expected exactly one actions.runner.* service executing from $RunnerDir, found $($svc.Count)" }
    $svc = $svc[0]
    $ServiceName = $svc.Name
    $Virtual = "NT SERVICE\$ServiceName"
    $VirtualSid = (New-Object Security.Principal.NTAccount($Virtual)).Translate([Security.Principal.SecurityIdentifier]).Value
    $logonNow = $svc.StartName

    if ($svc.StartName -ieq $Virtual) {
        $State = 'applied'
        if (-not (Test-Path -LiteralPath $manifestPath)) { Refuse "the service already logs on as $Virtual but $manifestPath is absent - not this script's work" }
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    } elseif ($inst.Lifecycle -eq 'install') {
        # The install's own before-shape: a service config.cmd registered under LocalSystem, by step 0
        # of a run that then stopped, or of this one.
        if ($svc.StartName -notin $SystemLogons) { Refuse "the service logs on as $($svc.StartName): the $($inst.Name) instance is installed under LocalSystem and moved to $Virtual, and this is neither" }
        $State = 'installed'
    } elseif (Test-Path -LiteralPath $manifestPath) {
        # A run that stopped after step 1: the manifest holds the pre-isolation state and is never re-recorded.
        $State = 'partial'
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
        if ($manifest.DeskAccount -ine $svc.StartName) { Refuse "the service logs on as $($svc.StartName) but $manifestPath records $($manifest.DeskAccount) - not this script's work" }
    } else {
        $State = 'fresh'
        if ($svc.StartName -in ($SystemLogons + @('NT AUTHORITY\LocalService', 'NT AUTHORITY\NetworkService'))) {
            Refuse "the service logs on as $($svc.StartName), not a desk account - the $($inst.Name) instance is MOVED off a HUMAN account; to move it off $($svc.StartName), change the logon by hand with the same grants"
        }
    }
}

# The MSVC install both instances read. The ci instance was the desk account's, so it is that
# account's %LOCALAPPDATA%; the release instance takes the SAME directory from the ci instance's
# record, so the two runners cannot come to read different toolsets.
if ($inst.Lifecycle -eq 'move') {
    $DeskAccount = if ($State -eq 'fresh') { $svc.StartName } else { $manifest.DeskAccount }
    $deskName = $DeskAccount -replace '^\.\\', "$env:COMPUTERNAME\"
    $DeskSid = (New-Object Security.Principal.NTAccount($deskName)).Translate([Security.Principal.SecurityIdentifier]).Value
    $DeskProfile = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$DeskSid").ProfileImagePath
    $MsvcSource = Join-Path $DeskProfile "AppData\Local\$MsvcName"
} else {
    $ciManifest = Join-Path (Select-RunnerInstance 'ci').DataRoot 'isolation\manifest.json'
    if (-not (Test-Path -LiteralPath $ciManifest)) { Refuse "no record of the ci instance at $ciManifest - apply -Instance ci first: the release instance reads the MSVC install that record names" }
    $MsvcSource = (Get-Content -Raw -LiteralPath $ciManifest | ConvertFrom-Json).MsvcSource
    if (-not $MsvcSource) { Refuse "$ciManifest names no MsvcSource" }
}
if (-not (Test-Path -LiteralPath (Join-Path $MsvcSource 'VC\Tools\MSVC'))) {
    Refuse "no MSVC install at $MsvcSource - setup-msvc1452 needs elevation to install one, which the virtual account will not have. Provision it first (one run of the probe job under the desk account does it), then re-run"
}

# A job must not be running ON THIS INSTANCE: the logon change restarts its service. A job on the
# other instance is another service's and is left alone.
function Get-InstanceProcess([string]$exe) {
    @(Get-CimInstance Win32_Process -Filter "Name='$exe'" | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$RunnerDir\", [StringComparison]::OrdinalIgnoreCase) })
}
$worker = @(Get-InstanceProcess 'Runner.Worker.exe')
if ($worker.Count -gt 0) { Refuse "a job is running on $($inst.RunnerName) (Runner.Worker pid $($worker.ProcessId -join ', ')) - let it finish, then re-run" }

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

# The job workspace, where .runner says it is (relative to the runner directory unless rooted).
# note: an instance not installed yet has no .runner; config.cmd's default workFolder is _work.
function Get-WorkDir {
    $workFolder = (Get-Content -Raw -LiteralPath (Join-Path $RunnerDir '.runner') | ConvertFrom-Json).workFolder
    if (-not $workFolder) { Refuse "$RunnerDir\.runner names no workFolder" }
    if ([IO.Path]::IsPathRooted($workFolder)) { $workFolder } else { Join-Path $RunnerDir $workFolder }
}
$WorkDir = if ($State -eq 'absent') { Join-Path $RunnerDir '_work' } else { Get-WorkDir }
if (Test-Path -LiteralPath $WorkDir) {
    if ((Get-Item -LiteralPath $WorkDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { Refuse "$WorkDir is itself a reparse point - step 5's /T would rewrite its target" }
    $scan = Find-ReparsePoint $WorkDir
    if ($scan.Reparse.Count -gt 0) { Refuse "$($scan.Reparse.Count) reparse point(s) under $WorkDir - step 5's icacls /T would rewrite what each points at (the first: $($scan.Reparse[0])). Remove them (a junction: [IO.Directory]::Delete), then re-run" }
    $unlistedNote = if ($scan.Unlisted.Count -gt 0) { "; $($scan.Unlisted.Count) folder(s) this account cannot list - step 5 re-owns each before walking into it, and refuses on a reparse point found there" } else { '' }
    $workPlan = "$WorkDir - owner Administrators, every entry reset to inherit (/T over $($scan.Walked) listed entries, no reparse point$unlistedNote)"
} else {
    $workPlan = "$WorkDir - absent (no job has run here): nothing to reset"
}

$registration = if ($inst.Lifecycle -eq 'install') {
    if ($State -eq 'absent') { "step 0 INSTALLS it: the latest actions/runner, verified, registered as $($inst.RunnerName) in group $($inst.Group) with ONLY the label $($inst.Labels); the token through the environment" }
    else { "kept: $($inst.RunnerName), group $($inst.Group), the one label $($inst.Labels) (no re-register: its RSA key is DPAPI LocalMachine-scoped)" }
} else { 'kept (no re-register: its RSA key is DPAPI LocalMachine-scoped)' }
$aclPlan = if ($inst.DeskReads) { "SYSTEM and Administrators full, $Virtual modify; the local group config.cmd made keeps its grant (the desk reads this runner)" }
           else { "SYSTEM and Administrators full, $Virtual modify, and NO OTHER entry: every other explicit grant is removed, config.cmd's local group included" }
@"

[isolate] instance: $($inst.Name)   state: $State
  service           $ServiceName  (logon now: $logonNow)
  new logon         $Virtual  (SID $VirtualSid) - no password, not an administrator
  runner directory  $RunnerDir - owner Administrators; $aclPlan;
                    inheritance from C:\ cut (it gave Authenticated Users MODIFY on the runner's own binaries)
  Python            $PythonDir - NuGet python 3.12.10, SHA-256 + SHA-512 pinned, signer checked
  LOCALAPPDATA      $LocalAppData, holding a junction $MsvcName -> $MsvcSource
                    ($Virtual granted read+execute on that directory only)
  runner .env       LOCALAPPDATA and PATH ($PythonDir, its Scripts, then the machine PATH)
  job workspace     $workPlan
                    (a pre-isolation folder keeps its creator's ACL; the virtual account could not delete one)
  registration      $registration
"@ | Write-Host

if (-not $Apply) { Write-Host "`n[isolate] PLAN ONLY - nothing was changed. Re-run with -Apply to do it."; exit 0 }

# --- Apply --------------------------------------------------------------------------------------

if ($State -eq 'absent') {
    Step "0/6 install $($inst.RunnerName) in group $($inst.Group), label $($inst.Labels) only"
    $installer = Join-Path $PSScriptRoot 'install-runner.ps1'
    $here = Get-Location
    try {
        & $installer -Org $RunnerOrg -RunnerName $inst.RunnerName -RunnerDir $RunnerDir -Labels $inst.Labels `
            -RunnerGroup $inst.Group -NoDefaultLabels:$inst.OnlyLabels
    } finally { Set-Location $here }
    $svc = @(Get-InstanceService)
    if ($svc.Count -ne 1) { Refuse "the install left $($svc.Count) actions.runner.* service(s) executing from $RunnerDir, not one" }
    $svc = $svc[0]
    if ($svc.StartName -notin $SystemLogons) { Refuse "the installed service logs on as $($svc.StartName), not LocalSystem" }
    $ServiceName = $svc.Name
    $Virtual = "NT SERVICE\$ServiceName"
    $VirtualSid = (New-Object Security.Principal.NTAccount($Virtual)).Translate([Security.Principal.SecurityIdentifier]).Value
    $pool = (Get-Content -Raw -LiteralPath (Join-Path $RunnerDir '.runner') | ConvertFrom-Json).poolName
    if ($pool -ne $inst.Group) { Refuse "the runner registered into the group '$pool', not '$($inst.Group)' - undo: isolate-runner-windows-rollback.ps1 -Instance $($inst.Name) -Apply" }
    $WorkDir = Get-WorkDir
    $State = 'installed'
    Say "$($inst.RunnerName) registered in $pool as $ServiceName; its virtual account is $Virtual ($VirtualSid)"
}

Step "1/6 stop the service and record the rollback manifest"
Stop-Service -Name $ServiceName -Force
(Get-Service -Name $ServiceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
if (-not (Test-Path -LiteralPath $manifestPath)) {
    $envFile = Join-Path $RunnerDir '.env'
    $envOrig = if (Test-Path -LiteralPath $envFile) { Get-Content -Raw -LiteralPath $envFile } else { '' }
    Set-Content -LiteralPath (Join-Path $StateDir 'env.orig') -Value $envOrig -NoNewline
    if ($inst.Lifecycle -eq 'move') {
        $aclSave = Join-Path $StateDir 'runner-acl.icacls'
        Native 'icacls.exe' @($RunnerDir, '/save', $aclSave, '/T', '/C', '/Q')
        $sidType = ((sc.exe qsidtype $ServiceName) -match 'SERVICE_SID_TYPE' | Select-Object -First 1) -replace '.*:\s*', ''
        @{ Lifecycle = 'move'; DeskAccount = $svc.StartName; ServiceName = $ServiceName; SidType = $sidType.Trim();
           MsvcSource = $MsvcSource; Applied = (Get-Date).ToString('s') } |
            ConvertTo-Json | Set-Content -LiteralPath $manifestPath
    } else {
        # An installed instance is undone by removing it, so there is no earlier ACL or logon to keep.
        @{ Lifecycle = 'install'; ServiceName = $ServiceName; MsvcSource = $MsvcSource; Applied = (Get-Date).ToString('s') } |
            ConvertTo-Json | Set-Content -LiteralPath $manifestPath
    }
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

Step "5/6 runner directory $RunnerDir - cut inheritance from C:\, grant $Virtual modify; its job workspace reset to inherit"
Native 'icacls.exe' @($RunnerDir, '/setowner', "*$SidAdmins", '/Q')
Native 'icacls.exe' @($RunnerDir, '/inheritance:r', '/grant:r',
    "*${SidSystem}:(OI)(CI)F", "*${SidAdmins}:(OI)(CI)F", "*${VirtualSid}:(OI)(CI)M", '/Q')
if (-not $inst.DeskReads) {
    # config.cmd granted FullControl to a local group it made (GITHUB_ActionsRunner_*); whoever its
    # members are, this instance's directory names three principals and no other.
    $kept = @($SidSystem, $SidAdmins, $VirtualSid)
    $others = @((Get-Acl -LiteralPath $RunnerDir).Access | Where-Object { -not $_.IsInherited } |
        ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } |
        Select-Object -Unique | Where-Object { $_ -notin $kept })
    foreach ($sid in $others) {
        Native 'icacls.exe' @($RunnerDir, '/remove', "*$sid", '/Q')
        Say "removed the explicit entry of $sid from $RunnerDir"
    }
    $left = @((Get-Acl -LiteralPath $RunnerDir).Access |
        ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } |
        Select-Object -Unique | Where-Object { $_ -notin $kept })
    if ($left.Count -gt 0) { Refuse "$RunnerDir still names $($left -join ', ') beside SYSTEM, Administrators and $Virtual" }
}
# note: AFTER the grant above, so the reset inherits it. Owner first: an owner may always rewrite a DACL, whatever it denies.
if (Test-Path -LiteralPath $WorkDir) {
    # The walk again, now re-owning a folder it cannot list (that folder alone, no /T) before
    # walking into it: a reparse point inside one is found before any /T runs.
    $scan = Find-ReparsePoint $WorkDir { param($dir)
        Native 'icacls.exe' @($dir, '/setowner', "*$SidAdmins", '/Q')
        Native 'icacls.exe' @($dir, '/reset', '/Q') }
    if ($scan.Reparse.Count -gt 0) { Refuse "$($scan.Reparse.Count) reparse point(s) under $WorkDir, found once its unlisted folders were re-owned (the first: $($scan.Reparse[0])) - no /T ran. Remove them, then re-run" }
    if ($scan.Unlisted.Count -gt 0) { Refuse "$($scan.Unlisted.Count) folder(s) under $WorkDir still cannot be listed after re-owning them (the first: $($scan.Unlisted[0])) - no /T ran" }
    Native 'icacls.exe' @($WorkDir, '/setowner', "*$SidAdmins", '/T', '/C', '/Q')
    Native 'icacls.exe' @($WorkDir, '/reset', '/T', '/C', '/Q')
    $explicit = @(@(Get-Item -LiteralPath $WorkDir -Force) + @(Get-ChildItem -LiteralPath $WorkDir -Force) | Where-Object {
        @((Get-Acl -LiteralPath $_.FullName).Access | Where-Object { -not $_.IsInherited }).Count -gt 0 })
    if ($explicit.Count -gt 0) { Refuse "$($explicit.Count) item(s) of $WorkDir still carry an explicit ACE after the reset, the first $($explicit[0].FullName)" }
    $workGrant = @((Get-Acl -LiteralPath $WorkDir).Access | Where-Object {
        $_.AccessControlType -eq 'Allow' -and
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $VirtualSid })
    if ($workGrant.Count -eq 0) { Refuse "$WorkDir grants $Virtual nothing after the reset - a job could not clean its own workspace" }
    Say "$WorkDir owned by Administrators, every entry inheriting $RunnerDir's ACL"
}
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
if (-not $listening) { Refuse "the runner did not reach 'Listening for Jobs' within 90 s - read $RunnerDir\_diag. Undo: malf\runner\isolate-runner-windows-rollback.ps1 -Instance $($inst.Name) -Apply" }
$listener = Get-InstanceProcess 'Runner.Listener.exe' | Select-Object -First 1
if (-not $listener) { Refuse "no Runner.Listener.exe runs from $RunnerDir" }
$owner = Invoke-CimMethod -InputObject $listener -MethodName GetOwner
if ("$($owner.Domain)\$($owner.User)" -ne $Virtual) { Refuse "Runner.Listener runs as $($owner.Domain)\$($owner.User), not $Virtual" }

@"

[isolate] DONE - $($inst.RunnerName) listens as $Virtual ($listening).
  The probe, one leg on each runner:
    gh workflow run runner-isolation-probe.yml -R CodeRoasted/coderoast
  Undo: pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Instance $($inst.Name) -Apply
"@ | Write-Host
