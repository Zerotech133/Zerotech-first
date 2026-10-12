# K33 shadow check weekly launcher (ASCII only, junction path)
# exit code semantic: 0=normal / 1=mixed signal (manual review) / 2=INVALID (recheck)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\k33_shadow_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\k33_shadow_check.py' `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\k33_shadow_out.log' `
        -RedirectStandardError 'C:\Temp\k33_shadow_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('k33_shadow exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py k33_shadow $code C:\Temp\k33_shadow_out.log 1>$null 2>$null
    }
    exit $code
} catch {
    ('k33_shadow ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
