# chanlun HK scan launcher (ASCII only, junction path)
# chain: export_us_hk.py --market HK (incremental) -> chanlun_scan.py --scan --market HK
# data: zhice-db(PG v2_bar_daily) -> data/hk/*.parquet -> pool data/chanlun_pool_hk.jsonl
# note: db HK history ~98 bars/symbol < MIN_BARS 120 -> scan ok=0 until db accumulates
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\chanlun_scan_hk_task.log'
try {
    Push-Location 'C:\aios\quant_backtest'
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\export_us_hk.py --market HK --batch 200 `
        1> C:\Temp\chanlun_export_hk_out.log 2> C:\Temp\chanlun_export_hk_err.log
    $erc = $LASTEXITCODE
    ('export_hk exit=' + $erc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    # export fail does not block: scan has its own fresh gate (local vs db)
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\chanlun_scan.py --scan --market HK `
        1> C:\Temp\chanlun_scan_hk_out.log 2> C:\Temp\chanlun_scan_hk_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('chanlun_scan_hk exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py chanlun_scan_hk $rc C:\Temp\chanlun_scan_hk_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('chanlun_scan_hk ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py chanlun_scan_hk 9 $log 1>$null 2>$null
    exit 1
}
