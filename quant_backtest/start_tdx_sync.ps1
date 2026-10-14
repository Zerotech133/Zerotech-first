# tdx warehouse daily incremental sync launcher v2 (ASCII only, junction path)
# v2: process-level retry (3 attempts, 120s gap) + lock file (no double-run)
#     + append-mode out/err logs with session separator (ref K-STOCK-382b/389)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
$log = 'C:\Temp\tdx_sync_task.log'
$out = 'C:\Temp\tdx_sync_out.log'
$err = 'C:\Temp\tdx_sync_err.log'
$lock = 'C:\Temp\tdx_sync.lock'

# ---- lock: skip if a live launcher holds it; clear stale lock (older than 3h) ----
if (Test-Path $lock) {
    $old = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if ($old) { [void][int]::TryParse((($old -split '\s+') | Select-Object -First 1), [ref]$oldPid) }
    $stale = $false
    if ($oldPid -gt 0 -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        $ts = (Get-Item $lock).LastWriteTime
        if (((Get-Date) - $ts).TotalHours -lt 3) {
            ('tdx_sync SKIP already running pid=' + $oldPid + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
            exit 0
        }
        $stale = $true
    }
    $why = 'dead pid=' + $oldPid
    if ($stale) { $why = 'stale age>3h' }
    ('tdx_sync LOCK-CLEAR ' + $why + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$PID.ToString() | Out-File $lock -Encoding ascii

try {
    Push-Location 'C:\aios\quant_backtest'
    $max = 3
    $rc = 99
    for ($i = 1; $i -le $max; $i++) {
        ('=== tdx_sync session start ' + (Get-Date -Format s) + ' attempt ' + $i + '/' + $max + ' ===') | Out-File $out -Append -Encoding utf8
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\tdx_sync.py --all `
            1>> $out 2>> $err
        $rc = $LASTEXITCODE
        ('tdx_sync attempt=' + $i + ' exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        if ($rc -eq 0) { break }
        if ($i -lt $max) { Start-Sleep -Seconds 120 }
    }
    Pop-Location
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py tdx_sync $rc C:\Temp\tdx_sync_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('tdx_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py tdx_sync 9 $log 1>$null 2>$null
    exit 1
} finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
