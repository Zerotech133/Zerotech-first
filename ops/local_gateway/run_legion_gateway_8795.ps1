# run_legion_gateway_8795.ps1 - gateway watchdog wrapper (8795)
# 2026-11-10: full logging (run/probe/start/ready/fail/exit)

$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\agent_platform\gateway\serve.py'
$log = 'C:\Temp\legion_gateway_8795.log'
$port = 8795

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

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

Write-Log "=== run (port=$port) ==="

if (Test-GatewayListening $port) {
    Write-Log "probe OK already listening - exit 0"
    exit 0
}
Write-Log "probe FAIL not listening - starting..."

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$stdoutLog = 'C:\Temp\legion_gateway_8795.stdout.log'
$stderrLog = 'C:\Temp\legion_gateway_8795.stderr.log'
$proc = Start-Process -FilePath $py -ArgumentList @('-X','utf8',$script) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog `
    -PassThru
Write-Log "STARTED pid=$($proc.Id)"

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 1000
    if (Test-GatewayListening $port) {
        Write-Log "port ready after $($i+1)s - exit 0"
        exit 0
    }
}
Write-Log "port NOT ready after 20s - exit 1"
exit 1
