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
# 2026-10-29 ON-DEMAND: skip the night run when backlog is small. Daily inflow is
# handled by the hourly JevBackfill; skipping frees the GPU for other night jobs.
# Threshold: env JEV_NIGHT_MIN_BACKLOG (default 500, set 0 to always run).
$minBacklog = 500
if ($env:JEV_NIGHT_MIN_BACKLOG) {
    $parsed = 0
    if ([int]::TryParse($env:JEV_NIGHT_MIN_BACKLOG, [ref]$parsed)) { $minBacklog = $parsed }
}
$pendingPy = 'C:ios	ools\jev_pending.py'
$pendingRaw = & $py -X utf8 -B $pendingPy 2>$null
if ($LASTEXITCODE -eq 0 -and $pendingRaw) {
    $pending = 0
    [void][int]::TryParse(([string]$pendingRaw).Trim(), [ref]$pending)
    W ("wrapper pending check pending={0} min={1}" -f $pending, $minBacklog)
    if ($pending -lt $minBacklog) {
        W ("wrapper SKIP night run: pending {0} < {1} (on-demand mode)" -f $pending, $minBacklog)
        exit 0
    }
} else {
    W ("wrapper pending check failed (rc={0}) -> run as usual" -f $LASTEXITCODE)
}
& $py -X utf8 -B $script --start 21:50 --until 07:00
$rc = $LASTEXITCODE
W ("wrapper exit rc={0}" -f $rc)
exit $rc