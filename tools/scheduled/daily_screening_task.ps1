# daily_screening_task.ps1 - ZeroBound daily screening pipeline
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_daily_screening.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/research/daily_screening.py --rs-mode off 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' daily_screening rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 8) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' daily_screening EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}