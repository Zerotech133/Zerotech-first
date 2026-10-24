# start_power_watch.ps1 - launch tools\power_watch.py detached (idempotent).
#
# ASCII ONLY. This file must stay pure ASCII (no BOM, CRLF): the repo has
# precedent where a .ps1 with Chinese text was parsed as GBK by PS 5.1 and
# blew up. All Chinese paths are DERIVED at runtime ($PSScriptRoot / its
# parent), never written as literals.
#
# Idempotent: if a python process whose command line contains power_watch.py
# is already running, print "already running pid=..." and exit 0.
# Detection order:
#   1) CIM query for python processes (the spec path)
#   2) pid file fallback (C:\Temp\power_watch.jsonl.pid written by the script)
#      because process listing is denied under restricted tokens and under
#      sandboxed shells (verified on 2026-10-24 in this very session).
#
# Interpreter preference: Python311 (production) first, then py312 which sits
# next to this repo, then Program Files Python312, then PATH.
#
# Output: C:\Temp\power_watch.out.log / C:\Temp\power_watch.err.log
# Stop:   taskkill /PID <pid> /F  (hard kill; no "stop" line is written) or
#         send CTRL_BREAK to its process group for a graceful stop line.
# Read:   Get-Content C:\Temp\power_watch.jsonl -Tail 3
#
# This script only starts one detached process. It does not touch services,
# credentials, the registry or power settings.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pyScript = Join-Path $root 'tools\power_watch.py'
$outLog = 'C:\Temp\power_watch.out.log'
$errLog = 'C:\Temp\power_watch.err.log'
$pidFile = 'C:\Temp\power_watch.jsonl.pid'

$script:cimNote = $null

function Get-PidFileOwner {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $pidFile -Raw -Encoding UTF8
        if (-not $raw) { return $null }
        $meta = $raw | ConvertFrom-Json
        if (-not $meta.pid) { return $null }
        if ($meta.script -and ($meta.script -notmatch 'power_watch\.py$')) { return $null }
        $ownerPid = [int]$meta.pid
        $proc = Get-Process -Id $ownerPid -ErrorAction Stop
        if ($proc.ProcessName -notlike 'python*') { return $null }
        $recorded = [datetime]::ParseExact([string]$meta.started, 'yyyy-MM-ddTHH:mm:ss', $null)
        if ([math]::Abs(($proc.StartTime - $recorded).TotalSeconds) -gt 5) { return $null }
        return $ownerPid
    } catch {
        return $null
    }
}

function Get-PowerWatchPid {
    try {
        $procs = Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop
        foreach ($proc in $procs) {
            $cmd = $proc.CommandLine
            if (-not $cmd) { continue }
            if ($cmd -notmatch 'power_watch\.py') { continue }
            if ($cmd -match '--once') { continue }
            return [int]$proc.ProcessId
        }
        return $null
    } catch {
        $script:cimNote = 'warning: CIM/WMI unavailable (' + $_.Exception.Message.Trim() + '); using pid file fallback'
    }
    return (Get-PidFileOwner)
}

function Resolve-Python {
    $candidates = @(
        'C:\Program Files\Python311\python.exe',
        (Join-Path (Split-Path -Parent $root) 'py312\python.exe'),
        'C:\Program Files\Python312\python.exe'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

if (-not (Test-Path -LiteralPath $pyScript -PathType Leaf)) {
    Write-Output ('error: missing script ' + $pyScript)
    exit 1
}

foreach ($dir in @((Split-Path -Parent $outLog), (Split-Path -Parent $errLog))) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

$existingPid = Get-PowerWatchPid
if ($script:cimNote) { Write-Output $script:cimNote }
if ($existingPid) {
    Write-Output ('already running pid=' + $existingPid)
    exit 0
}

$python = Resolve-Python
if (-not $python) {
    Write-Output 'error: no python.exe found (tried Python311, py312 beside repo, Python312, PATH)'
    exit 1
}

try {
    $started = Start-Process -FilePath $python `
        -ArgumentList @('-B', ('"' + $pyScript + '"')) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
} catch {
    Write-Output ('error: Start-Process failed: ' + $_.Exception.Message.Trim())
    exit 1
}

Start-Sleep -Milliseconds 900
$started.Refresh()
if ($started.HasExited) {
    Write-Output ('error: power_watch exited immediately rc=' + $started.ExitCode)
    if (Test-Path -LiteralPath $errLog) {
        Get-Content -LiteralPath $errLog -Tail 5 | ForEach-Object { Write-Output ('  stderr: ' + $_) }
    }
    exit 1
}

Write-Output ('started pid=' + $started.Id + ' interpreter=' + $python)
Write-Output ('  script=' + $pyScript)
Write-Output ('  log=C:\Temp\power_watch.jsonl stdout=' + $outLog + ' stderr=' + $errLog)
exit 0
