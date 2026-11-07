# fix_watchdog_8099_20261107.ps1 -- K-ORG-20261107-WATCHDOG / 8095 guard install
#
# WHY THIS FILE EXISTS
#   keepalive_9899.py's main() guards four ports (8095/8099/8097/9899), but the
#   registered task only calls guard_km_hub() -> guard_picks_sched/guard_push_admin
#   /guard_cow were never invoked. Consequence: 8095 (picks scheduler) has been
#   unguarded since 2026-10-15, 9899 since then too.
#
# WHY IT MUST RUN ELEVATED
#   The task's security descriptor grants the interactive user only
#   "Read, Synchronize" (verified: Get-Acl C:\Windows\System32\Tasks\<task>).
#   Non-elevated Set-ScheduledTask / Schedule.Service COM / schtasks all return
#   access denied. User t IS a member of Administrators, so a UAC consent is
#   enough -- no separate admin account needed.
#
# DESIGN NOTES
#   * ASCII-only on purpose: PS 5.1 mangles non-ASCII without a BOM, so the task
#     is resolved by name pattern '*8099*' and all paths go through the C:\aios
#     junction (same inode as the repo root). Nothing here depends on codepage.
#   * WorkingDirectory is preserved: rebuilding the Action with
#     New-ScheduledTaskAction drops it otherwise (it was C:\...\portal).
#   * ExecutionTimeLimit goes PT2M -> PT3M: four sequential guards with a 100s
#     global budget plus interpreter start-up fits PT2M only barely; being killed
#     at the limit takes the just-spawned child with it (K-PUSH-004).
#   * The Action calls m.main() instead of listing functions, so future guards
#     added to main() are picked up without touching the task again.
#
# USAGE (elevated PowerShell)
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\fix_watchdog_8099_20261107.ps1 -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\fix_watchdog_8099_20261107.ps1 -Apply
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\fix_watchdog_8099_20261107.ps1 -Verify
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\fix_watchdog_8099_20261107.ps1 -Rollback
# -Verify kills the 8095 listener on purpose and waits up to 7 min for the next
# periodic tick to revive it (the only real proof that the guard is armed).
# It needs elevation too, for the same reason -Apply does.
param(
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Rollback,
    [switch]$SelfTest,
    [switch]$NoCow,
    [switch]$Verify
)
$ErrorActionPreference = 'Stop'

$GUARD = 'C:\aios\ops\keepalive_9899.py'
$PORTAL = 'C:\aios\portal'
$PY = 'C:\Program Files\Python311\python.exe'
$BKROOT = 'C:\aios\backups\watchdog_20261107'

