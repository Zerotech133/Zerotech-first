# StrategyDaily wrapper -- multi-strategy daily orchestration 07:30 (P2)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
$ErrorActionPreference = 'Continue'
$out = 'C:\Temp\strategy_daily_out.log'
$err = 'C:\Temp\strategy_daily_err.log'
$log = 'C:\Temp\strategy_daily_task.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\strategy_layer\run_daily.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py strategy_daily $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py strategy_daily 999 $err 1>$null 2>$null
    exit 1
}
