#requires -Version 5.1
<#
.SYNOPSIS
    Weekly curated-layer rebuild: STAGE -> verify lines all OK -> auto REPLACE
    with one previous generation kept (tushare_curated.prev).

.DESCRIPTION
    Called by the CuratedWeekly scheduled task (Sunday 09:00). Until 2026-10-25
    the runner was stage-only: it rebuilt and verified <out>.tmp, then waited for
    a human to run --replace; the live layer therefore kept aging (2.6+ days on
    10-25). EXEC-R17 (approved by owner 2026-10-25) automates the switch with a
    mandatory one-generation rollback:

      1) STAGE: refresh_curated.py (no --replace) with the kg_sync idle guard;
      2) parse the output: replace ONLY if rc==0 AND both "verify 1/2 [OK]" and
         "verify 2/2 [OK]" lines are present (ASCII markers "1/2"/"2/2" followed
         by a "[OK]" line are used, so this file stays ASCII-only for PS 5.1);
      3) REPLACE: refresh_curated.py --replace --keep-prev --from-tmp
         --from-tmp re-uses the already verified <out>.tmp (it does NOT re-run
         the 22-52 minute merge); --keep-prev renames the old live layer to
         <out>.prev (one generation, rollback handle) instead of deleting it.
    Any failure (stage rc!=0, missing verify [OK], guardrail hit) -> NO replace,
    the staging dir is kept for inspection, and an alert flag
    jev-eval\alerts\CURATED_FAILED_<stamp>.flag is written (picked up by
    jev-eval\alert_forward.ps1, which forwards *.flag).

    Guardrails before REPLACE (belt and suspenders; refresh_curated.py --from-tmp
    repeats the row-level check and enforces new_rows >= old_rows):
      - rc must be 0 and both verify lines found;
      - staging dir must contain at least one parquet table;
      - staging table count must not be smaller than the live layer count.

    Switches:
      -StageOnly : emergency fallback to the old stage-only behaviour (no replace,
                   no alert flag; human reviews the log and replaces manually).
      -DryRun    : print planned commands/decisions only, execute nothing.
      -SimulateStageOutput / -SimulateStageExitCode / -LogFile / -LiveDir /
      -TmpDir / -AlertDir : rehearsal/unit-test overrides (empty = production
                   paths). They let the decision logic be exercised without
                   running STAGE or REPLACE against real data.

    This runner never deletes data: on success refresh_curated.py performs the
    two-phase swap and keeps <out>.prev; on failure <out>.tmp stays in place.
    The raw layer is opened read-only by refresh_curated.py.
#>
[CmdletBinding()]
param(
    [int]$GuardMinutes = 60,
    [switch]$StageOnly,
    [switch]$DryRun,
    # --- rehearsal / unit-test overrides (default empty = production paths) ---
    [string]$SimulateStageOutput = '',
    [int]$SimulateStageExitCode = 0,
    [string]$LogFile = '',
    [string]$LiveDir = '',
    [string]$TmpDir = '',
    [string]$AlertDir = ''
)

$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
$tool = Join-Path $PSScriptRoot 'refresh_curated.py'
$dataRoot = Join-Path $repo 'quant_backtest\data'

if (-not $LiveDir) { $LiveDir = Join-Path $dataRoot 'tushare_curated' }
if (-not $TmpDir) { $TmpDir = Join-Path $dataRoot 'tushare_curated.tmp' }
if (-not $AlertDir) { $AlertDir = Join-Path $repo 'jev-eval\alerts' }
if (-not $LogFile) { $LogFile = Join-Path (Join-Path $repo 'logs') 'curated_weekly.log' }

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Text)
    $line = '{0} | {1}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss'), $Text
    Write-Host $line
    try {
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch {
        Write-Host ('WARN: cannot append to log: {0}' -f $_.Exception.Message)
    }
}

