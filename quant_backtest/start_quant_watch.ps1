# quant position-watch launcher (ASCII only, junction path)
$log = 'C:\Temp\quant_watch_task.log'
try {
    # ★2026-11-09 改 Start-Process -WindowStyle Hidden：原 & python.exe 会闪 cmd 黑窗
    $pyProc = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList @('C:\aios\quant_backtest\watch_positions.py') `
        -WindowStyle Hidden `
        -RedirectStandardOutput 'C:\Temp\quant_watch_out.log' `
        -RedirectStandardError 'C:\Temp\quant_watch_err.log' `
        -PassThru -Wait
    $rc = if ($pyProc) { $pyProc.ExitCode } else { 1 }
    if ($null -eq $rc -or '' -eq "$rc") { $rc = 1 }
    ('watch exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py quant_watch $rc C:\Temp\quant_watch_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('watch ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py quant_watch 9 $log 1>$null 2>$null
    exit 1
}
