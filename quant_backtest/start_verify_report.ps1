# XueqiuReportVerify wrapper (ASCII-only per PS5.1 ANSI-decode rule, 2026-10-18)
# Daily 19:05 verification of XueqiuDailyReport output. Logs land in C:/aios/automation_runs/xq_report/
$root = $PSScriptRoot
$py = "C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$outDir = "C:\aios\automation_runs\xq_report"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
# Mode: explicit --smoke arg wins; otherwise before today 19:00 the daily report
# cannot exist yet, so auto fall back to smoke (chain-only) to avoid false alerts.
$modeArg = @()
if ($args -contains "--smoke") {
    $modeArg = @("--smoke")
} elseif ((Get-Date).Hour -lt 19) {
    $modeArg = @("--smoke")
}
$outFile = Join-Path $outDir ("cron_v_" + $stamp + ".out")
& $py (Join-Path $root "tools\verify_daily_report.py") @modeArg *> $outFile
$rc = $LASTEXITCODE
Add-Content -Path $outFile -Value ("exit_code=" + $rc)
# rc=1 = semantic verification failure -> alert. rc=2 = report not generated yet
# (tolerated early state, no alert). rc=0 = all pass.
if ($rc -eq 1) {
    Add-Content -Path (Join-Path $outDir "verify_alert.log") -Value ("{0} rc=1 WARN report verify failed, see cron_v_{0}.out" -f $stamp)
}
exit $rc
