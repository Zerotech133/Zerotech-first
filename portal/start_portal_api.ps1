# portal api launcher (ASCII only - SYSTEM-safe path via C:\aios junction)
# K98: Start-Process -PassThru ExitCode always EMPTY here (same fake-green
# family as morning; "python exited code= " x5 today, alert argv collapsed:
# log path shifted into code slot). Use & + $LASTEXITCODE instead.
$log = 'C:\Temp\portal_api_task.log'
# K234 idempotent lock: if 8091 already serves /health, SKIP entirely.
# Root cause of the 18:0x kill loop (6x code=-1 in 18min): every trigger of
# this script (DockerWatchdog v2.1 health guard / E107 grid fallback / manual)
# unconditionally Stop-Process'd ALL portal_api pythons first - killing the
# LIVE serving instance as an "orphan". Concurrent triggers = bounce war =
# user-facing 502 windows. With the lock, every concurrent trigger is a no-op.
# K234b (10-08): probe up to 3x (3s apart) before declaring death. The
# 6:05-6:12 outage chain started with ONE transient probe failure -> healthy
# instance killed -> new one aborted mid-start (0x800700E0) -> 502 for 7 min.
# Never kill on a single failed probe; only a truly dead service enters the
# kill branch below (and there killing orphans is a safe no-op).
$h = 0
for ($i = 0; $i -lt 3; $i++) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8091/health' `
            -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $h = 1; break }
    } catch { Start-Sleep -Seconds 3 }
}
if ($h -eq 1) {
    ('skip: 8091 already serving ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    exit 0
}
try {
    # task Stop cannot kill the child; clear orphans first
    # K186: WQL server-side filter (was full Win32Process enum - K132 hang root)
    Get-CimInstance -Query "SELECT ProcessId FROM Win32_Process WHERE Name = 'python.exe' AND CommandLine LIKE '%portal_api%'" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 1
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' 'C:\aios\portal\portal_api.py' 1> 'C:\Temp\portal_api_out.log' 2> 'C:\Temp\portal_api_err.log'
    $rc = $LASTEXITCODE
    if ($null -eq $rc -or '' -eq "$rc") { $rc = 1 }  # guard: alarm better than fake green
    ('python exited code=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py portal_api $rc C:\Temp\portal_api_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
