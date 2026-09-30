# runner-instances.ps1 - dot-sourced by isolate-runner-windows.ps1, its rollback and its selftest:
# the one table of the self-hosted runners this WINDOWS host carries, each a service of its own and
# so a virtual account of its own. The WSL twin is runner-instances.sh.
#
# WHY TWO SERVICES (DN-119.D8, option A, ruled by the Founder 2026-09-29). A runner runs every job as
# its one account, and that account owns what the next job executes: its Runner.Worker.exe, the
# Python its PATH puts first, its job workspace. The runner GROUP decides whose jobs those are. `ci`
# sits in `Default`, which admits every private repository of the organisation; `release` sits in
# `coderoast-release`, which admits only the repositories whose release jobs are routed to it, and
# carries no label but its own, so no job reaches it without naming it. A Windows service's virtual
# account (NT SERVICE\<service>) is derived from the service's name, so a second service IS a second
# account: no ci job can write what the release runner executes.
#
#   ci       malf-runner-win  - every private repository's Windows CI. Its lifecycle is a MOVE of a
#            runner the desk account registered (ROADMAP N214).
#   release  malf-release-win - the Windows legs of a release: the public sift-windows-x64.exe and the
#            golden proof's MSVC leg. Its lifecycle is a fresh INSTALL: this host never had one.

$RunnerOrg = 'CodeRoasted'
$ReleaseGroup = 'coderoast-release'
$RunnerInstances = @('ci', 'release')

# Select-RunnerInstance <name> - the instance's row; throws on an unknown name.
function Select-RunnerInstance([string]$name) {
    switch ($name) {
        'ci' {
            return [pscustomobject]@{
                Name       = 'ci'
                RunnerName = 'malf-runner-win'
                RunnerDir  = 'C:\actions-runner-malf-win'
                DataRoot   = 'C:\malf-runner-win'
                Lifecycle  = 'move'
                Group      = 'Default'
                # The installer's own label beside the defaults (self-hosted, Windows, X64).
                Labels     = 'malf-windows'
                OnlyLabels = $false
                # The desk reads this runner's logs and work tree through the local group config.cmd
                # made (a failed leg is settled offline), so that group's grant stays.
                DeskReads  = $true
            }
        }
        'release' {
            return [pscustomobject]@{
                Name       = 'release'
                RunnerName = 'malf-release-win'
                RunnerDir  = 'C:\actions-runner-release-win'
                DataRoot   = 'C:\malf-release-win'
                Lifecycle  = 'install'
                Group      = $ReleaseGroup
                Labels     = 'coderoast-release-windows'
                OnlyLabels = $true
                # SYSTEM, Administrators and the runner's own virtual account, no other: no account
                # reads what a release built before it ships, the desk included; the desk reads a
                # release job's log on GitHub, or elevated.
                DeskReads  = $false
            }
        }
        default {
            throw "[runner-instances] unknown instance '$name' - pass -Instance ci or -Instance release"
        }
    }
}
