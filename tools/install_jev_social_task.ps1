# JEVSocialDaily installer — JEV 社交快照「每日盘后前向采集」定时任务注册器。
#
# 背景（v2 P2.2 修正）：雪球/股吧接口只返回「查询时刻」实时榜，不接收日期参数，
#   历史回补物理不可行。唯一能补社交信号的方式 = 每个交易日后跑一次、逐日累积。
#   本任务 18:30 触发 C:\aios\tools\run_social_fetch_daily.bat：
#     周末 / 当日 daily 分片未落盘 → 跳过（不写盘、不误标）
#     真实交易日 → 采集 → portal/social_YYYYMMDD.json（原子替换）
#
# 为什么不用 schtasks.exe：本机安全中心把 schtasks.exe 列入程序黑名单，
#   调用被拒（Program Blacklist）。仓内既有正路 = ScheduledTasks cmdlet，
#   见 tools/install_jev_restart_task.ps1，本脚本照其模式实现。
#
# 用法（注册需管理员；-Elevate 可弹 UAC 自提权）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_jev_social_task.ps1 -Verify
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_jev_social_task.ps1 -Elevate
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_jev_social_task.ps1 -Uninstall
#
# 设计取舍：
#   · 任务名用 ASCII（JEVSocialDaily）—— PS 5.1 处理中文任务名不可靠；
#   · action 走 C:\aios 宪法别名（ASCII），不用中文真实路径（同一坑）；
#   · RunLevel Limited：只是跑 python 写 portal 目录，不需要提权，最小权限；
#   · LogonType Interactive：仅当前用户登录时运行 + StartWhenAvailable 补跑；
#     ⚠ 若需「未登录也跑」要改 S4U（需密码，本脚本不做，须老田另批）；
#   · MultipleInstances IgnoreNew：上一轮没跑完不叠跑；
#   · 18:30：daily 分片 15:39 更新，留近 3 小时余量；休市日 daily 不落盘 → 自动跳过。
#   · 本脚本只注册/注销任务，**不执行采集**；采集由任务触发或手工跑 .bat。

param(
    [switch]$Verify,
    [switch]$Uninstall,
    [switch]$Elevate
)

$ErrorActionPreference = 'Stop'
$TaskName  = 'JEVSocialDaily'
$BatPath   = 'C:\aios\tools\run_social_fetch_daily.bat'
$PyPath    = 'C:\aios\tools\run_social_fetch_daily.py'
$ResultFile = 'C:\aios\tools\social_task_install.result'
$RunLog    = 'C:\aios\tools\social_fetch_daily.log'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Result([string]$Line) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $text = "[$ts] $Line"
    [Console]::Out.WriteLine($text)
    try { Add-Content -Path $ResultFile -Value $text -Encoding UTF8 } catch { }
}

# ---------- 自提权 ----------
if ($Elevate -and -not (Test-Admin)) {
    $ps = (Get-Process -Id $PID).Path
    $args = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    try {
        Start-Process -FilePath $ps -ArgumentList $args -Verb RunAs | Out-Null
        Write-Output '已请求提权（UAC）。请在弹窗点「是」，随后看结果文件：'
        Write-Output ('  ' + $ResultFile)
        exit 0
    } catch {
        Write-Output ('提权失败: ' + $_.Exception.Message)
        exit 2
    }
}

# ---------- 现状检查 ----------
function Show-State {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Result "STATE absent (task $TaskName not registered)"; return $false }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Result ("STATE present logon=" + $t.Principal.LogonType +
        " user=" + $t.Principal.UserId +
        " runlevel=" + $t.Principal.RunLevel +
        " state=" + $t.State +
        " triggers=" + @($t.Triggers).Count +
        " lastResult=" + $i.LastTaskResult)
    foreach ($a in $t.Actions) { Write-Result ("  action: " + $a.Execute + ' ' + $a.Arguments) }
    foreach ($g in $t.Triggers) { Write-Result ("  trigger: " + $g.StartBoundary) }
    return $true
}

if ($Verify) {
    $found = Show-State
    Write-Result ('bat exists=' + (Test-Path $BatPath) + ' py exists=' + (Test-Path $PyPath))
    if (Test-Path $RunLog) {
        Write-Result ('run log tail: ' + ((Get-Content $RunLog -Tail 1) -join ''))
    }
    if (-not $found) { exit 3 }
    exit 0
}

if (-not (Test-Admin)) {
    Write-Output '需要管理员权限。请用管理员 PowerShell 重跑，或加 -Elevate 走 UAC。'
    exit 2
}

# ---------- 注销 ----------
if ($Uninstall) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Result ('UNINSTALLED ' + $TaskName + '（已采集的 social_*.json 快照保留，不删）')
    } else {
        Write-Result 'UNINSTALL skip: not present'
    }
    exit 0
}

# ---------- 注册 ----------
if (-not (Test-Path $BatPath) -or -not (Test-Path $PyPath)) {
    Write-Result ('ABORT: 触发器不存在 bat=' + $BatPath + ' py=' + $PyPath)
    exit 4
}

$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$act = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/c "' + $BatPath + '"')
$trg = New-ScheduledTaskTrigger -Daily -At '18:30'
$prin = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable -Priority 6

try {
    Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg `
        -Principal $prin -Settings $set -Force | Out-Null
    Write-Result ('REGISTERED ' + $TaskName + ' as ' + $me +
        ' (Interactive/Limited, daily 18:30, cmd -> bat)')
} catch {
    Write-Result ('REGISTER FAILED: ' + $_.Exception.Message)
    exit 5
}

Show-State | Out-Null
Write-Result '验证: install_jev_social_task.ps1 -Verify ；手工跑一次: ' + $BatPath
Write-Result '回滚: install_jev_social_task.ps1 -Uninstall'
exit 0
