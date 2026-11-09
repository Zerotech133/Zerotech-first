# FEEDBACK-L0 每日干跑计划任务注册/注销脚本（FEEDBACK-20261107 L2-L0 版）
# 用法：
#   powershell -File tools\feedback_task.ps1 -Register     # 注册每日 08:35
#   powershell -File tools\feedback_task.ps1 -Unregister   # 注销
param([switch]$Register, [switch]$Unregister)

$TaskName = "zeroq-feedback-dry"
$Py = "C:\Program Files\Python311\python.exe"
$Script = Join-Path $PSScriptRoot "..\intel_collector\examples\feedback_dry.py"
$LogDir = Join-Path $PSScriptRoot "..\reports\feedback_dry_daily"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "UNREGISTERED $TaskName"
    exit 0
}
if (-not $Register) {
    Write-Output "USAGE: -Register | -Unregister"
    exit 1
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Action = New-ScheduledTaskAction -Execute $Py -Argument ("-X utf8 `"" + $Script + "`" >> `"" + $LogDir + "\dry.log`" 2>&1")
$Trigger = New-ScheduledTaskTrigger -Daily -At 08:35
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Output "REGISTERED $TaskName daily 08:35"
