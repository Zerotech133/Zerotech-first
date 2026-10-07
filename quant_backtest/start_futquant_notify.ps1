# K223 FutQuantNotify wrapper (sentinel, next slot 09:35) - alert coverage
# backfill: the sentinel itself is an alerter, but a dead alerter is a
# blind spot - wrapper makes its failure visible via alert_fail.
# Idempotent: same-day re-run exits 0 via state file (K199).
$log = 'C:\Temp\futquant_notify_task.log'
$out = 'C:\Temp\futquant_notify_out.log'
$err = 'C:\Temp\futquant_notify_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\futquant_notify.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py futquant_notify $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py futquant_notify 999 $err 1>$null 2>$null
    exit 1
}
