# Deploy WeakMonthlyReview scheduled task: D3 weak case library monthly
# review automation. Monthly on day 1 at 09:00 Beijing (01:00 UTC):
# weak_monthly_review.py --send aggregates reports\weak_track_*.jsonl for
# the previous month into reports\weak_monthly_<YYYYMM>.md and pushes a
# 20-line summary via the Feishu text channel. Disk report is written even
# if the push fails (summary only logs SEND_FAIL).
# Mirror of deploy_strong.ps1 pattern:
#   - XML registration via Register-ScheduledTask -Xml
#   - StartBoundary with Z suffix = UTC anchor (no DST drift)
#   - Principal SYSTEM / RunLevel HighestAvailable
#   - StartWhenAvailable + RestartOnFailure PT5M x3 + ExecutionTimeLimit
#   - ASCII-only content by rule (PowerShell scripts must not contain
#     non-ASCII).
#   - Runtime paths via C:\aios junction (-> E: workspace) = 100% ASCII.
#   - Action: python weak_monthly_review.py --send, log appended to
#     logs\weak_review.out.
# Re-runnable: unregisters existing task first.
#
# NOTE: this script is DELIVERED, NOT executed by automation (schtasks is
# blocked in the agent sandbox). To register manually, run as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\aios\quant_backtest\deploy_weak_review.ps1
# then verify:
#   schtasks /Query /TN WeakMonthlyReview /V /FO LIST
$ErrorActionPreference = 'Stop'

$pyPath   = 'C:\Program Files\Python311\python.exe'
$workDir  = 'C:\aios\quant_backtest'
$logDir   = 'C:\aios\quant_backtest\logs'
$script   = 'C:\aios\quant_backtest\weak_monthly_review.py'
$taskName = 'WeakMonthlyReview'

if (-not (Test-Path $pyPath)) { throw "python not found: $pyPath" }
if (-not (Test-Path $script)) { throw "script not found: $script" }
if (-not (Test-Path $logDir)) { New-Item -Path $logDir -ItemType Directory -Force | Out-Null }

$desc = [System.Security.SecurityElement]::Escape(
    'Weak case library monthly review: aggregate previous-month weak_track state streams into reports\weak_monthly_<YYYYMM>.md and push a Feishu summary, day 1 of each month at Beijing 09:00.')

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-11-01T01:00:00Z</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth>
          <Day>1</Day>
        </DaysOfMonth>
        <Months>
          <January /><February /><March /><April /><May /><June />
          <July /><August /><September /><October /><November /><December />
        </Months>
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
      <Arguments>/c ""$pyPath" -X utf8 "$script" --send &gt;&gt; "$logDir\weak_review.out" 2&gt;&amp;1"</Arguments>
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
Write-Host '=== WeakMonthlyReview deployed ==='
