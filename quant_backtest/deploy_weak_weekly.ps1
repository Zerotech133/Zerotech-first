# Deploy WeakWeeklyReport scheduled task (K35-z section 15 weekly aggregate).
# Aggregates section 12 (watch) + section 13 (audit) into one weekly card.
# Same proven XML registration pattern as the monthly deploys:
#   - XML registration (Register-ScheduledTask -Xml)
#   - StartBoundary UTC anchor 07:50Z = Beijing Monday 15:50,
#     15 min after WeakScan (15:35) to avoid task pile-up
#   - Principal SYSTEM / HighestAvailable (systemprofile creds verified)
#   - DisallowStartIfOnBatteries=false; PT10M limit; PT15M x2 retry
#   - ASCII-only; runtime paths via C:\aios junction
# Weekly trigger schema: ScheduleByWeek with FULL day name <Monday />
# (same full-name rule as month elements) + WeeksInterval 1.
# Weekly semantics (not silent): pushes even when all green - proactive
# status report, layered over sections 12/13 silent-on-healthy discipline.
# Empty pipeline (no open, no closed cases) exits rc=0 skipping the push.
# Re-runnable: unregisters existing task first. Run as Administrator.
$ErrorActionPreference = 'Stop'

$taskName = 'WeakWeeklyReport'
$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\weak_track_weekly.py'
$workDir  = 'C:\aios\quant_backtest'
# First fire: 2026-10-05T07:50:00Z = Beijing Monday 2026-10-05 15:50.
$boundary = '2026-10-05T07:50:00Z'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }

$escArg  = [System.Security.SecurityElement]::Escape('-X utf8 ' + $pyScript + ' --send')
$escDesc = [System.Security.SecurityElement]::Escape(
    'K35-z weak section 15 weekly report: Monday 07:50 UTC = Beijing 15:50 ' +
    '(15 min after WeakScan 15:35). Aggregates section 12 watch (open-case ' +
    'health: overdue/stalled/task-missing) and section 13 audit (closed-case ' +
    'integrity: fields/archive/file/task-stop/newcase) into one indigo card. ' +
    'Weekly semantics: pushes even when all green (proactive status report). ' +
    'Empty pipeline exits rc=0 skipping push.')

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
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
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
Write-Host ('trigger    : weekly Monday, boundary {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host ''
Write-Host '=== WeakWeeklyReport deployed ==='
