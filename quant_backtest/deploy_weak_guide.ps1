# Deploy WeakMonthlyGuide scheduled task (K35-ae section 17 monthly guide card).
# Same proven pattern as deploy_weak_archive.ps1 / deploy_weak_summary.ps1:
#   - XML registration via Register-ScheduledTask -Xml
#     (no -Monthly on this PS; month elements FULL names, Months BEFORE
#      DaysOfMonth - schema order sensitive, else 0x80041316)
#   - StartBoundary UTC anchor 00:20Z = Beijing 08:20 on the 1st, OPENER of
#     the monthly chain (guide 08:20 -> summary 08:30 -> lessons 08:40 ->
#     archive 08:50; see the map first, then the facts)
#   - Principal SYSTEM / HighestAvailable (systemprofile .lark-cli creds
#      verified; OpenAPI direct channel works under SYSTEM)
#   - DisallowStartIfOnBatteries=false, PT10M limit, PT15M x2 restart
#   - ASCII-only; runtime paths via C:\aios junction
# Task IS in weak_track_health.py CORE (K35-ae): guard watches it from next
# day's 15:55 sweep (monthly cadence, never overlaps its own runtime).
# Re-runnable: unregisters existing task first. Run as Administrator.
$ErrorActionPreference = 'Stop'

$taskName = 'WeakMonthlyGuide'
$pyPath   = 'C:\Program Files\Python311\python.exe'
$pyScript = 'C:\aios\quant_backtest\weak_track_guide.py'
$workDir  = 'C:\aios\quant_backtest'
# First fire: 2026-10-31T00:20:00Z = Beijing 2026-11-01 08:20 (UTC anchored).
$boundary = '2026-10-31T00:20:00Z'

if (-not (Test-Path $pyPath))   { throw "python not found: $pyPath" }
if (-not (Test-Path $pyScript)) { throw "script not found: $pyScript" }

$escArg  = [System.Security.SecurityElement]::Escape('-X utf8 ' + $pyScript + ' --send')
$escDesc = [System.Security.SecurityElement]::Escape(
    'K35-ae weak section 17 monthly pipeline guide card: 1st of month 00:20 UTC ' +
    '= Beijing 08:20 (monthly chain opener before WeakMonthlySummary 08:30 / ' +
    'WeakMonthlyLearn 08:40 / WeakMonthlyArchive 08:50). Pushes grey map card ' +
    'grouping all 17 sections by cadence (daily / weekly / monthly / on-demand / ' +
    'the only daily human step). Always pushes (self-describing card).')

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
Write-Host '=== WeakMonthlyGuide deployed ==='
