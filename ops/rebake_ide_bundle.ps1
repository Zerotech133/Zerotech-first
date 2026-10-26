#requires -Version 5.1
<#
.SYNOPSIS
  重烘 Agent IDE bundle 到部署副本（不弄脏主仓工作树）。

.DESCRIPTION
  1) 在主仓运行 tools/build_agent_ide_bundle.py（读取 org.json/hr/prompts/
     .worktrees/tasks.json → portal/agent_ide_bundle.json）；
  2) 复制到部署副本 C:\项目\_vault\legion_ide\agent_ide_bundle.json（独立 IDE
     服务优先读取该副本，见 start_legion_ide.ps1）；
  3) `git checkout -- portal/agent_ide_bundle.json` 恢复仓内 tracked 文件，
     避免每次 merge 后工作树变脏。
  供 .git/hooks/post-merge 调用；也可手动执行。
  日志：C:\Temp\legion_ide_rebake.log。
#>
[CmdletBinding()]
param(
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$Python = "C:\项目\py312\python.exe",
    [string]$DeployDir = "C:\项目\_vault\legion_ide",
    [string]$LogFile = "C:\Temp\legion_ide_rebake.log"
)

function Write-RebakeLog([string]$Message) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $Message
    $dir = Split-Path -Parent $LogFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $line
}

$tool = Join-Path $Repo "tools\build_agent_ide_bundle.py"
$srcBundle = Join-Path $Repo "portal\agent_ide_bundle.json"
if (-not (Test-Path -LiteralPath $tool)) { Write-RebakeLog "ERROR tool missing: $tool"; exit 1 }

Push-Location $Repo
try {
    $out = & $Python $tool 2>&1
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($rc -ne 0) { Write-RebakeLog "ERROR rebake failed rc=${rc}: $($out | Select-Object -Last 3)"; exit 1 }
if (-not (Test-Path -LiteralPath $srcBundle)) { Write-RebakeLog "ERROR bundle not produced: $srcBundle"; exit 1 }

New-Item -ItemType Directory -Force -Path $DeployDir | Out-Null
$dst = Join-Path $DeployDir "agent_ide_bundle.json"
Copy-Item -LiteralPath $srcBundle -Destination $dst -Force
Write-RebakeLog ("OK bundle -> " + $dst + " (" + (Get-Item $dst).Length + " bytes)")

# 恢复仓内 tracked bundle，保持工作树干净（部署副本已保留新数据）
& git -C $Repo checkout -- portal/agent_ide_bundle.json 2>&1 | Out-Null
Write-RebakeLog "OK restored tracked portal/agent_ide_bundle.json (deploy copy is authoritative)"
exit 0
