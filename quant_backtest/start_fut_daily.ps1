# fut daily quant task (K162-E F4): fut_scan (F1+F2 snapshot) + fut_backtest (F3 matrix)
# K182-D refresh-chain expansion: + fut_walkforward (F3 robustness) + cn_fut_backtest (F3b CN matrix)
# cn_fut_backtest runs on Python311 (tushare lives there, NOT in the uv tool env)
# ASCII only - SYSTEM-safe path via C:\aios junction (same as start_portal_api.ps1)
$log = 'C:\Temp\fut_daily_task.log'
$py = "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe"
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if (-not (Test-Path $py)) {
        ('ERR uv tool python missing: ' + $py) | Out-File $log -Append -Encoding utf8
        exit 1
    }
    & $py -X utf8 'C:\aios\tools\fut_scan.py' 1> 'C:\Temp\fut_scan_out.log' 2> 'C:\Temp\fut_scan_err.log'
    $rc1 = $LASTEXITCODE
    & $py -X utf8 'C:\aios\tools\fut_backtest.py' 1> 'C:\Temp\fut_backtest_out.log' 2> 'C:\Temp\fut_backtest_err.log'
    $rc2 = $LASTEXITCODE
    & $py -X utf8 'C:\aios\tools\fut_walkforward.py' 1> 'C:\Temp\fut_walkforward_out.log' 2> 'C:\Temp\fut_walkforward_err.log'
    $rc3 = $LASTEXITCODE
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\tools\cn_fut_backtest.py' 1> 'C:\Temp\cn_fut_out.log' 2> 'C:\Temp\cn_fut_err.log'
    $rc4 = $LASTEXITCODE
    ('fut_scan exit=' + $rc1 + ' fut_backtest exit=' + $rc2 + ' fut_walkforward exit=' + $rc3 + ' cn_fut exit=' + $rc4 + ' ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    if ($rc1 -ne 0 -or $rc2 -ne 0 -or $rc3 -ne 0 -or $rc4 -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py fut_daily ($rc1 * 1000 + $rc2 * 100 + $rc3 * 10 + $rc4) C:\Temp\fut_backtest_err.log 1>$null 2>$null
        exit 1
    }
    exit 0
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
