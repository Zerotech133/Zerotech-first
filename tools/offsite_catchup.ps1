#requires -Version 5.1
<#
.SYNOPSIS
    Catch-up pusher for missed offsite backups (idempotent, quiet when link is down).

.DESCRIPTION
    Background (2026-10-30): a daily backup ran fine locally on 2026-10-29 while the
    VPN link was down; the offsite push failed, an alert fired, but nothing retried
    when the link came back. The gap sat until a human pushed it manually.

    What this script does, every run:
      1. Probe aiosbackup@10.205.0.118 with ssh (one retry). If unreachable:
         append a SKIP line to the log and exit 0. No alert storm.
      2. For each of the last -SinceDays days (not including today):
           * if the remote tar-set dir <RemoteRoot>/<yyyy-MM-dd> is missing AND a
             local tar set exists -> run tools/offsite_push_remote.ps1 -Date <day>
           * if the remote vault dir <RemoteRoot>/vault/<yyyy-MM-dd> is missing AND a
             local vault day dir exists -> run tools/offsite_push_vault.ps1 -Date <day>
      3. Re-check remote existence, append a one-line summary, exit:
           0 = nothing missing, or every missing day was pushed and verified
           3 = at least one day still missing after the attempt
           1 = unexpected error (bad paths, push script missing, ...)

    Safety rules (same as the two push scripts it calls):
      * The push scripts are idempotent and verify with SHA256 on both sides; this
        script never deletes anything and never touches remote paths outside the
        dated backup roots.
      * Days whose local copy is absent are skipped (nothing to push from).
      * Pure ASCII (Windows PowerShell 5.1 reads BOM-less UTF-8 .ps1 as CP936).

.PARAMETER SinceDays
    Look-back window in days (default 7, 1..60). Day 0 (today) is excluded because
    the daily backup runs at 03:00; a partial "today" set is never treated as a gap.

.PARAMETER DryRun
    Print the plan and exit; no network writes, no pushes, no receipts.

.PARAMETER ForceProbeFail
    Test switch: behave as if ssh probe failed (used to verify the quiet path).

.PARAMETER MockRemoteMissing
    Test switch: treat these yyyy-MM-dd dates as remote-missing during detection
    (only meaningful with -DryRun; never pushes). Comma-separated.

.PARAMETER LogPath
    Append-only log (default C:\Temp\offsite_catchup.log).

.PARAMETER BackoffSeconds
    Sleep between two pushes (default 5) so the link is not saturated.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_catchup.ps1 -DryRun
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_catchup.ps1 -SinceDays 3
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_catchup.ps1 -ForceProbeFail
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 60)][int]$SinceDays = 3,
    [switch]$DryRun,
    [switch]$ForceProbeFail,
    [string]$MockRemoteMissing = '',
    [string]$LogPath = 'C:\Temp\offsite_catchup.log',
    [string]$BackupRoot = '',
    [string]$VaultRoot = '',
    [int]$BackoffSeconds = 5
)

$ErrorActionPreference = 'Continue'

if (-not $PSScriptRoot) { throw 'Cannot resolve $PSScriptRoot. Run this file with -File.' }
$toolsDir   = $PSScriptRoot
$repoRoot   = Split-Path -Parent $toolsDir
$workspace  = Split-Path -Parent $repoRoot
if (-not $BackupRoot) { $BackupRoot = Join-Path $repoRoot 'backups' }
if (-not $VaultRoot) { $VaultRoot = Join-Path $workspace '_vault' }
$PushTar    = Join-Path $toolsDir 'offsite_push_remote.ps1'
$PushVault  = Join-Path $toolsDir 'offsite_push_vault.ps1'

