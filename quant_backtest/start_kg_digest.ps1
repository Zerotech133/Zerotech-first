# K241 kg_digest daily 07:40 (before 08:05 morning report) - alert coverage
# ASCII only - junction path. Shadow discipline: digest json only, notify reads it.
$log = 'C:\Temp\kg_digest_task.log'
$out = 'C:\Temp\kg_digest_out.log'
$err = 'C:\Temp\kg_digest_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path $PSScriptRoot 'kg_digest.py') run 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path (Split-Path $PSScriptRoot -Parent) 'news_collector\alert_fail_wrap.py') kg_digest $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 (Join-Path (Split-Path $PSScriptRoot -Parent) 'news_collector\alert_fail_wrap.py') kg_digest 999 $err 1>$null 2>$null
    exit 1
}
