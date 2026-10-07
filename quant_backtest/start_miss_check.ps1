# K225 miss detection sentinel (daily 21:30) - "should-have-run" coverage
# ASCII only - junction path. Complements alert_fail (ran-but-failed):
# this checks missed/silent-skip via Last Run vs schedule registry.
# Shadow json: data/miss_check_last.json; MISS>0 -> alert_fail push.
$log = 'C:\Temp\miss_check_task.log'
$out = 'C:\Temp\miss_check_out.log'
$err = 'C:\Temp\miss_check_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\miss_check.py' run 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py miss_check_task $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py miss_check_task 999 $err 1>$null 2>$null
    exit 1
}
