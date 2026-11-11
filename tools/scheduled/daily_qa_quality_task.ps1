# daily_qa_quality_task.ps1 - ZeroBound daily QA quality spot check + drift alert
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\zero_daily_qa_quality.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/kb_corpus/qa_quality_daily.py run --n 2 --send 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' qa_quality rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 10) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' qa_quality EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}

