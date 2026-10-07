# tdx rtmin one-shot pull launcher (3-4h) (ASCII only, junction path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:/Temp/rps_daily_task.log'
try {
    Push-Location 'C:/aios/quant_backtest'
    & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/quant_backtest/tdx_sync.py --include-rtmin `
        1> C:/Temp/tdx_rtmin_out.log 2> C:/Temp/tdx_rtmin_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('tdx_rtmin exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/quant_backtest/alert_fail.py tdx_rtmin $rc C:/Temp/tdx_rtmin_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('tdx_rtmin ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/quant_backtest/alert_fail.py tdx_rtmin 9 $log 1>$null 2>$null
    exit 1
}
