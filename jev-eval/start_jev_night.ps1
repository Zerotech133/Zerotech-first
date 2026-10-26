# JEV night launcher wrapper (2026-10-26, K-ORG-20261026-JEVNIGHTLAUNCH)
# ASCII only. Called by \ZeroBound\JevNight scheduled task.
# Purpose: set the approved night-run env (JEV_ITEM_SLEEP_S=0) explicitly,
# so the backfill child inherits it regardless of profile env propagation.
$ErrorActionPreference = 'Continue'
$env:JEV_ITEM_SLEEP_S = '0'
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\jev_night_task.log'
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\tools\jev_night.py'
function W($m) { ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) | Add-Content -Path $log -Encoding utf8 }
W ("wrapper start JEV_ITEM_SLEEP_S={0} PID={1}" -f $env:JEV_ITEM_SLEEP_S, $PID)
& $py -X utf8 -B $script --start 21:50 --until 07:00
$rc = $LASTEXITCODE
W ("wrapper exit rc={0}" -f $rc)
exit $rc