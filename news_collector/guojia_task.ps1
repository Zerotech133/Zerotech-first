# VERSION: guojia_task v1 (2026-10-18, K-ORG-191 daily reconcile wrapper)
$ErrorActionPreference = 'Stop'
# log under news_collector/logs (gitignored, same pattern as quote.log)
$log = Join-Path $PSScriptRoot 'logs\guojia_task.log'
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
    # single pass reconcile (default paths relative to this script dir);
    # exit 0 = ok (per-source degrade tolerated, see summary.pipeline)
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $PSScriptRoot 'guojia_reconcile.py') 2>&1
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
