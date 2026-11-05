# K213 tbe/pbe stats daily refresh (atomic json+html for portal /bt-tbe)
# Slot: daily 06:45 (before MorningReport 07:00). Reads A-share parquet bars only;
# 2026-11-05: ChanlunRefill 06:30 is gone, early batch is \ChanlunScan 07:20
# (same start_chanlun_scan.ps1). No ordering dependency on the scan.
# Full-market run ~2.5 min. ASCII only - junction path.
# NOTE (K213): python stdout/err go to SEPARATE files (fut_daily pattern) -
# never `>>` the status log itself: a concurrent reader of the status log
# during the PS re-encode append killed the task with 1067 once.
$log = 'C:\Temp\tbe_stats_task.log'
$out = 'C:\Temp\tbe_stats_out.log'
$err = 'C:\Temp\tbe_stats_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\chanlun_tbe_stats.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py tbe_stats $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py tbe_stats 999 $err 1>$null 2>$null
    exit 1
}
