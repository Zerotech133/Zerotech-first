# 单一任务 bot 守护（2026-10-25 老田批准"合并为单一计划任务守护"）
# DSHBotWatchdog 每 5 分钟调用本脚本：保证恰好一个 feishu_bot_ws.py 进程。
# 不再使用常驻 loop，避免"两个看护循环竞速各拉一个 bot"的双实例问题。
$ErrorActionPreference = "Continue"

$py_src = "C:\aios\CowAgent\venv\Scripts\python.exe"
$bot    = "C:\aios\portal\feishu_bot_ws.py"
$notify_bot = "C:\aios\portal\feishu_notify_bot.py"
$log    = "C:\aios\portal\logs\feishu_bot_task.log"
$out    = "C:\aios\portal\logs\feishu_bot_stdout.log"
$err    = "C:\aios\portal\logs\feishu_bot_stderr.log"
$notify_out = "C:\aios\portal\logs\feishu_notify_stdout.log"
$notify_err = "C:\aios\portal\logs\feishu_notify_stderr.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

Write-Log "=== single-task supervisor run ==="
if (-not (Test-Path -LiteralPath $py_src)) { Write-Log "ABORT python missing"; exit 1 }
if (-not (Test-Path -LiteralPath $bot)) { Write-Log "ABORT bot missing"; exit 1 }

$mutex = $null
$got = $false
try {
    $mutex = New-Object System.Threading.Mutex($false, 'Global\FeishuBotStart')
    $got = $mutex.WaitOne(0)
} catch { $got = $false }
if (-not $got) {
    Write-Log "start mutex busy - skip"
    exit 0
}

function Ensure-NotifyBot {
    if (-not (Test-Path -LiteralPath $notify_bot)) { Write-Log "ABORT notify bot missing"; return }
    $n = @(Get-CimInstance Win32_Process -Filter "name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*feishu_notify_bot.py*' })
    if ($n.Count -gt 0) {
        Write-Log ("notify alive pid=" + (($n | ForEach-Object { $_.ProcessId }) -join ','))
        return
    }
    Write-Log "starting notify bot ..."
    $np = Start-Process -FilePath $py_src `
        -ArgumentList @("-u", "-X", "utf8", $notify_bot) `
        -WorkingDirectory "C:\aios\portal" `
        -WindowStyle Hidden `
        -RedirectStandardOutput $notify_out `
        -RedirectStandardError $notify_err `
        -PassThru
    if ($np) { Write-Log "NOTIFY STARTED pid=$($np.Id)" } else { Write-Log "FAIL notify Start-Process null" }
}

try {
    $existing = @(Get-CimInstance Win32_Process -Filter "name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*feishu_bot_ws.py*' })
    Ensure-NotifyBot
    if ($existing.Count -gt 0) {
        Write-Log ("alive pid=" + (($existing | ForEach-Object { $_.ProcessId }) -join ','))
        exit 0
    }
    $env:VOICE_INGEST_ENABLED = "1"
    $env:VOICE_ASR_PROVIDER   = "local"
    $env:VOICE_ASR_MODEL      = "C:\aios\models\faster-whisper-small"
    $env:VOICE_AUTO_EXECUTE   = "1"
    $env:VOICE_ASR_TIMEOUT    = "90"
    $env:HF_ENDPOINT          = "https://hf-mirror.com"
    # 2026-10-26 B5：卡片回调强制验签（观察期；旧卡无签名将被拒）
    $env:CARD_SIGN_ENFORCE    = "1"
    Write-Log "starting bot ..."
    $proc = Start-Process -FilePath $py_src `
        -ArgumentList @("-u", "-X", "utf8", $bot) `
        -WorkingDirectory "C:\aios\portal" `
        -WindowStyle Hidden `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err `
        -PassThru
    if ($proc) { Write-Log "STARTED pid=$($proc.Id)" } else { Write-Log "FAIL Start-Process null" }
} catch {
    Write-Log ("FAIL " + $_.Exception.Message)
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    try { $mutex.Dispose() } catch { }
}
Write-Log "=== supervisor exit ==="
exit 0
