# install_check_link_relpath_task.ps1 - 注册退层路径守卫（每天 08:00）
#
# ⚠ 本脚本默认只打印将要做的事，不注册。加 -Apply 才真正写入计划任务。
#   按项目铁律：改动计划任务属系统级变更，需老田明确批准后执行。
#
# ── 批准五要素 ──────────────────────────────────────────────────────────
# ① 命令全文：
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\scheduled\install_check_link_relpath_task.ps1 -Apply
#    实际注册的动作：
#    C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -WindowStyle Hidden
#      -NoProfile -ExecutionPolicy Bypass
#      -File C:\aios\tools\scheduled\check_link_relpath_task.ps1 -Log C:\Temp\link_relpath.log
#    触发器：每天 08:00；StartWhenAvailable（错过的补跑一次）
# ② 影响面：
#    - 新增一个计划任务 link_relpath_guard，不改动任何已有任务
#      （preopen_daily / close_review_daily / PaperTrade / houfeng_* / FactorDaily 一律不动）；
#    - 每次**只读**：遍历约 2600 个 py 文件求值路径常量，不写任何业务文件；
#    - 唯一写入是 C:\Temp\link_relpath.log（一行一条，追加）；
#    - 不访问网络、不读行情、不碰账本。
# ③ 不可逆点：
#    - 无。全链路只读，无派生账、无覆盖写；
#    - 计划任务本身可 Unregister-ScheduledTask 撤销，无残留。
# ④ 替代方案：
#    - 不注册，手工跑 python tools\audit\check_link_relpath.py --quiet（一样一条命令，
#      只是要人记得；而这类缺陷的特征恰恰是「没人会想起来查」）；
#    - 或改到其他时点（只需改 -At）；08:00 的理由：它要在 preopen 08:10 与
#      PaperTrade 09:35 **之前**跑完，当天发现当天还能处置。
# ⑤ 回滚预案：
#    Unregister-ScheduledTask -TaskName link_relpath_guard -Confirm:$false
#    并删除 C:\Temp\link_relpath.log。
# ────────────────────────────────────────────────────────────────────────
#
# 为什么需要这个守卫：见 check_link_relpath.py 顶部判例（2026-11-16 paper_trade
# 抑制令经 C:\aios 别名入口静默失效）。这类缺陷不报错——算错的路径与「本来就没有」
# 返回值一样，所以只能靠主动去量。
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'link_relpath_guard'
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1 = Join-Path $PSScriptRoot 'check_link_relpath_task.ps1'
$dir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# 任务参数里**不能出现中文路径**：PS5.1 对含中文的路径不可信（Test-Path 返回
# False、参数被拆等），而任务参数一旦解析失败，任务会天天「跑了但什么都没做」。
# C:\aios 是项目根的 ASCII 别名 junction，正好用来规避 —— 且守卫本身就是要
# 在别名入口下工作，用别名启动同时验证了这一点。
# 别名不存在时退回实体路径，但会在下面显式告警。
$alias = 'C:\aios'
$aliasPs1 = Join-Path $alias 'tools\scheduled\check_link_relpath_task.ps1'
if (Test-Path $aliasPs1) {
    $ps1 = $aliasPs1
    $dir = $alias
} else {
    Write-Output ('WARN 别名入口不存在，任务参数将含中文路径: ' + $alias)
}
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = 'C:\Temp\link_relpath.log'
$arg = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '" -Log "' + $log + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$trg = New-ScheduledTaskTrigger -Daily -At '08:00'
$act = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output "任务名   : $name"
Write-Output "触发器   : 每天 08:00"
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
    Set-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set | Out-Null
    Write-Output "已更新既有任务: $name"
} else {
    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $prn `
        -Description 'retired-path guard: detect path constants that resolve differently via the C:\aios alias junction' | Out-Null
    Write-Output "已注册: $name"
}

# 注册后必须读回核对 —— Register-ScheduledTask 失败时仍会打印后续行，看着像成功
$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if (-not $t) { throw "注册后读不到任务 $name" }
Write-Output ('核对: state={0} action={1}' -f $t.State, ($t.Actions | ForEach-Object { $_.Execute }))
Write-Output ('核对: args={0}' -f ($t.Actions | ForEach-Object { $_.Arguments }))
Write-Output ('核对: trigger={0} user={1}' -f ($t.Triggers | ForEach-Object { $_.StartBoundary }), $t.Principal.UserId)
