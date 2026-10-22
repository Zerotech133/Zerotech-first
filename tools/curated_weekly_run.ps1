#requires -Version 5.1
<#
.SYNOPSIS
    Weekly curated-layer rebuild (stage-only). Called by the scheduled task.

.DESCRIPTION
    Runs tools\refresh_curated.py in STAGE-ONLY mode (no --replace), with the
    kg_sync guard enabled, and appends the outcome to logs\curated_weekly.log.

    Why stage-only: switching the live layer is a destructive step that should
    not happen unattended on its first automation. This task produces a
    verified .tmp; a human reviews the log and runs --replace when it looks right.

    The script never deletes the live curated layer and never writes the raw
    layer (refresh_curated opens the raw layer read-only).
#>
[CmdletBinding()]
param(
    [int]$GuardMinutes = 60,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo 'logs'
$logPath = Join-Path $logDir 'curated_weekly.log'
$tool = Join-Path $PSScriptRoot 'refresh_curated.py'

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Text)
    $line = '{0} | {1}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss'), $Text
    Write-Host $line
    try {
        Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    } catch {
        Write-Host ("WARN: cannot append to log: {0}" -f $_.Exception.Message)
    }
}

Write-Log '=== curated weekly rebuild START ==='
Write-Log ('repo={0} guard={1}min dry_run={2}' -f $repo, $GuardMinutes, [bool]$DryRun)

if (-not (Test-Path -LiteralPath $tool)) {
    Write-Log ('ERROR runner missing: {0}' -f $tool)
    exit 2
}

$python = 'C:\Program Files\Python311\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $cmd) {
        Write-Log 'ERROR: python not found (neither fixed path nor PATH)'
        exit 2
    }
    $python = $cmd.Source
}

$argv = @('-B', $tool, '--guard-minutes', [string]$GuardMinutes)
if ($DryRun) { $argv += '--dry-run' }

Write-Log ('exec: {0} {1}' -f $python, ($argv -join ' '))
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$out = & $python @argv 2>&1
$rc = $LASTEXITCODE
$sw.Stop()
foreach ($line in $out) {
    Write-Log ('  ' + [string]$line)
}
Write-Log ('=== curated weekly rebuild END exit={0} elapsed={1:N1}s ===' -f `
        $rc, $sw.Elapsed.TotalSeconds)

if ($rc -eq 0) {
    Write-Log 'NEXT: review the verify lines above, then decide whether to run'
    Write-Log '      python tools\refresh_curated.py --replace'
} elseif ($rc -eq 2) {
    Write-Log 'SKIPPED/REFUSED (exit 2). If it was the kg_sync guard, retry later.'
}
exit $rc
