# VERSION: jev_drift_task v1 (2026-10-17, K-ORG-160 daily drift monitor wrapper)
$ErrorActionPreference = 'Stop'
# log under project review_data (S4U task verified writable, same as jev_backfill)
$log = Join-Path $PSScriptRoot 'review_data\jev_drift_task.log'
if (-not (Test-Path -LiteralPath (Split-Path $log))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
}
function W([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}
try {
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
    W 'P0 start'
    # python resolution (house rule, same as jev_backfill_task.ps1)
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    # single pass drift check (default args); stdout captured for exit code passthrough
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $PSScriptRoot 'jev_drift.py') 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P1 exit=' + $code + ' out=' + ($out -join ' | '))

    # 2026-10-26 K-ORG-20261026-JEVROLE: 夜间独占收工后，顺带生成 JEV 三席日报。
    # 刻意不阻塞、不改本任务退出码：日报生成失败只留痕（P3 行），不污染漂移监控结果。
    $rolesPy = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\jev_roles_daily.py'
    if (Test-Path -LiteralPath $rolesPy) {
        $rolesOut = & $py -X utf8 $rolesPy 2>&1
        W ('P3 jev_roles exit=' + $LASTEXITCODE + ' out=' + ($rolesOut -join ' | '))
    } else {
        W ('P3 jev_roles SKIP: not found ' + $rolesPy)
    }
    # log cap ~400KB: keep tail
    if ((Get-Item -LiteralPath $log).Length -gt 400KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
        W 'P2 log rotated'
    }
    # P4 (2026-10-26 K-DATA-20261026-JEVAUDIT): JEV audit ledger + T+1 posterior + calibration
    # (read-only news.db; writes only agent_platform/data/jev_audit)
    $auditPy = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\jev_audit.py'
    if (Test-Path -LiteralPath $auditPy) {
        $auditOut = & $py -X utf8 -B $auditPy --days 45 2>&1
        W ('P4 jev_audit exit=' + $LASTEXITCODE + ' out=' + (($auditOut | Select-Object -Last 3) -join ' | '))
    } else {
        W ('P4 jev_audit SKIP: not found ' + $auditPy)
    }
    # P5 (2026-10-30 JEVROTATE): jev-eval/logs 轮转（gate/card/review/qdbridge）。
    # jev_common 每条日志独立 open/append/close -> 外部重命名安全；保留 14 天，gzip 回收。
    # 失败不阻塞、不改本任务退出码（只留痕）。
    $rotPy = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\rotate_logs.py'
    if (Test-Path -LiteralPath $rotPy) {
        $rotOut = & $py -X utf8 -B $rotPy --execute --gzip --active-guard-min 1 --keep-days 14 2>&1
        W ('P5 rotate_logs exit=' + $LASTEXITCODE + ' out=' + (($rotOut | Select-Object -Last 2) -join ' | '))
    } else {
        W ('P5 rotate_logs SKIP: not found ' + $rotPy)
    }

    exit $code
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
