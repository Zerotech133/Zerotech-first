# quant position-watch launcher (ASCII only, junction path)
$log = 'C:\Temp\quant_watch_task.log'
try {
    & 'C:\Program Files\Python311\python.exe' C:\aios\quant_backtest\watch_positions.py `
        1> C:\Temp\quant_watch_out.log 2> C:\Temp\quant_watch_err.log
    $rc = $LASTEXITCODE
    ('watch exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py quant_watch $rc C:\Temp\quant_watch_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('watch ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py quant_watch 9 $log 1>$null 2>$null
    exit 1
}
