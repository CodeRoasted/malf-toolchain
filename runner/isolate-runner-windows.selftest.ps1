# isolate-runner-windows.selftest.ps1 - the arms of isolate-runner-windows.ps1 that run WITHOUT
# elevation, on the Windows host that script isolates (its subject - junctions, ACLs, icacls - exists
# nowhere else, so no CI job runs this; the isolation's own probe job proves the applied state).
#
#   pwsh -ExecutionPolicy Bypass -File malf\runner\isolate-runner-windows.selftest.ps1
#
# What it proves: both scripts parse; the reparse-point walk finds a junction without walking
# through it, reports a folder it cannot list, and with a repair hands that folder alone to it and
# then finds a junction inside; step 5 walks, re-owns then resets the job workspace AFTER the
# runner directory's grant that reset inherits; the plan and the rollback's plan name the
# workspace. What it cannot: icacls under elevation, which the script's -Apply run checks
# itself (an explicit ACE left, or no grant to the virtual account, refuses).
# Exit 0 green, 1 red.

param(
    [string]$Script = (Join-Path $PSScriptRoot 'isolate-runner-windows.ps1'),
    [string]$Rollback = (Join-Path $PSScriptRoot 'isolate-runner-windows-rollback.ps1')
)

$ErrorActionPreference = 'Stop'
$script:pass = 0
$script:fail = 0

function Check([string]$label, $expected, $actual) {
    if ("$expected" -ceq "$actual") { $script:pass++; Write-Host "  ok   $label" }
    else { $script:fail++; Write-Host "  FAIL $label`n       expected: $expected`n       actual:   $actual" }
}

Write-Host '[1] both scripts parse'
foreach ($file in $Script, $Rollback) {
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile($file, [ref]$null, [ref]$errors) | Out-Null
    Check "$(Split-Path -Leaf $file) parses" 0 @($errors).Count
}

Write-Host '[2] the preflight walk finds a reparse point without walking through it'
$ast = [Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
$walk = $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                       $node.Name -eq 'Find-ReparsePoint' }, $true) | Select-Object -First 1
Check 'the script defines Find-ReparsePoint' 'True' "$($null -ne $walk)"
if ($walk) {
    . ([scriptblock]::Create($walk.Extent.Text))
    $base = Join-Path ([IO.Path]::GetTempPath()) ('isolate-selftest-' + [guid]::NewGuid())
    $junction = Join-Path $base 'work\a\J'
    $sealed = Join-Path $base 'work\sealed'
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    New-Item -ItemType Directory -Path (Join-Path $base 'work\a\b'), (Join-Path $base 'outside\deep') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $base 'work\a\b\f.txt') -Value 'x'
    Set-Content -LiteralPath (Join-Path $base 'outside\deep\g.txt') -Value 'x'
    New-Item -ItemType Junction -Path $junction -Target (Join-Path $base 'outside') | Out-Null
    try {
        $scan = Find-ReparsePoint (Join-Path $base 'work')
        Check 'the junction is found' $junction ($scan.Reparse -join ';')
        # work\a, work\a\b, work\a\b\f.txt, work\a\J - outside\deep and g.txt would make 6.
        Check 'and never walked through: 4 entries counted, none from its target' 4 $scan.Walked
        Check 'every folder listed' 0 @($scan.Unlisted).Count
        [IO.Directory]::Delete($junction)
        Check 'with the junction gone, none is found' 0 @((Find-ReparsePoint (Join-Path $base 'work')).Reparse).Count
        # A folder this account cannot list, holding a junction: step 5's walk re-owns it (here the
        # repair lifts the deny; elevation is not available to a selftest) and then finds the junction.
        $inner = Join-Path $sealed 'K'
        New-Item -ItemType Directory -Path $sealed | Out-Null
        New-Item -ItemType Junction -Path $inner -Target (Join-Path $base 'outside') | Out-Null
        & icacls.exe $sealed /deny "*${me}:(RD)" /Q | Out-Null
        $scan = Find-ReparsePoint (Join-Path $base 'work')
        Check 'read-only, a folder it cannot list is reported, never read as holding nothing' $sealed (@($scan.Unlisted | ForEach-Object { ($_ -split ': ', 2)[0] }) -join ';')
        Check 'and the junction inside it is not claimed seen' 0 @($scan.Reparse).Count
        $repaired = New-Object 'Collections.Generic.List[string]'
        $scan = Find-ReparsePoint (Join-Path $base 'work') { param($dir) $repaired.Add($dir); & icacls.exe $dir /remove:d "*$me" /Q | Out-Null }
        Check 'with a repair, that folder alone is handed to it, then walked' $sealed ($repaired -join ';')
        Check 'and the junction inside it is found' $inner ($scan.Reparse -join ';')
        Check 'nothing left unlisted' 0 @($scan.Unlisted).Count
    } finally {
        if (Test-Path -LiteralPath $sealed) { & icacls.exe $sealed /remove:d "*$me" /Q | Out-Null }
        foreach ($link in $junction, (Join-Path $sealed 'K')) { if (Test-Path -LiteralPath $link) { [IO.Directory]::Delete($link) } }
        Remove-Item -LiteralPath $base -Recurse -Force
    }
}

Write-Host '[3] step 5 re-owns, then resets, the job workspace after the grant it inherits'
$text = Get-Content -Raw -LiteralPath $Script
$grant = $text.IndexOf("Native 'icacls.exe' @(`$RunnerDir, '/inheritance:r'")
$rewalk = $text.IndexOf('$scan = Find-ReparsePoint $WorkDir { param($dir)')
$owner = $text.IndexOf("Native 'icacls.exe' @(`$WorkDir, '/setowner', `"*`$SidAdmins`", '/T', '/C', '/Q')")
$reset = $text.IndexOf("Native 'icacls.exe' @(`$WorkDir, '/reset', '/T', '/C', '/Q')")
Check 'the runner directory grant, the re-owning walk, then /setowner Administrators /T, then /reset /T over $WorkDir' 'True' "$($grant -ge 0 -and $rewalk -gt $grant -and $owner -gt $rewalk -and $reset -gt $owner)"
$scanAt = $text.IndexOf('Find-ReparsePoint $WorkDir')
$planAt = $text.IndexOf('PLAN ONLY - nothing was changed')
Check 'the walk runs in the preflight, before the plan and before anything changes' 'True' "$($scanAt -ge 0 -and $scanAt -lt $planAt)"
Check 'the plan prints the job workspace' 'True' "$($text.Contains('  job workspace     $workPlan'))"
Check 'the rollback plan says the workspace DACLs come back and owners do not' 'True' "$((Get-Content -Raw -LiteralPath $Rollback).Contains('NOT restored - icacls /save records DACLs only'))"

Write-Host "`nisolate-runner-windows selftest: $($script:pass) passed, $($script:fail) failed"
if ($script:fail -gt 0) { exit 1 }
exit 0
