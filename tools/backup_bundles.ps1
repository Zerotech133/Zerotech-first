#requires -Version 5.1
<#
.SYNOPSIS
    Single-disk vault backup: git bundles for the four repos + a zip of the
    workspace-root Markdown and HTML files, plus MANIFEST.txt and day-folder
    rotation.

.DESCRIPTION
    Layout written under <VaultRoot> (default: <workspace>\_vault):

        <VaultRoot>\<yyyy-MM-dd>\<short>-<yyyyMMdd>.bundle   (git bundle --all)
        <VaultRoot>\<yyyy-MM-dd>\workspace-docs-<yyyyMMdd>.zip
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
    * Repos and the workspace root are derived from $PSScriptRoot
      (tools -> repo -> workspace), so no non-ASCII path literal is baked in.
    * A failing repo is reported and skipped; the remaining repos still run.
    * A single unreadable archive input is skipped with a warning and a failed
      exit code, while the remaining files still go into the zip.
    * -DryRun prints the whole plan and creates/modifies/deletes nothing.
    * Rotation only touches child folders whose name is exactly yyyy-MM-dd,
      is strictly older than -KeepDays, and is not a reparse point.
    * No upload, no git config writes, no repo mutation (git bundle only reads).

.PARAMETER VaultRoot
    Destination root. Defaults to '<workspace>\_vault' where <workspace> is the
    parent of the repo that contains this script.

.PARAMETER KeepDays
    Keep date folders whose day is >= today - KeepDays. Default 14. 0 keeps
    only today's folder.

.PARAMETER DryRun
    Print the plan only. Nothing is created, overwritten or deleted.

.PARAMETER Exclude
    Extra filename globs (matched against the leaf name, case-insensitive) to
    keep out of the workspace zip. The built-in guards - .mcp.json, .env,
    .env.*, .env* - are always applied and cannot be removed, even when this
    parameter is supplied.

.NOTES
    Exit code: 0 = success (dry-run always 0), 1 = at least one bundle /
    workspace archive / rotation failure.
    Rotation permanently deletes old backup folders (not Recycle Bin) because
    reclaiming vault space is its purpose; use -DryRun to preview.
#>
[CmdletBinding()]
param(
    [string]$VaultRoot = '',
    [ValidateRange(0, 3650)][int]$KeepDays = 14,
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
$cutoff   = $now.Date.AddDays(-$KeepDays)

# Label is ASCII and is also the bundle filename prefix.
$repos = @(
    [pscustomobject]@{ Label = 'aios';           Path = $repoRoot }
    [pscustomobject]@{ Label = 'zerobound-kg';   Path = (Join-Path $workspace 'zerobound-kg') }
    [pscustomobject]@{ Label = 'digital-legion'; Path = (Join-Path $workspace 'digital-legion') }
    [pscustomobject]@{ Label = 'res-downloader'; Path = (Join-Path $workspace 'res-downloader-py') }
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
$script:failCount      = 0
$script:backedUpBytes  = [long]0
$script:failures       = New-Object System.Collections.Generic.List[string]
$script:manifestLines  = New-Object System.Collections.Generic.List[string]

# -------------------------------------------------------------- helpers -----
function Add-Failure {
    param([string]$Label, [string]$Detail)
    $script:failCount++
    $script:failures.Add(('{0}: {1}' -f $Label, $Detail))
    Write-Host ("  [FAIL] {0}  {1}" -f $Label, $Detail)
}

function Add-ManifestEntry {
    param([System.IO.FileInfo]$Item, [string]$Hash)
    $line = '{0}  {1}  {2} bytes  sha256={3}' -f `
        $Item.Name, $Item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $Item.Length, $Hash
    $script:manifestLines.Add($line)
    $script:backedUpBytes += $Item.Length
    $script:okCount++
    Write-Host ("  [ok]   {0}  {1:N0} bytes  sha256={2}" -f $Item.Name, $Item.Length, $Hash.Substring(0, 12))
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
}

Write-Host 'repos:'
foreach ($repo in $repos) { Invoke-Bundle -Repo $repo }

Write-Host 'workspace archive (md + html):'
Invoke-DocsArchive

Write-Host 'rotation:'
$rot = Invoke-Rotation -Root $VaultRoot -Cutoff $cutoff -Preview:$DryRun.IsPresent

if (-not $DryRun) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(('# vault backup {0}' -f $now.ToString('yyyy-MM-dd HH:mm:ss')))
    $lines.Add(('# vault {0}' -f $dayDir))
    foreach ($line in $script:manifestLines) { $lines.Add($line) }
    $lines.Add(('# summary ok={0} failed={1} bytes={2}' -f `
        $script:okCount, $script:failCount, $script:backedUpBytes))
    Set-Content -LiteralPath $manifest -Value $lines -Encoding UTF8 -Force
    Write-Host ''
    Write-Host ("manifest    : {0}" -f $manifest)
}

$swTotal.Stop()
Write-Host ''
Write-Host '=== summary ==='
if ($DryRun) {
    Write-Host ("repos       : {0} planned, {1} problem(s)" -f $repos.Count, $script:failCount)
    Write-Host ("docs        : {0} md + {1} html = {2} file(s), {3:N0} bytes" -f `
        $docs.Count, $html.Count, $archiveFiles.Count, [long]$archiveBytes)
    Write-Host ("excluded    : {0}" -f (Get-ExcludedSummary))
    Write-Host ("rotation    : {0} folder(s) would be removed" -f $rot.Candidates)
    Write-Host 'writes      : none (dry-run)'
    Write-Host 'result      : OK (plan only)'
    exit 0
}

Write-Host ("entries     : {0} file(s), {1:N0} bytes" -f $script:okCount, $script:backedUpBytes)
Write-Host ("archive     : {0} md + {1} html = {2} file(s), {3:N0} input bytes" -f `
    $docs.Count, $html.Count, $archiveFiles.Count, [long]$archiveBytes)
Write-Host ("excluded    : {0}" -f (Get-ExcludedSummary))
Write-Host ("failed      : {0}" -f $script:failCount)
if ($script:failCount -gt 0) {
    foreach ($f in $script:failures) { Write-Host ("  - {0}" -f $f) }
}
Write-Host ("rotation    : {0} removed, {1} failed" -f $rot.Removed, $rot.Failed)
Write-Host ("elapsed     : {0:N1}s" -f $swTotal.Elapsed.TotalSeconds)

if ($script:failCount -gt 0 -or $rot.Failed -gt 0) {
    Write-Host 'result      : FAILED'
    exit 1
}
Write-Host 'result      : OK'
exit 0
