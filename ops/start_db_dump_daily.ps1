# db_dump_daily launcher (ASCII only, junction path)
# 2026-11-05: PREPARED BUT **NOT SCHEDULED**.
# Registering a daily task for this script is an L2 change (K-STOCK-416 / AGENTS §7)
# and needs the five-element approval. Do not enable before the KEEP_DAYS rotation
# policy is decided -- see ops/db_dump_daily.py docstring (14d constant vs the
# 60d wording in the 11-05 manual run log).
# Only after approval: register \DbDumpDaily at 03:30 with this wrapper as action.
$log = 'C:\Temp\db_dump_daily_task.log'
('wrapper entered ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
$env:PYTHONUTF8 = '1'
try {
    Push-Location 'C:\aios'
    # No --keep-days: db_dump_daily.py has no argparse; such an argument is ignored.
    & 'C:\Program Files\Python311\python.exe' -X utf8 -u C:\aios\ops\db_dump_daily.py `
        1> C:\Temp\db_dump_daily_out.log 2> C:\Temp\db_dump_daily_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('db_dump_daily exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py db_dump_daily $rc C:\Temp\db_dump_daily_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('db_dump_daily ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py db_dump_daily 9 $log 1>$null 2>$null
    exit 1
}
