# isolate-runner-windows-rollback.ps1 - undo isolate-runner-windows.ps1 for one instance of the
# table runner-instances.ps1.
#   ci       (a runner that was MOVED): the service logs on as the desk account again, the runner
#            directory gets its saved ACLs back, the MSVC grant and the LOCALAPPDATA junction are
#            removed, and the runner's .env is restored.
#   release  (a runner that was INSTALLED): the runner is deregistered and its service deleted
#            (config.cmd remove, the removal token through the environment), and the MSVC grant and
#            the junction are removed.
#
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Instance ci          # PLAN only
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Instance ci -Apply   # undo it
# From an ELEVATED PowerShell. For ci it ASKS for the desk account's password: a service logging on
# as a human account needs it, and it is read as a SecureString and handed to Win32_Service.Change
# in memory - never on a command line, where any local process could read it.
#
# NOT undone, on purpose: the runner-owned Python under the instance's data root stays (inert without
# the service; the last line printed deletes it), and so does the record beside it. The release
# instance's runner directory stays too, unconfigured: a later install reuses the binaries in it.

param([string]$Instance, [switch]$Apply)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'runner-instances.ps1')
$inst = Select-RunnerInstance $Instance
$RunnerDir = $inst.RunnerDir
$DataRoot  = $inst.DataRoot
$StateDir  = Join-Path $DataRoot 'isolation'
$LocalAppData = Join-Path $DataRoot 'LocalAppData'
$MsvcName  = 'malf-msvc1452'

