# Deploy TushareSync scheduled task (registry-stored source + in-memory dot-source).
# Run as Administrator. Re-runnable: overwrites registry parts + re-registers task.
# Trigger: daily UTC 15:30 = Beijing 23:30 (avoids DST drift; project_memory lesson).
# Identity: SYSTEM, Highest. Action: powershell -NoProfile (skill铁律).
$ErrorActionPreference = 'Stop'
$taskName = 'TushareSync'
$regKey   = 'HKLM:\SOFTWARE\TushareSync\Deploy'
$markerRel = 'quant_backtest\tushare_sync.py'
$launcherRel = 'quant_backtest\start_tushare_sync.ps1'

# 1) read launcher source (UTF-8)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $here) { $here = $PWD.Path }
$launcherPath = Join-Path $here 'start_tushare_sync.ps1'
if (-not (Test-Path $launcherPath)) { throw "start_tushare_sync.ps1 not found at $launcherPath" }
$launcher = Get-Content -Raw -Path $launcherPath -Encoding UTF8

# 2) base64 + split at midpoint (avoid registry single-string size limits)
$bytes = [Text.Encoding]::UTF8.GetBytes($launcher)
$b64   = [Convert]::ToBase64String($bytes)
$cut   = [Math]::Floor($b64.Length / 2)
$part1 = $b64.Substring(0, $cut)
$part2 = $b64.Substring($cut)

# 3) store in registry
if (-not (Test-Path $regKey)) { New-Item -Path $regKey -Force | Out-Null }
Set-ItemProperty -Path $regKey -Name 'part1' -Value $part1
Set-ItemProperty -Path $regKey -Name 'part2' -Value $part2
Write-Host ("registry stored: {0} b64 chars" -f $b64.Length)

# 4) build task action: load from registry, write-back for audit, dot-source execute
$inner = @'
try {
  $b = Get-ItemProperty HKLM:\SOFTWARE\TushareSync\Deploy
  $s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b.part1.Trim()) + [Convert]::FromBase64String($b.part2.Trim()))
  $root = (Get-ChildItem E:\ -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'quant_backtest\tushare_sync.py') } | Select-Object -First 1).FullName
  if (-not $root) { throw 'workspace root not found' }
  [IO.File]::WriteAllText((Join-Path $root 'quant_backtest\start_tushare_sync.ps1'), $s)
} catch { ('ERR ' + $_.Exception.Message) | Out-File C:\Temp\tushare_diag.log -Append -Encoding ascii }
. ([ScriptBlock]::Create($s)) *> C:\Temp\tushare_run.log
'@
$argLine = '-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -Command "' + ($inner -replace "`r?`n", ' ') + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine

# 5) trigger: daily UTC 15:30 (Beijing 23:30) — UTC anchor avoids DST drift
#    Use XML to set StartBoundary with Z suffix; RepetitionDuration must be <= 9999 days.
$utcIso = '2026-09-29T15:30:00Z'
$triggerXml = @"
<CalendarTrigger xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <StartBoundary>$utcIso</StartBoundary>
  <Enabled>true</Enabled>
  <ScheduleByDay>
    <DaysInterval>1</DaysInterval>
  </ScheduleByDay>
</CalendarTrigger>
"@

# 6) register via XML (allows UTC StartBoundary + avoids RepetitionDuration limits)
$argEscaped = [System.Security.SecurityElement]::Escape($argLine)
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Tushare Pro daily incremental sync (Beijing 23:30 = UTC 15:30)</Description>
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
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <AllowHardTerminate>true</AllowHardTerminate>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT15M</Interval>
      <Count>2</Count>
    </RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$argEscaped</Arguments>
    </Exec>
  </Actions>
</Task>
"@
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Register-ScheduledTask -TaskName $taskName -Xml $taskXml | Out-Null

# 9) ensure C:\Temp exists for logs
if (-not (Test-Path 'C:\Temp')) { New-Item -Path 'C:\Temp' -ItemType Directory -Force | Out-Null }

# 10) ACL self-heal on workspace (skill铁律: SYSTEM needs access)
$root = (Get-ChildItem E:\ -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName $markerRel) } |
    Select-Object -First 1).FullName
if ($root) {
    $aclScript = Join-Path $root '.trae\skills\system-task-deploy\scripts\check-system-acl.ps1'
    if (Test-Path $aclScript) {
        Write-Host "ACL self-heal: $root"
        & $aclScript -Path $root -Fix
    } else {
        Write-Host "ACL self-heal skipped (check-system-acl.ps1 not found)"
    }
}

Write-Host ""
Write-Host "=== TushareSync deployed ==="
Write-Host "Trigger: daily UTC 15:30 (Beijing 23:30)"
Write-Host "Action : powershell -NoProfile (in-memory dot-source from registry)"
Write-Host "Logs   : C:\Temp\tushare_sync_{task,out,err}.log + tushare_run.log"
Write-Host ""
Write-Host "Verify :"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'   # manual kick"
Write-Host "  Get-Content C:\Temp\tushare_sync_out.log -Tail 30"
