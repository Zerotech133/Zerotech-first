# todays-picks daily aggregate launcher (ASCII only, junction path)
# NOTE: PS5.1 Start-Process + Redirect + PassThru must pair with -Wait,
#       otherwise ExitCode is always null (same as start_nine_dim.ps1).
# 14th review (2026-10-10): chain downstream after aggregate --
#   rc 0 or 3 (3 = T1 suspended but todays_picks.json freshly written)
#   -> build_pick_details.py --tier T1 (2 picks, incremental skip, seconds)
#   -> build_pick_verdicts.py  (local archives + quote lake, backfill windows)
#   rc 1 (crash, todays_picks.json may be STALE) -> skip downstream so
#   details never regenerate from yesterday's list as "today's" facts.
#   Each stage alerts independently via alert_fail.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$py = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\todays_picks_task.log'
function Invoke-Stage($name, $args_) {
    $allArgs = @('-X','utf8') + $args_
    $p = Start-Process -FilePath $py `
        -ArgumentList $allArgs `
        -WorkingDirectory 'C:\aios\portal' `
        -RedirectStandardOutput ("C:\Temp\{0}_out.log" -f $name) `
        -RedirectStandardError ("C:\Temp\{0}_err.log" -f $name) `
        -PassThru -WindowStyle Hidden -Wait
    $code = $p.ExitCode
    ('{0} exit={1} {2}' -f $name, $code, (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($code -ne 0) {
        & $py -X utf8 C:\aios\quant_backtest\alert_fail.py $name $code ("C:\Temp\{0}_err.log" -f $name) 1>$null 2>$null
    }
    return $code
}
try {
    $rc = Invoke-Stage 'todays_picks' @('C:\aios\portal\build_todays_picks.py')
    if ($rc -eq 0 -or $rc -eq 3) {
        $null = Invoke-Stage 'pick_details' @('C:\aios\portal\build_pick_details.py','--tier','T1')
        $null = Invoke-Stage 'pick_verdicts' @('C:\aios\portal\build_pick_verdicts.py')
    }
    exit $rc
} catch {
    ('todays_picks ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
