# K266 event_shadow daily refresh (K-STOCK-131)
# ASCII only - junction ASCII path (same as start_kg_extract_shadow.ps1)
# Dual-file redirect (K213 style): python output separate from status log.
# Shadow discipline: k266_m3a_events.py WITHOUT --dry/--commit rewrites
# event_shadow.jsonl in full (idempotent rebuild from structure_shadow);
# graph writes stay gated behind human-reviewed --commit, never here.
# Neo4j down = script error -> alert_fail (unlike kg_extract's
# no-new-reports exit-0, this pipeline has no benign skip state).
$log = 'C:\Temp\kg_event_shadow.log'
$out = 'C:\Temp\kg_event_shadow_out.log'
$err = 'C:\Temp\kg_event_shadow_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\k266_m3a_events.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py kg_event_shadow $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py kg_event_shadow 999 $err 1>$null 2>$null
    exit 1
}
