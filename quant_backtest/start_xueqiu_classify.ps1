# XueqiuClassify daily wrapper (18:30, approved 2026-10-30)
# ASCII-only by design (PS5.1 path/ANSI pitfalls). Read/write only xq classification DB.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\xueqiu_classify.out'
$err = Join-Path $root 'logs\xueqiu_classify.err'
$log = Join-Path $root 'logs\xueqiu_classify_status.log'
New-Item -ItemType Directory -Path (Split-Path $log -Parent) -Force | Out-Null
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'xq_classify.py') --limit 500 --order asc 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') xueqiu_classify $rc $err 1>$null 2>$null
}
exit $rc
