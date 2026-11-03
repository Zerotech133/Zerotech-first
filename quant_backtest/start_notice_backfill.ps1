# K260 G6b notice LLM backfill (daily, after evt_build / before KG-Cl1Sync 18:10)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\notice_backfill.out'
$err = Join-Path $root 'logs\notice_backfill.err'
$log = Join-Path $root 'logs\notice_backfill_status.log'
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
$batchLimit = 500
if ($env:NOTICE_BACKFILL_LIMIT) { $batchLimit = [int]$env:NOTICE_BACKFILL_LIMIT }
Add-Content -Path $log -Value ("batch_limit=" + $batchLimit + " " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'kg_notice_llm.py') --backfill --limit $batchLimit 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') notice_backfill $rc $err 1>$null 2>$null
}
exit $rc
