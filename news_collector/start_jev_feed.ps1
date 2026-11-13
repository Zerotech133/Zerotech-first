# jev_feed launcher (ASCII only, junction path)
# P1-2 20261016: datamart news/notice -> news.db xd_ rows + JEV judge, watermark incremental.
# Scheduled daily 21:00 (Task Scheduler trigger; older comment said 23:45 - stale).
# 2026-11-07 RFC-20261107 s14.6 (approved by laotian): enable prefilter for the
# feed entry; matches jev_backfill_task.ps1 / start_jev_night.ps1 / night_batch.py.
# Rollback: change 'safe' to '0' (data-level revert: RFC s7 prefilter SQL).
$env:JEV_PREFILTER = 'safe'
$log = 'C:\Temp\jev_feed_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','-u','C:\aios\news_collector\jev_feed.py' `
        -WorkingDirectory 'C:\aios\news_collector' `
        -RedirectStandardOutput 'C:\Temp\jev_feed_out.log' `
        -RedirectStandardError 'C:\Temp\jev_feed_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('jev_feed exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py jev_feed $rc C:\Temp\jev_feed_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('jev_feed ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
