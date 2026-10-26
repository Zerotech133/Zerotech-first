# install_legion_gateway_8795.ps1 — 安装 8795 本地网关常驻任务（L2 批准 2026-10-27）
# 任务：LegionGateway8795；AtLogOn 当前用户；失败重启 3 次/1 分钟；无限执行时长；仅 127.0.0.1:8795。
$ErrorActionPreference = 'Stop'
$wrapper = 'C:\aios\ops\local_gateway\run_legion_gateway_8795.ps1'
if (-not (Test-Path $wrapper)) { throw "wrapper missing: $wrapper" }

$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $wrapper + '"')
$trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName 'LegionGateway8795' -Action $act -Trigger $trg -Settings $set `
    -Description 'legion gateway /llm/chat on 127.0.0.1:8795; approved 2026-10-27 L2' -Force | Out-Null
Write-Output 'registered: LegionGateway8795'
Get-ScheduledTask -TaskName 'LegionGateway8795' | Select-Object TaskName, State
