# zhuge_carrier_switch.ps1
# WO-LEGION-ZHUGE-SWITCH-20261026 (2026-10-26)
#
# Probe the available carriers for the OpenClaw "zhuge" agent and switch its
# model.primary when a higher tier comes online. Safe by default (dry-run).
#
# Tiers (highest first):
#   gpt6      itokengate/gpt-6-sol        probe itokengate.com:443
#   ms1max35  ms1max-35b/qwen36-35b-fast  probe 192.168.5.156:8080
#   ms1max177 ms1max-177b/qwen38-flash    probe 192.168.5.155:8080
#   deepseek  deepseek/deepseek-chat      probe api.deepseek.com:443 (fallback)
#
# -Apply: backup openclaw.json -> openclaw config patch -> validate ->
#         real inference self-check; auto rollback on any failure.
# -ForceTier: skip probing (test/dry-run use).
# Scheduling this script (Task Scheduler) is L2 and needs human approval.
#
# Exit: 0 ok / no change; 1 error or rolled back.
param(
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Json,
    [ValidateSet('auto', 'gpt6', 'ms1max35', 'ms1max177', 'deepseek')]
    [string]$ForceTier = 'auto',
    [int]$ProbeTimeoutMs = 2000,
    [int]$InferTimeoutSec = 150
)

$ErrorActionPreference = 'Stop'
$logFile = 'C:\Temp\zhuge_carrier_switch.log'
$backupDir = 'C:\Temp\oc_cfg_backups'

function Log([string]$msg) {
    try {
        $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $msg
        $line | Out-File -FilePath $logFile -Append -Encoding utf8
    } catch { }
}

function Fail([string]$msg) {
    # A2-B11：错误路径也遵守 -Json 契约（计划任务以 -Json 调用，下游按 JSON 解析）
    Log ('ERROR ' + $msg)
    if ($Json) {
        @{ ok = $false; error = $msg; action = 'error'; log = $logFile } |
            ConvertTo-Json -Depth 4
    } else {
        Write-Output ('ERROR: ' + $msg)
    }
    exit 1
}

