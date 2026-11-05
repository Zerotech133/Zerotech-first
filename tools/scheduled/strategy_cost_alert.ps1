param(
    [string]$CostBp = "",
    [string]$CostFile = "",
    [switch]$Send,
    [switch]$AnyDay
)

# 60-day strategy live-cost threshold alert wrapper.
# Scheduled mode: skip non-trading days (SSE trade_cal), require today's live
# cost file, send Feishu only when -Send and status is warning/critical.
# Manual mode: -CostBp / -CostFile bypass the live-file freshness check;
# -AnyDay bypasses the trading-day check.

$ErrorActionPreference = "Continue"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Py = Join-Path (Split-Path $Repo -Parent) "py312\python.exe"
$Guard = Join-Path $Repo "tools\research\strategy_cost_guard.py"
$TradingDay = Join-Path $Repo "tools\research\trading_day.py"
$Live = Join-Path $Repo "research\reports\strategy_live_cost.json"
$Log = "C:\Temp\strategy_cost_alert.log"
$Today = Get-Date

function Write-Log([string]$Text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Text
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Output $line
}

if (-not $AnyDay) {
    $tdOut = & $Py -X utf8 $TradingDay check 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log ("SKIP: non-trading day ({0})" -f ($tdOut -join " "))
        exit 0
    }
}

$usingLiveFile = $false
$argList = @($Guard, "--no-fail")
if ($CostBp -ne "") {
    $argList += @("--cost-bp", $CostBp)
} elseif ($CostFile -ne "") {
    $argList += @("--from-file", $CostFile)
} elseif (Test-Path $Live) {
    $argList += @("--from-file", $Live)
    $usingLiveFile = $true
} else {
    Write-Log "SKIP: no cost input (use -CostBp / -CostFile or write $Live)"
    exit 0
}

if ($usingLiveFile) {
    $item = Get-Item -LiteralPath $Live
    if ($item.LastWriteTime.Date -ne $Today.Date) {
        Write-Log ("SKIP: live cost file is stale (mtime={0})" -f $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"))
        exit 0
    }
    try {
        $raw = Get-Content -LiteralPath $Live -Raw -Encoding UTF8
        $obj = $raw | ConvertFrom-Json
        if ($obj.PSObject.Properties.Name -contains "as_of") {
            $asOf = [string]$obj.as_of
            if ($asOf -ne $Today.ToString("yyyy-MM-dd")) {
                Write-Log ("SKIP: live cost as_of={0} is not today" -f $asOf)
                exit 0
            }
        }
    } catch {
        Write-Log ("WARN: could not parse live cost file for freshness: {0}" -f $_.Exception.Message)
    }
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
