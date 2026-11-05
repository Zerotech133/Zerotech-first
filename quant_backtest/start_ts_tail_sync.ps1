# tushare tail backfill launcher (ASCII only)
# Task: TushareTailSync, daily 02:10 (TushareSync starts 23:30, measured 1.86h)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = 'api.tushare.pro'
$env:no_proxy = 'api.tushare.pro'
$log = 'C:\Temp\ts_tail_task.log'
try {
    $root = $null
    if (Test-Path 'C:\aios\quant_backtest\ts_tail_sync.py') { $root = 'C:\aios' }
    if (-not $root) { throw 'workspace root not found' }
    $wd = Join-Path $root 'quant_backtest'
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','ts_tail_sync.py' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\ts_tail_out.log' `
        -RedirectStandardError 'C:\Temp\ts_tail_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('ts_tail_sync exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path $wd 'alert_fail.py') ts_tail_sync $rc 'C:\Temp\ts_tail_err.log' 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ts_tail_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
