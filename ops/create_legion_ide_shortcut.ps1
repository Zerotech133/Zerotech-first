#requires -Version 5.1
<#
.SYNOPSIS
  创建「军团独立IDE」桌面快捷方式（一键打开 Edge App 模式窗口）。

.DESCRIPTION
  快捷方式调用 ops/start_legion_ide.ps1 -Action app：
  服务未跑则先拉起 8103，再用 Edge --app 打开独立窗口。
  幂等：重复执行覆盖同名 .lnk。
#>
[CmdletBinding()]
param(
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Name = "军团独立IDE"
)

$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop ($Name + ".lnk")
$target = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $target)) { $target = "powershell.exe" }
$args = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
        (Join-Path $Repo "ops\start_legion_ide.ps1") + '" -Action app'

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $target
$sc.Arguments = $args
$sc.WorkingDirectory = $Repo
$sc.Description = "军团 Agent IDE 独立窗口（127.0.0.1:8103，缺失则自动拉起）"
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (Test-Path -LiteralPath $edge) { $sc.IconLocation = $edge + ",0" }
$sc.Save()
Write-Host "快捷方式已创建：$lnk"
exit 0
