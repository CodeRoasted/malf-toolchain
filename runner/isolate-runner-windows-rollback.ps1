# isolate-runner-windows-rollback.ps1 - undo isolate-runner-windows.ps1: the runner service logs on
# as the desk account again, the runner directory gets its saved ACLs back, the MSVC grant and the
# LOCALAPPDATA junction are removed, and the runner's .env is restored.
#
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1          # PLAN only
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows-rollback.ps1 -Apply   # undo it
# From an ELEVATED PowerShell. It ASKS for the desk account's password: a service logging on as a
# human account needs it, and it is read as a SecureString and handed to Win32_Service.Change in
# memory - never on a command line, where any local process could read it.
#
# NOT undone, on purpose: the runner-owned Python under C:\malf-runner-win stays (inert without the
# service; the last line printed deletes it), and so do the saved ACLs, kept beside it as the record.

param([switch]$Apply)

$ErrorActionPreference = 'Stop'

$RunnerDir = 'C:\actions-runner-malf-win'
$DataRoot  = 'C:\malf-runner-win'
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
if (-not (Test-Path -LiteralPath $manifestPath)) { Refuse "no rollback manifest at $manifestPath - isolate-runner-windows.ps1 -Apply never stopped the service, so there is nothing to undo" }
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$ServiceName = $manifest.ServiceName
$Virtual = "NT SERVICE\$ServiceName"
$VirtualSid = (New-Object Security.Principal.NTAccount($Virtual)).Translate([Security.Principal.SecurityIdentifier]).Value
$svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $svc) { Refuse "service $ServiceName does not exist" }
$worker = @(Get-Process -Name 'Runner.Worker' -ErrorAction SilentlyContinue)
if ($worker.Count -gt 0) { Refuse "a job is running (Runner.Worker pid $($worker.Id -join ', ')) - let it finish, then re-run" }
$aclSave = Join-Path $StateDir 'runner-acl.icacls'
if (-not (Test-Path -LiteralPath $aclSave)) { Refuse "the saved runner ACLs ($aclSave) are missing" }

@"

[rollback] plan
  service           $ServiceName  (logon now: $($svc.StartName)) -> $($manifest.DeskAccount) (asks its password)
  service SID type  -> $($manifest.SidType)
  runner directory  ACLs restored from $aclSave; .env restored
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
$junction = Join-Path $LocalAppData $MsvcName
if (Test-Path -LiteralPath $junction) {
    $item = Get-Item -LiteralPath $junction -Force
    if ($item.LinkType -ne 'Junction') { Refuse "$junction is not a junction - refusing to delete a real directory" }
    # Removes the link only; the MSVC install it points at is untouched.
    [IO.Directory]::Delete($junction)
}
if (Test-Path -LiteralPath $manifest.MsvcSource) {
    Native 'icacls.exe' @($manifest.MsvcSource, '/remove:g', "*$VirtualSid", '/Q')
}

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
