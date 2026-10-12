# quant backtest daily scan launcher (ASCII only, junction path)
$log = 'C:\Temp\bt_scan_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\quant_backtest\runner.py','--daily' `
        -RedirectStandardOutput 'C:\Temp\bt_scan_out.log' `
        -RedirectStandardError 'C:\Temp\bt_scan_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('bt_scan exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py bt_scan $rc C:\Temp\bt_scan_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('bt_scan ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
