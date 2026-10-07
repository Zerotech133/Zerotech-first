# news ai review launcher (ASCII only, junction path)
$log = 'C:\Temp\news_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\news_collector\news_ai.py','review' `
        -RedirectStandardOutput 'C:\Temp\news_review_out.log' `
        -RedirectStandardError 'C:\Temp\news_review_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle  # OPS-1004: 句柄缓存，防非交互环境 ExitCode=null（假绿家族）
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }  # null 兜底：宁可误告警不可假绿
    ('review exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py news_review $rc C:\Temp\news_review_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('review ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
