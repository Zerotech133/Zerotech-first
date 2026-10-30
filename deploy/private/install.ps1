# 零界个股分析 · 私有化部署安装脚本
# 用法（Windows PowerShell，无需管理员）：
#   .\install.ps1 -LicenseKeyFile "C:\path\license.key"
# 可选参数：-InstallDir "$env:LOCALAPPDATA\ZeroBoundStockAnalysis" -PythonExe "python"
param(
  [string]$LicenseKeyFile = "",
  [string]$InstallDir = "$env:LOCALAPPDATA\ZeroBoundStockAnalysis",
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
Write-Host "=== 零界个股分析 · 私有化安装 ===" -ForegroundColor Cyan

# 1) Python 检查
try {
  $ver = & $PythonExe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
} catch { throw "未找到 Python，请先安装 Python 3.9+（勾选 Add to PATH）" }
Write-Host "Python 版本: $ver"
$parts = $ver.Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 9)) {
  throw "需要 Python 3.9+，当前 $ver"
}

# 2) 目录
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "work") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null
Write-Host "安装目录: $InstallDir"

# 3) venv + 依赖
$venv = Join-Path $InstallDir ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
  Write-Host "创建虚拟环境 ..."
  & $PythonExe -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet pyarrow pandas
# 说明：私有化包内 analysis/commercial 代码为源码，无需 pip install 本包；直接 PYTHONPATH 引用。
Write-Host "依赖安装完成"

# 4) 写配置模板 + 激活文件
$cfg = Join-Path $InstallDir "config.example.ps1"
if (-not (Test-Path $cfg)) { Copy-Item ".\config.example.ps1" $cfg }
$envFile = Join-Path $InstallDir "activate.ps1"
if (-not (Test-Path $envFile)) { Copy-Item ".\activate.ps1" $envFile }

# 5) license 校验（可选：安装时就激活）
if ($LicenseKeyFile) {
  if (-not (Test-Path $LicenseKeyFile)) { throw "license 文件不存在: $LicenseKeyFile" }
  $key = (Get-Content $LicenseKeyFile -Raw).Trim()
  $env:PYTHONPATH = (Get-Location).Path
  $env:COMMERCIAL_LICENSE_SECRET = $env:COMMERCIAL_LICENSE_SECRET
  & $py -m commercial.cli license-verify --key $key
  if ($LASTEXITCODE -ne 0) { throw "license 校验失败，请检查 license 文件或密钥" }
  Copy-Item $LicenseKeyFile (Join-Path $InstallDir "license.key") -Force
  Write-Host "license 已激活" -ForegroundColor Green
}

# 6) 冒烟
$env:PYTHONPATH = (Get-Location).Path
& $py -c "import analysis, commercial; print('模块加载 OK')"
Write-Host ""
Write-Host "=== 安装完成 ===" -ForegroundColor Green
Write-Host "下一步:"
Write-Host "  1. 编辑 $InstallDir\config.example.ps1 并复制为 config.ps1，填入数据目录/密钥"
Write-Host "  2. 运行 .\activate.ps1 -ConfigFile $InstallDir\config.ps1 激活环境"
Write-Host "  3. 运行分析: python -m analysis.run --code 600337.SH --no-llm"
