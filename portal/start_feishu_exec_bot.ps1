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
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
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
    if ($proc) { Write-Log "STARTED pid=$($proc.Id)" } else { Write-Log "FAIL: null"; exit 1 }
} catch {
    Write-Log "FAIL: $($_.Exception.Message)"
    exit 1
}
