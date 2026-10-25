#requires -Version 5.1
<#
.SYNOPSIS
    Push one _vault bundle day set to the offsite server and verify it byte-for-byte.

.DESCRIPTION
    Companion to tools/offsite_push_remote.ps1 (which pushes backups\<date> tar sets).
    This one pushes <workspace>\_vault\<yyyy-MM-dd>\ to a SEPARATE remote root
    <RemoteRoot>/vault/<yyyy-MM-dd>/ so the existing tar-set retention (which only
    matches dated dirs directly under /home/aios-offsite) can never prune a vault set.

    Why a new script instead of adding -Vault to offsite_push_remote.ps1:
      * zero regression risk for the proven daily tar push - its receipt contract
        backups\<date>\offsite_remote_receipt.txt is consumed by ops/N1 and the
        BackupDaily inline offsite step, so it stays untouched;
      * vault has a different layout (MANIFEST.txt + fixed artifact set), a different
        receipt (offsite_vault_receipt.txt inside the vault day dir) and a different
        retention (KeepDays 14, matching tools\backup_bundles.ps1 -KeepDays);
      * a -DryRun mode can be added here without touching the daily critical path.

    Rules baked in (same as offsite_push_remote.ps1):
      * Verify by SHA256 on BOTH sides. Size-only comparison is not evidence.
      * Never delete remotely except inside our own vault root, dated dirs only.
      * Idempotent: a day already present and verified is skipped unless -Force.
      * Pure ASCII (PS 5.1 reads a BOM-less UTF-8 .ps1 as CP936).

.PARAMETER Date
    Vault day folder (yyyy-MM-dd). Default = newest dated folder under the vault root.

.PARAMETER VaultRoot
    Vault root. Default = <workspace>\_vault, derived from $PSScriptRoot.

.PARAMETER DryRun
    Print the exact plan (files/bytes, ssh/scp commands) and exit 0. Does NOT touch
    the network and does NOT write a receipt. Safe for verification runs.

.PARAMETER Verify
    Do not copy; only compare local hashes against the remote copy.

.PARAMETER Force
    Recopy even when the remote day folder already exists.

