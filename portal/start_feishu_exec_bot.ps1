# 零界·执行 bot 拉起器（W2，2026-10-24）
# 计划任务 DSHExecBot 每 5 分钟幂等调用：
#   · 无凭据（zb.owner.exec.json 不存在）→ no-op 留痕（W2 前安全待机）
#   · 已在跑 → 跳过；否则拉起并落 stdout/stderr 日志
$ErrorActionPreference = 'Continue'
$py     = "C:\aios\CowAgent\venv\Scripts\python.exe"
$bot    = "C:\aios\portal\feishu_dsh_bot.py"
$secret = "C:\aios\portal\work\feishu_secrets\zb.owner.exec.json"
$log    = "C:\aios\portal\logs\feishu_exec_bot_task.log"
$out    = "C:\aios\portal\logs\feishu_exec_bot_stdout.log"
$err    = "C:\aios\portal\logs\feishu_exec_bot_stderr.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

Write-Log "=== DSHExecBot run ==="
if (-not (Test-Path -LiteralPath $py)) { Write-Log "ABORT python missing [$py]"; exit 1 }
if (-not (Test-Path -LiteralPath $secret)) {
    Write-Log "SKIP: secret missing ($secret) — W2 待老田建 app"
    exit 0
}

# ★2026-11-09 加固：CommandLine 在非管理员账户/S4U 下返回 NULL，
# 导致进程枚举恒为空、看门狗每 5min 重复拉起。改用全局 Mutex
# + S4U 安全进程名枚举双重幂等（参考 run_bot_watchdog.ps1 的互斥模式）。
$mutex = $null; $got = $false
try {
    $mutex = New-Object System.Threading.Mutex($false, 'Global\FeishuDshBotStart')
    $got = $mutex.WaitOne(0)
} catch { $got = $false }
if (-not $got) {
    Write-Log "start mutex busy - skip (peer is starting or already started)"
    exit 0
}

try {
    # 二次判活：Mutex 已保证不会并发；正常用户会话下 CommandLine 可枚举
    $running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*feishu_dsh_bot.py*' })
    if ($running.Count -gt 0) {
        Write-Log "already running pid=$($running.ProcessId -join ',')"
        exit 0
    }
    Write-Log "starting 零界·执行 bot ..."
    try {
        $proc = Start-Process -FilePath $py `
            -ArgumentList @("-u", "-X", "utf8", $bot) `
            -WorkingDirectory "C:\aios\portal" `
            -WindowStyle Hidden `
            -RedirectStandardOutput $out `
            -RedirectStandardError $err `
            -PassThru
        if ($proc) {
            Write-Log "STARTED pid=$($proc.Id)"
            # 等待子进程就绪（CommandLine 写入 WMI 有短暂延迟），
            # 确保本 Mutex 持有者释放后，后续持有者能在二次判活枚举到
            Start-Sleep -Seconds 3
        } else { Write-Log "FAIL: null"; exit 1 }
    } catch {
        Write-Log "FAIL: $($_.Exception.Message)"
        exit 1
    }
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    try { $mutex.Dispose() } catch { }
}
