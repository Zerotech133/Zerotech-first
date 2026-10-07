# Deploy StrongEod scheduled task: strong production line stage-2b push chain.
# Daily 15:45 Beijing (07:45 UTC): strong_scan.py full-block scan ->
# strong_card.py --send push (sector-leader group only, watch list disk-only).
# Mirror of deploy_subscribe.ps1 pattern:
#   - XML registration via Register-ScheduledTask -Xml
#   - StartBoundary with Z suffix = UTC anchor (no DST drift)
#   - Principal SYSTEM / RunLevel HighestAvailable
#   - StartWhenAvailable + RestartOnFailure PT5M x3 + ExecutionTimeLimit
#   - ASCII-only content by rule (PowerShell scripts must not contain non-ASCII).
#   - Runtime paths via C:\aios junction (-> E: workspace) = 100% ASCII.
#   - Action-level chain: "cmd /c python scan && python card --send" so the
#     card push only runs after a successful scan; log redirect appends to
#     logs\strong_task.out. Duplicate-push risk is bounded by
#     strong_state.json daily cap (same stock max 1 push per day).
# Re-runnable: unregisters existing task first. Run as Administrator.
#
# NOTE: this script is DELIVERED, NOT executed by automation (schtasks is
# blocked in the agent sandbox). To register manually, run as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\aios\quant_backtest\deploy_strong.ps1
# then verify:
#   schtasks /Query /TN StrongEod /V /FO LIST
$ErrorActionPreference = 'Stop'

$pyPath   = 'C:\Program Files\Python311\python.exe'
$workDir  = 'C:\aios\quant_backtest'
$logDir   = 'C:\aios\quant_backtest\logs'
$scan     = 'C:\aios\quant_backtest\strong_scan.py'
$card     = 'C:\aios\quant_backtest\strong_card.py'
$taskName = 'StrongEod'

if (-not (Test-Path $pyPath)) { throw "python not found: $pyPath" }
if (-not (Test-Path $card))   { throw "script not found: $card" }
if (-not (Test-Path $logDir)) { New-Item -Path $logDir -ItemType Directory -Force | Out-Null }

$desc = [System.Security.SecurityElement]::Escape(
    'Strong production line eod: full-block scan then push sector-leader list at Beijing 15:45. Watch list (counter-block strength) is disk-only, never pushed. Same stock max 1 push per day (strong_state.json).')

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-08T07:45:00Z</StartBoundary>
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
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c ""$pyPath" -X utf8 "$scan" &gt;&gt; "$logDir\strong_task.out" 2&gt;&amp;1 &amp;&amp; "$pyPath" -X utf8 "$card" --send &gt;&gt; "$logDir\strong_task.out" 2&gt;&amp;1"</Arguments>
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
Write-Host ('principal  : {0} / {1}' -f $t.Principal.UserId, $t.Principal.RunLevel)
Write-Host ('trigger    : {0}' -f $t.Triggers[0].StartBoundary)
Write-Host ('next run   : {0}' -f $info.NextRunTime)
Write-Host '=== StrongEod deployed ==='
