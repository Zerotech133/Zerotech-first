# 零界个股分析 · 环境激活脚本（每次开新 PowerShell 窗口执行一次）
# 用法: .\activate.ps1 -ConfigFile "$env:LOCALAPPDATA\ZeroBoundStockAnalysis\config.ps1"
param(
  [string]$ConfigFile = "$env:LOCALAPPDATA\ZeroBoundStockAnalysis\config.ps1"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ConfigFile)) {
  throw "配置文件不存在: $ConfigFile（请从 config.example.ps1 复制并填写）"
}
. $ConfigFile

$InstallDir = $env:ZB_INSTALL_DIR
if (-not $InstallDir) { $InstallDir = Split-Path $ConfigFile -Parent }
$env:PYTHONPATH = $InstallDir
$env:COMMERCIAL_MODE = "commercial"
if (-not $env:COMMERCIAL_TENANT -and $env:ZB_TENANT) { $env:COMMERCIAL_TENANT = $env:ZB_TENANT }
if (-not $env:COMMERCIAL_LICENSE_KEY_FILE) { $env:COMMERCIAL_LICENSE_KEY_FILE = Join-Path $InstallDir "license.key" }
if (-not $env:ANALYSIS_WORK) { $env:ANALYSIS_WORK = Join-Path $InstallDir "work" }

$py = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "未找到虚拟环境，请先运行 install.ps1" }

Write-Host "PYTHONPATH = $env:PYTHONPATH"
Write-Host "COMMERCIAL_MODE = $env:COMMERCIAL_MODE ｜ TENANT = $env:COMMERCIAL_TENANT"
& $py -m commercial.cli license-verify --key (Get-Content $env:COMMERCIAL_LICENSE_KEY_FILE -Raw).Trim()
Write-Host "激活完成，可直接运行: python -m analysis.run --code <代码> --no-llm" -ForegroundColor Green
