# JEV-EVALCOV-20261105: 回填覆盖率日检包装（计划任务每日 07:40；
# 注册命令见 docs/月度评测与覆盖率告警_2026-11-05.md，未注册，需老田批准）。
# 只做三件事：注入 env、跑 jev_coverage_guard.py、把 stdout 的一行 JSON 追加到
# logs/coverage_daily.jsonl（趋势统计用）。退出码透传：0 正常 / 1 告警 / 2 持续欠回填。
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
if (-not $here) { $here = 'C:\aios\jev-eval' }
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = '1'

$logDir = Join-Path $here 'logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$log = Join-Path $logDir 'jev_coverage_task.log'
function W([string]$m) {
    ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}

try {
    $envPs1 = Join-Path $here 'jev-gate.env.ps1'
    if (Test-Path -LiteralPath $envPs1) { . $envPs1 }

    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }

    $jsonl = Join-Path $logDir 'coverage_daily.jsonl'
    & $py -X utf8 (Join-Path $here 'jev_coverage_guard.py') `
        1>> $jsonl `
        2>> (Join-Path $logDir 'coverage_daily_err.log')
    $rc = $LASTEXITCODE
    $last = Get-Content -LiteralPath $jsonl -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
    W ('coverage exit=' + $rc + ' json=' + $last)
    exit $rc
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
