#requires -Version 5.1
<#
.SYNOPSIS
    Turn one vault day folder into an upload-ready package: verify the six
    artifacts against MANIFEST.txt byte counts, then write three control files
    (offsite listing / SHA256SUMS.txt / upload README).

.DESCRIPTION
    This tool packages; it never uploads. It opens no socket, logs in to no
    account and touches no credential. "An offsite copy exists" only becomes
    true after a human carries these files to another location and verifies
    them there.

        source : <workspace>\_vault\<yyyy-MM-dd>\   (default: newest day)
        output : <workspace>\_offsite\              (or -OutDir)

    Written by default (three small text files):

        <date>-offsite.txt   human-readable list: name + bytes + the sha256
                             recorded in MANIFEST.txt, plus the "verify after
                             upload" checklist.
        SHA256SUMS.txt       standard sha256sum format: pure ASCII, LF endings,
                             lowercase hex, one line per artifact, so
                             'sha256sum -c SHA256SUMS.txt' works on Linux/WSL
                             and the PowerShell snippet in the README works too.
        README-<zh>.md       three steps (what to upload / how to verify / what
                             to do after it passes) and an explicit "this is
                             packaging, not uploading" banner. The file name
                             itself is Chinese: README-<upload notes>.md.

    The 723 MB of bundles are NOT copied unless -Copy is given. A copy into a
    folder on the same physical disk is not a second copy; it only saves the
    human a step, so it stays opt-in and says so out loud.

    Verification is byte-count only, on purpose. Recomputing sha256 over the six
    artifacts costs about 2.5 s on this host (measured on the 683 MB aios
    bundle), so the reason is not cost - it is role separation. MANIFEST.txt was
    written by the backup run that produced those bytes; hashing them again on
    the same disk proves at most "the file did not change", never "the bytes
    that arrive offsite are the bytes we recorded". The independent hash pass
    belongs on the receiving side (see the README this tool writes), and
    tools/verify_backups.py already does a scheduled full pass locally. What
    this tool adds is the cheap pre-upload check nobody else does: are the six
    artifacts still the sizes MANIFEST.txt says they are.

    Fail-closed: if any of the six artifacts is absent from MANIFEST.txt, absent
    on disk, not stamped with this day, or disagrees with the recorded byte
    count, NO output file is written and the exit code is 1. Packaging a broken
    day is worse than packaging nothing, because the package looks complete.

    Exit codes:
        0 = ok (a dry run also returns 0 when validation passes)
        1 = source validation failed (nothing written)
        2 = usage / environment error: bad -Date, missing vault or day folder,
            refused -OutDir, missing or damaged texts file, write failure.

    ASCII-only by requirement. Windows PowerShell 5.1 decodes a BOM-less .ps1 as
    the ANSI code page (CP936 on this host), so a literal Chinese character in
    this file would become mojibake in a -File run. The Chinese copy for the two
    documents therefore lives in tools/prepare_offsite.texts.txt, which this
    script reads with [IO.File]::ReadAllText (UTF-8, BOM-aware) and validates
    section by section. Missing section, missing key or an unresolved {TOKEN}
    aborts the run instead of shipping a half-empty README. That file must sit
    next to this script; a copy of the .ps1 alone is not enough.

.PARAMETER Date
    Day folder name, yyyy-MM-dd. Default: the newest yyyy-MM-dd folder found
    under the vault root.

.PARAMETER VaultRoot
    Vault root. Default: <workspace>\_vault, derived from $PSScriptRoot
    (tools -> repo -> workspace) so that no non-ASCII path literal is baked
    into this file.

.PARAMETER OutDir
    Where the control files (and, with -Copy, the artifacts) go. Default:
    <workspace>\_offsite. It may not be inside the vault and the vault may not
    be inside it - writing control files into a vault day folder would change
    the artifact set that MANIFEST.txt describes.

.PARAMETER Copy
    Also copy the six artifacts into -OutDir (723,513,740 bytes on 2026-10-22).
    The byte total and a time estimate are printed first, and every copy is
    verified by byte count afterwards. Same-disk copy: staging for upload, not
    a backup, not an offsite copy.

