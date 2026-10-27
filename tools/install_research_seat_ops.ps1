# install_research_seat_ops.ps1 -- STRAT1/BACK1 research duty triggers (ACAD1 P2)
# Tasks (weekly):
#   STRAT1Brief  Monday    09:40  -> research_seat_ops.py strat1
#   BACK1Check       Wednesday 15:10  -> research_seat_ops.py back1
#   SeatsWeekly    Friday    16:10  -> research_seat_ops.py weekly
# Policy: L2 host-level action (scheduled task create/delete) requires human
#         five-element approval (K-STOCK-416). Dry-run first if needed.
# Rollback: .\install_research_seat_ops.ps1 -Uninstall
# ASCII-only script.
param(
    [switch]$Uninstall,
    [switch]$DryRun,
    [string]$Python = "",
    [string]$Repo = "C:\aios"
)
$ErrorActionPreference = 'Stop'

$Tasks = @(
    @{ Name = 'STRAT1Brief'; Dow = 'Monday';    Hour = 9;  Min = 40; Sub = 'strat1'; Desc = 'STRAT1 weekly strategy hypothesis brief (L2 approved 2026-10-27)' },
    @{ Name = 'BACK1Check';      Dow = 'Wednesday'; Hour = 15; Min = 10; Sub = 'back1'; Desc = 'BACK1 weekly data-freeze/backtest duty check (L2 approved 2026-10-27)' },
    @{ Name = 'SeatsWeekly';   Dow = 'Friday';    Hour = 16; Min = 10; Sub = 'weekly'; Desc = 'STRAT1/BACK1 weekly duty report (L2 approved 2026-10-27)' }
)

if ($Uninstall) {
    foreach ($t in $Tasks) {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output ("removed: " + $t.Name)
    }
    return
}

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw 'python not found; pass -Python <path>' }
    $Python = $cmd.Source
}
$Tool = Join-Path $Repo 'agent_platform\tools\research_seat_ops.py'
if (-not (Test-Path $Tool)) { throw ("research_seat_ops.py missing: " + $Tool) }
if (-not (Test-Path $Python)) { throw ("python missing: " + $Python) }

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

foreach ($t in $Tasks) {
    $arg = '-X utf8 "' + $Tool + '" ' + $t.Sub
    $at = [datetime]::Today.AddHours($t.Hour).AddMinutes($t.Min)
    if ($DryRun) {
        Write-Output ("[dry-run] " + $t.Name + " weekly " + $t.Dow + " " + $at.ToString('HH:mm') + " | " + $Python + " " + $arg)
        continue
    }
    $Action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $Repo
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $t.Dow -At $at
    Register-ScheduledTask -TaskName $t.Name -Action $Action -Trigger $Trigger `
        -Settings $Settings -Description $t.Desc -Force | Out-Null
    Write-Output ("registered: " + $t.Name + " (" + $t.Dow + " " + $at.ToString('HH:mm') + ")")
}

if (-not $DryRun) {
    Get-ScheduledTask -TaskName ($Tasks | ForEach-Object { $_.Name }) |
        Select-Object TaskName, State | Format-Table -AutoSize
}
