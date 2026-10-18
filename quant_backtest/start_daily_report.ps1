# XueqiuDailyReport wrapper (ASCII-only per PS5.1 ANSI-decode rule, 2026-10-18)
# Daily 19:00 report generation. Logs land in C:/aios/automation_runs/xq_report/
$root = $PSScriptRoot
$py = "C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$outDir = "C:\aios\automation_runs\xq_report"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
& $py (Join-Path $root "xq_daily_report.py") *> (Join-Path $outDir ("cron_r_" + $stamp + ".out"))
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path (Join-Path $outDir "cron_alert.log") -Value ("{0} rc={1} WARN daily report nonzero exit" -f $stamp, $LASTEXITCODE)
}
