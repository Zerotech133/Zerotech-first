# JEV-EVALCOV-20261105: Jev 月度回归评测入口（计划任务每月 1 日 07:20；
# 注册命令见 docs/月度评测与覆盖率告警_2026-11-05.md，未注册，需老田批准）。
# 流程（三段串联，任一失败都继续，最后按优先级返回）：
#   1) jev_eval.py --repeats 3          正式评测（30 题 x3 遍，~3.6 万 token）
#   2) jev_eval_guard.py --cadence monthly   准确率/题型/超龄（月度阈值 35 天）
#   3) jev_coverage_guard.py            回填覆盖率 + 毕业 JSON 新鲜度
# 口径说明：旧 guard 硬编码 3 天超龄，月度跑成功后第 4 天起天天告警；
# 入口显式传 --cadence monthly，超龄阈值改 35 天（31 天月长 + 补跑余量）。
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
if (-not $here) { $here = 'C:\aios\jev-eval' }
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = '1'

$logDir = Join-Path $here 'logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$log = Join-Path $logDir 'jev_monthly_eval.log'
function W([string]$m) {
    ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}

try {
    W 'P0 start (cadence=monthly)'

    # 环境注入：JEV_BACKEND / JEV_OLLAMA_URL / NO_PROXY 等；缺失不致命（沿用继承环境）
    $envPs1 = Join-Path $here 'jev-gate.env.ps1'
    if (Test-Path -LiteralPath $envPs1) {
        . $envPs1
        W 'P0 env: jev-gate.env.ps1 loaded'
    } else {
        W ('P0 WARN env missing: ' + $envPs1 + ' (inherit environment)')
    }

    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }

    # 1) 正式评测
    W 'P1 eval: jev_eval.py --repeats 3'
    & $py -X utf8 (Join-Path $here 'jev_eval.py') --repeats 3 `
        1> (Join-Path $logDir 'monthly_eval_out.log') `
        2> (Join-Path $logDir 'monthly_eval_err.log')
    $rc = $LASTEXITCODE
    W ('P1 eval exit=' + $rc)

    # 2) 守卫判定（评测失败也跑：MAX_AGE 会提示结果过期）
    W 'P2 guard: jev_eval_guard.py --cadence monthly'
    & $py -X utf8 (Join-Path $here 'jev_eval_guard.py') --cadence monthly `
        1> (Join-Path $logDir 'monthly_guard_out.log') `
        2> (Join-Path $logDir 'monthly_guard_err.log')
    $grc = $LASTEXITCODE
    $guardLast = Get-Content -LiteralPath (Join-Path $logDir 'monthly_guard_out.log') `
        -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
    W ('P2 guard exit=' + $grc + ' out=' + $guardLast)

    # 3) 覆盖率守卫：stdout 为一行 JSON，落 jsonl 便于趋势统计
    W 'P3 coverage: jev_coverage_guard.py'
    $covJsonl = Join-Path $logDir 'monthly_coverage.jsonl'
    & $py -X utf8 (Join-Path $here 'jev_coverage_guard.py') `
        1>> $covJsonl `
        2>> (Join-Path $logDir 'monthly_coverage_err.log')
    $crc = $LASTEXITCODE
    $covLast = Get-Content -LiteralPath $covJsonl -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
    W ('P3 coverage exit=' + $crc + ' json=' + $covLast)

    # 日志封顶 200KB，保留尾部（沿用 run_eval.ps1 判例）
    if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 200KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
    }

    W ('P4 done eval=' + $rc + ' guard=' + $grc + ' coverage=' + $crc)
    # 退出码优先级：评测 > 评测守卫 > 覆盖率守卫（0=正常 1=告警）
    if ($rc -ne 0) { exit $rc }
    if ($grc -ne 0) { exit $grc }
    exit $crc
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
