# daily_health_task.ps1 - ZeroBound daily task health monitor + Feishu alert
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\zero_daily_health.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/research/daily_tasks_health.py --send 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' daily_health rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 8) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' daily_health EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}

