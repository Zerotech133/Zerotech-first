# Jev 复核门收益回填 + 覆盖率守卫（计划任务每日 20:30 触发）
# A3/P1-1 2026-10-07：root cause = run_backfill SQL %d/%s 混用 TypeError（已修），
# 常驻进程白天重启频繁从未活到 20:05 自动回填窗口 → 本任务独立兜底（幂等，已回填自动跳过）
# 路径经 C:\aios junction（ASCII）规避计划任务命令行中文坑（ChanlunScan 判例）
$ErrorActionPreference = 'Continue'
$here = 'C:\aios\jev-eval'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = Join-Path $here 'review_data\backfill_task.log'

# 环境注入：JEV_API_KEY / LARK_ALERT_OPEN_ID（覆盖率告警推送）/ lark-cli PATH
. (Join-Path $here 'jev-gate.env.ps1')

$py = 'C:\Program Files\Python311\python.exe'
& $py -X utf8 (Join-Path $here 'signal_review_gate.py') --backfill `
    1> (Join-Path $here 'review_data\backfill_out.log') `
    2> (Join-Path $here 'review_data\backfill_err.log')
$rc = $LASTEXITCODE

("backfill exit=" + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
# 日志轮转 ~200KB 保尾
if (Test-Path $log) {
    if ((Get-Item $log).Length -gt 200KB) {
        $tail = Get-Content $log -Tail 100 -Encoding UTF8
        Set-Content $log $tail -Encoding UTF8
    }
}
exit $rc
