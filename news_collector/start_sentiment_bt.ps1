# sentiment backtest launcher (ASCII only, junction path)
$log = 'C:\Temp\sentiment_bt_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\news_collector\sentiment_backtest.py', '--push' `
        -WorkingDirectory 'C:\aios\news_collector' `
        -RedirectStandardOutput 'C:\Temp\sentiment_bt_out.log' `
        -RedirectStandardError 'C:\Temp\sentiment_bt_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: handle cache against ExitCode=null
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('sentiment_bt exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py sentiment_bt $rc C:\Temp\sentiment_bt_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('sentiment_bt ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
