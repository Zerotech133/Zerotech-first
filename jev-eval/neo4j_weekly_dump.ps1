# =====================================================================
# neo4j_weekly_dump.ps1 - weekly consistent dump of neo4j-kg (Sat 05:30)
# New for machine swap v11 (2026-10-16); old-machine script was never in
# the repo, rewritten from scratch.
# First attempt used STOP DATABASE - that is Enterprise-only (community
# cypher-shell rc=1, drill 09:51). Community-consistent flow instead:
#   docker stop -> sidecar "docker run --rm" mounts volume neo4j_kg_data
#   + host dump dir -> neo4j-admin database dump -> docker start (ALWAYS).
# No credentials needed (offline dump via mounted volume).
# Contract consumed by n1_check v1.7/v1.8 section 8:
#   dump dir C:\项目\老田的AI助手\backups\neo4j\*.dump, freshness window 8 days.
# 2026-10-16 章程 §5：路径去 C:\aios junction 依赖（junction 仅作兼容别名）。
# =====================================================================
$ErrorActionPreference = 'Continue'

$Container = 'neo4j-kg'
$Volume    = 'neo4j_kg_data'
$Image     = 'neo4j:5'
$DumpDir   = 'C:\项目\老田的AI助手\backups\neo4j'
$KeepCount = 2
$MinBytes  = 1MB          # quality gate: empty/failed dump is tiny
$stamp     = Get-Date -Format 'yyyy-MM-dd_HHmm'
$LogFile   = Join-Path $DumpDir ('dump_' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
$script:Failed = 0

if (-not (Test-Path $DumpDir)) { New-Item -ItemType Directory -Path $DumpDir -Force | Out-Null }
function Log([string]$msg)  { $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $msg; Write-Output $line; Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
function LogOk([string]$w)  { Log ('OK   ' + $w) }
function LogFail([string]$w) { $script:Failed++; Log ('FAIL ' + $w) }

Log ("weekly dump start, stamp=" + $stamp)

$dest = Join-Path $DumpDir ('neo4j-kg_' + (Get-Date -Format 'yyyyMMdd') + '.dump')
$dumpOk = $false

# ---- 1. stop container (dump requires the store offline) ----
& docker stop $Container 2>$null
if ($LASTEXITCODE -ne 0) {
    LogFail ('docker stop rc=' + $LASTEXITCODE + ' - aborting, no dump attempted')
    # container likely still running; nothing was touched
} else {
    Log 'container stopped'
    # ---- 2. sidecar dump: temp container, same volume, host dir mounted ----
    try {
        $rawDump = Join-Path $DumpDir 'neo4j.dump'
        if (Test-Path $rawDump) { Remove-Item $rawDump -Force }
        # NOTE: no :ro on /data - the neo4j image entrypoint chowns /data on
        # boot and aborts when the mount is read-only (drill 09:53). The dump
        # itself only reads /data and writes to /dumps.
        & docker run --rm -v ($Volume + ':/data') -v ($DumpDir + ':/dumps') $Image `
            neo4j-admin database dump neo4j --to-path=/dumps --overwrite-destination 2>$null
        if ($LASTEXITCODE -ne 0) { throw ("sidecar dump rc=" + $LASTEXITCODE) }
        if (-not (Test-Path $rawDump)) { throw 'neo4j.dump not created' }
        $sz = (Get-Item $rawDump).Length
        if ($sz -lt $MinBytes) { throw ("dump too small: " + $sz + " bytes (min " + $MinBytes + ")") }
        Move-Item $rawDump $dest -Force
        $dumpOk = $true
        LogOk ('neo4j-kg dump ' + [math]::Round($sz/1MB) + 'MB -> ' + $dest)
    } catch {
        LogFail ("dump: " + $_.Exception.Message)
        if (Test-Path $rawDump) { Remove-Item $rawDump -Force -ErrorAction SilentlyContinue }
        if ($dumpOk -eq $false -and (Test-Path $dest)) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
    } finally {
        # ---- 3. ALWAYS restart the container ----
        & docker start $Container 2>$null
        if ($LASTEXITCODE -eq 0) { Log 'container restarted' }
        else { LogFail ('docker start rc=' + $LASTEXITCODE + ' - neo4j LEFT STOPPED, manual start needed!') }
    }
}

# ---- 4. keep-2 prune ----
$sets = @(Get-ChildItem $DumpDir -Filter 'neo4j-kg_*.dump' -File -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending)
foreach ($f in ($sets | Select-Object -Skip $KeepCount)) {
    Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
    Log ('PRUNE ' + $f.Name)
}

Log ('DONE failed=' + $script:Failed)

# failure alert flag (AlertPush forwards, same convention as backup_daily)
if ($script:Failed -gt 0) {
    try {
        $aDir = 'C:\项目\老田的AI助手\jev-eval\alerts'
        if (-not (Test-Path $aDir)) { New-Item -ItemType Directory -Path $aDir -Force | Out-Null }
        @(
            'type=neo4j_weekly_dump_failed',
            ('stamp=' + $stamp),
            ('log=' + $LogFile)
        ) | Set-Content -LiteralPath (Join-Path $aDir ('NEO4J_DUMP_FAILED_' + $stamp + '.flag')) -Encoding UTF8
    } catch { }
    exit 1
}
exit 0
