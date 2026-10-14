# -*- coding: utf-8 -*-
# K-STOCK-368 deploy: jev card handler 换绑独立 app（告警卡片助手 cli_aa469ee72af85bc9）
# 机器级 env 固化（判例同 PG-Robot deploy）+ 重启链。
# 前置：新 app 已发布 v1.1.0 + im:message 已开通（发消息三选一，免审即时生效）
#       + K-STOCK-368 已 merge master（watchdog 跑主仓代码，env 覆盖需先入库）
#       + NEW_OPEN_ID 已取得（chat members API，union_id 交叉验证）。
$ErrorActionPreference = "Stop"

$APP_ID = "cli_aa469ee72af85bc9"
$APP_SECRET = "bgzAZ9IxySu7PfXJRLRnHbyPXIiTqQM5"
# 老田在新 app 域的 open_id（发卡收件人；app 维度，部署前由 chat members API 取得）
$NEW_OPEN_ID = $args[0]
if (-not $NEW_OPEN_ID -or $NEW_OPEN_ID -cnotmatch '^ou_[0-9a-f]{32}$') { throw "usage: deploy_368.ps1 <NEW_OPEN_ID ou_xxx>" }

# 1) 机器级 env（watchdog 每 5 分钟新起进程读注册表 env，即时生效）
[Environment]::SetEnvironmentVariable('JEV_CARD_APP_ID', $APP_ID, 'Machine')
[Environment]::SetEnvironmentVariable('JEV_CARD_APP_SECRET', $APP_SECRET, 'Machine')
Write-Host "machine env set: JEV_CARD_APP_ID=$APP_ID"

# 2) 收件人换新 app 域 open_id —— DPAPI 迁移后真源=plain 副本（W1-2 判例：
#    更新 plain 对应行 → 重跑 migrate_env_dpapi.py 重加密 .enc → --verify；
#    migrate 内置 plain 幂等保护，不会反向覆盖）
$envFile = 'E:\老田的AI助手\jev-eval\jev-gate.env.plain.ps1'
$lines = Get-Content $envFile -Encoding UTF8
$hit = ($lines | Select-String -SimpleMatch 'LARK_ALERT_OPEN_ID')
if ($hit.Count -ne 1) { throw "LARK_ALERT_OPEN_ID anchor count=$($hit.Count) (expect 1)" }
$old = ($lines -match 'ou_[0-9a-f]{32}')
if (-not $old) { throw 'old open_id (ou_) not found in plain file' }
$new = $lines | ForEach-Object {
    if ($_ -match 'LARK_ALERT_OPEN_ID') { $_ -replace "ou_[0-9a-f]{32}", $NEW_OPEN_ID } else { $_ }
}
Set-Content $envFile -Value $new -Encoding UTF8
Write-Host "LARK_ALERT_OPEN_ID -> $NEW_OPEN_ID (plain)"

# 3) 重加密 .enc（以 plain 为真源）+ 解密回读 verify
$py = 'C:/Program Files/Python311/python.exe'
& $py 'E:\老田的AI助手\jev-eval\migrate_env_dpapi.py'
if ($LASTEXITCODE -ne 0) { throw 'migrate_env_dpapi re-encrypt FAIL' }
& $py 'E:\老田的AI助手\jev-eval\migrate_env_dpapi.py' --verify
if ($LASTEXITCODE -ne 0) { throw 'DPAPI verify FAIL' }
Write-Host ".enc re-encrypted, MIGRATION_OK"

# 4) 停 card handler（让 ZhiceWatchdog 下轮以新 env 拉起）
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'lark_card_handler' } | ForEach-Object {
    Write-Host "kill card handler pid=$($_.ProcessId)"
    taskkill /PID $_.ProcessId /T /F | Out-Null
}

# 5) 恢复 feishu_bot_ws：Enable + 启动任务（它走老 app，与新 app 无冲突）
Enable-ScheduledTask -TaskName 'FeishuBotWatchdog' | Out-Null
Start-ScheduledTask -TaskName 'FeishuBotWatchdog'
Write-Host "FeishuBotWatchdog enabled + started (feishu_bot_ws 回归老 app 总线)"

Write-Host "deploy done. 手动触发 ZhiceWatchdog 后验证 card.log 新 app 就绪。"
