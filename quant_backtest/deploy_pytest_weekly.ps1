# Deploy PytestWeekly scheduled task: core gold-sample tests run weekly.
# Every Saturday 09:00 Beijing (01:00 UTC): pytest on the three core
# signal-layer gold-sample files (chanlun 13 + weak 17 + strong 21 = 51
# cases, pure synthetic, zero DB / zero network, ~5s runtime).
# Mirror of deploy_strong.ps1 pattern:
#   - XML registration via Register-ScheduledTask -Xml
#   - StartBoundary with Z suffix = UTC anchor (no DST drift)
#   - Principal SYSTEM / RunLevel HighestAvailable
#   - StartWhenAvailable + RestartOnFailure PT5M x3 + ExecutionTimeLimit
#   - ASCII-only content by rule; runtime paths via C:\aios junction.
#   - Weekly cadence per C3 (research plan v2): gold samples must be run
#     on a schedule, not just written - unexecuted guards = no guards.
# Re-runnable: unregisters existing task first. Run as Administrator.
#
# NOTE: this script is DELIVERED, NOT executed by automation (schtasks is
# blocked in the agent sandbox). To register manually, run as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\aios\quant_backtest\deploy_pytest_weekly.ps1
# then verify:
#   schtasks /Query /TN PytestWeekly /V /FO LIST
# Failure triage: read tail of logs\pytest_weekly.out (last-run summary
# also visible in portal dev.html health board via ops health check).
$ErrorActionPreference = 'Stop'

$pyPath   = 'C:\Program Files\Python311\python.exe'
$workDir  = 'C:\aios\quant_backtest'
$logDir   = 'C:\aios\quant_backtest\logs'
$taskName = 'PytestWeekly'
$tests    = 'test_chanlun_signal_gold.py test_weak_track_check_trigger.py test_strong_track_gold.py'

if (-not (Test-Path $pyPath)) { throw "python not found: $pyPath" }
if (-not (Test-Path $logDir)) { New-Item -Path $logDir -ItemType Directory -Force | Out-Null }

$desc = [System.Security.SecurityElement]::Escape(
    'Core gold-sample tests weekly (chanlun 13 + weak 17 + strong 21 = 51 cases). C3 guard: signal-layer regressions caught weekly, not just written.')

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-10T01:00:00Z</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Saturday />
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
      <Interval>PT5M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c ""$pyPath" -m pytest $tests -q --tb=short &gt;&gt; "$logDir\pytest_weekly.out" 2&gt;&amp;1"</Arguments>
      <WorkingDirectory>$workDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Register-ScheduledTask -TaskName $taskName -Xml $xml | Out-Null

# Verify.
$t = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host ('registered : {0} (state={1})' -f $t.TaskName, $t.State)
Write-Host ('action     : {0} {1}' -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
Write-Host ('trigger    : {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host '=== PytestWeekly deployed ==='
