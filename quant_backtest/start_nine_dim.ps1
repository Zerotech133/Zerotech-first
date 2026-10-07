# nine-dim daily scoring launcher (ASCII only, junction path)
# NOTE: PS5.1 Start-Process + Redirect + PassThru must pair with -Wait,
#       otherwise ExitCode is always null (Wait-Process does NOT fix it).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\nine_dim_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\nine_dim.py' `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\nine_dim_out.log' `
        -RedirectStandardError 'C:\Temp\nine_dim_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('nine_dim exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py nine_dim $code C:\Temp\nine_dim_err.log 1>$null 2>$null
    }
    exit $code
} catch {
    ('nine_dim ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
