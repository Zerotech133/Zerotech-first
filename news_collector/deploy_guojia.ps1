# VERSION: deploy_guojia v1 (2026-10-18) - register GuojiaDaily (admin required)
$ErrorActionPreference = 'Stop'
$task = 'GuojiaDaily'
# idempotent: drop existing first
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $task -Confirm:$false }
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\news_collector\guojia_task.ps1"'
# 07:25 = after JevDriftDaily 07:10; all six benchmarks have T-1 daily bar by then
$trigger = New-ScheduledTaskTrigger -Daily -At '07:25'
$principal = New-ScheduledTaskPrincipal -UserId 't' -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Guojia reconcile (K-ORG-191) daily 07:25' -Force | Out-Null
$t = Get-ScheduledTask -TaskName $task
Write-Output ('OK State=' + $t.State)
Write-Output ('Action=' + $t.Actions[0].Arguments)
Write-Output ('Trigger=' + $t.Triggers[0].StartBoundary)
Write-Output ('Principal=' + $t.Principal.UserId + '/' + $t.Principal.LogonType + '/' + $t.Principal.RunLevel)
