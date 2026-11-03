# Register "AI-Workstation-A0-Daily-Board" daily at 07:50 (ASCII only).
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\install_hq_daily_task.ps1
$ErrorActionPreference = 'Stop'
$TaskName = 'AI-Workstation-A0-Daily-Board'
$Wrapper = 'C:\aios\ops\run_hq_daily.ps1'
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "' + $Wrapper + '"')
$Trigger = New-ScheduledTaskTrigger -Daily -At '07:50'
$Principal = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + '\' + $env:USERNAME) -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'LingJie A0 daily board: 13 dept cloud reports -> A0 briefing' -Force | Out-Null
Write-Output ('registered: ' + $TaskName + ' @ 07:50')
