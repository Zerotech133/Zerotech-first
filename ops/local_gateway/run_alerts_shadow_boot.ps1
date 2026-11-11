# run_alerts_shadow_boot.ps1 - alerts_shadow.py (8001) watchdog wrapper
# 2026-11-11 FIX: python path changed to ASCII-only Python311 (中文路径在 PS5 GBK 读脚本时乱码)

$py = 'C:\Program Files\Python311\pythonw.exe'
$script = 'C:\项目\老田的AI助手\tools\alerts_shadow.py'
$port = 8001
$log = 'C:\Temp\alerts_shadow_boot.log'

function Write-Log($msg) {
    $ts = Get-Date -Format 'MM-dd HH:mm:ss'
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

if (-not (Test-Path $py)) { Write-Log "FAIL python not found: $py"; exit 1 }

try {
    $proc = Start-Process -FilePath $py -ArgumentList @('-X','utf8',$script,'--port',"$port") `
        -WindowStyle Hidden -PassThru -ErrorAction Stop
    Write-Log "STARTED pid=$($proc.Id)"
} catch {
    Write-Log "FAIL Start-Process: $($_.Exception.Message)"; exit 1
}

for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-Port $port) { Write-Log "port ready after $($i+1)s - exit 0"; exit 0 }
}
Write-Log "port NOT ready after 15s - exit 1"
exit 1
