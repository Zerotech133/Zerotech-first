# Tushare Token 轮换脚本
# 用法:
#   .\rotate_tushare_token.ps1                  # 交互式：打开浏览器，提示粘贴新 token
#   .\rotate_tushare_token.ps1 -Token "新token" # 直接传入新 token
#   .\rotate_tushare_token.ps1 -SkipRestart     # 只更新 env 文件，不重启进程
#
# 作用：
#   1. （可选）打开 Tushare 官网让用户重新生成 token
#   2. 调用 Tushare API 验证新 token 有效性（trade_cal 接口，低积分消耗）
#   3. 原子更新 jev-gate.env.ps1 的 TUSHARE_TOKEN 行（保留 BOM 防止中文注释乱码）
#   4. 重启 signal_review_gate.py（唯一使用 TUSHARE_TOKEN 的进程）
#   5. 验证重启后日志正常
param(
    [Parameter()][string]$Token = "",
    [switch]$SkipRestart,
    [switch]$SkipBrowser
)

$EnvFile = "$PSScriptRoot\jev-gate.env.ps1"
$GateLog = "$env:TEMP\zhice-jev-gate.log"
$JevPy = "C:\Program Files\Python311\python.exe"   # 产线解释器（双 Python 治理：禁止裸 python）

# ── 1. 获取新 token ────────────────────────────────────────
if (-not $Token) {
    if (-not $SkipBrowser) {
        "正在打开 Tushare token 管理页..."
        Start-Process "https://tushare.pro/user/token"
    }
    ""
    "请在浏览器中："
    "  1. 登录 Tushare（如未登录）"
    "  2. 复制现有 token 旁的 [重新生成] 按钮点击"
    "  3. 复制新 token（64 位十六进制字符串）"
    ""
    $Token = Read-Host "粘贴新 token（或回车取消）"
    if (-not $Token) { "未提供 token，退出"; exit 1 }
    $Token = $Token.Trim()
}

# ── 2. 验证新 token 有效性 ─────────────────────────────────
"验证新 token（调用 trade_cal 接口）..."
$body = @{
    api_name = "trade_cal"
    token   = $Token
    params  = @{ exchange = "SSE" }
    fields  = "cal_date"
} | ConvertTo-Json -Compress

try {
    $r = Invoke-WebRequest -Uri "https://api.tushare.pro" -Method POST `
        -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 15
    $j = $r.Content | ConvertFrom-Json
    if ($j.code -ne 0) {
        Write-Error "Tushare 验证失败: $($j.msg)"
        exit 1
    }
    "  ✅ token 有效（trade_cal 返回 $($j.data.items.Count) 项）"
} catch {
    Write-Error "Tushare 验证异常: $($_.Exception.Message)"
    exit 1
}

# ── 3. 原子更新 jev-gate.env.ps1 ──────────────────────────
if (-not (Test-Path $EnvFile)) { Write-Error "找不到 $EnvFile"; exit 1 }

# 用 [System.IO.File] 处理 BOM（PS5.1 中文注释需要 UTF-8 BOM）
$utf8Bom  = New-Object System.Text.UTF8Encoding $true
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$content  = [System.IO.File]::ReadAllText($EnvFile, $utf8Bom)

# 检查 token 是否已在文件中（避免重复更新）
if ($content -match [regex]::Escape($Token)) {
    "  ⚠️  token 与文件中现有值一致，跳过更新"
} else {
    # 匹配 $env:TUSHARE_TOKEN = "..."
    $pattern = '(\$env:TUSHARE_TOKEN\s*=\s*)"([^"]*)"'
    if ($content -notmatch $pattern) {
        Write-Error "未在 $EnvFile 找到 TUSHARE_TOKEN 行"
        exit 1
    }
    $newContent = [regex]::Replace($content, $pattern, "`${1}`"$Token`"")

    # 备份
    $bak = "$EnvFile.bak.$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
    [System.IO.File]::WriteAllText($bak, $content, $utf8Bom)
    "  📦 备份: $bak"

    # 写入（保留 BOM）
    [System.IO.File]::WriteAllText($EnvFile, $newContent, $utf8Bom)
    "  ✅ 已更新 $EnvFile"
}

# ── 4. 重启 signal_review_gate.py ─────────────────────────
if ($SkipRestart) {
    "跳过重启（-SkipRestart）"
    "请手动重启："
    "  Get-CimInstance Win32_Process -Filter `"Name='python.exe'`" | "
    "    Where-Object { `$_.CommandLine -match 'signal_review_gate' } | "
    "    ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }"
    "  & `"$EnvFile`""
    "  Start-Process 'C:\Program Files\Python311\python.exe' -ArgumentList `"$PSScriptRoot\signal_review_gate.py`",'--loop'"
    exit 0
}

"重启 signal_review_gate.py..."
# 加载新 env
. $EnvFile

# 停旧进程
$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'signal_review_gate' }
if ($old) {
    $old | ForEach-Object {
        "  Stop pid=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep 2
}

# 启动新进程
Start-Process $JevPy -ArgumentList "$PSScriptRoot\signal_review_gate.py", '--loop' `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$GateLog.review.out" `
    -RedirectStandardError "$GateLog.review.err"

Start-Sleep 12

# ── 5. 验证 ────────────────────────────────────────────────
$np = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'signal_review_gate' }

if ($np) {
    "  ✅ signal_review_gate RUNNING pid=$($np.ProcessId)"
    "--- 最新 3 行日志 ---"
    Get-Content "$GateLog.review.out" -Tail 3 -ErrorAction SilentlyContinue
    ""
    "Tushare token 轮换完成 ✅"
} else {
    Write-Error "signal_review_gate 启动失败，检查日志:"
    Get-Content "$GateLog.review.err" -Tail 10 -ErrorAction SilentlyContinue
    exit 1
}
