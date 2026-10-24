#requires -Version 5.1
<#
.SYNOPSIS
    Push the newest local backup snapshot to the offsite server and verify it byte-for-byte.

.DESCRIPTION
    Why this exists: until 2026-10-24 this project had **zero** offsite copies -
    every repo, 46 GB of market data and 7 days of backups lived on one physical
    disk. `C:\backups_mirror` was a false comfort: sha256 sampling proved it is a
    byte-identical copy on the SAME disk.

    Target: 10.205.0.118 (CentOS, /home = 2.0 TB, ~1.9 TB free), key-auth only.

    Rules baked in (each paid for):
      * Verify by SHA256 on BOTH sides. Size-only comparison was proven wrong here.
      * Never delete remotely except inside our own dated directories older than -KeepDays.
      * Idempotent: a day already present and verified is skipped unless -Force.
      * Pure ASCII (PS 5.1 reads BOM-less UTF-8 as CP936).

.PARAMETER Verify
    Do not copy; only compare local hashes against the remote copy.

.PARAMETER KeepDays
    Remote retention: delete our dated dirs older than this many days (default 30).
    Only paths under $RemoteRoot that match ^\d{4}-\d{2}-\d{2}$ are ever removed.

.PARAMETER Date
    Specific snapshot (yyyy-MM-dd). Default = newest under backups\.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_push_remote.ps1 -Verify
#>
[CmdletBinding()]
param(
    [switch]$Verify,
    [switch]$Force,
    [int]$KeepDays = 30,
    [string]$Date
)

$ErrorActionPreference = 'Continue'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$BackupRoot = Join-Path $RepoRoot 'backups'
$Key        = 'C:\Users\t\.ssh\zeroq_deploy'
# 2026-10-24: switched to the dedicated account aiosbackup (no longer root).
# Its authorized_keys carries from="192.168.204.12", and /home/aios-offsite is
# owned by it. root is now only used for one-off admin (creating users, sshd).
$RemoteHost = 'aiosbackup@10.205.0.118'
$RemoteRoot = '/home/aios-offsite'
$SshOpts    = @('-i', $Key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')

function Invoke-Remote {
    param([string]$Command)
    $raw = & ssh @SshOpts $RemoteHost $Command 2>&1
    return @{ Code = $LASTEXITCODE; Out = ($raw | Out-String).Trim() }
}

# ---------------------------------------------------------------- pick snapshot
if (-not $Date) {
    $latest = Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{4}$' } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Write-Host 'ERROR: no dated snapshot under backups\'; exit 2 }
    $Date = $latest.Name.Substring(0, 10)
}
$localDir = Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "$Date*" } | Sort-Object Name -Descending | Select-Object -First 1
if (-not $localDir) { Write-Host "ERROR: snapshot for $Date not found"; exit 2 }

$files = Get-ChildItem -LiteralPath $localDir.FullName -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '^offsite_remote_receipt' }
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
Write-Host '=== offsite push (remote) ==='
Write-Host ("source : {0}" -f $localDir.FullName)
Write-Host ("target : {0}:{1}/{2}/" -f $RemoteHost, $RemoteRoot, $Date)
Write-Host ("files  : {0}, {1:N0} bytes ({2:N2} GB)" -f $files.Count, $bytes, ($bytes / 1GB))
Write-Host ''

# ---------------------------------------------------------------- connectivity
$probe = Invoke-Remote 'echo OK'
if ($probe.Code -ne 0 -or $probe.Out -notmatch 'OK') {
    Write-Host ("ERROR: ssh unreachable: {0}" -f $probe.Out)
    exit 2
}

# ---------------------------------------------------------------- copy
if (-not $Verify) {
    $exists = Invoke-Remote ("test -d {0}/{1} && echo YES || echo NO" -f $RemoteRoot, $Date)
    if ($exists.Out -match 'YES' -and -not $Force) {
        Write-Host 'remote day already exists; verifying instead of copying (-Force to recopy)'
    } else {
        $mk = Invoke-Remote ("mkdir -p {0}/{1}" -f $RemoteRoot, $Date)
        if ($mk.Code -ne 0) { Write-Host ("ERROR: mkdir failed: {0}" -f $mk.Out); exit 2 }
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $scpOut = & scp -i $Key -o BatchMode=yes -o ConnectTimeout=15 -r `
            (Join-Path $localDir.FullName '*') `
            ("{0}:{1}/{2}/" -f $RemoteHost, $RemoteRoot, $Date) 2>&1
        $scpCode = $LASTEXITCODE
        $sw.Stop()
        if ($scpCode -ne 0) {
            Write-Host ("ERROR: scp failed rc={0}: {1}" -f $scpCode, ($scpOut | Out-String).Trim())
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
$remote = Invoke-Remote ("cd {0}/{1} && sha256sum * 2>/dev/null" -f $RemoteRoot, $Date)
if ($remote.Code -ne 0) { Write-Host ("ERROR: remote sha256sum failed: {0}" -f $remote.Out); exit 2 }
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
    # Only dated dirs under our own root; never touches anything else.
    $cmd = "find {0} -maxdepth 1 -type d -name '20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]' -mtime +{1} -print" -f $RemoteRoot, $KeepDays
    $list = Invoke-Remote $cmd
    $dirs = @(($list.Out -split "`n") | Where-Object { $_ -match '/\d{4}-\d{2}-\d{2}$' })
    if ($dirs.Count -gt 0) {
        foreach ($d in $dirs) {
            $r = Invoke-Remote ("rm -rf '{0}'" -f $d.Trim())
            if ($r.Code -eq 0) { $pruned++; Write-Host ("  [PRUNE] {0}" -f $d.Trim()) }
        }
    }
    Write-Host ("retain : pruned {0} dir(s) older than {1} days" -f $pruned, $KeepDays)
}

# ---------------------------------------------------------------- receipt
if (-not $Verify) {
    $stamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
    $rc = ("offsite remote: {0} -> {1}:{2}/{3} | files={4} ok={5} mismatch={6} missing={7} pruned={8} at={9}" -f `
        $localDir.FullName, $RemoteHost, $RemoteRoot, $Date, $files.Count, $ok, $bad, $miss, $pruned, $stamp)
    $rc | Set-Content -LiteralPath (Join-Path $localDir.FullName 'offsite_remote_receipt.txt') -Encoding UTF8
    Write-Host ("receipt: {0}" -f (Join-Path $localDir.FullName 'offsite_remote_receipt.txt'))
}

Write-Host ''
if ($bad -gt 0 -or $miss -gt 0) {
    Write-Host 'RESULT: FAIL - remote copy differs from source; DO NOT treat as a backup.'
    exit 1
}
Write-Host 'RESULT: OK - remote copy is byte-identical to the source.'
exit 0
