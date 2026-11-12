# preopen_task.ps1 - 后峰盘前 Agent 每日入口（校对 → 选票 → 简报）
#
# 时点：交易日 08:10。为什么是 08:10：
#   · 04:26 有一次 tdx 全量同步（ts_daily / ts_adj_factor），到 08:10 早已落地；
#   · 09:15 集合竞价前要能转出去，08:10 留足 40 分钟余量（本流程实测 2~3 分钟）；
#   · 不等 08:30 之后，是因为竞价阶段行情接口会开始返回当日数据，
#     此时若重跑选票会混入未收盘的当日 bar —— 盘前必须只认上一个交易日。
#
# 不做的事（重要）：不注册自身、不改系统配置。注册计划任务需老田先批准
# （见 install_preopen_task.ps1 顶部的五要素说明）。
#
# 两个不同的码，别混：
#   业务码（run_preopen.py 返回，记在日志「业务rc=」行）：
#     0 全绿 / 1 有告警但报告已出 / 2 阻断未出报告 / 3 脚本失败
#     rc=2、3 发飞书告警；rc=1 只记日志 —— 告警疲劳比漏报更危险，告警项已写进报告。
#   本脚本退出码（给 Task Scheduler 看）：
#     0 = 已跑完（业务 0/1）/ 2 = 阻断 / 3 = 崩溃
#     理由见文件末尾注释：LastTaskResult 问的是「跑完了吗」，不是「结论好不好」。
param(
    [switch]$WhatIf,
    [switch]$SkipPick,
    [string]$Log = 'C:\Temp\preopen_task.log'
)

# ══ P0 启动落痕（2026-11-12）══════════════════════════════════════════
# 「任务是否启动」与「是否算票」必须能分开判。
# 判例（2026-11-12）：计划任务 08:10 触发，但日志文件的创建时间是 08:27:42 ——
# 08:10 那几次到底跑没跑，事后无从判断，因为日志只在业务跑起来之后才写第一行。
# 「没启动」和「启动了但没算票」在证据上完全一样，这比任何单点故障都难查。
#
# 所以本段是本脚本的第一段可执行代码，且落痕路径 **不依赖后面的任何推导**
# （$root / $entry 都可能算错，落痕绝不能用它们算出来的路径）。
# 落痕是 append-only JSONL，一行一次启动；字段 schema=launch/v1，kind 区分
# start / end / preview（preview = -WhatIf，不算真跑）。
$LAUNCH = 'C:\Temp\preopen_launch.jsonl'
$T0 = Get-Date

function Write-Launch([string]$kind, [hashtable]$extra) {
    try {
        $o = [ordered]@{
            schema    = 'launch/v1'
            kind      = $kind
            task      = 'preopen'
            ts        = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            day       = (Get-Date).ToString('yyyyMMdd')
            pid       = $PID
            host      = $env:COMPUTERNAME
            user      = $env:USERNAME
            psver     = $PSVersionTable.PSVersion.ToString()
            whatif    = [bool]$WhatIf
            skip_pick = [bool]$SkipPick
            log       = $Log
            psscript  = $PSCommandPath
        }
        if ($extra) { foreach ($k in $extra.Keys) { $o[$k] = $extra[$k] } }
        $enc = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::AppendAllText($LAUNCH, (($o | ConvertTo-Json -Compress) + "`n"), $enc)
    } catch {
        # 落痕失败绝不阻断业务，但要往日志塞一条 —— 否则「没痕迹」又被当成「没跑」
        try { ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' WARN launch beacon failed: ' + $_.Exception.Message) | Out-File $Log -Append -Encoding utf8 } catch { }
    }
}

# -WhatIf 不是一次真跑，落痕写成 preview，否则「今天启动过」会变成假阳性
if ($WhatIf) { Write-Launch 'preview' } else { Write-Launch 'start' }

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'

$py = 'C:\Program Files\Python311\python.exe'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$entry = Join-Path $root 'tools\preopen\run_preopen.py'
$alert = Join-Path $root 'quant_backtest\alert_fail.py'

# 时间戳按「写这一行的时候」取。
# 旧写法在顶部算一次 $stamp 全程复用，于是 start 和 end 两行时间戳完全相同 ——
# 正好把 P0 想分清的「几点启动 / 跑了几分钟」抹平了。这不是格式问题，是证据问题。
function Write-Log([string]$msg) {
    ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $msg) | Out-File $Log -Append -Encoding utf8
}

if ($WhatIf) {
    Write-Output "py    = $py"
    Write-Output "root  = $root"
    Write-Output "entry = $entry"
    Write-Output "exists= $(Test-Path $entry)"
    exit 0
}

Write-Log "=== preopen start (SkipPick=$SkipPick) ==="

if (-not (Test-Path $entry)) {
    Write-Log "FATAL entry not found: $entry"
    if (Test-Path $alert) {
        & $py -X utf8 $alert 'preopen' 3 $Log | Out-Null
    }
    # 启动过、没进业务就死了 —— 这条 end 记录是「无痕」与「有痕」的分界
    Write-Launch 'end' @{ rc = 3; ts_rc = 3; stage = 'entry_check'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 3
}

$argv = @('-X', 'utf8', $entry)
if ($SkipPick) { $argv += '--skip-pick' }

$out = & $py @argv 2>&1
$rc = $global:LASTEXITCODE

$out | Out-File $Log -Append -Encoding utf8
Write-Log "=== preopen end 业务rc=$rc ==="

if ($rc -eq 2 -or $rc -eq 3) {
    if (Test-Path $alert) {
        & $py -X utf8 $alert 'preopen' $rc $Log | Out-Null
    } else {
        Write-Log "alert_fail.py missing, skip notify"
    }
}

# ── 退出码映射（2026-11-12 修正）──────────────────────────────────────
# 计划任务的 LastTaskResult 回答的是「这个任务跑完了吗」，不是「业务判定是什么」。
# 旧写法 exit $rc 会把业务码 1（有告警但报告已出）报成任务失败，
# 于是「正常有告警」和「真失败」在任务计划程序里压成同一个信号 ——
# 这正是本平台一直在防的混淆（同族：数据未就绪被说成无事可做）。
# 业务判定看日志的「业务rc=」行，或 deliverables\preopen_run_latest.json 的 rc 字段。
#   0 = 已完成（业务 0/1 都算完成，报告已出）
#   2 = 阻断，未出报告
#   3 = 脚本崩溃
if ($rc -eq 0 -or $rc -eq 1) {
    Write-Launch 'end' @{ rc = $rc; ts_rc = 0; stage = 'done'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 0
}
Write-Launch 'end' @{ rc = $rc; ts_rc = $rc; stage = 'blocked'
                      dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
exit $rc
