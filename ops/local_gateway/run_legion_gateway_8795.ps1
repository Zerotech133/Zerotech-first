# run_legion_gateway_8795.ps1 — 8795 本地网关常驻包装器
# L2 批准：老田 2026-10-27（「批准 L2：8795 常驻（登录触发 + 失败重启）」）
# 2026-10-31 P119 审计修复（WO-GUANYU-P119-20261031）：
#   任务触发周期 5min + MultipleInstancesPolicy=IgnoreNew，旧实现把 python serve.py
#   前台挂在任务实例里 → 实例永不退出 → 后续重复触发被调度器拒绝（0x800710E0
#   假红 + Missed=19）。改为：
#     ① 先 TCP 探活 8795，已在服务 → 秒退 0（不占任务槽）；
#     ② 未监听 → detached 启动 gateway（日志追加），最多等 20s 端口就绪：
#        成功 exit 0、失败 exit 1（下一轮重复触发自动重试，等价 keepalive）。
#   任务定义/端口/绑定不变（仍仅 127.0.0.1:8795）。
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\agent_platform\gateway\serve.py'
$log = 'C:\Temp\legion_gateway_8795.log'
$port = 8795

function Test-GatewayListening([int]$p) {
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(800)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($client) { try { $client.Close() } catch { } }
    }
}

# ① 已有实例在服务 → 秒退 0（避免任务实例占槽/重复绑定）
if (Test-GatewayListening $port) { exit 0 }

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ② detached 启动，日志追加（cmd 重定向保留历史；python 进程独立于任务实例）
$cmdline = '"{0}" -X utf8 "{1}" >> "{2}" 2>&1' -f $py, $script, $log
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmdline -WindowStyle Hidden | Out-Null

# 等端口就绪（最多 20s）：成功即退 0；失败退 1，由 5min 重复触发重试
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-GatewayListening $port) { exit 0 }
}
exit 1
