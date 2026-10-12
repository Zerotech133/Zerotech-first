# quant quarterly retune launcher (ASCII only, junction path)
$log = 'C:\Temp\quant_retune_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\quant_backtest\opt_defaults.py','--all','--apply','--push' `
        -RedirectStandardOutput 'C:\Temp\quant_retune_out.log' `
        -RedirectStandardError 'C:\Temp\quant_retune_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('retune exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py quant_retune $rc C:\Temp\quant_retune_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('retune ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
