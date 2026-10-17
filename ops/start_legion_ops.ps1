# LegionOps dual-service launcher (ASCII only - PS 5.1 encoding safe)
# Starts both 8093 (org_registry_server) and 8096 (api_legion_ops), idempotent.
$ErrorActionPreference = 'Continue'
$py = 'C:\Program Files\Python311\python.exe'
$root = 'C:\aios'

function Ensure($port, $script, $log) {
    $up = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($up) { Write-Output "[OK] port $port already up"; return }
    Start-Process -FilePath $py -ArgumentList '-X','utf8',"$root\$script" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput "C:\Temp\$log" -RedirectStandardError "C:\Temp\$log.err"
    Start-Sleep -Seconds 3
    $up2 = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($up2) { Write-Output "[OK] port $port started" } else { Write-Output "[FAIL] port $port" }
}

Ensure 8093 'tools\org_registry_server.py' 'org_registry_8093.log'
Ensure 8096 'portal\api_legion_ops_v2.py' 'legion_ops_api.log'
