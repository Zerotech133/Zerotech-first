# install_daily_check_task.ps1 - 注册「05:30 检查派单」与「07:00 回执核验+云端抢险」
#
# ⚠ 本脚本默认只打印将要做的事，不注册。加 -Apply 才真正写入计划任务。
#   按项目铁律：改动计划任务属系统级变更，需老田明确批准后执行。
#
# ── 批准五要素 ──────────────────────────────────────────────────────────
# ① 命令全文：
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\scheduled\install_daily_check_task.ps1 -Apply
#    实际注册两个动作（同一份 daily_check_task.ps1，用 -Phase 区分）：
#    powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass
#      -File <repo>\tools\scheduled\daily_check_task.ps1 -Phase check  -Log C:\Temp\daily_check_task.log
#    powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass
#      -File <repo>\tools\scheduled\daily_check_task.ps1 -Phase rescue -Log C:\Temp\daily_check_task.log
#    触发器：周一至周五 05:30 / 07:00，均 StartWhenAvailable（错过的补跑一次）
# ② 影响面：
#    - 新增 preopen_check_0530、preopen_rescue_0700；**不改动**已有
#      preopen_daily(08:10) 与任何采集任务；
#    - 05:30 只读行情分片与交易日历，写 var/preopen/daily_check/ 台账、
#      C:\Temp\daily_check_task.log、C:\Temp\daily_check_launch.jsonl；
#      按 daily_check_routes.json 给责任部门投 bridge_inbox 内部工单（不进飞书）；
#      仅阻断级判定经 notify_hub 发一条红告警到人（4h 去重）。
#    - 07:00 只读同一批台账 + bridge_inbox 回执状态；**对未回执的部门调用
#      云端大模型（DeepSeek）生成抢险方案**——这一步会把该部门的检查结论、
#      数据源状态与字段值（不含持仓、账户、策略参数）发到外部 API，
#      每次每部门一次调用（约 820 提示 + 900 完成 token，忽略不计的量级），
#      需要外网可达；外网不通时按「抢险不可用」fail-visible，不发假方案。
#      方案只落盘并发信，**不执行任何命令**（改库/删数据/改任务/改配置一律
#      写进 needs_human）。
# ③ 不可逆点：
#    - 部门工单入箱即进既有消费队列，占用对应席位的待办；可按 id 定位处置，
#      或经 inbox_gateway.consume 关闭，不产生外部影响；
#    - 阻断级飞书红告警、07:00 加急信外发后不可撤回（与既有健康检查同口径）；
#    - 云端调用消耗的 token 不可退，但量大可忽略；
#    - 计划任务本身可 Unregister-ScheduledTask 撤销，无残留。
# ④ 替代方案：
#    - 不注册，手工跑 daily_check_dispatch.py --dry / daily_check_rescue.py --dry 预演；
#    - 只注册 05:30（-What check），抢险留给人，不产生任何外部 API 调用；
#    - 注册抢险但关云端：计划任务动作里给 rescue 加 --no-llm
#      （只核验回执并发提醒，不出方案）；
#    - 或改 -AtCheck / -AtRescue 到其他时点。
# ⑤ 回滚预案：
#    Unregister-ScheduledTask -TaskName preopen_check_0530  -Confirm:$false
#    Unregister-ScheduledTask -TaskName preopen_rescue_0700 -Confirm:$false
#    任务产物仅日志与台账，保留即可追溯。
# ────────────────────────────────────────────────────────────────────────
#
# 为什么是 05:30 / 07:00：02:10 TushareTailSync、04:30 TdxTailSync 之后留 1 小时
# 余量；07:00 给部门 1.5h 回执窗口，同时给 08:10 出名单留 70 分钟抢险。
param(
    [switch]$Apply,
    [ValidateSet('check', 'rescue', 'both')]
    [string]$What = 'both',
    [string]$AtCheck = '05:30',
    [string]$AtRescue = '07:00'
)

$ErrorActionPreference = 'Stop'
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1 = Join-Path $PSScriptRoot 'daily_check_task.ps1'
$dir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = 'C:\Temp\daily_check_task.log'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

function Register-One {
    param([string]$Name, [string]$At, [string]$Phase, [string]$Desc)
    $arg = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 +
           '" -Phase ' + $Phase + ' -Log "' + $log + '"'
    $trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $At
    $act = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Write-Output "任务名   : $Name  (phase=$Phase @ $At)"
    Write-Output "参数     : $arg"

    if (-not $Apply) { return }

    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Output "已存在同名任务，改为更新动作与触发器（不改身份）"
        Set-ScheduledTask -TaskName $Name -Action $act -Trigger $trg -Settings $set | Out-Null
    } else {
        # 注意：-LogonType 属于 New-ScheduledTaskPrincipal，不能传给 Register-ScheduledTask
        Register-ScheduledTask -TaskName $Name -Action $act -Trigger $trg `
            -Settings $set -Principal $prn -Description $Desc | Out-Null
    }

    # 注册后必须读回核对（不能只信「命令没报错」）
    $t = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    $gotTrigger = ($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ','
    $gotAction = ($t.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' | '
    Write-Output "读回 Trigger : $gotTrigger"
    Write-Output "读回 Action  : $gotAction"
    Write-Output "读回 State   : $($t.State)"
    if ($gotAction -notlike '*daily_check_task.ps1*') { throw "$Name 动作读回不符，注册可能未生效" }
    if ($gotAction -notlike "*-Phase $Phase*") { throw "$Name 的 phase 参数读回不符" }
    if ($gotTrigger -notlike "*$($At)*") { throw "$Name 触发时点读回不符，期望含 $At" }
    Write-Output "REGISTER_OK $Name"
}

Write-Output "执行     : $exe"
Write-Output "工作目录 : $dir"
Write-Output "日志     : $log"
Write-Output ''

$wantCheck = $What -in @('check', 'both')
$wantRescue = $What -in @('rescue', 'both')

if ($wantCheck) {
    Register-One -Name 'preopen_check_0530' -At $AtCheck -Phase 'check' `
        -Desc '每日 05:30 盘前数据检查；发现问题按责任部门发内部工单要求立即处理'
}
if ($wantRescue) {
    Register-One -Name 'preopen_rescue_0700' -At $AtRescue -Phase 'rescue' `
        -Desc '每日 07:00 核验部门回执；未回执的由云端模型出抢险方案并加急催办（不代执行）'
}

if (-not $Apply) {
    Write-Output ''
    Write-Output '（预演，未注册。确认五要素后加 -Apply）'
    exit 0
}
Write-Output 'ALL_REGISTER_OK'
