# FeishuBotWatchdog wrapper — 自循环守护版
# 不用计划任务，自己 while 循环每 60s 检查一次
# 启动：管理员 PowerShell 或双击：powershell -File run_bot_watchdog_loop.ps1
$ErrorActionPreference = "Continue"

$py_src = "C:\项目\老田的AI助手\CowAgent\venv\Scripts\python.exe"
$bot    = "C:\aios\portal\feishu_bot_ws.py"
$log    = "C:\aios\portal\logs\feishu_bot_task.log"
$INTERVAL_SEC = 60

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

Write-Log "=== watchdog LOOP start (interval=${INTERVAL_SEC}s) ==="

# 用 Get-Item 拿干净路径
if (-not (Test-Path -LiteralPath $py_src)) { Write-Log "ABORT python not found [$py_src]"; exit 1 }
$py = (Get-Item -LiteralPath $py_src).FullName

while ($true) {
    try {
        $alive = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
            Where-Object { $_.CommandLine -like '*feishu_bot_ws.py*' }

        if ($alive) {
            # 正常，每个周期只在首次 alive 时写一条（避免日志刷屏）
            # 保持静默，只在状态变化时写
        } else {
            Write-Log "BOT DEAD — starting..."
            try {
                $proc = Start-Process -FilePath $py `
                    -ArgumentList @("-u", "-X", "utf8", $bot) `
                    -WorkingDirectory "C:\aios\portal" `
                    -WindowStyle Hidden `
                    -PassThru
                if ($proc) { Write-Log "STARTED pid=$($proc.Id)" } else { Write-Log "FAIL: Start-Process null" }
            } catch {
                Write-Log "FAIL: $($_.Exception.Message)"
            }
        }
    } catch {
        Write-Log "ERROR in loop: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $INTERVAL_SEC
}
