# K263 GubaCrawl daily wrapper (daytime anti-rate-limit windows since 2026-11-12:
# GubaCrawl 09:40 / GubaCrawl_B 10:40 / GubaCrawl_C 14:00; evening 17:50/20:30/22:30
# windows hit eastmoney soft rate-limit -> no_marker storms, see
# deliverables/2026-11-12 GubaCrawl root-cause report (approved combo 1+4)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
# 2026-11-02 multi-window: optional -BatchIndex (default -1 = auto rotation; 0..3 = fixed batch)
# G14-dedup 2026-11-12: same-day alert dedup - only first failing run of the day
# sends the feishu alert; later failures log one line (flag file logs\guba_alert_sent.flag
# holds the yyyyMMdd of last sent day; single file, no accumulation).
# rollback: restore the .bak-20261112-G14 copy in the project backups folder
param(
    [int]$BatchIndex = -1,
    [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\guba_crawl.out'
$err = Join-Path $root 'logs\guba_crawl.err'
$log = Join-Path $root 'logs\guba_crawl_status.log'
$gargs = @('--pool', '--pages', '2', '--limit', '60')
if ($BatchIndex -ge 0) { $gargs += @('--batch-index', "$BatchIndex") }
if ($DryRun) {
    $shown = ($gargs -join ' ')
    Write-Output ("DRYRUN python=" + $py + " guba_crawl.py " + $shown + " batch_index=" + $BatchIndex)
    exit 0
}
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') + " batch_index=" + $BatchIndex) -Encoding ascii
& $py -X utf8 (Join-Path $root 'guba_crawl.py') @gargs 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    # G14-dedup: alert only on first failure of the calendar day (dedupe key =
    # local yyyyMMdd in flag file); repeats within same day -> one log line only.
    # Race hardening: concurrent runs (manual multi-start) both pass the read ->
    # write "already sent" check. Lock file wins: create-exclusive decides the
    # single alerter; loser reads today's date from the winner's write.
    $flag = Join-Path $root 'logs\guba_alert_sent.flag'
    $today = (Get-Date).ToString('yyyyMMdd')
    $sentDay = ''
    if (Test-Path $flag) { $sentDay = (Get-Content $flag -Raw).Trim() }
    if ($sentDay -eq $today) {
        Add-Content -Path $log -Value ("alert suppressed (already sent today " + $today + ")") -Encoding ascii
    } else {
        $lock = Join-Path $root 'logs\guba_alert.lock'
        $got = $false
        try { [void][IO.File]::WriteAllText($lock, $today); $got = $true }
        catch [System.IO.IOException] { $got = $false }  # file in use = loser
        catch { $got = $true }  # other IO errors: prefer alerting over silence
        if (-not $got) {
            Start-Sleep -Seconds 3
            $recheck = ''
            if (Test-Path $flag) { $recheck = (Get-Content $flag -Raw).Trim() }
            if ($recheck -ne $today) {
                & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') guba_crawl $rc $err 1>$null 2>$null
                Set-Content -Path $flag -Value $today -Encoding ascii
                Add-Content -Path $log -Value ("alert sent for " + $today + " (lock lost)") -Encoding ascii
            } else {
                Add-Content -Path $log -Value ("alert suppressed (lock lost, winner sent)") -Encoding ascii
            }
        } else {
            & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') guba_crawl $rc $err 1>$null 2>$null
            Set-Content -Path $flag -Value $today -Encoding ascii
            Add-Content -Path $log -Value ("alert sent for " + $today) -Encoding ascii
        }
    }
}
exit $rc
