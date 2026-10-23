# jev_feed_verify launcher (ASCII only, junction path)
# 20261018 OPS: daily 00:15 verify last night 23:45 JevFeed-Daily result
# + Feishu summary to quant group (success too). Retire after 2 stable weeks.
$log = 'C:\Temp\jev_verify_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\news_collector\jev_feed_verify.py' `
        -WorkingDirectory 'C:\aios\news_collector' `
        -RedirectStandardOutput 'C:\Temp\jev_verify_out.log' `
        -RedirectStandardError 'C:\Temp\jev_verify_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('jev_verify exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py jev_feed_verify $rc C:\Temp\jev_verify_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('jev_verify ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
