# pick-archive-monitor launcher (ASCII only, junction path)
# Chains after TodaysPicks 15:10 (aggregate + details + verdicts, seconds~mins).
# rc semantics from pick_archive_monitor.py:
#   0 = healthy (silent), 2 = alerts found (script already pushed via Feishu),
#   1 = monitor itself crashed -> alert_fail so a dead watchdog is never silent.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$py = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\pick_archive_monitor_task.log'
try {
    $p = Start-Process -FilePath $py `
        -ArgumentList @('-X','utf8','C:\aios\quant_backtest\pick_archive_monitor.py','--push') `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\pick_monitor_out.log' `
        -RedirectStandardError 'C:\Temp\pick_monitor_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('monitor exit={0} {1}' -f $code, (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -eq 1) {
        & $py -X utf8 C:\aios\news_collector\alert_fail_wrap.py PickArchiveMonitor $code 'C:\Temp\pick_monitor_err.log' 1>$null 2>$null
    }
    exit 0
} catch {
    ('monitor ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
