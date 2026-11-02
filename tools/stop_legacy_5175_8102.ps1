# 关停 L1 遗留端口 5175/8102（管理员运行）
#   powershell -ExecutionPolicy Bypass -File C:\项目\老田的AI助手\tools\stop_legacy_5175_8102.ps1
# 机制：按监听端口动态取 PID（不写死），树杀后循环验证；不触碰 8105/8091/8734。
param([switch]$Quiet)
$ErrorActionPreference = 'Continue'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = "C:\Temp\stop_legacy_5175_8102_$ts.log"
function Log([string]$m) { $line = "$(Get-Date -Format o) $m"; $line | Tee-Object -FilePath $log -Append | Out-Host }

if ($Quiet) {
    $any = @(5175,8102 | Where-Object { @(Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue).Count -gt 0 })
    if ($any.Count -eq 0) { exit 0 }
}
Log '== L1 遗留端口关停 =='
$killed = @()
foreach ($port in 5175, 8102) {
    for ($round = 1; $round -le 3; $round++) {
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) { Log "${port}: off (round $round)"; break }
        $pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($p in $pids) {
            Log "${port}: kill pid=$p (round $round)"
            & taskkill.exe /PID $p /T /F 2>&1 | ForEach-Object { Log ($_ | Out-String).Trim() }
            $killed += $p
        }
        Start-Sleep -Seconds 2
        $left = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($left.Count -eq 0) { Log "${port}: off after kill"; break }
        if ($round -eq 3) { Log "${port}: STILL LISTENING (need elevation or source stop)"; exit 1 }
    }
}

Start-Sleep -Seconds 2
$bad = @()
foreach ($port in 5175, 8102) {
    if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { $bad += $port }
}
if ($bad.Count -gt 0) { Log ("FAIL: still listening " + ($bad -join ',')); exit 1 }

# 验证唯一入口链路
foreach ($u in @('/quant/', '/quant-api/api/v1/health', '/probe/zhice')) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing ("http://127.0.0.1:8734" + $u) -MaximumRedirection 0 -TimeoutSec 6 -ErrorAction Stop
        Log "probe ${u}: $($r.StatusCode)"
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        Log "probe ${u}: $code"
    }
}
Log 'OK: 5175/8102 off; 唯一入口 8734'
exit 0
