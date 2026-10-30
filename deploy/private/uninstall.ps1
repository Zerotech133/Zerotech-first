# 零界个股分析 · 卸载脚本（保留数据目录，除非 -Purge）
param(
  [string]$InstallDir = "$env:LOCALAPPDATA\ZeroBoundStockAnalysis",
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
Write-Host "卸载目录: $InstallDir"
if (-not (Test-Path $InstallDir)) { Write-Host "目录不存在，无需卸载"; exit 0 }

if ($Purge) {
  Write-Host "警告：将删除全部数据与报告" -ForegroundColor Yellow
  $ans = Read-Host "确认删除? 输入 YES"
  if ($ans -ne "YES") { Write-Host "已取消"; exit 1 }
  Remove-Item -Recurse -Force $InstallDir
  Write-Host "已完全删除" -ForegroundColor Green
} else {
  Remove-Item -Recurse -Force (Join-Path $InstallDir ".venv") -ErrorAction SilentlyContinue
  Remove-Item -Force (Join-Path $InstallDir "license.key") -ErrorAction SilentlyContinue
  Write-Host "已删除运行环境与 license（数据/报告保留在 $InstallDir\work 与 data）" -ForegroundColor Green
}
