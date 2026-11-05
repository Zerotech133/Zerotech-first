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
$StartDate = [datetime]::ParseExact('2026-11-06', 'yyyy-MM-dd', $null)
$GenericScript = Join-Path $Root 'tools\w0_a0_daily_0850_deliver.py'

function Write-DeliveryLog([string]$Message) {
    Add-Content -LiteralPath $Log -Value $Message -Encoding UTF8
}

$Now = Get-Date
$testMode = $false
if ($env:W0_DAILY_TEST_DATE) {
    $Now = [datetime]::ParseExact($env:W0_DAILY_TEST_DATE, 'yyyy-MM-dd', $null)
    $testMode = $true
}

$DateKey = $Now.ToString('MMdd')
$DateStr = $Now.ToString('yyyy-MM-dd')
$DateTask = 'W0_A0_{0}_0850_Deliver' -f $DateKey
$SpecificScript = Join-Path $Root ("tools\w0_a0_{0}_0850_deliver.py" -f $DateKey)

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
Write-DeliveryLog "[$stamp] START task=$TaskName cwd=$Root date=$DateStr"

if ($Now.Date -lt $StartDate.Date) {
    Write-DeliveryLog "[$stamp] SKIP not started start_date=2026-11-06 date=$DateStr"
    exit 0
}

if ($Now.DayOfWeek -eq [System.DayOfWeek]::Saturday -or $Now.DayOfWeek -eq [System.DayOfWeek]::Sunday) {
    Write-DeliveryLog "[$stamp] SKIP weekend date=$DateStr"
    exit 0
}

# If today's dedicated one-off task exists and is enabled, it owns delivery for
# today; skip here to avoid a duplicate A0 notification. 11-06 is intentionally
# handled by this daily task (its one-off task is disabled).
if ($DateTask -ne $TaskName) {
    $dateTask = Get-ScheduledTask -TaskName $DateTask -ErrorAction SilentlyContinue
    if ($null -ne $dateTask -and $dateTask.State -ne 'Disabled') {
        Write-DeliveryLog "[$stamp] SKIP date_specific_task_active task=$DateTask state=$($dateTask.State)"
        exit 0
    }
}

$Script = $GenericScript
if (Test-Path -LiteralPath $SpecificScript) {
    $Script = $SpecificScript
}
Write-DeliveryLog "[$stamp] SELECT script=$Script date=$DateStr"

if ($testMode) {
    Write-DeliveryLog "[$stamp] PLAN_ONLY test_date=$DateStr script=$Script"
    exit 0
}

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
