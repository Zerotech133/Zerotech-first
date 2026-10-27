# install_legion_gateway_8795.ps1 — 安装 8795 本地网关常驻任务（L2 批准 2026-10-27）
# 任务：LegionGateway8795；AtLogOn 当前用户 + 每 5 分钟健康探测触发器（自愈；
# 2026-10-27 实测：Task Scheduler 的 RestartOnFailure 对「长驻任务被外部杀掉」不触
# 发，故用同一任务的周期触发器补自愈——wrapper 健康探测存活即秒退，不会重复启动）。
# 失败重启设置保留（Restart 3/1min，对启动失败仍有效）；无限执行时长；仅 127.0.0.1:8795。
$ErrorActionPreference = 'Stop'
$wrapper = 'C:\aios\ops\local_gateway\run_legion_gateway_8795.ps1'
if (-not (Test-Path $wrapper)) { throw "wrapper missing: $wrapper" }

$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $wrapper + '"')
$trgLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trgKeep = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName 'LegionGateway8795' -Action $act `
    -Trigger @($trgLogon, $trgKeep) -Settings $set `
    -Description 'legion gateway /llm/chat on 127.0.0.1:8795; approved 2026-10-27 L2 (+5min keepalive)' -Force | Out-Null
Write-Output 'registered: LegionGateway8795 (AtLogOn + 5min keepalive)'
Get-ScheduledTask -TaskName 'LegionGateway8795' | Select-Object TaskName, State
$t = Get-ScheduledTask -TaskName 'LegionGateway8795'
$t.Triggers | ForEach-Object {
    Write-Output ('  trigger: ' + $_.CimClass.CimClassName + ' rep=' + $_.Repetition.Interval)
}
