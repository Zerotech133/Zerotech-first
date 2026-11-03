# VERSION: register_jev_tasks_20261105 v1 (2026-11-04, task-8 / WO-JEV-OPS-20261103)
# ASCII-only on purpose: Windows PowerShell 5.1 / non-UTF8 consoles / S4U safe.
#
# PURPOSE
#   A5 pre-stage package for the 2026-11-05 06:00 L2 window: register the two JEV
#   scheduled tasks that make the JEV review loop actually roll:
#     \JevQueueRefresh   daily 08:10   writes news.db.jev_review_queue (append-only)
#     \JevCalibDaily     daily 07:50   read-only dec_audit -> outputs/jev_calib_<date>.json/.md
#
# SAFETY CONTRACT (hard)
#   * Default mode = DRY RUN: prints the five L2 elements + current task state.
#     It does NOT create / modify / delete any scheduled task (read-only queries only).
#   * Real registration only with -Execute AND a non-empty -A0Approval string,
#     in the 11-05 window, after A0 confirms the A5 item item-by-item (K-STOCK-416).
#   * Rollback (unregister) only with -Unregister AND -A0Approval.
#   * This package is NOT an L2 approval by itself.
#
# USAGE
#   # 1) dry run (safe, no changes) - use this at 05:30 briefing
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\register_jev_tasks_20261105.ps1 -DryRun
#   # 2) window only, after A0 per-item confirmation (elevated PowerShell)
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\register_jev_tasks_20261105.ps1 -Execute -A0Approval "A5 approved 2026-11-05 05:4x"
#   # 3) rollback (elevated PowerShell)
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\ops\register_jev_tasks_20261105.ps1 -Unregister -A0Approval "rollback A5 2026-11-05"
#   # optional: -Tasks Queue|Calib|All  -BackupDir <dir>  -LogPath <file>  -Force
#
# EXIT CODES: 0 = ok (dry run / execute verified / unregister verified)
#             1 = execution failure (best-effort rollback attempted)
#             2 = refused / precondition failed (no change made)
#             3 = post-change verification failed (inspect before retry)

[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateSet('All', 'Queue', 'Calib')]
    [string]$Tasks = 'All',
    [switch]$Execute,
    [switch]$Unregister,
    [string]$A0Approval = '',
    [switch]$Force,
    [string]$BackupDir = 'C:\Temp\jev_tasks_backup_20261105',
    [string]$LogPath = 'C:\Temp\jev_l2_A5_register.log'
)

$ErrorActionPreference = 'Continue'
$script:Changed = New-Object System.Collections.ArrayList

