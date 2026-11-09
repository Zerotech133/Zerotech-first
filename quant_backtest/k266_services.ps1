# K266 文档服务自愈脚本（计划任务 K266-DocServices 每 5 分钟触发）
# 职责：① 拉起 clip_watch 常驻（单实例检查） ② doc_ingest --once 幂等单扫
#       ③ M3 结构化（幂等跳过已判 doc，产出 structure_shadow tags → /kg/doc 词云）
$ErrorActionPreference = 'SilentlyContinue'
# 2026-10-16 换机修复：E: 盘不存在 → C:\aios junction（原硬编码 C:\项目\老田的AI助手）
$root = 'C:\aios'
$log = 'C:\Temp\k266_services.log'

function Log($msg) {
    "$(Get-Date -Format 'MM-dd HH:mm:ss')  $msg" | Out-File $log -Append -Encoding utf8
}

# --- 1) clip_watch 单实例拉起（pythonw 无窗口常驻） ---
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'k266_clip_watch' }
if (-not $running) {
    Start-Process -FilePath 'C://Program Files\Python311\pythonw.exe' -ArgumentList '-X','utf8',"$root\quant_backtest\k266_clip_watch.py" `
        -WorkingDirectory "$root\quant_backtest" -WindowStyle Hidden
    Log "clip_watch: 拉起（先前不在岗）"
} else {
    Log "clip_watch: 在岗 PID=$($running.ProcessId -join ',')"
}

# --- 2) doc_ingest 幂等单扫（registry 追踪，无新文件时空转退出） ---
$env:PADDLE_PDX_CACHE_HOME = 'C:\Temp\paddlex'
$env:PYTHONPYCACHEPREFIX   = 'C:\Temp\pycache'
$env:MODELSCOPE_CACHE      = 'C:\Temp\modelscope'
$out = & 'C:\Program Files\Python311\python.exe' -X utf8 "$root\quant_backtest\k266_doc_ingest.py" --once 2>&1
$rc = $LASTEXITCODE
if ($out) { $out | Out-File 'C:\Temp\k266_ingest.log' -Append -Encoding utf8 }
Log "ingest --once rc=$rc $($out -join ' | ')"

# --- 3) M3 结构化（幂等：只判新增 doc；Ollama qwen3.5:9b → tags 入 structure_shadow） ---
# 仅当本扫有新增（输出含 "OK d"）才跑，空转不占 Ollama；单实例守卫防并发重判
# 2026-10-25 K-ORG-20261025-JEVNIGHT：夜间 JEV 独占期暂停 M3。
# 旗文件 C:\Temp\k266_m3.pause 由 tools\jev_night.py 写入/删除；
# 删旗后下一 tick 自动恢复（"OK d" 判定不变，只多一道暂停闸）。
$m3Pause = 'C:\Temp\k266_m3.pause'

function Get-LockPid([string]$path) {
    try {
        $j = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j -and $j.pid) { return [int]$j.pid }
    } catch { }
    return 0
}
function Test-PidAlive([int]$procId) {
    if ($procId -le 0) { return $false }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) { return $true }
    # 2026-10-27 OS-FIX-K266GUARD-A04：S4U/异会话下 Get-Process 可能看不到存活进程，
    # 用 tasklist 兜底再判一次（仍看不到则交由锁文件 mtime 心跳作第二证据）。
    try {
        $tl = & tasklist /FI "PID eq $procId" /FO CSV /NH 2>$null
        if ($tl -and ("$tl" -match '^\s*"')) { return $true }
    } catch { }
    return $false
}
function Test-LockHeartbeat([string]$path, [int]$maxAgeMin = 15) {
    # 锁文件 mtime = 持有者心跳（jev_night 每轮 touch）；<15min 视为仍活（第二证据）。
    # 目的：S4U pid 可见性不足时不误删暂停旗；过期锁仍按「无 owner」清理。
    try {
        $f = Get-Item -LiteralPath $path -ErrorAction Stop
        return (((Get-Date) - $f.LastWriteTime).TotalMinutes -lt $maxAgeMin)
    } catch { return $false }
}
function Get-M3OwnerState([string]$Root, [string]$FallbackLock) {
    # owner 判定（2026-10-27 A-04）：主锁 2 条 + jev_night fallback 路径。
    # fallback 来源=tools/jev_night.py LOCK_FALLBACK_PATH（默认 C:\项目\.locks\jev_night.lock，
    # 可用 env JEV_NIGHT_LOCK_FALLBACK 覆盖；正常不触发 fallback，见 jev_night.py:64-70）。
    # 每条候选：pid 存活 或 锁 mtime 心跳 <15min 即视为仍持有；命中任一即返回。
    $cands = @(
        @{ Kind = 'night_batch'; Path = (Join-Path $Root 'locks\night_batch.lock') },
        @{ Kind = 'jev_night'; Path = (Join-Path $Root 'locks\jev_night.lock') },
        @{ Kind = 'jev_night.fallback'; Path = $FallbackLock }
    )
    $detail = @()
    foreach ($c in $cands) {
        if (-not (Test-Path -LiteralPath $c.Path)) { continue }
        $lp = Get-LockPid $c.Path
        $alive = (Test-PidAlive $lp) -or (Test-LockHeartbeat $c.Path)
        $detail += ('{0}(pid={1},alive={2})' -f $c.Kind, $lp, $alive)
        if ($alive) { return @{ Alive = $true; Detail = ($detail -join '; ') } }
    }
    return @{ Alive = $false; Detail = ($detail -join '; ') }
}

$m3PauseActive = Test-Path $m3Pause
if ($m3PauseActive) {
    # 2026-10-27 A-04：owner 判定补 jev_night fallback 锁（C:\项目\.locks\jev_night.lock）
    # + 锁心跳第二证据；S4U 下 pid 可见性差时不再误删旗。
    $jnFallback = $env:JEV_NIGHT_LOCK_FALLBACK
    if (-not $jnFallback) { $jnFallback = 'C:\项目\.locks\jev_night.lock' }
    $owner = Get-M3OwnerState -Root $root -FallbackLock $jnFallback
    if ($owner.Alive) {
        Log "m3: 暂停旗存在（$($owner.Detail)），本轮不拉起"
    } else {
        # 2026-10-27 OS-FIX-K266GUARD：任务被强杀（如 schtasks /End）不会走
        # finally，暂停旗残留会让 M3 永久停摆。无存活 owner（含锁心跳）即清理并恢复 M3。
        Remove-Item -LiteralPath $m3Pause -Force -ErrorAction SilentlyContinue
        Log "m3: 陈旧暂停旗已清理（无 night_batch/jev_night 存活 owner；候选=$($owner.Detail)），恢复 M3"
        $m3PauseActive = $false
    }
}
if (-not $m3PauseActive -and "$out" -match 'OK d') {
    $m3run = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'k266_m3_structure' }
    if ($m3run) {
        Log "m3: 已在跑 PID=$($m3run.ProcessId -join ',')，本轮跳过"
    } else {
        # 异步拉起（同 clip_watch 模式）：M3 补积压可达数十分钟，同步等待会阻塞
        # 本 tick 的后续环节并吞掉 12:06-12:46 全部调度窗（12-08 实测 46 分钟）
        # 2026-10-20 错峰闸门（T-Q10, 老田拍板）：避开雪球分类 08/09/10 整点班窗
        # 2026-11-06 交互让位闸门3（K266-PROBE-YIELD，老田批准启用）：JEV judge/人类
        # 交互延迟超 6s 时 M3 让位交还锁（睡 60s 重探；累计让位 900s 本轮收工，
        # k266_services 5 分钟一轮自愈接续）。当日 11-06 实证：M3 16k ctx 长批独占
        # Ollama 单槽队列 → JEV judge 60s 超时 → 146 积压（R3 根因）。
        $env:K266_PEAK_SHIFT   = '1'
        $env:K266_PROBE_YIELD  = '1'
        Start-Process -FilePath 'C://Program Files\Python311\python.exe' -ArgumentList '-X','utf8',"$root\quant_backtest\k266_m3_structure.py" `
            -WorkingDirectory "$root\quant_backtest" -WindowStyle Hidden `
            -RedirectStandardOutput 'C:\Temp\k266_m3.log' -RedirectStandardError 'C:\Temp\k266_m3_err.log'
        Log "m3: 异步拉起（进度见影子增长，结束见 k266_m3.log）"
    }
}
