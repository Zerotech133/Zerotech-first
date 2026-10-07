# tushare daily incremental sync launcher (ASCII only, enumerate Chinese path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\tushare_sync_task.log'
try {
    # locate workspace root by marker (Chinese dir name never on cmdline)
    $root = (Get-ChildItem E:\ -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'quant_backtest\tushare_sync.py') } |
        Select-Object -First 1).FullName
    if (-not $root) { throw 'workspace root not found' }
    $wd = Join-Path $root 'quant_backtest'
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','tushare_sync.py','--incremental','--market','all' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\tushare_sync_out.log' `
        -RedirectStandardError 'C:\Temp\tushare_sync_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('tushare_sync exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py tushare_sync $rc C:\Temp\tushare_sync_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('tushare_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
