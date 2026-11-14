# db_dump_daily launcher (ASCII only, junction path)
# 2026-11-05: PREPARED BUT **NOT SCHEDULED**.
# Registering a daily task for this script is an L2 change (K-STOCK-416 / AGENTS §7)
# and needs the five-element approval. Do not enable before the KEEP_DAYS rotation
# policy is decided -- see ops/db_dump_daily.py docstring (14d constant vs the
# 60d wording in the 11-05 manual run log).
# Only after approval: register \DbDumpDaily at 03:30 with this wrapper as action.
# 2026-11-14 FIX: 加全局 Mutex（根治 Parallel 触发抢同一份 out/err 文件 = 持续 rc=267009）
# 每个进程实例独立日志文件 — 彻底消除并发写日志的文件锁冲突
# 注意：Get-Date -Format 必须加括号后再拼接，否则 PS 会把 + 当成 -Date 参数值
$dateStr = Get-Date -Format 'yyyyMMdd_HHmmss'
$stamp = "$dateStr`_PID$PID"
$log = "C:\Temp\db_dump_daily_task_$stamp.log"

# --- 全局 Mutex: 防并发触发抢文件锁 ---
$MutexName = "Local\DbDumpDaily_Singleton_20261114"
$mutex = New-Object System.Threading.Mutex($false, $MutexName)
$hasLock = $mutex.WaitOne(0, $false)
if (-not $hasLock) {
    # 抢不到锁 = 并发冗余触发 — 秒退 exit 0，不写共享日志（避免并发文件锁）
    # 仅当前实例有唯一日志文件名，写自己的日志不影响他人
    ('mutex busy — skip') | Out-File $log -Encoding utf8
    exit 0
}

('wrapper entered ' + (Get-Date -Format s)) | Out-File $log -Encoding utf8
$env:PYTHONUTF8 = '1'

# 每次用唯一文件名，彻底消除文件锁冲突
$outLog = "C:\Temp\db_dump_daily_out_$stamp.log"
$errLog = "C:\Temp\db_dump_daily_err_$stamp.log"

try {
    Push-Location 'C:\aios'
    & 'C:\Program Files\Python311\python.exe' -X utf8 -u C:\aios\ops\db_dump_daily.py `
        1> $outLog 2> $errLog
    $rc = $LASTEXITCODE
    Pop-Location
    ('db_dump_daily exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py db_dump_daily $rc $errLog 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('db_dump_daily ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py db_dump_daily 9 $log 1>$null 2>$null
    exit 1
} finally {
    # 显式释放 Mutex — 所有 exit 路径都经过这里
    try { $mutex.ReleaseMutex() } catch { }
    try { $mutex.Dispose() } catch { }
}




