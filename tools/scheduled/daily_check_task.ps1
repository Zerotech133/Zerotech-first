# daily_check_task.ps1 - 每日 05:30 数据检查 + 部门派单的任务入口
#
# 与 08:10 preopen_daily 的分工：
#   05:30 只做「检查 + 派单」，不出名单；08:10 才出名单。
#   中间 2h40m 是留给责任部门的处理窗口。
#
# 两个不同的码，别混：
#   业务判定（python 打印 BUSINESS_VERDICT=... 与台账 JSON）：
#     ok / warn / fail，另加 STATE=CHECKED / DATA_NOT_READY / CLEAN / BLOCKED
#   本脚本退出码（给 Task Scheduler 看）：
#     0 = 跑完了（业务判定无论好坏）/ 3 = 真失败（无法确定目标日、投递失败、脚本崩溃）
#   理由：LastTaskResult 问的是「跑完了吗」，不是「结论好不好」。
#
# 不做的事：不注册自身、不改系统配置。注册见 install_daily_check_task.ps1
# 顶部的五要素说明（系统级变更需老田批准）。
param(
    [switch]$WhatIf,
    [switch]$Dry,
    [string]$Date = '',
    [string]$Log = 'C:\Temp\daily_check_task.log'
)

$LAUNCH = 'C:\Temp\daily_check_launch.jsonl'
$T0 = Get-Date

function Write-Launch([string]$kind, [hashtable]$extra) {
    try {
        $o = [ordered]@{
            schema = 'launch/v1'; kind = $kind; task = 'daily_check_0530'
            ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            day = (Get-Date).ToString('yyyyMMdd')
            pid = $PID; host = $env:COMPUTERNAME; user = $env:USERNAME
            psver = $PSVersionTable.PSVersion.ToString()
            whatif = [bool]$WhatIf; dry = [bool]$Dry; date = $Date
            log = $Log; psscript = $PSCommandPath
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
$entry = Join-Path $root 'tools\preopen\daily_check_dispatch.py'

function Write-Log([string]$msg) {
    ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $msg) | Out-File $Log -Append -Encoding utf8
}

if ($WhatIf) {
    Write-Output "py    = $py"
    Write-Output "root  = $root"
    Write-Output "entry = $entry"
    Write-Output "exists= $(Test-Path $entry)"
    $dateDesc = '由交易日历推导（今天之前的最后一个开市日）'
    if ($Date) { $dateDesc = $Date }
    Write-Output "date  = $dateDesc"
    exit 0
}

Write-Log "=== daily_check_0530 start (Dry=$Dry Date=$Date) ==="

if (-not (Test-Path $entry)) {
    Write-Log "FATAL entry not found: $entry"
    Write-Launch 'end' @{ rc = 3; ts_rc = 3; stage = 'entry_check'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 3
}

$argv = @('-X', 'utf8', $entry)
if ($Dry) { $argv += '--dry' }
if ($Date) { $argv += @('--date', $Date) }

$out = & $py @argv 2>&1
$rc = $global:LASTEXITCODE
$out | Out-File $Log -Append -Encoding utf8
Write-Log "=== daily_check_0530 end 业务rc=$rc ==="

if ($rc -eq 0) {
    Write-Launch 'end' @{ rc = 0; ts_rc = 0; stage = 'done'
                          dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
    exit 0
}
Write-Launch 'end' @{ rc = $rc; ts_rc = $rc; stage = 'failed'
                      dur_sec = [int](((Get-Date) - $T0).TotalSeconds) }
exit $rc
