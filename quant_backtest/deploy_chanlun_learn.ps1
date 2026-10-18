# VERSION: deploy_chanlun_learn v1 (2026-10-18) - register ChanlunLearnDaily (admin required)
# K-ORG-192：缠论学习环每日调度挂接。幂等：先卸旧再注册。
# 路径走 C:\aios junction（= 主仓，AgentShadowDaily/JevDriftDaily 同款先例）。
$ErrorActionPreference = 'Stop'
$task = 'ChanlunLearnDaily'
# idempotent: drop existing first
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $task -Confirm:$false }
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\quant_backtest\chanlun_learn_task.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At '19:40'
$principal = New-ScheduledTaskPrincipal -UserId 't' -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Chanlun learn loop L1-L4 daily 19:40 (K-ORG-190 engine / K-ORG-192 schedule)' -Force | Out-Null
$t = Get-ScheduledTask -TaskName $task
Write-Output ('OK State=' + $t.State)
Write-Output ('Action=' + $t.Actions[0].Arguments)
Write-Output ('Trigger=' + $t.Triggers[0].StartBoundary)
Write-Output ('Principal=' + $t.Principal.UserId + '/' + $t.Principal.LogonType + '/' + $t.Principal.RunLevel)
