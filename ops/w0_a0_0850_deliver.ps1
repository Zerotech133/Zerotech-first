$ErrorActionPreference = 'Stop'

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # Non-fatal if the host cannot change its console encoding.
}

$TaskName = 'W0_A0_0850_Deliver'
$Log = 'C:\Temp\w0_a0_0850_deliver.log'
$Root = Split-Path -Parent $PSScriptRoot
$Python = 'C:\Program Files\Python311\python.exe'
$Script = Join-Path $Root 'tools\w0_a0_0850_deliver.py'

function Write-DeliveryLog([string]$Message) {
    Add-Content -LiteralPath $Log -Value $Message -Encoding UTF8
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
Write-DeliveryLog "[$stamp] START task=$TaskName cwd=$Root"

Set-Location -LiteralPath $Root
$code = 1
$lines = @()
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $lines = @(& $Python -X utf8 $Script 2>&1)
    $code = $LASTEXITCODE
} catch {
    $lines += "ERROR: $($_.Exception.Message)"
    $code = 1
} finally {
    $ErrorActionPreference = $oldPreference
}

foreach ($line in $lines) {
    Write-DeliveryLog "[$stamp] $([string]$line)"
}

if ($null -eq $code) {
    $code = 1
}

if ($code -ne 0) {
    Write-DeliveryLog "[$stamp] FAIL rc=$code task=$TaskName"
    exit $code
}

$delivery = $null
foreach ($line in $lines) {
    $text = [string]$line
    if ($text -notmatch '^\s*\{') {
        continue
    }
    try {
        $obj = $text | ConvertFrom-Json
        if ($obj.ok -eq $true) {
            $delivery = $obj
        }
    } catch {
        # Ignore non-JSON lines; the rc=0 result is still recorded below.
    }
}

if ($delivery) {
    $id = [string]$delivery.data.id
    Write-DeliveryLog "[$stamp] SUCCESS delivered id=$id task=$TaskName"
    exit 0
}

$allText = ($lines | ForEach-Object { [string]$_ }) -join "`n"
if ($allText -match '(?m)^skip:') {
    Write-DeliveryLog "[$stamp] SKIP no delivery task=$TaskName"
    exit 0
}

Write-DeliveryLog "[$stamp] END rc=0 no delivery payload task=$TaskName"
exit 0
