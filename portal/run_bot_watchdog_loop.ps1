# FeishuBotWatchdog wrapper — 自循环守护版（2026-10-24 加固；2026-10-27 父子进程感知修复）
# 不用计划任务，自己 while 循环每 60s 检查一次；AgentWatchdog 每 5min 兜底重启本 loop。
# 加固点：
#   1) 进程判活之外加「心跳判活」——进程在但心跳 >15min 停滞（假死/WS 假在线）→ 强杀重启；
#   2) 子进程 stdout/stderr 落独立日志（原来丢进隐藏窗口，WS 连接日志不可见）；
#   3) 语音输入环境变量在此显式注入，保证所有拉起路径一致；
#   4) 2026-10-27：bot 会派生「同命令行子进程」（parent+child）。逻辑实例=进程树根，
#      去重/重启必须按整棵树处理，否则父子对会被当成 2 个实例 → 杀子→父退→重启风暴。
param([switch]$SelfTest)
$ErrorActionPreference = "Continue"

$py_src = "C:\aios\CowAgent\venv\Scripts\python.exe"
$bot    = "C:\aios\portal\feishu_bot_ws.py"
$log    = "C:\aios\portal\logs\feishu_bot_task.log"
$hb     = "C:\aios\portal\logs\feishu_bot_heartbeat.txt"
$out    = "C:\aios\portal\logs\feishu_bot_stdout.log"
$err    = "C:\aios\portal\logs\feishu_bot_stderr.log"
$INTERVAL_SEC = 60
$HEARTBEAT_MAX_AGE_SEC = 900

# 语音输入（本地 faster-whisper）：显式注入，避免依赖用户环境变量
$env:VOICE_INGEST_ENABLED = "1"
$env:VOICE_ASR_PROVIDER   = "local"
$env:VOICE_ASR_MODEL      = "C:\aios\models\faster-whisper-small"
$env:VOICE_AUTO_EXECUTE   = "1"
$env:VOICE_ASR_TIMEOUT    = "90"
# HuggingFace 镜像：本地 whisper 模型首次下载用（国内可直连 hf-mirror）
$env:HF_ENDPOINT         = "https://hf-mirror.com"

function Write-Log($msg) {
    $ts = Get-Date -Format "MM-dd HH:mm:ss"
    "$ts $msg" | Out-File -Append -Encoding utf8 $log
}

function Get-BotProcessesAll {
    @(Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*feishu_bot_ws.py*' })
}

# 逻辑 bot 实例 = 进程树根：排除「父进程也是 bot 进程」的同命令行子进程。
# 说明：bot 运行时会派生同 cmdline 子进程（父子对），二者只算 1 个逻辑实例。
function Get-BotProcRoots {
    param($Processes = $null)
    if ($null -eq $Processes) { $all = @(Get-BotProcessesAll) } else { $all = @($Processes) }
    if ($all.Count -eq 0) { return @() }
    $pids = @($all | ForEach-Object { [int]$_.ProcessId })
    $roots = @($all | Where-Object { $pids -notcontains [int]$_.ParentProcessId })
    if ($roots.Count -eq 0) {
        # 异常：全部互为父子/父缺失（cmdline 枚举残缺）→ 退回最早一个，避免误判为 0 个而重复拉起
        $roots = @($all | Sort-Object CreationDate | Select-Object -First 1)
    }
    return $roots
}

# 取以 RootPid 为根的整棵 bot 树（含同 cmdline 子进程）
function Get-BotTreePids {
    param([int]$RootPid, $Processes = $null)
    if ($null -eq $Processes) { $all = @(Get-BotProcessesAll) } else { $all = @($Processes) }
    $ids = New-Object System.Collections.Generic.List[int]
    $ids.Add([int]$RootPid)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($p in $all) {
            if (($ids -contains [int]$p.ParentProcessId) -and -not ($ids -contains [int]$p.ProcessId)) {
                $ids.Add([int]$p.ProcessId)
                $changed = $true
            }
        }
    }
    return $ids
}

