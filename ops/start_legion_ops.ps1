# LegionOps dual-service launcher (ASCII only - PS 5.1 encoding safe)
# Starts both 8093 (org_registry_server) and 8096 (api_legion_ops), idempotent.
$ErrorActionPreference = 'Continue'
$py = 'C:\Program Files\Python311\python.exe'
$root = 'C:\aios'

# K-RESTART (10-19): one-shot sentinel flag for a controlled restart of 8096.
# The current api_legion_ops_v2 process is elevated; only this elevated task
# context can kill it (non-elevated taskkill = access denied). Flag present =>
# kill matching python orphans first, then fall through to normal start.
if (Test-Path 'C:\Temp\LEGION_OPS_RESTART.flag') {
    Remove-Item 'C:\Temp\LEGION_OPS_RESTART.flag' -Force -ErrorAction SilentlyContinue
    ('restart-flag: killing api_legion_ops_v2 orphans ' + (Get-Date -Format s)) |
        Out-File 'C:\Temp\legion_ops_restart.log' -Append -Encoding utf8
    Get-CimInstance -Query "SELECT ProcessId FROM Win32_Process WHERE Name = 'python.exe' AND CommandLine LIKE '%api_legion_ops_v2%'" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

function Ensure($port, $script, $log, $pyExe = $py) {
    $up = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($up) { Write-Output "[OK] port $port already up"; return }
    Start-Process -FilePath $pyExe -ArgumentList '-X','utf8',"$root\$script" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput "C:\Temp\$log" -RedirectStandardError "C:\Temp\$log.err"
    Start-Sleep -Seconds 3
    $up2 = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($up2) { Write-Output "[OK] port $port started" } else { Write-Output "[FAIL] port $port" }
}

# 8796 legion_command serve.py: org sandbox + intent-card contract endpoint.
# Pinned to the repo venv (C:\aios\CowAgent\venv): the tooling-managed venv can be
# reclaimed (K-STOCK-413), which would make this service silently vanish.
# Keep ASCII only in this file - PS 5.1 reads it with the system codepage.
$pyLegion = 'C:\aios\CowAgent\venv\Scripts\python.exe'

# One-shot restart for 8796 (same sentinel pattern as the 8096 K-RESTART above).
# The task runs in its own session, so only this context can stop the child it
# spawned; an interactive shell gets "access denied". Create the flag, run the
# task once, flag is consumed and 8796 comes back with the new code.
$flag8796 = 'C:\Temp\LEGION_8796_RESTART.flag'
if (Test-Path $flag8796) {
    Remove-Item $flag8796 -Force -ErrorAction SilentlyContinue
    $c8796 = Get-NetTCPConnection -LocalPort 8796 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c8796) { Stop-Process -Id $c8796.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

function Ensure8796 {
    # Port-up is not enough: old builds can hold 8796 while missing
    # /api/ide/openhands/sessions or /stream. Probe a feature endpoint first.
    $up = Test-NetConnection -ComputerName 127.0.0.1 -Port 8796 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($up) {
        try {
            $probe = Invoke-RestMethod 'http://127.0.0.1:8796/api/ide/openhands/sessions?limit=1' -TimeoutSec 3
            if ($probe.ok) {
                Write-Output '[OK] port 8796 latest (sessions probe)'
                return
            }
        } catch {
            Write-Output '[WARN] 8796 stale build; restarting'
        }
        $c = Get-NetTCPConnection -LocalPort 8796 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }
    Start-Process -FilePath $pyLegion -ArgumentList '-X','utf8',"$root\agent_platform\legion_command\serve.py" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput 'C:\Temp\legion_command_8796.log' -RedirectStandardError 'C:\Temp\legion_command_8796.log.err'
    Start-Sleep -Seconds 6
    try {
        $probe = Invoke-RestMethod 'http://127.0.0.1:8796/api/ide/openhands/sessions?limit=1' -TimeoutSec 5
        if ($probe.ok) { Write-Output '[OK] port 8796 started latest' }
        else { Write-Output '[FAIL] 8796 sessions probe' }
    } catch {
        Write-Output '[FAIL] 8796 start/probe'
    }
}

Ensure 8093 'tools\org_registry_server.py' 'org_registry_8093.log'
Ensure 8096 'portal\api_legion_ops_v2.py' 'legion_ops_api.log'
Ensure8796
