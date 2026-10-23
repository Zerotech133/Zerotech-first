#requires -Version 5.1
#requires -Version 5.1
<#
.SYNOPSIS
    One command: copy the newest _vault snapshot offsite, prove it byte-for-byte.
.DESCRIPTION
    Why: the host has exactly one volume (C:). Five git repos, 7.2 GiB of
    curated data and 295 work orders all sit on that one disk; until
    2026-10-23 there was no offsite copy at all.
    Rules:
      * SHA256, never size alone - "same name, same size" was proven to be
        seven DIFFERENT files on this very host.
      * Delete nothing except our own temporary probe file.
      * Pure ASCII: PS 5.1 reads a BOM-less UTF-8 script as CP936.
      * With no -Root, report candidates and exit 0 (never guess a target).
.PARAMETER Root
    Target directory. Omit to only list candidate volumes.
.PARAMETER Verify
    Skip copying; verify an existing <Root>\<date> copy against the source.
.PARAMETER WhatIf
    Report what would happen; copy nothing.
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\offsite_oneclick.ps1 -WhatIf
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$Verify,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

# Vault lives one level ABOVE the repo root. Computed, never hard-coded:
# a literal path would contain non-ASCII characters, and PS 5.1 reads a
# BOM-less UTF-8 script as CP936 -> the Chinese project dir would be mangled
# into mojibake. This is the same trap that broke a scheduled task in Oct 2026.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VaultDir = Join-Path (Split-Path -Parent $RepoRoot) '_vault'
$VerifyScript = Join-Path $PSScriptRoot 'verify_offsite.ps1'
$SystemDrive = $env:SystemDrive.TrimEnd('\')

function Get-NewestSnapshot {
    if (-not (Test-Path -LiteralPath $VaultDir)) { throw "vault not found: $VaultDir" }
    $dirs = Get-ChildItem -LiteralPath $VaultDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
        Sort-Object Name -Descending
    if (-not $dirs) { throw "no dated snapshot under $VaultDir" }
    return $dirs[0]
}

function Get-CandidateVolumes {
    # Excluding by drive letter is enough for a normal USB/network target.
    # A subst/duplicate mount of C: would share letter-independent identity;
    # if that matters, pass -Root explicitly instead of trusting this.
    $out = @()
    foreach ($d in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        if (-not $d.Root) { continue }
        if ($d.Root.TrimEnd('\') -eq $SystemDrive) { continue }
        if (-not (Test-Path -LiteralPath $d.Root)) { continue }
        $out += [pscustomobject]@{
            Name   = $d.Name
            Root   = $d.Root
            FreeGB = if ($d.Free) { [math]::Round($d.Free / 1GB, 1) } else { $null }
        }
    }
    return $out
}

function Test-Writable {
    param([string]$Path)
    $probe = Join-Path $Path ('.offsite_probe_' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Set-Content -LiteralPath $probe -Value 'ok' -Encoding ASCII -ErrorAction Stop
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------- discovery
Write-Host '=== offsite one-click ==='
Write-Host ("vault      : {0}" -f $VaultDir)
Write-Host ("system drv : {0} (never a valid offsite target)" -f $SystemDrive)
Write-Host ''

if (-not $Root) {
    $cands = Get-CandidateVolumes
    Write-Host ("candidate volumes: {0}" -f $cands.Count)
    foreach ($c in $cands) {
        $w = Test-Writable -Path $c.Root
        Write-Host ("  [{0}] {1}  free={2} GB  writable={3}" -f $c.Name, $c.Root, $c.FreeGB, $w)
    }
    if ($cands.Count -eq 0) {
        Write-Host 'No non-system volume is available right now.'
        Write-Host 'Plug in the offsite disk (or pass -Root explicitly) and run again.'
    }
    Write-Host ''
    Write-Host 'nothing was copied.'
    exit 0
}

if (-not (Test-Path -LiteralPath $Root)) {
    Write-Host ("ERROR: target does not exist: {0}" -f $Root)
    exit 2
}
$snap = Get-NewestSnapshot
$dest = Join-Path $Root $snap.Name
Write-Host ("snapshot   : {0}" -f $snap.FullName)
Write-Host ("target     : {0}" -f $dest)
Write-Host ''

$sourceFiles = Get-ChildItem -LiteralPath $snap.FullName -File -Recurse -ErrorAction Stop
if (-not $sourceFiles) { Write-Host 'ERROR: snapshot is empty'; exit 2 }
$totalBytes = ($sourceFiles | Measure-Object -Property Length -Sum).Sum
Write-Host ("source: {0} file(s), {1:N0} bytes" -f $sourceFiles.Count, $totalBytes)

# ---------------------------------------------------------------- copy
if (-not $Verify -and -not $WhatIf) {
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    foreach ($f in $sourceFiles) {
        $rel = $f.FullName.Substring($snap.FullName.Length).TrimStart('\')
        $to = Join-Path $dest $rel
        $toDir = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $toDir)) {
            New-Item -ItemType Directory -Path $toDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $f.FullName -Destination $to -Force
    }
    Write-Host 'copy       : done'
} elseif ($WhatIf) {
    Write-Host 'copy       : skipped (-WhatIf)'
} else {
    Write-Host 'copy       : skipped (-Verify)'
}
Write-Host ''

# ---------------------------------------------------------------- verify
$failed = 0
$lines = @()
foreach ($f in $sourceFiles) {
    $rel = $f.FullName.Substring($snap.FullName.Length).TrimStart('\')
    $to = Join-Path $dest $rel
    $state = 'MISSING'
    if (Test-Path -LiteralPath $to) {
        $t = Get-Item -LiteralPath $to
        if ($t.Length -ne $f.Length) {
            $state = 'SIZE_MISMATCH'
        } elseif (((Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash) -eq ((Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash)) {
            $state = 'OK'
        } else {
            $state = 'SHA256_MISMATCH'
        }
    }
    if ($state -ne 'OK' -and -not $WhatIf) { $failed++ }
    $lines += ("{0}`t{1}`t{2}`t{3}" -f $state, $f.Length, ((Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash), $rel)
}

$okCount = ($lines | Where-Object { $_ -like 'OK*' }).Count
Write-Host ("verify     : {0} ok, {1} problem(s)" -f $okCount, $failed)

# verify_offsite.ps1 needs a SHA256SUMS.txt (64-hex + two spaces + name)
# and defaults to 6 artifacts (MANIFEST.txt excluded). Without it: exit 2.
$sums = Join-Path $dest 'SHA256SUMS.txt'
$artifacts = @($lines | Where-Object { $_ -like 'OK*' -and $_ -notmatch 'MANIFEST\.txt$' })
$sumLines = @($artifacts | ForEach-Object { $p = $_ -split "`t"; "{0}  {1}" -f $p[2].ToLower(), $p[3] })
$verifyRc = 'skipped'
if ((Test-Path -LiteralPath $VerifyScript) -and (Test-Path -LiteralPath $dest) -and -not $WhatIf) {
    Set-Content -LiteralPath $sums -Value $sumLines -Encoding UTF8
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VerifyScript -Root $dest -ExpectedCount $sumLines.Count 1>$null 2>$null
    $verifyRc = $LASTEXITCODE
    Write-Host ("verify_offsite.ps1 exit: {0} ({1} entries)" -f $verifyRc, $sumLines.Count)
    if ($verifyRc -ne 0) { $failed++ }
}

# ---------------------------------------------------------------- receipt
if (-not $WhatIf) {
    $receipt = Join-Path $dest 'offsite_receipt.txt'
    $body = @(
        ("at {0} | {1} -> {2}" -f (Get-Date -Format s), $snap.FullName, $dest)
        ("files {0} | bytes {1} | verified {2} ok / {3} problem | verify_offsite {4}" -f $sourceFiles.Count, $totalBytes, $okCount, $failed, $verifyRc)
        'state`tsize`tsha256`trelpath'
    ) + $lines
    Set-Content -LiteralPath $receipt -Value $body -Encoding UTF8
    Write-Host ("receipt    : {0}" -f $receipt)
}

Write-Host ''
if ($WhatIf) {
    Write-Host 'RESULT: dry run, nothing was copied.'
    exit 0
}
if ($failed -gt 0) {
    Write-Host ("RESULT: FAIL ({0} problem(s))" -f $failed)
    exit 1
}
Write-Host 'RESULT: OK - offsite copy is byte-identical to the source.'
exit 0
