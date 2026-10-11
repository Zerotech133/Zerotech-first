# K-STOCK-098 #1 alert push wrapper (07:10 morning / 16:05 eod) -
# failure observability. Bare python task had no alert on failure
# (historical rc=1 "no alerts data" mornings were silent).
# ASCII only - junction path. Param: -Mode morning|eod
# Note: alert_push.py itself now pushes an info card on empty data
# and only exits 1 on real push-channel failure; this wrapper makes
# any remaining nonzero rc loud (alert_send.log + Feishu fallback).
param([string]$Mode = 'eod')
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\news_collector\alert_push.py'
$tag = 'alert_push_' + $Mode
$log = 'C:\Temp\alert_push_task.log'
$out = 'C:\Temp\alert_push_out.log'
$err = 'C:\Temp\alert_push_err.log'
try {
    ('starting ' + $Mode + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($Mode -eq 'morning') {
        & $py -X utf8 $script --morning 1> $out 2> $err
    } else {
        & $py -X utf8 $script 1> $out 2> $err
    }
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & $py -X utf8 C:\aios\news_collector\alert_fail_wrap.py $tag $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & $py -X utf8 C:\aios\news_collector\alert_fail_wrap.py $tag 999 $err 1>$null 2>$null
    exit 1
}
