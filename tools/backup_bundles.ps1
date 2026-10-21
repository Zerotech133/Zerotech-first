#requires -Version 5.1
<#
.SYNOPSIS
    Single-disk vault backup: git bundles for the four repos + a zip of the
    workspace-root Markdown and HTML files + a read-only manifest of the data
    root's shape (data_manifest.json), plus MANIFEST.txt and day-folder
    rotation.

.DESCRIPTION
    Layout written under <VaultRoot> (default: <workspace>\_vault):

        <VaultRoot>\<yyyy-MM-dd>\<short>-<yyyyMMdd>.bundle   (git bundle --all)
        <VaultRoot>\<yyyy-MM-dd>\workspace-docs-<yyyyMMdd>.zip
        <VaultRoot>\<yyyy-MM-dd>\data_manifest.json          (data-root shape)
        <VaultRoot>\<yyyy-MM-dd>\MANIFEST.txt

    * The workspace zip now carries two classes of root-level files: *.md
      (documents) and *.html/*.htm (standalone prototypes and previews). They
      are archived together under the historical name
      workspace-docs-<date>.zip, but planned and reported separately
      ("N md + M html") with an input-byte total for each class.
    * JSON is deliberately NOT archived. The workspace-root .mcp.json contains
      a plaintext tushare token in a URL query string (token=<secret>), so a
      root-wide *.json sweep would copy a live credential into every daily
      backup. package.json / package-lock.json are small and regenerable.
      .ps1 / .txt / .log files are code or log noise and can also carry
      secrets, so only *.md + *.html + *.htm are selected. .env / .env.* are
      excluded unconditionally as a second line of defence.
    * Why include HTML but not JSON is evidence-based, not a hunch: on
      2026-10-22 the root HTML prototypes were credential-scanned with a regex
      that also matches unquoted URL-query values (token|api_key|secret|
      password followed by '=' and >=16 chars). Every prototype had zero
      hits; .mcp.json had one. HTML is safe to back up, JSON is not.
    * data_manifest.json is the one artifact that describes something *outside*
      the vault: the shape of the quant data root
      (<repo>\quant_backtest\data, 43 top-level dirs / 268,044 files /
      42.53 GiB on 2026-10-22), which no git repository tracks. The generator
      tools\gen_data_manifest.py scans it read-only (~1.4 s warm) and records
      per-directory file counts, byte totals and newest mtime, plus the size
      and sha256 of catalog.json. Without it a restore rebuilds the code but
      has no way to know what the data tree was supposed to look like. The
      data itself is still not copied - this is a description, not a backup of
      the bytes.
    * The generator is invoked as
      'python -B tools\gen_data_manifest.py --out <dayDir>\data_manifest.json'
      and the product is registered in MANIFEST.txt like every other artifact
      (same line format, counted in '#' summary, eligible for carry-over).
      Python is looked up on PATH as 'python' and then as 'py -3'. Exit code 2
      from the generator means its soft --time-budget was hit: the JSON is
      still valid but the totals are partial (scan.partial=true), so it is
      registered with a WARN rather than dropped. A missing python or a
      missing generator is fail-open: WARN, an explicit '[skip]'/'skipped'
      counter in the output, the remaining artifacts still run and the exit
      code stays 0 - a shape manifest is not worth failing a backup over. A
      genuine generator failure (non-zero exit and no file) is an ordinary
      failed artifact, so the carry-over rule below can still register a
      same-day file whose bytes are unchanged.
    * -DryRun never runs python: it prints the plan line and an ETA only. The
      ETA comes from scan.elapsed_sec of the newest data_manifest.json already
      in the vault (fallback 2 s), because a dry run must not pay the 268k-file
      scan cost.
    * Repos and the workspace root are derived from $PSScriptRoot
      (tools -> repo -> workspace), so no non-ASCII path literal is baked in.
    * A failing repo is reported and skipped; the remaining repos still run.
    * A single unreadable archive input is skipped with a warning and a failed
      exit code, while the remaining files still go into the zip.
    * Concurrent runs are serialized by a per-day lock file
      <dayDir>\.backup.lock containing pid + host + start time. If a fresh lock
      is held, the second instance prints a FATAL message and exits 1 without
      touching the day folder. A lock older than -LockTimeoutMinutes is
      reported as stale and taken over. The lock is always released from a
      finally block (success, failure, exception).
    * When an artifact fails but the previous MANIFEST record and the file on
      disk still agree (byte count + sha256), the old record is carried into
      the new MANIFEST under a '#' comment. A partial failure therefore cannot
      turn a good older bundle into an unregistered orphan. A missing file or
      a changed hash is never carried ("prefer a gap over a lie").
    * -DryRun prints the whole plan and creates/modifies/deletes nothing. It
      also never creates the lock; it only prints where a real run would.
    * Rotation only touches child folders whose name is exactly yyyy-MM-dd,
      is strictly older than -KeepDays, and is not a reparse point.
    * No upload, no git config writes, no repo mutation (git bundle only reads).

.PARAMETER VaultRoot
    Destination root. Defaults to '<workspace>\_vault' where <workspace> is the
    parent of the repo that contains this script.

.PARAMETER KeepDays
    Keep date folders whose day is >= today - KeepDays. Default 14. 0 keeps
    only today's folder.

.PARAMETER LockTimeoutMinutes
    Age in minutes after which an existing lock file is considered stale and
    may be taken over. Default 120. Raise it if a legitimate run can exceed two
    hours (e.g. a very large aios bundle on a slow disk).

.PARAMETER DryRun
    Print the plan only. Nothing is created, overwritten or deleted, and no
    lock file is created.

.PARAMETER Exclude
    Extra filename globs (matched against the leaf name, case-insensitive) to
    keep out of the workspace zip. The built-in guards - .mcp.json, .env,
    .env.*, .env* - are always applied and cannot be removed, even when this
    parameter is supplied.

.NOTES
    Exit code: 0 = success (dry-run always 0), 1 = at least one bundle /
    workspace archive / data-manifest / rotation failure, or the day lock is
    held by another instance / cannot be acquired. A data_manifest that was
    skipped because python is missing is fail-open and does not change the
    exit code.
    Rotation permanently deletes old backup folders (not Recycle Bin) because
    reclaiming vault space is its purpose; use -DryRun to preview.
#>
[CmdletBinding()]
param(
    [string]$VaultRoot = '',
    [ValidateRange(0, 3650)][int]$KeepDays = 14,
    [ValidateRange(1, 10080)][int]$LockTimeoutMinutes = 120,
    [switch]$DryRun,
    [string[]]$Exclude = @('.mcp.json', '.env', '.env.*', '.env*')
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------------------------------------------------------- paths -----
if (-not $PSScriptRoot) {
    throw 'Cannot resolve $PSScriptRoot. Run this file with -File, do not paste it.'
}
$toolsDir  = $PSScriptRoot
$repoRoot  = Split-Path -Parent $toolsDir
$workspace = Split-Path -Parent $repoRoot

if (-not $VaultRoot) {
    $VaultRoot = Join-Path $workspace '_vault'
} elseif (-not [System.IO.Path]::IsPathRooted($VaultRoot)) {
    $VaultRoot = Join-Path (Get-Location).Path $VaultRoot
}
$VaultRoot = [System.IO.Path]::GetFullPath($VaultRoot).TrimEnd('\')
if ($VaultRoot -match '^[A-Za-z]:$') {
    throw "Refusing to use drive root '$VaultRoot' as VaultRoot."
}

foreach ($forbidden in @($workspace, $repoRoot)) {
    if ($VaultRoot.TrimEnd('\') -ieq ([System.IO.Path]::GetFullPath($forbidden)).TrimEnd('\')) {
        throw "Refusing to use '$VaultRoot' as VaultRoot: it is the workspace or the repo root."
    }
}

$now      = Get-Date
$dayName  = $now.ToString('yyyy-MM-dd')
$stamp    = $now.ToString('yyyyMMdd')
$dayDir   = Join-Path $VaultRoot $dayName
$manifest = Join-Path $dayDir 'MANIFEST.txt'
$lockPath = Join-Path $dayDir '.backup.lock'
$cutoff   = $now.Date.AddDays(-$KeepDays)

# Produced by tools\gen_data_manifest.py: describes the quant data root (dirs,
# file counts, bytes, catalog.json sha256) without copying any of it.
$dataManifestName   = 'data_manifest.json'
$dataManifestPath   = Join-Path $dayDir $dataManifestName
$dataManifestScript = Join-Path $toolsDir 'gen_data_manifest.py'

# This script stays pure ASCII on purpose: Windows PowerShell 5.1 reads a
# BOM-less .ps1 as the ANSI code page (CP936 on this host), so a literal Chinese
# string would become mojibake in a -File run. The Chinese strings the
# acceptance criteria ask for are assembled from code points instead:
#   $lockDryRunHint = "real run " + hui + jian + suo
#   $carryNoteZh    = "carried over from the previous successful record"
#   $dataManifestZh* = the -DryRun plan line for data_manifest.json:
#                      "will generate data_manifest.json (estimated N s)"
$lockDryRunHint = 'real run ' + [char]0x4F1A + [char]0x5EFA + [char]0x9501
$carryNoteZh = -join ([char[]]@(
    0x6CBF, 0x7528, 0x4E0A, 0x4E00, 0x6B21, 0x6210, 0x529F, 0x8BB0, 0x5F55))

# 0x5C06 0x751F 0x6210 0x6570 0x636E 0x6E05 0x5355 = "generate data manifest"
# 0xFF08 0x9884 0x8BA1 = "(estimated"
# 0x79D2 0xFF09       = "s)"
$dataManifestZhPrefix = -join ([char[]]@(
    0x5C06, 0x751F, 0x6210, 0x6570, 0x636E, 0x6E05, 0x5355))
$dataManifestZhOpen   = -join ([char[]]@(0xFF08, 0x9884, 0x8BA1))
$dataManifestZhClose  = -join ([char[]]@(0x79D2, 0xFF09))

# Label is ASCII and is also the bundle filename prefix.
$repos = @(
    [pscustomobject]@{ Label = 'aios';           Path = $repoRoot }
    [pscustomobject]@{ Label = 'zerobound-kg';   Path = (Join-Path $workspace 'zerobound-kg') }
    [pscustomobject]@{ Label = 'digital-legion'; Path = (Join-Path $workspace 'digital-legion') }
    [pscustomobject]@{ Label = 'res-downloader'; Path = (Join-Path $workspace 'res-downloader-py') }
)

# Every artifact a complete run should leave in $dayDir. The carry-over pass
# uses this list: a name this run did not produce may keep its old MANIFEST
# line when the file on disk still matches that line exactly.
$expectedArtifacts = @(
    $repos | ForEach-Object { '{0}-{1}.bundle' -f $_.Label, $stamp }
    'workspace-docs-{0}.zip' -f $stamp
    $dataManifestName
)

# ------------------------------------------------------- archive inputs -----
# Built-in guards are merged with -Exclude again (the default is not enough):
# a caller-supplied list can never re-include the token-bearing .mcp.json or
# a .env file by accident.
$builtinExcludes = @('.mcp.json', '.env', '.env.*', '.env*')
$excludePatterns = @($builtinExcludes + @($Exclude)) |
    ForEach-Object { ([string]$_).Trim() } |
    Where-Object { $_ } |
    Select-Object -Unique

function Test-ExcludedName {
    param([string]$Name)
    foreach ($pattern in $excludePatterns) {
        if ($Name -like $pattern) { return $true }
    }
    return $false
}

# Enumerate every root file once, so the exclusion report can name .mcp.json
# even though JSON is never a candidate for the archive.
$rootFiles = @(Get-ChildItem -LiteralPath $workspace -File -Force -ErrorAction SilentlyContinue |
    Sort-Object Name)
$excludedFiles = @($rootFiles | Where-Object { Test-ExcludedName $_.Name })
$archiveFiles = @($rootFiles | Where-Object {
    ($_.Extension -in @('.md', '.html', '.htm')) -and
    -not (Test-ExcludedName $_.Name)
})
$docs = @($archiveFiles | Where-Object { $_.Extension -ieq '.md' })
$html = @($archiveFiles | Where-Object { $_.Extension -ine '.md' })

function Get-TotalBytes {
    param([object[]]$Files)
    $sum = ($Files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return [long]0 }
    return [long]$sum
}

$docsBytes    = Get-TotalBytes $docs
$htmlBytes    = Get-TotalBytes $html
$archiveBytes = [long]$docsBytes + [long]$htmlBytes

function Get-ExcludedSummary {
    if ($excludedFiles.Count -eq 0) {
        return 'none present  (guards: {0})' -f ($excludePatterns -join ', ')
    }
    return (($excludedFiles | ForEach-Object { $_.Name }) -join ', ')
}

$script:okCount        = 0
$script:carriedCount   = 0
$script:failCount      = 0
$script:skipCount      = 0
$script:backedUpBytes  = [long]0
$script:failures       = New-Object System.Collections.Generic.List[string]
$script:skips          = New-Object System.Collections.Generic.List[string]
$script:manifestLines  = New-Object System.Collections.Generic.List[string]
$script:okNames        = New-Object System.Collections.Generic.List[string]

$script:lockAcquired = $false
$script:lockStarted  = $now.ToString('yyyy-MM-dd HH:mm:ss')
$script:lockHost     = $env:COMPUTERNAME
if (-not $script:lockHost) {
    try { $script:lockHost = [System.Net.Dns]::GetHostName() }
    catch { $script:lockHost = 'unknown-host' }
}

# -------------------------------------------------------------- helpers -----
function Add-Failure {
    param([string]$Label, [string]$Detail)
    $script:failCount++
    $script:failures.Add(('{0}: {1}' -f $Label, $Detail))
    Write-Host ("  [FAIL] {0}  {1}" -f $Label, $Detail)
}

# A skip is deliberately NOT a failure: -DryRun or a missing python must not
# turn a good backup red. It is still printed and counted, so it is never
# silent, and it is reported in the '#' summary line.
function Add-Skip {
    param([string]$Label, [string]$Detail)
    $script:skipCount++
    $script:skips.Add(('{0}: {1}' -f $Label, $Detail))
    Write-Host ("  [WARN] {0}  {1}  (skipped)" -f $Label, $Detail)
}

function Add-ManifestEntry {
    param([System.IO.FileInfo]$Item, [string]$Hash)
    $line = '{0}  {1}  {2} bytes  sha256={3}' -f `
        $Item.Name, $Item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $Item.Length, $Hash
    $script:manifestLines.Add($line)
    $script:okNames.Add($Item.Name)
    $script:backedUpBytes += $Item.Length
    $script:okCount++
    Write-Host ("  [ok]   {0}  {1:N0} bytes  sha256={2}" -f $Item.Name, $Item.Length, $Hash.Substring(0, 12))
}

# ---------------------------------------------------------------- lock ------
# A lock file is created with FileMode.CreateNew (atomic on Windows), so two
# instances cannot both think they won. Contenders only read the existing file;
# they never truncate it. A stale lock is removed only after its content is
# re-read, so we do not delete a lock another instance just refreshed.
function New-BackupLock {
    param([string]$Path)
    $content = @(
        '# vault backup lock - created by tools/backup_bundles.ps1'
        ('pid={0}' -f $PID)
        ('host={0}' -f $script:lockHost)
        ('started={0}' -f $script:lockStarted)
        ('timeout_minutes={0}' -f $LockTimeoutMinutes)
    ) -join [Environment]::NewLine

    $utf8   = New-Object System.Text.UTF8Encoding -ArgumentList $false
    $stream = $null
    $created = $false
    $complete = $false
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None)
        $created = $true
        $writer = New-Object System.IO.StreamWriter -ArgumentList @($stream, $utf8)
        try { $writer.Write($content) }
        finally { $writer.Dispose() }
        $complete = $true
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        # Never leave a half-written lock behind if the write failed after this
        # run created the file. Only touch it when *we* created it, so another
        # instance's lock is never removed.
        if ($created -and -not $complete) {
            try { Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
}

function Read-BackupLock {
    param([string]$Path)
    $info = [pscustomobject]@{
        Pid        = $null
        Host       = '?'
        Started    = $null
        StartText  = ''
        AgeMinutes = $null
    }
    try {
        $lines = @(Get-Content -LiteralPath $Path -ErrorAction Stop -TotalCount 20)
    } catch {
        return $info
    }
    $startedText = ''
    foreach ($line in $lines) {
        if ($line -match '^\s*pid\s*=\s*(\d+)\s*$') {
            $info.Pid = [int]$Matches[1]
        } elseif ($line -match '^\s*host\s*=\s*(.+?)\s*$') {
            $info.Host = $Matches[1]
        } elseif ($line -match '^\s*started\s*=\s*(.+?)\s*$') {
            $startedText = $Matches[1]
        }
    }
    $info.StartText = $startedText
    $parsed = [datetime]::MinValue
    if ($startedText -and [datetime]::TryParseExact(
            $startedText, 'yyyy-MM-dd HH:mm:ss',
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
        $info.Started = $parsed
    } else {
        # Malformed/partial lock: fall back to the file timestamp rather than
        # declaring it fresh forever.
        try { $info.Started = (Get-Item -LiteralPath $Path -ErrorAction Stop).LastWriteTime }
        catch { $info.Started = $null }
    }
    if ($null -ne $info.Started) {
        $info.AgeMinutes = ((Get-Date) - $info.Started).TotalMinutes
    }
    return $info
}

function Enter-BackupLock {
    param([string]$Path)
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            New-BackupLock -Path $Path
            $script:lockAcquired = $true
            Write-Host ("lock        : acquired by pid={0} on {1} at {2}" -f `
                $PID, $script:lockHost, $script:lockStarted)
            return $true
        } catch [System.IO.IOException] {
            # Lock already exists (CreateNew) or is momentarily open: inspect it.
        } catch {
            Write-Host ("FATAL: cannot create lock file {0}: {1}" -f $Path, $_.Exception.Message)
            return $false
        }

        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { continue }
        $info = Read-BackupLock -Path $Path
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { continue }

        $ageText = 'unknown age'
        if ($null -ne $info.AgeMinutes) { $ageText = '{0:N1} min' -f $info.AgeMinutes }
        $startedText = '?'
        if ($null -ne $info.Started) { $startedText = $info.Started.ToString('yyyy-MM-dd HH:mm:ss') }

        if ($null -eq $info.AgeMinutes -or $info.AgeMinutes -le $LockTimeoutMinutes) {
            Write-Host 'FATAL: another backup instance holds the lock; refusing to overwrite it.'
            Write-Host ("       lock file : {0}" -f $Path)
            Write-Host ("       owner     : pid={0} host={1} started={2} (age {3})" -f `
                $info.Pid, $info.Host, $startedText, $ageText)
            Write-Host ("       timeout   : {0} min. Wait for it to finish, raise -LockTimeoutMinutes," -f $LockTimeoutMinutes)
            Write-Host '                   or delete the lock file manually if you are certain it is dead.'
            return $false
        }

        Write-Host ("  [WARN] stale lock (age {0} > timeout {1} min): pid={2} host={3} started={4}; taking over." -f `
            $ageText, $LockTimeoutMinutes, $info.Pid, $info.Host, $info.StartText)
        # Re-read right before deleting: another instance may have taken over in
        # the meantime and this would otherwise destroy a fresh lock.
        $confirm = Read-BackupLock -Path $Path
        if (($null -ne $confirm.Pid -and $confirm.Pid -ne $info.Pid) -or
            ($confirm.StartText -and $confirm.StartText -ne $info.StartText)) {
            Write-Host '  [WARN] lock changed while inspecting it; retrying acquisition.'
            continue
        }
        try {
            Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
        } catch {
            Write-Host ("FATAL: stale lock exists but cannot be removed: {0}" -f $_.Exception.Message)
            return $false
        }
    }
    Write-Host ("FATAL: could not acquire lock after {0} attempts: {1}" -f $maxAttempts, $Path)
    return $false
}

function Exit-BackupLock {
    if (-not $script:lockAcquired) { return }
    $script:lockAcquired = $false
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        Write-Host 'lock        : release skipped (lock file already gone)'
        return
    }
    $info = Read-BackupLock -Path $lockPath
    if ($info.Pid -ne $PID -or $info.StartText -ne $script:lockStarted) {
        # A stale takeover happened while this run was still alive. Never delete
        # the new owner's lock.
        Write-Host 'lock        : release skipped (lock now belongs to another instance)'
        return
    }
    try {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction Stop
        Write-Host 'lock        : released'
    } catch {
        Write-Host ("  [WARN] cannot release lock {0}: {1}" -f $lockPath, $_.Exception.Message)
    }
}

# ---------------------------------------------------- MANIFEST carry-over ----
# Same shape as verify_backups.py / gen_health_card.py parse: name, two spaces,
# timestamp, two spaces, bytes, two spaces, sha256. Keep this in sync with them.
$manifestEntryPattern = '^(?<name>.+?)\s{2,}(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s{2,}(?<size>\d+)\s+bytes\s{2,}sha256=(?<sha>[0-9A-Fa-f]{64})\s*$'

function Read-OldManifest {
    param([string]$Path)
    $entries = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $entries }
    try {
        $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop)
    } catch {
        Write-Host ("  [WARN] cannot read previous manifest {0}: {1}" -f $Path, $_.Exception.Message)
        return $entries
    }
    foreach ($line in $lines) {
        if ($line.TrimStart().StartsWith('#')) { continue }
        $m = [regex]::Match($line, $manifestEntryPattern)
        if (-not $m.Success) { continue }
        $name = $m.Groups['name'].Value.Trim()
        $key = $name.ToLowerInvariant()
        if ($entries.ContainsKey($key)) { continue }
        $entries[$key] = [pscustomobject]@{
            Name      = $name
            Timestamp = $m.Groups['ts'].Value
            Size      = [long]$m.Groups['size'].Value
            Sha256    = $m.Groups['sha'].Value.ToUpperInvariant()
            Line      = $line.TrimEnd()
        }
    }
    return $entries
}

function Get-CarriedManifestEntry {
    param([string]$Name, [hashtable]$OldEntries, [string]$Dir)
    $key = $Name.ToLowerInvariant()
    if (-not $OldEntries.ContainsKey($key)) {
        Write-Host ("  [carry] {0}: not in the previous MANIFEST; nothing to carry" -f $Name)
        return $null
    }
    $entry = $OldEntries[$key]
    $path = Join-Path $Dir $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Host ("  [carry] {0}: previous entry exists but the file is gone; not carried" -f $Name)
        return $null
    }
    try {
        $item = Get-Item -LiteralPath $path -ErrorAction Stop
        if ([long]$item.Length -ne [long]$entry.Size) {
            Write-Host ("  [carry] {0}: size changed since the previous record ({1} -> {2}); not carried" -f `
                $Name, $entry.Size, $item.Length)
            return $null
        }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256 -ErrorAction Stop).Hash
    } catch {
        Write-Host ("  [carry] {0}: cannot re-verify the file ({1}); not carried" -f $Name, $_.Exception.Message)
        return $null
    }
    if ($hash -ine $entry.Sha256) {
        Write-Host ("  [carry] {0}: sha256 changed since the previous record; not carried" -f $Name)
        return $null
    }
    Write-Host ("  [carry] {0}: unchanged since {1}; previous MANIFEST line kept" -f $Name, $entry.Timestamp)
    return $entry
}

function Write-ManifestFile {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(('# vault backup {0}' -f $now.ToString('yyyy-MM-dd HH:mm:ss')))
    $lines.Add(('# vault {0}' -f $dayDir))
    foreach ($line in $script:manifestLines) { $lines.Add($line) }

    # A failed artifact must not silently drop an older, still-good file from
    # the MANIFEST: verify_backups.py would flag it as an orphan and exit 1.
    # Only carry a line when the file still matches that line byte for byte.
    $oldEntries = Read-OldManifest -Path $manifest
    Write-Host ("carry-over  : {0} usable entr{1} in the previous MANIFEST" -f `
        $oldEntries.Count, $(if ($oldEntries.Count -eq 1) { 'y' } else { 'ies' }))
    foreach ($name in $expectedArtifacts) {
        if ($script:okNames -contains $name) { continue }
        $entry = Get-CarriedManifestEntry -Name $name -OldEntries $oldEntries -Dir $dayDir
        if ($null -eq $entry) { continue }
        $lines.Add(('# {0}: {1} (file unchanged, size + sha256 re-verified)' -f `
            $carryNoteZh, $entry.Name))
        $lines.Add($entry.Line)
        $script:carriedCount++
    }

    # '#' lines are ignored by every reader (verify_backups.parse_manifest,
    # gen_health_card), so the extra skipped= field is metadata only and cannot
    # break their parsing. ok= counts registered artifacts including
    # data_manifest.json; bytes= is their summed size.
    $lines.Add(('# summary ok={0} carried={1} failed={2} skipped={3} bytes={4}' -f `
        $script:okCount, $script:carriedCount, $script:failCount,
        $script:skipCount, $script:backedUpBytes))
    Set-Content -LiteralPath $manifest -Value $lines -Encoding UTF8 -Force
}

function Invoke-Bundle {
    param([pscustomobject]$Repo)
    $bundleName = '{0}-{1}.bundle' -f $Repo.Label, $stamp
    $bundlePath = Join-Path $dayDir $bundleName
    $repoPath   = [string]$Repo.Path

    if (-not (Test-Path -LiteralPath $repoPath -PathType Container)) {
        Add-Failure $Repo.Label 'repo folder not found'
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repoPath '.git'))) {
        Add-Failure $Repo.Label ('not a git repository: ' + $repoPath)
        return
    }
    if ($DryRun) {
        Write-Host ("  [plan] {0,-14} {1}  ->  {2}" -f $Repo.Label, $repoPath, $bundleName)
        return
    }

    Write-Host ("  [run]  {0,-14} -> {1}" -f $Repo.Label, $bundlePath)
    # Build next to the target, then swap in only on success: a failed re-run
    # must not destroy the bundle already produced earlier today.
    $tmpPath = $bundlePath + '.tmp'
    if (Test-Path -LiteralPath $tmpPath) {
        Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $oldEap = $ErrorActionPreference
    try {
        # Native stderr must not become a terminating error.
        $ErrorActionPreference = 'Continue'
        $out = @(& git -C $repoPath bundle create $tmpPath --all 2>&1 |
            ForEach-Object { $_.ToString() })
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldEap
        $sw.Stop()
    }

    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $tmpPath -PathType Leaf)) {
        $detail = 'git bundle rc={0}' -f $rc
        if ($out.Count -gt 0) {
            $tail = ($out | Select-Object -Last 3) -join ' | '
            $detail = '{0}; {1}' -f $detail, $tail
        }
        Add-Failure $Repo.Label $detail
        if (Test-Path -LiteralPath $tmpPath) {
            Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
        }
        return
    }

    try {
        Move-Item -LiteralPath $tmpPath -Destination $bundlePath -Force -ErrorAction Stop
    } catch {
        Add-Failure $Repo.Label ('cannot finalize bundle: ' + $_.Exception.Message)
        Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
        return
    }

    $item = Get-Item -LiteralPath $bundlePath
    $hash = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash
    Add-ManifestEntry -Item $item -Hash $hash
    Write-Host ("         {0:N0} bytes in {1:N1}s" -f $item.Length, $sw.Elapsed.TotalSeconds)
}

function Invoke-DocsArchive {
    if ($excludedFiles.Count -gt 0) {
        Write-Host ("  [skip] excluded by name: {0}  (guards: {1})" -f `
            (($excludedFiles | ForEach-Object { $_.Name }) -join ', '),
            ($excludePatterns -join ', '))
    }
    if ($archiveFiles.Count -eq 0) {
        Write-Host '  [skip] no *.md / *.html / *.htm directly under the workspace root'
        return
    }
    $zipName = 'workspace-docs-{0}.zip' -f $stamp
    $zipPath = Join-Path $dayDir $zipName

    if ($DryRun) {
        Write-Host ("  [plan] workspace archive: {0} md ({1:N0} bytes) + {2} html ({3:N0} bytes) = {4} file(s), {5:N0} bytes  ->  {6}" -f `
            $docs.Count, [long]$docsBytes, $html.Count, [long]$htmlBytes,
            $archiveFiles.Count, [long]$archiveBytes, $zipName)
        return
    }

    Write-Host ("  [run]  workspace archive: {0} md + {1} html = {2} file(s), {3:N0} bytes -> {4}" -f `
        $docs.Count, $html.Count, $archiveFiles.Count, [long]$archiveBytes, $zipPath)
    $zipTmp = Join-Path $dayDir ('workspace-docs-{0}.partial.zip' -f $stamp)
    if (Test-Path -LiteralPath $zipTmp) {
        Remove-Item -LiteralPath $zipTmp -Force -ErrorAction SilentlyContinue
    }

    # Add one entry at a time instead of calling Compress-Archive over the whole
    # list: a single locked/unreadable file then cannot lose the entire archive.
    $added   = 0
    $skipped = New-Object System.Collections.Generic.List[string]
    $zip       = $null
    $zipStream = $null
    $zipError  = $null
    try {
        # Windows PowerShell 5.1 needs both assemblies; pwsh already has them.
        try { Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop } catch { }
        try { Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop } catch { }
        $zipStream = [System.IO.File]::Open($zipTmp, [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $zip = New-Object System.IO.Compression.ZipArchive -ArgumentList @(
            $zipStream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        foreach ($file in $archiveFiles) {
            try {
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $zip, $file.FullName, $file.Name,
                    [System.IO.Compression.CompressionLevel]::Optimal)
                $added++
            } catch {
                $skipped.Add($file.Name)
                Write-Host ("  [WARN] {0}: {1}" -f $file.Name, $_.Exception.Message)
            }
        }
    } catch {
        $zipError = $_.Exception.Message
    } finally {
        # Dispose the FileShare.None handles before any cleanup of the partial
        # file, otherwise Remove-Item on Windows would fail with a sharing error.
        if ($null -ne $zip) { $zip.Dispose() }
        if ($null -ne $zipStream) { $zipStream.Dispose() }
    }

    if ($null -ne $zipError) {
        Add-Failure 'workspace-docs' ('cannot create zip: ' + $zipError)
        if (Test-Path -LiteralPath $zipTmp) {
            Remove-Item -LiteralPath $zipTmp -Force -ErrorAction SilentlyContinue
        }
        return
    }

    if ($added -eq 0) {
        Add-Failure 'workspace-docs' 'no file could be added to the zip'
        if (Test-Path -LiteralPath $zipTmp) {
            Remove-Item -LiteralPath $zipTmp -Force -ErrorAction SilentlyContinue
        }
        return
    }
    if ($skipped.Count -gt 0) {
        Add-Failure 'workspace-docs' ('{0} file(s) skipped: {1}' -f `
            $skipped.Count, ($skipped -join ', '))
    }

    try {
        Move-Item -LiteralPath $zipTmp -Destination $zipPath -Force -ErrorAction Stop
    } catch {
        Add-Failure 'workspace-docs' ('cannot finalize zip: ' + $_.Exception.Message)
        if (Test-Path -LiteralPath $zipTmp) {
            Remove-Item -LiteralPath $zipTmp -Force -ErrorAction SilentlyContinue
        }
        return
    }
    $item = Get-Item -LiteralPath $zipPath
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    Add-ManifestEntry -Item $item -Hash $hash
    Write-Host ("         {0:N0} bytes, {1} of {2} file(s) added (md {3} + html {4})" -f `
        $item.Length, $added, $archiveFiles.Count, $docs.Count, $html.Count)
}

# ---------------------------------------------------- data-root manifest -----
# The data root is the one asset no git repository tracks, so the vault stores a
# *description* of it instead of its bytes. tools/gen_data_manifest.py does the
# read-only scan; this wrapper only launches it, registers the file and keeps
# every non-fatal case (python missing, budget exhausted) out of the failure
# count so one missing description cannot fail an otherwise good backup.
function Resolve-PythonLauncher {
    # Get-Command only inspects PATH, it executes nothing, so this is safe to
    # call from -DryRun. 'python' first, then the py launcher with -3.
    foreach ($spec in @(
            [pscustomobject]@{ Exe = 'python'; Prefix = @() }
            [pscustomobject]@{ Exe = 'py'; Prefix = @('-3') }
        )) {
        $cmd = Get-Command $spec.Exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $cmd) { continue }
        $exePath = [string]$cmd.Source
        if (-not $exePath) { $exePath = [string]$cmd.Path }
        if (-not $exePath) { continue }
        return [pscustomobject]@{ Exe = $exePath; Prefix = @($spec.Prefix) }
    }
    return $null
}

function Get-DataManifestEstimateSeconds {
    # Dry-run ETA only. The newest data_manifest.json already in the vault is the
    # best measurement available (scan.elapsed_sec of the read-only scan); the
    # extra second covers catalog.json's sha256 plus the JSON write. This
    # function only reads, so it is safe under -DryRun.
    param([string]$Root, [string]$Name)
    $fallback = 2.0
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $fallback }

    $candidates = New-Object System.Collections.Generic.List[string]
    try {
        $dayDirs = @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
            Sort-Object Name -Descending)
        foreach ($dir in $dayDirs) { $candidates.Add((Join-Path $dir.FullName $Name)) }
    } catch { }
    $candidates.Add((Join-Path $Root $Name))

    foreach ($path in $candidates) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try {
            $doc = Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction Stop |
                ConvertFrom-Json
        } catch {
            continue
        }
        if ($null -eq $doc -or $null -eq $doc.scan -or $null -eq $doc.scan.elapsed_sec) { continue }
        $value = [double]$doc.scan.elapsed_sec
        if ($value -le 0 -or $value -gt 600) { continue }
        # Round up, plus a second for the catalog hash and the write.
        return [math]::Ceiling($value + 1.0)
    }
    return $fallback
}

function Get-DataManifestPlanZh {
    # "<generate-data-manifest> data_manifest.json (<estimated> N <s>)" - all
    # Chinese pieces come from code points, never from a literal (see the ASCII
    # note near the top of this file).
    param([string]$Name, [double]$Seconds)
    return '{0} {1} {2} {3:N0} {4}' -f $dataManifestZhPrefix, $Name,
        $dataManifestZhOpen, $Seconds, $dataManifestZhClose
}

function Invoke-DataManifest {
    if (-not (Test-Path -LiteralPath $dataManifestScript -PathType Leaf)) {
        Add-Skip 'data-manifest' ('generator not found: ' + $dataManifestScript)
        return
    }
    $launcher = Resolve-PythonLauncher

    if ($DryRun) {
        # Never spend the 268k-file scan on a dry run: print the plan and stop.
        $eta = Get-DataManifestEstimateSeconds -Root $VaultRoot -Name $dataManifestName
        if ($null -eq $launcher) {
            Write-Host '  [WARN] python not found on PATH; a real run would skip data_manifest.json (fail-open)'
        }
        Write-Host ("  [plan] data manifest: {0} -> {1}  (est. {2:N0} s; python is not run in dry-run)" -f `
            $dataManifestScript, $dataManifestName, $eta)
        Write-Host ("         {0}" -f (Get-DataManifestPlanZh -Name $dataManifestName -Seconds $eta))
        return
    }

    if ($null -eq $launcher) {
        Add-Skip 'data-manifest' 'python not found on PATH; artifact skipped (fail-open)'
        return
    }

    $argv = @($launcher.Prefix) + @('-B', $dataManifestScript, '--out', $dataManifestPath)
    Write-Host ("  [run]  data manifest: {0} {1}" -f $launcher.Exe, ($argv -join ' '))
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $oldEap = $ErrorActionPreference
    $rc = -1
    try {
        # Native stderr must not become a terminating error (the generator
        # prints its WARN lines there).
        $ErrorActionPreference = 'Continue'
        $out = @(& $launcher.Exe @argv 2>&1 | ForEach-Object { $_.ToString() })
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldEap
        $sw.Stop()
    }
    foreach ($line in $out) { Write-Host ("         {0}" -f $line) }

    $exists = Test-Path -LiteralPath $dataManifestPath -PathType Leaf
    # rc=2 is the generator's soft --time-budget signal: the JSON is complete
    # and parseable, only its totals are partial (scan.partial=true). Dropping
    # it would throw away a usable shape description, so register it and say so.
    if ($rc -notin @(0, 2) -or -not $exists) {
        $detail = 'gen_data_manifest.py rc={0}' -f $rc
        if (-not $exists) { $detail = '{0}; {1} not written' -f $detail, $dataManifestName }
        if ($out.Count -gt 0) {
            $detail = '{0}; {1}' -f $detail, (($out | Select-Object -Last 3) -join ' | ')
        }
        Add-Failure 'data-manifest' $detail
        return
    }
    if ($rc -eq 2) {
        Write-Host '  [WARN] gen_data_manifest.py rc=2: scan hit --time-budget, totals are partial (scan.partial=true in the JSON)'
    }

    $item = Get-Item -LiteralPath $dataManifestPath
    $hash = (Get-FileHash -LiteralPath $dataManifestPath -Algorithm SHA256).Hash
    Add-ManifestEntry -Item $item -Hash $hash
    Write-Host ("         {0:N0} bytes in {1:N1}s" -f $item.Length, $sw.Elapsed.TotalSeconds)
}

function Invoke-Rotation {
    param([string]$Root, [datetime]$Cutoff, [bool]$Preview)
    $result = [pscustomobject]@{ Removed = 0; Failed = 0; Candidates = 0 }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        Write-Host '  vault root does not exist; nothing to rotate'
        return $result
    }

    $dirs = @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and
            -not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        } | Sort-Object Name)

    foreach ($dir in $dirs) {
        $parsed = [datetime]::MinValue
        $styles = [System.Globalization.DateTimeStyles]::None
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        if (-not [datetime]::TryParseExact($dir.Name, 'yyyy-MM-dd', $culture, $styles, [ref]$parsed)) {
            continue
        }
        if ($parsed -ge $Cutoff) { continue }
        $result.Candidates = $result.Candidates + 1
        if ($Preview) {
            Write-Host ("  [would-del] {0}" -f $dir.FullName)
            continue
        }

        $size = [long]0
        $sum = (Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if ($null -ne $sum) { $size = [long]$sum }
        try {
            Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction Stop
            Write-Host ("  [del]  {0}  ({1:N0} bytes)" -f $dir.Name, $size)
            $result.Removed = $result.Removed + 1
        } catch {
            Write-Host ("  [WARN] rotation failed for {0}: {1}" -f $dir.FullName, $_.Exception.Message)
            $result.Failed = $result.Failed + 1
        }
    }
    if ($result.Candidates -eq 0) {
        Write-Host '  no date folder older than the cutoff'
    }
    return $result
}

# ----------------------------------------------------------------- main -----
$swTotal = [System.Diagnostics.Stopwatch]::StartNew()

Write-Host '=== vault backup ==='
Write-Host ("mode        : {0}" -f $(if ($DryRun) { 'DRY-RUN (no writes)' } else { 'REAL RUN' }))
Write-Host ("time        : {0}" -f $now.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host ("repo root   : {0}" -f $repoRoot)
Write-Host ("workspace   : {0}" -f $workspace)
Write-Host ("vault root  : {0}  (exists: {1})" -f $VaultRoot, (Test-Path -LiteralPath $VaultRoot))
Write-Host ("date folder : {0}  (exists: {1})" -f $dayDir, (Test-Path -LiteralPath $dayDir))
if ($DryRun) {
    Write-Host ("lock        : {0}  (DRY-RUN: not created; {1})" -f $lockPath, $lockDryRunHint)
} else {
    Write-Host ("lock        : {0}  (stale after {1} min)" -f $lockPath, $LockTimeoutMinutes)
}
Write-Host ("keep days   : {0}  (keep folders dated >= {1})" -f $KeepDays, $cutoff.ToString('yyyy-MM-dd'))
Write-Host ''

if ($DryRun) {
    Write-Host 'DRY-RUN: nothing below will be created, overwritten or deleted.'
    Write-Host ''
}

$git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $git) {
    if ($DryRun) {
        Write-Host '  [WARN] git not found on PATH; a real run would fail every bundle'
    } else {
        throw 'git not found on PATH'
    }
}

if (-not $DryRun) {
    try {
        New-Item -ItemType Directory -Path $dayDir -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Host ("FATAL: cannot create date folder {0}: {1}" -f $dayDir, $_.Exception.Message)
        exit 1
    }
    if (-not (Enter-BackupLock -Path $lockPath)) {
        Write-Host 'FATAL: backup aborted before touching any artifact or MANIFEST.'
        exit 1
    }
}

try {
    Write-Host 'repos:'
    foreach ($repo in $repos) { Invoke-Bundle -Repo $repo }

    Write-Host 'workspace archive (md + html):'
    Invoke-DocsArchive

    Write-Host 'data manifest (read-only data-root scan):'
    Invoke-DataManifest

    Write-Host 'rotation:'
    $rot = Invoke-Rotation -Root $VaultRoot -Cutoff $cutoff -Preview:$DryRun.IsPresent

    if (-not $DryRun) {
        Write-ManifestFile
        Write-Host ''
        Write-Host ("manifest    : {0}" -f $manifest)
    }
} finally {
    Exit-BackupLock
}

$swTotal.Stop()
Write-Host ''
Write-Host '=== summary ==='
if ($DryRun) {
    Write-Host ("repos       : {0} planned, {1} problem(s)" -f $repos.Count, $script:failCount)
    Write-Host ("docs        : {0} md + {1} html = {2} file(s), {3:N0} bytes" -f `
        $docs.Count, $html.Count, $archiveFiles.Count, [long]$archiveBytes)
    Write-Host ("data        : {0} planned (est. {1:N0} s, python not run)" -f `
        $dataManifestName, (Get-DataManifestEstimateSeconds -Root $VaultRoot -Name $dataManifestName))
    Write-Host ("excluded    : {0}" -f (Get-ExcludedSummary))
    Write-Host ("rotation    : {0} folder(s) would be removed" -f $rot.Candidates)
    Write-Host 'writes      : none (dry-run)'
    Write-Host 'result      : OK (plan only)'
    exit 0
}

Write-Host ("entries     : {0} file(s), {1:N0} bytes" -f $script:okCount, $script:backedUpBytes)
Write-Host ("carried     : {0} file(s) kept from the previous MANIFEST" -f $script:carriedCount)
Write-Host ("archive     : {0} md + {1} html = {2} file(s), {3:N0} input bytes" -f `
    $docs.Count, $html.Count, $archiveFiles.Count, [long]$archiveBytes)
Write-Host ("excluded    : {0}" -f (Get-ExcludedSummary))
Write-Host ("failed      : {0}" -f $script:failCount)
if ($script:failCount -gt 0) {
    foreach ($f in $script:failures) { Write-Host ("  - {0}" -f $f) }
}
Write-Host ("skipped     : {0} artifact(s) skipped (fail-open, not a failure)" -f $script:skipCount)
if ($script:skipCount -gt 0) {
    foreach ($s in $script:skips) { Write-Host ("  - {0}" -f $s) }
}
Write-Host ("rotation    : {0} removed, {1} failed" -f $rot.Removed, $rot.Failed)
Write-Host ("elapsed     : {0:N1}s" -f $swTotal.Elapsed.TotalSeconds)

if ($script:failCount -gt 0 -or $rot.Failed -gt 0) {
    Write-Host 'result      : FAILED'
    exit 1
}
Write-Host 'result      : OK'
exit 0
