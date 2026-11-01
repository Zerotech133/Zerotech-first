# weekly_eval_task.ps1 - ZeroBound weekly eval regression (v3 frozen baseline)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_weekly_eval.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    $out = & $python -X utf8 tools/kb_corpus/weekly_eval_regression.py 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' weekly_eval rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 10) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' weekly_eval EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
