# deploy_deadline_watch.ps1 - DeadlineWatch 部署脚本（K-ORG-197 轨道B/W33）
# 幂等部署流程：
#   1. 语法与 BOM 自检：本脚本与 news_collector\deadline_watch_task.ps1
#      必须恰好一个 UTF-8 BOM 头（无 BOM 或双 BOM 均判失败），并用
#      Language.Parser 做语法校验；自检失败立即退出（fail-visible）。
#   2. 拷贝四个文件到 C:\aios 对应目录（目标目录不存在则递归创建，
#      目标已存在则覆盖；源文件缺失即报错退出）：
#        portal\push_matrix.py / portal\push_matrix.json  -> C:\aios\portal\
#        news_collector\deadline_watchdog.py
#        news_collector\deadline_watch_task.ps1           -> C:\aios\news_collector\
#   3. 注册 Windows 计划任务 DeadlineWatch：每日 06:05 / 08:45 / 16:45 /
#      20:45 / 22:05 五个时点（对应各窗口 deadline+grace），执行
#      powershell.exe -NoProfile -ExecutionPolicy Bypass -File
#      C:\aios\news_collector\deadline_watch_task.ps1
#      （包装器内部再跑 python -X utf8 deadline_watchdog.py；计划任务
#      无法直接以 python 执行 .ps1，故经包装器中转，语义一致。）
# 幂等性：注册前先 Unregister-ScheduledTask（不存在不报错），再 Register。
#
# 权限要求（重要）：真实注册计划任务需要管理员权限（UAC 提权）——请右键
# "以管理员身份运行" PowerShell 后执行本脚本；非管理员时文件拷贝仍会完成，
# 到注册步骤以退出码 4 结束并给出提示。只想自检语法/BOM 可加 -CheckOnly
# （无需提权，不部署不注册）。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\deploy_deadline_watch.ps1 [-CheckOnly]

param([switch]$CheckOnly)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$srcTask = Join-Path $repoRoot "news_collector\deadline_watch_task.ps1"
$dstPortal = "C:\aios\portal"
$dstNews = "C:\aios\news_collector"
$taskName = "DeadlineWatch"
$taskPs1 = Join-Path $dstNews "deadline_watch_task.ps1"


function Assert-SingleBom([string]$Path) {
    $b = [System.IO.File]::ReadAllBytes($Path)
    $one = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    $two = ($b.Length -ge 6 -and $b[3] -eq 0xEF -and $b[4] -eq 0xBB -and $b[5] -eq 0xBF)
    if (-not $one -or $two) {
        throw "BOM 自检失败（需恰好一个 UTF-8 BOM 头）: $Path"
    }
    Write-Host "[ok] BOM 自检通过: $Path"
}


function Assert-PsSyntax([string]$Path) {
    $tokens = $null
    $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$tokens, [ref]$errs)
    if ($errs -and $errs.Count -gt 0) {
        throw ("语法自检失败: {0} -> {1}" -f $Path, $errs[0].Message)
    }
    Write-Host "[ok] 语法自检通过: $Path"
}


function Copy-RepoFile([string]$Src, [string]$Dst) {
    if (-not (Test-Path -LiteralPath $Src)) { throw "源文件缺失: $Src" }
    # C:\aios 为主仓 junction：src 与 dst 同一实体文件时跳过拷贝（部署即主树）
    $srcFull = (Resolve-Path -LiteralPath $Src).Path
    if ((Test-Path -LiteralPath $Dst) -and ((Resolve-Path -LiteralPath $Dst).Path -ieq $srcFull)) {
        Write-Host "[skip] 同仓同文件（junction 部署即主树）: $Dst"
        return
    }
    $dir = Split-Path -Parent $Dst
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Copy-Item -LiteralPath $Src -Destination $Dst -Force
    Write-Host "[ok] 已部署: $Dst"
}


# ---- 1. 自检（无论是否 -CheckOnly 都执行）----
Assert-SingleBom $PSCommandPath
Assert-PsSyntax $PSCommandPath
Assert-SingleBom $srcTask
Assert-PsSyntax $srcTask

if ($CheckOnly) {
    Write-Host "[CheckOnly] 自检完成，未部署未注册。"
    exit 0
}

# ---- 2. 部署文件（幂等：目录 mkdir -p 语义，文件存在则覆盖）----
Copy-RepoFile (Join-Path $repoRoot "portal\push_matrix.py") (Join-Path $dstPortal "push_matrix.py")
Copy-RepoFile (Join-Path $repoRoot "portal\push_matrix.json") (Join-Path $dstPortal "push_matrix.json")
Copy-RepoFile (Join-Path $repoRoot "news_collector\deadline_watchdog.py") (Join-Path $dstNews "deadline_watchdog.py")
Copy-RepoFile $srcTask $taskPs1

# 部署后对落地副本再校验一次（防拷贝环节引入坏编码）
Assert-SingleBom $taskPs1
Assert-PsSyntax $taskPs1

# ---- 3. 注册计划任务（需要 UAC 管理员提权）----
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[需提权] 注册计划任务 $taskName 需要管理员权限（UAC）。"
    Write-Host "  文件已部署完成；请以管理员身份打开 PowerShell 后重新运行本脚本完成注册。"
    exit 4
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $taskPs1)

# 五个触发时点：各窗口 deadline+grace（us 冬令时口径由矩阵解析器自适配）
$triggerTimes = @("06:05", "08:45", "16:45", "20:45", "22:05")
$triggers = foreach ($t in $triggerTimes) {
    New-ScheduledTaskTrigger -Daily -At $t
}

# 幂等注册：先注销同名任务（不存在不报错），再注册
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Description "K-ORG-197 市场推送矩阵数据到点升级器（每日五时点巡检，退出码 2=有 pending）" | Out-Null

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { throw "计划任务注册后验证失败: $taskName" }
Write-Host "[ok] 计划任务 $taskName 已注册：每日 $($triggerTimes -join ' / ') 运行 $taskPs1"
exit 0
