# VERSION: jev_drift_task v1 (2026-10-17, K-ORG-160 daily drift monitor wrapper)
$ErrorActionPreference = 'Stop'
# log under project review_data (S4U task verified writable, same as jev_backfill)
$log = Join-Path $PSScriptRoot 'review_data\jev_drift_task.log'
if (-not (Test-Path -LiteralPath (Split-Path $log))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
}
function W([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}
try {
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
    W 'P0 start'
    # python resolution (house rule, same as jev_backfill_task.ps1)
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    # single pass drift check (default args); stdout captured for exit code passthrough
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $PSScriptRoot 'jev_drift.py') 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P1 exit=' + $code + ' out=' + ($out -join ' | '))
    # log cap ~400KB: keep tail
    if ((Get-Item -LiteralPath $log).Length -gt 400KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
        W 'P2 log rotated'
    }
    exit $code
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
