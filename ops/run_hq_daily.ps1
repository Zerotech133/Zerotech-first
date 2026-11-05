# LingJie A0 daily board run - 07:50 wrapper
# ASCII-only wrapper; writes start/end markers so Task Scheduler failures are visible.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$Date = Get-Date -Format 'yyyy-MM-dd'
$Log = 'C:\Temp\hq_daily_run.log'
$BriefDir = Join-Path $Root 'deliverables\data_briefs'
$Brief = Join-Path $BriefDir ($Date + '_data_brief.json')
$Rollup = Join-Path $Root ('deliverables\daily_briefs\' + $Date + '\rollup.json')
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Path $BriefDir -Force | Out-Null

function Write-RunLog([string]$Message) {
    try {
        ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) |
            Out-File -LiteralPath $Log -Append -Encoding utf8
    } catch {}
}

Write-RunLog "START date=$Date root=$Root pid=$PID"

# 1) Build the data brief. A brief failure must not prevent the daily board.
& $Py -u -X utf8 (Join-Path $Root 'tools\datawarehouse\hq_brief.py') --json --out $Brief *>> $Log
Write-RunLog "BRIEF rc=$LASTEXITCODE exists=$(Test-Path -LiteralPath $Brief)"

# 2) Run the 17-seat board, with a bounded per-seat timeout and modest concurrency.
# The Python runner has no progress output, so -u and this wrapper's start/end markers
# distinguish a real run from a task killed before Python emitted anything.
if (Test-Path -LiteralPath $Brief) {
    & $Py -u -X utf8 (Join-Path $Root 'tools\hq_daily_run.py') --data-brief $Brief --notify-inbox --timeout 90 --concurrency 4 *>> $Log
} else {
    & $Py -u -X utf8 (Join-Path $Root 'tools\hq_daily_run.py') --notify-inbox --timeout 90 --concurrency 4 *>> $Log
}
$rc = $LASTEXITCODE
Write-RunLog "END rc=$rc rollup_exists=$(Test-Path -LiteralPath $Rollup)"
exit $rc
