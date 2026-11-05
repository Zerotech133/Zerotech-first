# K260 每日回归（计划任务 K260RegressionDaily 每晨 06:40，KgDailySync 06:20 晨链后）
# 五断言见 _k260_regression.py（jsonl 行数/字段自洽/三错类基线/图谱逐类一致/图谱残留）
# exit code 非 0 = 断言失败（计划任务 Last Result 可见 + 日志留痕 + alert_fail 推飞书）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\aios\quant_backtest\_k260_regression_task.log'
# 2026-11-05: PS 5.1 的 *>> / >> 一律写 UTF-16LE，而下面 Out-File 写 UTF-8，
# 同一份日志两种编码混写 -> UTF-8 打开全是乱码。改为全部走 Out-File utf8。
# 2>&1 会把 stderr 包成 ErrorRecord，先 ToString() 再落盘，保持纯文本。
$pyout = & 'C:\Program Files\Python311\python.exe' -X utf8 `
    C:\aios\quant_backtest\_k260_regression.py 2>&1
$rc = $LASTEXITCODE
$pyout | ForEach-Object { $_.ToString() } | Out-File $log -Append -Encoding utf8
("k260_regression exit=$rc " + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
if ($rc -ne 0) {
    # 10-09 补：失败推飞书（MissCheck 只兜「没跑」，此分支兜「跑挂」）
    & 'C:\Program Files\Python311\python.exe' -X utf8 `
        C:\aios\news_collector\alert_fail_wrap.py k260_regression $rc $log 1>$null 2>$null
}
if (Test-Path $log) {
    if ((Get-Item $log).Length -gt 200KB) {
        $tail = Get-Content $log -Tail 100 -Encoding UTF8
        Set-Content $log $tail -Encoding UTF8
    }
}
exit $rc