function Stop-BotTree {
    param([int]$RootPid, $Processes = $null)
    foreach ($id in (Get-BotTreePids -RootPid $RootPid -Processes $Processes)) {
        try { Stop-Process -Id $id -Force -ErrorAction Stop }
        catch { }
    }
}

if ($SelfTest) {
    $mk = {
        param($procId, $ppid, $ageMin)
        [pscustomobject]@{
            ProcessId = [int]$procId
            ParentProcessId = [int]$ppid
            CreationDate = (Get-Date).AddMinutes(-1 * $ageMin)
            CommandLine = 'python.exe -u -X utf8 C:\aios\portal\feishu_bot_ws.py'
        }
    }
    $t1 = @((& $mk 100 1 5), (& $mk 101 100 5))
    $r1 = @(Get-BotProcRoots -Processes $t1)
    $tree1 = @(Get-BotTreePids -RootPid 100 -Processes $t1)
    $t2 = @((& $mk 200 1 5), (& $mk 201 200 5), (& $mk 300 1 2), (& $mk 301 300 2))
    $r2 = @(Get-BotProcRoots -Processes $t2)
    $t3 = @((& $mk 400 999 1))
    $r3 = @(Get-BotProcRoots -Processes $t3)
    $ok = ($r1.Count -eq 1 -and $r1[0].ProcessId -eq 100 -and
           $tree1.Count -eq 2 -and ($tree1 -contains 100) -and ($tree1 -contains 101) -and
           $r2.Count -eq 2 -and
           $r3.Count -eq 1 -and $r3[0].ProcessId -eq 400)
    if ($ok) {
        Write-Host "SELFTEST PASS: parent-child tree -> 1 root; two trees -> 2 roots; orphan -> root"
        exit 0
    }
    Write-Host ("SELFTEST FAIL: r1={0} tree1={1} r2={2} r3={3}" -f $r1.Count, $tree1.Count, $r2.Count, $r3.Count)
    exit 1
}

# 2026-10-25：看护循环单实例锁（进程生命期持有；第二个循环抢不到直接退出）。
# 与进程枚举去重互补：枚举在 S4U/Session0 可能漏，命名互斥更可靠。
$script:LOOP_MUTEX = $null
try {
    $script:LOOP_MUTEX = New-Object System.Threading.Mutex($false, 'Global\FeishuBotLoop')
    if (-not $script:LOOP_MUTEX.WaitOne(0)) {
        Write-Log "another watchdog loop holds mutex — exit"
        exit 0
    }
} catch {
    Write-Log ("loop mutex unavailable: {0}" -f $_.Exception.Message)
}

Write-Log "=== watchdog LOOP start (interval=${INTERVAL_SEC}s, heartbeatMax=${HEARTBEAT_MAX_AGE_SEC}s) ==="
Write-Log ("env-check: ps={0} voiceEnabled={1} provider={2} model={3}" -f $PSVersionTable.PSVersion, $env:VOICE_INGEST_ENABLED, $env:VOICE_ASR_PROVIDER, $env:VOICE_ASR_MODEL)
# 记录自身 PID：start_bot_watchdog_once.ps1 用它幂等判活（比 cmdline 模糊匹配可靠）
$PID | Out-File -Encoding ascii "C:\aios\portal\logs\bot_watchdog.pid"

# 去重：已有其它看护循环在跑 → 直接退出（防计划任务每 5 分钟叠加出"循环风暴"）
$others = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -like '*-File*C:\aios\portal\run_bot_watchdog_loop.ps1*' -and
        $_.CommandLine -notlike '*start_bot_watchdog_once*'
    })
if ($others.Count -gt 0) {
    Write-Log ("duplicate loop detected ({0} 个在跑) — exit" -f $others.Count)
    exit 0
}

if (-not (Test-Path -LiteralPath $py_src)) { Write-Log "ABORT python not found [$py_src]"; exit 1 }
$py = (Get-Item -LiteralPath $py_src).FullName

