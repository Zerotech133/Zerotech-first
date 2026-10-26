# JEV service stack watchdog (restored 2026-10-26, K-ORG-20261026-JEVSTACK)
# ASCII only. Pull-up-if-missing for the Jev quad, port-based liveness,
# plus card-handler websocket self-heal (age > 6h or ws-down > 90s).
# Scheduled task: \JevStack (every 10 min, S4U). Log: logs\jev-stack.log
# Status snapshot: logs\jev-stack-status.json
# Exit: 0 = all up OR only externally-blocked components; 1 = internal start failure.
param(
    [switch]$Status,
    [switch]$Force
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
    @{ Name = 'qd-bridge'; Port = 9103; Script = 'jev_qd_bridge.py'; Args = @('--loop') },
    @{ Name = 'card-handler'; Port = 9104; Script = 'lark_card_handler.py'; Args = @() }
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

# 3) Card-handler self-heal (only when it is up but unhealthy).
if (Test-Port 9104) {
    $reason = Get-CardRestartReason
    if ($reason) {
        $oldPid = Stop-TreeByPort 9104
        Write-Log ('restart: card-handler reason=' + $reason + ' killed=' + $oldPid)
    }
}

# 4) Pull up missing components.
$started = @()
$failed = @()
foreach ($c in $Components) {
    if (Test-Port $c.Port) { continue }
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
$snap = [ordered]@{
    ts = (Get-Date -Format 's')
    started = @($started)
    down = @($down)
    external = @($external)
    internal = @($internal)
    ports = [ordered]@{}
}
foreach ($c in $Components) {
    $snap.ports[$c.Name] = (Test-Port $c.Port)
}
$snap | ConvertTo-Json -Depth 4 -Compress | Set-Content -Path $StatusFile -Encoding UTF8
if ($down.Count -eq 0) {
    Write-Log 'stack ok (4/4 listening)'
    exit 0
}
if ($internal.Count -gt 0) {
    Write-Log ('FINAL down: ' + ($internal -join ',') + ' (internal failure)')
    exit 1
}
Write-Log ('FINAL down: ' + ($external -join ',') + ' (external: platform event bus busy, will retry next run)')
exit 0
