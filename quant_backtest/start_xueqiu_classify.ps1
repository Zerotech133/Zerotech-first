# XueqiuClassify daily wrapper (18:30, approved 2026-10-30)
# ASCII-only by design (PS5.1 path/ANSI pitfalls). Read/write only xq classification DB.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\xueqiu_classify.out'
$err = Join-Path $root 'logs\xueqiu_classify.err'
$log = Join-Path $root 'logs\xueqiu_classify_status.log'
$env:XQ_OLLAMA_LOCK_WAIT_S='600'
# 2026-11-04 v1.8 gate 1 enabled (was off by default since 2026-10-20 draft):
#   probe Ollama latency before each round; >6s means someone else (human chat,
#   k266 batch) holds the single slot -> wait up to 1800s for a gap, else skip
#   this round (next shift retries). Empty probe measured 0.35-0.42s; contended
#   6.6-20s, so the 6s threshold separates cleanly.
$env:XQ_PEAK_SHIFT='1'
New-Item -ItemType Directory -Path (Split-Path $log -Parent) -Force | Out-Null
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'xq_classify.py') --limit 500 --order asc 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') xueqiu_classify $rc $err 1>$null 2>$null
}
exit $rc
