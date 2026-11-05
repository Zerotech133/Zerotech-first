# xtcloud (tdx) tail backfill launcher (ASCII only)
# Task: TdxTailSync, daily 04:30 (four TdxSync batches start 17:35-18:05)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = 'rookie.xtcloud-tech.com'
$env:no_proxy = 'rookie.xtcloud-tech.com'
$log = 'C:\Temp\tdx_tail_task.log'
try {
    $root = $null
    if (Test-Path 'C:\aios\quant_backtest\tdx_tail_sync.py') { $root = 'C:\aios' }
    if (-not $root) { throw 'workspace root not found' }
    $wd = Join-Path $root 'quant_backtest'
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','tdx_tail_sync.py' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\tdx_tail_out.log' `
        -RedirectStandardError 'C:\Temp\tdx_tail_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('tdx_tail_sync exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path $wd 'alert_fail.py') tdx_tail_sync $rc 'C:\Temp\tdx_tail_err.log' 1>$null 2>$null
    }
    exit $rc
} catch {
    ('tdx_tail_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
