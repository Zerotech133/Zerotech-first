param(
    [string]$Time = "18:10",
    [string]$TaskName = "FactorDaily",
    [switch]$RunNow
)

# Register FACTOR1 daily factor-mining production line under \ZeroBound.
# Weekdays only; the wrapper fails closed on stale market data.
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Wrapper = Join-Path $Repo "tools\scheduled\factor_daily_task.ps1"
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
$def.RegistrationInfo.Description = "FACTOR1 daily factor mining: weekdays ${Time}, fail-closed on stale data, report/ledger only (no broadcast)"
$def.Principal.UserId = $env:USERNAME
$def.Principal.LogonType = 3
$def.Principal.RunLevel = 0
$def.Settings.Enabled = $true
$def.Settings.StartWhenAvailable = $true
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Settings.ExecutionTimeLimit = "PT30M"
$def.Settings.MultipleInstances = 2

$trigger = $def.Triggers.Create(3)
$today = Get-Date -Format "yyyy-MM-dd"
$trigger.StartBoundary = "${today}T${Time}:00"
$trigger.DaysOfWeek = 62
$trigger.WeeksInterval = 1

$action = $def.Actions.Create(0)
$action.Path = $PowerShell
$action.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Wrapper`""
$action.WorkingDirectory = $Repo

$folder.RegisterTaskDefinition($TaskName, $def, 6, $env:USERNAME, $null, 3, $null)
$task = $folder.GetTask($TaskName)
Write-Output ("registered: {0}\{1} state={2} next={3}" -f $folder.Path.TrimEnd("\"), $TaskName, $task.State, $task.NextRunTime)
if ($RunNow) {
    $task.Run($null) | Out-Null
    Write-Output "run requested"
}
