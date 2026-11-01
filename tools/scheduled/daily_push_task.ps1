# daily_push_task.ps1 - ZeroBound daily Feishu push (ASCII wrapper)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_daily_push.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/kb_corpus/kb_daily_push.py send 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' daily_push rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 6) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' daily_push EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}