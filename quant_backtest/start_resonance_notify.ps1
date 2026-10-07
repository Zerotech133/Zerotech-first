# K208 E3 resonance morning digest (daily 08:05)
# ASCII only - junction ASCII path (same as start_fut_daily.ps1 / col_audit_task.ps1)
# Dual-file redirect (K213): python output separate from status log, status log
# carries only instant ASCII appends so concurrent readers can never collide.
$log = 'C:\Temp\kg_resonance_notify.log'
$out = 'C:\Temp\kg_resonance_notify_out.log'
$err = 'C:\Temp\kg_resonance_notify_err.log'
try {
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\kg_resonance_notify.py' 1> $out 2> $err
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py resonance_notify $rc $err 1>$null 2>$null
    }
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py resonance_notify 999 $err 1>$null 2>$null
    exit 1
}
