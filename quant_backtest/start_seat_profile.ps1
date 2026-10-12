# seat profile daily launcher (ASCII only; Chinese path via C:\aios junction)
# K-STOCK-123: ts_top_inst -> portal/data/seat_profiles.json (after tushare_sync 23:30)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\seat_profile_task.log'
try {
    $wd = 'C:\aios\quant_backtest'
    if (-not (Test-Path (Join-Path $wd 'seat_profile_daily.py'))) {
        throw 'seat_profile_daily.py not found under C:\aios\quant_backtest'
    }
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','seat_profile_daily.py' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\seat_profile_out.log' `
        -RedirectStandardError 'C:\Temp\seat_profile_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: cache handle, avoid ExitCode=null fake-green
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('seat_profile exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py seat_profile $rc C:\Temp\seat_profile_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('seat_profile ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
