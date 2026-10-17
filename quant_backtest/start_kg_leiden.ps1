# KgLeiden launcher: SIMILAR_K Leiden daily clustering (ASCII only, junction path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\kg_leiden_task.log'
try {
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\zerobound-kg\kg_leiden_sync.py `
        1> C:\Temp\kg_leiden_out.log 2> C:\Temp\kg_leiden_err.log
    $rc = $LASTEXITCODE
    ('kg_leiden exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_leiden $rc C:\Temp\kg_leiden_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('kg_leiden ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_leiden 9 $log 1>$null 2>$null
    exit 1
}
