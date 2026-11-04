# Jev 月度回归评测 + 基线漂移守卫（计划任务每月 1 日 09:00 触发）
# A3/P1-3 2026-10-07：16 天未回归评测，模型端 jev-latest 若静默升级，
# 三个生产组件阈值（URGENT_THRESHOLD/REVIEW_KEEP）锚定的 09-21 基线可能失效。
# 流程：jev_eval.py --repeats 3（30 题×3 遍，~3.6 万 token）→ jev_eval_guard.py
# 判定（准确率 <90% 或 noul/choice <95% → 飞书告警；正常也推月报摘要）。
$ErrorActionPreference = 'Continue'
$here = 'C:\aios\jev-eval'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = Join-Path $here 'review_data\eval_task.log'

# 环境注入：JEV_API_KEY / LARK_ALERT_OPEN_ID / lark-cli PATH / JEV_LOG_DIR
. (Join-Path $here 'jev-gate.env.ps1')

$py = 'C:\Program Files\Python311\python.exe'

# 1) 正式评测（repeats 3）
& $py -X utf8 (Join-Path $here 'jev_eval.py') --repeats 3 `
    1> (Join-Path $here 'review_data\eval_out.log') `
    2> (Join-Path $here 'review_data\eval_err.log')
$rc = $LASTEXITCODE

# 2) 守卫判定 + 推送（评测失败也跑 guard：MAX_AGE 检查会提示结果过期）
# JEV-EVALCOV-20261105: 显式声明月度口径（超龄 35 天）；不写则走 guard 默认值，
# 未来默认值变更会静默改掉本脚本语义。
& $py -X utf8 (Join-Path $here 'jev_eval_guard.py') --cadence monthly 1>$null 2>$null
$grc = $LASTEXITCODE

("eval exit=" + $rc + " guard=" + $grc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
if (Test-Path $log) {
    if ((Get-Item $log).Length -gt 200KB) {
        $tail = Get-Content $log -Tail 100 -Encoding UTF8
        Set-Content $log $tail -Encoding UTF8
    }
}
if ($rc -ne 0) { exit $rc }
exit $grc
