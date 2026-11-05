# JevRestart installer — 方案A：给 JEV 三件套一个唯一的、有权限的、可追溯的重启入口。
#
# 背景：9101/9102/9103 现落在 session 0，用户态 taskkill 报「拒绝访问」；
#       而提权后（RunLevel Highest）同一账号可以终止跨会话的同名用户进程。
# 因此本任务 = 用户 t + InteractiveToken + Highest + 无触发器（只按需触发）。
#
# 用法（必须管理员）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_jev_restart_task.ps1
#   ... -Verify      只检查现状，不改动
#   ... -Uninstall   注销 JevRestart
#   ... -Elevate     从普通会话自提权（弹 UAC）后执行注册
#
# 设计取舍：
#   · 不用 SYSTEM：jev-gate.env.ps1 走 DPAPI（用户级），jev-gate.env.plain.ps1 明文回退
#     文件当前不存在，SYSTEM 下解密失败将导致 env 加载失败；
#   · 不挂触发器：重启是显式动作，避免与 JevStack（10 分钟看护）互相打架；
#   · action 用 C:\aios 别名（ASCII），不用中文真实路径（PS 5.1 对中文路径不可靠）。
#   · 注意 AIOS-S4U-Guard 会尝试把含 'aios' 的用户任务改成 S4U；若它改成功，
#     本任务会落到 session 0 且 DPAPI 可能失败 — 由 -Verify 检查并告警。

param(
    [switch]$Verify,
    [switch]$Uninstall,
    [switch]$Elevate
)

$ErrorActionPreference = 'Stop'
$TaskName = 'JevRestart'
$ScriptPath = 'C:\aios\jev-eval\start_jev_stack.ps1'
$StackLog = 'C:\aios\jev-eval\logs\jev-stack.log'
$ResultFile = 'C:\aios\jev-eval\logs\jev_restart_install.result'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Result([string]$Line) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $text = "[$ts] $Line"
    # 用 Console 直写，避免被函数调用方的 if 条件捕获（Write-Output 会被吞进表达式）
    [Console]::Out.WriteLine($text)
    try {
        New-Item -ItemType Directory -Path (Split-Path $ResultFile) -Force | Out-Null
        Add-Content -Path $ResultFile -Value $text -Encoding UTF8
    } catch { }
}

# ---------- 自提权 ----------
if ($Elevate -and -not (Test-Admin)) {
    $ps = (Get-Process -Id $PID).Path
    $args = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    try {
        Start-Process -FilePath $ps -ArgumentList $args -Verb RunAs | Out-Null
        Write-Output '已请求提权（UAC）。请在弹窗里点「是」，然后等待结果文件生成。'
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
    if ($t.Principal.LogonType -eq 'S4U') {
        Write-Result '  WARN 已被 AIOS-S4U-Guard 改成 S4U（DPAPI 可能失败，需重注册）'
    }
    return $true
}

if ($Verify) {
    $found = Show-State
    if (-not $found) { exit 3 }
    if (Test-Path $StackLog) {
        Write-Result ('stack log tail: ' + ((Get-Content $StackLog -Tail 1) -join ''))
    }
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
        Write-Result 'UNINSTALLED JevRestart'
    } else {
        Write-Result 'UNINSTALL skip: not present'
    }
    exit 0
}

# ---------- 注册 ----------
if (-not (Test-Path $ScriptPath)) {
    Write-Result ('ABORT: 栈脚本不存在 ' + $ScriptPath)
    exit 4
}

$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ScriptPath + '" -Force')
$prin = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -Priority 6

try {
    Register-ScheduledTask -TaskName $TaskName -Action $act -Principal $prin -Settings $set -Force | Out-Null
    Write-Result ('REGISTERED ' + $TaskName + ' as ' + $me + ' (Interactive/Highest, no trigger)')
} catch {
    Write-Result ('REGISTER FAILED: ' + $_.Exception.Message)
    exit 5
}

Show-State | Out-Null
Write-Result '用法: Start-ScheduledTask -TaskName JevRestart  (= 重启 9101/9102/9103，不碰 9104)'
exit 0
