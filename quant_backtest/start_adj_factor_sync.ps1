# ts_adj_factor daily sync launcher (ASCII only, enumerate Chinese path)
# Runs after TdxSync 17:30 so factors and same-day ts_daily rows land together.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\adj_factor_sync_task.log'
try {
    # locate workspace root by marker (Chinese dir name never on cmdline)
    $root = (Get-ChildItem E:\ -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'quant_backtest\adj_factor_sync.py') } |
        Select-Object -First 1).FullName
    if (-not $root) { throw 'workspace root not found' }
    $wd = Join-Path $root 'quant_backtest'
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','adj_factor_sync.py' `
        -WorkingDirectory $wd `
        -RedirectStandardOutput 'C:\Temp\adj_factor_sync_out.log' `
        -RedirectStandardError 'C:\Temp\adj_factor_sync_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: handle cache, ExitCode=null in non-interactive (fake-green family)
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null guard: prefer false alarm over fake green
    ('adj_factor_sync exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py adj_factor_sync $rc C:\Temp\adj_factor_sync_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('adj_factor_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
