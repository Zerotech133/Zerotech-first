# daily_kb_task.ps1 - ZeroBound daily KB refresh
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$python = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\zero_daily_kb.log'
$body = 'C:\Temp\zero_daily_kb.body.txt'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    # NOTE(2026-11-16): the pipe is REQUIRED; do not "simplify" back to $out = & $python ...
    # On this host $LASTEXITCODE is only back-filled when the native command sits in a
    # pipeline. Bare calls/assignments leave it at its previous value (sentinel test:
    # set 42, run `exit 7`, read back 42). That made `exit $rc` degenerate into a
    # no-argument exit -> code 0, so any Python failure looked like success.
    # Symptom: since 11-15 all four daily logs ended with `rc=` (empty).
    & $python -X utf8 tools/kb_corpus/daily_kb.py run 2>&1 | Out-File $body -Encoding utf8
    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 1 }   # still unknown -> record failure, never assume success
    ($stamp + ' daily_kb rc=' + $rc) | Out-File $log -Append -Encoding utf8
    (Get-Content -LiteralPath $body | Select-Object -Last 10) | Out-File $log -Append -Encoding utf8
    Remove-Item -LiteralPath $body -Force -ErrorAction SilentlyContinue
    exit $rc
} catch {
    ($stamp + ' daily_kb EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
