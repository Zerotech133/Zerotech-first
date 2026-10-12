# Weekly weak-scan launcher (ASCII only, junction path)
# Runs weak_scan.py --send (full-market signal-2 scan + incremental card push).
# exit code semantic: 0=normal (incl. "no new candidates, skipped") /
# 1+=error (technical alert via alert_fail_wrap.py)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\weak_scan_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\weak_scan.py','--send' `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\weak_scan_out.log' `
        -RedirectStandardError 'C:\Temp\weak_scan_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('weak_scan exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ge 1) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py weak_scan $code 'C:\Temp\weak_scan_out.log' 1>$null 2>$null
    }
    exit $code
} catch {
    ('weak_scan ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
