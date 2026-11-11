# install_houfeng_settle_task.ps1 - 注册侯风「当日结算」任务（周一至周五 16:40）
#
# 为什么需要这个任务（2026-11-11 判例）：
#   15:10 那趟拿不到当日日线（adj_factor 取得到、daily 取不到），当日到期的台账项
#   只能记「待结算」，靠次日 15:10 重试才补上 —— 数字没错，但台账 ts 晚一天。
#   16:40 这次运行让当日结清落到当天，同时产生第二个收盘后快照，
#   使价格守卫 R1「两轮收盘后快照互比」有对比对象（一天一跑时那一级永不触发）。
#
# 与 15:10 任务的差别：只跑 settle + snap，不跑数据巡检（同日同批数据，重复检无信息）。
#
# 默认只打印将要执行的动作，加 -Apply 才真正注册。同名任务已存在则报错退出，不覆盖。
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'houfeng_settle_daily'
$ps1  = 'C:\项目\老田的AI助手\tools\scheduled\houfeng_snap_task.ps1'
$dir  = 'C:\项目\老田的AI助手'
$exe  = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$log  = 'C:\Temp\houfeng_settle_task.log'
$arg  = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 +
        '" -Phases "settle,snap" -Log "' + $log + '" -Tag houfeng_settle'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg  = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '16:40')
$act  = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn  = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output ('任务名: ' + $name)
Write-Output ('动作:   ' + $exe)
Write-Output ('参数:   ' + $arg)
Write-Output ('目录:   ' + $dir)
Write-Output ('触发:   周一至周五 16:40（当日日线此时已发布，11-11 实测 15:50 就可拉到）')
Write-Output ('内容:   cohort_track.py --settle 再 --snap；跳过数据巡检')
Write-Output ('日志:   ' + $log + '（与 15:10 那趟分开，避免交错）')
Write-Output ('设置:   StartWhenAvailable=开, MultipleInstances=IgnoreNew, 单次上限 30 分钟')
Write-Output ('身份:   ' + $env:USERNAME + ' / Interactive / Limited')

if (-not $Apply) {
    Write-Output ''
    Write-Output '[预览模式] 未做任何改动。确认无误后加 -Apply 执行注册。'
    exit 0
}

$exist = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($exist) {
    Write-Output ('[中止] 同名任务已存在（State=' + $exist.State + '），不覆盖。')
    exit 2
}

Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $prn `
    -Description '侯风台账当日结算：16:40 用当日日线结清到期项，并出第二个收盘后快照（供价格守卫互比）。幂等，可重跑。' | Out-Null

$t = Get-ScheduledTask -TaskName $name
$i = Get-ScheduledTaskInfo -TaskName $name
Write-Output ''
Write-Output ('[已注册] State=' + $t.State + '  NextRun=' + $i.NextRunTime)
