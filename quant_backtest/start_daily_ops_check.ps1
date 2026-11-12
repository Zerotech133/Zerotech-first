# daily_ops_check wrapper (05:30 morning inspector)
# Three-domain check (data-sync / intel-collect / quant-research) + alert audit.
# Report -> feishu (unified to owner, domain-tagged, P0 flag); all-green = silent.
# Iron rules: out/err split, ASCII status log, rc!=0 -> alert_fail_wrap + same-day
# dedup flag (G14 pattern). rollback: unregister task + delete two files.
param([switch]$DryRun)
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\daily_ops_check.out'
$err = Join-Path $root 'logs\daily_ops_check.err'
$log = Join-Path $root 'logs\daily_ops_check_status.log'
if ($DryRun) { Write-Output "DRYRUN python daily_ops_check.py"; exit 0 }
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'daily_ops_check.py') 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    $flag = Join-Path $root 'logs\daily_ops_alert.flag'
    $today = (Get-Date).ToString('yyyyMMdd')
    $sentDay = ''
    if (Test-Path $flag) { $sentDay = (Get-Content $flag -Raw).Trim() }
    if ($sentDay -eq $today) {
        Add-Content -Path $log -Value ("alert suppressed (already sent today " + $today + ")") -Encoding ascii
    } else {
        & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') daily_ops_check $rc $err 1>$null 2>$null
        Set-Content -Path $flag -Value $today -Encoding ascii
        Add-Content -Path $log -Value ("alert sent for " + $today) -Encoding ascii
    }
}
exit $rc
