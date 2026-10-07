# zhice-os-v2 启动/停止/守护脚本（集成到 E:\老田的AI助手）
# 用法:
#   .\zhice.ps1 start    # 数据库容器 + API(8102) + 调度器 + 前端(5175) + Jev 四件套
#   .\zhice.ps1 stop     # 全部停止（先优雅后强杀）
#   .\zhice.ps1 status   # 查看状态
#   .\zhice.ps1 watchdog # 拉起缺失组件（配合计划任务每 5 分钟自愈，动作记录见 zhice-watchdog.log）
param([Parameter(Position=0)][ValidateSet('start','stop','status','restart','watchdog')][string]$Action='status')

$Code = 'E:\zhice-os-v2-windows-20260920\code'
$Py  = "$Code\.venv\Scripts\python.exe"
$JevPy = "C:\Program Files\Python311\python.exe"   # Jev 四件套产线解释器（双 Python 治理：禁止裸 python，回退只改此行）
$ApiLog  = "$env:TEMP\zhice-api.log"
$SchedLog = "$env:TEMP\zhice-scheduler.log"
$FeLog    = "$env:TEMP\zhice-frontend.log"
$GateLog  = "$env:TEMP\zhice-jev-gate.log"
$WatchdogLog = "$env:TEMP\zhice-watchdog.log"

function Get-Procs {
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'zhice|jev_gate|jev_qd_bridge|signal_review_gate|lark_card_handler' }
}

function Wait-DbReady([int]$Sec = 30) {
  $t0 = Get-Date
  while (((Get-Date) - $t0).TotalSeconds -lt $Sec) {
    if ((docker exec zhice-db pg_isready -U zhice 2>$null) -match 'accepting') { return $true }
    Start-Sleep 2
  }
  return $false
}

function Ensure-Db {
  if (-not (docker ps --filter name=zhice-db -q)) { docker start zhice-db 2>$null | Out-Null }
  return (Wait-DbReady)
}

function Clear-ViteCache {
  # 强杀 vite 后 .vite-temp 和 .vite/deps 残留会导致 vite 写新配置 EPERM。
  # Remove-Item 在该路径偶发静默失败（目录残留），Python rmtree 更可靠，双保险
  foreach ($p in @("$Code\frontend\node_modules\.vite-temp", "$Code\frontend\node_modules\.vite\deps")) {
    Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $p) {
      & $JevPy -c "import shutil; shutil.rmtree(r'$p', ignore_errors=True)"
    }
  }
}

function Start-Api {
  Start-Process $Py -ArgumentList '-m','uvicorn','zhice_os_v2.api.main:app','--host','0.0.0.0','--port','8102' -WorkingDirectory $Code -WindowStyle Hidden -RedirectStandardOutput "$ApiLog.out" -RedirectStandardError "$ApiLog.err"
}
function Start-Scheduler {
  # K93：US 日线源 yfinance（query*.finance.yahoo.com）裸连被 DNS 污染
  # （间歇 Could not resolve host，按字母序前几十只成功后全失败）。
  # 本机 Clash 常驻 7890——端口活着才注入代理（缺席退化直连，不劣于旧态）；
  # 国内源（DataCenter/Tushare/东财）由 Clash 规则分流自动直连，不受影响。
  $proxyOk = $false
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 7890); $proxyOk = $true; $c.Close() } catch {}
  if ($proxyOk) {
    $env:HTTPS_PROXY = 'http://127.0.0.1:7890'
    $env:HTTP_PROXY  = 'http://127.0.0.1:7890'
  }
  Start-Process $Py -ArgumentList '-m','zhice_os_v2.scheduler' -WorkingDirectory $Code -WindowStyle Hidden -RedirectStandardOutput "$SchedLog.out" -RedirectStandardError "$SchedLog.err"
}

