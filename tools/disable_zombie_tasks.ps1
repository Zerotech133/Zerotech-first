#requires -Version 5.1
<#
.SYNOPSIS
    Disable (or re-enable) the two scheduled tasks that fire against a target
    that does not exist.

.DESCRIPTION
    Evidence gathered 2026-10-22 from Microsoft-Windows-TaskScheduler/Operational
    (Event ID 201, EventData TaskName/ResultCode):

      \JevStack           code=4294770688  71 times today, every 10 minutes
      \FeishuBotWatchdog  code=4294770688 142 times today, every ~10 minutes

    4294770688 = 0xFFFD0000 = -196608. Verified on this machine: it is what
    powershell.exe returns when its -File argument points at a file that does
    not exist (a process does start, then exits immediately).

    JevStack's target, C:\aios\jev-eval\start_jev_stack.ps1, has NEVER existed
    in git history, and the jev-eval folder now holds 16 .ps1 files, none with
    that name. FeishuBotWatchdog's definition cannot be read at all ("Access is
    denied"), and it returns the same code, so its target is missing too.

    Consequence: both tasks cannot do anything useful - they start a shell,
    fail to find the script, and exit. They contribute ~213 failure events per
    day and bury real failures in the event log.

    Nothing is lost by disabling them. If the functions are still wanted
    (a jev-stack watchdog, a Feishu-bot watchdog), re-create the tasks against
    the CURRENT entry point - do not resurrect a script that never existed.

    ---- FIVE ELEMENTS (AGENTS.md section 7 format) ----

    1. Full command
         schtasks /change /tn "\JevStack" /disable
         schtasks /change /tn "\FeishuBotWatchdog" /disable

    2. Blast radius
         Two task definitions: their Enabled flag only. The actions, triggers,
         principals and history are untouched, and -Enable restores them.

    3. Irreversible point
         None. Disabling is a flag flip; -Enable puts it back. (Task history is
         not modified either.)

    4. Alternatives tried
         - Repointing the actions: JevStack's target never existed, so there is
           nothing to repoint it to; FeishuBotWatchdog's definition is
           unreadable, so it cannot even be inspected without admin tooling.
         - Leaving them: costs ~213 misleading events/day.

    5. Rollback
         powershell.exe -NoProfile -ExecutionPolicy Bypass `
             -File tools\disable_zombie_tasks.ps1 -Enable

.PARAMETER Enable
    Re-enable both tasks instead of disabling them.

.PARAMETER VerifyOnly
    Show the current state; change nothing.
#>
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

$tasks = @('JevStack', 'FeishuBotWatchdog')
$tasksRoot = Join-Path $env:WINDIR 'System32\Tasks'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-EnabledFlag {
    param([string]$Name)
    $p = Join-Path $tasksRoot $Name
    try {
        if (-not (Test-Path -LiteralPath $p)) { return '(definition not found)' }
        $t = [System.IO.File]::ReadAllText($p)
    } catch {
        return '(unreadable)'
    }
    $m = [regex]::Match($t, '<Enabled>([^<]+)</Enabled>')
    if ($m.Success) { return $m.Groups[1].Value }
    return '(no Enabled node: defaults to true)'
}

Write-Host '=== zombie task switch ==='
Write-Host ('tasks    : {0}' -f ($tasks -join ', '))
Write-Host ('action   : {0}' -f $(if ($Enable) { 'ENABLE' } else { 'DISABLE' }))
Write-Host ('elevated : {0}' -f (Test-Elevated))
Write-Host ''

Write-Host '--- current state ---'
foreach ($n in $tasks) {
    Write-Host ('  {0,-20} Enabled={1}' -f $n, (Get-EnabledFlag -Name $n))
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
    if ($Enable) { $fwd += '-Enable' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $fwd -Verb RunAs -ErrorAction Stop
        Write-Host 'elevated instance started. Watch the new window.'
        exit 0
    } catch {
        Write-Host ('ERROR: could not elevate: {0}' -f $_.Exception.Message)
        exit 2
    }
}

$verb = if ($Enable) { '/enable' } else { '/disable' }
$failed = 0
Write-Host ''
Write-Host ('--- applying ({0}) ---' -f $verb)

foreach ($n in $tasks) {
    $out = & schtasks /change /tn $n $verb 2>&1
    $rc = $LASTEXITCODE
    if ($rc -eq 0) {
        Write-Host ('  {0,-20} OK   Enabled={1}' -f $n, (Get-EnabledFlag -Name $n))
    } else {
        Write-Host ('  {0,-20} FAIL rc={1}' -f $n, $rc)
        $out | Select-Object -First 2 | ForEach-Object { Write-Host ('      ' + $_) }
        $failed++
    }
}

Write-Host ''
Write-Host ('summary  : {0} ok, {1} problem(s)' -f ($tasks.Count - $failed), $failed)
Write-Host 'verify   : re-run with -VerifyOnly'
Write-Host 'rollback : tools\disable_zombie_tasks.ps1 -Enable'
Write-Host ''
Write-Host 'After this, 0xFFFD0000 should stop appearing in the event log.'
Write-Host 'Expect the health card check ops.task_launch_failures to drop within'
Write-Host 'the 30-minute grading window.'
if ($failed -gt 0) { exit 1 }
exit 0
