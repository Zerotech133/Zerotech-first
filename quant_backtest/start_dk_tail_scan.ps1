# dk_tail_scan wrapper (tail-session double-knife scan + watch update)
# modes: 14:55 --live tail scan (sina snapshot) / 17:45 --watch forward-return update
# iron rules: out/err split, ASCII status log, rc!=0 -> alert_fail_wrap (dedup via
# same-day flag logs\dk_tail_alert.flag, mirrors G14 pattern)
# NOTE: R1-R8 verdict stands - daily double-knife has no proven alpha; this is an
# execution-mode observation tool, NOT a trading recommendation.
# rollback: delete task + this file; watchlist stays at data\dk_tail_watch\
param(
    [string]$Mode = "live",   # live | watch | scan
    [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\dk_tail_scan.out'
$err = Join-Path $root 'logs\dk_tail_scan.err'
$log = Join-Path $root 'logs\dk_tail_scan_status.log'
$margs = @()
if ($Mode -eq 'live') { $margs = @('--live') }
elseif ($Mode -eq 'watch') { $margs = @('--watch') }
else { $margs = @('--scan') }
if ($DryRun) {
    Write-Output ("DRYRUN python dk_tail_scan.py " + ($margs -join ' '))
    exit 0
}
Add-Content -Path $log -Value ("starting mode=" + $Mode + " " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'dk_tail_scan.py') @margs 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    $flag = Join-Path $root 'logs\dk_tail_alert.flag'
    $today = (Get-Date).ToString('yyyyMMdd')
    $sentDay = ''
    if (Test-Path $flag) { $sentDay = (Get-Content $flag -Raw).Trim() }
    if ($sentDay -eq $today) {
        Add-Content -Path $log -Value ("alert suppressed (already sent today " + $today + ")") -Encoding ascii
    } else {
        & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') dk_tail_scan $rc $err 1>$null 2>$null
        Set-Content -Path $flag -Value $today -Encoding ascii
        Add-Content -Path $log -Value ("alert sent for " + $today) -Encoding ascii
    }
}
exit $rc
