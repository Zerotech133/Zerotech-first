#requires -Version 5.1
<#
.SYNOPSIS
  安装/卸载本仓 post-merge 钩子：merge 后自动重烘独立 IDE bundle。

.DESCRIPTION
  - 目标：git 公共目录下的 hooks/post-merge（worktree 安全：用 git rev-parse
    --git-common-dir 解析，不写 worktree 私有 .git）；
  - 钩子内容只调用 ops/rebake_ide_bundle.ps1（部署副本，不弄脏主仓）；
  - 已有 post-merge 会先备份为 post-merge.bak-<时间戳>；
  - -Uninstall 时把当前钩子移出为 .bak（不删除，遵守回收站纪律）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\install_git_hooks.ps1
  powershell -ExecutionPolicy Bypass -File ops\install_git_hooks.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$Repo = "C:\项目\老田的AI助手",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$common = (& git -C $Repo rev-parse --git-common-dir 2>$null).Trim()
if (-not $common) { Write-Error "无法解析 git common dir（$Repo）"; exit 1 }
if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $Repo $common }
$hookDir = Join-Path $common "hooks"
New-Item -ItemType Directory -Force -Path $hookDir | Out-Null
$hook = Join-Path $hookDir "post-merge"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

if ($Uninstall) {
    if (Test-Path -LiteralPath $hook) {
        $bak = "$hook.bak-$stamp"
        Move-Item -LiteralPath $hook -Destination $bak -Force
        Write-Host "post-merge 已移出到 $bak（未删除）"
    } else {
        Write-Host "post-merge 不存在，无需卸载"
    }
    exit 0
}

if (Test-Path -LiteralPath $hook) {
    $bak = "$hook.bak-$stamp"
    Move-Item -LiteralPath $hook -Destination $bak -Force
    Write-Host "旧 post-merge 已备份：$bak"
}

$content = @'
#!/bin/sh
# post-merge: 独立 IDE bundle 自动重烘（写部署副本，不弄脏主仓工作树）
# 安装器：ops/install_git_hooks.ps1 · 卸载：同脚本 -Uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:/项目/老田的AI助手/ops/rebake_ide_bundle.ps1" >> "C:/Temp/legion_ide_rebake.log" 2>&1
exit 0
'@
# sh 钩子必须 LF（CRLF 会触发 bad interpreter）
$content = $content -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($hook, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "已安装 post-merge：$hook"
exit 0
