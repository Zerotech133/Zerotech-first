# Hermes ACAD1 (Zhice) extension build pipeline - thin wrapper.
# ASCII-only on purpose: the repo path contains CJK characters, and a UTF-8
# .ps1 without BOM is mis-decoded by Windows PowerShell 5.1. All logic lives in
# tools/hermes_acad1_build.py; this file only locates and invokes it.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\hermes_acad1_build.ps1
#   powershell ... -File tools\hermes_acad1_build.ps1 --publish-live
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$script = Join-Path $here 'hermes_acad1_build.py'
if (-not (Test-Path -LiteralPath $script)) {
    Write-Error "build script not found: $script"
    exit 2
}
$py = $env:PYTHON
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    Write-Error "python not found in PATH; set PYTHON env var to a python.exe"
    exit 2
}
Push-Location -LiteralPath $root
try {
    & $py $script @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
