# JEV service stack watchdog (restored 2026-10-26, K-ORG-20261026-JEVSTACK)
# ASCII only. Pull-up-if-missing for the Jev triplet, port-based liveness.
# 2026-10-26 OS-FIX-W4FINAL：card-handler(9104) 已随 W3 统一分发退役，JevStack
# 不再拉起它；若外部仍有残留监听则尽力停掉（W4 验收要求 9104=down）。
# Scheduled task: \JevStack (every 10 min, S4U). Log: logs\jev-stack.log
# Status snapshot: logs\jev-stack-status.json
# Exit: 0 = all up OR only externally-blocked components; 1 = internal start failure.
param(
    [switch]$Status,
    [switch]$Force,
    # 2026-11-05：9104 card-handler 实际仍在服务中（jev_card_actions_total 有计数），
    # 每次拉起都无脑 taskkill 会让飞书卡片「已处理/忽略」回调失效。改为显式开关，
    # 默认不动 9104；-Force 仍会连带清理（等价旧行为）。
    [switch]$StopRetired
)
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'jev-stack.log'
$ProcOut = Join-Path $LogDir 'jev-proc'
$StatusFile = Join-Path $LogDir 'jev-stack-status.json'
$BusDir = Join-Path $env:USERPROFILE '.lark-cli\events'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-Log([string]$Msg) {
    $line = '[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] ' + $Msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

# 2026-11-05 JEVSTACK4：环境块大小写重复修复。
# 本机会话环境块可同时存在 HTTPS_PROXY 与 https_proxy（沙箱/会话注入叠在用户环境之上），
# Start-Process 克隆子进程环境时 .NET 字典大小写不敏感 -> 抛
# 「已添加项。字典中的关键字:“HTTPS_PROXY”」，整段拉起失败（11-05 03:46 断档即此因）。
# 处理：按大小写不敏感去重，保留首个键的原值（实测重复项取值相同，无损）。
# 只改本进程环境，不碰系统/用户级设置。
function Repair-DupEnv {
    $all = [Environment]::GetEnvironmentVariables()
    $keys = @($all.Keys | ForEach-Object { [string]$_ })
    $val = @{}
    foreach ($k in $keys) { $val[$k] = [string]$all[$k] }
    $fixed = @()
    foreach ($k in $keys) {
        $u = $k.ToUpperInvariant()
        if ($k -cne $u) { continue }                     # 只看全大写那一份
        $variants = @($keys | Where-Object { $_ -cne $u -and $_.ToUpperInvariant() -eq $u })
        if ($variants.Count -eq 0) { continue }
        try {
            [Environment]::SetEnvironmentVariable($u, $null, 'Process')
            [Environment]::SetEnvironmentVariable($u, $val[$u], 'Process')
            $fixed += ($u + '(+' + $variants.Count + ')')
        } catch { }
    }
    return $fixed
}
$dupFixed = @(Repair-DupEnv)
if ($dupFixed.Count -gt 0) { Write-Log ('env dedupe: ' + ($dupFixed -join ',')) }

function Test-Port([int]$Port) {
    $conn = $null
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    } catch {
        $conn = $null
    }
    if ($conn) { return $true }
    $hit = netstat -ano | Select-String (':' + $Port + '\s+.*LISTENING')
    return [bool]$hit
}

function Get-PortPid([int]$Port) {
    $conn = $null
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    } catch {
        $conn = $null
    }
    if ($conn) { return [int]$conn.OwningProcess }
    $line = netstat -ano | Select-String (':' + $Port + '\s+.*LISTENING') | Select-Object -First 1
    if ($line) {
        $parts = ($line.Line.Trim() -split '\s+')
        return [int]$parts[-1]
    }
    return 0
}

function Stop-TreeByPort([int]$Port) {
    $procId = Get-PortPid $Port
    if ($procId -gt 0) {
        taskkill /PID $procId /T /F 2>$null | Out-Null
        Start-Sleep -Seconds 2
        return $procId
    }
    return 0
}

