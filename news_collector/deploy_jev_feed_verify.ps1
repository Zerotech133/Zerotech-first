# Deploy JevFeed-Verify scheduled task (run elevated once; UAC).
# Mirrors JevFeed-Daily XML: user t S4U / Limited, no battery stop, 3h limit,
# StartWhenAvailable, daily 00:15 (first fire tomorrow 00:15, verifies the
# run of the previous evening). Re-runnable (-Force). Result -> C:\Temp log.
$ErrorActionPreference = 'Stop'
$log = 'C:\Temp\jev_verify_deploy.log'
try {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $at = (Get-Date).Date.AddDays(1).AddMinutes(15).ToString('yyyy-MM-ddTHH:mm:sszzz')
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/2004/02/mit/task">
  <RegistrationInfo>
    <Description>JevFeed-Daily 00:15 result verify + Feishu summary to quant group (success too); retire after 2 stable weeks (2026-11-01)</Description>
    <URI>\JevFeed-Verify</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$sid</UserId>
      <LogonType>S4U</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$at</StartBoundary>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "C:\aios\news_collector\start_jev_feed_verify.ps1"</Arguments>
      <WorkingDirectory>C:\aios\news_collector</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
    Register-ScheduledTask -TaskName 'JevFeed-Verify' -Xml $xml -Force | Out-Null
    $st = (Get-ScheduledTask -TaskName 'JevFeed-Verify').State
    $nx = (Get-ScheduledTaskInfo -TaskName 'JevFeed-Verify').NextRunTime
    ("OK registered boundary=$at sid=$sid state=$st next=$nx") |
        Out-File $log -Encoding utf8
    exit 0
} catch {
    ("DEPLOY_ERR " + $_.Exception.Message) | Out-File $log -Encoding utf8
    exit 1
}
