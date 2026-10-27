# install_arch1_ops.ps1 -- ARCH1 architecture/tech-debt weekly patrol (WO-LEGION-ARCH1-PATROL)
# Task: ARCH1ArchPatrol  Monday 09:50  -> arch1_ops.py weekly
# Policy: L2 host-level action (scheduled task create/delete) requires human
#         five-element approval (K-STOCK-416). Dry-run first if needed.
# Rollback: .\install_arch1_ops.ps1 -Uninstall
# ASCII-only script.
param(
    [switch]$Uninstall,
    [switch]$DryRun,
    [string]$Python = "",
    [string]$Repo = "C:\aios"
)
$ErrorActionPreference = 'Stop'

$TaskName = 'ARCH1ArchPatrol'
$Dow = 'Monday'
$Hour = 9
$Min = 50

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output ("removed: " + $TaskName)
    return
}

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw 'python not found; pass -Python <path>' }
    $Python = $cmd.Source
}
$Tool = Join-Path $Repo 'agent_platform\tools\arch1_ops.py'
if (-not (Test-Path $Tool)) { throw ("arch1_ops.py missing: " + $Tool) }
if (-not (Test-Path $Python)) { throw ("python missing: " + $Python) }

$at = [datetime]::Today.AddHours($Hour).AddMinutes($Min)
$arg = '-X utf8 "' + $Tool + '" weekly'
if ($DryRun) {
    Write-Output ("[dry-run] " + $TaskName + " weekly " + $Dow + " " + $at.ToString('HH:mm') + " | " + $Python + " " + $arg)
    return
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Dow -At $at
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description 'ARCH1 weekly architecture/tech-debt patrol (L2 approved 2026-10-27)' -Force | Out-Null
Write-Output ("registered: " + $TaskName + " (" + $Dow + " " + $at.ToString('HH:mm') + ")")
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize
