# AIOS-S4U-Guard v3: auto-convert new AIOS tasks to S4U hidden mode (runs as SYSTEM, every 5 min)
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\aios\s4u_guard.log'
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 100KB)) { Remove-Item $log -Force }

# ── 父进程取证（2026-11-05）─────────────────────────────────────────────
# 日志属主是 NT AUTHORITY\SYSTEM，但 487 个计划任务里没有一个的 action 引用
# 本脚本（全量 XML 无 s4u_guard 字样、无 AIOS-S4U-Guard 任务、WMI 永久订阅也无）。
# 宿主不明 = 谁都能在不知道的情况下把它停掉，或者它早已不受控。
# 每轮把父进程链写进日志，下一轮就能回答"谁在拉起我"。
# 找到宿主后可把 Depth 降为 1 或整段移除，开销很小（一次 CIM 查询 x4）。
function Get-ParentChain {
  param([int]$ProcId, [int]$Depth = 4)
  $parts = @()
  $cur = $ProcId
  for ($i = 0; $i -lt $Depth; $i++) {
    if (-not $cur -or $cur -le 0) { break }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
    if (-not $p) { break }
    $cmd = ''
    if ($p.CommandLine) { $cmd = ($p.CommandLine -replace '\s+', ' ') }
    if ($cmd.Length -gt 90) { $cmd = $cmd.Substring(0, 90) }
    $parts += ('{0}:{1}:{2}' -f $p.ProcessId, $p.Name, $cmd)
    $cur = [int]$p.ParentProcessId
  }
  return ($parts -join ' <- ')
}

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()

# 2026-11-05 白名单：以下任务必须保持 Interactive，不得转 S4U。
# 原因：它们解 jev-gate.env.enc 走「用户级 DPAPI」，lark-cli 还依赖用户 profile(~)；
#       S4U 不加载 profile，转过去后 gate 起不来、公告源降级、机器人发不出消息，
#       等于把 2026-11-05 修好的卡片/告警链路全部推倒。
#       Jev*   = JevStack / JevRestart / JevMonthlyEval / JevCoverageDaily / JevDriftDaily ...
#       DSHBot*= 飞书下钻机器人看护（同一份用户凭据）
# 2026-11-05 补：JevFeed-Verify 名字里带连字符，旧正则 [A-Za-z0-9]* 匹配不到，
# 结果这个纯 JEV 域任务一直躺在"58 个转换失败"名单里。连字符必须进字符类。
$exempt = '^(Jev[A-Za-z0-9-]*|DSHBot[A-Za-z0-9-]*)$'
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

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$parentChain = Get-ParentChain -ProcId $PID -Depth 4
if ($parentChain.Length -gt 180) { $parentChain = $parentChain.Substring(0, 180) }

# ── 失败告警出口（2026-11-05）───────────────────────────────────────────
# 旧版把"匹配到但没转成功"只写进日志：58 个任务每 5 分钟失败一次，日志滚完就没了，
# 没人被告知。这里把状态落盘，再交给 s4u_alert.py 做去重 + 飞书推送。
# 落盘必须成功（本地审计/排障要用），发送失败只影响 rc，不反噬守卫本身。
$stateDir = 'C:\aios\state'
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
$stateFile = Join-Path $stateDir 's4u_guard_state.json'
try {
  $payload = [ordered]@{
    stamp     = $stamp
    matched   = $matched
    exempt    = $exemptCount
    converted = @($changed)
    failed    = @($failed)
    parent    = $parentChain
    host      = $env:COMPUTERNAME
  } | ConvertTo-Json -Depth 4
  [IO.File]::WriteAllText($stateFile, $payload, (New-Object System.Text.UTF8Encoding($false)))
} catch {
  $stateFile = "<write-failed: $($_.Exception.Message)>"
}

$alertRc = -1
$alertPy = 'C:\aios\tools\s4u_alert.py'
$py = 'C:\Program Files\Python311\python.exe'
if ((Test-Path $alertPy) -and (Test-Path $py)) {
  & $py -X utf8 $alertPy 2>&1 | Out-Null
  $alertRc = $LASTEXITCODE
}

# 日志不再笼统写 "noop (all S4U)"：匹配到却没转成功的情况必须看得见
# （2026-11-05 之前这里每 5 分钟写 noop，实际 66 个任务全部转换失败且被 catch 吞掉）。
if ($changed.Count -gt 0) {
  "$stamp converted($($changed.Count)): $($changed -join ', ') | failed=$($failed.Count) exempt=$exemptCount alert_rc=$alertRc | parent=$parentChain" | Out-File $log -Append -Encoding utf8
} else {
  "$stamp noop matched=$matched failed=$($failed.Count) exempt=$exemptCount alert_rc=$alertRc | parent=$parentChain" | Out-File $log -Append -Encoding utf8
}
