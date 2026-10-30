# JEV night launcher wrapper (2026-10-26, K-ORG-20261026-JEVNIGHTLAUNCH)
# ASCII only. Called by \ZeroBound\JevNight scheduled task.
# Purpose: set the approved night-run env (JEV_ITEM_SLEEP_S=0) explicitly,
# so the backfill child inherits it regardless of profile env propagation.
# 2026-10-30 BATCH2: on-demand gate is fail-closed - a broken pending check
# must NOT start a night run (old code logged "run as usual" and misfired).
$ErrorActionPreference = 'Continue'
$env:JEV_ITEM_SLEEP_S = '0'
# 2026-10-30 PREFILTERON (user approved): safe prefilter - routine governance
# templates only; measured false-kill 1.0% of skips, signal capture 100%.
$env:JEV_PREFILTER = 'safe'
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\jev_night_task.log'
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\tools\jev_night.py'
$pendingPy = 'C:\aios\tools\jev_pending.py'
function W($m) { ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) | Add-Content -Path $log -Encoding utf8 }
W ("wrapper start JEV_ITEM_SLEEP_S={0} PID={1}" -f $env:JEV_ITEM_SLEEP_S, $PID)
foreach ($p in @($py, $script, $pendingPy)) {
    if (-not (Test-Path $p)) {
        W ("wrapper ERROR missing path: {0}" -f $p)
        exit 1
    }
}
# Threshold: env JEV_NIGHT_MIN_BACKLOG (default 500, set 0 to always run).
$minBacklog = 500
if ($env:JEV_NIGHT_MIN_BACKLOG) {
    $parsed = 0
    if ([int]::TryParse($env:JEV_NIGHT_MIN_BACKLOG, [ref]$parsed)) {
        $minBacklog = $parsed
    } else {
        W ("wrapper ERROR invalid JEV_NIGHT_MIN_BACKLOG={0}" -f $env:JEV_NIGHT_MIN_BACKLOG)
        exit 1
    }
}
$pendingRaw = & $py -X utf8 -B $pendingPy 2>$null
$rcPending = $LASTEXITCODE
if ($rcPending -ne 0) {
    W ("wrapper ERROR pending check failed rc={0} -> exit 1 (fail-closed)" -f $rcPending)
    exit 1
}
$pending = -1
if (-not [int]::TryParse(([string]$pendingRaw).Trim(), [ref]$pending)) {
    W ("wrapper ERROR pending output not integer: {0} -> exit 1 (fail-closed)" -f ([string]$pendingRaw).Trim())
    exit 1
}
W ("wrapper pending check pending={0} min={1}" -f $pending, $minBacklog)
if ($pending -lt $minBacklog) {
    W ("wrapper SKIP night run: pending {0} < {1} (on-demand mode)" -f $pending, $minBacklog)
    exit 0
}
& $py -X utf8 -B $script --start 21:50 --until 07:00
$rc = $LASTEXITCODE
W ("wrapper exit rc={0}" -f $rc)
exit $rc
