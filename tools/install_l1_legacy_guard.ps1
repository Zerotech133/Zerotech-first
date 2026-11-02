# L1 遗留端口守卫安装器（管理员运行一次）
# 作用：注册计划任务 L1LegacyPortGuard（SYSTEM/最高权限）：
#   - 开机启动时执行一次
#   - 之后每 5 分钟执行一次
#   - 调用 tools/stop_legacy_5175_8102.ps1 -Quiet，清退任何复活的 5175/8102
# 背景：2026-11-02 09:06 主机重启后 5175/8102 被未知 SYSTEM 父进程拉起；普通用户 taskkill 被拒。
#      本守卫以 SYSTEM 运行，可持久清退，无需每次人工。
# 卸载：Unregister-ScheduledTask -TaskName 'L1LegacyPortGuard' -Confirm:$false
$ErrorActionPreference = 'Stop'
$task = 'L1LegacyPortGuard'
$script = Join-Path $PSScriptRoot 'stop_legacy_5175_8102.ps1'
if (-not (Test-Path $script)) { throw "missing $script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Quiet' -f $script)
$t1 = New-ScheduledTaskTrigger -AtStartup
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $task -Action $action -Trigger @($t1, $t2) `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host ("OK: installed scheduled task {0}" -f $task)
Write-Host 'Running once now...'
& schtasks.exe /run /tn $task | Out-Null
Start-Sleep -Seconds 25
foreach ($p in 5175, 8102) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) { Write-Host ("FAIL: {0} still listening" -f $p); exit 1 }
    Write-Host ("OK: {0} off" -f $p)
}
Write-Host 'L1 guard active: 5175/8102 off; auto-enforced every 5 minutes.'
exit 0
