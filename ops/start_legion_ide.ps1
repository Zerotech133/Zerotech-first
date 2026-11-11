#requires -Version 5.1
<#
.SYNOPSIS
  军团 Agent IDE · 独立形态启动器（start/stop/status/app）

.DESCRIPTION
  独立服务：agent_platform/legion_ide/serve.py（默认 127.0.0.1:8103）。
  - start : 未监听则分离拉起（WMI 优先，回退 Start-Process），幂等；
  - stop  : 按端口找 pid 终止；
  - status: 端口/健康 JSON；
  - app   : 确保服务在跑，并用 Edge App 模式打开独立窗口（桌面应用观感）。
  写/执行能力默认关闭（IDE_RW=off）；用 -EnableWrite 启动才开启（受控写/白名单执行）。
  bundle：优先部署副本 C:\项目\_vault\legion_ide\agent_ide_bundle.json
  （post-merge 自动重烘，避免仓内旧数据），不存在则服务侧退回仓内预烘文件。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\start_legion_ide.ps1 -Action app
  powershell -ExecutionPolicy Bypass -File ops\start_legion_ide.ps1 -Action start -EnableWrite
#>
[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "app")]
    [string]$Action = "start",
    [int]$Port = 8103,
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Python = "C:\Program Files\Python311\python.exe",
    [string]$LogFile = "C:\Temp\legion_ide_launcher.log",
    [string]$Bundle = "",
    [switch]$EnableWrite,
    [string]$LocalTokenFile = "",
    [switch]$EnableCap
)

function Write-LaunchLog([string]$Message) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $Message
    $dir = Split-Path -Parent $LogFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $line
}

function Get-IdePid {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Resolve-BundlePath {
    if ($Bundle) { return $Bundle }
    $deploy = "C:\项目\_vault\legion_ide\agent_ide_bundle.json"
    if (Test-Path -LiteralPath $deploy) { return $deploy }
    return ""
}

$serve = Join-Path $Repo "agent_platform\legion_ide\serve.py"
if (-not (Test-Path -LiteralPath $serve)) { Write-LaunchLog "ERROR serve.py missing: $serve"; exit 1 }

switch ($Action) {
    "status" {
        $p = Get-IdePid
        if (-not $p) { Write-Host "legion-ide : offline (port $Port)"; exit 1 }
        Write-Host "legion-ide : listening pid=$p port=$Port"
        try {
            $h = Invoke-RestMethod -Uri ("http://127.0.0.1:$Port/api/health") -TimeoutSec 5
            $h | ConvertTo-Json -Depth 6
        } catch { Write-Host "health_fail: $($_.Exception.Message)" }
        exit 0
    }
    "stop" {
        $p = Get-IdePid
        if (-not $p) { Write-LaunchLog "legion-ide not running"; exit 0 }
        try { Stop-Process -Id $p -Force -ErrorAction Stop; Write-LaunchLog "stopped pid=$p" }
        catch { Write-LaunchLog "stop failed pid=$p : $($_.Exception.Message)"; exit 1 }
        exit 0
    }
    "start" {
        $p = Get-IdePid
        if ($p) { Write-LaunchLog "legion-ide already running pid=$p (rw 由该进程启动参数决定)"; exit 0 }
        # 写/执行开关：默认 off；-EnableWrite 才 on（受控写/白名单执行）
        $env:IDE_RW = if ($EnableWrite) { "on" } else { "off" }
        # 2026-10-31 K-ORG-20261031-BUNDLEB5：Phase2-B 后 bundle 不再 tracked；
        # 干净环境启动前缺失则 bootstrap，失败直接退出（不带着缺 bundle 起服务）。
        $bootstrapScript = Join-Path $Repo "tools\bundle_bootstrap.py"
        if (Test-Path -LiteralPath $bootstrapScript) {
            & $Python -X utf8 $bootstrapScript --repo $Repo --quiet
            if ($LASTEXITCODE -ne 0) { Write-LaunchLog "ERROR bundle bootstrap failed"; exit 1 }
        }
        $bundlePath = Resolve-BundlePath
        $stateDir = 'C:\项目\_vault\legion_ide'
        if (-not (Test-Path -LiteralPath $stateDir)) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
        $cmd = '"' + $Python + '" "' + $serve + '" --port ' + $Port + ' --state-dir "' + $stateDir + '"'
        if ($bundlePath) { $cmd += ' --bundle "' + $bundlePath + '"' }
        if ($EnableWrite) { $cmd += ' --rw' }   # WMI 不继承 env：显式传参
        if ($LocalTokenFile) { $cmd += ' --local-token-file "' + $LocalTokenFile + '"' }
        if ($EnableCap) { $cmd += ' --cap-enabled' }
        try {
            $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
                CommandLine = $cmd; CurrentDirectory = $Repo
            }
            if ($r.ReturnValue -eq 0) {
                Write-LaunchLog "STARTED legion-ide pid=$($r.ProcessId) port=$Port bundle=$bundlePath IDE_RW=$($env:IDE_RW)"
                Start-Sleep -Seconds 2
                if (Get-IdePid) { exit 0 }
                Write-LaunchLog "WARN started but port not listening yet"
                exit 0
            }
            Write-LaunchLog "WARN WMI create rc=$($r.ReturnValue); fallback"
        } catch {
            Write-LaunchLog "WARN WMI error: $($_.Exception.Message); fallback"
        }
        $argsList = @("`"$serve`"", "--port", "$Port", "--state-dir", "`"$stateDir`"")
        if ($bundlePath) { $argsList += @("--bundle", "`"$bundlePath`"") }
        if ($EnableWrite) { $argsList += @("--rw") }   # WMI 不继承 env：显式传参
        if ($LocalTokenFile) { $argsList += @("--local-token-file", "`"$LocalTokenFile`"") }
        if ($EnableCap) { $argsList += @("--cap-enabled") }
        Start-Process -FilePath $Python -ArgumentList $argsList `
            -WorkingDirectory $Repo -WindowStyle Hidden
        Write-LaunchLog "STARTED legion-ide (Start-Process fallback) port=$Port bundle=$bundlePath IDE_RW=$($env:IDE_RW)"
        Start-Sleep -Seconds 2
        exit 0
    }
    "app" {
        & $PSCommandPath -Action start -Port $Port -Repo $Repo -Python $Python -Bundle $Bundle -EnableWrite:$EnableWrite | Out-Host
        $url = "http://127.0.0.1:$Port/"
        $edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if (Test-Path -LiteralPath $edge) {
            Start-Process -FilePath $edge -ArgumentList @("--app=$url", "--window-size=1440,900")
            Write-LaunchLog "APP window opened: $url"
        } else {
            Start-Process $url
            Write-LaunchLog "browser opened (Edge app mode unavailable): $url"
        }
        exit 0
    }
}

