# Deploy MorningReport scheduled task (P-B3 pre-market brief).
# Mirror of tushare_sync deploy pattern:
#   - XML registration via Register-ScheduledTask -Xml
#   - StartBoundary with Z suffix = UTC anchor (no DST drift; Beijing 07:00 daily)
#   - Principal SYSTEM / RunLevel HighestAvailable
#   - ExecutionTimeLimit PT1H + RestartOnFailure (PT15M x2) + StartWhenAvailable
#   - [System.Security.SecurityElement]::Escape for XML-embedded strings
# ASCII-only content by rule (PowerShell scripts must not contain non-ASCII).
# All runtime paths go through C:\aios junction (-> E: workspace) = 100% ASCII.
# Re-runnable: unregisters existing task first. Run as Administrator.
$ErrorActionPreference = 'Stop'

$taskName = 'MorningReport'
$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\morning_report.py'
$workDir  = 'C:\aios\quant_backtest'
# First fire: 2026-09-28T23:00:00Z = Beijing 2026-09-29 07:00 (UTC anchored).
$boundary = '2026-09-28T23:00:00Z'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }
if (-not (Test-Path (Join-Path $workDir 'data'))) {
    New-Item -Path (Join-Path $workDir 'data') -ItemType Directory -Force | Out-Null
}

$escArg  = [System.Security.SecurityElement]::Escape($pyScript)
$escDesc = [System.Security.SecurityElement]::Escape(
    'P-B3 pre-market morning report: daily 23:00 UTC = Beijing 07:00 (UTC anchored, no LLM, template aggregation, Feishu push, idempotent daily once).')

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
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
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
Write-Host ('trigger    : {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host ''
Write-Host '=== MorningReport deployed ==='
