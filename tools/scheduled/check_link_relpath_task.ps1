# check_link_relpath_task.ps1 - daily guard for alias-junction path drift
#
# Why this exists (2026-11-16 case):
#   A scheduled task started via C:\aios (the ASCII junction for the project
#   root) computed DELIVERABLES as C:\deliverables (nonexistent), because
#   os.path.abspath does NOT resolve junctions - it is pure string math.
#   The suppression order was therefore never read, and "file not found" was
#   treated as "no suppression today". It stayed silent for half a month.
#
#   That class of bug never raises. Wrong path and "nothing there" return the
#   same value, so nobody notices. Only an active measurement can surface it.
#
# What it does: runs tools/audit/check_link_relpath.py --quiet and writes its
#   "LNK:" line to the log.
#
# Exit code:
#   0 = scanned, no defect
#   1 = scanned, N defects found (real problem, needs action)
#   2 = crash (nothing was verified in this run)
#   Judge by the "LNK:" line in the log, not by the exit code alone.
#   (Same rule as close_review_task.ps1: LastTaskResult answers "did it run",
#    not "is the answer good".)
#
# Note: this launcher starts the script through C:\aios on purpose. The guard
#   must work when invoked via the alias - that is exactly what it protects.

param(
    [switch]$WhatIf,
    [string]$Log = 'C:\Temp\link_relpath.log'
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
$ErrorActionPreference = 'Continue'

$py = 'C:\Program Files\Python311\python.exe'
$root = 'C:\aios'
$entry = Join-Path $root 'tools\audit\check_link_relpath.py'

function Write-Log([string]$msg) {
    # Normalize to a single line before writing: native command output is
    # wrapped at the current console width, and a scheduled task has no
    # console, so the same text can arrive hard-wrapped differently than when
    # the script is run by hand.
    $one = ($msg -replace '[\r\n]+', ' ').Trim()
    if ($one.Length -eq 0) { return }
    ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $one) |
        Out-File $Log -Append -Encoding utf8
}

if ($WhatIf) {
    Write-Output "py    = $py"
    Write-Output "root  = $root"
    Write-Output "entry = $entry"
    Write-Output "exists= $(Test-Path $entry)"
    Write-Output "log   = $Log"
    exit 0
}

Write-Log '=== link_relpath guard start ==='

if (-not (Test-Path $entry)) {
    Write-Log "LNK: CRASH entry not found: $entry"
    exit 2
}

try {
    $out = & $py -X utf8 $entry --quiet 2>&1 | Out-String
    $rc = if ($LASTEXITCODE -eq $null) { 2 } else { $LASTEXITCODE }
    Write-Log ('=== link_relpath guard end rc=' + $rc + ' ===')
    foreach ($line in ($out -split "`r?`n")) { Write-Log $line }
    if ($rc -eq 0) { exit 0 }
    if ($rc -eq 1) { exit 1 }
    exit 2
} catch {
    Write-Log ('LNK: CRASH ' + $_.Exception.Message)
    exit 2
}
