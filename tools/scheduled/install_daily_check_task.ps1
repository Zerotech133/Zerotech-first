# install_daily_check_task.ps1 - 注册「每日 05:30 数据检查 + 部门派单」任务
#
# ⚠ 本脚本默认只打印将要做的事，不注册。加 -Apply 才真正写入计划任务。
#   按项目铁律：改动计划任务属系统级变更，需老田明确批准后执行。
#
# ── 批准五要素 ──────────────────────────────────────────────────────────
# ① 命令全文：
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\scheduled\install_daily_check_task.ps1 -Apply
#    实际注册的动作：
#    powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass
#      -File <repo>\tools\scheduled\daily_check_task.ps1 -Log C:\Temp\daily_check_task.log
#    触发器：周一至周五 05:30，StartWhenAvailable（错过的补跑一次）
# ② 影响面：
#    - 新增一个计划任务名 preopen_check_0530；**不改动**已有 preopen_daily(08:10)
#      及任何采集任务；
#    - 每次运行只读行情分片与交易日历，写 var/preopen/daily_check/ 下台账、
#      C:\Temp\daily_check_task.log、C:\Temp\daily_check_launch.jsonl；
#    - 有问题的项目：按 tools/preopen/daily_check_routes.json 给对应部门投递
#      内部工单（bridge_inbox，AI↔AI 闭环，不进飞书）；仅阻断级判定经
#      notify_hub 发一条红告警到人（4h 窗口去重）。
# ③ 不可逆点：
#    - 部门工单一旦入箱即进入既有消费队列，会占用对应席位的待办；但可通过
#      inbox_gateway.consume 关闭，或按 id 定位后处置，不产生外部影响；
#    - 阻断级飞书红告警外发后不可撤回（与既有健康检查家族同口径）；
#    - 计划任务本身可 Unregister-ScheduledTask 撤销，无残留。
# ④ 替代方案：
#    - 不注册，手工跑 tools\preopen\daily_check_dispatch.py --dry 预演；
#    - 或只把现有 preopen_daily 从 08:10 整体挪到 05:30（那样检查与出名单
#      同时发生，发现问题后没有留给部门的处理窗口，不推荐）；
#    - 或改 -At 到其他时点。
# ⑤ 回滚预案：
#    Unregister-ScheduledTask -TaskName preopen_check_0530 -Confirm:$false
#    任务产物仅日志与台账，保留即可追溯；如需清理按路径逐个删除。
# ────────────────────────────────────────────────────────────────────────
#
# 为什么是 05:30：02:10 TushareTailSync、04:30 TdxTailSync 之后留 1 小时余量；
# 08:10 出名单之前留 2h40m 给责任部门处理与回执。
param(
    [switch]$Apply,
    [string]$At = '05:30'
)

$ErrorActionPreference = 'Stop'
$name = 'preopen_check_0530'
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1 = Join-Path $PSScriptRoot 'daily_check_task.ps1'
$dir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = 'C:\Temp\daily_check_task.log'
$arg = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '" -Log "' + $log + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $At
$act = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output "任务名   : $name"
Write-Output "触发器   : 周一至周五 $At"
Write-Output "执行     : $exe"
Write-Output "参数     : $arg"
Write-Output "工作目录 : $dir"
Write-Output "日志     : $log"

if (-not $Apply) {
    Write-Output ''
    Write-Output '（预演，未注册。确认五要素后加 -Apply）'
    exit 0
}

$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "已存在同名任务，改为更新动作与触发器（不改身份）"
    Set-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set | Out-Null
} else {
    # 注意：-LogonType 属于 New-ScheduledTaskPrincipal，不能传给 Register-ScheduledTask
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg `
        -Settings $set -Principal $prn `
        -Description '每日 05:30 盘前数据检查；发现问题按责任部门发内部工单要求立即处理' | Out-Null
}

# 注册后必须读回核对（不能只信「命令没报错」）
$t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
$gotTrigger = ($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ','
$gotAction = ($t.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' | '
Write-Output "读回校验 Trigger : $gotTrigger"
Write-Output "读回校验 Action  : $gotAction"
Write-Output "读回校验 Principal: $($t.Principal.UserId) / $($t.Principal.LogonType) / $($t.Principal.RunLevel)"
Write-Output "读回校验 State   : $($t.State)"
if ($gotAction -notlike '*daily_check_task.ps1*') { throw '动作读回不符，注册可能未生效' }
if ($gotTrigger -notlike "*$($At)*") { throw ('触发时点读回不符，期望含 ' + $At) }
Write-Output 'REGISTER_OK'
