# todays-picks daily aggregate launcher (ASCII only, junction path)
# NOTE: PS5.1 Start-Process + Redirect + PassThru must pair with -Wait,
#       otherwise ExitCode is always null (same as start_nine_dim.ps1).
# 14th review (2026-10-10): chain downstream after aggregate --
#   rc 0 or 3 (3 = T1 suspended but todays_picks.json freshly written)
#   -> build_pick_details.py --tier T1 (2 picks, incremental skip, seconds)
#   -> build_pick_verdicts.py  (local archives + quote lake, backfill windows)
#   rc 1 (crash, todays_picks.json may be STALE) -> skip downstream so
#   details never regenerate from yesterday's list as "today's" facts.
#   Each stage alerts independently via alert_fail_wrap so send results and
#   pending→.sent flow are recorded in news_collector/logs/alert_send.log.
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
        & $py -X utf8 C:\aios\news_collector\alert_fail_wrap.py $name $code ("C:\Temp\{0}_err.log" -f $name) 1>$null 2>$null
    }
    return $code
}
try {
    $rc = Invoke-Stage 'todays_picks' @('C:\aios\portal\build_todays_picks.py')
    if ($rc -eq 0 -or $rc -eq 3) {
        # 2026-11-05 audit fix: pick_details rc used to be dropped ($null), so the
        # task stayed LastResult=0 and acceptance passed. Persist status and
        # propagate nonzero rc instead.
        $drc = Invoke-Stage 'pick_details' @('C:\aios\portal\build_pick_details.py','--tier','T1')
        ('pick_details rc={0} {1}' -f $drc, (Get-Date -Format s)) |
            Out-File 'C:\Temp\pick_details.status' -Append -Encoding utf8
        if ($drc -ne 0) {
            exit $drc
        }
        # 2026-11-03: verdicts full backfill can exceed 1h. Launch detached so the
        # scheduled task itself completes; the verifier keeps writing its own logs.
        $pidFile = 'C:\Temp\pick_verdicts.pid'
        $running = $false
        if (Test-Path $pidFile) {
            try {
                $oldPid = [int]((Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1).Split('|')[0])
                $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
                if ($proc) {
                    # PID reuse guard: trust only if process started around pid file write time.
                    $fileTime = (Get-Item $pidFile).LastWriteTime
                    if ($proc.StartTime -ge $fileTime.AddMinutes(-2)) { $running = $true }
                }
            } catch { $running = $false }
        }
        if (-not $running) {
            $vp = Start-Process -FilePath $py `
                -ArgumentList @('-X','utf8','C:\aios\portal\build_pick_verdicts.py') `
                -WorkingDirectory 'C:\aios\portal' `
                -RedirectStandardOutput 'C:\Temp\pick_verdicts_out.log' `
                -RedirectStandardError 'C:\Temp\pick_verdicts_err.log' `
                -PassThru -WindowStyle Hidden
            ('{0}|{1}' -f $vp.Id, $vp.StartTime.ToString('o')) | Out-File $pidFile -Encoding ascii
            ('pick_verdicts detached pid=' + $vp.Id + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        } else {
            ('pick_verdicts already running pid=' + $oldPid + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        }
    }
    exit $rc
} catch {
    ('todays_picks ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
