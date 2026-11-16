#requires -Version 5.1
<#
.SYNOPSIS
  军团 Agent IDE 幂等保活（5 分钟级）：8103 端口无人监听则拉起，已在跑则 no-op。

.DESCRIPTION
  供计划任务（每 5 分钟）与登录触发器调用；也可手动运行。
  判活以端口 8103 监听为准（服务自身 /api/health 可再确认）。
  日志：C:\Temp\legion_ide_keepalive.log（只增）。
  本脚本不是危险操作：只做"缺则拉起"，不发信号、不改系统配置。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\keepalive_legion_ide.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Python = "C:\Program Files\Python311\python.exe",
    [int]$Port = 8103,
    [string]$LogFile = "C:\Temp\legion_ide_keepalive.log",
    [switch]$DryRun
)

function Write-KeepLog([string]$Message) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $Message
    $dir = Split-Path -Parent $LogFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $line
}

$launcher = Join-Path $Repo "ops\start_legion_ide.ps1"
if (-not (Test-Path -LiteralPath $launcher)) { Write-KeepLog "ERROR launcher missing: $launcher"; exit 1 }

$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-KeepLog "OK legion-ide alive pid=$($conn.OwningProcess) port=$Port"
    exit 0
}

if ($DryRun) { Write-KeepLog "DRY-RUN: would start legion-ide port=$Port"; exit 0 }

# 2026-10-31 P0-4：保活与手工启动参数必须一致，否则 /rw 无 token、cap 未启用
# 2026-11-16：local_token 路径原先硬编码，会随 push 暴露本机目录结构
# （vault_path_inventory_20261116 A 级第 8 条）。改为环境变量优先、仓库根 .env.local 兜底
# （该文件被 .gitignore 的 `.env.*` 忽略）。取不到则不启用 /rw 与 cap（与原先"文件不存在"一致）。
$startArgs = @('-Action', 'start', '-Port', $Port, '-Repo', $Repo, '-Python', $Python, '-EnableWrite')
$tokenFile = $env:LEGION_LOCAL_TOKEN_FILE
if (-not $tokenFile) {
    $envLocal = Join-Path (Split-Path -Parent $PSScriptRoot) '.env.local'
    if (Test-Path -LiteralPath $envLocal) {
        foreach ($line in (Get-Content -LiteralPath $envLocal -Encoding UTF8)) {
            if ($line -match '^\s*LEGION_LOCAL_TOKEN_FILE\s*=') {
                $tokenFile = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}
if ($tokenFile -and (Test-Path -LiteralPath $tokenFile)) {
    $startArgs += @('-LocalTokenFile', $tokenFile, '-EnableCap')
}
Write-KeepLog ("START args: " + ($startArgs -join ' '))
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher @startArgs | Out-Null
Start-Sleep -Seconds 2
$conn2 = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn2) { Write-KeepLog "STARTED legion-ide pid=$($conn2.OwningProcess) port=$Port"; exit 0 }
Write-KeepLog "ERROR start attempted but port $Port not listening"; exit 1

