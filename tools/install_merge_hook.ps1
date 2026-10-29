# install_merge_hook.ps1 - 安装/卸载合并前质量门钩子（P13，2026-10-28）
#
# 作用：在 .git/hooks/pre-merge-commit 写入钩子，任何 git merge（含
# `python tools/devtask.py merge`）在生成 merge commit 前都会先跑
# `tools/merge_preflight.py`：
#   - 纯文档/配置变更 → 放行；
#   - 其他变更 → 跑 test_gate -Suite fast，新增失败即中止合并。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_merge_hook.ps1
#   powershell ... -File tools\install_merge_hook.ps1 -Uninstall
# 说明：钩子为本地安装物（不进 git）；重装/卸载均可重复执行。绕过方式：
#   设置 DEVTOOL_MERGE_GATE=0（回滚/紧急，须在回单注明"绕过质量闸"）。
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$gitdir = (& git -C $root rev-parse --git-common-dir).Trim()
if (-not $gitdir) { Write-Error '无法定位 git 目录'; exit 1 }
if (-not [System.IO.Path]::IsPathRooted($gitdir)) { $gitdir = Join-Path $root $gitdir }
$hooks = Join-Path $gitdir 'hooks'
if (-not (Test-Path -LiteralPath $hooks)) { New-Item -ItemType Directory -Path $hooks | Out-Null }
$hook = Join-Path $hooks 'pre-merge-commit'

if ($Uninstall) {
    if (Test-Path -LiteralPath $hook) {
        Remove-Item -LiteralPath $hook -Force
        Write-Host "已卸载：$hook"
    } else {
        Write-Host "未安装：$hook"
    }
    exit 0
}

# 钩子脚本（sh；Git for Windows 自带 sh 执行）。消息保持 ASCII 避免控制台编码问题。
$content = @'
#!/bin/sh
# pre-merge-commit: quality gate (test_gate -Suite fast) before merge commit (P13, 2026-10-28)
# Bypass (rollback/emergency only): DEVTOOL_MERGE_GATE=0
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if ! command -v python >/dev/null 2>&1; then
  echo "[merge-gate] python not found; blocking merge (fail-closed)" >&2
  exit 1
fi
exec python "$ROOT/tools/merge_preflight.py"
'@
[System.IO.File]::WriteAllText($hook, $content.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "已安装合并质量门钩子：$hook"
Write-Host "验证：git merge 时将自动跑 tools\merge_preflight.py；纯文档单自动放行。"
