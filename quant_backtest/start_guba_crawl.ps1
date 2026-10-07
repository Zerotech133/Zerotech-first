# K263 GubaCrawl daily wrapper (17:50, after close; pool-mode active stocks)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\guba_crawl.out'
$err = Join-Path $root 'logs\guba_crawl.err'
$log = Join-Path $root 'logs\guba_crawl_status.log'
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
& $py -X utf8 (Join-Path $root 'guba_crawl.py') --pool --pages 2 --limit 60 1> $out 2> $err
$rc = $LASTEXITCODE
Add-Content -Path $log -Value ("exit=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($rc -ne 0) {
    & $py -X utf8 (Join-Path $root 'alert_fail.py') guba_crawl $rc $err 1>$null 2>$null
}
exit $rc
