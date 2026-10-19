# AIOS-S4U-Guard v2: auto-convert new AIOS tasks to S4U hidden mode (runs as SYSTEM, every 15 min)
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\aios\s4u_guard.log'
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 100KB)) { Remove-Item $log -Force }

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()

$tasks = Get-ScheduledTask | Where-Object {
  $_.TaskName -ne 'AIOS-S4U-Guard' -and
  $_.Principal.LogonType -ne 'ServiceAccount' -and
  $_.Principal.UserId -notmatch '^(SYSTEM|NT AUTHORITY\\SYSTEM|S-1-5-18)$' -and
  (@($_.Actions) | Where-Object { ($_.Execute -match 'aios|ollama_watchdog') -or ($_.Arguments -match 'aios|ollama_watchdog') })
}

$changed = @()
foreach ($t in $tasks) {
  if ($t.Principal.LogonType -ne 'S4U') {
    try {
      $folder = $svc.GetFolder($t.TaskPath)
      $def = $folder.GetTask($t.TaskName).Definition
      $def.Principal.LogonType = 2   # TASK_LOGON_S4U
      $uid = $def.Principal.UserId
      $null = $folder.RegisterTaskDefinition($t.TaskName, $def, 6, $uid, $null, 2, $null)
      $chk = Get-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName
      if ($chk.Principal.LogonType -eq 'S4U') { $changed += $t.TaskName }
    } catch { }
  }
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($changed.Count -gt 0) {
  "$stamp converted: $($changed -join ', ')" | Out-File $log -Append -Encoding utf8
} else {
  "$stamp noop (all S4U)" | Out-File $log -Append -Encoding utf8
}
