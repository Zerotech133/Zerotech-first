# Agent platform shadow automation (K-STOCK-004 blueprint v2 / K-ORG-001 sec.4)
# Mode 'run'  : weekdays 17:10 - run_shadow.py -> data/shadow/shadow_ledger.jsonl
#               (idempotent per day+symbol; failed rows -> P0 feishu alert inside)
# Mode 'push' : weekdays 17:35 - a0_chief.py --push (daily report to Feishu)
# Mode 'eval' : fridays  17:45 - eval_shadow.py -> data/shadow/eval_last_run.log
#               (three-criteria verdict J1/J2/J3; P2 automation, 10-17)
# ASCII-only by design (scheduled-task Chinese-path judgment: use C:\aios junction).
param([Parameter(Mandatory=$true)][ValidateSet('run','push','eval')][string]$Mode)
$ErrorActionPreference = 'Continue'
$here = 'C:\aios\agent_platform'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$py = 'C:\Program Files\Python311\python.exe'
if ($Mode -eq 'run') {
    & $py -X utf8 (Join-Path $here 'run_shadow.py') 1> (Join-Path $here 'data\shadow\last_run.log') 2>&1
    exit $LASTEXITCODE
}
elseif ($Mode -eq 'eval') {
    # cmd /c byte-level redirect: PS 5.1 native-stdout redirect mangles UTF-8
    # Chinese through GBK decode + UTF-16 rewrite (19:51 log mojibake verdict).
    $log = Join-Path $here 'data\shadow\eval_last_run.log'
    cmd /c "`"$py`" -X utf8 `"$here\eval_shadow.py`" > `"$log`" 2>&1"
    exit $LASTEXITCODE
}
else {
    & $py -X utf8 (Join-Path $here 'a0_chief.py') --push 1> (Join-Path $here 'data\shadow\chief_last_run.log') 2>&1
    exit $LASTEXITCODE
}
