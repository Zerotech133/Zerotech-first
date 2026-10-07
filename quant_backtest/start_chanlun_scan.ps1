# chanlun full-market scan launcher (ASCII only, junction path)
# K72: -ScanArgs passthrough (e.g. '--scan --plot' / '--scan --plot --fill-pool-trend')
param([string]$ScanArgs = '')
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\chanlun_scan_task.log'
# OPS-0930b 错峰：SWA 补跑时多任务同秒启动（07:52 竞态实证），
# 随机延迟 3-8 分钟打散；正常触发仅晚几分钟无副作用
Start-Sleep -Seconds (Get-Random -Minimum 180 -Maximum 480)
try {
    Push-Location 'C:\aios\quant_backtest'
    $pyArgs = @('-X', 'utf8', 'C:\aios\quant_backtest\chanlun_scan.py', '--scan')
    $extra = @($ScanArgs -split '\s+' | Where-Object { $_ -and $_ -ne '--scan' })
    $pyArgs += $extra
    & 'C:\Program Files\Python311\python.exe' @pyArgs `
        1> C:\Temp\chanlun_scan_out.log 2> C:\Temp\chanlun_scan_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('chanlun_scan exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py chanlun_scan $rc C:\Temp\chanlun_scan_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('chanlun_scan ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py chanlun_scan 9 $log 1>$null 2>$null
    exit 1
}
