# VERSION: deploy_czsc_shadow v1 (2026-10-19) - register CzscShadowDaily (admin required)
# K-ORG-202：CZSC 影子旁路每日调度挂接。幂等：先卸旧再注册。
# 路径走 C:\aios junction（= 主仓，ChanlunLearnDaily 同款先例）。
$ErrorActionPreference = 'Stop'
$task = 'CzscShadowDaily'
# idempotent: drop existing first
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $task -Confirm:$false }
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\quant_backtest\czsc_shadow_task.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At '19:55'
$principal = New-ScheduledTaskPrincipal -UserId 't' -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'CZSC shadow ledger daily 19:55 (K-ORG-202 bypass, never enters verdict/L3/L4)' -Force | Out-Null
$t = Get-ScheduledTask -TaskName $task
Write-Output ('OK State=' + $t.State)
Write-Output ('Action=' + $t.Actions[0].Arguments)
Write-Output ('Trigger=' + $t.Triggers[0].StartBoundary)
Write-Output ('Principal=' + $t.Principal.UserId + '/' + $t.Principal.LogonType + '/' + $t.Principal.RunLevel)
