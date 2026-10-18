# AgentWatchdog 一键修复（管理员 PowerShell 一次执行）
# 背景：原任务 Arguments 截断为 "-File \" 且 WorkingDirectory 指向不存在路径，
#       每 5min 报 0x8007010B，监控自身已死。根因双杀：
#       ① schtasks /TR 的 %LOCALAPPDATA% 引号嵌套截断；
#       ② %LOCALAPPDATA%\Programs\agent_platform\ 目录不存在。
# ★2026-10-18 现况（非管理员路径已落地）：
#   - 旧任务 ACL=t 只读（提升上下文所建），同名覆盖须管理员；
#   - 已以新名 AgentWatchdog2 注册同 XML（触发器 PT5M/同参数），LastTaskResult=0x0，
#     watch-daemon + ack-serve 双 daemon 复活（幂等拉起）；
#   - start_agent_platform.ps1 已修两刀：①代理变量归一（宿主注入小写 http_proxy
#     会令 PS5.1 Start-Process 环境字典炸，daemon 全灭且 exit 0 掩盖）②拉起失败
#     exit 1（LastTaskResult 可见，防假绿）；
#   - 管理员跑本脚本仍可：同名覆盖旧 AgentWatchdog 消噪音（覆盖后与 2 号并存，
#     幂等互不冲突）。
# 已就绪：start_agent_platform.ps1 已复制到 %LOCALAPPDATA%\Programs\agent_platform\
#         （2026-10-18 00:40 会话）；本目录 agentwatchdog_fixed.xml 已修正
#         Arguments（全路径 -File）与 WorkingDirectory。
# 用法：管理员 PowerShell 里  powershell -ExecutionPolicy Bypass -File fix_agentwatchdog.ps1
# 验收：脚本输出 State + LastTaskResult==0 + watch-daemon/ack-serve 进程可见。
$ErrorActionPreference = 'Continue'
$here = Split-Path $MyInvocation.MyCommand.Path
$xmlPath = Join-Path $here 'agentwatchdog_fixed.xml'
$dst = Join-Path $env:LOCALAPPDATA 'Programs\agent_platform'

# 0) 前置自检：脚本副本在位（缺则补拷）
if (-not (Test-Path (Join-Path $dst 'start_agent_platform.ps1'))) {
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Copy-Item (Join-Path $here 'start_agent_platform.ps1') $dst -Force
}

# 1) 重注册（保触发器 PT5M）
$t = Get-Content $xmlPath -Raw
Register-ScheduledTask -TaskName 'AgentWatchdog' -Xml $t -Force | Out-Null

# 2) 立即触发实测
Start-ScheduledTask -TaskName 'AgentWatchdog'
Start-Sleep -Seconds 6

# 3) 验收：任务状态 + 结果码 + 两 daemon 进程
$v = Get-ScheduledTask -TaskName 'AgentWatchdog'
$info = Get-ScheduledTaskInfo -TaskName 'AgentWatchdog'
"Task State   : $($v.State)"
"LastRunTime  : $($info.LastRunTime)"
"LastTaskResult: 0x{0:X} (0=成功)" -f $info.LastTaskResult
"Arguments    : $($v.Actions[0].Arguments)"
"WorkingDir   : $($v.Actions[0].WorkingDirectory)"
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'agent_platform\.cli (watch-daemon|ack-serve)' }
"daemon 进程   : $($procs.Count) 个（预期 2：watch-daemon + ack-serve）"
$procs | ForEach-Object { "  pid=$($_.ProcessId) $($_.CommandLine.Substring(0,[Math]::Min(90,$_.CommandLine.Length)))" }
if ($procs.Count -lt 2) {
  "!! daemon 未齐——查 %TEMP% 下 start_agent_platform.ps1 输出，或手动跑："
  "   powershell -NoProfile -ExecutionPolicy Bypass -File `"$dst\start_agent_platform.ps1`""
}
