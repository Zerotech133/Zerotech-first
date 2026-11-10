# run_alerts_shadow_boot.ps1 — alerts_shadow.py (8001) watchdog wrapper
# 先 TCP 探活 8001，已监听 -> 秒退 0；未监听 -> 启动并等 15s
# 2026-11-10 根治：原始任务直接跑 pythonw.exe 无探活 → 周期性触发撞端口
#   STATUS_SHARING_VIOLATION (2147946720)。改为本 PS wrapper 先探活再起。
# 2026-11-10 加全流程日志（run/probe/start/ready/fail/exit）。

$py = 'C:\项目\py312\pythonw.exe'
$script = 'C:\项目\老田的AI助手\tools\alerts_shadow.py'
$port = 8001
$log = 'C:\Temp\alerts_shadow_boot.log'

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

function Test-Port([int]$p) {
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(800)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch { return $false }
    finally { if ($client) { try { $client.Close() } catch { } } }
}

Write-Log "=== run (port=$port) ==="

if (Test-Port $port) {
    Write-Log "probe OK already listening - exit 0"
    exit 0
}
Write-Log "probe FAIL not listening - starting..."

$proc = Start-Process -FilePath $py -ArgumentList @('-X','utf8',$script,'--port',"$port") `
    -WindowStyle Hidden -PassThru
Write-Log "STARTED pid=$($proc.Id)"

for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-Port $port) {
        Write-Log "port ready after $($i+1)s - exit 0"
        exit 0
    }
}
Write-Log "port NOT ready after 15s - exit 1"
exit 1