while ($true) {
    try {
        # 逻辑实例（进程树根）；父子对只算 1 个
        $alive = @(Get-BotProcRoots)
        $stale = $false
        if ($alive.Count -gt 0) {
            $oldest = ($alive | Sort-Object CreationDate | Select-Object -First 1).CreationDate
            $procAge = if ($oldest) { ((Get-Date) - $oldest).TotalSeconds } else { 0 }
            # 只有「进程已活过阈值」才看心跳：新拉起的实例还没来得及写心跳，
            # 不能立刻判死（历史故障：旧心跳文件让新实例被连环击杀 → 风暴）。
            if ($procAge -gt $HEARTBEAT_MAX_AGE_SEC) {
                if (Test-Path -LiteralPath $hb) {
                    $hbAge = ((Get-Date) - (Get-Item -LiteralPath $hb).LastWriteTime).TotalSeconds
                    if ($hbAge -gt $HEARTBEAT_MAX_AGE_SEC) { $stale = $true }
                } else {
                    $stale = $true
                }
            }
        }
        # 2026-10-25 多实例自愈（2026-10-27 修正）：逻辑实例多于 1 个才收敛；
        # 保留最早实例的整棵树，其余实例按「整树」杀掉（绝不杀保留实例的子进程）。
        if ($alive.Count -gt 1) {
            $sorted = @($alive | Sort-Object CreationDate)
            $keep = $sorted[0]
            Write-Log ("MULTI INSTANCE ({0} roots) — keep root pid={1}, kill other trees" -f $alive.Count, $keep.ProcessId)
            foreach ($p in ($sorted | Select-Object -Skip 1)) {
                Write-Log ("kill tree root pid=$($p.ProcessId)")
                Stop-BotTree -RootPid ([int]$p.ProcessId)
            }
            $alive = @($keep)
        }
        if ($stale) {
            Write-Log "HEARTBEAT STALE > ${HEARTBEAT_MAX_AGE_SEC}s — killing $($alive.Count) bot tree(s) and restarting"
            foreach ($p in $alive) {
                Write-Log ("kill tree root pid=$($p.ProcessId)")
                Stop-BotTree -RootPid ([int]$p.ProcessId)
            }
            Start-Sleep -Seconds 2
            $alive = @(Get-BotProcRoots)
        }
        if ($alive.Count -eq 0) {
            # 2026-10-25 双实例修复：启动前抢全局互斥，二次判活
            # （loop + 计划任务 watchdog 同秒都判死会各拉一个 → 双 bot 双回复）
            $mutex = $null
            $got = $false
            try {
                $mutex = New-Object System.Threading.Mutex($false, 'Global\FeishuBotStart')
                $got = $mutex.WaitOne(0)
            } catch { $got = $false }
            if (-not $got) {
                Write-Log "start mutex busy — skip this round"
            } else {
                try {
                    $alive2 = @(Get-BotProcRoots)
                    if ($alive2.Count -gt 0) {
                        Write-Log "another instance started by peer — skip"
                    } else {
                        Write-Log "BOT DEAD — starting..."
                        try {
                            $proc = Start-Process -FilePath $py `
                                -ArgumentList @("-u", "-X", "utf8", $bot) `
                                -WorkingDirectory "C:\aios\portal" `
                                -WindowStyle Hidden `
                                -RedirectStandardOutput $out `
                                -RedirectStandardError $err `
                                -PassThru
                            if ($proc) { Write-Log "STARTED pid=$($proc.Id)" } else { Write-Log "FAIL: Start-Process null" }
                        } catch {
                            Write-Log "FAIL: $($_.Exception.Message)"
                        }
                    }
                } finally {
                    try { $mutex.ReleaseMutex() } catch { }
                    try { $mutex.Dispose() } catch { }
                }
            }
        }
    } catch {
        Write-Log "ERROR in loop: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $INTERVAL_SEC
}
