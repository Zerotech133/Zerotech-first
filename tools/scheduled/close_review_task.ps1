# close_review_task.ps1 - 收盘复盘每日入口（交易日 15:40）
#
# 时点：交易日 15:40。为什么是 15:40：
#   · 15:00 收盘，行情快照 15:05 之后才稳定（含收盘集合竞价结果）；
#   · 本平台已有的 houfeng_settle_daily 在 16:40，那条线跑的是侯风的口径，
#     与本链路的「盘前 15 只成绩」无关，不要合在一起（口径不同不能互相代言）；
#   · 15:40 出结果，16:00 前能转发，收盘会还能用上。
#
# 做什么：读当天开盘实际用的选票篮子 → 取当日收盘 → 算 1 日成绩与同池超额
#         → 落 md/json → 写累计台账（同日覆盖、跨日留痕；40 日评价靠台账，不靠单日报告）。
#
# 不做的事：不改任何选股口径、不回写选票产物、不注册自身。
#
# 两个码，别混（与 preopen_task.ps1 同源）：
#   业务码（close_review.py 返回）：0 出了复盘 / 2 阻断（无篮子、行情日期不符）/ 3 脚本失败
#   本脚本退出码（给 Task Scheduler 看）：0 = 已跑完（业务 0/1）/ 2 = 阻断 / 3 = 崩溃
#   LastTaskResult 问的是「跑完了吗」，不是「结论好不好」。
param(
    [switch]$WhatIf,
    [string]$Date = '',
    [string]$Log = 'C:\Temp\close_review_task.log'
)

# ── 启动落痕（P0 同款，2026-11-12）─────────────────────────────────────
# 理由与 preopen_task.ps1 相同：没有落痕时，「没启动」与「启动了但秒退」无法区分。
# 落痕路径不依赖后面任何推导，且是本脚本第一段可执行代码。
$LAUNCH = 'C:\Temp\close_review_launch.jsonl'
$T0 = Get-Date

function Write-Launch([string]$kind, [hashtable]$extra) {
    try {
        $o = [ordered]@{
            schema = 'launch/v1'
            kind   = $kind
            task   = 'close_review'
            ts     = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            day    = (Get-Date).ToString('yyyyMMdd')
            pid    = $PID
            host   = $env:COMPUTERNAME
            user   = $env:USERNAME
            psver  = $PSVersionTable.PSVersion.ToString()
            whatif = [bool]$WhatIf
            date   = $Date
            log    = $Log
        }
        if ($extra) { foreach ($k in $extra.Keys) { $o[$k] = $extra[$k] } }
        $enc = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::AppendAllText($LAUNCH, (($o | ConvertTo-Json -Compress) + "`n"), $enc)
    } catch {
        try { ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' WARN launch beacon failed: ' + $_.Exception.Message) | Out-File $Log -Append -Encoding utf8 } catch { }
    }
}

if ($WhatIf) { Write-Launch 'preview' } else { Write-Launch 'start' }

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'

$py = 'C:\Program Files\Python311\python.exe'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$entry = Join-Path $root 'tools\review\close_review.py'
$alert = Join-Path $root 'quant_backtest\alert_fail.py'

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

Write-Log "=== close_review start (Date=$Date) ==="

if (-not (Test-Path $entry)) {
    Write-Log "FATAL entry not found: $entry"
    if (Test-Path $alert) { & $py -X utf8 $alert 'close_review' 3 $Log | Out-Null }
    Write-Launch 'end' @{ rc = 3; ts_rc = 3; stage = 'entry_check'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 3
}

$argv = @('-X', 'utf8', $entry)
if ($Date) { $argv += @('--date', $Date) }

$out = & $py @argv 2>&1
$rc = $global:LASTEXITCODE

$out | Out-File $Log -Append -Encoding utf8
Write-Log "=== close_review end 业务rc=$rc ==="

if ($rc -eq 2 -or $rc -eq 3) {
    if (Test-Path $alert) {
        & $py -X utf8 $alert 'close_review' $rc $Log | Out-Null
    } else {
        Write-Log "alert_fail.py missing, skip notify"
    }
}

if ($rc -eq 0 -or $rc -eq 1) {
    Write-Launch 'end' @{ rc = $rc; ts_rc = 0; stage = 'done'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 0
}
Write-Launch 'end' @{ rc = $rc; ts_rc = $rc; stage = 'blocked'
                      dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
exit $rc
