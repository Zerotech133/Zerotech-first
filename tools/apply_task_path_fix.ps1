#requires -Version 5.1
<#
.SYNOPSIS
    Re-register 7 scheduled tasks whose actions could not start at all.

.DESCRIPTION
    Evidence (2026-10-22, Microsoft-Windows-TaskScheduler/Operational, ID 201):

        \QuantWatchdog     code=2147942402  710 times  04:37 -> 16:26
        \AlertWatch        code=2147942401   22 times  04:45 -> 15:15
        \AlertWatch        code=2147942403    2 times  15:45 -> 16:16
        \StrongEod         code=2147942403    2 times
        \XmapSync          code=2147942403    1 time
        \KgChainWatchdog   code=2147942401    2 times
        \DeadlineWatch     code=2147942402    2 times
        (\FeishuBotWatchdog and \JevStack return 0xFFFD0000 - different cause,
         they are included here only for the junction-alias repair.)

    0x80070001 / 0x80070002 / 0x80070003 all mean the ACTION NEVER STARTED
    (invalid function / file not found / path not found), not a script error.

    Two causes:

    A. QuantWatchdog stores a path that went through a UTF-8 -> GBK mis-decode.
       The literal in the task is mojibake, so no such file can exist.
       That single task accounts for 710 failures/day.

    B. Six tasks reference the junction alias C:\aios in their action,
       arguments or working directory. An interactive session resolves it;
       the task sessions do not, returning the 0x8007000x family.

    Fix: rewrite those paths to the physical root, keep every other node
    (triggers, principal, settings) byte-identical. AlertWatch additionally
    gets <Interval> PT30M -> PT5M: it polls every 30 minutes while its
    freshness gate only accepts sources completed within 5 minutes, so the two
    clocks can never line up.

    This script deliberately contains NO non-ASCII bytes and no mojibake
    literal. Verification compares the live definition against the corrected
    XML we generated, so it never needs to spell out the broken path.

    ---- FIVE ELEMENTS (AGENTS.md section 7 format) ----

    1. Full command
         schtasks /create /tn "<name>" /xml "<repo>\tools\schtasks_xml\<name>_fixed.xml" /f
       for the seven names listed in $allTasks.

    2. Blast radius
         Exactly seven task definitions. Only <Command>/<Arguments>/
         <WorkingDirectory> path strings change (C:\aios -> physical root, or
         the mojibake prefix -> physical root); AlertWatch also changes
         <Interval>. Triggers, principal (S4U), run level and settings are
         copied verbatim from the live definitions.

    3. Irreversible point
         The seven definitions get overwritten. Each original is saved
         verbatim as tools\schtasks_xml\<name>_original.xml, so -Restore
         puts all of them back.

    4. Alternatives tried
         - Fixing only AlertWatch: leaves a watchdog dead 710 times a day.
         - Editing tasks through the API: unavailable to this session
           (schtasks -> path not found, CIM -> 0x80041003 access denied).

    5. Rollback
         powershell.exe -NoProfile -ExecutionPolicy Bypass `
             -File tools\apply_task_path_fix.ps1 -Restore

.PARAMETER Restore
    Re-register every original definition.

.PARAMETER VerifyOnly
    Report, per task, whether the live definition already equals the fixed XML.
    Changes nothing.

.PARAMETER Only
    Apply only these task names (default: all seven).
#>
[CmdletBinding()]
param(
    [switch]$Restore,
    [switch]$VerifyOnly,
    [string[]]$Only,
    [switch]$IncludeHardening
)

$ErrorActionPreference = 'Stop'

# DEFAULT: only the one with a PROVEN broken path (UTF-8 -> GBK mojibake in
# <Arguments>, 712 launch failures today). The other six use the C:\aios alias
# exactly like 27 tasks that run fine, so the alias is NOT the discriminator -
# their 1-2 failures look transient. Rewriting them is harmless hardening, not
# a bug fix, hence opt-in. See the "task path failure - correction" doc in the
# workspace root (dated 2026-10-22).
# NOTE (2026-10-22 17:00): -IncludeHardening is NOT recommended.
# AGENTS.md section 8 registers C:\aios as the official ASCII alias for this
# repo, and section 0 forbids Chinese paths inside .ps1/.bat because PS 5.1
# misreads them as CP936. Rewriting six working tasks from the ASCII alias to
# C:\<chinese>\... would swim against both rules and risks re-creating the very
# mojibake that broke QuantWatchdog. The switch stays only as an escape hatch
# for a future case where the alias itself is proven broken.
$allTasks = @('QuantWatchdog')
if ($IncludeHardening) {
    $allTasks += @('JevStack', 'KgChainWatchdog', 'StrongEod', 'XmapSync',
                   'AlertWatch', 'DeadlineWatch')
}
$xmlDir = Join-Path $PSScriptRoot 'schtasks_xml'
$tasksRoot = Join-Path $env:WINDIR 'System32\Tasks'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Node {
    param([string]$Xml, [string]$Tag)
    $m = [regex]::Match($Xml, ('<' + $Tag + '>([^<]+)</' + $Tag + '>'))
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}

function Get-Signature {
    param([string]$Xml)
    return [pscustomobject]@{
        Command  = Get-Node $Xml 'Command'
        Args     = Get-Node $Xml 'Arguments'
        Cwd      = Get-Node $Xml 'WorkingDirectory'
        Interval = Get-Node $Xml 'Interval'
    }
}

function Read-XmlFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return [System.IO.File]::ReadAllText($Path) } catch { return $null }
}

$targets = if ($Only) { $Only } else { $allTasks }

Write-Host '=== scheduled task path repair ==='
Write-Host ('tasks    : {0}' -f ($targets -join ', '))
Write-Host ('xml dir  : {0}' -f $xmlDir)
Write-Host ('elevated : {0}' -f (Test-Elevated))
Write-Host ''

Write-Host '--- current state (compared against the corrected xml) ---'
foreach ($n in $targets) {
    $liveXml = Read-XmlFile (Join-Path $tasksRoot $n)
    $fixedXml = Read-XmlFile (Join-Path $xmlDir ($n + '_fixed.xml'))
    if ($null -eq $liveXml) { Write-Host ('  {0,-18} (cannot read live definition)' -f $n); continue }
    $live = Get-Signature $liveXml
    $want = if ($null -ne $fixedXml) { Get-Signature $fixedXml } else { $null }
    $applied = $false
    if ($null -ne $want) {
        $applied = ($live.Command -eq $want.Command) -and ($live.Args -eq $want.Args) -and
                   ($live.Cwd -eq $want.Cwd) -and ($live.Interval -eq $want.Interval)
    }
    $alias = $liveXml -match 'C:\\aios'
    Write-Host ('  {0,-18} interval={1,-6} applied={2,-5} hasAlias={3}' -f $n, $live.Interval, $applied, $alias)
    if ($live.Args) {
        Write-Host ('      args: ' + $live.Args.Substring(0, [Math]::Min(96, $live.Args.Length)))
    }
}

if ($VerifyOnly) {
    Write-Host ''
    Write-Host 'verify-only: nothing changed.'
    exit 0
}

if (-not (Test-Elevated)) {
    Write-Host ''
    Write-Host 'not elevated -> re-launching with a UAC prompt ...'
    $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $PSCommandPath)
    if ($Restore) { $fwd += '-Restore' }
    if ($Only) { $fwd += '-Only'; $fwd += $Only }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $fwd -Verb RunAs -ErrorAction Stop
        Write-Host 'elevated instance started. Watch the new window.'
        exit 0
    } catch {
        Write-Host ('ERROR: could not elevate: {0}' -f $_.Exception.Message)
        exit 2
    }
}

$suffix = if ($Restore) { '_original.xml' } else { '_fixed.xml' }
$failed = 0
Write-Host ''
Write-Host ('--- applying ({0}) ---' -f $suffix)

foreach ($n in $targets) {
    $xml = Join-Path $xmlDir ($n + $suffix)
    $wantXml = $null
    if (-not $Restore) { $wantXml = Read-XmlFile $xml }
    if (-not (Test-Path -LiteralPath $xml)) {
        Write-Host ('  {0,-18} SKIP: xml missing' -f $n)
        $failed++
        continue
    }
    $out = & schtasks /create /tn $n /xml $xml /f 2>&1
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host ('  {0,-18} FAIL rc={1}' -f $n, $rc)
        $out | Select-Object -First 2 | ForEach-Object { Write-Host ('      ' + $_) }
        $failed++
        continue
    }
    $liveXml = Read-XmlFile (Join-Path $tasksRoot $n)
    if ($null -eq $liveXml) {
        Write-Host ('  {0,-18} applied (live definition unreadable, cannot confirm)' -f $n)
        continue
    }
    $live = Get-Signature $liveXml
    if ($null -ne $wantXml) {
        $want = Get-Signature $wantXml
        $ok = ($live.Command -eq $want.Command) -and ($live.Args -eq $want.Args) -and
              ($live.Cwd -eq $want.Cwd) -and ($live.Interval -eq $want.Interval)
        Write-Host ('  {0,-18} OK   interval={1,-6} matchesXml={2}' -f $n, $live.Interval, $ok)
        if (-not $ok) { $failed++ }
    } else {
        Write-Host ('  {0,-18} restored interval={1}' -f $n, $live.Interval)
    }
}

Write-Host ''
Write-Host ('summary  : {0} ok, {1} problem(s)' -f ($targets.Count - $failed), $failed)
Write-Host 'verify   : re-run this script with -VerifyOnly'
Write-Host 'rollback : tools\apply_task_path_fix.ps1 -Restore'
Write-Host ''
Write-Host 'NOTE: these tasks were failing to START. After this, expect rc=0 or a'
Write-Host '      real script exit code; 0x8007000x should disappear from the log.'
if ($failed -gt 0) { exit 1 }
exit 0
