# install_houfeng_settle_task.ps1 - 注册侯风「当日结算」任务（周一至周五 16:40）
#
# 为什么需要这个任务（2026-11-11 判例）：
#   15:10 那趟拿不到当日日线（adj_factor 取得到、daily 取不到），当日到期的台账项
#   只能记「待结算」，靠次日 15:10 重试才补上 —— 数字没错，但台账 ts 晚一天。
#   16:40 这次运行让当日结清落到当天，同时产生第二个收盘后快照，
#   使价格守卫 R1「两轮收盘后快照互比」有对比对象（一天一跑时那一级永不触发）。
#
# 与 15:10 任务的差别：跑 settle + snap + arena + sector + lens + registry，不跑数据巡检
# （同日同批数据，重复检无信息）。
#
# 2026-11-12 再补：加 registry（逻辑产物新鲜度）。它只在 16:40 这趟跑——
# 15:10 时当日行情日线还没落盘，lag 的 clock 会退回上一交易日，判定虽自洽
# 但与盘后口径不同，同一天出两套结论没有意义。rc=2 直接调 alert_fail.py 发飞书。
#
# 2026-11-12 补：原注册参数是 "settle,snap"，导致后来加的 arena / lens 阶段
# 从未在调度里真正跑过（脚本支持 ≠ 任务参数已更新）。用 -Update 改参数，
# 不动触发器与身份，可回滚。
#
# 默认只打印将要执行的动作，加 -Apply 才真正注册。/ -Update 更新已有任务的参数。
param([switch]$Apply, [switch]$Update)

$ErrorActionPreference = 'Stop'
$name = 'houfeng_settle_daily'
# 路径从脚本自身位置推导，不写死本机绝对路径（本仓对外可见，2026-11-11 脱敏）
if (-not $PSScriptRoot) {
    throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）'
}
$ps1  = Join-Path $PSScriptRoot 'houfeng_snap_task.ps1'
$dir  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe  = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log  = 'C:\Temp\houfeng_settle_task.log'   # 不含用户名的通用临时目录；与已注册任务保持一致
$arg  = '-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "' + $ps1 +
        '" -Phases "settle,snap,arena,sector,lens,registry" -Log "' + $log + '" -Tag houfeng_settle'

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
Write-Output ('内容:   settle + snap + arena + sector + lens + registry（跳过数据巡检）；registry rc=2 发飞书告警')
Write-Output ('日志:   ' + $log + '（与 15:10 那趟分开，避免交错）')
Write-Output ('设置:   StartWhenAvailable=开, MultipleInstances=IgnoreNew, 单次上限 30 分钟')
Write-Output ('身份:   ' + $env:USERNAME + ' / Interactive / Limited')

if (-not $Apply) {
    if ($Update) {
        $cur = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($cur) {
            $oldArg = ($cur.Actions | ForEach-Object { $_.Arguments }) -join ' | '
            Write-Output ''
            Write-Output ('原参数: ' + $oldArg)
            Write-Output ('新参数: ' + $arg)
            if ($oldArg -eq $arg) { Write-Output '[无需改动] 参数已一致。' }
        } else {
            Write-Output ('[注意] 任务 ' + $name + ' 不存在，-Update 无从更新。')
        }
    }
    Write-Output ''
    Write-Output '[预览模式] 未做任何改动。注册用 -Apply；已存在想改参数用 -Update -Apply。'
    exit 0
}

# -Update：只替换动作（参数），触发器/身份/设置原样保留
if ($Update) {
    $exist = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $exist) {
        Write-Output '[中止] 任务不存在，请先用 -Apply 注册。'
        exit 2
    }
    $oldArg = ($exist.Actions | ForEach-Object { $_.Arguments }) -join ' | '
    Write-Output ''
    Write-Output ('原参数: ' + $oldArg)
    Write-Output ('新参数: ' + $arg)
    if ($oldArg -eq $arg) {
        Write-Output '[无需改动] 参数已一致。'
        exit 0
    }
    Set-ScheduledTask -TaskName $name -Action $act | Out-Null
    $chk = Get-ScheduledTask -TaskName $name
    $nowArg = ($chk.Actions | ForEach-Object { $_.Arguments }) -join ' | '
    $ok = ($nowArg -eq $arg)
    Write-Output ('[已更新] 参数一致=' + $ok + '  State=' + $chk.State)
    if (-not $ok) { exit 1 }
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