function Say([string]$m)  { Write-Host "[rollback] $m" -ForegroundColor Cyan }
function Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Refuse([string]$m) { throw "[rollback] REFUSED: $m" }
function Native([string]$exe, [string[]]$argv) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @argv } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { Refuse "$exe $($argv -join ' ') exited $LASTEXITCODE" }
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Refuse 'run this from an ELEVATED PowerShell (Run as Administrator)'
}
$manifestPath = Join-Path $StateDir 'manifest.json'
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $ServiceName = $manifest.ServiceName
} elseif ($inst.Lifecycle -eq 'install') {
    # An install that stopped between its step 0 and its step 1 left a registered runner and no
    # manifest: the service is then the one executing from the runner directory, and no MSVC access
    # was granted yet.
    $found = @(Get-CimInstance -ClassName Win32_Service | Where-Object {
        $_.Name -like 'actions.runner.*' -and $_.PathName -and
        $_.PathName.IndexOf("$RunnerDir\", [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    if ($found.Count -ne 1) { Refuse "no rollback manifest at $manifestPath and $($found.Count) service(s) executing from $RunnerDir - nothing of the $($inst.Name) instance to undo" }
    $manifest = $null
    $ServiceName = $found[0].Name
} else {
    Refuse "no rollback manifest at $manifestPath - isolate-runner-windows.ps1 -Instance $($inst.Name) -Apply never stopped the service, so there is nothing to undo"
}
$Virtual = "NT SERVICE\$ServiceName"
$VirtualSid = (New-Object Security.Principal.NTAccount($Virtual)).Translate([Security.Principal.SecurityIdentifier]).Value
$svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $svc) { Refuse "service $ServiceName does not exist" }
# A job on THIS instance only: the other instance is another service's.
$worker = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Worker.exe'" | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$RunnerDir\", [StringComparison]::OrdinalIgnoreCase) })
if ($worker.Count -gt 0) { Refuse "a job is running on $($inst.RunnerName) (Runner.Worker pid $($worker.ProcessId -join ', ')) - let it finish, then re-run" }

# Shared by both lifecycles: the junction (the link only; the MSVC install it points at is
# untouched) and the virtual account's grant on that install.
function Remove-MsvcAccess {
    $junction = Join-Path $LocalAppData $MsvcName
    if (Test-Path -LiteralPath $junction) {
        $item = Get-Item -LiteralPath $junction -Force
        if ($item.LinkType -ne 'Junction') { Refuse "$junction is not a junction - refusing to delete a real directory" }
        [IO.Directory]::Delete($junction)
    }
    if ($manifest -and (Test-Path -LiteralPath $manifest.MsvcSource)) {
        Native 'icacls.exe' @($manifest.MsvcSource, '/remove:g', "*$VirtualSid", '/Q')
    }
}

if ($inst.Lifecycle -eq 'install') {
    @"

[rollback] plan - instance $($inst.Name)
  runner            $($inst.RunnerName) deregistered from the organisation and its service $ServiceName deleted
                    (config.cmd remove; the removal token minted by gh, or `$env:RUNNER_TOKEN, through the environment)
  MSVC              grant for $Virtual removed from $(if ($manifest) { $manifest.MsvcSource } else { '(none was granted)' }); junction $LocalAppData\$MsvcName removed
  kept on purpose   $DataRoot (the runner Python and this record) and $RunnerDir (the runner's binaries, unconfigured)
"@ | Write-Host
    if (-not $Apply) { Write-Host "`n[rollback] PLAN ONLY - nothing was changed. Re-run with -Apply to do it."; exit 0 }

    $token = $env:RUNNER_TOKEN
    if (-not $token) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $token = & gh api -X POST "/orgs/$RunnerOrg/actions/runners/remove-token" -q .token } finally { $ErrorActionPreference = $prev }
        if ($LASTEXITCODE -ne 0 -or -not $token) { Refuse "could not mint a removal token (gh exit $LASTEXITCODE) - mint one and pass it as `$env:RUNNER_TOKEN" }
    }

    Step '1/3 stop the service'
    Stop-Service -Name $ServiceName -Force
    (Get-Service -Name $ServiceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))

    Step '2/3 MSVC grant and junction'
    Remove-MsvcAccess

    Step "3/3 deregister $($inst.RunnerName) and delete its service"
    $env:ACTIONS_RUNNER_INPUT_TOKEN = $token
    try { Native (Join-Path $RunnerDir 'config.cmd') @('remove') } finally { Remove-Item Env:ACTIONS_RUNNER_INPUT_TOKEN -ErrorAction SilentlyContinue }
    if (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'") { Refuse "service $ServiceName still exists after config.cmd remove" }
    if (Test-Path -LiteralPath $StateDir) { Rename-Item -LiteralPath $StateDir -NewName ("isolation.rolled-back." + (Get-Date -Format 'yyyyMMddTHHmmss')) }

    @"

[rollback] DONE - $($inst.RunnerName) is deregistered and $ServiceName is deleted.
  To delete what this script keeps:
    Remove-Item -Recurse -Force $DataRoot, $RunnerDir
"@ | Write-Host
    exit 0
}

$aclSave = Join-Path $StateDir 'runner-acl.icacls'
if (-not (Test-Path -LiteralPath $aclSave)) { Refuse "the saved runner ACLs ($aclSave) are missing" }

@"

[rollback] plan - instance $($inst.Name)
  service           $ServiceName  (logon now: $($svc.StartName)) -> $($manifest.DeskAccount) (asks its password)
  service SID type  -> $($manifest.SidType)
  runner directory  ACLs restored from $aclSave (saved /T before the isolation, so the job workspace's
                    are among them, undoing step 5's reset); .env restored
  owners            NOT restored - icacls /save records DACLs only: Administrators stays owner of
                    $RunnerDir and of its job workspace, and access follows the restored DACLs
  MSVC              grant for $Virtual removed from $($manifest.MsvcSource); junction $LocalAppData\$MsvcName removed
  kept on purpose   $DataRoot (the runner Python and this record)
"@ | Write-Host
if (-not $Apply) { Write-Host "`n[rollback] PLAN ONLY - nothing was changed. Re-run with -Apply to do it."; exit 0 }

$password = Read-Host -AsSecureString "Password of $($manifest.DeskAccount) (the service logs on with it)"

Step '1/4 stop the service'
Stop-Service -Name $ServiceName -Force
(Get-Service -Name $ServiceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))

Step '2/4 runner directory: saved ACLs and .env'
# icacls /save wrote names relative to the runner directory's PARENT, so /restore runs there.
Native 'icacls.exe' @((Split-Path -Parent $RunnerDir), '/restore', $aclSave, '/C', '/Q')
$envOrig = Get-Content -Raw -LiteralPath (Join-Path $StateDir 'env.orig')
$envFile = Join-Path $RunnerDir '.env'
if ($envOrig) { Set-Content -LiteralPath $envFile -Value $envOrig -NoNewline -Encoding ASCII }
elseif (Test-Path -LiteralPath $envFile) { Remove-Item -LiteralPath $envFile -Force }

Step '3/4 MSVC grant and junction'
Remove-MsvcAccess

Step "4/4 log the service on as $($manifest.DeskAccount) and start it"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $r = Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{ StartName = $manifest.DeskAccount; StartPassword = $plain }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $plain = $null
}
if ($r.ReturnValue -ne 0) { Refuse "Win32_Service.Change returned $($r.ReturnValue) (22 = the account's 'Log on as a service' right, 15 = wrong password)" }
Native 'sc.exe' @('sidtype', $ServiceName, $manifest.SidType.ToLowerInvariant())
Start-Service -Name $ServiceName
(Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
Rename-Item -LiteralPath $StateDir -NewName ("isolation.rolled-back." + (Get-Date -Format 'yyyyMMddTHHmmss'))

@"

[rollback] DONE - $ServiceName runs as $($manifest.DeskAccount) again.
  To delete the runner-owned data this script keeps:
    Remove-Item -Recurse -Force $DataRoot
"@ | Write-Host
