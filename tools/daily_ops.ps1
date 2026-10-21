#requires -Version 5.1
<#
.SYNOPSIS
    每日运维总览：把「治理健康卡」「备份校验」「deliverables 双根一致性」串成一条只读命令。

.DESCRIPTION
    依次运行仓库里的三个只读 Python 工具，解析它们的 JSON，最后给出一段人读摘要
    和一句话建议。脚本本身不启动/停止服务、不连数据库、不注册计划任务、不访问网络。
    默认不写任何文件；只有显式传 -Log 时才向工作区根追加一行摘要。

    退出码语义（fail-closed）：
      0 = 绿或黄（三个工具都 exit 0；文档漂移只算黄，不改变退出码）
      1 = 红或无法判定（任一工具 exit 1，或输出无法解析、自相矛盾、工具缺失）
      2 = 脚本自身错误，或任一工具 exit 2 / Python 解释器不可用

.PARAMETER Log
    可选。追加一行摘要的日志文件路径。为遵守本机沙箱约束，只允许写工作区根
    （仓库的父目录，例如 C:\项目）下的文件，不接受仓库子目录；默认不写日志。
    日志以 UTF-8（无 BOM）追加，写失败只告警，不改变健康结论的退出码。

.PARAMETER Quick
    备份校验只对最大的 1 个文件计算 sha256（--sample 1）。默认全量计算。

.PARAMETER Json
    stdout 输出合并后的单个 JSON 对象；人读摘要改走 stderr，保证 stdout 可管道解析。
    顶层固定含 health_card / backup_verify / parity 三个原始 JSON 段（跳过的工具为 null）。

.PARAMETER SkipParity
    跳过 deliverables 双根一致性检查（第三个工具）。仅用于调试/隔离故障：
    跳过后该块不运行、不判定、不影响退出码，人读摘要里该块整段消失，-Json 中
    parity 为 null。默认必须运行——长期跳过等于让文档漂移重新无人看守。

.PARAMETER ParityA
    deliverables 双根比较的 A 根；默认 <工作区根>\deliverables，通常是
    C:\项目\deliverables。显式指向不存在/非目录的路径时，工具自身 exit 2，
    本脚本按「无法判定」计入无法判定项并整体 exit 2。

.PARAMETER ParityB
    deliverables 双根比较的 B 根；默认 <仓库根>\deliverables。语义同 -ParityA。

.PARAMETER Repo
    可选。覆盖仓库根；默认由 $PSScriptRoot 推导（tools 的父目录）。
    备份仓默认为 <仓库父目录>\_vault。主要用于验收/演练时指向无效路径。

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\daily_ops.ps1

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\daily_ops.ps1 -Quick -Json

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\daily_ops.ps1 -SkipParity

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\daily_ops.ps1 -ParityA D:\no-such-dir

.NOTES
    PowerShell 5.1 兼容：不使用 ??、?.、三元运算符、-Parallel。
    已知局限：
      1. Windows PowerShell 5.1 的 ConvertFrom-Json 走 JavaScriptSerializer，
         单段 JSON 约 2 MiB 上限，且嵌套深度上限约 100 层；本脚本默认只校验
         最新日期目录，实测两段 JSON 均在 10 KiB 以内，够用。若未来扩到 --all
         且清单极大，解析可能失败，此时按「无法判定」判红，不会当成功。
      2. 默认全量 sha256，备份大时可能耗时数十秒到数分钟；赶时间用 -Quick。
      3. 脚本不解释业务原因，只做汇总；红项详情以三个工具的原始 JSON 为准。
      4. 文档一致性工具会输出全部同名对明细（默认 48 对约 45 KiB），仍在 5.1
         ConvertFrom-Json 的 2 MiB 上限内；同名对极多导致解析失败时按「无法判定」
         判红，不会当一致。交集为 0 对（--a/--b 可能指错但目录存在）也按
         「无法判定」处理——工具本身只告警不退非零，汇总侧必须看交集数。
#>
[CmdletBinding()]
param(
    [string]$Log,
    [switch]$Quick,
    [switch]$Json,
    [switch]$SkipParity,
    [string]$Repo,
    [string]$ParityA,
    [string]$ParityB
)

$ErrorActionPreference = 'Stop'

# 兜底：脚本自身未预期异常一律 exit 2，绝不安静地退成 0/1。
trap {
    [Console]::Error.WriteLine(("[内部错误] 脚本自身异常：{0}" -f $_.Exception.Message))
    exit 2
}

