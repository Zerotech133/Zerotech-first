# K241 namechange alias sync (weekly Sun 06:20) - alert coverage
# ASCII only - junction path. Low frequency: renames are rare events.
$log = 'C:\Temp\namechange_task.log'
$out = 'C:\Temp\namechange_out.log'
$err = 'C:\Temp\namechange_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\namechange_sync.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py namechange $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py namechange 999 $err 1>$null 2>$null
    exit 1
}
