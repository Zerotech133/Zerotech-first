# install_close_review_task.ps1 - 注册收盘复盘任务（周一至周五 15:40）
#
# ⚠ 本脚本默认只打印将要做的事，不注册。加 -Apply 才真正写入计划任务。
#   按项目铁律：改动计划任务属系统级变更，需老田明确批准后执行。
#
# ── 批准五要素 ──────────────────────────────────────────────────────────
# ① 命令全文：
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\scheduled\install_close_review_task.ps1 -Apply
#    实际注册的动作：
#    powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass \
#      -File <repo>\tools\scheduled\close_review_task.ps1 -Log C:\Temp\close_review_task.log
#    触发器：周一至周五 15:40，StartWhenAvailable（错过的补跑一次）
# ② 影响面：
#    - 新增一个计划任务 close_review_daily，不改动已有任务
#      （preopen_daily / houfeng_settle_daily / FactorDaily 等一律不动）；
#    - 每个交易日 15:40 读 deliverables 下当日篮子与选票产物（只读），
#      写 3 处：deliverables\close_review_<day>.md / .json
#               var\preopen\close_review\ledger.jsonl（append-only 累计台账）
#               C:\Temp\close_review_task.log 与 close_review_launch.jsonl（落痕）；
#    - 会访问外网行情（hq.sinajs.cn）取当日收盘快照；本地已有当日数据时优先用本地，
#      不外发任何本地数据（只发股票代码）。
# ③ 不可逆点：
#    - 同一天重跑会覆盖当日 md/json（可由当日数据重生，无长期损失）；
#    - ledger.jsonl 是 append-only，重跑会**追加**一行同日记录（读取时按 exec_day
#      取最后一条，不会重复计数），但历史行不会消失 —— 这是刻意的：账要留痕。
#    - 计划任务本身可 Unregister-ScheduledTask 撤销，无残留。
# ④ 替代方案：
#    - 不注册，手工跑 python tools\review\close_review.py（一样一条命令，只是要人记得）；
#    - 或改到 15:50 / 16:00（只需改 -At），但晚于 16:40 会与 houfeng_settle 抢 IO。
# ⑤ 回滚预案：
#    Unregister-ScheduledTask -TaskName close_review_daily -Confirm:$false
#    并删除 C:\Temp\close_review_task.log、close_review_launch.jsonl。
# ────────────────────────────────────────────────────────────────────────
#
# 为什么是 15:40：见 close_review_task.ps1 顶部注释（15:00 收盘、15:05 后快照稳定、
# 与 16:40 的 houfeng_settle 错开、16:00 前能转发）。
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'close_review_daily'
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1 = Join-Path $PSScriptRoot 'close_review_task.ps1'
$dir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = 'C:\Temp\close_review_task.log'
$arg = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '" -Log "' + $log + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '15:40'
$act = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output "任务名   : $name"
Write-Output "触发器   : 周一至周五 15:40"
Write-Output "执行     : $exe"
Write-Output "参数     : $arg"
Write-Output "工作目录 : $dir"
Write-Output "日志     : $log"

if (-not $Apply) {
    Write-Output ''
    Write-Output '（预演，未注册。确认五要素后加 -Apply）'
    exit 0
}

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    # 与 install_preopen_task.ps1 同款：只更新动作/触发/设置，不改任务身份
    Set-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set | Out-Null
    Write-Output "已更新既有任务: $name"
} else {
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $prn `
        -Description '收盘复盘：当日盘前选票的 1 日成绩与同池超额（含 40 日累计台账）' | Out-Null
    Write-Output "已注册: $name"
}

# 注册后必须读回核对 —— Register-ScheduledTask 失败时仍会打印后续行，看着像成功
$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if (-not $t) { throw "注册后读不到任务 $name" }
Write-Output ('核对: state={0} action={1}' -f $t.State, ($t.Actions | ForEach-Object { $_.Execute }))
