# KgFastRP launcher: Security topology embedding + vector index
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\kg_fastrp_task.log'
try {
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\zerobound-kg\kg_fastrp_sync.py `
        1> C:\Temp\kg_fastrp_out.log 2> C:\Temp\kg_fastrp_err.log
    $rc = $LASTEXITCODE
    ('kg_fastrp exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_fastrp $rc C:\Temp\kg_fastrp_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('kg_fastrp ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_fastrp 9 $log 1>$null 2>$null
    exit 1
}