# ---------------------------------------------------------------------------
# 基础路径与运行环境
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    [Console]::Error.WriteLine('[错误] 无法确定脚本目录（$PSScriptRoot 为空），请用 -File 方式运行。')
    exit 2
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot
if ([string]::IsNullOrWhiteSpace($workspaceRoot)) {
    $workspaceRoot = $repoRoot
}

if (-not [string]::IsNullOrWhiteSpace($Repo)) {
    try {
        $repoRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Repo)
        $repoRoot = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\')
    } catch {
        [Console]::Error.WriteLine(("-Repo 路径无法解析：{0}" -f $_.Exception.Message))
        exit 2
    }
}
$vaultRoot = Join-Path (Split-Path -Parent $repoRoot) '_vault'

# deliverables 双根：A 默认 <工作区根>\deliverables，B 默认 <仓库根>\deliverables。
# 显式传入时解析成绝对路径；允许不存在——交给工具 exit 2，本脚本按「无法判定」处理。
$parityRootA = Join-Path $workspaceRoot 'deliverables'
$parityRootB = Join-Path $repoRoot 'deliverables'
try {
    if (-not [string]::IsNullOrWhiteSpace($ParityA)) {
        $parityRootA = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ParityA)
    }
    if (-not [string]::IsNullOrWhiteSpace($ParityB)) {
        $parityRootB = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ParityB)
    }
} catch {
    [Console]::Error.WriteLine(("-ParityA/-ParityB 路径无法解析：{0}" -f $_.Exception.Message))
    exit 2
}

# Python 解释器：优先固定路径，其次 PATH 中的 python.exe / python。
$python = $null
$preferredPython = 'C:\Program Files\Python311\python.exe'
if (Test-Path -LiteralPath $preferredPython -PathType Leaf) {
    $python = $preferredPython
} else {
    $cmd = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $cmd) {
        $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if ($null -ne $cmd) {
        $python = $cmd.Source
    }
}

if ([string]::IsNullOrWhiteSpace($python)) {
    $msg = "找不到 Python 解释器（优先 $preferredPython，其次 PATH 中的 python）。"
    if ($Json) {
        $fatal = [ordered]@{
            schema       = 'daily_ops/v1'
            generated_at = (Get-Date).ToString('o')
            status       = 'error'
            exit_code    = 2
            error        = $msg
            next_step    = '先装好 Python 3.11 或把它加入 PATH，再重跑本命令。'
        }
        Write-Output ($fatal | ConvertTo-Json -Depth 5)
    } else {
        [Console]::Error.WriteLine("[错误] $msg")
    }
    exit 2
}

# 让 5.1 按 UTF-8 解码 Python 的 stdout（两个工具都强制 UTF-8 输出）。
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {
    # 无控制台（如某些计划任务宿主）时忽略，不影响管道捕获。
}

# -Log 只允许工作区根，避免往仓库子目录写文件。
$logFullPath = $null
if (-not [string]::IsNullOrWhiteSpace($Log)) {
    try {
        $resolvedLog = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Log)
        $logFullPath = [System.IO.Path]::GetFullPath($resolvedLog)
        $logDir = [System.IO.Path]::GetDirectoryName($logFullPath)
        $workspaceFull = [System.IO.Path]::GetFullPath($workspaceRoot).TrimEnd('\')
        if ($logDir.TrimEnd('\') -ne $workspaceFull) {
            throw ("-Log 只允许写工作区根 {0}，收到：{1}" -f $workspaceRoot, $Log)
        }
    } catch {
        [Console]::Error.WriteLine("[错误] $($_.Exception.Message)")
        exit 2
    }
}

# ---------------------------------------------------------------------------
# 工具调用与 JSON 解析
# ---------------------------------------------------------------------------
function Get-JsonField {
    param(
        [Parameter(Mandatory = $false)] $Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Invoke-PythonTool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ToolPath,
        [Parameter(Mandatory = $true)][string[]]$ToolArguments
    )

    $result = [ordered]@{
        name        = $Name
        path        = $ToolPath
        exit_code   = $null
        output      = ''
        json        = $null
        parse_error = $null
        spawn_error = $null
    }

    if (-not (Test-Path -LiteralPath $ToolPath -PathType Leaf)) {
        $result.spawn_error = "工具不存在：$ToolPath"
        return [pscustomobject]$result
    }

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # stderr 丢弃：stdout 必须是纯 JSON；工具自身错误用退出码判定。
        $lines = & $python $ToolPath @ToolArguments 2>$null
        $code = $LASTEXITCODE
    } catch {
        $result.spawn_error = $_.Exception.Message
        return [pscustomobject]$result
    } finally {
        $ErrorActionPreference = $oldEap
    }

    if ($null -eq $code) {
        $result.spawn_error = '未取得工具退出码'
        return [pscustomobject]$result
    }
    $result.exit_code = [int]$code

    if ($null -eq $lines) { $lines = @() }
    $text = (@($lines) | ForEach-Object { [string]$_ }) -join "`n"
    $result.output = $text

    if ([string]::IsNullOrWhiteSpace($text)) {
        $result.parse_error = 'stdout 为空'
        return [pscustomobject]$result
    }

    try {
        $result.json = $text | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $result.parse_error = $_.Exception.Message
    }
    return [pscustomobject]$result
}

