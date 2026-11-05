param(
    [string]$CostBp = "",
    [string]$CostFile = "",
    [switch]$Send
)

# 60-day strategy live-cost threshold alert wrapper.
# Input priority: -CostBp > -CostFile > research/reports/strategy_live_cost.json.
# Default is dry-run; pass -Send to deliver a Feishu text alert (explicit opt-in).
# The guard exits 2/3 on warning/critical; --no-fail keeps this task green and
# the alert is recorded in the log / JSON report instead.

$ErrorActionPreference = "Continue"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Py = Join-Path (Split-Path $Repo -Parent) "py312\python.exe"
$Guard = Join-Path $Repo "tools\research\strategy_cost_guard.py"
$Live = Join-Path $Repo "research\reports\strategy_live_cost.json"
$Log = "C:\Temp\strategy_cost_alert.log"

function Write-Log([string]$Text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Text
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Output $line
}

$argList = @($Guard, "--no-fail")
if ($CostBp -ne "") {
    $argList += @("--cost-bp", $CostBp)
} elseif ($CostFile -ne "") {
    $argList += @("--from-file", $CostFile)
} elseif (Test-Path $Live) {
    $argList += @("--from-file", $Live)
} else {
    Write-Log "SKIP: no cost input (use -CostBp / -CostFile or write $Live)"
    exit 0
}
if ($Send) { $argList += "--send" }

try {
    $output = & $Py -X utf8 @argList 2>&1
    $rc = $LASTEXITCODE
    Write-Log ("guard rc={0}`n{1}" -f $rc, ($output -join "`n"))
    exit 0
} catch {
    Write-Log ("ERROR: {0}" -f $_.Exception.Message)
    exit 0
}