function Test-SchedulerAlive {
  # K96：scheduler 假死探测。进程存活≠调度在跑——10-03 04:10~10-04 18:07 进程在而
  # job 全停（CRYPTO hourly 断供 14h，watchdog 进程级检测零发现）。改按业务产出水位判：
  # CRYPTO bar_daily 每小时全量 upsert（37 标的 created_at 恒刷新），max(created_at)
  # 距今超 90min（hourly + 容差）判假死。查询失败/空值 fail-open 返回真——探测自身
  # 故障不误杀（误重启丢挂起态 vs 漏报整站停摆，两害取其轻），进程级检测仍兜底。
  try {
    $ts = docker exec zhice-db psql -U zhice -d zhice_os_v2 -t -A -c "SELECT extract(epoch from max(created_at)) FROM v2_bar_daily WHERE market='CRYPTO'" 2>$null
    if (-not ($ts -match '^\d+(\.\d+)?$')) { return $true }
    return (([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$ts) -lt 5400)
  } catch { return $true }
}

function Start-Frontend {
  Clear-ViteCache
  Start-Process 'npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory "$Code\frontend" -WindowStyle Hidden -RedirectStandardOutput "$FeLog.out" -RedirectStandardError "$FeLog.err"
}

function Start-JevStack {
  # 按需拉起 Jev 四件套中缺失的进程，返回本次启动的组件名列表。
  # 需先加载 jev-gate.env.ps1（JEV_API_KEY 等）。
  $started = @()
  if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'jev_gate' })) {
    Start-Process $JevPy -ArgumentList "$PSScriptRoot\jev-eval\jev_gate.py" -WindowStyle Hidden -RedirectStandardOutput "$GateLog.out" -RedirectStandardError "$GateLog.err"
    $started += 'jev-gate'
  }
  if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'signal_review_gate' })) {
    Start-Process $JevPy -ArgumentList "$PSScriptRoot\jev-eval\signal_review_gate.py",'--loop' -WindowStyle Hidden -RedirectStandardOutput "$GateLog.review.out" -RedirectStandardError "$GateLog.review.err"
    $started += 'review-gate'
  }
  if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'jev_qd_bridge' })) {
    Start-Process $JevPy -ArgumentList "$PSScriptRoot\jev-eval\jev_qd_bridge.py",'--loop' -WindowStyle Hidden -RedirectStandardOutput "$GateLog.qdbridge.out" -RedirectStandardError "$GateLog.qdbridge.err"
    $started += 'qd-bridge'
  }
  if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'lark_card_handler' })) {
    Start-Process $JevPy -ArgumentList "$PSScriptRoot\jev-eval\lark_card_handler.py" -WindowStyle Hidden -RedirectStandardOutput "$GateLog.card.out" -RedirectStandardError "$GateLog.card.err"
    $started += 'card-handler'
  }
  return $started
}

function Get-CardHandlerRestartReason {
  # 飞书事件流自愈判定：返回重启原因（'age'/'ws-down'），健康或进程缺失时返回 $null。
  # age    : 进程年龄超过 6 小时——lark SDK 的 websocket 长连接偶发“假重连”
  #          （TCP 仍 ESTABLISHED、日志也打印 connected，但应用层再也收不到事件，
  #          进程存活类检测完全无法发现），用周期性重建连接把连接年龄封顶兜底。
  # ws-down: bus.log 尾部最后一条 SDK 状态为断连/接收失败且超过 90 秒未恢复 connected。
  $p = Get-Procs | Where-Object { $_.CommandLine -match 'lark_card_handler' } | Select-Object -First 1
  if (-not $p) { return $null }
  try { if (((Get-Date) - $p.CreationDate).TotalSeconds -gt 21600) { return 'age' } } catch {}
  try {
    $bus = Get-ChildItem "$env:USERPROFILE\.lark-cli\events" -Recurse -Filter bus.log -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($bus) {
      $lastGood = $null; $lastBad = $null
      foreach ($ln in (Get-Content $bus.FullName -Tail 50 -ErrorAction SilentlyContinue)) {
        if ($ln -match '^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}).*\[SDK\] connected to wss') {
          $lastGood = [datetime]::ParseExact($Matches[1], 'yyyy/MM/dd HH:mm:ss', $null)
        } elseif ($ln -match '^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}).*(\[SDK ERROR\]|\[SDK\] disconnected)') {
          $lastBad = [datetime]::ParseExact($Matches[1], 'yyyy/MM/dd HH:mm:ss', $null)
        }
      }
      if ($lastBad -and (-not $lastGood -or $lastBad -gt $lastGood) -and (((Get-Date) - $lastBad).TotalSeconds -gt 90)) {
        return 'ws-down'
      }
    }
  } catch {}
  return $null
}

function Stop-CardHandlerTree {
  # /T 连带 lark-cli event consume 与 event _bus 子孙进程一起结束，
  # 避免残留事件总线继续持有僵尸 websocket（只杀 python 父进程会留下孤儿 bus）。
  foreach ($x in @(Get-Procs | Where-Object { $_.CommandLine -match 'lark_card_handler' })) {
    taskkill /PID $x.ProcessId /T /F 2>$null | Out-Null
  }
  Start-Sleep 2
}

