# AIOS-S4U-Guard v2: auto-convert new AIOS tasks to S4U hidden mode (runs as SYSTEM, every 15 min)
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\aios\s4u_guard.log'
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 100KB)) { Remove-Item $log -Force }

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()

# 2026-11-05 白名单：以下任务必须保持 Interactive，不得转 S4U。
# 原因：它们解 jev-gate.env.enc 走「用户级 DPAPI」，lark-cli 还依赖用户 profile(~)；
#       S4U 不加载 profile，转过去后 gate 起不来、公告源降级、机器人发不出消息，
#       等于把 2026-11-05 修好的卡片/告警链路全部推倒。
#       Jev*   = JevStack / JevRestart / JevMonthlyEval / JevCoverageDaily / JevDriftDaily ...
#       DSHBot*= 飞书下钻机器人看护（同一份用户凭据）
$exempt = '^(Jev[A-Za-z0-9]*|DSHBot[A-Za-z0-9]*)$'
$exemptCount = @(Get-ScheduledTask | Where-Object { $_.TaskName -match $exempt }).Count

$tasks = Get-ScheduledTask | Where-Object {
  $_.TaskName -ne 'AIOS-S4U-Guard' -and
  $_.TaskName -notmatch $exempt -and
  $_.Principal.LogonType -ne 'ServiceAccount' -and
  $_.Principal.UserId -notmatch '^(SYSTEM|NT AUTHORITY\\SYSTEM|S-1-5-18)$' -and
  (@($_.Actions) | Where-Object { ($_.Execute -match 'aios|ollama_watchdog') -or ($_.Arguments -match 'aios|ollama_watchdog') })
}

$changed = @()
$failed  = @()
$matched = @($tasks).Count
foreach ($t in $tasks) {
  if ($t.Principal.LogonType -ne 'S4U') {
    try {
      $folder = $svc.GetFolder($t.TaskPath)
      $def = $folder.GetTask($t.TaskName).Definition
      $def.Principal.LogonType = 2   # TASK_LOGON_S4U
      $uid = $def.Principal.UserId
      $null = $folder.RegisterTaskDefinition($t.TaskName, $def, 6, $uid, $null, 2, $null)
      $chk = Get-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName
      if ($chk.Principal.LogonType -eq 'S4U') { $changed += $t.TaskName } else { $failed += $t.TaskName }
    } catch {
      $failed += $t.TaskName
    }
  }
}

# 日志不再笼统写 "noop (all S4U)"：匹配到却没转成功的情况必须看得见
# （2026-11-05 之前这里每 5 分钟写 noop，实际 66 个任务全部转换失败且被 catch 吞掉）。
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($changed.Count -gt 0) {
  "$stamp converted($($changed.Count)): $($changed -join ', ') | failed=$($failed.Count) exempt=$exemptCount" | Out-File $log -Append -Encoding utf8
} else {
  "$stamp noop matched=$matched failed=$($failed.Count) exempt=$exemptCount" | Out-File $log -Append -Encoding utf8
}
