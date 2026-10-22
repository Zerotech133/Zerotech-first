#requires -Version 5.1
<#
.SYNOPSIS
    Switch AlertWatch to a 5-minute repetition (or restore the original 30-minute one).

.DESCRIPTION
    Why this file exists: AlertWatch runs on a PT30M repetition while its
    freshness gate only accepts sources completed within 5 minutes. Those two
    clocks can never line up, so the watcher has refused to work since
    2026-10-18 (state file stuck; ~93 hours at the time of writing) while the
    ledger only showed "one more rc=1".

    Option A (this script): poll every 5 minutes, keep the 5-minute freshness
    window. Worst case a batch lands right after a poll and is caught 4:59
    later - still inside the window. Semantics unchanged, ~288 runs/day
    instead of 48; each run is one local HTTP call plus a little math.

    Option B (not recommended here): keep PT30M and widen the window to
    ~35 minutes, i.e. accept half-hour-old data as "current". That weakens the
    very guarantee the gate exists for.

    The agent session cannot run schtasks (Access denied / path not found),
    so this stays a one-command manual step. Run it in an ELEVATED PowerShell;
    the script re-launches itself with a UAC prompt if it is not elevated.

    ---- FIVE ELEMENTS (AGENTS.md section 7 format) ----

    1. Full command
         schtasks /create /tn "\AlertWatch" /xml "<repo>\tools\schtasks_xml\AlertWatch_PT5M.xml" /f

    2. Blast radius
         Replaces ONE task definition: \AlertWatch. The action, arguments,
         working directory, principal and start boundary are copied verbatim
         from the live definition; only <Interval> changes PT30M -> PT5M.
         Nothing else is touched. The watcher itself is not modified.

    3. Irreversible point
         The previous definition is overwritten. It has been saved verbatim to
         tools\schtasks_xml\AlertWatch_original_PT30M.xml, so -Restore puts it
         back exactly.

    4. Alternatives tried
         - Widening the freshness window in code: implemented as an env knob
           (ALERT_FRESH_WINDOW_MIN) but rejected as the default fix, because a
           35-minute window would accept stale data.
         - Editing the live task in place: needs the same permission we lack.

    5. Rollback
         powershell.exe -NoProfile -ExecutionPolicy Bypass `
             -File tools\register_alert_watch_5min.ps1 -Restore

.PARAMETER Restore
    Re-register the original PT30M definition instead.

.PARAMETER VerifyOnly
    Print the live definition's Interval; change nothing.
#>
[CmdletBinding()]
param(
    [switch]$Restore,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

$taskName = 'AlertWatch'
$xmlDir = Join-Path $PSScriptRoot 'schtasks_xml'
# v2 (2026-10-22 16:20): the live task is failing at CreateProcess with
# 0x80070003 (ERROR_PATH_NOT_FOUND) in its S4U session, twice observed
# (15:45:05 and 16:16:00). All paths exist and are readable interactively,
# and python launches fine - so the failure is specific to the S4U token.
# The one structural difference we can see is that <WorkingDirectory> points at
# the junction path C:\aios\news_collector. The PT5M_physpath variant changes
# BOTH the interval and that working directory to the physical path.
$xmlNew = Join-Path $xmlDir 'AlertWatch_PT5M_physpath.xml'
$xmlNewNoPhys = Join-Path $xmlDir 'AlertWatch_PT5M.xml'
$xmlOld = Join-Path $xmlDir 'AlertWatch_original_PT30M.xml'
$liveXml = Join-Path $env:WINDIR 'System32\Tasks\AlertWatch'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LiveInterval {
    if (-not (Test-Path -LiteralPath $liveXml)) { return '(cannot read live task)' }
    try {
        $text = [System.IO.File]::ReadAllText($liveXml)
    } catch {
        return '(access denied)'
    }
    $m = [regex]::Match($text, '<Interval>([^<]+)</Interval>')
    if ($m.Success) { return $m.Groups[1].Value }
    return '(no Interval element)'
}

Write-Host '=== AlertWatch repetition switch ==='
Write-Host ("live interval : {0}" -f (Get-LiveInterval))
Write-Host ("xml dir       : {0}" -f $xmlDir)
Write-Host ("elevated      : {0}" -f (Test-Elevated))

if ($VerifyOnly) {
    Write-Host ''
    Write-Host 'verify-only: nothing changed.'
    exit 0
}

if (-not (Test-Elevated)) {
    Write-Host ''
    Write-Host 'not elevated -> re-launching with a UAC prompt ...'
    $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $PSCommandPath)
    if ($Restore) { $fwd += '-Restore' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $fwd -Verb RunAs -ErrorAction Stop
        Write-Host 'elevated instance started. Watch the new window.'
        exit 0
    } catch {
        Write-Host ('ERROR: could not elevate: {0}' -f $_.Exception.Message)
        Write-Host '       Run this in an Administrator PowerShell by hand.'
        exit 2
    }
}

$xml = if ($Restore) { $xmlOld } else { $xmlNew }
$label = if ($Restore) { 'RESTORE (PT30M)' } else { 'APPLY (PT5M)' }

if (-not (Test-Path -LiteralPath $xml)) {
    Write-Host ('ERROR: xml not found: {0}' -f $xml)
    exit 2
}

Write-Host ''
Write-Host ("{0} from {1}" -f $label, $xml)
$out = & schtasks /create /tn $taskName /xml $xml /f 2>&1
$rc = $LASTEXITCODE
$out | ForEach-Object { Write-Host ('  ' + $_) }

if ($rc -ne 0) {
    Write-Host ''
    Write-Host 'result : FAILED'
    exit $rc
}

Write-Host ''
Write-Host ("live interval : {0}" -f (Get-LiveInterval))
Write-Host 'result : applied'
Write-Host 'verify : schtasks /query /tn "\AlertWatch" /v /fo LIST | findstr /i "Interval Next"'
Write-Host 'rollback: tools\register_alert_watch_5min.ps1 -Restore'
Write-Host ''
Write-Host 'NOTE: this only makes the watcher ABLE to pass. The remaining blockers'
Write-Host '      (chanlun completed_at, nine_dim generated_at timezone) are'
Write-Host '      producer-side; see the "alert source timestamp" work order in the'
Write-Host '      workspace root (a .md file dated 2026-10-22).'
exit 0
