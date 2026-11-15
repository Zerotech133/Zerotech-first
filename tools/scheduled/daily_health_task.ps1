# daily_health_task.ps1 - ZeroBound daily task health monitor + Feishu alert
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\zero_daily_health.log'
$body = 'C:\Temp\zero_daily_health.body.txt'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    # NOTE(2026-11-16): keep the pipe; see daily_kb_task.ps1 for the sentinel evidence.
    # $LASTEXITCODE is not back-filled for bare/assigned native calls on this host,
    # so `exit $rc` used to become a no-argument exit (code 0) and hid every failure.
    & $python -X utf8 tools/research/daily_tasks_health.py --send 2>&1 | Out-File $body -Encoding utf8
    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 1 }
    ($stamp + ' daily_health rc=' + $rc) | Out-File $log -Append -Encoding utf8
    (Get-Content -LiteralPath $body | Select-Object -Last 8) | Out-File $log -Append -Encoding utf8
    Remove-Item -LiteralPath $body -Force -ErrorAction SilentlyContinue
    exit $rc
} catch {
    ($stamp + ' daily_health EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}

