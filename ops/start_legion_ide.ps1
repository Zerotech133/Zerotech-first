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
  日志：C:\Temp\legion_ide_launcher.log（只增）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\start_legion_ide.ps1 -Action start
  powershell -ExecutionPolicy Bypass -File ops\start_legion_ide.ps1 -Action app
#>
[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "app")]
    [string]$Action = "start",
    [int]$Port = 8103,
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Python = "C:\项目\py312\python.exe",
    [string]$LogFile = "C:\Temp\legion_ide_launcher.log"
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
        if ($p) { Write-LaunchLog "legion-ide already running pid=$p"; exit 0 }
        $cmd = '"' + $Python + '" "' + $serve + '" --port ' + $Port
        try {
            $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
                CommandLine = $cmd; CurrentDirectory = $Repo
            }
            if ($r.ReturnValue -eq 0) {
                Write-LaunchLog "STARTED legion-ide pid=$($r.ProcessId) port=$Port"
                Start-Sleep -Seconds 2
                if (Get-IdePid) { exit 0 }
                Write-LaunchLog "WARN started but port not listening yet"
                exit 0
            }
            Write-LaunchLog "WARN WMI create rc=$($r.ReturnValue); fallback"
        } catch {
            Write-LaunchLog "WARN WMI error: $($_.Exception.Message); fallback"
        }
        Start-Process -FilePath $Python -ArgumentList @("`"$serve`"", "--port", "$Port") `
            -WorkingDirectory $Repo -WindowStyle Hidden
        Write-LaunchLog "STARTED legion-ide (Start-Process fallback) port=$Port"
        Start-Sleep -Seconds 2
        exit 0
    }
    "app" {
        & $PSCommandPath -Action start -Port $Port -Repo $Repo -Python $Python | Out-Host
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
