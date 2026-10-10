# todays-picks daily aggregate launcher (ASCII only, junction path)
# NOTE: PS5.1 Start-Process + Redirect + PassThru must pair with -Wait,
#       otherwise ExitCode is always null (same as start_nine_dim.ps1).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\todays_picks_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\portal\build_todays_picks.py' `
        -WorkingDirectory 'C:\aios\portal' `
        -RedirectStandardOutput 'C:\Temp\todays_picks_out.log' `
        -RedirectStandardError 'C:\Temp\todays_picks_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('todays_picks exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py todays_picks $code C:\Temp\todays_picks_err.log 1>$null 2>$null
    }
    exit $code
} catch {
    ('todays_picks ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
