# Deploy WeakMonthlySummary scheduled task (K35-u section 10 monthly case summary).
# Mirror of deploy_morning_report.ps1 pattern:
#   - XML registration via Register-ScheduledTask -Xml
#     (New-ScheduledTaskTrigger has no -Monthly on this box; XML CalendarTrigger
#      ScheduleByMonth is the proven route)
#   - StartBoundary with Z suffix = UTC anchor (00:30Z = Beijing 08:30 on the
#     1st of each month, no DST drift)
#   - Principal SYSTEM / RunLevel HighestAvailable
#     (systemprofile\.lark-cli\config.json verified present; OpenAPI direct
#      channel works under SYSTEM - MorningReport precedent, daily rc=0)
#   - ExecutionTimeLimit PT10M + RestartOnFailure (PT15M x2) + StartWhenAvailable
#   - ASCII-only content by rule; all runtime paths via C:\aios junction
# Re-runnable: unregisters existing task first. Run as Administrator.
$ErrorActionPreference = 'Stop'

$taskName = 'WeakMonthlySummary'
$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\weak_track_summary.py'
$workDir  = 'C:\aios\quant_backtest'
# First fire: 2026-10-31T00:30:00Z = Beijing 2026-11-01 08:30 (UTC anchored).
$boundary = '2026-10-31T00:30:00Z'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }

$escArg  = [System.Security.SecurityElement]::Escape('-X utf8 ' + $pyScript + ' --send')
$escDesc = [System.Security.SecurityElement]::Escape(
    'K35-u weak section 10 monthly case summary: 1st of month 00:30 UTC = Beijing 08:30 ' +
    '(UTC anchored). Parses reports/weak_cases weak_close_*.md headers, pushes aggregate ' +
    'stats card to Feishu. No closed cases yet -> graceful empty exit rc=0, no push.')

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
      <ScheduleByMonth>
        <Months>
          <January /><February /><March /><April /><May /><June />
          <July /><August /><September /><October /><November /><December />
        </Months>
        <DaysOfMonth>
          <Day>1</Day>
        </DaysOfMonth>
      </ScheduleByMonth>
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
Write-Host ('trigger    : monthly day 1, boundary {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host ''
Write-Host '=== WeakMonthlySummary deployed ==='
