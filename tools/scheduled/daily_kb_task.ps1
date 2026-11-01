# daily_kb_task.ps1 - ZeroBound daily KB refresh
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_daily_kb.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/kb_corpus/daily_kb.py run 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' daily_kb rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 10) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' daily_kb EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}