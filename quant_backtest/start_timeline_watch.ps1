# XueqiuTimelineWatch wrapper (one-shot 19:35, read-only evidence collector)
# All-ASCII wrapper. python out/err to separate files, ASCII status log.
# NEVER touches other scheduled tasks; the watch itself is read-only.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$logDir = 'C:\aios\automation_runs\xq_timeline'
$out = Join-Path $logDir 'timeline_watch.out'
$err = Join-Path $logDir 'timeline_watch.err'
$log = Join-Path $logDir 'timeline_watch_status.log'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'tools\xq_timeline_watch.py') 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
exit $rc
