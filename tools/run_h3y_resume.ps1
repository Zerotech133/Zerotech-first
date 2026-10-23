#requires -Version 5.1
<#
.SYNOPSIS
    Resume the three-year staging rebuild until it finishes, retrying on network errors.

.DESCRIPTION
    run_three_year_history.py has no top-level network guard: a single TuShare
    ConnectionError aborts the whole run. It IS resumable (completed receipts are
    reused), so the right wrapper is "run again until exit 0".

    Paths use the ASCII junction C:\aios, never the Chinese physical path:
    PowerShell 5.1 reads a BOM-less UTF-8 script as CP936 and would mangle it.

.PARAMETER MaxAttempts
    How many resume attempts before giving up (default 8).
#>
[CmdletBinding()]
param(
    [int]$MaxAttempts = 8
)

$ErrorActionPreference = 'Continue'

$root = 'C:\aios\quant_backtest'
$py = 'C:\Program Files\Python311\python.exe'
$script = Join-Path $root 'run_three_year_history.py'
$log = 'C:\aios\work\h3y_resume.log'

New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null

for ($i = 1; $i -le $MaxAttempts; $i++) {
    $stamp = Get-Date -Format 's'
    Add-Content -LiteralPath $log -Value ("=== attempt " + $i + " start " + $stamp)
    Push-Location $root
    & $py -B $script *>> $log
    $rc = $LASTEXITCODE
    Pop-Location
    Add-Content -LiteralPath $log -Value ("=== attempt " + $i + " rc=" + $rc + " at " + (Get-Date -Format 's'))
    if ($rc -eq 0) {
        Add-Content -LiteralPath $log -Value 'ALL-DONE'
        exit 0
    }
    if ($i -lt $MaxAttempts) {
        Add-Content -LiteralPath $log -Value 'waiting 60s before resume'
        Start-Sleep -Seconds 60
    }
}
Add-Content -LiteralPath $log -Value 'GAVE-UP'
exit 1
