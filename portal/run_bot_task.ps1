$py = 'C:\项目\老田的AI助手\CowAgent\venv\Scripts\python.exe'
$bot = 'C:\项目\老田的AI助手\portal\feishu_bot_ws.py'
$logf = 'C:\项目\老田的AI助手\portal\logs\feishu_bot_task.log'
"[$(Get-Date -Format 'MM-dd HH:mm:ss')] task wrapper start" | Out-File -Append -Encoding utf8 $logf
& $py -u $bot 2>&1 | Out-File -Append -Encoding utf8 $logf
"[$(Get-Date -Format 'MM-dd HH:mm:ss')] task wrapper exit code=$LASTEXITCODE" | Out-File -Append -Encoding utf8 $logf