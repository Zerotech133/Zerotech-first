# Weak track daily launcher (ASCII only, junction path)
# Parametrized for multi-case: -Code 600519.SH -Pivots 1500,1600 -Since 2026-10-05
# Defaults keep case 301216 (existing task WeakTrackCheck runs w/o args, unchanged).
# exit code semantic: 0=normal / 1=review triggered (SOP text auto-sent by
# weak_track_check.py itself via weak_track_alert.py, do NOT double-push) /
# 2=script error (technical alert via alert_fail_wrap.py)
param(
    [string]$Code = '301216',
    [string]$Pivots = '16.12,16.80,17.93',
    [string]$Since = '2026-10-02'
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$c6 = ($Code -split '\.')[0]
$log = "C:\Temp\weak_track_${c6}_task.log"
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\weak_track_check.py',$Code,'--pivots',$Pivots,'--since',$Since `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput "C:\Temp\weak_track_${c6}_out.log" `
        -RedirectStandardError "C:\Temp\weak_track_${c6}_err.log" `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ("weak_track $Code exit=" + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ge 2) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py weak_track_$c6 $code "C:\Temp\weak_track_${c6}_out.log" 1>$null 2>$null
    }
    exit $code
} catch {
    ('weak_track ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
