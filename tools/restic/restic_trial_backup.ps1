# restic_trial_backup.ps1 - P1 生产化（2026-11-03）：备份 -> 保留策略 -> 校验 -> receipt -> 告警
# 原试跑脚本已备份为 restic_trial_backup.ps1.bak-prod-<stamp>
$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logDir = 'C:\项目\work\restic_trial'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('run-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
function Log([string]$m) { Add-Content -Path $log -Value ('[' + $stamp + '] ' + $m) -Encoding UTF8 }

$py = 'C:\项目\py312\python.exe'
$vault = 'C:\项目\老田的AI助手\tools\secrets\vault.py'
$restic = 'C:\Users\t\dw_ops\restic.exe'
$receipt = 'C:\项目\老田的AI助手\tools\restic\restic_receipt.py'
$alert = 'C:\项目\老田的AI助手\news_collector\alert_fail_wrap.py'
$env:RESTIC_REPOSITORY = 'C:\项目\_restic_repo'
$env:RESTIC_CACHE_DIR = 'C:\项目\_restic_cache'
$errLog = 'C:\Temp\restic_backup_err.log'

$pwdOut = & $py $vault decrypt restic/repo_password 2>&1
if ($LASTEXITCODE -ne 0 -or -not $pwdOut) { Log 'FAIL: vault decrypt restic/repo_password'; exit 2 }
$env:RESTIC_PASSWORD = ($pwdOut | Select-Object -First 1).Trim()

$targets = @(
    'C:\项目\_vault',
    'C:\项目\老田的AI助手\agent_platform\registry',
    'C:\项目\老田的AI助手\agent_platform\catalogs',
    'C:\项目\老田的AI助手\tools\secrets',
    'C:\项目\deliverables',
    'C:\项目\STATUS.md',
    'C:\项目\DOCS_INDEX.md'
)
Log 'START restic backup (prod)'
$out = & $restic backup --tag 'p1-trial' --exclude-caches --json @targets 2>&1
$rc = $LASTEXITCODE
foreach ($line in @($out | Select-Object -Last 3)) { Log ('OUT ' + ([string]$line)) }
Log ('BACKUP rc=' + $rc)

$frc = -1
$prc = -1
$crc = -1
$subset = '5%'
$rrc = -1
if ($rc -eq 0) {
    $isWeekly = ((Get-Date).DayOfWeek -eq [System.DayOfWeek]::Sunday)
    $fout = & $restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --json 2>&1
    $frc = $LASTEXITCODE
    Log ('FORGET rc=' + $frc)

    if ($isWeekly -or $env:RESTIC_PRUNE -eq '1') {
        $prout = & $restic prune --json 2>&1
        $prc = $LASTEXITCODE
        Log ('PRUNE rc=' + $prc)
    }

    if ($isWeekly) { $subset = '10%' }
    $chk = & $restic check --read-data-subset=$subset 2>&1
    $crc = $LASTEXITCODE
    foreach ($line in @($chk | Select-Object -Last 4)) { Log ('CHK ' + ([string]$line)) }
    Log ('CHECK rc=' + $crc + ' subset=' + $subset)

    $receiptOut = & $py -X utf8 $receipt --backup-rc $rc --check-rc $crc --forget-rc $frc --prune-rc $prc --subset $subset --repo $env:RESTIC_REPOSITORY --restic $restic --log $log 2>&1
    $rrc = $LASTEXITCODE
    Log ('RECEIPT rc=' + $rrc + ' ' + ([string]$receiptOut))
} else {
    Log 'FAIL: backup rc != 0'
}

$finalRc = $rc
if ($crc -ne 0) { $finalRc = 1 }
if ($rrc -ne 0) { $finalRc = 1 }
if ($finalRc -ne 0) {
    Log ('ALERT final_rc=' + $finalRc)
    (& $py -X utf8 $alert 'restic_backup' $finalRc $errLog) 1>$null 2>$null
}
$env:RESTIC_PASSWORD = ''
Log ('DONE final_rc=' + $finalRc)
exit $finalRc
