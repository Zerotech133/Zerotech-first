# WeightUpdater wrapper -- weekly strategy weight evolution, Sunday 20:00 (P4)
# Shadow-period discipline: runs DRY-RUN only; --apply is manual until full window.
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
$ErrorActionPreference = 'Continue'
$out = 'C:\Temp\weight_updater_out.log'
$err = 'C:\Temp\weight_updater_err.log'
$log = 'C:\Temp\weight_updater_task.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\strategy_layer\weight_updater.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py weight_updater $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py weight_updater 999 $err 1>$null 2>$null
    exit 1
}
