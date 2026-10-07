# chanlun US scan launcher (ASCII only, junction path)
# chain: export_us_hk.py --market US (incremental) -> chanlun_scan.py --scan --market US
# data: zhice-db(PG v2_bar_daily) -> data/us/*.parquet -> pool data/chanlun_pool_us.jsonl
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\chanlun_scan_us_task.log'
try {
    Push-Location 'C:\aios\quant_backtest'
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\export_us_hk.py --market US --batch 200 `
        1> C:\Temp\chanlun_export_us_out.log 2> C:\Temp\chanlun_export_us_err.log
    $erc = $LASTEXITCODE
    ('export_us exit=' + $erc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    # export fail does not block: scan has its own fresh gate (local vs db)
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\chanlun_scan.py --scan --market US `
        1> C:\Temp\chanlun_scan_us_out.log 2> C:\Temp\chanlun_scan_us_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('chanlun_scan_us exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py chanlun_scan_us $rc C:\Temp\chanlun_scan_us_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('chanlun_scan_us ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py chanlun_scan_us 9 $log 1>$null 2>$null
    exit 1
}
