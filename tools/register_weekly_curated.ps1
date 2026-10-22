#requires -Version 5.1
<#
.SYNOPSIS
    Register (or remove) the weekly curated-layer rebuild task.

.DESCRIPTION
    Run this from an ELEVATED PowerShell (Run as Administrator).

    Why this file exists: the agent session cannot register tasks
    (schtasks -> "path not found", New-ScheduledTaskAction -> Access denied
    0x80041003 - not elevated). Registration is a one-command manual step.

    v2 (2026-10-22): the v1 used `schtasks /create /tr "<cmd with quotes>"`,
    which is fragile - PowerShell re-splits the /tr value and the task either
    fails to create or gets a truncated action. v2 uses Register-ScheduledTask
    and passes -Execute and -Argument as SEPARATE parameters, so there is no
    quoting to get wrong. It also verifies the task right after creating it
    and prints the query result, so "did it work?" is answered in the same run.

    ---- FIVE ELEMENTS (AGENTS.md section 7 blocker-approval format) ----

    1. Full command
         Register-ScheduledTask -TaskName CuratedWeekly -TaskPath '\ZeroBound\'
             -Action (New-ScheduledTaskAction -Execute powershell.exe
                      -Argument '-NoProfile -ExecutionPolicy Bypass
                                 -File <repo>\tools\curated_weekly_run.ps1')
             -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 09:00)
             -Description 'ZeroBound weekly curated-layer rebuild (stage-only)'

    2. Blast radius
         Writes only <repo>\quant_backtest\data\tushare_curated.tmp\ (staging)
         and <repo>\logs\curated_weekly.log. Never touches:
           data\tushare\ (raw, read-only), data\tushare_curated\ (live layer),
           _vault\ (backups).

    3. Irreversible point
         None at registration time. The task itself runs STAGE-ONLY: it
         rebuilds .tmp, verifies row counts, stops. Switching the live layer
         (--replace) remains a manual step.

    4. Alternatives tried
         - Agent-session registration: blocked (0x80041003).
         - schtasks /create with quoted /tr: fragile, produced no task (v1).
         - Putting it inside daily_ops: rejected - turns a 3-second card into
           a 15-minute one.

    5. Rollback
         powershell.exe -NoProfile -ExecutionPolicy Bypass `
             -File tools\register_weekly_curated.ps1 -Unregister
         Removes the task. Nothing else is touched.

.PARAMETER Unregister
    Remove the task instead of creating it.

.PARAMETER Time
    Local start time, default 09:00 (after the 06:00-08:00 kg_sync window).

.PARAMETER Force
    Overwrite an existing task of the same name.

.PARAMETER RunNow
    Start the task immediately after registering (so you do not have to wait
    for Sunday to find out whether it works).

.PARAMETER VerifyOnly
    Only query and print the task; do not create or remove anything.
#>
[CmdletBinding()]
param(
    [switch]$Unregister,
    [string]$Time = '09:00',
    [switch]$Force,
    [switch]$RunNow,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

$taskName = 'CuratedWeekly'
$taskPath = '\ZeroBound\'
$fullName = $taskPath + $taskName
$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'curated_weekly_run.ps1'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Task {
    Write-Host ''
    Write-Host '--- verification ---'
    # Two traps avoided here:
    #  1. Do NOT regex-match the LIST labels: they are localized (the zh-CN
    #     build prints localized label names), and embedding those literals
    #     would make this file non-ASCII, which PS 5.1 then decodes as CP936
    #     -> mojibake. We print the raw block instead and let the human read it.
    #  2. A native command writing to stderr raises NativeCommandError when
    #     $ErrorActionPreference is 'Stop', so we relax it around the call.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $q = & schtasks /query /tn $fullName /v /fo LIST 2>&1
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($rc -eq 0) {
        $q | ForEach-Object { Write-Host ('  ' + $_.ToString()) }
        Write-Host ''
        Write-Host 'RESULT: task exists. Registration verified.'
        return $true
    }
    Write-Host ('  schtasks /query {0} -> exit {1}' -f $fullName, $rc)
    $q | Select-Object -First 2 | ForEach-Object { Write-Host ('  ' + $_.ToString()) }
    Write-Host ''
    Write-Host 'RESULT: task NOT found (error 2 / file-not-found means no such task path).'
    return $false
}

Write-Host '=== ZeroBound weekly curated rebuild task ==='
Write-Host ("repo     : {0}" -f $repo)
Write-Host ("task     : {0}" -f $fullName)
Write-Host ("runner   : {0}" -f $runner)
Write-Host ("elevated : {0}" -f (Test-Elevated))

if ($VerifyOnly) {
    [void](Show-Task)
    exit 0
}

if ($Unregister) {
    Write-Host ''
    Write-Host 'removing task ...'
    $out = & schtasks /delete /tn $fullName /f 2>&1
    $rc = $LASTEXITCODE
    $out | ForEach-Object { Write-Host ('  ' + $_) }
    if ($rc -eq 0) { Write-Host 'result   : removed'; exit 0 }
    Write-Host 'result   : not removed'
    exit $rc
}

if (-not (Test-Elevated)) {
    Write-Host ''
    Write-Host 'not elevated -> re-launching with a UAC prompt ...'
    Write-Host '  (answer Yes in the Windows prompt; the elevated window stays open'
    Write-Host '   and prints the result. This window can be closed.)'
    $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $PSCommandPath)
    if ($RunNow) { $fwd += '-RunNow' }
    if ($Force) { $fwd += '-Force' }
    if ($PSBoundParameters.ContainsKey('Time')) { $fwd += @('-Time', $Time) }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $fwd -Verb RunAs -ErrorAction Stop
        Write-Host ''
        Write-Host 'elevated instance started. Watch the new window.'
        exit 0
    } catch {
        Write-Host ''
        Write-Host ('ERROR: could not elevate automatically: {0}' -f $_.Exception.Message)
        Write-Host '       Do it by hand: right-click PowerShell -> Run as Administrator, then:'
        Write-Host ('       powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -RunNow' -f $PSCommandPath)
        exit 2
    }
}

if (-not (Test-Path -LiteralPath $runner)) {
    Write-Host ('ERROR: runner not found: {0}' -f $runner)
    exit 2
}

$existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    Write-Host ''
    Write-Host 'task already exists; re-run with -Force to overwrite.'
    [void](Show-Task)
    exit 1
}

# --- build with the native cmdlets: -Execute and -Argument stay separate,
#     so a path with spaces can never be re-split by the command line. ---
Write-Host ''
Write-Host 'registering ...'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runner) `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

