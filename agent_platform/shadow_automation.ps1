# Agent platform shadow automation (K-STOCK-004 blueprint v2 / K-ORG-001 sec.4)
# Mode 'run'  : weekdays 17:10 - run_shadow.py -> data/shadow/shadow_ledger.jsonl
#               (idempotent per day+symbol; failed rows -> P0 feishu alert inside)
# Mode 'push' : weekdays 17:35 - a0_chief.py --push (daily report to Feishu)
# ASCII-only by design (scheduled-task Chinese-path judgment: use C:\aios junction).
param([Parameter(Mandatory=$true)][ValidateSet('run','push')][string]$Mode)
$ErrorActionPreference = 'Continue'
$here = 'C:\aios\agent_platform'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$py = 'C:\Program Files\Python311\python.exe'
if ($Mode -eq 'run') {
    & $py -X utf8 (Join-Path $here 'run_shadow.py') 1> (Join-Path $here 'data\shadow\last_run.log') 2>&1
    exit $LASTEXITCODE
}
else {
    & $py -X utf8 (Join-Path $here 'a0_chief.py') --push 1> (Join-Path $here 'data\shadow\chief_last_run.log') 2>&1
    exit $LASTEXITCODE
}
