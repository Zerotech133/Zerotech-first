# 8091 受控换血脚本（L1/U-7）
# 需管理员 PowerShell 运行：
#   powershell -ExecutionPolicy Bypass -File C:\项目\老田的AI助手\tools\restart_8091_controlled.ps1
# 机制：创建 C:\Temp\PORTAL_API_RESTART.flag → 触发计划任务"AI工作站门户API" → 脚本自清 orphan 后拉起新进程。
# 回滚：若新进程未起，重复运行本脚本或 schtasks /run /tn "AI工作站门户API"。
$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = "C:\Temp\restart_8091_$ts.log"
function Log([string]$m) {
    $line = "$(Get-Date -Format o) $m"
    $line | Tee-Object -FilePath $log -Append | Out-Host
}
function Health8091 {
    try {
        $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8091/health' -TimeoutSec 5
        return [int]$r.StatusCode
    } catch { return 0 }
}

Log "== 8091 受控换血开始 =="
$c = Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue
if (-not $c) { Log 'FAIL: 8091 not listening before restart'; exit 1 }
$old = ($c | Select-Object -First 1 -ExpandProperty OwningProcess)
Log "old pid=$old health=$(Health8091)"

# 换血哨兵（脚本会消费）
New-Item -ItemType File -Force 'C:\Temp\PORTAL_API_RESTART.flag' | Out-Null
$task = 'AI工作站门户API'
$run = & schtasks.exe /run /tn $task 2>&1
Log "schtasks run: $($run -join ' | ')"

$new = $null
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    $c = Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    $pidNow = ($c | Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($pidNow -and $pidNow -ne $old -and (Health8091) -eq 200) { $new = $pidNow; break }
}
if (-not $new) {
    Log "FAIL: new 8091 not healthy (old=$old)"
    Log "ROLLBACK: schtasks /run /tn \"$task\""
    exit 1
}
Log "new pid=$new health=200"

# 代码生效校验（换血后新代码）
$hp = $null
foreach ($u in @('http://127.0.0.1:8734/health-portal', 'http://127.0.0.1:8091/health-portal')) {
    try { $r = Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 8; if ($r.StatusCode -eq 200) { $hp = $r.Content; break } } catch {}
}
if ($hp) {
    $ok8105 = $hp -match '智策API\(8105\)'
    $no5175 = -not ($hp -match '5175')
    Log "code liveness: 8105=$ok8105 no5175=$no5175"
    if (-not ($ok8105 -and $no5175)) { Log 'WARN: code liveness not confirmed; check portal/logs'; exit 2 }
} else {
    Log 'WARN: /health-portal not reachable; restart health OK, code liveness unverified'
    exit 2
}
Log 'OK: 8091 换血完成'
exit 0
