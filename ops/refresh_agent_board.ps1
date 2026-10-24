# refresh_agent_board.ps1 - regenerate the agent board page (served at /agent_board.html)
#
# WHY: the board is a *static* file under portal/, so nginx serves it with zero
# service restart. But a static file goes stale; the scheduled task
# "AgentBoardRefresh" runs this every 30 minutes to keep it current.
#
# ASCII-only on purpose: this repo has no BOM on .ps1 files, and PS 5.1 would
# read non-ASCII bytes as GBK (see AGENTS.md deploy note).

$py     = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\tools\agent_census.py'
$out    = 'C:\aios\portal\agent_board.html'
$log    = 'C:\Temp\agent_board_refresh.log'

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if (-not (Test-Path $py)) {
    ("[" + $stamp + "] SKIP: python not found at " + $py) | Out-File $log -Append -Encoding utf8 -ErrorAction SilentlyContinue
    exit 1
}
if (-not (Test-Path $script)) {
    ("[" + $stamp + "] SKIP: script not found at " + $script) | Out-File $log -Append -Encoding utf8 -ErrorAction SilentlyContinue
    exit 2
}

& $py -X utf8 -B $script --html $out 2>&1 | Out-Null
$rc = $LASTEXITCODE

$bytes = 0
if (Test-Path $out) { $bytes = (Get-Item $out).Length }
("[" + $stamp + "] rc=" + $rc + " bytes=" + $bytes) | Out-File $log -Append -Encoding utf8 -ErrorAction SilentlyContinue

exit $rc
