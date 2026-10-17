# =====================================================================
# offsite_sync.ps1 - T3 offsite copy: C:\backups_mirror (T2) ->
# C:\Users\t\OneDrive\backups_mirror (T3), keep-2 retention.
# New for machine swap v11 (2026-10-16). Old machine ran this as part of
# a separate task chain; task name kept as "OffsiteSync" because N1
# (n1_check) matches scheduled-task Arguments against this script name.
# Contract consumed by n1_check:
#   - log file C:\Temp\offsite_sync.log, one line per run:
#     [YYYYMMDD_HHMMSS] OK set=<stamp> files=N bytes=B
#     [YYYYMMDD_HHMMSS] FAIL <reason>
#   - exit code 0 on success, 1 on failure (N1 checks RC=0).
# robocopy codes 0-7 are success (1 = files copied); >=8 is failure.
# =====================================================================
$ErrorActionPreference = "Continue"

$T2 = 'C:\backups_mirror'
$T3 = Join-Path $env:USERPROFILE 'OneDrive\backups_mirror'
$LogFile = 'C:\Temp\offsite_sync.log'
$KeepCount = 2

function Log([string]$msg) {
    Add-Content -Path $LogFile -Value $msg -Encoding UTF8
    Write-Output $msg
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$script:Failed = $false

if (-not (Test-Path 'C:\Temp')) { New-Item -ItemType Directory -Path 'C:\Temp' -Force | Out-Null }

# ---- gate: T2 must hold at least one backup set ----
if (-not (Test-Path $T2)) {
    Log ("[{0}] FAIL T2 root missing: {1}" -f $stamp, $T2)
    exit 1
}
$newest = Get-ChildItem $T2 -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_' } |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $newest) {
    Log ("[{0}] FAIL no backup sets under T2" -f $stamp)
    exit 1
}

# ---- copy T2 -> T3 (incremental; existing sets are skipped cheaply) ----
try {
    New-Item -ItemType Directory -Path $T3 -Force | Out-Null
    # /E subdirs incl empty; /COPY:DAT data+attrs+timestamps; /R:1 /W:1 fast fail
    $null = & robocopy.exe $T2 $T3 /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        $script:Failed = $true
        Log ("[{0}] FAIL robocopy rc={1} (>=8)" -f $stamp, $rc)
    }
} catch {
    $script:Failed = $true
    Log ("[{0}] FAIL robocopy exception: {1}" -f $stamp, $_.Exception.Message)
}

# ---- verify the newest set landed in T3 ----
if (-not $script:Failed) {
    $probe = Join-Path $T3 $newest.Name
    if (-not (Test-Path $probe)) {
        $script:Failed = $true
        Log ("[{0}] FAIL newest set absent in T3: {1}" -f $stamp, $newest.Name)
    }
}

# ---- T3 retention: keep newest 2 sets ----
if (Test-Path $T3) {
    $sets = @(Get-ChildItem $T3 -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_' } |
        Sort-Object Name -Descending)
    foreach ($d in ($sets | Select-Object -Skip $KeepCount)) {
        Remove-Item $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Log ("[{0}] PRUNE T3 {1}" -f $stamp, $d.Name)
    }
}

# ---- result line (N1 parses the OK line) ----
if (-not $script:Failed) {
    $setFiles = @(Get-ChildItem (Join-Path $T3 $newest.Name) -Recurse -File -ErrorAction SilentlyContinue)
    $setBytes = ($setFiles | Measure-Object -Property Length -Sum).Sum
    if (-not $setBytes) { $setBytes = 0 }
    Log ("[{0}] OK set={1} files={2} bytes={3}" -f $stamp, $newest.Name, $setFiles.Count, $setBytes)
    exit 0
}

# failure alert flag (forwarded by AlertPush task like backup flags)
try {
    $aDir = 'C:\aios\jev-eval\alerts'
    if (-not (Test-Path $aDir)) { New-Item -ItemType Directory -Path $aDir -Force | Out-Null }
    @(
        'type=offsite_sync_failed',
        ("stamp=" + $stamp),
        ("t2=" + $T2),
        ("t3=" + $T3)
    ) | Set-Content -LiteralPath (Join-Path $aDir ("OFFSITE_FAILED_" + $stamp + ".flag")) -Encoding UTF8
} catch { }
exit 1
