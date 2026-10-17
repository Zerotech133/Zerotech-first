# K227 gold (AU.SHF) 3buy/3sell signal shadow journal (daily 18:30)
# Shadow discipline: record only, no push, no pool/push-口径 changes.
# Dual-file redirect (K213 rule): python output separate from status log.
$log = 'C:\Temp\gold_shadow_task.log'
$out = 'C:\Temp\gold_shadow_out.log'
$err = 'C:\Temp\gold_shadow_err.log'
# 20261016换机：gold_signal_shadow 走 tushare pro，注入 TUSHARE_TOKEN（值取自 .env.local 的 XT_DATA_KEY）
try {
    $envLine = Get-Content 'C:\aios\quant_backtest\.env.local' -Encoding UTF8 | Where-Object { $_ -match '^XT_DATA_KEY=' } | Select-Object -First 1
    if ($envLine) { $env:TUSHARE_TOKEN = $envLine.Substring('XT_DATA_KEY='.Length).Trim() }
} catch {}
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\gold_signal_shadow.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py gold_shadow $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py gold_shadow 999 $err 1>$null 2>$null
    exit 1
}