switch ($Action) {
  'status' {
    $db = docker ps --filter name=zhice-db --format '{{.Status}}'
    "db      : $(if($db){$db}else{'STOPPED'})"
    $procs = Get-Procs
    $api = $procs | Where-Object { $_.CommandLine -match 'uvicorn' }
    $sch = $procs | Where-Object { $_.CommandLine -match 'scheduler' }
    $fe  = $procs | Where-Object { $_.CommandLine -match 'vite' }
    "api     : $(if($api){"RUNNING pid=$($api.ProcessId)"}else{'STOPPED'})"
    "scheduler: $(if($sch){"RUNNING pid=$($sch.ProcessId)"}else{'STOPPED'})"
    "frontend: $(if($fe){"RUNNING pid=$($fe.ProcessId)"}else{'STOPPED'})"
    $gate  = $procs | Where-Object { $_.CommandLine -match 'jev_gate' }
    $card  = $procs | Where-Object { $_.CommandLine -match 'lark_card_handler' }
    "jev-gate: $(if($gate){"RUNNING pid=$($gate.ProcessId)"}else{'STOPPED'})"
    "card-handler: $(if($card){"RUNNING pid=$($card.ProcessId)"}else{'STOPPED'})"
    try { "api-health: $((Invoke-WebRequest -UseBasicParsing http://localhost:8102/api/v1/health -TimeoutSec 3).StatusCode)" } catch { "api-health: N/A" }
    schtasks /Query /TN "ZhiceWatchdog" *>$null
    "watchdog: $(if($LASTEXITCODE -eq 0){'计划任务已注册（每 5 分钟自愈）'}else{'未注册（schtasks 见 zhice.ps1 头注释）'})"
  }
  'start' {
    if (-not (Ensure-Db)) { Write-Error 'zhice-db 未就绪'; exit 1 }
    'db      : OK (accepting connections)'
    Set-Location $Code
    Start-Api
    Start-Sleep 3
    Start-Scheduler
    Start-Frontend
    # vite 新建配置文件偶发 EPERM（Windows 瞬锁，非残留锁）——启动后检测失败则清理重试
    Start-Sleep 10
    if (-not (Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match 'vite' })) {
      'vite 首启失败（EPERM），清理重试…'
      Start-Frontend
      Start-Sleep 10
    }
    # Jev 四件套（可选）：仅当 jev-gate.env 存在且配置了 JEV_API_KEY 时启动
    if (Test-Path "$PSScriptRoot\jev-eval\jev-gate.env.ps1") {
      & "$PSScriptRoot\jev-eval\jev-gate.env.ps1"  # 加载 $env:JEV_API_KEY 等
      if ($env:JEV_API_KEY) {
        Start-JevStack | ForEach-Object { "${_}: 启动" }
      }
    }
    Start-Sleep 8
    & $PSCommandPath status
    "`n日志: $ApiLog / $SchedLog / $FeLog / $WatchdogLog"
    "前端: http://localhost:5175"
  }
  'stop' {
    # 先尝试优雅关闭（WM_CLOSE，有窗口/可响应的进程能走清理逻辑），2s 后对残留强杀。
    # 各 Python 服务状态均为逐条原子落盘，强杀不丢已确认状态（P0/P1 审查结论）。
    Get-Procs | ForEach-Object { taskkill /PID $_.ProcessId /T 2>$null | Out-Null }
    Start-Sleep 2
    Get-Procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    # 等 node 退出后再清 .vite-temp 和 .vite/deps（3 秒内锁常未释放 → 下次启动 EPERM）
    Start-Sleep 3
    foreach ($p in @("$Code\frontend\node_modules\.vite-temp", "$Code\frontend\node_modules\.vite\deps")) {
      Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue
    }
    docker stop zhice-db 2>$null | Out-Null
    '全部已停止'
  }
  'restart' { & $PSCommandPath stop; Start-Sleep 3; & $PSCommandPath start }
  'watchdog' {
    # 自愈入口：缺什么补什么；全部健康时静默。计划任务注册（管理员 PowerShell 一次性执行）：
    #   schtasks /Create /TN ZhiceWatchdog /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\zhice.ps1`" watchdog" /SC MINUTE /MO 5 /F
    $fixed = @()
    if (-not (docker ps --filter name=zhice-db -q)) {
      docker start zhice-db 2>$null | Out-Null
      $fixed += 'db(容器)'
    }
    if (-not (Wait-DbReady 15)) {
      # db 未就绪（可能刚被拉起还在恢复）：本轮到此为止，其余组件下轮自愈
      if ($fixed) { Add-Content $WatchdogLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] watchdog 拉起: $($fixed -join ', ')（db 尚未就绪，其余下轮）" }
      return
    }
    Set-Location $Code
    # K96 假死探测：scheduler 进程在但产出水位停滞 → 树杀，交由下方「缺失拉起」重启
    # （新进程+新连接池；startup job 会立即补跑全市场 fetch_daily，幂等无害）。
    $sch = Get-Procs | Where-Object { $_.CommandLine -match 'scheduler' }
    if ($sch -and -not (Test-SchedulerAlive)) {
      $sch | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }
      Start-Sleep 2
      $fixed += 'scheduler-restart(假死)'
    }
    if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'uvicorn' }))  { Start-Api;        $fixed += 'api' }
    if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'scheduler' })) { Start-Scheduler;  $fixed += 'scheduler' }
    if (-not (Get-Procs | Where-Object { $_.CommandLine -match 'vite' }))      { Start-Frontend;   $fixed += 'frontend' }
    if (Test-Path "$PSScriptRoot\jev-eval\jev-gate.env.ps1") {
      & "$PSScriptRoot\jev-eval\jev-gate.env.ps1"
      if ($env:JEV_API_KEY) {
        # 先做 card-handler 事件流健康自愈（年龄 6h 封顶 / ws 断连 90s 未恢复），
        # 杀掉后由 Start-JevStack 按“缺失拉起”逻辑重建全新 websocket 连接。
        $cardReason = Get-CardHandlerRestartReason
        if ($cardReason) {
          Stop-CardHandlerTree
          $fixed += "card-handler-restart($cardReason)"
        }
        $fixed += Start-JevStack
      }
    }
    if ($fixed.Count) {
      $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] watchdog 拉起: $($fixed -join ', ')"
      Add-Content $WatchdogLog $line
      $line
    }
  }
}
