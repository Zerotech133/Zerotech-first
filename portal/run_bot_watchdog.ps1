# FeishuBotWatchdog wrapper — 每 5min 被计划任务触发
# 职责：如果 feishu_bot_ws.py 不在运行，拉起它
# 绕开 BOM+GBK 编码事故：路径用 Get-Item 解析真实 FullName
$ErrorActionPreference = "Continue"

# 用字面量先找 Python（Get-Item 自动处理编码）
# 2026-11-12 FIX: 中文路径改 ASCII (PS5 GBK 读脚本会乱码崩)
$py_src = "C:\aios\CowAgent\venv\Scripts\python.exe"
$bot    = "C:\aios\portal\feishu_bot_ws.py"
$log    = "C:\aios\portal\logs\feishu_bot_task.log"
$ts     = Get-Date -Format "MM-dd HH:mm:ss"

function Write-Log($msg) {
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

Write-Log "=== wrapper start ==="

# ① 用 Get-Item 解析真实路径（绕 BOM+GBK）
if (-not (Test-Path -LiteralPath $py_src)) { Write-Log "ABORT python not found [$py_src]"; exit 1 }
$py = (Get-Item -LiteralPath $py_src).FullName
if (-not (Test-Path -LiteralPath $bot))   { Write-Log "ABORT bot not found [$bot]"; exit 1 }
if (-not (Test-Path -LiteralPath (Split-Path $log))) { New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null }

Write-Log "PRECHECK py=[$py] bot=[$bot]"

# ② 查是否已有活跃 Bot
$existing = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*feishu_bot_ws.py*' }

if ($existing) {
    foreach ($e in $existing) { Write-Log "ALIVE pid=$($e.ProcessId)" }
    Write-Log "=== skip (already running) ==="
    exit 0
}

# ③ 拉起 Bot（2026-10-25：全局互斥 + 二次判活，防与 loop 版双实例）
$mutex = $null
$got = $false
try {
    $mutex = New-Object System.Threading.Mutex($false, 'Global\FeishuBotStart')
    $got = $mutex.WaitOne(0)
} catch { $got = $false }
if (-not $got) {
    Write-Log "start mutex busy — skip"
    exit 0
}
try {
    $existing2 = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*feishu_bot_ws.py*' }
    if ($existing2) {
        Write-Log "peer already started — skip"
    } else {
        Write-Log "STARTING..."
        try {
            $proc = Start-Process -FilePath $py `
                -ArgumentList @("-u", "-X", "utf8", $bot) `
                -WorkingDirectory "C:\aios\portal" `
                -WindowStyle Hidden `
                -PassThru
            if ($proc) { Write-Log "STARTED pid=$($proc.Id)" }
        } catch {
            Write-Log "FAIL: $($_.Exception.Message)"
        }
    }
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    try { $mutex.Dispose() } catch { }
}
Write-Log "=== wrapper exit ==="
exit 0
