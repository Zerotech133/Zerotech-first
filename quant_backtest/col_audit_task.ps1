# weekly source column audit (K178; SYSTEM-safe path via C:\aios junction)
# audit exit 1 = broken-source suspects -> alert_fail_wrap.py feishu alert.
# all-ASCII per system-task-deploy command-line rule.
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # PS5.1 redirect keeps UTF-8 readable
$log = 'C:\Temp\col_audit_task.log'
$sched = 'C:\Temp\col_audit_sched.log'
& 'C:\Program Files\Python311\python.exe' -X utf8 `
    'C:\aios\quant_backtest\source_col_audit.py' `
    --json 'C:\aios\quant_backtest\reports\col_audit.json' *> $log
$rc = $LASTEXITCODE
if ($null -eq $rc -or '' -eq "$rc") { $rc = 1 }
# K180: regenerate summary html (regardless of audit rc - FAIL needs it too)
& 'C:\Program Files\Python311\python.exe' -X utf8 `
    'C:\aios\quant_backtest\gen_col_audit_summary.py' 1>$null 2>$null
('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $sched -Append -Encoding utf8
if ($rc -ne 0) {
    & 'C:\Program Files\Python311\python.exe' -X utf8 `
        'C:\aios\news_collector\alert_fail_wrap.py' source_col_audit $rc $log `
        1>$null 2>$null
}
exit $rc
