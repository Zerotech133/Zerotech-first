# install_preopen_task.ps1 - 注册后峰盘前任务（周一至周五 08:10）
#
# ⚠ 本脚本默认只打印将要做的事，不注册。加 -Apply 才真正写入计划任务。
#   按项目铁律：改动计划任务属系统级变更，需老田明确批准后执行。
#
# ── 批准五要素 ──────────────────────────────────────────────────────────
# ① 命令全文：
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\scheduled\install_preopen_task.ps1 -Apply
#    实际注册的动作：
#    powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass
#      -File <repo>\tools\scheduled\preopen_task.ps1 -Log C:\Temp\preopen_task.log
#    触发器：周一至周五 08:10，StartWhenAvailable（错过的补跑一次）
# ② 影响面：
#    - 新增一个计划任务名 preopen_daily，不改动已有任务（houfeng_snap / houfeng_settle 等）；
#    - 每日 08:10 会读 quant_backtest\data 下 tdx 分片（只读），
#      写 C:\项目\deliverables\ 下两个产物 + C:\Temp\preopen_task.log；
#    - 选票脚本会按需从源端补拉缺失的龙虎榜（只读外网，不改本地历史）。
# ③ 不可逆点：
#    - 产物文件覆盖写（preopen_report_<date>.html / preopen_pick15_*.json）；
#      旧内容不在版本库内，覆盖后不可恢复（但可由当日数据重跑重生）。
#    - 计划任务本身可 Unregister-ScheduledTask 撤销，无残留。
# ④ 替代方案：
#    - 不注册，改为手工跑 tools\preopen\run_preopen.py（一样一条命令，只是要人记得）；
#    - 或注册到 08:00/08:20 等其他时点（只需改 -At）。
# ⑤ 回滚预案：
#    Unregister-ScheduledTask -TaskName preopen_daily -Confirm:$false
#    并删除 C:\Temp\preopen_task.log。
# ────────────────────────────────────────────────────────────────────────
#
# 为什么是 08:10：见 preopen_task.ps1 顶部注释（04:26 同步已落地、09:15 竞价前要能转出、
# 08:30 后接口会开始返回当日数据）。
#
# 为什么不用 -Update：这是新任务，首次注册用 -Apply；后续改参数再加 -Update 支持。
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'preopen_daily'
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1 = Join-Path $PSScriptRoot 'preopen_task.ps1'
$dir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = 'C:\Temp\preopen_task.log'
$arg = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '" -Log "' + $log + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '08:10'
$act = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output "任务名   : $name"
Write-Output "触发器   : 周一至周五 08:10"
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
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg `
        -Settings $set -Principal $prn -Description '后峰盘前：数据校对→精选→简报H5' | Out-Null
}
Write-Output '已注册。验证：Get-ScheduledTask -TaskName preopen_daily | Get-ScheduledTaskInfo'
