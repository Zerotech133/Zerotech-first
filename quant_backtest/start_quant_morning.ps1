# quant morning battle-plan launcher (ASCII only, junction path)
# K98: Start-Process -PassThru ExitCode is always EMPTY in Task Scheduler here
# (6 log lines "morning exit= " prove it; null-guard cannot catch empty string;
# exit '' = 0 -> fake green, alert_fail never fires). Use & + $LASTEXITCODE.
$log = 'C:\Temp\quant_morning_task.log'
try {
    & 'C:\Program Files\Python311\python.exe' 'C:\aios\quant_backtest\runner.py' --morning 1> 'C:\Temp\quant_morning_out.log' 2> 'C:\Temp\quant_morning_err.log'
    $rc = $LASTEXITCODE
    if ($null -eq $rc -or '' -eq "$rc") { $rc = 1 }  # guard: alarm better than fake green
    ('morning exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py quant_morning $rc C:\Temp\quant_morning_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('morning ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