function New-GuardArgs([string]$body) {
    return '-X utf8 -c "import importlib.util; s=importlib.util.spec_from_file_location(''km_watchdog'',r''' + $GUARD + '''); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ' + $body + '"'
}

$ARGS_NEW = New-GuardArgs 'm.main()'
if ($NoCow) {
    # three-port variant: keeps 9899 out of the guard entirely
    $ARGS_NEW = New-GuardArgs 'm.guard_picks_sched(); m.guard_km_hub(); m.guard_push_admin()'
}
$ARGS_OLD = New-GuardArgs 'm.guard_km_hub()'

# ---- 0. self-test: prove the -c string survives CommandLine parsing ---------
# Emulates the task exactly: ProcessStartInfo.Arguments is passed verbatim to
# CreateProcess, same as the scheduled task's Arguments field. Guards against
# the classic "quotes look fine here but python gets a different string".
if ($SelfTest) {
    # NOTE: string literals inside the -c body must be SINGLE-quoted -- the whole
    # body is wrapped in double quotes for the CRT, a double quote would end it.
    $probe = New-GuardArgs "print('ARGS_OK budget=' + str(m.BUDGET_S) + ' portal=' + m.PORTAL_DIR)"
    Write-Host ('probe args = ' + $probe)
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $PY
    $psi.Arguments = $probe
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $p = [Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    Write-Host ('rc=' + $p.ExitCode)
    Write-Host ('stdout: ' + $out.Trim())
    if ($err.Trim()) { Write-Host ('stderr: ' + $err.Trim()) -ForegroundColor Red }
    exit $p.ExitCode
}

# ---- 0a. reverse verification: kill 8095, wait for the PERIODIC tick --------
# Why this exists: "port came up once" only proves the spawn path works once.
# What has to be proven is "if 8095 dies at an arbitrary time, the next 5-minute
# task tick notices and revives it" -- i.e. the guard is actually armed.
#
# Why -Verify also needs elevation: the 8095 child is spawned by an S4U task
# instance (different logon session), so a non-elevated token gets
# ERROR_ACCESS_DENIED on terminate -- measured: OpenProcess lasterr=5,
# Stop-Process "access denied", WMI Terminate rc=2.
#
# Design: kill, prove the port is really DOWN, then WAIT for the natural
# trigger instead of calling Start-ScheduledTask. Calling the task ourselves
# would prove the action string, not the schedule.
if ($Verify) {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host 'BLOCKED: -Verify terminates the 8095 listener, which needs elevation.' -ForegroundColor Red
        Write-Host '  (non-elevated: OpenProcess ERROR_ACCESS_DENIED / WMI Terminate rc=2)' -ForegroundColor Red
        exit 2
    }
    $vTask = @(Get-ScheduledTask | Where-Object { $_.TaskName -like '*8099*' })
    if ($vTask.Count -ne 1) { Write-Host ('BLOCKED: expect 1 task matching *8099*, got ' + $vTask.Count) -ForegroundColor Red; exit 2 }
    $vName = $vTask[0].TaskName
    $vArgs = $vTask[0].Actions[0].Arguments
    Write-Host ('task = ' + $vName)
    Write-Host ('  action guards picks = ' + (($vArgs -like '*m.main()*') -or ($vArgs -like '*guard_picks_sched*')))
    if (-not (($vArgs -like '*m.main()*') -or ($vArgs -like '*guard_picks_sched*'))) {
        Write-Host 'ABORT: the task does not guard 8095 yet -- run -Apply first.' -ForegroundColor Red
        exit 3
    }

    $VLOG = 'C:\Temp\watchdog_9899.log'
    $vBefore = 0
    if (Test-Path -LiteralPath $VLOG) { $vBefore = @(Get-Content -LiteralPath $VLOG).Count }
    Write-Host ('log baseline = ' + $vBefore + ' lines  now=' + (Get-Date).ToString('HH:mm:ss'))

    $vc = Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue
    $vOld = 0
    if ($vc) { $vOld = $vc[0].OwningProcess; Write-Host ('8095 before = LISTEN pid=' + $vOld) }
    else { Write-Host '8095 before = DOWN (verify becomes: was it revived by the next tick)' -ForegroundColor Yellow }

    if ($vOld -ne 0) {
        # identity gate: never kill a port just because it is listening
        $body = (& curl.exe -s -m 5 http://127.0.0.1:8095/) -join ''
        $isPicks = $body -like '*picks*'
        Write-Host ('identity = page mentions picks: ' + $isPicks)
        if (-not $isPicks) {
            Write-Host 'ABORT: 8095 does not look like publish_schedule_server; refusing to kill.' -ForegroundColor Red
            exit 4
        }
        try {
            Stop-Process -Id $vOld -Force -ErrorAction Stop
            Write-Host ('killed pid=' + $vOld) -ForegroundColor Yellow
        } catch {
            Write-Host ('KILL FAILED: ' + $_.Exception.Message) -ForegroundColor Red
            exit 5
        }
        Start-Sleep -Seconds 3
        $vc2 = Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue
        if ($vc2) {
            # a forked child can hold the port; kill the actual listener too
            Write-Host ('port re-held by pid=' + $vc2[0].OwningProcess + ' -> killing it as well') -ForegroundColor Yellow
            try { Stop-Process -Id $vc2[0].OwningProcess -Force -ErrorAction Stop } catch { }
            Start-Sleep -Seconds 3
            $vc2 = Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue
        }
        if ($vc2) {
            Write-Host ('FAIL: 8095 still listening (pid=' + $vc2[0].OwningProcess + ')') -ForegroundColor Red
            exit 6
        }
        Write-Host 'port 8095 = DOWN (kill confirmed)' -ForegroundColor Green
    }

    $vi = Get-ScheduledTaskInfo -TaskName $vName
    Write-Host ('next_run = ' + $vi.NextRunTime + '  (polling up to 7 min: 5 min interval + one 100s budget)')
    $deadline = (Get-Date).AddMinutes(7)
    $vNew = 0
    $elapsed = 0
    $t0 = Get-Date
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        $vn = Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue
        if ($vn) {
            $cand = $vn[0].OwningProcess
            if ($cand -ne $vOld) {
                $vNew = $cand
                $elapsed = [int]((Get-Date) - $t0).TotalSeconds
                break
            }
        }
    }

    Write-Host ''
    if ($vNew -eq 0) {
        Write-Host ('VERIFY FAILED: 8095 did not come back within 7 min (old pid=' + $vOld + ')') -ForegroundColor Red
        Write-Host '  manual fallback:'
        Write-Host ('    & "' + $PY + '" "' + $PORTAL + '\publish_schedule_server.py" --bg')
        Write-Host '  then look at C:\Temp\picks_sched_wd.log and the log delta below'
    } else {
        $code = & curl.exe -s -m 5 -o NUL -w "%{http_code}" http://127.0.0.1:8095/
        Write-Host ('VERIFY OK: revived pid=' + $vNew + ' after ~' + $elapsed + 's, HTTP ' + $code) -ForegroundColor Green
        if ($code -ne '200') { Write-Host '  WARNING: port listens but HTTP is not 200' -ForegroundColor Yellow }
    }
    Write-Host ('task last_run=' + (Get-ScheduledTaskInfo -TaskName $vName).LastRunTime)

    Write-Host '--- watchdog log delta ---'
    if (Test-Path -LiteralPath $VLOG) {
        $all = @(Get-Content -LiteralPath $VLOG)
        $delta = @($all | Select-Object -Skip $vBefore)
        if ($delta.Count -eq 0) {
            Write-Host '  (no new lines -- guard never ran, or it ran and found the port alive)' -ForegroundColor Yellow
        } else {
            $delta | ForEach-Object { Write-Host ('  ' + $_) }
        }
        $joined = $delta -join "`n"
        Write-Host ('  has "picks_sched:8095 DOWN x2 -> spawn" = ' + ($joined -like '*picks_sched:8095 DOWN*'))
        Write-Host ('  has "picks_sched REVIVED"              = ' + ($joined -like '*picks_sched REVIVED*'))
    } else {
        Write-Host '  NO LOG FILE' -ForegroundColor Red
    }

    if ($vNew -eq 0) { exit 1 }
    exit 0
}

# ---- 0b. resolve task by pattern (ASCII-safe) -------------------------------
$matches = @(Get-ScheduledTask | Where-Object { $_.TaskName -like '*8099*' })
if ($matches.Count -ne 1) {
    Write-Host ("BLOCKED: expected exactly 1 task matching *8099*, got " + $matches.Count) -ForegroundColor Red
    $matches | ForEach-Object { Write-Host ("  " + $_.TaskName) }
    exit 2
}
$task = $matches[0]
$name = $task.TaskName
Write-Host ("task = " + $name)

$xmlNow = Export-ScheduledTask -TaskName $name
$curArgs = ([xml]$xmlNow).Task.Actions.Exec.Arguments
$curWd = ([xml]$xmlNow).Task.Actions.Exec.WorkingDirectory
$curLimit = ([xml]$xmlNow).Task.Settings.ExecutionTimeLimit
Write-Host ("  current WorkingDirectory = [" + $curWd + "]")
Write-Host ("  current ExecutionTimeLimit = [" + $curLimit + "]")
Write-Host ("  current calls guard_picks_sched = " + ($curArgs -like '*guard_picks_sched*'))
Write-Host ("  current calls m.main()          = " + ($curArgs -like '*m.main()*'))

# ---- 2. dry run (no elevation needed, nothing written) ---------------------
if ($DryRun -or (-not $Apply -and -not $Rollback)) {
    Write-Host ''
    Write-Host '--- DRY RUN: nothing written, no task change ---' -ForegroundColor Yellow
    Write-Host ('would set Arguments = ' + $(if ($Rollback) { $ARGS_OLD } else { $ARGS_NEW }))
    Write-Host ('would set WorkingDirectory = ' + $PORTAL)
    Write-Host ('would set ExecutionTimeLimit = ' + $(if ($Rollback) { 'PT2M' } else { 'PT3M' }))
    exit 0
}

# ---- 3. elevation gate (only for real writes) ------------------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'BLOCKED: not elevated. Run PowerShell as administrator, then re-run.' -ForegroundColor Red
    Write-Host '  (task SD gives the user only Read/Synchronize; any write needs UAC)' -ForegroundColor Red
    exit 2
}
Write-Host ("elevated as " + $id.Name) -ForegroundColor Green

# ---- 4. backup before touching anything (UTF-16 matches XML declaration) ----
if (-not (Test-Path -LiteralPath $BKROOT)) { New-Item -ItemType Directory -Force $BKROOT | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$bk = Join-Path $BKROOT ('task_8099_' + $stamp + '.xml')
[IO.File]::WriteAllText($bk, $xmlNow, (New-Object Text.UnicodeEncoding($false, $true)))
Write-Host ("  backup -> " + $bk + " (" + (Get-Item $bk).Length + " bytes)")

# ---- 5. apply / rollback ---------------------------------------------------
$targetArgs = $ARGS_NEW
$targetLimit = 'PT3M'
if ($Rollback) { $targetArgs = $ARGS_OLD; $targetLimit = 'PT2M' }

$act = New-ScheduledTaskAction -Execute $PY -Argument $targetArgs -WorkingDirectory $PORTAL
$settings = $task.Settings
$settings.ExecutionTimeLimit = $targetLimit

try {
    Set-ScheduledTask -TaskName $name -Action $act -Settings $settings | Out-Null
    Write-Host 'Set-ScheduledTask: OK (action + settings)' -ForegroundColor Green
} catch {
    Write-Host ('Set-ScheduledTask with -Settings failed: ' + $_.Exception.Message) -ForegroundColor Yellow
    Set-ScheduledTask -TaskName $name -Action $act | Out-Null
    Write-Host 'Set-ScheduledTask: action only (limit unchanged)' -ForegroundColor Yellow
}

# ---- 6. verify by re-export (never trust the call's own success) -----------
$xmlNew = Export-ScheduledTask -TaskName $name
$x = ([xml]$xmlNew).Task
$newArgs = $x.Actions.Exec.Arguments
$newWd = $x.Actions.Exec.WorkingDirectory
$newLimit = $x.Settings.ExecutionTimeLimit
$newSddl = $x.Principal.LogonType

$checks = [ordered]@{
    'args is target'      = ($newArgs -eq $targetArgs)
    'has picks guard'     = ($newArgs -like '*m.main()*') -or ($newArgs -like '*guard_picks_sched*')
    'workingdir kept'     = ($newWd -eq $PORTAL)
    'limit target'        = ($newLimit -eq $targetLimit)
    'principal untouched' = ($newSddl -eq 'S4U')
}
$fail = 0
foreach ($k in $checks.Keys) {
    $ok = $checks[$k]
    if (-not $ok) { $fail++ }
    Write-Host ("  " + $k.PadRight(20) + $(if ($ok) { 'OK' } else { 'FAIL' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}
if ($fail -gt 0) {
    Write-Host 'VERIFY FAILED -- rollback with: -Rollback (see below)' -ForegroundColor Red
    Write-Host ('backup xml: ' + $bk)
    exit 1
}

# ---- 7. trigger once and observe (only when installing) --------------------
if (-not $Rollback) {
    Write-Host ''
    Write-Host 'triggering one run now...' -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $name
    $deadline = (Get-Date).AddSeconds(60)
    $listen = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $listen = Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue
        if ($listen) { break }
    }
    if ($listen) {
        Write-Host ('8095 LISTEN pid=' + $listen[0].OwningProcess + ' (elapsed ok)') -ForegroundColor Green
    } else {
        Write-Host '8095 still DOWN after 60s -- check C:\Temp\picks_sched_wd.log' -ForegroundColor Yellow
    }
    if (Test-Path 'C:\Temp\watchdog_9899.log') {
        Write-Host '--- watchdog log tail ---'
        Get-Content 'C:\Temp\watchdog_9899.log' -Tail 6
    }
    $info = Get-ScheduledTaskInfo -TaskName $name
    Write-Host ('last_run=' + $info.LastRunTime + ' rc=' + $info.LastTaskResult + ' next=' + $info.NextRunTime)
}

Write-Host ''
Write-Host ('backup xml: ' + $bk)
Write-Host ('rollback  : powershell -NoProfile -ExecutionPolicy Bypass -File ' + $PSCommandPath + ' -Rollback') -ForegroundColor Yellow
exit 0
