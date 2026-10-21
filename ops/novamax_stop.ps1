# NovaMax llama-server stop v3 (ports 1234/1238)
# v2 lesson: nova_guard.py respawns instances via panel 3001 after ~3min of failures.
# v3 strategy: kill chain + create nova_guard.off so the guardian stops resurrecting.
$ports = @(1234, 1238)
$offFile = 'C:\Users\t\AppData\Local\Programs\Ollama\nova_guard.off'
Set-Content -Path $offFile -Value ("stopped at " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding ASCII
Write-Host "OFF switch created: $offFile (nova_guard will skip auto-start)."

function Get-Chain {
    # returns PIDs: llama-server on ports, their node parent (NovaMax backend), python grandparent (start_novamax.py)
    $result = @{ llama = @(); node = @(); python = @() }
    foreach ($p in $ports) {
        $pids = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $pids) {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -eq 'llama-server') {
                if ($result.llama -notcontains $procId) { $result.llama += $procId }
                $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").ParentProcessId
                $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid" -ErrorAction SilentlyContinue
                if ($parent -and $parent.Name -eq 'node.exe' -and $parent.CommandLine -match 'NovaMax') {
                    if ($result.node -notcontains $ppid) { $result.node += $ppid }
                    $gpid = $parent.ParentProcessId
                    $gp = Get-CimInstance Win32_Process -Filter "ProcessId=$gpid" -ErrorAction SilentlyContinue
                    if ($gp -and $gp.Name -eq 'python.exe' -and $gp.CommandLine -match 'start_novamax\.py') {
                        if ($result.python -notcontains $gpid) { $result.python += $gpid }
                    }
                }
            }
        }
    }
    return $result
}

$killed = @()
# Round 1: kill chain (python starter first, then node guardian, then llama-server)
$chain = Get-Chain
foreach ($procId in $chain.python) {
    Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue
    $killed += "python-starter PID $procId"
}
foreach ($procId in $chain.node) {
    Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue
    $killed += "node-guardian PID $procId"
}
foreach ($procId in $chain.llama) {
    Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue
    $killed += "llama-server PID $procId"
}

if ($killed.Count -eq 0) { Write-Host "Nothing running on ports 1234/1238. Already stopped." }

# Watch 100s: covers node ~60s respawn cycle. If respawned, kill round 2.
$respawnKilled = @()
for ($i = 1; $i -le 10; $i++) {
    Start-Sleep -Seconds 10
    $chain = Get-Chain
    $all = @($chain.llama) + @($chain.node) + @($chain.python)
    if ($all.Count -gt 0) {
        foreach ($procId in $chain.python) { Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue; $respawnKilled += "python PID $procId" }
        foreach ($procId in $chain.node)    { Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue; $respawnKilled += "node PID $procId" }
        foreach ($procId in $chain.llama)   { Stop-Process -Id $procId -Force -Confirm:$false -ErrorAction SilentlyContinue; $respawnKilled += "llama PID $procId" }
    }
}

Write-Host ""
if ($killed.Count -gt 0)   { Write-Host ("KILLED: " + ($killed -join '; ')) }
if ($respawnKilled.Count -gt 0) { Write-Host ("RESPAWN-KILLED during watch: " + ($respawnKilled -join '; ')) }

$final = Get-Chain
if (@($final.llama).Count -eq 0 -and @($final.node).Count -eq 0) {
    Write-Host "OK: 1234/1238 fully released, no respawn within 100s watch window."
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Host ("Free memory now: {0:N1} GB" -f ($os.FreePhysicalMemory/1MB))
} else {
    Write-Host "WARN: still respawning after 100s - open NovaStudio GUI and disable its auto-start model feature, then rerun."
}
