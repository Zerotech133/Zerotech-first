# crypto_daily daily incremental sync launcher (ASCII only, junction path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\crypto_sync_task.log'
try {
    Push-Location 'C:\aios\quant_backtest'
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\crypto_sync.py `
        1> C:\Temp\crypto_sync_out.log 2> C:\Temp\crypto_sync_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('crypto_sync exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py crypto_sync $rc C:\Temp\crypto_sync_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('crypto_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py crypto_sync 9 $log 1>$null 2>$null
    exit 1
}
