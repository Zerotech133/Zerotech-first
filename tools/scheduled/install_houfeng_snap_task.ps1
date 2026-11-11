# install_houfeng_snap_task.ps1 - 注册侯风台账盘后快照任务
#
# 默认只打印将要执行的动作，加 -Apply 才真正注册。
# 若同名任务已存在，直接报错退出，不覆盖既有任务。
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$name = 'houfeng_snap_daily'
$ps1  = 'C:\项目\老田的AI助手\tools\scheduled\houfeng_snap_task.ps1'
$dir  = 'C:\项目\老田的AI助手'
$exe  = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe'
$arg  = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '"'

if (-not (Test-Path $ps1)) { throw ('脚本不存在: ' + $ps1) }

$days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$trg  = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '15:10')
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
Write-Output ('触发:   周一至周五 15:10（收盘后 10 分钟，接口返回的现价即收盘价）')
Write-Output ('内容:   先 cohort_track.py --settle，再 --snap（顺序不可颠倒）')
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
    -Description '侯风台账盘后快照：先结算到期项，再出全景快照（含滚动降级线）。幂等，可重跑。' | Out-Null

$t = Get-ScheduledTask -TaskName $name
$i = Get-ScheduledTaskInfo -TaskName $name
Write-Output ''
Write-Output ('[已注册] State=' + $t.State + '  NextRun=' + $i.NextRunTime)
