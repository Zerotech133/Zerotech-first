# Deploy WeakDailyHealth scheduled task (K35-ae section 16 daily task guard).
# Same proven pattern as deploy_morning_report.ps1 (daily) / deploy_weak_archive.ps1:
#   - XML registration via Register-ScheduledTask -Xml (no -Daily on this PS)
#   - StartBoundary UTC anchor 07:55Z = Beijing 15:55 daily, AFTER the daily
#     pair (WeakTrackCheck 15:30 / WeakScan 15:35) so the guard sweeps the
#     whole chain at end of day (watch the watchmen)
#   - Principal SYSTEM / HighestAvailable (systemprofile .lark-cli creds
#      verified by deploy_weak_archive precedent; OpenAPI direct works)
#   - DisallowStartIfOnBatteries=false, PT10M limit, PT15M x2 restart
#   - ASCII-only; runtime paths via C:\aios junction
# Silent discipline: all green -> rc=0 no push; anomalies -> red card push.
# NOTE: this task itself is NOT in weak_track_health.py CORE on purpose -
# querying its own state mid-run is always "Running" = daily false alarm
# (self-watch trap, see K35-ae comment in weak_track_health.py).
# Re-runnable: unregisters existing task first. Run as Administrator.
$ErrorActionPreference = 'Stop'

$taskName = 'WeakDailyHealth'
$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\weak_track_health.py'
$workDir  = 'C:\aios\quant_backtest'
# First fire: 2026-10-03T07:55:00Z = Beijing 2026-10-03 15:55 (today, UTC anchored).
$boundary = '2026-10-03T07:55:00Z'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }

$escArg  = [System.Security.SecurityElement]::Escape('-X utf8 ' + $pyScript + ' --send')
$escDesc = [System.Security.SecurityElement]::Escape(
    'K35-ae weak section 16 daily task guard: daily 07:55 UTC = Beijing 15:55 ' +
    '(after WeakTrackCheck 15:30 / WeakScan 15:35, end-of-day chain sweep). ' +
    'Checks Weak* task family: state / LastTaskResult / missed schedule. ' +
    'Silent when all green (rc=0, no push); pushes red card on anomalies.')

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$escDesc</Description>
    <URI>\$taskName</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$boundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Interval>PT15M</Interval>
      <Count>2</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$pyPath</Command>
      <Arguments>$escArg</Arguments>
      <WorkingDirectory>$workDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Re-runnable: drop existing registration before registering.
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Register-ScheduledTask -TaskName $taskName -Xml $taskXml | Out-Null

# Verify on the spot.
$t = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host ('registered : {0} (state={1})' -f $t.TaskName, $t.State)
Write-Host ('action     : {0} {1}' -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
Write-Host ('workdir    : {0}' -f $t.Actions[0].WorkingDirectory)
Write-Host ('principal  : {0} / {1}' -f $t.Principal.UserId, $t.Principal.RunLevel)
Write-Host ('trigger    : daily, boundary {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host ''
Write-Host '=== WeakDailyHealth deployed ==='
