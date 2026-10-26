# install_academy_ops.ps1 -- Academy research ops triggers (ACAD1 P0)
# Tasks (weekly):
#   AcademyIntakeBrief  Monday    09:30  -> academy_ops.py intake
#   AcademyReview       Wednesday 15:00  -> academy_ops.py review
#   AcademyWeeklyReport Friday    16:00  -> academy_ops.py weekly
# Policy: L2 host-level action (scheduled task create/delete) requires human
#         five-element approval (K-STOCK-416). Can run with -DryRun first.
# Rollback: .\install_academy_ops.ps1 -Uninstall
# ASCII-only script (avoid CJK path/encoding issues in scheduled tasks).
param(
    [switch]$Uninstall,
    [switch]$DryRun,
    [string]$Python = "",
    [string]$Repo = "C:\aios"
)
$ErrorActionPreference = 'Stop'

$Tasks = @(
    @{ Name = 'AcademyIntakeBrief';  Dow = 'Monday';    Hour = 9;  Min = 30; Sub = 'intake'; Desc = 'ACAD1 weekly research intake brief (L2 approved 2026-10-27)' },
    @{ Name = 'AcademyReview';       Dow = 'Wednesday'; Hour = 15; Min = 0;  Sub = 'review'; Desc = 'ACAD1 weekly research review packet (L2 approved 2026-10-27)' },
    @{ Name = 'AcademyWeeklyReport'; Dow = 'Friday';    Hour = 16; Min = 0;  Sub = 'weekly'; Desc = 'ACAD1 weekly research report to A0 (L2 approved 2026-10-27)' }
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
$Tool = Join-Path $Repo 'agent_platform\tools\academy_ops.py'
if (-not (Test-Path $Tool)) { throw ("academy_ops.py missing: " + $Tool) }
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
