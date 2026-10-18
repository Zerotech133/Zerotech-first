# Backfill sprint runner wrapper (Windows scheduled task).
# ASCII-only per PS5.1 rule: powershell -File decodes non-BOM UTF-8 as ANSI.
$root = $PSScriptRoot
$py = "C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$logDir = "C:\aios\automation_runs\xq_classify"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $logDir ("cron_s_" + $stamp + ".out")
$err = Join-Path $logDir ("cron_s_" + $stamp + ".err")
& $py (Join-Path $root "tools\backfill_sprint.py") --limit 2000 1> $out 2> $err
$rc = $LASTEXITCODE
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path (Join-Path $logDir "backfill_sprint.log") -Value ("[wrapper] run rc=" + $rc + " at " + $ts)
if (($rc -eq 2) -or ($rc -eq 3) -or ($rc -eq 4)) {
  # semantic exits: 2=outside window, 3=lock busy, 4=yielded -- info only
  Add-Content -Path (Join-Path $logDir "sprint_info.log") -Value ("[wrapper] rc=" + $rc + " at " + $ts + " (semantic exit)")
} elseif ($rc -ne 0) {
  Add-Content -Path (Join-Path $logDir "sprint_alert.log") -Value ("[wrapper] rc=" + $rc + " at " + $ts + " see cron_s_" + $stamp)
}
exit $rc
