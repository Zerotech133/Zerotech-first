# K260 每日回归（计划任务 K260RegressionDaily 每晨 06:40，KgDailySync 06:20 晨链后）
# 五断言见 _k260_regression.py（jsonl 行数/字段自洽/三错类基线/图谱逐类一致/图谱残留）
# exit code 非 0 = 断言失败（计划任务 Last Result 可见 + 日志留痕）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\aios\quant_backtest\_k260_regression_task.log'
& 'C:\Program Files\Python311\python.exe' -X utf8 `
    C:\aios\quant_backtest\_k260_regression.py *>> $log
$rc = $LASTEXITCODE
("k260_regression exit=$rc " + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
if (Test-Path $log) {
    if ((Get-Item $log).Length -gt 200KB) {
        $tail = Get-Content $log -Tail 100 -Encoding UTF8
        Set-Content $log $tail -Encoding UTF8
    }
}
exit $rc
