# Deploy K245 subscribe push scheduled tasks (intraday 30min + eod 15:30).
# Mirror of deploy_morning_report.ps1 pattern:
#   - XML registration via Register-ScheduledTask -Xml
#   - StartBoundary with Z suffix = UTC anchor (no DST drift)
#   - Principal SYSTEM / RunLevel HighestAvailable
#   - ExecutionTimeLimit + RestartOnFailure + StartWhenAvailable
#   - ASCII-only content by rule (PowerShell scripts must not contain non-ASCII).
# All runtime paths go through C:\aios junction (-> E: workspace) = 100% ASCII.
# Re-runnable: unregisters existing task first. Run as Administrator.
#
# Two tasks:
#   SubPushIntraday : every 30 min, 09:00-15:00 Beijing (01:00-07:00 UTC),
#                     intraday scan -> push only changed subscriptions.
#   SubPushEod      : daily 15:30 Beijing (07:30 UTC), full summary push.
$ErrorActionPreference = 'Stop'

$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\subscribe.py'
$workDir  = 'C:\aios\quant_backtest'
$logDir   = 'C:\aios\quant_backtest\logs'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }
if (-not (Test-Path $logDir))   { New-Item -Path $logDir -ItemType Directory -Force | Out-Null }

$escScript  = [System.Security.SecurityElement]::Escape($pyScript)
$escIntra   = [System.Security.SecurityElement]::Escape(
    'K245 subscription push: intraday scan every 30min (Beijing 09:00-15:00), push only changed, per-sub daily cap 3.')
$escEod     = [System.Security.SecurityElement]::Escape(
    'K245 subscription push: end-of-day full summary at Beijing 15:30, all enabled subscriptions.')

function New-SubTaskXml([string]$desc, [string]$extraArgs, [string]$triggerXml) {
@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>
$triggerXml
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
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Interval>PT10M</Interval>
      <Count>2</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c ""$pyPath" -X utf8 "$pyScript" $extraArgs &gt;&gt; "$logDir\subscribe_task.out" 2&gt;&amp;1"</Arguments>
      <WorkingDirectory>$workDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

# --- Task 1: intraday every 30 min, Beijing 09:00-15:00 = UTC 01:00-07:00 ---
$trigIntra = @"
    <CalendarTrigger>
      <StartBoundary>2026-10-05T01:00:00Z</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>PT6H</Duration>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
"@
$taskIntra = 'SubPushIntraday'
$xmlIntra  = New-SubTaskXml $escIntra '--scan intraday' $trigIntra
if (Get-ScheduledTask -TaskName $taskIntra -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskIntra -Confirm:$false
}
Register-ScheduledTask -TaskName $taskIntra -Xml $xmlIntra | Out-Null

# --- Task 2: eod daily 15:30 Beijing = UTC 07:30 ---
$trigEod = @"
    <CalendarTrigger>
      <StartBoundary>2026-10-05T07:30:00Z</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
"@
$taskEod = 'SubPushEod'
$xmlEod  = New-SubTaskXml $escEod '--scan eod' $trigEod
if (Get-ScheduledTask -TaskName $taskEod -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskEod -Confirm:$false
}
Register-ScheduledTask -TaskName $taskEod -Xml $xmlEod | Out-Null

# Verify.
foreach ($tn in @($taskIntra, $taskEod)) {
    $t = Get-ScheduledTask -TaskName $tn
    $info = Get-ScheduledTaskInfo -TaskName $tn
    Write-Host ('registered : {0} (state={1})' -f $t.TaskName, $t.State)
    Write-Host ('action     : {0} {1}' -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
    Write-Host ('principal  : {0} / {1}' -f $t.Principal.UserId, $t.Principal.RunLevel)
    Write-Host ('trigger    : {0}' -f $t.Triggers[0].StartBoundary)
    Write-Host ('next run   : {0}' -f $info.NextRunTime)
    Write-Host ''
}
Write-Host '=== K245 subscribe push tasks deployed ==='
