#requires -Version 5.1
<#
.SYNOPSIS
    Verify an offsite backup copy against SHA256SUMS.txt.

.DESCRIPTION
    The agent session cannot upload anywhere: this machine has a single volume
    (C:) and no cloud credentials. So the offsite step stays manual - but the
    verification does not have to be.

    Usage, after you copy the 9 files to the offsite target:

        # on the target (Linux/macOS/git-bash)
        sha256sum -c SHA256SUMS.txt

        # on the target (Windows, this script)
        powershell.exe -NoProfile -ExecutionPolicy Bypass `
            -File verify_offsite.ps1 -Root D:\backup\2026-10-22

.PARAMETER Root
    Directory that holds the copied artifacts plus SHA256SUMS.txt.

.PARAMETER SumsFile
    Default: <Root>\SHA256SUMS.txt

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File verify_offsite.ps1 -Root X:\vault\20261022
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$SumsFile,
    [int]$ExpectedCount = 6
)

$ErrorActionPreference = 'Stop'

if (-not $SumsFile) { $SumsFile = Join-Path $Root 'SHA256SUMS.txt' }

Write-Host '=== offsite verification ==='
Write-Host ("root     : {0}" -f $Root)
Write-Host ("sums     : {0}" -f $SumsFile)

if (-not (Test-Path -LiteralPath $Root)) {
    Write-Host ("ERROR: root not found: {0}" -f $Root)
    exit 2
}
if (-not (Test-Path -LiteralPath $SumsFile)) {
    Write-Host 'ERROR: SHA256SUMS.txt not found (copy it together with the artifacts)'
    exit 2
}

$lines = Get-Content -LiteralPath $SumsFile -Encoding UTF8 |
    Where-Object { $_.Trim() -ne '' }
Write-Host ("entries  : {0} (expected {1})" -f $lines.Count, $ExpectedCount)

$ok = 0
$bad = 0
$missing = 0

foreach ($line in $lines) {
    # standard format: <64 lowercase hex><two spaces><name>
    $m = [regex]::Match($line, '^(?<hash>[0-9a-fA-F]{64})\s{2}(?<name>.+)$')
    if (-not $m.Success) {
        Write-Host ("  [BAD-LINE] {0}" -f $line)
        $bad++
        continue
    }
    $expect = $m.Groups['hash'].Value.ToLowerInvariant()
    $name = $m.Groups['name'].Value.Trim()
    $path = Join-Path $Root $name
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host ("  [MISSING ] {0}" -f $name)
        $missing++
        continue
    }
    $got = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -eq $expect) {
        $size = (Get-Item -LiteralPath $path).Length
        Write-Host ("  [OK      ] {0,-36} {1,14:N0} B" -f $name, $size)
        $ok++
    } else {
        Write-Host ("  [MISMATCH] {0}" -f $name)
        Write-Host ("             expected {0}" -f $expect)
        Write-Host ("             actual   {0}" -f $got)
        $bad++
    }
}

Write-Host ''
Write-Host ("summary  : {0} ok, {1} mismatch, {2} missing, {3} entries" -f `
        $ok, $bad, $missing, $lines.Count)

if ($ok -eq $ExpectedCount -and $bad -eq 0 -and $missing -eq 0) {
    Write-Host 'result   : OK - this offsite copy is byte-identical to the source'
    Write-Host ''
    Write-Host 'NEXT: record it, so the next person knows an offsite copy exists:'
    Write-Host ('      append to _vault\README.md:  {0} -> {1} verified {2}' -f `
            '2026-10-22', $Root, (Get-Date).ToString('yyyy-MM-dd HH:mm'))
    exit 0
}

Write-Host 'result   : FAILED - do not treat this copy as a backup'
exit 1