function Resolve-ConfigPath {
    if ($env:OPENCLAW_CONFIG -and (Test-Path $env:OPENCLAW_CONFIG)) {
        return $env:OPENCLAW_CONFIG
    }
    foreach ($n in @('openclaw.json', 'config.json')) {
        $p = Join-Path $env:USERPROFILE ('.openclaw\' + $n)
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Probe-Tcp([string]$hostName, [int]$port, [int]$timeoutMs) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync($hostName, $port)
        $started = [DateTime]::UtcNow
        while (-not $task.IsCompleted) {
            if (([DateTime]::UtcNow - $started).TotalMilliseconds -gt $timeoutMs) {
                $client.Dispose(); return $false
            }
            Start-Sleep -Milliseconds 50
        }
        $ok = $client.Connected
        $client.Dispose()
        return $ok
    } catch {
        return $false
    }
}

function Find-OpenClaw {
    $cmd = Get-Command openclaw -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-CurrentModel {
    param([string]$cfgPath)
    try {
        $raw = Get-Content -Path $cfgPath -Raw -Encoding UTF8
        $cfg = $raw | ConvertFrom-Json
        return [string]$cfg.agents.entries.zhuge.model.primary
    } catch {
        return ''
    }
}

$tiers = [ordered]@{
    gpt6      = @{ model = 'itokengate/gpt-6-sol';             host = 'itokengate.com';  port = 443 }
    ms1max35  = @{ model = 'ms1max-35b/qwen36-35b-fast';       host = '192.168.5.156';   port = 8080 }
    ms1max177 = @{ model = 'ms1max-177b/qwen38-flash';         host = '192.168.5.155';   port = 8080 }
    deepseek  = @{ model = 'deepseek/deepseek-chat';           host = 'api.deepseek.com'; port = 443 }
}

$oc = Find-OpenClaw
if (-not $oc) { Fail 'openclaw not found on PATH' }
$cfgPath = Resolve-ConfigPath
if (-not $cfgPath) { Fail 'openclaw config not found (~/.openclaw/openclaw.json or config.json)' }
$current = Get-CurrentModel -cfgPath $cfgPath

# ---- pick target tier ----
$probes = [ordered]@{}
$targetTier = $null
if ($ForceTier -ne 'auto') {
    $targetTier = $ForceTier
} else {
    foreach ($name in $tiers.Keys) {
        $t = $tiers[$name]
        $ok = Probe-Tcp -hostName $t.host -port $t.port -timeoutMs $ProbeTimeoutMs
        $probes[$name] = $ok
        Log ('probe ' + $name + ' ' + $t.host + ':' + $t.port + ' = ' + $ok)
        if ($ok) { $targetTier = $name; break }
    }
    if (-not $targetTier) { $targetTier = 'deepseek' }
}
$targetModel = $tiers[$targetTier].model
$changed = ($current -ne $targetModel)
$dry = ($DryRun -or (-not $Apply))

$result = [ordered]@{
    ok = $true
    dry_run = $dry
    current = $current
    target_tier = $targetTier
    target = $targetModel
    changed = $changed
    probes = $probes
    action = if ($dry) { 'plan-only' } elseif ($changed) { 'apply' } else { 'no-change' }
    log = $logFile
}

if ($dry -or (-not $changed)) {
    Log ('plan current=' + $current + ' target=' + $targetModel + ' changed=' + $changed + ' dry=' + $dry)
    if ($Json) { $result | ConvertTo-Json -Depth 8 } else {
        Write-Output ('current : ' + $current)
        Write-Output ('target  : ' + $targetModel + '  (' + $targetTier + ')')
        Write-Output ('changed : ' + $changed + '   mode: ' + $result.action)
        if ($probes.Count -gt 0) {
            foreach ($k in $probes.Keys) { Write-Output ('probe ' + $k + ' : ' + $probes[$k]) }
        }
    }
    exit 0
}

# ---- apply ----
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $backupDir ('openclaw-' + $stamp + '.json')
Copy-Item -Path $cfgPath -Destination $backup -Force
Log ('apply target=' + $targetModel + ' backup=' + $backup)

function Restore-Backup {
    try {
        Copy-Item -Path $backup -Destination $cfgPath -Force
        & $oc config validate *> $null
        Log ('rolled back to ' + $backup)
    } catch {
        Log ('ROLLBACK FAILED: ' + $_.Exception.Message)
    }
}

try {
    $patchObj = @{ agents = @{ entries = @{ zhuge = @{ model = @{ primary = $targetModel } } } } }
    $patchJson = $patchObj | ConvertTo-Json -Depth 10
    $patchJson | & $oc config patch --stdin 2>&1 | ForEach-Object { Log ('patch: ' + $_) }
    if ($LASTEXITCODE -ne 0) { throw ('config patch failed exit=' + $LASTEXITCODE) }
    & $oc config validate 2>&1 | ForEach-Object { Log ('validate: ' + $_) }
    if ($LASTEXITCODE -ne 0) { throw ('config validate failed exit=' + $LASTEXITCODE) }

    # real inference self-check (uses OpenClaw secret store; no key on disk)
    # A2-B06：必须有超时（InferTimeoutSec 旧版声明未用，OpenClaw 挂起会无限阻塞）
    # 且必须判「非空正文」——{"ok":true,"outputs":[]} 不能算健康（L5 会判 empty_output）。
    $job = Start-Job -ScriptBlock {
        param($exe)
        & $exe infer model run --local --agent zhuge --prompt 'reply only: OK' --json
    } -ArgumentList $oc
    if (-not (Wait-Job $job -Timeout $InferTimeoutSec)) {
        Stop-Job $job -ErrorAction SilentlyContinue
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        throw ('self-check timeout ' + $InferTimeoutSec + 's')
    }
    $inferText = (Receive-Job $job | Out-String)
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    Log ('selfcheck raw len=' + $inferText.Length)
    $ok = $false
    try {
        $start = $inferText.IndexOf('{')
        if ($start -ge 0) {
            $d = $inferText.Substring($start) | ConvertFrom-Json
            $outText = ''
            if ($d.ok) {
                $outText = [string]$d.text
                if (-not $outText -and $d.outputs) {
                    $outText = (($d.outputs | ForEach-Object { [string]$_.text }) -join "`n")
                }
                if (-not $outText -and $d.result) {
                    $outText = [string]$d.result.text
                    if (-not $outText -and $d.result.outputs) {
                        $outText = (($d.result.outputs | ForEach-Object { [string]$_.text }) -join "`n")
                    }
                }
            }
            $ok = -not [string]::IsNullOrWhiteSpace($outText)
            Log ('selfcheck ok=' + $ok + ' text_len=' + ($outText.Length))
        }
    } catch { $ok = $false }
    if (-not $ok) { throw 'self-check inference empty/failed' }

    $result.ok = $true
    $result.action = 'applied'
    $result.backup = $backup
    Log ('applied ok target=' + $targetModel + ' backup=' + $backup)
    if ($Json) { $result | ConvertTo-Json -Depth 8 } else {
        Write-Output ('APPLIED: ' + $current + ' -> ' + $targetModel)
        Write-Output ('backup : ' + $backup)
    }
    exit 0
} catch {
    $msg = $_.Exception.Message
    Log ('apply failed: ' + $msg + ' -> rollback')
    Restore-Backup
    $result.ok = $false
    $result.action = 'rolled-back'
    $result.error = $msg
    $result.backup = $backup
    if ($Json) { $result | ConvertTo-Json -Depth 8 } else { Write-Output ('ERROR: ' + $msg + ' (rolled back)') }
    exit 1
}
