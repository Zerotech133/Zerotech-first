# PostHolidayCheck daily launcher (ASCII only, junction path)
# runs until first trading day after holiday, then the python script
# deletes this scheduled task itself; exit!=0 -> alert_fail push
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\post_holiday_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\post_holiday_check.py' `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\post_holiday_out.log' `
        -RedirectStandardError 'C:\Temp\post_holiday_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('post_holiday exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py post_holiday $code C:\Temp\post_holiday_out.log 1>$null 2>$null
    }
    exit $code
} catch {
    ('post_holiday ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
