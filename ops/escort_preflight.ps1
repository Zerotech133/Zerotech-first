# escort_preflight.ps1 - 2026-10-19 周一护航只读预检 (K-ORG-190)
# 用途: 16:40 预检基线 + 17:10/17:25/17:35 后逐点复验
# 红线: 本脚本只含 Get-* / 只读查询, 绝不 Start-ScheduledTask / Stop-ScheduledTask / 杀进程 / 写文件
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File ops\escort_preflight.ps1

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shadowDir = 'C:\aios\agent_platform\data\shadow'

Write-Host '============================================='
Write-Host (' 护航预检 @ {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host ' (只读巡检, 无任何写/启停操作)'
Write-Host '============================================='

# --- 1. 三触发点计划任务状态 ---
$tasks = 'AgentShadowDaily', 'AgentMondayCheckpoint', 'AgentChiefPush'
Write-Host ''
Write-Host '[1] 三触发点计划任务 (17:10 / 17:25 / 17:35)'
foreach ($n in $tasks) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    $i = Get-ScheduledTaskInfo -TaskName $n -ErrorAction SilentlyContinue
    Write-Host ('  ---- {0} ----' -f $n)
    if (-not $t -or -not $i) { Write-Host '  !! 任务不存在, 立即升级'; continue }
    $lr = '0x{0:X}' -f $i.LastTaskResult
    $verdict = switch ($i.LastTaskResult) {
        267011  { '正常待跑态(从未到点)' }
        267009  { '运行中' }
        0       { '上次成功' }
        default { '!! 上次运行失败/终止 -> 升级' }
    }
    Write-Host ('  State      : {0}' -f $t.State)
    foreach ($a in $t.Actions) { Write-Host ('  Action     : {0} {1}' -f $a.Execute, $a.Arguments) }
    Write-Host ('  NextRun    : {0}' -f $i.NextRunTime)
    Write-Host ('  LastRun    : {0}' -f $i.LastRunTime)
    Write-Host ('  LastResult : {0}  ({1})' -f $lr, $verdict)
    Write-Host ('  MissedRuns : {0}' -f $i.NumberOfMissedRuns)
}

# --- 2. 关键日志尾部 ---
Write-Host ''
Write-Host '[2] 关键日志尾部 (每文件末 8 行)'
$logs = @(
    @{ Name = 'last_run.log          (AgentShadowDaily 17:10)';        Path = Join-Path $shadowDir 'last_run.log' },
    @{ Name = 'monday_checkpoint.log (AgentMondayCheckpoint 17:25)'; Path = Join-Path $shadowDir 'monday_checkpoint.log' },
    @{ Name = 'chief_last_run.log    (AgentChiefPush 17:35)';      Path = Join-Path $shadowDir 'chief_last_run.log' }
)
foreach ($l in $logs) {
    Write-Host ('  ---- {0} ----' -f $l.Name)
    $f = Get-Item -Path $l.Path -ErrorAction SilentlyContinue
    if (-not $f) { Write-Host '  (文件不存在 = 该任务从未跑过, 属正常待跑态)'; continue }
    Write-Host ('  LastWrite : {0}  Size: {1}B' -f $f.LastWriteTime, $f.Length)
    Get-Content -Path $l.Path -Tail 8 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('  | {0}' -f $_) }
}

# --- 3. 依赖端口与进程存活 ---
Write-Host ''
Write-Host '[3] 依赖端口/进程存活 (8091 健康总线 / 8094 keepalive / 9899)'
$listens = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8091, 8094, 9899 }
foreach ($p in 8091, 8094, 9899) {
    $hit = $listens | Where-Object { $_.LocalPort -eq $p } | Select-Object -First 1
    if ($hit) {
        $proc = Get-Process -Id $hit.OwningProcess -ErrorAction SilentlyContinue
        Write-Host ('  port {0}: LISTEN  pid={1} {2}' -f $p, $hit.OwningProcess, $proc.ProcessName)
    } else {
        Write-Host ('  port {0}: 无监听 (9899 属既有现状; 8091 无监听 -> 升级)' -f $p)
    }
}

# --- 4. 健康总线只读探测 ---
Write-Host ''
Write-Host '[4] 健康总线只读 GET (超时只记录事实, 不判定不处置)'
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8091/api/execution/health' -UseBasicParsing -TimeoutSec 5
    Write-Host ('  8091 /api/execution/health -> HTTP {0}' -f $r.StatusCode)
} catch {
    Write-Host ('  8091 /api/execution/health -> 探测失败: {0}' -f $_.Exception.Message)
}

# --- 5. 今日复验提示 ---
Write-Host ''
Write-Host '[5] 复验提示'
Write-Host '  LastResult=0x41303 + LastRun=1999/11/30 = 从未到点, 正常待跑态, 不是故障'
Write-Host '  判卡: 17:10/17:25/17:35 各 +10 分钟仍未触发 -> 升级'
Write-Host '  红线: 绝不手动跑 AgentShadowDaily; 只观察不干预'
Write-Host '============================================='
Write-Host ' 预检完成'
Write-Host '============================================='