function Get-OutputPreview {
    param([string]$Text, [int]$Max = 160)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '(空)' }
    $oneLine = ($Text -replace '\s+', ' ').Trim()
    if ($oneLine.Length -le $Max) { return $oneLine }
    return $oneLine.Substring(0, $Max) + '...'
}

# ---------------------------------------------------------------------------
# 运行三个工具（先健康卡，后备份校验，最后 deliverables 双根一致性）
# ---------------------------------------------------------------------------
$healthScript = Join-Path $PSScriptRoot 'gen_health_card.py'
$backupScript = Join-Path $PSScriptRoot 'verify_backups.py'
$parityScript = Join-Path $PSScriptRoot 'check_deliverables_parity.py'

$healthArgs = @('--repo', $repoRoot)
$backupArgs = @('--json', '--no-progress', '--root', $vaultRoot)
if ($Quick) {
    $backupArgs += @('--sample', '1')
}
$parityArgs = @('--json', '--max-report', '20', '--a', $parityRootA, '--b', $parityRootB)

$health = Invoke-PythonTool -Name 'health_card' -ToolPath $healthScript -ToolArguments $healthArgs
$backup = Invoke-PythonTool -Name 'backup_verify' -ToolPath $backupScript -ToolArguments $backupArgs

# -SkipParity 时保留一个「未运行」占位对象，下游取值路径不变。
$parity = [pscustomobject]@{
    name        = 'deliverables_parity'
    path        = $parityScript
    exit_code   = $null
    output      = ''
    json        = $null
    parse_error = $null
    spawn_error = $null
}
if (-not $SkipParity) {
    $parity = Invoke-PythonTool -Name 'deliverables_parity' -ToolPath $parityScript -ToolArguments $parityArgs
}

$unable = New-Object System.Collections.ArrayList
$toolError = $false

# --- 健康卡 ---
$healthStatus = ''
$healthReasons = @()
$healthUsable = $false

if ($null -ne $health.exit_code -and $health.exit_code -eq 2) {
    $toolError = $true
    [void]$unable.Add('健康卡工具自身错误（exit 2）')
} elseif ($null -ne $health.spawn_error) {
    [void]$unable.Add(("健康卡无法执行：{0}" -f $health.spawn_error))
} elseif ($null -ne $health.parse_error) {
    [void]$unable.Add(("健康卡输出无法解析：{0}；片段：{1}" -f $health.parse_error, (Get-OutputPreview $health.output)))
} elseif ($null -eq $health.json) {
    [void]$unable.Add('健康卡输出无法解析：JSON 为空')
} else {
    $rawStatus = Get-JsonField $health.json 'status'
    $healthStatus = ([string]$rawStatus).ToLowerInvariant()
    $rawReasons = Get-JsonField $health.json 'reasons'
    if ($null -ne $rawReasons) {
        $healthReasons = @($rawReasons | ForEach-Object { [string]$_ })
    }

    if (@('green', 'yellow', 'red') -notcontains $healthStatus) {
        [void]$unable.Add(("健康卡 status 不可识别：'{0}'" -f [string]$rawStatus))
    } else {
        $healthUsable = $true
        $declared = Get-JsonField $health.json 'exit_code'
        if ($null -ne $declared -and [int]$declared -ne [int]$health.exit_code) {
            [void]$unable.Add(("健康卡退出码 {0} 与 JSON exit_code {1} 不一致" -f $health.exit_code, $declared))
            $healthUsable = $false
        } elseif ($health.exit_code -eq 0 -and $healthStatus -eq 'red') {
            [void]$unable.Add('健康卡 exit 0 但 status=red，自相矛盾')
            $healthUsable = $false
        } elseif ($health.exit_code -eq 1 -and $healthStatus -ne 'red') {
            [void]$unable.Add(("健康卡 exit 1 但 status={0}，自相矛盾" -f $healthStatus))
            $healthUsable = $false
        } elseif ($health.exit_code -notin @(0, 1)) {
            [void]$unable.Add(("健康卡退出码 {0} 不在契约内（0/1/2）" -f $health.exit_code))
            $healthUsable = $false
        }
    }
}