try {
    $task = Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
        -Action $action -Trigger $trigger -Settings $settings `
        -Description 'ZeroBound weekly curated-layer rebuild (stage-only, kg_sync guarded)' `
        -Force:$Force -ErrorAction Stop
    Write-Host ('  registered: {0}{1}  state={2}' -f $taskPath, $task.TaskName, $task.State)
} catch {
    Write-Host ('ERROR: Register-ScheduledTask failed: {0}' -f $_.Exception.Message)
    exit 1
}

$ok = Show-Task

if ($RunNow) {
    Write-Host ''
    Write-Host 'starting the task now (so you can see the log today) ...'
    $s = Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
    if ($?) {
        Write-Host ('  started. log: {0}' -f (Join-Path $repo 'logs\curated_weekly.log'))
        Write-Host '  it takes ~15 minutes; watch the log or: Get-ScheduledTaskInfo -TaskName CuratedWeekly -TaskPath "\ZeroBound\"'
    } else {
        Write-Host '  could not start it now; it will still run Sunday.'
    }
}

Write-Host ''
Write-Host 'schedule : weekly, Sunday ' + $Time + ', limited privileges'
Write-Host 'rollback : tools\register_weekly_curated.ps1 -Unregister'
Write-Host 'NOTE     : stage-only. Switching the live layer stays manual (--replace).'
if (-not $ok) { exit 1 }
exit 0
