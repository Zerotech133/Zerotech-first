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
#
# 2026-10-31 P122（WO-GUANYU-P122-20261031，L2-1 恢复条件）：
#   ① openclaw CLI 调用统一「超时 + 进程树 kill + 退避重试」：修复 shared-state
#      清理超时（CLI 在 patch/validate 后清理锁 >5s 即报错）导致每 10min 写配置+回滚死循环；
#   ② 回滚判据改为 备份文件 sha256 还原校验（不依赖可能同样超时的 CLI validate）；
#   ③ 同一 target 失败后进入冷却（默认 30min，状态文件可 env 覆盖），冷却期任务绿退出；
#   ④ 备份保留最近 N 份（默认 20），旧备份进回收站（不直接删）；
#   ⑤ 路径/超时/重试/冷却/保留数均可 env 覆盖，供隔离测试（测试不碰生产配置）。
#
# Exit: 0 ok / no change / cooldown; 1 error or rolled back.
param(
    [switch]$Apply,
    [switch]$DryRun,
    [switch]$Json,
    [ValidateSet('auto', 'gpt6', 'ms1max35', 'ms1max177', 'deepseek')]
    [string]$ForceTier = 'auto',
    [int]$ProbeTimeoutMs = 2000,
    [int]$InferTimeoutSec = 150,
    [int]$CliTimeoutSec = 45,
    [int]$RetryCount = 3,
    [int]$CooldownMinutes = 30,
    [int]$BackupRetain = 20
)

$ErrorActionPreference = 'Stop'
function Env-OrDefault([string]$name, [string]$default) {
    $v = [Environment]::GetEnvironmentVariable($name)
    if ($v) { return [string]$v }
    return $default
}
$logFile = Env-OrDefault 'ZH_CARRIER_LOG' 'C:\Temp\zhuge_carrier_switch.log'
$backupDir = Env-OrDefault 'ZH_CARRIER_BACKUP_DIR' 'C:\Temp\oc_cfg_backups'
$stateFile = Env-OrDefault 'ZH_CARRIER_STATE_FILE' 'C:\Temp\zhuge_carrier_switch_state.json'

function Log([string]$msg) {
    try {
        $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $msg
        $line | Out-File -FilePath $logFile -Append -Encoding utf8
    } catch { }
}

function Fail([string]$msg) {
    Log ('ERROR ' + $msg)
    if ($Json) {
        @{ ok = $false; error = $msg; action = 'error'; log = $logFile } |
            ConvertTo-Json -Depth 4
    } else {
        Write-Output ('ERROR: ' + $msg)
    }
    exit 1
}

function Get-Sha256([string]$path) {
    try { return (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower() }
    catch { return '' }
}

function Resolve-ConfigPath {
    if ($env:OPENCLAW_CONFIG -and (Test-Path $env:OPENCLAW_CONFIG)) { return $env:OPENCLAW_CONFIG }
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
    } catch { return $false }
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
    } catch { return '' }
}

function Kill-ProcessTree([int]$rootPid) {
    $all = @()
    try { $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue } catch { }
    $kids = @($all | Where-Object { $_.ParentProcessId -eq $rootPid } | ForEach-Object { [int]$_.ProcessId })
    foreach ($k in $kids) { Kill-ProcessTree $k }
    try { Stop-Process -Id $rootPid -Force -ErrorAction SilentlyContinue } catch { }
}

