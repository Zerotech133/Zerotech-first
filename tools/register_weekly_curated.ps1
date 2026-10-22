#requires -Version 5.1
<#
.SYNOPSIS
    Register (or remove) the weekly curated-layer rebuild task.

.DESCRIPTION
    This script exists because the DSH agent session CANNOT register scheduled
    tasks: schtasks /create returns "The system cannot find the path specified"
    and New-ScheduledTaskAction returns Access denied (0x80041003) - the session
    is not elevated and the Task Scheduler CIM provider is unreachable.

    So the registration is a one-command manual step:

        powershell.exe -NoProfile -ExecutionPolicy Bypass `
            -File tools\register_weekly_curated.ps1

    Run it from an ELEVATED PowerShell (Run as Administrator).

    ---- FIVE ELEMENTS (AGENTS.md section 7 blocker-approval format) ----

    1. Full command
         schtasks /create /tn "ZeroBound\CuratedWeekly" /sc weekly /d SUN
                  /st 09:00 /f /ru <current user> /rl LIMITED
                  /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass
                       -File <repo>\tools\curated_weekly_run.ps1"

    2. Blast radius
         Writes only under <repo>\quant_backtest\data\tushare_curated.tmp\
         (a staging directory). It does NOT touch:
           - the raw layer  data\tushare\          (read-only)
           - the live curated layer data\tushare_curated\  (never replaced)
           - the vault       _vault\
         Logs go to <repo>\logs\curated_weekly.log.

    3. Irreversible point
         None. The task runs STAGE-ONLY: it rebuilds .tmp and verifies, then
         stops. Switching to the new layer (--replace) stays a manual step.
         Deleting .tmp next week is the only irreversible action, and .tmp is
         a pure derivative that costs ~15 minutes to regenerate.

    4. Alternatives tried
         - Agent-session registration: blocked (0x80041003, see above).
         - Daily-ops integration: rejected, it would make the 3-second daily
           card take 15 minutes.
         - Manual weekly reminder: rejected, it depends on someone remembering.

    5. Rollback
         powershell.exe -NoProfile -ExecutionPolicy Bypass `
             -File tools\register_weekly_curated.ps1 -Unregister
         That removes the task. Nothing else is touched.

.PARAMETER Unregister
    Remove the task instead of creating it.

.PARAMETER Time
    Local start time, default 09:00 (after the 06:00-08:00 kg_sync window).

.PARAMETER Force
    Overwrite an existing task of the same name.
#>
[CmdletBinding()]
param(
    [switch]$Unregister,
    [string]$Time = '09:00',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$taskName = 'ZeroBound\CuratedWeekly'
$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'curated_weekly_run.ps1'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host '=== weekly curated rebuild task ==='
Write-Host ("repo     : {0}" -f $repo)
Write-Host ("task     : {0}" -f $taskName)
Write-Host ("runner   : {0}" -f $runner)

if ($Unregister) {
    Write-Host ''
    Write-Host 'removing task ...'
    $out = & schtasks /delete /tn $taskName /f 2>&1
    $rc = $LASTEXITCODE
    $out | ForEach-Object { Write-Host ("  " + $_) }
    if ($rc -eq 0) {
        Write-Host 'result   : removed'
        exit 0
    }
    Write-Host 'result   : not removed (see message above)'
    exit $rc
}

if (-not (Test-Elevated)) {
    Write-Host ''
    Write-Host 'ERROR: this script must run in an ELEVATED PowerShell.'
    Write-Host '       Right-click PowerShell -> Run as Administrator, then re-run:'
    Write-Host ("       powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"{0}`"" -f $PSCommandPath)
    exit 2
}

if (-not (Test-Path -LiteralPath $runner)) {
    Write-Host ("ERROR: runner not found: {0}" -f $runner)
    exit 2
}

$python = 'C:\Program Files\Python311\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host ("WARN: python not found at {0}; the task will fall back to PATH." -f $python)
}

$tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runner
Write-Host ''
Write-Host 'creating task ...'
$args = @('/create', '/tn', $taskName, '/tr', $tr, '/sc', 'weekly', '/d', 'SUN',
          '/st', $Time, '/rl', 'LIMITED')
if ($Force) { $args += '/f' }

$out = & schtasks @args 2>&1
$rc = $LASTEXITCODE
$out | ForEach-Object { Write-Host ("  " + $_) }

if ($rc -ne 0) {
    Write-Host ''
    Write-Host 'result   : FAILED (see message above)'
    Write-Host 'hint     : if it says the task already exists, re-run with -Force'
    exit $rc
}

Write-Host ''
Write-Host 'result   : created'
Write-Host ("schedule : weekly, Sunday {0}, limited privileges" -f $Time)
Write-Host 'verify   : schtasks /query /tn "ZeroBound\CuratedWeekly" /v /fo LIST'
Write-Host 'rollback : tools\register_weekly_curated.ps1 -Unregister'
Write-Host ''
Write-Host 'NOTE: the task runs STAGE-ONLY (rebuild .tmp + verify).'
Write-Host '      Switching to the new layer stays a manual --replace step.'
exit 0