# ---------------------------------------------------------------------------
# Constants / task specs
# ---------------------------------------------------------------------------
$ProdRoot = 'C:\aios'
$PythonExe = 'C:\Program Files\Python311\python.exe'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TaskBag = @(
    [pscustomobject]@{
        Short = 'Queue'
        Name = '\JevQueueRefresh'
        Time = '08:10'
        TimeLimit = 'PT20M'
        Desc = 'JEV review queue rolling refresh daily 08:10 (append-only to news.db.jev_review_queue; backup + single tx + idempotent; L2 A5 2026-11-05)'
        ToolRel = 'tools\jev_queue_refresh.py'
        ToolArgs = ('--apply --confirm --limit 500 --db "' + $ProdRoot + '\news_collector\news.db" --backup-dir "C:\Temp\jev_queue_backups"')
        LogFile = 'C:\Temp\jev_queue_refresh_daily.log'
        DataCheck = ($ProdRoot + '\news_collector\news.db')
        Impact = @(
            'Writes ONLY news.db.jev_review_queue: INSERT OR IGNORE, <=500 rows per run, idempotent on id=news_id.',
            'Reads news.db (WAL, ~1.4-1.8s for the candidate scan) then takes a consistent sqlite backup (~131MB) BEFORE any write; single transaction; failure = rollback.',
            'No service restart, no port, no nginx, no other DB/table, no external network.',
            'Daily 08:10 (after JevDriftDaily 07:10, before DayBatch 08:30; outside the 21:50-07:00 JEV night window).',
            'Backups accumulate in C:\Temp\jev_queue_backups (~131MB per writing run): keep last 7, older ones via Recycle Bin (AGENTS red line: no rm/del).',
            'Failure leaves last-result != 0 in Task Scheduler; stdout/stderr go to the task log path below.',
            ('Task log: ' + 'C:\Temp\jev_queue_refresh_daily.log')
        )
        Irreversible = @(
            'Only persistent change is appended rows in jev_review_queue (production write).',
            'A consistent pre-write backup and a receipt JSON (all inserted news ids) are always produced, so one batch can be rolled back precisely.',
            'No UPDATE/DELETE of existing rows, no schema change, no credential/port/service change.'
        )
        Alternatives = @(
            'Do not add a task: append the same call as P6 to jev-eval\jev_drift_task.ps1 (fewer task objects, but couples failure domain with drift task).',
            'Weekly batch: same command with --limit 2000 once a week (fewer backups, review latency up to 7 days).',
            'Read-only mode: run dry-run daily and let a human import candidates (zero production write, queue still static).',
            'Status quo: keep the 2026-10-30 static queue (no L2 change).'
        )
        Rollback = @(
            'schtasks /End /TN "\JevQueueRefresh"',
            'schtasks /Delete /TN "\JevQueueRefresh" /F',
            'Restore previous XML (if any) from -BackupDir: schtasks /Create /TN "\JevQueueRefresh" /XML "<BackupDir>\JevQueueRefresh.before.xml" /F',
            'Undo the last inserted batch: read the newest *.receipt.json in C:\Temp\jev_queue_backups and DELETE those ids only (see the A5 L2 pre-stage doc under docs/ops, section 5).',
            'Worst case: restore the pre-write .bak snapshot (stop writers first, L2 window only).'
        )
    },
    [pscustomobject]@{
        Short = 'Calib'
        Name = '\JevCalibDaily'
        Time = '07:50'
        TimeLimit = 'PT10M'
        Desc = 'JEV posterior calibration report daily 07:50 (read-only dec_audit -> outputs/jev_calib_<date>.json/.md; L2 A5 2026-11-05)'
        ToolRel = 'tools\jev_calib_runner.py'
        ToolArgs = ('--db "' + $ProdRoot + '\agent_platform\data\jev_audit\jev_audit.db" --out-dir "' + $ProdRoot + '\outputs"')
        LogFile = 'C:\Temp\jev_calib_daily.log'
        DataCheck = ($ProdRoot + '\agent_platform\data\jev_audit\jev_audit.db')
        Impact = @(
            'Read-only on agent_platform/data/jev_audit/jev_audit.db (mode=ro + query_only=1); measured ~0.6s for 115k rows.',
            'Writes only two report files under C:\aios\outputs (json ~7KB, md ~3KB, atomic replace, same name per day).',
            'No DB write, no threshold/weight change, no service restart, no port, no network.',
            'Daily 07:50 (right after JevDriftDaily 07:10 which fills dec_audit posterior; outside the night window).',
            ('Task log: ' + 'C:\Temp\jev_calib_daily.log')
        )
        Irreversible = @(
            'None: DB is read-only; report files are regenerable; outputs/ is gitignored.',
            'No task data is written by the tool itself.'
        )
        Alternatives = @(
            'Append as P6 inside jev-eval\jev_drift_task.ps1 instead of a new task (same window, one less task; still an L2 change to an existing task action).',
            'Manual/on-demand run only (no scheduled task; no daily continuity).',
            'Merge the metrics into tools/jev_audit.py output (larger code change, different owner).'
        )
        Rollback = @(
            'schtasks /End /TN "\JevCalibDaily"',
            'schtasks /Delete /TN "\JevCalibDaily" /F',
            'Restore previous XML (if any): schtasks /Create /TN "\JevCalibDaily" /XML "<BackupDir>\JevCalibDaily.before.xml" /F',
            'Report files need no rollback (regenerable).'
        )
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = ('[{0}] [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    Write-Output $line
    if (-not $script:DryMode) {
        try {
            $dir = Split-Path -Parent $LogPath
            if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Force -Path $dir | Out-Null
            }
            $line | Out-File -LiteralPath $LogPath -Append -Encoding utf8
        } catch {
            Write-Output ('[WARN] cannot append log: ' + $_.Exception.Message)
        }
    }
}

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Get-SelectedSpecs {
    param([string]$Filter)
    if ($Filter -eq 'All') { return @($TaskBag) }
    return @($TaskBag | Where-Object { $_.Short -eq $Filter })
}

function Get-TaskState {
    param([string]$Name)
    $short = $Name.TrimStart('\')
    $task = $null
    try {
        $task = Get-ScheduledTask -TaskName $short -ErrorAction SilentlyContinue
    } catch {
        $task = $null
    }
    if ($null -eq $task) { return @{ Exists = $false } }
    $info = $null
    try {
        $info = Get-ScheduledTaskInfo -TaskName $short -ErrorAction SilentlyContinue
    } catch {
        $info = $null
    }
    $next = ''
    $last = ''
    if ($null -ne $info) {
        if ($null -ne $info.NextRunTime) {
            $next = $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')
        }
        $last = [string]$info.LastTaskResult
    }
    return @{
        Exists = $true
        State = [string]$task.State
        NextRun = $next
        LastResult = $last
        TaskPath = [string]$task.TaskPath
    }
}

function Get-TaskXml {
    param([string]$Name)
    $short = $Name.TrimStart('\')
    try {
        $xml = Export-ScheduledTask -TaskName $short -ErrorAction SilentlyContinue
    } catch {
        $xml = $null
    }
    if ($null -eq $xml -or $xml -eq '') {
        $raw = & schtasks /Query /TN $Name /XML 2>$null
        if ($LASTEXITCODE -ne 0 -or $null -eq $raw) { return $null }
        return (($raw | Out-String).Trim())
    }
    return [string]$xml
}

function Get-StartBoundary {
    param([string]$Hhmm)
    $parts = $Hhmm.Split(':')
    $candidate = (Get-Date).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    if ((Get-Date) -gt $candidate) { $candidate = $candidate.AddDays(1) }
    return $candidate.ToString('yyyy-MM-ddTHH:mm:ss+08:00')
}

function Get-CurrentSid {
    try {
        return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    } catch {
        return 'S-1-5-21-0-0-0-1001'
    }
}

function ConvertTo-XmlText {
    param([string]$Text)
    return ($Text.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;'))
}

function Get-ActionPreview {
    param($Spec)
    return ('"' + $PythonExe + '" -X utf8 -B "' + ($ProdRoot + '\' + $Spec.ToolRel) + '" ' + $Spec.ToolArgs + ' >> "' + $Spec.LogFile + '" 2>&1')
}

function Get-TaskXmlText {
    param($Spec, [string]$StartBoundary)
    $sid = Get-CurrentSid
    $preview = Get-ActionPreview $Spec
    $argsEscaped = ConvertTo-XmlText $preview
    $template = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{DESC}</Description>
    <URI>\{NAME}</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>{SID}</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>{TIMELIMIT}</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{START}</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Windows\System32\cmd.exe</Command>
      <Arguments>/c "{PREVIEW}"</Arguments>
      <WorkingDirectory>{WORKDIR}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@
    return $template.Replace('{DESC}', (ConvertTo-XmlText $Spec.Desc)).
        Replace('{NAME}', $Spec.Name.TrimStart('\')).
        Replace('{SID}', $sid).
        Replace('{TIMELIMIT}', $Spec.TimeLimit).
        Replace('{START}', $StartBoundary).
        Replace('{PREVIEW}', $argsEscaped).
        Replace('{WORKDIR}', $ProdRoot)
}

function Get-PrereqCheck {
    param([string[]]$Names)
    $problems = New-Object System.Collections.ArrayList
    $warns = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        [void]$problems.Add(('python missing: ' + $PythonExe))
    }
    if (-not (Test-Path -LiteralPath $ProdRoot)) {
        [void]$problems.Add(('production root missing (junction C:\aios): ' + $ProdRoot))
    }
    foreach ($name in $Names) {
        $spec = $TaskBag | Where-Object { $_.Name -eq $name }
        if ($null -eq $spec) { continue }
        $localTool = Join-Path $RepoRoot $spec.ToolRel
        if (-not (Test-Path -LiteralPath $localTool)) {
            [void]$problems.Add(('package tool missing in repo: ' + $localTool))
        }
        $prodTool = Join-Path $ProdRoot $spec.ToolRel
        if (-not (Test-Path -LiteralPath $prodTool)) {
            if ($script:Mode -eq 'Execute') {
                [void]$problems.Add(('production tool missing (merge WO-JEV-OPS first): ' + $prodTool))
            } else {
                [void]$warns.Add(('production tool not there yet (expected before 21:40 merge): ' + $prodTool))
            }
        }
        if (-not (Test-Path -LiteralPath $spec.DataCheck)) {
            [void]$problems.Add(('data file missing: ' + $spec.DataCheck))
        }
    }
    $outputsDir = Join-Path $ProdRoot 'outputs'
    if (-not (Test-Path -LiteralPath $outputsDir)) {
        [void]$warns.Add(('outputs dir missing (will be created on execute): ' + $outputsDir))
    }
    return @{ Problems = $problems; Warnings = $warns }
}

function Backup-Task {
    param([string]$Name)
    $short = $Name.TrimStart('\')
    $xml = Get-TaskXml $Name
    if ($null -ne $xml -and $xml -ne '') {
        $target = Join-Path $BackupDir ($short + '.before.xml')
        [System.IO.File]::WriteAllText($target, $xml, [System.Text.Encoding]::Unicode)
        return $target
    }
    $marker = Join-Path $BackupDir ($short + '.NOT_EXIST.txt')
    $text = ('NOT_EXIST ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' (no previous task XML to restore)')
    [System.IO.File]::WriteAllText($marker, $text, [System.Text.Encoding]::UTF8)
    return $null
}

function Restore-Backup {
    param([string]$Name)
    $short = $Name.TrimStart('\')
    $source = Join-Path $BackupDir ($short + '.before.xml')
    if (-not (Test-Path -LiteralPath $source)) {
        return $false
    }
    $xml = [System.IO.File]::ReadAllText($source, [System.Text.Encoding]::Unicode)
    $temp = Join-Path $BackupDir ($short + '.restore.xml')
    [System.IO.File]::WriteAllText($temp, $xml, [System.Text.Encoding]::Unicode)
    & schtasks /Create /TN $Name /XML $temp /F | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-TaskRegistered {
    param($Spec)
    $state = Get-TaskState $Spec.Name
    if (-not $state.Exists) {
        return @{ Ok = $false; Detail = 'task not found after registration' }
    }
    $xml = Get-TaskXml $Spec.Name
    if ($null -eq $xml -or $xml.IndexOf($Spec.ToolRel, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return @{ Ok = $false; Detail = 'task XML does not reference tool ' + $Spec.ToolRel }
    }
    if ($state.NextRun -eq '') {
        return @{ Ok = $false; Detail = 'next run time is empty' }
    }
    $expected = [datetime]::ParseExact($state.NextRun, 'yyyy-MM-dd HH:mm:ss',
        [System.Globalization.CultureInfo]::InvariantCulture)
    if ($expected.ToString('HH:mm') -ne $Spec.Time) {
        return @{ Ok = $false; Detail = ('next run time ' + $state.NextRun + ' != expected ' + $Spec.Time) }
    }
    return @{ Ok = $true; Detail = ('state=' + $state.State + ' next=' + $state.NextRun + ' last=' + $state.LastResult) }
}

# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------
$Mode = 'DryRun'
if ($DryRun -and ($Execute -or $Unregister)) {
    Write-Output '[REFUSED] -DryRun cannot be combined with -Execute / -Unregister (rc=2).'
    exit 2
}
if ($Execute -and $Unregister) {
    Write-Output '[REFUSED] -Execute and -Unregister are mutually exclusive (rc=2).'
    exit 2
}
if ($Execute) { $Mode = 'Execute' }
elseif ($Unregister) { $Mode = 'Unregister' }
else { $Mode = 'DryRun' }
$script:Mode = $Mode
$script:DryMode = ($Mode -eq 'DryRun')
$Selected = Get-SelectedSpecs $Tasks

if ($Mode -ne 'DryRun' -and [string]::IsNullOrWhiteSpace($A0Approval)) {
    Write-Output '[REFUSED] real task change requires -A0Approval "<per-item A5 confirmation text>" (K-STOCK-416: silence is not approval). rc=2'
    exit 2
}
if ($Mode -ne 'DryRun' -and -not (Test-IsAdmin)) {
    Write-Output '[REFUSED] real task change requires ELEVATED PowerShell (run as Administrator). rc=2'
    exit 2
}

# ---------------------------------------------------------------------------
# Header / state / prereq
# ---------------------------------------------------------------------------
if ($Mode -eq 'DryRun') {
    Write-Output '================================================================================'
    Write-Output 'JEV TWO-TASK REGISTRATION PACKAGE - DRY RUN (no task created/modified/deleted)'
    Write-Output 'A5 of the 2026-11-05 06:00 L2 window | task-8 / WO-JEV-OPS-20261103'
    Write-Output 'This is a PRE-STAGE package. It is NOT an L2 approval.'
    Write-Output 'Real registration is allowed only in the 11-05 window after A0 confirms A5'
    Write-Output 'item-by-item at the 05:30 review (K-STOCK-416: silence is not approval).'
    Write-Output '================================================================================'
} else {
    Write-Output ('================================================================================')
    Write-Output ('JEV TWO-TASK REGISTRATION - MODE=' + $Mode)
    Write-Output ('A0 approval text: ' + $A0Approval)
    Write-Output ('================================================================================')
}
Write-Output ('[ENV] as_of=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output ('[ENV] repo_root=' + $RepoRoot)
Write-Output ('[ENV] prod_root=' + $ProdRoot + '  python=' + $PythonExe)
Write-Output ('[ENV] backup_dir=' + $BackupDir + '  log=' + $LogPath)
Write-Output '[STATE] (read-only query, before any action)'
$stateBefore = @{}
foreach ($spec in $Selected) {
    $state = Get-TaskState $spec.Name
    if ($state.Exists) {
        $stateBefore[$spec.Name] = ('REGISTERED state=' + $state.State + ' next=' + $state.NextRun + ' last=' + $state.LastResult)
    } else {
        $stateBefore[$spec.Name] = 'NOT_REGISTERED'
    }
    Write-Output ('  ' + $spec.Name + ': ' + $stateBefore[$spec.Name])
}
$prereq = Get-PrereqCheck @($Selected | ForEach-Object { $_.Name })
foreach ($p in $prereq.Problems) { Write-Output ('[PREREQ][FAIL] ' + $p) }
foreach ($w in $prereq.Warnings) { Write-Output ('[PREREQ][WARN] ' + $w) }
if ($prereq.Problems.Count -eq 0) { Write-Output '[PREREQ] PASS (hard prerequisites ok)' }

if ($Mode -eq 'DryRun') {
    foreach ($spec in $Selected) {
        $start = Get-StartBoundary $spec.Time
        $preview = Get-ActionPreview $spec
        $taskXml = Get-TaskXmlText -Spec $spec -StartBoundary $start
        try {
            [void]([xml]$taskXml)
        } catch {
            Write-Output ('[FAIL] generated task XML is NOT well-formed for ' + $spec.Name + ': ' + $_.Exception.Message + ' (rc=2, no change made)')
            exit 2
        }
        Write-Output ''
        Write-Output '--------------------------------------------------------------------------------'
        Write-Output ('[TASK] ' + $spec.Name + '   daily ' + $spec.Time + '   (time limit ' + $spec.TimeLimit + ')')
        Write-Output '--------------------------------------------------------------------------------'
        Write-Output '[1] COMMAND (full text)'
        Write-Output ('  register (one-time, elevated, in L2 window only; after A0 per-item OK):')
        Write-Output ('    schtasks /Create /TN "' + $spec.Name + '" /XML "' + $BackupDir + '\' + $spec.Name.TrimStart('\') + '.xml" /F')
        Write-Output ('  task action (runs every day at ' + $spec.Time + '):')
        Write-Output ('    cmd.exe /c "' + $preview + '"')
        Write-Output ('  task log: ' + $spec.LogFile)
        Write-Output ('  task XML (write to ' + $BackupDir + '\' + $spec.Name.TrimStart('\') + '.xml on -Execute):')
        Write-Output '-----BEGIN TASK XML-----'
        $taskXml -split "`r?`n" | ForEach-Object { Write-Output $_ }
        Write-Output '-----END TASK XML-----'
        Write-Output ''
        Write-Output '[2] IMPACT'
        foreach ($line in $spec.Impact) { Write-Output ('  - ' + $line) }
        Write-Output ''
        Write-Output '[3] IRREVERSIBLE POINTS'
        foreach ($line in $spec.Irreversible) { Write-Output ('  - ' + $line) }
        Write-Output ''
        Write-Output '[4] ALTERNATIVES'
        foreach ($line in $spec.Alternatives) { Write-Output ('  - ' + $line) }
        Write-Output ''
        Write-Output '[5] ROLLBACK'
        foreach ($line in $spec.Rollback) { Write-Output ('  - ' + $line) }
    }
    Write-Output ''
    Write-Output '--------------------------------------------------------------------------------'
    Write-Output '[VERIFY AFTER REGISTER] (execute in the 11-05 window, copy/paste)'
    foreach ($spec in $Selected) {
        Write-Output ('  schtasks /Query /TN "' + $spec.Name + '" /FO LIST /V')
    }
    foreach ($spec in $Selected) {
        Write-Output ('  schtasks /Run /TN "' + $spec.Name + '"')
    }
    foreach ($spec in $Selected) {
        Write-Output ('  Get-Content ' + $spec.LogFile + ' -Tail 20')
    }
    $hasQueue = ($Selected | Where-Object { $_.Short -eq 'Queue' }).Count -gt 0
    if ($hasQueue) {
        Write-Output '  Get-ChildItem C:\Temp\jev_queue_backups | Sort-Object LastWriteTime -Descending | Select-Object -First 3'
        Write-Output '  python -X utf8 -B C:\aios\tools\jev_queue_ops.py --db C:\aios\news_collector\news.db --audit-db C:\aios\agent_platform\data\jev_audit\jev_audit.db'
    }
    Write-Output ''
    Write-Output '--------------------------------------------------------------------------------'
    Write-Output '[NO TASK CHANGES] dry run finished: 0 tasks created / modified / deleted.'
    Write-Output '[NO TASK CHANGES] only read-only queries were issued; rc=0.'
    $changed = $false
    foreach ($spec in $Selected) {
        $state = Get-TaskState $spec.Name
        if ($state.Exists) {
            $now = ('REGISTERED state=' + $state.State + ' next=' + $state.NextRun + ' last=' + $state.LastResult)
        } else {
            $now = 'NOT_REGISTERED'
        }
        if ($now -ne $stateBefore[$spec.Name]) {
            Write-Output ('[FAIL] task state changed during dry run for ' + $spec.Name + ': before=' + $stateBefore[$spec.Name] + ' after=' + $now)
            $changed = $true
        }
    }
    if ($changed) {
        Write-Output '[FAIL] task state change detected; rc=3'
        exit 3
    }
    Write-Output ('[NO TASK CHANGES] state before == after for: ' + (($Selected | ForEach-Object { $_.Name + '=' + $stateBefore[$_.Name] }) -join '; '))
    if ($prereq.Problems.Count -gt 0) {
        Write-Output '[NOTE] hard prereq failures above do NOT block the dry run, but WILL block -Execute.'
    }
    exit 0
}

# ---------------------------------------------------------------------------
# Execute / Unregister
# ---------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $BackupDir)) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
}
Write-Log (('mode=' + $Mode + ' tasks=' + $Tasks + ' a0=' + $A0Approval))

if ($prereq.Problems.Count -gt 0) {
    foreach ($p in $prereq.Problems) { Write-Log ('[REFUSED] ' + $p) 'ERROR' }
    Write-Log 'no change made (rc=2)' 'ERROR'
    exit 2
}

if ($Mode -eq 'Unregister') {
    $exitCode = 0
    foreach ($spec in $Selected) {
        $state = Get-TaskState $spec.Name
        $backup = Backup-Task $spec.Name
        if (-not $state.Exists) {
            Write-Log ($spec.Name + ' already absent (backup marker: ' + $BackupDir + ')')
            continue
        }
        Write-Log ('backup before unregister: ' + $(if ($null -eq $backup) { $BackupDir } else { $backup }))
        & schtasks /Delete /TN $spec.Name /F | Out-Null
        $rc = $LASTEXITCODE
        if ($rc -ne 0) {
            Write-Log ($spec.Name + ' delete failed rc=' + $rc) 'ERROR'
            $exitCode = 1
            continue
        }
        [void]$script:Changed.Add($spec.Name)
        $after = Get-TaskState $spec.Name
        if ($after.Exists) {
            Write-Log ($spec.Name + ' still present after delete (rc=3)') 'ERROR'
            $exitCode = 3
        } else {
            Write-Log ($spec.Name + ' unregistered and verified absent')
        }
    }
    Write-Log ('rollback summary changed=' + ($script:Changed -join ','))
    exit $exitCode
}

# Execute mode
$registered = New-Object System.Collections.ArrayList
$failure = $null
foreach ($spec in $Selected) {
    $state = Get-TaskState $spec.Name
    $backup = Backup-Task $spec.Name
    Write-Log ('backup before register: ' + $(if ($null -eq $backup) { 'none (task did not exist)' } else { $backup }))
    if ($state.Exists -and -not $Force) {
        $failure = ('task already exists; pass -Force to replace (backup taken): ' + $spec.Name)
        Write-Log ('[REFUSED] ' + $failure) 'ERROR'
        break
    }
    $start = Get-StartBoundary $spec.Time
    $xmlPath = Join-Path $BackupDir ($spec.Name.TrimStart('\') + '.xml')
    $taskXml = Get-TaskXmlText -Spec $spec -StartBoundary $start
    try {
        [System.IO.File]::WriteAllText($xmlPath, $taskXml, [System.Text.Encoding]::Unicode)
        [void]([xml]$taskXml)
    } catch {
        $failure = ('generated task XML is invalid / not writable: ' + $_.Exception.Message)
        Write-Log ('[FAIL] ' + $failure) 'ERROR'
        break
    }
    Write-Log ('registering ' + $spec.Name + ' from ' + $xmlPath + ' (start=' + $start + ')')
    & schtasks /Create /TN $spec.Name /XML $xmlPath /F | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        $failure = ('schtasks /Create failed rc=' + $rc + ' for ' + $spec.Name)
        Write-Log ('[FAIL] ' + $failure) 'ERROR'
        break
    }
    [void]$registered.Add($spec.Name)
    $verify = Test-TaskRegistered $spec
    if (-not $verify.Ok) {
        $failure = ('post-register verify failed: ' + $spec.Name + ' :: ' + $verify.Detail)
        Write-Log ('[FAIL] ' + $failure) 'ERROR'
        break
    }
    Write-Log ('REGISTER_OK ' + $spec.Name + ' :: ' + $verify.Detail)
}

if ($null -ne $failure) {
    Write-Log 'rolling back tasks created in this run (best effort)' 'WARN'
    foreach ($created in $registered) {
        $restored = Restore-Backup $created
        if ($restored) {
            Write-Log ('restored previous XML for ' + $created) 'WARN'
        } else {
            & schtasks /Delete /TN $created /F | Out-Null
            Write-Log ('deleted newly created task ' + $created + ' (no previous XML)') 'WARN'
        }
    }
    Write-Log ('FAILED rc=1: ' + $failure) 'ERROR'
    exit 1
}

Write-Log ('EXECUTE OK changed=' + ($registered -join ',') + ' (verified)')
Write-Log 'next: manual run + log/output checks listed in the dry-run [VERIFY AFTER REGISTER] block'
exit 0
