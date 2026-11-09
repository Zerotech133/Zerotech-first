# AIDE brief pre-generation launcher (2026-11-08)
# ASCII only (PS 5.1 + non-ASCII path pitfalls). Called by a scheduled task.
#
# Purpose: run AIDE brief pre-generation AFTER close, so the stock pages can
# serve a ready-made conclusion on open instead of waiting 100s+ for local
# inference.
#
# Tiers:
#   L1 = T1+T2 high-value pool  (~32 stocks) -> run after close, every day
#   L2 = T3 single-strategy     (~738 stocks) -> only when cloud LLM is wired
#                                               (config/cloud_llm.json enabled),
#                                               local-only would take ~31h
#
# Usage:
#   powershell -NoProfile -File start_intel_brief_pregen.ps1
#   powershell -NoProfile -File start_intel_brief_pregen.ps1 -Tier L1,L2 -Limit 50
#
# NOTE: this script does NOT register any scheduled task. Registering a task is
# a system-level change and needs 老田's explicit approval per the red line.
# Command to register (for the record, do not auto-run):
#   schtasks /Create /TN "\ZeroBound\IntlBriefPregen" /SC DAILY /ST 15:45 ^
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\portal\start_intel_brief_pregen.ps1"
param(
    [string]$Tier = "L1",
    [int]$Limit = 0,
    [switch]$Force
)
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'

$root   = 'C:\aios'
$py     = 'C:\Program Files\Python311\python.exe'
$script = Join-Path $root 'tools\intel_brief_pregen.py'
$logDir = Join-Path $root 'logs'
$log    = Join-Path $logDir 'intel_brief_pregen_task.log'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
function W($m) {
    ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) |
        Add-Content -Path $log -Encoding utf8
}

foreach ($p in @($py, $script)) {
    if (-not (Test-Path $p)) { W ("ERROR missing path: {0}" -f $p); exit 1 }
}

$argList = @('-X', 'utf8', $script, '--tier', $Tier)
if ($Limit -gt 0) { $argList += @('--limit', "$Limit") }
if ($Force) { $argList += '--force' }

W ("start tier={0} limit={1} force={2} pid={3}" -f $Tier, $Limit, $Force, $PID)
& $py @argList 2>&1 | ForEach-Object { $_ | Add-Content -Path $log -Encoding utf8 }
$rc = $LASTEXITCODE
W ("done rc={0}" -f $rc)
exit $rc