# Card handler can keep a zombie websocket: TCP stays ESTABLISHED while the
# SDK silently stops delivering events. Restart by age (>6h) or ws-down (>90s).
function Get-CardRestartReason {
    $procId = Get-PortPid 9104
    if ($procId -le 0) { return $null }
    try {
        $p = Get-Process -Id $procId -ErrorAction Stop
        if (((Get-Date) - $p.StartTime).TotalSeconds -gt 21600) { return 'age' }
    } catch { }
    try {
        $bus = Get-ChildItem $BusDir -Recurse -Filter 'bus.log' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($bus) {
            $lastGood = $null
            $lastBad = $null
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
    } catch { }
    return $null
}

$Components = @(
    @{ Name = 'jev-gate'; Port = 9101; Script = 'jev_gate.py'; Args = @() },
    @{ Name = 'review-gate'; Port = 9102; Script = 'signal_review_gate.py'; Args = @('--loop') },
    @{ Name = 'qd-bridge'; Port = 9103; Script = 'jev_qd_bridge.py'; Args = @('--loop') }
    # W3 退役（2026-10-26 OS-FIX-W4FINAL）：card-handler(9104) 由统一分发替代，
    # 不再随 JevStack 拉起；保留注释便于回滚。
    # @{ Name = 'card-handler'; Port = 9104; Script = 'lark_card_handler.py'; Args = @() }
)

if ($Status) {
    foreach ($c in $Components) {
        $state = 'DOWN'
        if (Test-Port $c.Port) { $state = 'UP   pid=' + (Get-PortPid $c.Port) }
        '  {0,-14} port {1}: {2}' -f $c.Name, $c.Port, $state
    }
    exit 0
}

# 1) Load credentials/environment first (gate + card need them).
$envScript = Join-Path $Root 'jev-gate.env.ps1'
if (Test-Path $envScript) {
    try {
        . $envScript
    } catch {
        Write-Log ('ERROR env load failed: ' + $_.Exception.Message)
        exit 1
    }
} else {
    Write-Log 'WARN jev-gate.env.ps1 missing (continue with inherited env)'
}

# 2) Optional full restart.
if ($Force) {
    $killed = @()
    foreach ($c in $Components) {
        if (Test-Port $c.Port) {
            $oldPid = Stop-TreeByPort $c.Port
            if ($oldPid -gt 0) { $killed += $oldPid }
        }
    }
    if ($killed.Count -gt 0) {
        Write-Log ('force-restart: killed ' + $killed.Count + ' (' + ($killed -join ',') + ')')
    }
}

# 3) 9104 card-handler：默认不清理（2026-11-05 实测它仍在 serving，metric
# jev_card_actions_total 有计数，taskkill 会打断飞书卡片按钮回调）。
# 仅 -Force 或显式 -StopRetired 时才按旧 W4 口径停掉。
if (($Force -or $StopRetired) -and (Test-Port 9104)) {
    $oldPid = Stop-TreeByPort 9104
    Write-Log ('retired card-handler(9104) stopped pid=' + $oldPid)
}

# 4) External-block backoff (2026-10-26 JEVSTACK3, ASCII only).
# If a component stays down for >=6 consecutive runs (about 1 hour) AND the last run
# classified it as "external" (platform-side blocker), retry only once per hour instead
# of every 10 minutes. Internal failures and healthy components keep the normal cadence.
$prevStreaks = @{}
$prevAttempt = @{}
try {
    if (Test-Path $StatusFile) {
        $prev = Get-Content $StatusFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($prev.streaks) { foreach ($k in $prev.streaks.PSObject.Properties.Name) { $prevStreaks[$k] = [int]$prev.streaks.$k } }
        if ($prev.last_attempt) { foreach ($k in $prev.last_attempt.PSObject.Properties.Name) { $prevAttempt[$k] = [double]$prev.last_attempt.$k } }
    }
} catch { Write-Log ("WARN read prev status failed: " + $_.Exception.Message) }
$nowTs = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$backoffSkip = @{}
foreach ($c in $Components) {
    if (Test-Port $c.Port) { continue }
    $prevStreak = if ($prevStreaks.ContainsKey($c.Name)) { [int]$prevStreaks[$c.Name] } else { 0 }
    if ($prevStreak -ge 6) {
        $lastTry = if ($prevAttempt.ContainsKey($c.Name)) { [double]$prevAttempt[$c.Name] } else { 0.0 }
        if (($nowTs - $lastTry) -lt 3600) {
            $backoffSkip[$c.Name] = $true
        } else {
            $prevAttempt[$c.Name] = $nowTs
        }
    }
}

# 5) Pull up missing components.
$started = @()
$failed = @()
foreach ($c in $Components) {
    if (Test-Port $c.Port) { continue }
    if ($backoffSkip.ContainsKey($c.Name)) {
        Write-Log ("backoff skip: " + $c.Name + " (external streak=" + $prevStreaks[$c.Name] + ", retry hourly)")
        continue
    }
    $out = $ProcOut + '.' + $c.Name + '.out'
    $err = $ProcOut + '.' + $c.Name + '.err'
    $scriptPath = Join-Path $Root $c.Script
    $argList = @('"' + $scriptPath + '"')
    foreach ($a in $c.Args) { $argList += $a }
    try {
        Start-Process -FilePath $Py -ArgumentList $argList -WorkingDirectory $Root `
            -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
        $started += $c.Name
    } catch {
        $failed += ($c.Name + ':' + $_.Exception.Message)
    }
}
if ($started.Count -gt 0) { Write-Log ('pulled up: ' + ($started -join ',')) }
foreach ($f in $failed) { Write-Log ('start FAIL ' + $f) }

# 5) Verify (wait up to 10s for freshly started components to bind their port).
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    $pending = @()
    foreach ($name in $started) {
        $port = ($Components | Where-Object { $_.Name -eq $name }).Port
        if (-not (Test-Port $port)) { $pending += $name }
    }
    if ($pending.Count -eq 0) { break }
    Start-Sleep -Seconds 1
}
$down = @()
foreach ($c in $Components) {
    if (-not (Test-Port $c.Port)) { $down += $c.Name }
}
# External blockers: the launcher cannot fix these (e.g. Feishu platform still
# holds a stale event bus for the app: online_instance_cnt=1). They are
# reported but do NOT fail the task (avoids 288 noisy rc=1 per day).
$external = @()
$internal = @()
foreach ($name in $down) {
    $outFile = $ProcOut + '.' + $name + '.out'
    $isExternal = $false
    if (Test-Path $outFile) {
        $tail = Get-Content $outFile -Tail 30 -ErrorAction SilentlyContinue
        foreach ($ln in $tail) {
            if ($ln -match 'another event bus is already connected' -or $ln -match 'online_instance_cnt') {
                $isExternal = $true
                break
            }
        }
    }
    if ($isExternal) { $external += $name } else { $internal += $name }
}
# streak: up -> 0; internal failure -> 0 (keep 10-min retry); external -> prev + 1
$streaks = [ordered]@{}
foreach ($c in $Components) {
    $prevStreak = if ($prevStreaks.ContainsKey($c.Name)) { [int]$prevStreaks[$c.Name] } else { 0 }
    if (-not (Test-Port $c.Port)) {
        if ($external -contains $c.Name) {
            $cur = $prevStreak + 1
            if ($cur -eq 6) {
                Write-Log ("EXTERNAL ALERT: " + $c.Name + " blocked by external dependency for 6 consecutive runs -> hourly retry")
            }
        } else {
            $cur = 0
        }
    } else {
        $cur = 0
    }
    $streaks[$c.Name] = $cur
}
$snap = [ordered]@{
    ts = (Get-Date -Format 's')
    started = @($started)
    down = @($down)
    external = @($external)
    internal = @($internal)
    streaks = $streaks
    last_attempt = $prevAttempt
    ports = [ordered]@{}
}
foreach ($c in $Components) {
    $snap.ports[$c.Name] = (Test-Port $c.Port)
}
$snap | ConvertTo-Json -Depth 4 -Compress | Set-Content -Path $StatusFile -Encoding UTF8
if ($down.Count -eq 0) {
    $upCount = @($Components | Where-Object { Test-Port $_.Port }).Count
    Write-Log ('stack ok (' + $upCount + '/' + $Components.Count + ' listening)')
    exit 0
}
if ($internal.Count -gt 0) {
    Write-Log ('FINAL down: ' + ($internal -join ',') + ' (internal failure)')
    exit 1
}
Write-Log ('FINAL down: ' + ($external -join ',') + ' (external: platform event bus busy, will retry next run)')
exit 0