# --- 备份校验 ---
$backupResult = ''
$backupSummary = $null
$backupProblems = @()
$backupFreshness = $null
$backupUsable = $false

if ($null -ne $backup.exit_code -and $backup.exit_code -eq 2) {
    $toolError = $true
    [void]$unable.Add('备份校验工具自身错误（exit 2）')
} elseif ($null -ne $backup.spawn_error) {
    [void]$unable.Add(("备份校验无法执行：{0}" -f $backup.spawn_error))
} elseif ($null -ne $backup.parse_error) {
    [void]$unable.Add(("备份校验输出无法解析：{0}；片段：{1}" -f $backup.parse_error, (Get-OutputPreview $backup.output)))
} elseif ($null -eq $backup.json) {
    [void]$unable.Add('备份校验输出无法解析：JSON 为空')
} else {
    $rawResult = Get-JsonField $backup.json 'result'
    $backupResult = ([string]$rawResult).ToLowerInvariant()
    $backupSummary = Get-JsonField $backup.json 'summary'
    $backupFreshness = Get-JsonField $backup.json 'freshness'
    $rawProblems = Get-JsonField $backup.json 'problems'
    if ($null -ne $rawProblems) {
        $backupProblems = @($rawProblems | ForEach-Object { [string]$_ })
    }

    if (@('ok', 'failed') -notcontains $backupResult) {
        [void]$unable.Add(("备份校验 result 不可识别：'{0}'" -f [string]$rawResult))
    } elseif ($null -eq $backupSummary) {
        [void]$unable.Add('备份校验 JSON 缺少 summary 字段')
    } elseif ($null -eq (Get-JsonField $backupSummary 'files')) {
        [void]$unable.Add('备份校验 JSON summary 缺少 files 字段')
    } else {
        $backupUsable = $true
        $declared = Get-JsonField $backup.json 'exit_code'
        if ($null -ne $declared -and [int]$declared -ne [int]$backup.exit_code) {
            [void]$unable.Add(("备份校验退出码 {0} 与 JSON exit_code {1} 不一致" -f $backup.exit_code, $declared))
            $backupUsable = $false
        } elseif ($backup.exit_code -eq 0 -and $backupResult -ne 'ok') {
            [void]$unable.Add(("备份校验 exit 0 但 result={0}，自相矛盾" -f $backupResult))
            $backupUsable = $false
        } elseif ($backup.exit_code -eq 1 -and $backupResult -ne 'failed') {
            [void]$unable.Add(("备份校验 exit 1 但 result={0}，自相矛盾" -f $backupResult))
            $backupUsable = $false
        } elseif ($backup.exit_code -notin @(0, 1)) {
            [void]$unable.Add(("备份校验退出码 {0} 不在契约内（0/1/2）" -f $backup.exit_code))
            $backupUsable = $false
        }
    }
}

# --- 文档一致性（deliverables 双根） ---
$parityVerdict = ''
$parityCounts = $null
$parityIntersection = 0
$paritySame = 0
$parityDiverged = 0
$paritySizeOnly = 0
$parityError = 0
$parityBadPairs = @()
$parityUsable = $false
$parityDrifted = $false
$parityUnableNote = ''
$paritySkipped = [bool]$SkipParity