$RemoteHost = 'aiosbackup@10.205.0.118'
$RemoteRoot = '/home/aios-offsite'
$Key        = 'C:\Users\t\.ssh\zeroq_deploy'
$SshOpts    = @('-i', $Key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')

function Write-Log {
    param([string]$Message)
    $line = ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    Write-Host $line
    try {
        $logDir = Split-Path -Parent $LogPath
        if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        $line | Add-Content -LiteralPath $LogPath -Encoding UTF8
    } catch {
        Write-Host ('[warn] log append failed: {0}' -f $_.Exception.Message)
    }
}

function Invoke-RemoteText {
    param([string]$Command)
    $raw = & ssh @SshOpts $RemoteHost $Command 2>&1
    return @{ Code = $LASTEXITCODE; Out = (($raw | Out-String).Trim()) }
}

function Test-RemoteDir {
    param([string]$Path)
    if ($script:MockMissing -contains $Path) { return $false }
    $r = Invoke-RemoteText ("test -d '{0}' && echo PRESENT || echo ABSENT" -f $Path)
    return ($r.Out -match 'PRESENT')
}

function Find-LocalTarSet {
    param([string]$Day)
    if (-not (Test-Path -LiteralPath $BackupRoot)) { return $null }
    $d = Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like ($Day + '*') } |
        Sort-Object Name -Descending | Select-Object -First 1
    return $d
}

function Find-LocalVaultDay {
    param([string]$Day)
    $p = Join-Path $VaultRoot $Day
    if (Test-Path -LiteralPath $p -PathType Container) { return (Get-Item -LiteralPath $p) }
    return $null
}

function Get-ReceiptState {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $text = (Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue)
    if ($text -match 'mismatch=0' -and $text -match 'missing=0') { return 'OK' }
    return 'BAD'
}

# ---------------------------------------------------------------- main
Write-Log ('=== offsite catchup start === dryrun={0} since={1}d forceprobe={2}' -f `
    [bool]$DryRun, $SinceDays, [bool]$ForceProbeFail)

$script:MockMissing = @()
if ($MockRemoteMissing) {
    foreach ($m in ($MockRemoteMissing -split ',')) {
        $mm = $m.Trim()
        if ($mm -match '^\d{4}-\d{2}-\d{2}$') {
            $script:MockMissing += (($RemoteRoot.TrimEnd('/')) + '/' + $mm)
            $script:MockMissing += (($RemoteRoot.TrimEnd('/')) + '/vault/' + $mm)
        }
    }
}

if ($ForceProbeFail) {
    Write-Log 'probe : FAIL (forced by -ForceProbeFail) -> SKIP quiet, nothing pushed'
    exit 0
}

if (-not $DryRun) {
    $probe = Invoke-RemoteText 'echo OK'
    if ($probe.Code -ne 0 -or $probe.Out -notmatch 'OK') {
        Write-Log ('probe : FAIL (ssh unreachable) -> SKIP quiet; first 160 chars: {0}' -f `
            ($probe.Out -replace "`r?`n", ' ').Substring(0, [Math]::Min(160, ($probe.Out -replace "`r?`n", ' ').Length)))
        exit 0
    }
    Write-Log 'probe : OK (aiosbackup ssh reachable)'
} else {
    Write-Log 'probe : skipped (-DryRun)'
}

$missingDays = @()
$pushed = @()
$skippedLocal = @()