function Write-AlertFlag {
    param([string]$Prefix, [string[]]$Detail)
    try {
        if (-not (Test-Path -LiteralPath $AlertDir)) {
            New-Item -ItemType Directory -Path $AlertDir -Force | Out-Null
        }
        $stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
        $flag = Join-Path $AlertDir ('{0}_{1}.flag' -f $Prefix, $stamp)
        $body = @(
            'curated_weekly_run.ps1 alert flag',
            ('time : ' + (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')),
            ('host : ' + $env:COMPUTERNAME),
            ('live : ' + $LiveDir),
            ('stage: ' + $TmpDir)
        ) + $Detail
        Set-Content -LiteralPath $flag -Value $body -Encoding UTF8
        Write-Log ('ALERT FLAG written: ' + $flag)
        return $flag
    } catch {
        Write-Log ('ERROR: cannot write alert flag: ' + $_.Exception.Message)
        return $null
    }
}

function Get-ParquetCount {
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir)) { return 0 }
    return @(Get-ChildItem -LiteralPath $Dir -Filter '*.parquet' -File `
            -ErrorAction SilentlyContinue).Count
}

function Resolve-Python {
    $fixed = 'C:\Program Files\Python311\python.exe'
    if (Test-Path -LiteralPath $fixed) { return $fixed }
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $cmd) { return $null }
    return $cmd.Source
}

$totalSw = [System.Diagnostics.Stopwatch]::StartNew()
$modeText = if ($DryRun) { 'DRY-RUN' } elseif ($StageOnly) { 'STAGE-ONLY' } else { 'AUTO-REPLACE' }
Write-Log '=== curated weekly rebuild START ==='
Write-Log ('repo={0}' -f $repo)
Write-Log ('mode={0} guard={1}min stage_only={2} dry_run={3}' -f `
        $modeText, $GuardMinutes, [bool]$StageOnly, [bool]$DryRun)
Write-Log ('live={0}' -f $LiveDir)
Write-Log ('tmp ={0}' -f $TmpDir)

if (-not (Test-Path -LiteralPath $tool)) {
    Write-Log ('ERROR runner missing: ' + $tool)
    exit 2
}

$python = Resolve-Python
$stageArgs = @('-B', $tool, '--guard-minutes', [string]$GuardMinutes)
$replaceArgs = @('-B', $tool, '--replace', '--keep-prev', '--from-tmp',
                 '--guard-minutes', [string]$GuardMinutes)

$runStage = (-not $DryRun) -and (-not $SimulateStageOutput)
if ($runStage -and ($null -eq $python)) {
    Write-Log 'ERROR: python not found (neither fixed path nor PATH)'
    exit 2
}

# ---------------------------------------------------------------- STAGE ----
$stageRc = 0
$stageOut = @()
$stageSw = [System.Diagnostics.Stopwatch]::StartNew()
if ($SimulateStageOutput) {
    if (-not (Test-Path -LiteralPath $SimulateStageOutput)) {
        Write-Log ('ERROR simulated stage output missing: ' + $SimulateStageOutput)
        exit 2
    }
    $stageOut = @(Get-Content -LiteralPath $SimulateStageOutput -Encoding UTF8)
    $stageRc = $SimulateStageExitCode
    Write-Log ('SIMULATE stage output={0} exit_code={1} (python NOT executed)' -f `
            $SimulateStageOutput, $stageRc)
    foreach ($line in $stageOut) { Write-Log ('  ' + [string]$line) }
} elseif ($DryRun) {
    Write-Log ('DRY-RUN: would exec STAGE: {0} {1}' -f $python, ($stageArgs -join ' '))
    Write-Log 'DRY-RUN: stage not executed (no data written, no pyarrow scan)'
} else {
    Write-Log ('exec STAGE: {0} {1}' -f $python, ($stageArgs -join ' '))
    $stageOut = @(& $python @stageArgs 2>&1)
    $stageRc = $LASTEXITCODE
    foreach ($line in $stageOut) { Write-Log ('  ' + [string]$line) }
}
$stageSw.Stop()
if ($runStage -or $SimulateStageOutput) {
    Write-Log ('STAGE END exit={0} elapsed={1:N1}s' -f $stageRc, $stageSw.Elapsed.TotalSeconds)
}

# ------------------------------------------------- parse verification lines ----
# ASCII-only marker scan: a "1/2" heading followed by a "[OK]" line means
# verify 1/2 passed; same for "2/2". If refresh_curated.py changes its wording
# the markers still work as long as "N/2" and "[OK]/[FAIL]" tokens remain.
$verifyOk1 = $false
$verifyOk2 = $false
$sawFail = $false
$section = ''
foreach ($raw in $stageOut) {
    $s = [string]$raw
    if ($s -match '(?<![0-9])1/2') { $section = '1'; continue }
    if ($s -match '(?<![0-9])2/2') { $section = '2'; continue }
    if ($s -match '^\s*\[FAIL\]') { $sawFail = $true; continue }
    if ($s -match '^\s*\[OK\]') {
        if ($section -eq '1') { $verifyOk1 = $true }
        elseif ($section -eq '2') { $verifyOk2 = $true }
    }
}
if ($stageOut.Count -gt 0) {
    Write-Log ('verify parse: 1/2_OK={0} 2/2_OK={1} saw_FAIL={2}' -f `
            $verifyOk1, $verifyOk2, $sawFail)
}

# -------------------------------------------------------- guardrails ----
$liveTables = Get-ParquetCount $LiveDir
$newTables = Get-ParquetCount $TmpDir
Write-Log ('guard: live_tables={0} tmp_tables={1}' -f $liveTables, $newTables)

$violations = @()
if (-not $verifyOk1) { $violations += 'verify 1/2 [OK] line not found' }
if (-not $verifyOk2) { $violations += 'verify 2/2 [OK] line not found' }
if ($newTables -le 0) {
    $violations += 'staging dir has no parquet tables'
} elseif ($liveTables -gt 0 -and $newTables -lt $liveTables) {
    $violations += ('table shrink: staging {0} < live {1}' -f $newTables, $liveTables)
}
$replaceAllowed = ($stageRc -eq 0) -and ($violations.Count -eq 0)

if ($DryRun) {
    if ($stageOut.Count -eq 0) {
        Write-Log 'DRY-RUN: no decision (stage not executed; use -SimulateStageOutput to rehearse)'
    } elseif ($replaceAllowed) {
        Write-Log ('DRY-RUN: WOULD REPLACE: {0} {1}' -f $python, ($replaceArgs -join ' '))
    } else {
        Write-Log ('DRY-RUN: WOULD SKIP replace + WOULD WRITE ALERT FLAG (stage_rc={0}; {1})' -f `
                $stageRc, ($violations -join '; '))
    }
    Write-Log ('=== curated weekly rebuild END dry-run elapsed={0:N1}s ===' -f `
            $totalSw.Elapsed.TotalSeconds)
    exit 0
}

# --------------------------------------------- emergency stage-only fallback ----
if ($StageOnly) {
    if ($stageRc -eq 0) {
        Write-Log 'STAGE-ONLY: staging layer verified/left in place; NO replace (emergency fallback)'
        Write-Log 'NEXT: review verify lines above, then run manually:'
        Write-Log ('      python tools\refresh_curated.py --replace --keep-prev --from-tmp')
    } else {
        Write-Log ('STAGE-ONLY: stage failed rc={0}; staging dir kept for inspection: {1}' -f `
                $stageRc, $TmpDir)
    }
    Write-Log ('=== curated weekly rebuild END exit={0} elapsed={1:N1}s ===' -f `
            $stageRc, $totalSw.Elapsed.TotalSeconds)
    exit $stageRc
}

# ------------------------------------------------------ no-replace / alert ----
if (-not $replaceAllowed) {
    $detail = @('stage_rc=' + [string]$stageRc) + $violations
    Write-Log ('NO REPLACE: ' + ($detail -join '; '))
    Write-Log ('staging dir kept for inspection: ' + $TmpDir)
    [void](Write-AlertFlag 'CURATED_FAILED' $detail)
    if ($stageRc -ne 0) { $exitCode = $stageRc }
    elseif ($violations.Count -gt 0) { $exitCode = 3 }
    else { $exitCode = 1 }
    Write-Log ('=== curated weekly rebuild END exit={0} elapsed={1:N1}s ===' -f `
            $exitCode, $totalSw.Elapsed.TotalSeconds)
    exit $exitCode
}

# -------------------------------------------------------------- REPLACE ----
Write-Log ('exec REPLACE: {0} {1}' -f $python, ($replaceArgs -join ' '))
$replaceSw = [System.Diagnostics.Stopwatch]::StartNew()
$replaceOut = @(& $python @replaceArgs 2>&1)
$replaceRc = $LASTEXITCODE
$replaceSw.Stop()
foreach ($line in $replaceOut) { Write-Log ('  ' + [string]$line) }
Write-Log ('REPLACE END exit={0} elapsed={1:N1}s' -f `
        $replaceRc, $replaceSw.Elapsed.TotalSeconds)

if ($replaceRc -ne 0) {
    Write-Log 'REPLACE FAILED: old live layer should be unchanged (two-phase swap is rollback-safe)'
    Write-Log ('staging dir kept for inspection: ' + $TmpDir)
    [void](Write-AlertFlag 'CURATED_REPLACE_FAILED' @(
        ('replace_rc=' + [string]$replaceRc),
        'old live layer expected unchanged; see REPLACE output in curated_weekly.log'))
    Write-Log ('=== curated weekly rebuild END exit={0} elapsed={1:N1}s ===' -f `
            $replaceRc, $totalSw.Elapsed.TotalSeconds)
    exit $replaceRc
}

$newest = Get-ChildItem -LiteralPath $LiveDir -Filter '*.parquet' -File `
    -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -ne $newest) {
    Write-Log ('live layer newest parquet mtime: ' + $newest.LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss'))
}
$prevDir = $LiveDir + '.prev'
Write-Log ('prev generation present: {0} ({1})' -f (Test-Path -LiteralPath $prevDir), $prevDir)
Write-Log 'NEXT: health card data.curated_freshness should turn green within one refresh cycle.'
Write-Log ('=== curated weekly rebuild END exit=0 elapsed={0:N1}s ===' -f `
        $totalSw.Elapsed.TotalSeconds)
exit 0
