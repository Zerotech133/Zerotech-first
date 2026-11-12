# 2026-11-12 FIX: 中文路径改 ASCII (PS5 GBK 读脚本会乱码崩)
$py = 'C:\aios\CowAgent\venv\Scripts\python.exe'
$bot = 'C:\aios\portal\feishu_bot_ws.py'
$logf = 'C:\aios\portal\logs\feishu_bot_task.log'
"[$(Get-Date -Format 'MM-dd HH:mm:ss')] task wrapper start" | Out-File -Append -Encoding utf8 $logf
& $py -u $bot 2>&1 | Out-File -Append -Encoding utf8 $logf
"[$(Get-Date -Format 'MM-dd HH:mm:ss')] task wrapper exit code=$LASTEXITCODE" | Out-File -Append -Encoding utf8 $logf