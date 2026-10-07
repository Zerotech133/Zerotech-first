# K211 CL-1 daily graph sync: chanlun_pool + seats + volume -> Neo4j
# Slot: daily 18:10 (after KG-TdxSync 17:40, before next-morning 08:05 resonance report)
# Idempotent full sync (kg_build_cl1.py upsert). ASCII only - junction path.
# Dual-file redirect (K213): python output separate from status log.
$log = 'C:\Temp\kg_cl1_sync.log'
$out = 'C:\Temp\kg_cl1_out.log'
$err = 'C:\Temp\kg_cl1_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\kg_build_cl1.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py cl1_sync $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py cl1_sync 999 $err 1>$null 2>$null
    exit 1
}