.PARAMETER DryRun
    Print the plan, including the exact byte sizes of the control files that
    would be written, and create/modify/delete nothing. The exit code still
    reflects source validation.

.NOTES
    Read-only with respect to the vault: nothing here writes, moves or deletes
    anything under the vault root. Rotation remains the business of
    tools/backup_bundles.ps1.
#>
[CmdletBinding()]
param(
    [string]$Date = '',
    [string]$VaultRoot = '',
    [string]$OutDir = '',
    [switch]$Copy,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Every usage / environment failure below is a plain 'throw'. Left alone, that
# would surface as PowerShell's generic exit code 1 and blur the difference
# between "the vault day is broken" (1) and "the call was wrong" (2). The trap
# turns any escaped terminating error into the documented exit code 2.
trap {
    Write-Host ''
    Write-Host ("FATAL: {0}" -f $_.Exception.Message)
    exit 2
}

# --------------------------------------------------------------- helpers -----
$script:failCount = 0
$script:warnCount = 0

function Add-Failure {
    param([string]$Message)
    $script:failCount++
    Write-Host ("  [FAIL] {0}" -f $Message)
}

function Add-Warning {
    param([string]$Message)
    $script:warnCount++
    Write-Host ("  [warn] {0}" -f $Message)
}

function Test-PathInside {
    # True when $Child is $Parent or sits below it. Ordinal-ignore-case, because
    # this is Windows and the same folder may be spelled two ways.
    param([string]$Child, [string]$Parent)
    $c = $Child.TrimEnd('\') + '\'
    $p = $Parent.TrimEnd('\') + '\'
    return $c.StartsWith($p, [System.StringComparison]::OrdinalIgnoreCase)
}

function Convert-ToCrlf {
    param([string]$Text)
    return $Text.Replace("`r`n", "`n").Replace("`n", "`r`n")
}

function Format-Count {
    param([long]$Value)
    return $Value.ToString('N0', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-FreeSpaceBytes {
    param([string]$Path)
    try {
        $root = [System.IO.Path]::GetPathRoot($Path)
        $di = New-Object System.IO.DriveInfo($root)
        return [long]$di.AvailableFreeSpace
    } catch {
        return -1
    }
}

# ----------------------------------------------------------------- paths -----
if (-not $PSScriptRoot) {
    throw 'Cannot resolve $PSScriptRoot. Run this file with -File, do not paste it.'
}
$toolsDir  = $PSScriptRoot
$repoRoot  = Split-Path -Parent $toolsDir
$workspace = Split-Path -Parent $repoRoot

$DateArg = $Date
$copyMode = [bool]$Copy

if (-not $VaultRoot) {
    $VaultRoot = Join-Path $workspace '_vault'
} elseif (-not [System.IO.Path]::IsPathRooted($VaultRoot)) {
    $VaultRoot = Join-Path (Get-Location).Path $VaultRoot
}
$VaultRoot = [System.IO.Path]::GetFullPath($VaultRoot).TrimEnd('\')

if (-not $OutDir) {
    $OutDir = Join-Path $workspace '_offsite'
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path (Get-Location).Path $OutDir
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir).TrimEnd('\')

foreach ($bad in @($VaultRoot, $OutDir)) {
    if ($bad -match '^[A-Za-z]:$') { throw ("Refusing to use drive root '{0}'." -f $bad) }
}
if (Test-PathInside -Child $OutDir -Parent $VaultRoot) {
    throw (("Refusing -OutDir '{0}': it is the vault root or sits inside '{1}'. Control files " +
        "would pollute the artifact set that MANIFEST.txt describes.") -f $OutDir, $VaultRoot)
}
if (Test-PathInside -Child $VaultRoot -Parent $OutDir) {
    throw ("Refusing -OutDir '{0}': the vault '{1}' is inside it." -f $OutDir, $VaultRoot)
}
if (-not (Test-Path -LiteralPath $VaultRoot -PathType Container)) {
    throw ("Vault root not found or not a folder: {0}" -f $VaultRoot)
}

# ------------------------------------------------------- load Chinese copy ---
# UTF-8 text, one "=== section ===" marker per block. Encoding is decided by the
# BOM and defaults to UTF-8, never by the console code page, so this works
# whatever the host's ANSI code page is.
$textsPath = Join-Path $toolsDir 'prepare_offsite.texts.txt'
if (-not (Test-Path -LiteralPath $textsPath -PathType Leaf)) {
    throw (("Chinese copy not found: {0} (it ships next to this script; the .ps1 alone cannot " +
        "write the two Chinese documents)") -f $textsPath)
}
$textsRaw = [System.IO.File]::ReadAllText($textsPath)
$sections = @{}
$currentSection = ''
foreach ($rawLine in $textsRaw.Split("`n")) {
    $line = $rawLine.TrimEnd("`r")
    if ($line -match '^===\s*(?<id>[A-Za-z0-9_-]+)\s*===$') {
        $currentSection = [string]$Matches['id']
        $sections[$currentSection] = New-Object System.Collections.Generic.List[string]
        continue
    }
    if ($currentSection) { $sections[$currentSection].Add($line) }
}
foreach ($requiredSection in @('offsite', 'readme', 'map')) {
    if (-not $sections.ContainsKey($requiredSection)) {
        throw ("texts file is missing the '=== {0} ===' section: {1}" -f $requiredSection, $textsPath)
    }
}

$zhOffsiteTemplate = ($sections['offsite'] -join "`n").Trim("`n")
$zhReadmeTemplate  = ($sections['readme'] -join "`n").Trim("`n")
if ($zhOffsiteTemplate.IndexOf('{ROWS}') -lt 0) {
    throw ("texts file: the 'offsite' section lost its {ROWS} slot: {0}" -f $textsPath)
}
if ($zhReadmeTemplate.IndexOf('{ROWS_TABLE}') -lt 0) {
    throw ("texts file: the 'readme' section lost its {ROWS_TABLE} slot: {0}" -f $textsPath)
}

# Small strings: 'key<TAB>value' per line, no tabs inside values.
$zhTexts = @{}
foreach ($line in $sections['map']) {
    if (-not $line.Trim()) { continue }
    $idx = $line.IndexOf("`t")
    if ($idx -lt 0) { throw ("texts file: map line has no tab: {0}" -f $line) }
    $zhTexts[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
}
foreach ($requiredKey in @('readme_name', 'note_only', 'note_copied')) {
    if (-not $zhTexts.ContainsKey($requiredKey)) {
        throw ("texts file: the map section is missing the '{0}' key: {1}" -f $requiredKey, $textsPath)
    }
}
$readmeName = [string]$zhTexts['readme_name']
if (-not $readmeName.EndsWith('.md')) {
    throw ("texts file: readme_name looks wrong (expected *.md): '{0}'" -f $readmeName)
}

# ----------------------------------------------------------- pick the day ---
$dayDirs = @(Get-ChildItem -LiteralPath $VaultRoot -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
    Sort-Object -Property Name)
if (-not $dayDirs.Count) {
    throw ("No yyyy-MM-dd day folder under the vault root '{0}'." -f $VaultRoot)
}

if ($DateArg) {
    if ($DateArg -notmatch '^\d{4}-\d{2}-\d{2}$') {
        throw ("Bad -Date '{0}': expected yyyy-MM-dd." -f $DateArg)
    }
    try {
        $null = [datetime]::ParseExact($DateArg, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        throw ("Bad -Date '{0}': not a real calendar date." -f $DateArg)
    }
    $dayName = $DateArg
    $dayPickNote = 'explicit -Date'
} else {
    $dayName = [string]$dayDirs[$dayDirs.Count - 1].Name
    $dayPickNote = ('newest of {0} day folder(s)' -f $dayDirs.Count)
}
$dayDir = Join-Path $VaultRoot $dayName
if (-not (Test-Path -LiteralPath $dayDir -PathType Container)) {
    throw ("Day folder not found: {0}" -f $dayDir)
}
$stamp = $dayName.Replace('-', '')
$manifestPath = Join-Path $dayDir 'MANIFEST.txt'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw ("MANIFEST.txt not found: {0}" -f $manifestPath)
}

# ------------------------------------------------------------ header print --
$now = Get-Date
$modeText = 'REAL RUN (writes the control files)'
if ($DryRun) { $modeText = 'DRY RUN (nothing will be written)' }
Write-Host ''
Write-Host 'prepare_offsite.ps1 - stage one vault day for offsite upload'
Write-Host ("  mode        : {0}" -f $modeText)
Write-Host ("  vault root  : {0}" -f $VaultRoot)
Write-Host ("  day folder  : {0}" -f $dayDir)
Write-Host ("                selected: {0}" -f $dayPickNote)
Write-Host ("  manifest    : {0}" -f $manifestPath)
Write-Host ("  out dir     : {0}" -f $OutDir)
Write-Host ("  texts       : {0}" -f $textsPath)
Write-Host ''

# ------------------------------------------------------- parse MANIFEST -----
# Same line shape as tools/verify_backups.py parses, so the two tools can never
# disagree about what a MANIFEST line means:
#   name  <yyyy-MM-dd HH:mm:ss>  <bytes> bytes  sha256=<64 hex>
# '#' lines are comments (carry-over records live there) and are never entries.
$linePattern = '^(?<name>.+?)\s{2,}(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s{2,}(?<size>\d+)\s+bytes\s{2,}sha256=(?<sha>[0-9A-Fa-f]{64})\s*$'
$manifestTs = ''
$summaryBytes = $null
$entries = New-Object System.Collections.Generic.List[object]

foreach ($rawLine in [System.IO.File]::ReadAllLines($manifestPath)) {
    $line = $rawLine.Trim()
    if (-not $line) { continue }
    if ($line.StartsWith('#')) {
        if ($line -match '^#\s*vault backup\s+(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
            if (-not $manifestTs) { $manifestTs = [string]$Matches['ts'] }
        }
        if ($line -match '^#\s*summary\b') {
            if ($line -match '\bbytes=(?<b>\d+)') { $summaryBytes = [long]$Matches['b'] }
        }
        continue
    }
    $m = [regex]::Match($line, $linePattern)
    if (-not $m.Success) {
        Add-Warning ("unparsable MANIFEST line ignored: {0}" -f $line)
        continue
    }
    $entries.Add([pscustomobject]@{
        Name   = $m.Groups['name'].Value
        MTime  = $m.Groups['ts'].Value
        Bytes  = [long]$m.Groups['size'].Value
        Sha256 = $m.Groups['sha'].Value.ToUpperInvariant()
    })
}

if (-not $manifestTs) { $manifestTs = $now.ToString('yyyy-MM-dd HH:mm:ss') }

# ------------------------------------------------------- expected artifacts -
# The contract of tools/backup_bundles.ps1: four <label>-<yyyyMMdd>.bundle, one
# workspace-docs-<yyyyMMdd>.zip and one data_manifest.json. The labels are not
# hard-coded: four bundles with this day's stamp is the requirement, and an
# unexpected label set is a warning, not a failure, so that adding or renaming a
# repository does not have to be mirrored in two files.
$bundlePattern = '^(?<label>[A-Za-z0-9][A-Za-z0-9._-]*)-' + [regex]::Escape($stamp) + '\.bundle$'
$zipPattern    = '^workspace-docs-' + [regex]::Escape($stamp) + '\.zip$'

$manifestBundles = @($entries | Where-Object { $_.Name -match '\.bundle$' })
foreach ($b in $manifestBundles) {
    if ($b.Name -notmatch $bundlePattern) {
        Add-Failure ("bundle '{0}' does not carry this day's stamp '{1}'" -f $b.Name, $stamp)
    }
}
$bundleEntries = @($manifestBundles | Where-Object { $_.Name -match $bundlePattern } | Sort-Object -Property Name)
$zipEntries    = @($entries | Where-Object { $_.Name -match $zipPattern })
$dataEntries   = @($entries | Where-Object { $_.Name -ceq 'data_manifest.json' })

if ($bundleEntries.Count -ne 4) {
    $found = ''
    if ($bundleEntries.Count) { $found = ': ' + (($bundleEntries | ForEach-Object { $_.Name }) -join ', ') }
    Add-Failure ("expected 4 '<repo>-{0}.bundle' entries in MANIFEST.txt, found {1}{2}" -f `
        $stamp, $bundleEntries.Count, $found)
}
if ($zipEntries.Count -ne 1) {
    Add-Failure ("expected exactly 1 'workspace-docs-{0}.zip' entry in MANIFEST.txt, found {1}" -f `
        $stamp, $zipEntries.Count)
}
if ($dataEntries.Count -ne 1) {
    Add-Failure ("expected exactly 1 'data_manifest.json' entry in MANIFEST.txt, found {0}" -f $dataEntries.Count)
}

$knownLabels = @('aios', 'zerobound-kg', 'digital-legion', 'res-downloader')
$foundLabels = @()
foreach ($b in $bundleEntries) {
    if ($b.Name -match $bundlePattern) { $foundLabels += [string]$Matches['label'] }
}
$missingLabels = @($knownLabels | Where-Object { $foundLabels -notcontains $_ })
$extraLabels   = @($foundLabels | Where-Object { $knownLabels -notcontains $_ })
if ($missingLabels.Count -or $extraLabels.Count) {
    $missingText = 'none'
    if ($missingLabels.Count) { $missingText = $missingLabels -join ', ' }
    $extraText = 'none'
    if ($extraLabels.Count) { $extraText = $extraLabels -join ', ' }
    Add-Warning ("bundle label set differs from the known four (missing: {0}; unexpected: {1})" -f `
        $missingText, $extraText)
}

# Keep MANIFEST order (that is the order a human reading MANIFEST.txt expects),
# restricted to the six required artifacts.
$requiredNames = New-Object System.Collections.Generic.List[string]
foreach ($b in $bundleEntries) { $requiredNames.Add($b.Name) }
foreach ($z in $zipEntries)    { $requiredNames.Add($z.Name) }
foreach ($d in $dataEntries)   { $requiredNames.Add($d.Name) }
$expected = @($entries | Where-Object { $requiredNames -contains $_.Name })

$expectedCount = 0
foreach ($b in $bundleEntries) { $expectedCount++ }
foreach ($z in $zipEntries)    { $expectedCount++ }
foreach ($d in $dataEntries)   { $expectedCount++ }

# ------------------------------------------------------- existing package ---
# A second date packaged into the same OutDir would silently overwrite
# SHA256SUMS.txt and the README, and the older package would then verify against
# the wrong list. Warn loudly rather than refuse: rotating the whole folder is a
# human decision, and re-packaging the same date is a normal, idempotent rerun.
$existingSums = Join-Path $OutDir 'SHA256SUMS.txt'
if (Test-Path -LiteralPath $existingSums -PathType Leaf) {
    $firstLines = @([System.IO.File]::ReadAllLines($existingSums) | Where-Object { $_.Trim() } | Select-Object -First 1)
    $existingStamp = ''
    if ($firstLines.Count) { $existingStamp = ($firstLines[0] -replace '^[0-9A-Fa-f]{64}\s+', '') }
    Add-Warning ("{0} already holds SHA256SUMS.txt (first entry: {1}); this package will replace it" -f `
        $OutDir, $existingStamp)
}

# --------------------------------------------------------- byte-count check -
Write-Host 'size check against MANIFEST.txt (byte counts only, no sha256 recompute):'
$checked = New-Object System.Collections.Generic.List[object]
$okCount = 0
$totalBytes = [long]0
$index = 0

foreach ($e in $expected) {
    $index++
    $path = Join-Path $dayDir $e.Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Add-Failure ("{0}: listed in MANIFEST.txt ({1} bytes) but missing on disk" -f $e.Name, (Format-Count $e.Bytes))
        continue
    }
    $diskBytes = [long](Get-Item -LiteralPath $path -Force).Length
    if ($diskBytes -ne $e.Bytes) {
        $delta = $diskBytes - $e.Bytes
        Add-Failure ("{0}: size mismatch, disk {1} bytes, MANIFEST {2} bytes (delta {3:+#;-#;0})" -f `
            $e.Name, (Format-Count $diskBytes), (Format-Count $e.Bytes), $delta)
        continue
    }
    $totalBytes += $diskBytes
    $okCount++
    $shortHash = $e.Sha256.Substring(0, 16) + '...'
    Write-Host ("  [ok]   {0,2}  {1,-32}  {2,12} bytes  sha256 {3}" -f $index, $e.Name, $diskBytes, $shortHash)
    $checked.Add([pscustomobject]@{ Name = $e.Name; Bytes = $diskBytes; Sha256 = $e.Sha256 })
}

# A day folder may legitimately hold leftovers (a previous day's bundle, a lock
# file). They are not part of the six, never packaged, and only warned about.
$registeredNames = @($entries | ForEach-Object { $_.Name })
$ignoreNames = @('MANIFEST.txt', '.backup.lock')
$extras = @(Get-ChildItem -LiteralPath $dayDir -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $registeredNames -notcontains $_.Name -and $ignoreNames -notcontains $_.Name })
foreach ($x in $extras) {
    Add-Warning ("file in the day folder is not registered in MANIFEST.txt, not packaged: {0} ({1} bytes)" -f `
        $x.Name, (Format-Count $x.Length))
}

if ($null -ne $summaryBytes) {
    $parsedBytes = [long]0
    foreach ($e in $entries) { $parsedBytes += $e.Bytes }
    if ($parsedBytes -ne $summaryBytes) {
        Add-Warning (("MANIFEST summary says bytes={0} but the parsed entry lines sum to {1}; " +
            "carry-over records are '#'-commented and not parsed, which normally explains the gap") -f `
            (Format-Count $summaryBytes), (Format-Count $parsedBytes))
    }
}

# Age of the backup this package is based on: 36 h = aging, 72 h = stale, the
# same thresholds tools/verify_backups.py reports as yellow / red. A stale day
# folder is still packageable - the point is that the human sees it.
$backupTime = $null
try {
    $backupTime = [datetime]::ParseExact($manifestTs, 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture)
} catch {
    $backupTime = $null
}
if ($null -ne $backupTime) {
    $ageHours = ($now - $backupTime).TotalHours
    if ($ageHours -gt 72) {
        Add-Warning ("this day folder is {0:N0} h old (MANIFEST {1}); consider a fresh backup before uploading" -f $ageHours, $manifestTs)
    } elseif ($ageHours -gt 36) {
        Add-Warning ("this day folder is {0:N0} h old (MANIFEST {1}); 36 h is the yellow threshold" -f $ageHours, $manifestTs)
    }
}

Write-Host ("  summary: {0}/{1} artifacts match MANIFEST.txt exactly, {2} failure(s), {3} warning(s)" -f `
    $okCount, $expectedCount, $script:failCount, $script:warnCount)

if ($script:failCount -gt 0 -or $checked.Count -ne 6) {
    Write-Host ''
    Write-Host ('FATAL: source validation failed - nothing was written (fail-closed). ' +
        'Fix the day folder, or re-run tools\backup_bundles.ps1, before packaging.')
    exit 1
}

# ------------------------------------------------------------ documents -----
$totalText = Format-Count $totalBytes
$mibText = ($totalBytes / 1048576).ToString('F2', [System.Globalization.CultureInfo]::InvariantCulture)
$generatedText = $now.ToString('yyyy-MM-dd HH:mm:ss')

# Row layout shared by both documents. Names are left-aligned in 34 columns
# (the longest artifact name is 31 characters), byte counts right-aligned in 12.
$offsiteRows = New-Object System.Collections.Generic.List[string]
$tableRows   = New-Object System.Collections.Generic.List[string]
$rowIndex = 0
foreach ($e in $checked) {
    $rowIndex++
    $offsiteRows.Add(('{0,2}   {1,-34}  {2,12}  {3}' -f $rowIndex, $e.Name, $e.Bytes, $e.Sha256))
    $descKey = 'desc_data_manifest'
    if ($e.Name -match $bundlePattern) {
        $descKey = 'desc_' + $Matches['label']
    } elseif ($e.Name -match '\.zip$') {
        $descKey = 'desc_workspace-docs'
    }
    $desc = $e.Name
    if ($zhTexts.ContainsKey($descKey)) { $desc = [string]$zhTexts[$descKey] }
    $tableRows.Add(('| {0} | `{1}` | {2} | {3} |' -f $rowIndex, $e.Name, $e.Bytes, $desc))
}

if ($copyMode) { $modeNote = [string]$zhTexts['note_copied'] } else { $modeNote = [string]$zhTexts['note_only'] }

function Expand-Tokens {
    param([string]$Text)
    $t = $Text
    $t = $t.Replace('{ROWS_TABLE}', ($tableRows -join "`r`n"))
    $t = $t.Replace('{ROWS}', ($offsiteRows -join "`r`n"))
    $t = $t.Replace('{MODE_NOTE}', $modeNote)
    $t = $t.Replace('{MANIFEST_TIME}', $manifestTs)
    $t = $t.Replace('{GENERATED}', $generatedText)
    $t = $t.Replace('{TOTAL_BYTES}', $totalText)
    $t = $t.Replace('{COUNT}', [string]$checked.Count)
    $t = $t.Replace('{MIB}', $mibText)
    $t = $t.Replace('{SRC}', $dayDir)
    $t = $t.Replace('{DATE}', $dayName)
    $left = [regex]::Matches($t, '\{[A-Z_]+\}')
    if ($left.Count -gt 0) {
        $names = @($left | ForEach-Object { $_.Value } | Select-Object -Unique) -join ', '
        throw ("unexpanded token(s) in the generated document: {0}" -f $names)
    }
    return $t
}

$offsiteText = Convert-ToCrlf (Expand-Tokens -Text $zhOffsiteTemplate)
$readmeText  = Convert-ToCrlf (Expand-Tokens -Text $zhReadmeTemplate)

$sumsLines = New-Object System.Collections.Generic.List[string]
foreach ($e in $checked) {
    $sumsLines.Add(('{0}  {1}' -f $e.Sha256.ToLowerInvariant(), $e.Name))
}
# Standard sha256sum layout: lowercase hex, two spaces, one name per line, LF
# endings and no BOM, so that 'sha256sum -c SHA256SUMS.txt' works unmodified.
$sumsText = ($sumsLines -join "`n") + "`n"

$outputs = @(
    [pscustomobject]@{ Name = ($dayName + '-offsite.txt'); Text = $offsiteText; Crlf = $true }
    [pscustomobject]@{ Name = 'SHA256SUMS.txt';             Text = $sumsText;    Crlf = $false }
    [pscustomobject]@{ Name = $readmeName;                  Text = $readmeText;  Crlf = $true }
)
foreach ($o in $outputs) {
    $o | Add-Member -NotePropertyName 'Path' -NotePropertyValue (Join-Path $OutDir $o.Name)
    $o | Add-Member -NotePropertyName 'Bytes' -NotePropertyValue ([System.Text.Encoding]::UTF8.GetByteCount($o.Text))
}

$outDirBytes = [long]0
foreach ($o in $outputs) { $outDirBytes += $o.Bytes }
$needBytes = $totalBytes
if ($copyMode) { $needBytes = $totalBytes + $outDirBytes }
$freeBytes = Get-FreeSpaceBytes -Path $OutDir

Write-Host ''
if ($copyMode) {
    Write-Host 'copy mode   : ON - the 6 artifacts will be copied as well'
} else {
    Write-Host 'copy mode   : off - control files only (add -Copy to stage the bundles)'
}
Write-Host ("payload     : {0} bytes ({1} MiB) in {2} artifact(s)" -f $totalText, $mibText, $checked.Count)
if ($copyMode) {
    Write-Host ("              -Copy writes {0} bytes to {1}: a few seconds on an SSD," -f (Format-Count $needBytes), $OutDir)
    Write-Host ('              tens of seconds on a spinning disk. Time is disk-bound, not CPU-bound.')
} else {
    Write-Host ('              not copied: the 683 MB bundle stays in the vault; upload it from there')
}
if ($freeBytes -ge 0) {
    Write-Host ("free space  : {0} bytes on {1}" -f (Format-Count $freeBytes), [System.IO.Path]::GetPathRoot($OutDir))
}
if ($copyMode -and $freeBytes -ge 0 -and $freeBytes -lt ($needBytes * 1.02)) {
    Write-Host ''
    Write-Host ("FATAL: not enough free space on {0}: need {1} bytes, have {2} bytes." -f `
        [System.IO.Path]::GetPathRoot($OutDir), (Format-Count $needBytes), (Format-Count $freeBytes))
    exit 2
}

# ---------------------------------------------------------------- write -----
if ($DryRun) {
    Write-Host ''
    Write-Host 'planned control files (dry run: nothing is written):'
    foreach ($o in $outputs) {
        Write-Host ("  [plan] {0,-52} {1,8} bytes" -f $o.Path, $o.Bytes)
    }
    if ($copyMode) {
        foreach ($e in $checked) {
            Write-Host ("  [plan] copy {0,-32} {1,12} bytes" -f $e.Name, $e.Bytes)
        }
    }
    Write-Host ''
    Write-Host ('DRY RUN: no file was created, changed or deleted. Re-run without -DryRun ' +
        'to write the 3 control file(s).')
    exit 0
}

if (-not (Test-Path -LiteralPath $OutDir -PathType Container)) {
    Write-Host ''
    Write-Host ("creating out dir: {0}" -f $OutDir)
    $null = New-Item -ItemType Directory -Path $OutDir -Force
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach ($o in $outputs) {
    try {
        [System.IO.File]::WriteAllText($o.Path, $o.Text, $utf8NoBom)
    } catch {
        Write-Host ''
        Write-Host ("FATAL: cannot write {0}: {1}" -f $o.Path, $_.Exception.Message)
        exit 2
    }
    $written = (Get-Item -LiteralPath $o.Path -Force).Length
    $expect = [System.Text.Encoding]::UTF8.GetByteCount($o.Text)
    if ($written -ne $expect) {
        Write-Host ("  [FAIL] {0}: wrote {1} bytes, expected {2}" -f $o.Name, $written, $expect)
        exit 2
    }
    Write-Host ("  [ok]   wrote {0,-52} {1,8} bytes" -f $o.Path, $written)
}

# Optional staging copy. Verified by byte count, never by hash: the point is
# "did every byte land", and the hash pass belongs on the receiving side.
if ($copyMode) {
    Write-Host ''
    Write-Host ("copying {0} artifact(s), {1} bytes total:" -f $checked.Count, $totalText)
    $swCopy = [System.Diagnostics.Stopwatch]::StartNew()
    $copiedBytes = [long]0
    foreach ($e in $checked) {
        $src = Join-Path $dayDir $e.Name
        $dst = Join-Path $OutDir $e.Name
        Copy-Item -LiteralPath $src -Destination $dst -Force
        $got = [long](Get-Item -LiteralPath $dst -Force).Length
        if ($got -ne $e.Bytes) {
            Write-Host ("  [FAIL] copy {0}: {1} bytes copied, expected {2}" -f $e.Name, $got, $e.Bytes)
            exit 2
        }
        $copiedBytes += $got
        Write-Host ("  [ok]   copied {0,-32} {1,12} bytes" -f $e.Name, $got)
    }
    $swCopy.Stop()
    $rate = 0.0
    if ($swCopy.Elapsed.TotalSeconds -gt 0) { $rate = ($copiedBytes / 1MB) / $swCopy.Elapsed.TotalSeconds }
    Write-Host ("  copied {0} bytes in {1:N1} s ({2:N0} MiB/s)" -f (Format-Count $copiedBytes), $swCopy.Elapsed.TotalSeconds, $rate)
}

# --------------------------------------------------------------- summary ----
Write-Host ''
Write-Host ("package ready: {0}" -f $OutDir)
Write-Host ("  artifacts : {0} files, {1} bytes ({2} MiB)" -f $checked.Count, $totalText, $mibText)
Write-Host ("  control   : 3 files, {0} bytes" -f (Format-Count $outDirBytes))
Write-Host ''
Write-Host 'this script packaged; it did NOT upload. Offsite copies still: 0.'
Write-Host 'next steps (human):'
Write-Host '  1) upload the 6 artifacts + the 3 control files (9 files, keep the names)'
Write-Host '     to an offsite target: private object storage, private git host, or a'
Write-Host '     cloud drive that keeps original file names'
Write-Host '  2) on that target run: sha256sum -c SHA256SUMS.txt   (or the PowerShell'
Write-Host '     snippet in the README) - all 6 lines must say OK'
Write-Host '  3) record target + date + result in _vault\README.md, then re-run this'
Write-Host '     tool after the next backup'
Write-Host ''
exit 0
