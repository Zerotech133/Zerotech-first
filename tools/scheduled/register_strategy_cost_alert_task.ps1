param(
    [string]$Time = "18:40",
    [string]$TaskName = "StrategyCostAlert",
    [switch]$RunNow
)

# Register the trading-day 60d strategy cost alert task under \ZeroBound.
# Weekly trigger Mon-Fri at $Time; the wrapper itself skips SSE holidays and
# days without a fresh research/reports/strategy_live_cost.json.
# Feishu delivery is enabled via -Send in the task action; it only fires when
# the guard status is warning/critical and a live cost file exists.

$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Wrapper = Join-Path $Repo "tools\scheduled\strategy_cost_alert.ps1"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
try {
    $folder = $svc.GetFolder("\ZeroBound")
} catch {
    $root = $svc.GetFolder("\")
    [void]$root.CreateFolder("ZeroBound")
    $folder = $svc.GetFolder("\ZeroBound")
}

$def = $svc.NewTask(0)
$def.RegistrationInfo.Description = "60d strategy cost alert: trading-day ${Time}, warning >100bp, critical >198bp; Feishu via -Send"
$def.Principal.UserId = $env:USERNAME
$def.Principal.LogonType = 3
$def.Principal.RunLevel = 0
$def.Settings.Enabled = $true
$def.Settings.StartWhenAvailable = $true
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Settings.ExecutionTimeLimit = "PT10M"
$def.Settings.MultipleInstances = 2

$trigger = $def.Triggers.Create(3)
$today = Get-Date -Format "yyyy-MM-dd"
$trigger.StartBoundary = "${today}T${Time}:00"
$trigger.DaysOfWeek = 62
$trigger.WeeksInterval = 1

$action = $def.Actions.Create(0)
$action.Path = $PowerShell
$action.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Wrapper`" -Send"
$action.WorkingDirectory = $Repo

$folder.RegisterTaskDefinition($TaskName, $def, 6, $env:USERNAME, $null, 3, $null)
$task = $folder.GetTask($TaskName)
Write-Output ("registered: {0}\{1} state={2} next={3}" -f $folder.Path.TrimEnd("\"), $TaskName, $task.State, $task.NextRunTime)
if ($RunNow) {
    $task.Run($null) | Out-Null
    Write-Output "run requested"
}
