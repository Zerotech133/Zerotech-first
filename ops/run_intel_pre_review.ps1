# run_intel_pre_review.ps1 - idempotent scheduler shell for intel machine pre-review
# (WO-INTEL-HEALTH-20261031 / I1).
#
# WHAT: wraps `python tools/intel_pre_review.py --write --out ...` (that tool is
#       read-only on news.db: it never writes the DB, it only writes an independent
#       JSONL decision file).
# MODE: default = DryRun (preview, no file written). Use -Write to write JSONL.
# LOG:  appends to <LogDir>\run_<yyyyMMdd>.log (long-running output lands in files).
# RETENTION: a previous same-day artifact is moved into <OutDir>\archive (NO delete:
#            repo red line requires recycle-bin for deletions; a human may recycle later).
# IDEMPOTENT: same-day rerun replaces the artifact atomically (staging .tmp -> final);
#             a local mutex prevents concurrent runs (second run logs and exits 0).
# SAFETY: does NOT register scheduled tasks, does NOT write system config / registry /
#         services. Scheduling / task registration requires Lao Tian L2 approval first.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\run_intel_pre_review.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\run_intel_pre_review.ps1 -Write
#   ... -Write -Limit 200 -KeepDays 14 -OutDir C:\Temp\intel_pre_review
#   ... -Write -DbPath C:\aios\news_collector\news.db   (optional DB path override)
# Exit: 0 = ok (including dry-run and second-instance skip); 2 = config error;
#       3 = artifact verification failed; otherwise the wrapped tool exit code (1/2).

[CmdletBinding()]
param(
    [switch]$Write,
    [switch]$DryRun,
    [int]$Limit = 200,
    [int]$KeepDays = 14,
    [string]$OutDir = 'C:\Temp\intel_pre_review',
    [string]$LogDir = 'C:\Temp\intel_pre_review\logs',
    [string]$Python = 'python',
    [string]$DbPath = ''
)

$ErrorActionPreference = 'Stop'
$day = Get-Date -Format 'yyyyMMdd'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$toolPath = Join-Path $repoRoot 'tools\intel_pre_review.py'
$archiveDir = Join-Path $OutDir 'archive'
$finalPath = Join-Path $OutDir ('intel_pre_review_' + $day + '.jsonl')
$stagingPath = $finalPath + '.tmp'

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
$logFile = Join-Path $LogDir ('run_' + $day + '.log')

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = '[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] [' + $Level + '] ' + $Message
    try { Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 } catch { }
    Write-Output $line
}

function Move-ToArchive {
    param([string]$Path, [string]$Tag)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $target = Join-Path $archiveDir ((Split-Path -Leaf $Path) + '.' + $Tag)
    Move-Item -LiteralPath $Path -Destination $target -Force
    Write-Log 'INFO' ('archived ' + $Path + ' -> ' + $target)
}

if ($Write -and $DryRun) {
    Write-Log 'ERROR' '-Write and -DryRun are mutually exclusive'
    exit 2
}
if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Write-Log 'ERROR' ('python not found: ' + $Python)
    exit 2
}
if (-not (Test-Path -LiteralPath $toolPath)) {
    Write-Log 'ERROR' ('tool not found: ' + $toolPath)
    exit 2
}
if ($Limit -lt 1 -or $Limit -gt 200) {
    Write-Log 'ERROR' ('-Limit must be 1..200, got ' + $Limit)
    exit 2
}

$mode = if ($Write) { 'write' } else { 'dry_run' }
$mutex = New-Object System.Threading.Mutex($false, 'Local\intel_pre_review_shell')
$acquired = $false
try { $acquired = $mutex.WaitOne(0) } catch { $acquired = $false }
if (-not $acquired) {
    Write-Log 'WARN' 'another run is in progress; skip (idempotent)'
    exit 0
}

try {
    Write-Log 'INFO' ('mode=' + $mode + ' limit=' + $Limit + ' out=' + $OutDir)
    $toolArgs = @('-X', 'utf8', $toolPath, '--limit', [string]$Limit, '--json')
    if ($DbPath) { $toolArgs += @('--db', $DbPath) }
    if ($Write) {
        $toolArgs += @('--write', '--out', $stagingPath)
    }
    $output = & $Python @toolArgs 2>&1
    $rc = $LASTEXITCODE
    $text = ($output | Out-String).TrimEnd()
    # log the tool output but cap it (JSON payload can be huge; log stays readable)
    $maxToolLogLines = 80
    $logged = 0
    foreach ($line in ($text -split "`r?`n")) {
        if ($line) {
            $logged++
            if ($logged -le $maxToolLogLines) { Write-Log 'TOOL' $line }
        }
    }
    if ($logged -gt $maxToolLogLines) {
        Write-Log 'TOOL' ('... (' + ($logged - $maxToolLogLines) + ' lines truncated; full JSON is in the artifact)')
    }
    if ($rc -ne 0) {
        Write-Log 'ERROR' ('tool failed rc=' + $rc)
        Move-ToArchive $stagingPath ('failed-' + (Get-Date -Format 'HHmmss'))
        exit $rc
    }
    if ($Write) {
        if (-not (Test-Path -LiteralPath $stagingPath)) {
            Write-Log 'ERROR' 'tool rc=0 but staging artifact missing (fail-visible)'
            exit 3
        }
        # rotate: keep previous same-day artifact, never delete
        Move-ToArchive $finalPath ('prev-' + (Get-Date -Format 'HHmmss'))
        Move-Item -LiteralPath $stagingPath -Destination $finalPath -Force
        $bytes = (Get-Item -LiteralPath $finalPath).Length
        Write-Log 'INFO' ('artifact written: ' + $finalPath + ' bytes=' + $bytes)
        # retention: old artifacts are archived (no delete) after KeepDays
        $cutoff = (Get-Date).AddDays(-1 * $KeepDays)
        Get-ChildItem -LiteralPath $OutDir -File -Filter 'intel_pre_review_*.jsonl' |
            Where-Object { $_.FullName -ne $finalPath -and $_.LastWriteTime -lt $cutoff } |
            ForEach-Object { Move-ToArchive $_.FullName ('keep-' + $KeepDays + 'd') }
    } else {
        Write-Log 'INFO' 'dry-run complete; no artifact written (use -Write to persist)'
    }
    exit 0
} finally {
    try { [void]$mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
}
