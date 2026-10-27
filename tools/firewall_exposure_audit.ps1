# firewall_exposure_audit.ps1
# Read-only Windows exposure audit (no changes, no admin required for the checks
# that work in medium integrity; firewall rule enumeration may need admin).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\firewall_exposure_audit.ps1
#   powershell ... -OutFile C:\Temp\fw_audit.csv
# Output: console summary + CSV (ports) + JSON summary beside the CSV.
# Rollback: none (read-only).

param(
    [string]$OutFile = ""
)

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if (-not $OutFile) { $OutFile = "C:\Temp\firewall_exposure_audit_$stamp.csv" }
$OutDir = Split-Path -Parent $OutFile
if ($OutDir -and -not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    host = $env:COMPUTERNAME
    listen_total = 0
    non_loopback = 0
    addresses = @{}
    ports = @()
    firewall_profiles = @()
    a7_rules_visible = $null
    docker_ports = @()
    warnings = @()
}

try {
    $listen = Get-NetTCPConnection -State Listen -ErrorAction Stop
} catch {
    $result.warnings += "Get-NetTCPConnection failed: $($_.Exception.Message)"
    $listen = @()
}
$result.listen_total = @($listen).Count

$loopback = @('127.0.0.1', '::1')
$nonLoop = @($listen | Where-Object { $loopback -notcontains $_.LocalAddress })
$result.non_loopback = $nonLoop.Count

$addrGroups = $nonLoop | Group-Object LocalAddress | Sort-Object Count -Descending
foreach ($g in $addrGroups) { $result.addresses[$g.Name] = $g.Count }

$portRows = @()
foreach ($c in ($nonLoop | Sort-Object LocalPort)) {
    $procName = ''
    try { $procName = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName } catch {}
    $portRows += [pscustomobject]@{
        local_address = $c.LocalAddress
        local_port    = $c.LocalPort
        owning_pid    = $c.OwningProcess
        process_name  = $procName
    }
}
$result.ports = $portRows
if ($portRows.Count -gt 0) { $portRows | Export-Csv -Path $OutFile -NoTypeInformation -Encoding UTF8 }

try {
    $profiles = Get-NetFirewallProfile -ErrorAction Stop
    foreach ($p in $profiles) {
        $result.firewall_profiles += [pscustomobject]@{ name = $p.Name; enabled = [bool]$p.Enabled }
    }
} catch {
    $result.warnings += "Get-NetFirewallProfile failed (may need admin): $($_.Exception.Message)"
}

try {
    $a7 = @(Get-NetFirewallRule -Group 'ZeroBound-Audit-A7' -ErrorAction Stop)
    $result.a7_rules_visible = $a7.Count
} catch {
    $result.warnings += "Get-NetFirewallRule(Group=ZeroBound-Audit-A7) failed (normally needs admin): $($_.Exception.Message)"
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        $result.docker_ports = @(docker ps --format '{{.Names}} {{.Ports}}' 2>$null)
    } catch {
        $result.warnings += "docker ps failed: $($_.Exception.Message)"
    }
} else {
    $result.warnings += "docker not found on PATH"
}

$jsonPath = [System.IO.Path]::ChangeExtension($OutFile, '.json')
$result | ConvertTo-Json -Depth 5 | Set-Content -Path $jsonPath -Encoding UTF8

Write-Output ("listen_total=" + $result.listen_total + " non_loopback=" + $result.non_loopback)
foreach ($kv in $result.addresses.GetEnumerator()) { Write-Output ("  addr " + $kv.Key + " = " + $kv.Value) }
foreach ($p in $result.firewall_profiles) { Write-Output ("  firewall " + $p.name + " enabled=" + $p.enabled) }
Write-Output ("a7_rules_visible=" + $result.a7_rules_visible)
Write-Output ("csv=" + $OutFile)
Write-Output ("json=" + $jsonPath)
if ($result.warnings.Count -gt 0) {
    Write-Output "warnings:"
    foreach ($w in $result.warnings) { Write-Output ("  - " + $w) }
}
