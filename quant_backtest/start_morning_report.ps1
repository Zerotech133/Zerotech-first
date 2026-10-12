# K223 MorningReport wrapper (daily 07:00) - alert coverage backfill
# ASCII only - junction path. Bare python task had no alert on failure
# (10-05 07:00 miss discovered silently during K221 patrol).
# NOTE: wrapper covers "ran but failed"; missed-start detection is a
# separate sentinel concern (pool item, not this file).
$log = 'C:\Temp\morning_report_task.log'
$out = 'C:\Temp\morning_report_out.log'
$err = 'C:\Temp\morning_report_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\morning_report.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py morning_report $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py morning_report 999 $err 1>$null 2>$null
    exit 1
}
