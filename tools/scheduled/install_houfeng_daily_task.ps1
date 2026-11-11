# install_houfeng_daily_task.ps1 - 注册侯风每日信号产线的计划任务
#
# 默认只打印将要执行的动作，加 -Apply 才真正注册。
# 若同名任务已存在，Register-ScheduledTask 会报错退出（不覆盖既有任务）。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_houfeng_daily_task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_houfeng_daily_task.ps1 -Apply
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'houfeng_daily_signal'
$ps1  = 'C:\项目\老田的AI助手\tools\scheduled\houfeng_daily_task.ps1'
$dir  = 'C:\项目\老田的AI助手'
$exe  = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$arg  = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg  = @(
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '08:40'),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '09:40'),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '10:40')
)
$act  = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir
$set  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$prn  = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Write-Output ('任务名: ' + $name)
Write-Output ('动作:   ' + $exe)
Write-Output ('参数:   ' + $arg)
Write-Output ('目录:   ' + $dir)
Write-Output ('触发:   周一至周五 08:40 / 09:40 / 10:40')
Write-Output ('设置:   StartWhenAvailable=开, MultipleInstances=IgnoreNew, 单次上限 20 分钟')
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
    -Description '侯风每日信号产线：拉增量 -> 选股(auto/main) -> 登记批次。08:40/09:40/10:40 三次互为重试，幂等；不跑快照、不广播。' | Out-Null

$t = Get-ScheduledTask -TaskName $name
$i = Get-ScheduledTaskInfo -TaskName $name
Write-Output ''
Write-Output ('[已注册] State=' + $t.State + '  NextRun=' + $i.NextRunTime)
