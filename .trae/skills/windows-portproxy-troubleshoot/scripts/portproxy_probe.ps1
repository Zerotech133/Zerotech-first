param(
    [int]$Listen = 8734,
    [int]$Connect = 8080
)
$ErrorActionPreference = 'Continue'
function Section($t) { Write-Host "`n=== $t ===" }
Section "iphlpsvc state"
try {
    $s = Get-Service iphlpsvc -ErrorAction Stop
    Write-Host "Status=$($s.Status)  StartType=$($s.StartType)"
} catch {
    Write-Host "iphlpsvc unavailable: $($_.Exception.Message)"
}
Section "portproxy v4tov4 table"
try {
    $out = (& netsh interface portproxy show all 2>&1) | Out-String
    Write-Host $out
    $has = $out -match "127\.0\.0\.1\s+${Listen}\s+127\.0\.0\.1\s+${Connect}"
    Write-Host "rule 127.0.0.1:${Listen} -> 127.0.0.1:${Connect} exists: $has"
} catch {
    Write-Host "netsh failed: $($_.Exception.Message)"
}
Section "listening (Listen=${Listen}, Connect=${Connect})"
foreach($p in @($Listen,$Connect)){
    $cs = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if($cs){
        foreach($c in $cs){
            $procId = $c.OwningProcess
            $name = if($procId -and $procId -ne 0){(Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName}else{"(Idle)"}
            Write-Host "  $($c.LocalAddress):${p}  PID=$procId  $name"
        }
    } else {
        Write-Host "  ${p}: no listener"
    }
}
Section "backend HTTP ($Connect)"
foreach($urlPath in @("/api/health","/health","/intel")){
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:${Connect}${urlPath}" -UseBasicParsing -TimeoutSec 3
        Write-Host "  $urlPath  HTTP $([int]$r.StatusCode)"
        break
    } catch {
        Write-Host "  $urlPath  FAIL  $($_.Exception.Message)"
    }
}
if($Listen -ne $Connect){
    Section "entry HTTP ($Listen)"
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:${Listen}/intel" -UseBasicParsing -TimeoutSec 3
        Write-Host "  /intel  HTTP $([int]$r.StatusCode)"
    } catch {
        Write-Host "  /intel  FAIL  $($_.Exception.Message)"
    }
}