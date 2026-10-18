# XueqiuSeedAuto wrapper (every 30min; 45min self-heal lock inside python wrapper)
# Three-iron-rule style: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
# ASCII-only rule: PS5.1 reads BOM-less UTF-8 scripts as ANSI - never inline CJK paths here.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = 'C:\aios\automation_runs\xq_backfill\seed_task.out'
$err = 'C:\aios\automation_runs\xq_backfill\seed_task.err'
$log = 'C:\aios\automation_runs\xq_backfill\seed_task_status.log'
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'xq_seed_auto.py') 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0 -and $rc -ne 1) {
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') xueqiu_seed_auto $rc $err 1>$null 2>$null
}
exit $rc
