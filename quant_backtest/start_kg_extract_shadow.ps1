# K219 kg_extract shadow pipeline (daily 06:50)
# ASCII only - junction ASCII path (same as start_resonance_notify.ps1)
# Dual-file redirect (K213 style): python output separate from status log.
# Shadow discipline: kg_extract never pushes; Ollama down / no new reports
# are exit 0 by design (skip visible in _out.log), alert only on script error.
$log = 'C:\Temp\kg_extract_shadow.log'
$out = 'C:\Temp\kg_extract_shadow_out.log'
$err = 'C:\Temp\kg_extract_shadow_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\kg_extract.py' run 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_extract_shadow $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py kg_extract_shadow 999 $err 1>$null 2>$null
    exit 1
}
