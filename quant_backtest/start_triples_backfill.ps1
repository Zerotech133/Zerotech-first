# K223 TriplesOutcomeBackfill wrapper (daily 15:35) - alert coverage
# backfill. Bare task ran on the Chinese path directly (C:\项目\老田的AI助手);
# wrapper unifies on the C:\aios junction like every other task.
# Idempotent backfill: re-runs are no-ops for already-filled dates.
$log = 'C:\Temp\triples_backfill_task.log'
$out = 'C:\Temp\triples_backfill_out.log'
$err = 'C:\Temp\triples_backfill_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\tools\triples_outcome_backfill.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py triples_backfill $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py triples_backfill 999 $err 1>$null 2>$null
    exit 1
}
