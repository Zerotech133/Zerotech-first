# rps resonance daily launcher (ASCII only, junction path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:/Temp/rps_daily_task.log'
try {
    Push-Location 'C:/aios/quant_backtest'
    & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/quant_backtest/rps_resonance.py --daily `
        1> C:/Temp/rps_daily_out.log 2> C:/Temp/rps_daily_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('rps_daily exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/news_collector/alert_fail_wrap.py rps_daily $rc C:/Temp/rps_daily_err.log 1>$null 2>$null
    }
    # E2: sector effect shadow scan - runs only after rps_daily success,
    # independent stdout/err redirect + own status line + own alert
    $scanRc = 0
    if ($rc -eq 0) {
        & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/quant_backtest/sector_effect.py --scan `
            1> C:/Temp/sector_scan_out.log 2> C:/Temp/sector_scan_err.log
        $scanRc = $LASTEXITCODE
        ('sector_scan exit=' + $scanRc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        if ($scanRc -ne 0) {
            & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/news_collector/alert_fail_wrap.py sector_scan $scanRc C:/Temp/sector_scan_err.log 1>$null 2>$null
        }
    }
    # task rc: rps_daily failure dominates; otherwise propagate scan failure
    if ($rc -eq 0) { $rc = $scanRc }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('rps_daily ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:/Program Files\Python311\python.exe' -X utf8 C:/aios/news_collector/alert_fail_wrap.py rps_daily 9 $log 1>$null 2>$null
    exit 1
}
