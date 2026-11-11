#requires -Version 5.1
<#
.SYNOPSIS
  治理时钟幂等保活（5 分钟级）：缺失则拉起，已在跑则 no-op。

.DESCRIPTION
  供"计划任务 / 登录触发器"每 5 分钟调用；也可手动运行。
  判活以 lock 文件里的 pid 为准（时钟自身维护心跳与去重）：
    · lock 存在且 pid 存活 → 直接退出 0（不重复起进程）；
    · pid 已死 → 清理陈旧锁后拉起新进程（WMI 分离，不随本脚本退出）；
    · 成功/失败均追加日志 C:\Temp\governance_keepalive.log（长跑落盘纪律）。

  本脚本不是危险操作：只做"缺则拉起"，不改服务/网络/数据；删除/停用只需删计划任务。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\keepalive_governance_clock.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Python = "C:\Program Files\Python311\python.exe",
    [string]$LogFile = "C:\Temp\governance_keepalive.log",
    [switch]$DryRun
)

function Write-KeepLog([string]$Message) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $Message
    $dir = Split-Path -Parent $LogFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $line
}

$lockPath = Join-Path $Repo "agent_platform\data\governance_clock.lock"
$clockPy = Join-Path $Repo "ops\governance_clock.py"
if (-not (Test-Path -LiteralPath $clockPy)) {
    Write-KeepLog "ERROR clock script missing: $clockPy"
    exit 1
}

# --- 1) 判活：lock 里的 pid 是否真的活着 ---
$runningPid = $null
if (Test-Path -LiteralPath $lockPath) {
    try {
        $info = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $cand = [int]$info.pid
        $proc = Get-Process -Id $cand -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) { $runningPid = $cand }
    } catch {
        Write-KeepLog "WARN unreadable lock: $($_.Exception.Message)"
    }
}
if ($runningPid) {
    Write-KeepLog "OK governance_clock alive pid=$runningPid"
    exit 0
}

# --- 2) 陈旧锁清理（时钟自身对 >6h 锁有回收，但死 pid 的近期锁会误拒启动）---
if (Test-Path -LiteralPath $lockPath) {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    Write-KeepLog "cleared stale lock"
}

if ($DryRun) {
    Write-KeepLog "DRY-RUN: would start governance_clock"
    exit 0
}

# --- 3) 分离拉起（WMI 优先；失败回退 Start-Process）---
$commandLine = '"' + $Python + '" "' + $clockPy + '" --loop'
try {
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $Repo
    }
    if ($r.ReturnValue -eq 0) {
        Write-KeepLog "STARTED governance_clock pid=$($r.ProcessId)"
        exit 0
    }
    Write-KeepLog "WARN WMI create rc=$($r.ReturnValue); falling back"
} catch {
    Write-KeepLog "WARN WMI create error: $($_.Exception.Message); falling back"
}
try {
    Start-Process -FilePath $Python -ArgumentList @("`"$clockPy`"", "--loop") `
        -WorkingDirectory $Repo -WindowStyle Hidden
    Write-KeepLog "STARTED governance_clock (Start-Process fallback)"
    exit 0
} catch {
    Write-KeepLog "ERROR start failed: $($_.Exception.Message)"
    exit 1
}

