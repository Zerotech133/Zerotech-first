# snapshot table full refresh launcher (ASCII only)
# Task: ThsMemberRefresh, monthly on day 1 at 21:00
# 背景：ts_ths_member 无业务日期列（_fetched_at 是上游抓取快照的时间），
# 只能整表刷新。远端 total 虚报 15,593,536，真实边界 382,731 行（77 页/约 6 分钟）。
# 口径见 build_catalog._FREQ_FIX_V33 注释：月度快照 + SLA 35d。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = 'rookie.xtcloud-tech.com'
$env:no_proxy = 'rookie.xtcloud-tech.com'
$log = 'C:\Temp\refresh_ths_member_task.log'
try {
    $root = $null
    if (Test-Path 'C:\aios\quant_backtest\refresh_snapshot.py') { $root = 'C:\aios' }
    if (-not $root) { throw 'workspace root not found' }
    $wd = Join-Path $root 'quant_backtest'
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','refresh_snapshot.py','--table','ts_ths_member' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\refresh_ths_member_out.log' `
        -RedirectStandardError 'C:\Temp\refresh_ths_member_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('refresh_ts_ths_member exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path $wd 'alert_fail.py') refresh_ts_ths_member $rc 'C:\Temp\refresh_ths_member_err.log' 1>$null 2>$null
    }
    exit $rc
} catch {
    ('refresh_ts_ths_member ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