for ($i = 1; $i -le $SinceDays; $i++) {
    $day = (Get-Date).AddDays(-1 * $i).ToString('yyyy-MM-dd')
    $tarRemote = ($RemoteRoot.TrimEnd('/')) + '/' + $day
    $vaultRemote = ($RemoteRoot.TrimEnd('/')) + '/vault/' + $day
    $tarLocal = Find-LocalTarSet -Day $day
    $vaultLocal = Find-LocalVaultDay -Day $day

    if (-not $tarLocal -and -not $vaultLocal) {
        $skippedLocal += $day
        continue
    }

    $tarPresent = $true
    $vaultPresent = $true
    if ($tarLocal) { $tarPresent = Test-RemoteDir -Path $tarRemote }
    if ($vaultLocal) { $vaultPresent = Test-RemoteDir -Path $vaultRemote }

    if ($tarPresent -and $vaultPresent) { continue }

    $dayMissing = @()
    if (-not $tarPresent) { $dayMissing += 'tar' }
    if (-not $vaultPresent) { $dayMissing += 'vault' }
    $missingDays += ('{0}({1})' -f $day, ($dayMissing -join '+'))

    if ($DryRun) {
        if (-not $tarPresent) { Write-Log ('[PLAN] push tar   {0}' -f $day) }
        if (-not $vaultPresent) { Write-Log ('[PLAN] push vault {0}' -f $day) }
        continue
    }

    if (-not $tarPresent) {
        Write-Log ('push : tar {0} ...' -f $day)
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $PushTar -Date $day 2>&1
        $rc = $LASTEXITCODE
        $outTail = (($out | Out-String).Trim())
        if ($outTail.Length -gt 400) { $outTail = $outTail.Substring($outTail.Length - 400) }
        Write-Log ('push : tar {0} rc={1} tail={2}' -f $day, $rc, ($outTail -replace "`r?`n", ' | '))
        $receipt = $null
        if ($tarLocal) { $receipt = Get-ReceiptState -Path (Join-Path $tarLocal.FullName 'offsite_remote_receipt.txt') }
        if ($rc -eq 0 -or $receipt -eq 'OK') { $pushed += ('{0}:tar' -f $day) }
        if ($BackoffSeconds -gt 0) { Start-Sleep -Seconds $BackoffSeconds }
    }
    if (-not $vaultPresent) {
        Write-Log ('push : vault {0} ...' -f $day)
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $PushVault -Date $day 2>&1
        $rc = $LASTEXITCODE
        $outTail = (($out | Out-String).Trim())
        if ($outTail.Length -gt 400) { $outTail = $outTail.Substring($outTail.Length - 400) }
        Write-Log ('push : vault {0} rc={1} tail={2}' -f $day, $rc, ($outTail -replace "`r?`n", ' | '))
        $vreceipt = Get-ReceiptState -Path (Join-Path (Join-Path $VaultRoot $day) 'offsite_vault_receipt.txt')
        if ($rc -eq 0 -or $vreceipt -eq 'OK') { $pushed += ('{0}:vault' -f $day) }
        if ($BackoffSeconds -gt 0) { Start-Sleep -Seconds $BackoffSeconds }
    }
}

# ---------------------------------------------------------------- final state
$stillMissing = @()
if (-not $DryRun -and $missingDays.Count -gt 0) {
    for ($i = 1; $i -le $SinceDays; $i++) {
        $day = (Get-Date).AddDays(-1 * $i).ToString('yyyy-MM-dd')
        $tarLocal = Find-LocalTarSet -Day $day
        $vaultLocal = Find-LocalVaultDay -Day $day
        if (-not $tarLocal -and -not $vaultLocal) { continue }
        $tarRemote = ($RemoteRoot.TrimEnd('/')) + '/' + $day
        $vaultRemote = ($RemoteRoot.TrimEnd('/')) + '/vault/' + $day
        if ($tarLocal -and -not (Test-RemoteDir -Path $tarRemote)) { $stillMissing += ('{0}:tar' -f $day) }
        if ($vaultLocal -and -not (Test-RemoteDir -Path $vaultRemote)) { $stillMissing += ('{0}:vault' -f $day) }
    }
}

Write-Log ('summary : scanned={0}d missing={1} pushed={2} stillMissing={3} localOnly={4}' -f `
    $SinceDays, ($missingDays -join ', '), ($pushed -join ', '), ($stillMissing -join ', '), ($skippedLocal -join ','))

if ($DryRun) {
    Write-Log 'result : DRY-RUN OK (plan only)'
    exit 0
}
if ($stillMissing.Count -gt 0) {
    Write-Log 'result : INCOMPLETE - some days still missing; alerts/next run will retry'
    exit 3
}
Write-Log 'result : OK'
exit 0