function Invoke-OcCli {
    # 统一 CLI 调用：可选 stdin 文件；超时 kill 进程树；返回 @{ rc; out; timed_out; elapsed_ms }
    param([string[]]$ocArgs, [int]$timeoutSec, [string]$stdinFile = '')
    $outFile = Join-Path $env:TEMP ('zh_oc_out_' + $PID + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.log')
    $errFile = $outFile + '.err'
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $timedOut = $false
    $rc = -1
    $text = ''
    try {
        if ($oc -match '\.ps1$') {
            $exe = 'powershell.exe'
            $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $oc) + $ocArgs
        } else {
            $exe = $oc
            $argList = $ocArgs
        }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $quoted = @()
        foreach ($a in $argList) {
            $s = [string]$a
            if ($s -match '[\s"]') { $quoted += ('"' + ($s -replace '"', '\"') + '"') } else { $quoted += $s }
        }
        $psi.Arguments = ($quoted -join ' ')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        if ($stdinFile) { $psi.RedirectStandardInput = $true }
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        $null = $proc.Start()
        if ($stdinFile) {
            try {
                $stdinText = [System.IO.File]::ReadAllText($stdinFile)
                $proc.StandardInput.Write($stdinText)
                $proc.StandardInput.Close()
            } catch { Log ('stdin write failed: ' + $_.Exception.Message) }
        }
        $ms = [Math]::Max(1000, $timeoutSec * 1000)
        $finished = $proc.WaitForExit($ms)
        if (-not $finished) {
            Kill-ProcessTree ([int]$proc.Id)
            $timedOut = $true
            Log ('cli timeout after ' + $timeoutSec + 's; process tree killed')
        } else {
            $rc = [int]$proc.ExitCode
            try { $text = $proc.StandardOutput.ReadToEnd() + $proc.StandardError.ReadToEnd() } catch { }
            if ($rc -ne 0) { Log ('cli exit rc=' + $rc) }
        }
    } catch {
        Log ('cli invoke failed: ' + $_.Exception.Message)
        $rc = -1
    } finally {
        $sw.Stop()
        foreach ($f in @($outFile, $errFile)) {
            if (Test-Path -LiteralPath $f) {
                $text += [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
            }
        }
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
    return @{ rc = $rc; out = $text.Trim(); timed_out = $timedOut; elapsed_ms = [int]$sw.ElapsedMilliseconds }
}

function Invoke-OcCli-Retry {
    # 退避重试（1s, 3s, ...），超时/非零都重试；返回最后一次结果
    param([string]$name, [string[]]$ocArgs, [int]$timeoutSec)
    $last = @{ rc = -1; out = ''; timed_out = $false; elapsed_ms = 0 }
    for ($i = 1; $i -le $RetryCount; $i++) {
        $r = Invoke-OcCli -ocArgs $ocArgs -timeoutSec $timeoutSec
        Log ($name + ' attempt ' + $i + '/' + $RetryCount + ' rc=' + $r.rc + ' timed_out=' + $r.timed_out + ' ms=' + $r.elapsed_ms)
        if ($r.out) { foreach ($line in ($r.out -split "`r?`n")) { if ($line.Trim()) { Log ($name + ': ' + $line.Trim()) } } }
        $last = $r
        if ($r.rc -eq 0) { return $r }
        if ($i -lt $RetryCount) { Start-Sleep -Seconds (1 + 2 * ($i - 1)) }
    }
    return $last
}

function Read-Cooldown {
    try {
        if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
        $d = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $d.target -or -not $d.ts) { return $null }
        return $d
    } catch { return $null }
}

function Set-Cooldown([string]$target, [string]$reason) {
    try {
        @{ target = $target; ts = (Get-Date).ToString('o'); reason = $reason } |
            ConvertTo-Json -Depth 3 | Out-File -FilePath $stateFile -Encoding utf8 -Force
        Log ('cooldown set target=' + $target + ' reason=' + $reason)
    } catch { Log ('cooldown write failed: ' + $_.Exception.Message) }
}

function Clear-Cooldown {
    try {
        if (Test-Path -LiteralPath $stateFile) {
            @{ cleared = $true; ts = (Get-Date).ToString('o') } |
                ConvertTo-Json | Out-File -FilePath $stateFile -Encoding utf8 -Force
        }
    } catch { }
}

function Prune-Backups {
    # 只保留最近 $BackupRetain 份；旧文件进回收站（Windows 删除红线）
    try {
        $files = @(Get-ChildItem -Path $backupDir -Filter 'openclaw-*.json' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
        if ($files.Count -le $BackupRetain) { return }
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction SilentlyContinue
        foreach ($f in ($files | Select-Object -Skip $BackupRetain)) {
            try {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
                    $f.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin')
                Log ('backup pruned (recycle) ' + $f.Name)
            } catch {
                Log ('backup prune failed ' + $f.Name + ': ' + $_.Exception.Message)
            }
        }
    } catch { Log ('backup prune error: ' + $_.Exception.Message) }
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
    retry = $RetryCount
    cli_timeout_sec = $CliTimeoutSec
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

# ---- cooldown：同一 target 失败后 N 分钟内不再重试（任务绿退出，避免死循环）----
$cd = Read-Cooldown
if ($cd -and $cd.target -eq $targetModel) {
    try { $ageMin = ((Get-Date) - [datetime]::Parse($cd.ts)).TotalMinutes } catch { $ageMin = 9999 }
    if ($ageMin -ge 0 -and $ageMin -lt $CooldownMinutes) {
        $result.action = 'cooldown'
        $result.ok = $true
        $result.error = 'cooldown_active'
        $result.cooldown_age_min = [math]::Round($ageMin, 1)
        Log ('cooldown active target=' + $targetModel + ' age=' + [math]::Round($ageMin, 1) + 'min -> skip apply')
        if ($Json) { $result | ConvertTo-Json -Depth 8 } else {
            Write-Output ('COOLDOWN: ' + $targetModel + ' 失败冷却中（' + [math]::Round($ageMin, 1) + 'min）')
        }
        exit 0
    }
}

# ---- apply ----
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $backupDir ('openclaw-' + $stamp + '.json')
Copy-Item -Path $cfgPath -Destination $backup -Force
$backupSha = Get-Sha256 $backup
Log ('apply target=' + $targetModel + ' backup=' + $backup + ' sha=' + $backupSha.Substring(0, [Math]::Min(12, $backupSha.Length)))
Prune-Backups

function Restore-Backup {
    # 回滚判据：备份文件 sha256 与还原后配置 sha256 一致（不依赖 CLI）
    try {
        Copy-Item -Path $backup -Destination $cfgPath -Force
        $got = Get-Sha256 $cfgPath
        if ($got -eq $backupSha) {
            Log ('rolled back sha ok to ' + $backup)
            return $true
        }
        Log ('ROLLBACK SHA MISMATCH expect=' + $backupSha + ' got=' + $got)
        return $false
    } catch {
        Log ('ROLLBACK FAILED: ' + $_.Exception.Message)
        return $false
    }
}

try {
    $patchObj = @{ agents = @{ entries = @{ zhuge = @{ model = @{ primary = $targetModel } } } } }
    $patchJson = $patchObj | ConvertTo-Json -Depth 10
    $patchFile = Join-Path $env:TEMP ('zh_patch_' + $PID + '.json')
    $patchJson | Out-File -FilePath $patchFile -Encoding utf8 -Force

    $patchRes = @{ rc = -1; out = ''; timed_out = $false }
    for ($i = 1; $i -le $RetryCount; $i++) {
        $r = Invoke-OcCli -ocArgs @('config', 'patch', '--stdin') -timeoutSec $CliTimeoutSec -stdinFile $patchFile
        Log ('config patch attempt ' + $i + '/' + $RetryCount + ' rc=' + $r.rc + ' timed_out=' + $r.timed_out + ' ms=' + $r.elapsed_ms)
        if ($r.out) { foreach ($line in ($r.out -split "`r?`n")) { if ($line.Trim()) { Log ('patch: ' + $line.Trim()) } } }
        $patchRes = $r
        if ($r.rc -eq 0) { break }
        if ($i -lt $RetryCount) { Start-Sleep -Seconds (1 + 2 * ($i - 1)) }
    }
    Remove-Item -LiteralPath $patchFile -Force -ErrorAction SilentlyContinue
    if ($patchRes.rc -ne 0) {
        throw ('config patch failed rc=' + $patchRes.rc + ' timed_out=' + $patchRes.timed_out)
    }

    # validate 失败仅告警，不单独阻断（CLI validate 本身可能撞 shared-state；真正判据是 self-check）
    $valRes = Invoke-OcCli-Retry -name 'config validate' -ocArgs @('config', 'validate') -timeoutSec $CliTimeoutSec
    if ($valRes.rc -ne 0) { Log ('WARN config validate rc=' + $valRes.rc + ' (continue to self-check)') }

    # real inference self-check（判「非空正文」，{"ok":true,"outputs":[]} 不算健康）
    $inferText = ''
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

    Clear-Cooldown
    $result.ok = $true
    $result.action = 'applied'
    $result.backup = $backup
    $result.backup_sha = $backupSha
    Log ('applied ok target=' + $targetModel + ' backup=' + $backup)
    if ($Json) { $result | ConvertTo-Json -Depth 8 } else {
        Write-Output ('APPLIED: ' + $current + ' -> ' + $targetModel)
        Write-Output ('backup : ' + $backup)
    }
    exit 0
} catch {
    $msg = $_.Exception.Message
    Log ('apply failed: ' + $msg + ' -> rollback')
    $restored = Restore-Backup
    Set-Cooldown -target $targetModel -reason $msg
    $result.ok = $false
    $result.action = 'rolled-back'
    $result.error = $msg
    $result.backup = $backup
    $result.rollback_sha_ok = $restored
    if ($Json) { $result | ConvertTo-Json -Depth 8 } else { Write-Output ('ERROR: ' + $msg + ' (rolled back=' + $restored + ')') }
    exit 1
}
