# K263 GubaCrawl daily wrapper (17:50, after close; pool-mode active stocks)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
# 2026-11-02 multi-window: optional -BatchIndex (default -1 = auto rotation; 0..3 = fixed batch)
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
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') guba_crawl $rc $err 1>$null 2>$null
}
exit $rc