if ($paritySkipped) {
    # 显式跳过：不运行、不判定、不计入无法判定、不影响退出码。
} elseif ($null -ne $parity.exit_code -and $parity.exit_code -eq 2) {
    $toolError = $true
    [void]$unable.Add('文档一致性工具自身错误（exit 2：根路径不存在/不是目录），无法判定')
} elseif ($null -ne $parity.spawn_error) {
    [void]$unable.Add(("文档一致性无法执行：{0}" -f $parity.spawn_error))
} elseif ($null -ne $parity.parse_error) {
    [void]$unable.Add(("文档一致性输出无法解析：{0}；片段：{1}" -f $parity.parse_error, (Get-OutputPreview $parity.output)))
} elseif ($null -eq $parity.json) {
    [void]$unable.Add('文档一致性输出无法解析：JSON 为空')
} else {
    $rawVerdict = Get-JsonField $parity.json 'verdict'
    $parityVerdict = ([string]$rawVerdict).ToLowerInvariant()
    $parityCounts = Get-JsonField $parity.json 'counts'

    if (@('consistent', 'drifted') -notcontains $parityVerdict) {
        [void]$unable.Add(("文档一致性 verdict 不可识别：'{0}'" -f [string]$rawVerdict))
    } elseif ($null -eq $parityCounts) {
        [void]$unable.Add('文档一致性 JSON 缺少 counts 字段')
    } elseif ($null -eq (Get-JsonField $parityCounts 'intersection')) {
        [void]$unable.Add('文档一致性 JSON counts 缺少 intersection 字段')
    } else {
        $parityUsable = $true
        $declared = Get-JsonField $parity.json 'exit_code'
        if ($null -ne $declared -and [int]$declared -ne [int]$parity.exit_code) {
            [void]$unable.Add(("文档一致性退出码 {0} 与 JSON exit_code {1} 不一致" -f $parity.exit_code, $declared))
            $parityUsable = $false
        } elseif ($parity.exit_code -eq 0 -and $parityVerdict -ne 'consistent') {
            [void]$unable.Add(("文档一致性 exit 0 但 verdict={0}，自相矛盾" -f $parityVerdict))
            $parityUsable = $false
        } elseif ($parity.exit_code -eq 1 -and $parityVerdict -ne 'drifted') {
            [void]$unable.Add(("文档一致性 exit 1 但 verdict={0}，自相矛盾" -f $parityVerdict))
            $parityUsable = $false
        } elseif ($parity.exit_code -notin @(0, 1)) {
            [void]$unable.Add(("文档一致性退出码 {0} 不在契约内（0/1/2）" -f $parity.exit_code))
            $parityUsable = $false
        } else {
            $parityIntersection = [int](Get-JsonField $parityCounts 'intersection')
            $paritySame = [int](Get-JsonField $parityCounts 'same')
            $parityDiverged = [int](Get-JsonField $parityCounts 'diverged')
            $paritySizeOnly = [int](Get-JsonField $parityCounts 'size_only')
            $parityError = [int](Get-JsonField $parityCounts 'error')

            if ($parityIntersection -le 0) {
                # 工具对空交集只告警、不退非零；汇总侧必须当成「无法判定」，否则
                # --a/--b 指到「存在但指错」的目录会被误读成一致。
                $parityUnableNote = 'A/B 交集为 0 对，--a/--b 可能指错目录'
                [void]$unable.Add('文档一致性无法判定：A/B 交集为 0 对（--a/--b 可能指错目录）')
                $parityUsable = $false
            } else {
                $parityDrifted = ($parityVerdict -eq 'drifted')
                if ($parityDrifted) {
                    $rawPairs = Get-JsonField $parity.json 'pairs'
                    if ($null -ne $rawPairs) {
                        foreach ($pair in @($rawPairs)) {
                            $pairStatus = ([string](Get-JsonField $pair 'status')).ToLowerInvariant()
                            if ($pairStatus -ne 'same') {
                                $parityBadPairs += [pscustomobject]@{
                                    rel    = [string](Get-JsonField $pair 'rel')
                                    status = $pairStatus
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

# ---------------------------------------------------------------------------
# 汇总结论
# ---------------------------------------------------------------------------
$healthRed = ($healthUsable -and $healthStatus -eq 'red')
$healthYellow = ($healthUsable -and $healthStatus -eq 'yellow')
$backupRed = ($backupUsable -and $backupResult -eq 'failed')
# 文档漂移是黄不是红：需要人处理，但不影响系统运行，也不改变退出码。
$parityYellow = ($parityUsable -and $parityDrifted)
$overallRed = ($healthRed -or $backupRed -or ($unable.Count -gt 0))

if ($toolError) {
    $overallStatus = 'error'
    $overallLabel = '工具错误'
    $exitCode = 2
} elseif ($overallRed) {
    $overallStatus = 'red'
    if ($unable.Count -gt 0) {
        $overallLabel = '红（含无法判定）'
    } else {
        $overallLabel = '红'
    }
    $exitCode = 1
} elseif ($healthYellow -or $parityYellow) {
    $overallStatus = 'yellow'
    $overallLabel = '黄'
    $exitCode = 0
} else {
    $overallStatus = 'green'
    $overallLabel = '绿'
    $exitCode = 0
}

# 红项建议按实际红因给，避免「仓库路径错」也提示去查任务退出码。
$healthRedReasons = @($healthReasons | Where-Object { $_ -match '^\[red\]' })
$healthRedHint = '健康卡红项'
$hasTaskRed = $false
foreach ($redReason in $healthRedReasons) {
    if ($redReason -match '非零退出') { $hasTaskRed = $true }
}
if ($hasTaskRed) {
    $healthRedHint = '健康卡红项（重点：非零退出任务）'
} elseif ($healthRedReasons.Count -gt 0) {
    $firstRed = ($healthRedReasons[0] -replace '^\[red\]\s*', '')
    $firstRed = ($firstRed -split '[：:]')[0]
    if ($firstRed.Length -gt 36) { $firstRed = $firstRed.Substring(0, 36) + '...' }
    $healthRedHint = ("健康卡首个红项（{0}）" -f $firstRed)
}

if ($toolError) {
    $nextStep = '先修工具自身错误（exit 2），本次结果不能作为判断依据，修好后重跑。'
} elseif ($unable.Count -gt 0) {
    $nextStep = '先恢复可解析的工具输出（见“无法判定”项），在能判定前一律按红处理，不要放行。'
} elseif ($healthRed -and $backupRed) {
    $nextStep = ("先处理{0}，再修备份校验失败项，最后重跑本命令确认。" -f $healthRedHint)
} elseif ($healthRed) {
    $nextStep = ("先处理{0}，处理后重跑本命令确认。" -f $healthRedHint)
} elseif ($backupRed) {
    $nextStep = '先查备份校验失败项（缺失/大小/sha256/新鲜度），补齐后重跑本命令确认。'
} elseif ($parityYellow -and $healthYellow) {
    $nextStep = '当前无红项；先人工确认文档漂移以哪边为准并回填，再安排处理健康卡黄项，明日复跑观察。'
} elseif ($parityYellow) {
    $nextStep = '当前无红项；文档有漂移（黄，不影响系统运行）：人工确认以哪边为准并回填，再重跑本命令确认。'
} elseif ($healthYellow) {
    $nextStep = '当前无红项；安排处理健康卡黄项（如未提交变更），明日复跑观察。'
} else {
    $nextStep = '保持现状，明日复跑即可。'
}

# ---------------------------------------------------------------------------
# 人读摘要
# ---------------------------------------------------------------------------
$summaryLines = New-Object System.Collections.ArrayList
[void]$summaryLines.Add('===== 每日运维总览 =====')
[void]$summaryLines.Add(("时间     : {0}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')))
[void]$summaryLines.Add(("仓库     : {0}" -f $repoRoot))
[void]$summaryLines.Add(("备份仓   : {0}" -f $vaultRoot))
[void]$summaryLines.Add(("总体状态 : {0}（exit {1}）" -f $overallLabel, $exitCode))

# 健康卡块
if ($null -ne $health.exit_code -and $health.exit_code -eq 2) {
    [void]$summaryLines.Add('健康卡   : 工具自身错误（exit 2）')
} elseif ($null -ne $health.spawn_error) {
    [void]$summaryLines.Add(("健康卡   : 无法判定（{0}）" -f $health.spawn_error))
} elseif ($null -ne $health.parse_error) {
    [void]$summaryLines.Add(("健康卡   : 无法判定（输出不是合法 JSON：{0}）" -f (Get-OutputPreview $health.parse_error 80)))
} elseif (-not $healthUsable) {
    [void]$summaryLines.Add('健康卡   : 无法判定（JSON 结构不符合契约或退出码自相矛盾）')
} else {
    $healthColor = '绿'
    if ($healthStatus -eq 'yellow') { $healthColor = '黄' }
    if ($healthStatus -eq 'red') { $healthColor = '红' }
    $nonGreen = $healthReasons.Count
    [void]$summaryLines.Add(("健康卡   : {0} / 退出码 {1} / {2} 项非绿" -f $healthColor, $health.exit_code, $nonGreen))
    if ($nonGreen -gt 0) {
        foreach ($reason in $healthReasons) {
            [void]$summaryLines.Add(("  - {0}" -f $reason))
        }
    } elseif ($healthStatus -ne 'green') {
        [void]$summaryLines.Add('  - （工具未提供 reasons 明细）')
    }
}

# 备份块
if ($null -ne $backup.exit_code -and $backup.exit_code -eq 2) {
    [void]$summaryLines.Add('备份校验 : 工具自身错误（exit 2）')
} elseif ($null -ne $backup.spawn_error) {
    [void]$summaryLines.Add(("备份校验 : 无法判定（{0}）" -f $backup.spawn_error))
} elseif ($null -ne $backup.parse_error) {
    [void]$summaryLines.Add(("备份校验 : 无法判定（输出不是合法 JSON：{0}）" -f (Get-OutputPreview $backup.parse_error 80)))
} elseif (-not $backupUsable) {
    [void]$summaryLines.Add('备份校验 : 无法判定（JSON 结构不符合契约或退出码自相矛盾）')
} else {
    if ($backupResult -eq 'ok') {
        [void]$summaryLines.Add(("备份校验 : 通过 / 退出码 {0}" -f $backup.exit_code))
    } else {
        [void]$summaryLines.Add(("备份校验 : 有问题 / 退出码 {0}" -f $backup.exit_code))
    }

    $files = Get-JsonField $backupSummary 'files'
    $okCount = Get-JsonField $backupSummary 'ok'
    $mismatch = Get-JsonField $backupSummary 'mismatch'
    $missing = Get-JsonField $backupSummary 'missing'
    $unreadable = Get-JsonField $backupSummary 'unreadable'
    $unverified = Get-JsonField $backupSummary 'unverified'
    $orphans = Get-JsonField $backupSummary 'orphans'
    $parseErrors = Get-JsonField $backupSummary 'parse_errors'
    $elapsed = Get-JsonField $backupSummary 'elapsed_s'
    $complete = Get-JsonField $backupSummary 'complete'

    if ($null -ne $files) {
        [void]$summaryLines.Add(("  文件 {0}：ok {1}，mismatch {2}，missing {3}，unreadable {4}，unverified {5}" -f `
                    $files, $okCount, $mismatch, $missing, $unreadable, $unverified))
    }
    [void]$summaryLines.Add(("  孤儿 {0}；清单解析错误 {1}；耗时 {2}s；模式 {3}" -f `
                $orphans, $parseErrors, $elapsed, $(if ($complete -eq $true) { '全量 sha256' } else { '抽样 sha256' })))

    $freshStatus = Get-JsonField $backupFreshness 'status'
    $freshDate = Get-JsonField $backupFreshness 'newest_date'
    $freshAge = Get-JsonField $backupFreshness 'age_hours'
    if ($null -ne $freshStatus) {
        [void]$summaryLines.Add(("  最新备份 {0}，距参考时间 {1}h（{2}）" -f $freshDate, $freshAge, $freshStatus))
    }

    if ($backupProblems.Count -gt 0) {
        [void]$summaryLines.Add(("  问题 {0} 项：" -f $backupProblems.Count))
        $show = [Math]::Min($backupProblems.Count, 10)
        for ($i = 0; $i -lt $show; $i++) {
            [void]$summaryLines.Add(("    - {0}" -f $backupProblems[$i]))
        }
        if ($backupProblems.Count -gt $show) {
            [void]$summaryLines.Add(("    - ……其余 {0} 项省略，详见原始 JSON" -f ($backupProblems.Count - $show)))
        }
    }
}

# 文档一致性块（-SkipParity 时整段不出现）
if (-not $paritySkipped) {
    if ($null -ne $parity.exit_code -and $parity.exit_code -eq 2) {
        [void]$summaryLines.Add('文档一致性: 工具自身错误（exit 2），无法判定')
    } elseif ($null -ne $parity.spawn_error) {
        [void]$summaryLines.Add(("文档一致性: 无法判定（{0}）" -f $parity.spawn_error))
    } elseif ($null -ne $parity.parse_error) {
        [void]$summaryLines.Add(("文档一致性: 无法判定（输出不是合法 JSON：{0}）" -f (Get-OutputPreview $parity.parse_error 80)))
    } elseif (-not $parityUsable) {
        if ([string]::IsNullOrWhiteSpace($parityUnableNote)) {
            $parityUnableNote = 'JSON 结构不符合契约或退出码自相矛盾'
        }
        [void]$summaryLines.Add(("文档一致性: 无法判定（{0}）" -f $parityUnableNote))
    } elseif ($parityDrifted) {
        $badTotal = $parityDiverged + $paritySizeOnly + $parityError
        [void]$summaryLines.Add(("文档一致性: 漂移 {0} 对，详见 check_deliverables_parity.py" -f $badTotal))
        [void]$summaryLines.Add(("  交集 {0} 对：same {1}，diverged {2}，size_only {3}，error {4}" -f `
                    $parityIntersection, $paritySame, $parityDiverged, $paritySizeOnly, $parityError))
        if ($parityBadPairs.Count -gt 0) {
            $showPairs = [Math]::Min($parityBadPairs.Count, 5)
            for ($i = 0; $i -lt $showPairs; $i++) {
                [void]$summaryLines.Add(("    - [{0}] {1}" -f $parityBadPairs[$i].status, $parityBadPairs[$i].rel))
            }
            if ($parityBadPairs.Count -gt $showPairs) {
                [void]$summaryLines.Add(("    - ……其余 {0} 对省略，详见原始 JSON" -f ($parityBadPairs.Count - $showPairs)))
            }
        }
    } else {
        [void]$summaryLines.Add(("文档一致性: 一致 / {0} 对同名全一致" -f $paritySame))
        $aOnly = Get-JsonField $parityCounts 'a_only'
        $bOnly = Get-JsonField $parityCounts 'b_only'
        if ($null -ne $aOnly -or $null -ne $bOnly) {
            [void]$summaryLines.Add(("  A/B 独有 {0}/{1} 篇（只计数，不判漂移）" -f $aOnly, $bOnly))
        }
    }
}

if ($unable.Count -gt 0) {
    [void]$summaryLines.Add(("无法判定 : {0} 项" -f $unable.Count))
    foreach ($item in $unable) {
        [void]$summaryLines.Add(("  - {0}" -f $item))
    }
}

[void]$summaryLines.Add(("下一步   : {0}" -f $nextStep))

$summaryText = ($summaryLines -join [Environment]::NewLine)

# ---------------------------------------------------------------------------
# 输出：-Json 时 stdout 只放 JSON；人读摘要走 stderr
# ---------------------------------------------------------------------------
$payload = [ordered]@{
    schema       = 'daily_ops/v1'
    generated_at = (Get-Date).ToString('o')
    repo         = $repoRoot
    vault        = $vaultRoot
    quick        = [bool]$Quick
    skip_parity  = [bool]$paritySkipped
    parity_a     = $parityRootA
    parity_b     = $parityRootB
    status       = $overallStatus
    exit_code    = $exitCode
    summary      = [ordered]@{
        health = [ordered]@{
            status     = $healthStatus
            exit_code  = $health.exit_code
            usable     = $healthUsable
            red        = $healthRed
            yellow     = $healthYellow
            reason_count = $healthReasons.Count
        }
        backup = [ordered]@{
            result     = $backupResult
            exit_code  = $backup.exit_code
            usable     = $backupUsable
            red        = $backupRed
            files      = (Get-JsonField $backupSummary 'files')
            elapsed_s  = (Get-JsonField $backupSummary 'elapsed_s')
            complete   = (Get-JsonField $backupSummary 'complete')
        }
        parity = [ordered]@{
            skipped      = $paritySkipped
            verdict      = $parityVerdict
            exit_code    = $parity.exit_code
            usable       = $parityUsable
            drifted      = $parityDrifted
            yellow       = $parityYellow
            intersection = $parityIntersection
            same         = $paritySame
            diverged     = $parityDiverged
            size_only    = $paritySizeOnly
            error        = $parityError
        }
        unable     = @($unable)
    }
    health_card  = $health.json
    backup_verify = $backup.json
    parity       = $parity.json
    next_step    = $nextStep
}

if ($Json) {
    Write-Output ($payload | ConvertTo-Json -Depth 20)
    [Console]::Error.WriteLine($summaryText)
} else {
    Write-Output $summaryText
}

# ---------------------------------------------------------------------------
# 可选日志：只追加一行，失败不改变健康结论
# ---------------------------------------------------------------------------
if ($null -ne $logFullPath) {
    $logHealth = $healthStatus
    if ([string]::IsNullOrWhiteSpace($logHealth)) { $logHealth = 'unavailable' }
    $logBackup = $backupResult
    if ([string]::IsNullOrWhiteSpace($logBackup)) { $logBackup = 'unavailable' }
    if ($paritySkipped) {
        $logParity = 'skipped'
    } else {
        $logParity = $parityVerdict
        if ([string]::IsNullOrWhiteSpace($logParity)) { $logParity = 'unavailable' }
    }
    $logLine = ('{0} | status={1} | exit={2} | health={3} | backup={4} | parity={5} | unable={6} | repo={7} | next={8}' -f `
            (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'), $overallStatus, $exitCode, $logHealth, $logBackup, `
            $logParity, $unable.Count, $repoRoot, (($nextStep -replace '\s+', ' ').Trim()))
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::AppendAllText($logFullPath, $logLine + [Environment]::NewLine, $utf8NoBom)
    } catch {
        [Console]::Error.WriteLine(("[警告] 日志写入失败（不影响退出码）：{0}" -f $_.Exception.Message))
    }
}

exit $exitCode