.PARAMETER KeepDays
    Remote vault retention: delete our dated dirs older than this many days
    (default 14, same as backup_bundles.ps1). Only paths matching ^\d{4}-\d{2}-\d{2}$
    directly under <RemoteRoot>/vault are ever removed.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_push_vault.ps1 -DryRun
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_push_vault.ps1 -Date 2026-10-25
#>
[CmdletBinding()]
param(
    [string]$Date,
    [string]$VaultRoot = '',
    [switch]$Verify,
    [switch]$Force,
    [int]$KeepDays = 14,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

if (-not $PSScriptRoot) {
    throw 'Cannot resolve $PSScriptRoot. Run this file with -File, do not paste it.'
}
$toolsDir  = $PSScriptRoot
$repoRoot  = Split-Path -Parent $toolsDir
$workspace = Split-Path -Parent $repoRoot
if (-not $VaultRoot) {
    $VaultRoot = Join-Path $workspace '_vault'
} elseif (-not [System.IO.Path]::IsPathRooted($VaultRoot)) {
    $VaultRoot = Join-Path (Get-Location).Path $VaultRoot
}
$VaultRoot = [System.IO.Path]::GetFullPath($VaultRoot).TrimEnd('\')

$Key        = 'C:\Users\t\.ssh\zeroq_deploy'
# 2026-10-24: dedicated account aiosbackup (same credentials as the tar push).
$RemoteHost = 'aiosbackup@10.205.0.118'
$RemoteRoot = '/home/aios-offsite'
$RemoteVaultRel = 'vault'
$SshOpts    = @('-i', $Key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')

function Invoke-Remote {
    param([string]$Command)
    if ($DryRun) { return @{ Code = 0; Out = '(dry-run) ' + $Command } }
    $raw = & ssh @SshOpts $RemoteHost $Command 2>&1
    return @{ Code = $LASTEXITCODE; Out = ($raw | Out-String).Trim() }
}

# ---------------------------------------------------------------- pick day
if (-not $Date) {
    $latest = Get-ChildItem -LiteralPath $VaultRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Write-Host 'ERROR: no yyyy-MM-dd day folder under the vault root'; exit 2 }
    $Date = $latest.Name
}
if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    Write-Host ("ERROR: bad -Date '{0}' (yyyy-MM-dd expected)" -f $Date); exit 2
}
$dayDir = Join-Path $VaultRoot $Date
if (-not (Test-Path -LiteralPath $dayDir -PathType Container)) {
    Write-Host ("ERROR: vault day folder not found: {0}" -f $dayDir); exit 2
}
# SAFETY: never operate outside our own vault root (defense in depth for the prune below).
$fullDay   = [System.IO.Path]::GetFullPath($dayDir).TrimEnd('\')
$fullVault = [System.IO.Path]::GetFullPath($VaultRoot).TrimEnd('\')
if (-not ($fullDay.StartsWith($fullVault + '\', [System.StringComparison]::OrdinalIgnoreCase))) {
    Write-Host ("ERROR: day folder escapes the vault root: {0}" -f $fullDay); exit 2
}

$files = @(Get-ChildItem -LiteralPath $dayDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '^offsite_vault_receipt' })
if (-not $files.Count) {
    Write-Host ("ERROR: vault day folder has no files: {0}" -f $dayDir); exit 2
}
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
$remoteTarget = ('{0}/{1}/{2}' -f $RemoteRoot, $RemoteVaultRel, $Date)

Write-Host '=== vault offsite push (remote) ==='
Write-Host ("mode   : {0}" -f $(if ($DryRun) { 'DRY-RUN (no network, no writes)' } else { 'REAL RUN' }))
Write-Host ("source : {0}" -f $dayDir)
Write-Host ("target : {0}:{1}/" -f $RemoteHost, $remoteTarget)
Write-Host ("files  : {0}, {1:N0} bytes ({2:N2} MB)" -f $files.Count, $bytes, ($bytes / 1MB))
Write-Host ''

# ---------------------------------------------------------------- dry-run plan
if ($DryRun) {
    Write-Host 'plan (nothing below is executed):'
    Write-Host ("  1) ssh {0} 'mkdir -p {1}'" -f $RemoteHost, $remoteTarget)
    Write-Host ("  2) scp {0} file(s) -> {1}:{2}/" -f $files.Count, $RemoteHost, $remoteTarget)
    Write-Host ("  3) ssh {0} 'cd {1} && sha256sum *' then compare all {2} local hashes" -f $RemoteHost, $remoteTarget, $files.Count)
    Write-Host ("  4) receipt -> {0}" -f (Join-Path $dayDir 'offsite_vault_receipt.txt'))
    Write-Host ("  5) retention: remote vault dated dirs older than {0} day(s)" -f $KeepDays)
    foreach ($f in $files) {
        Write-Host ("  [plan] {0,-34} {1,14:N0} bytes" -f $f.Name, $f.Length)
    }
    Write-Host ''
    Write-Host 'result : OK (plan only)'
    exit 0
}

# ---------------------------------------------------------------- connectivity
$probe = Invoke-Remote 'echo OK'
if ($probe.Code -ne 0 -or $probe.Out -notmatch 'OK') {
    Write-Host ("ERROR: ssh unreachable: {0}" -f $probe.Out); exit 2
}

# ---------------------------------------------------------------- copy
if (-not $Verify) {
    $exists = Invoke-Remote ("test -d {0} && echo YES || echo NO" -f $remoteTarget)
    if ($exists.Out -match 'YES' -and -not $Force) {
        Write-Host 'remote vault day already exists; verifying instead of copying (-Force to recopy)'
    } else {
        $mk = Invoke-Remote ("mkdir -p {0}" -f $remoteTarget)
        if ($mk.Code -ne 0) { Write-Host ("ERROR: mkdir failed: {0}" -f $mk.Out); exit 2 }
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $scpOut = & scp -i $Key -o BatchMode=yes -o ConnectTimeout=15 -r `
            (Join-Path $dayDir '*') `
            ("{0}:{1}/" -f $RemoteHost, $remoteTarget) 2>&1
        $scpCode = $LASTEXITCODE
        $sw.Stop()
        if ($scpCode -ne 0) {
            Write-Host ("ERROR: scp failed rc={0}: {1}" -f $scpCode, (($scpOut | Out-String).Trim()))
            exit 1
        }
        $mbps = if ($sw.Elapsed.TotalSeconds -gt 0) { [math]::Round(($bytes / 1MB) / $sw.Elapsed.TotalSeconds, 2) } else { 0 }
        Write-Host ("copy   : done in {0:N0}s ({1} MB/s)" -f $sw.Elapsed.TotalSeconds, $mbps)
    }
} else {
    Write-Host 'copy   : skipped (-Verify)'
}
Write-Host ''

# ---------------------------------------------------------------- verify
$remote = Invoke-Remote ("cd {0} && sha256sum * 2>/dev/null" -f $remoteTarget)
if ($remote.Code -ne 0) {
    Write-Host ("ERROR: remote sha256sum failed: {0}" -f $remote.Out); exit 2
}
$rmap = @{}
foreach ($line in ($remote.Out -split "`n")) {
    $m = [regex]::Match($line, '^([0-9a-f]{64})\s+(.+)$')
    if ($m.Success) { $rmap[$m.Groups[2].Value.Trim()] = $m.Groups[1].Value }
}

$ok = 0; $bad = 0; $miss = 0
foreach ($f in $files) {
    $lh = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
    if (-not $rmap.ContainsKey($f.Name)) {
        Write-Host ("  [MISSING] {0}" -f $f.Name); $miss++; continue
    }
    if ($rmap[$f.Name] -eq $lh) {
        $ok++
    } else {
        Write-Host ("  [MISMATCH] {0} local={1} remote={2}" -f $f.Name, $lh.Substring(0,12), $rmap[$f.Name].Substring(0,12))
        $bad++
    }
}
Write-Host ("verify : {0} ok, {1} mismatch, {2} missing (of {3})" -f $ok, $bad, $miss, $files.Count)
Write-Host ''

# ---------------------------------------------------------------- retention
$pruned = 0
if (-not $Verify -and $KeepDays -gt 0) {
    $cmd = "find {0}/{1} -maxdepth 1 -type d -name '20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]' -mtime +{2} -print" -f $RemoteRoot, $RemoteVaultRel, $KeepDays
    $list = Invoke-Remote $cmd
    $dirs = @(($list.Out -split "`n") | Where-Object { $_ -match '/\d{4}-\d{2}-\d{2}$' })
    if ($dirs.Count -gt 0) {
        foreach ($d in $dirs) {
            $r = Invoke-Remote ("rm -rf '{0}'" -f $d.Trim())
            if ($r.Code -eq 0) { $pruned++; Write-Host ("  [PRUNE] {0}" -f $d.Trim()) }
        }
    }
    Write-Host ("retain : pruned {0} vault dir(s) older than {1} days" -f $pruned, $KeepDays)
}

# ---------------------------------------------------------------- receipt
if (-not $Verify) {
    $stampNow = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
    $rc = ("offsite vault: {0} -> {1}:{2} | files={3} ok={4} mismatch={5} missing={6} pruned={7} at={8}" -f `
        $dayDir, $RemoteHost, $remoteTarget, $files.Count, $ok, $bad, $miss, $pruned, $stampNow)
    $rc | Set-Content -LiteralPath (Join-Path $dayDir 'offsite_vault_receipt.txt') -Encoding UTF8
    Write-Host ("receipt: {0}" -f (Join-Path $dayDir 'offsite_vault_receipt.txt'))
}

Write-Host ''
if ($bad -gt 0 -or $miss -gt 0) {
    Write-Host 'RESULT: FAIL - remote copy differs from source; DO NOT treat as a backup.'
    exit 1
}
Write-Host 'RESULT: OK - remote vault copy is byte-identical to the source.'
exit 0
