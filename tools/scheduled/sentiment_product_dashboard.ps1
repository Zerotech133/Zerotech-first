$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = 'C:\Program Files\Python311\python.exe'
$script = Join-Path $repo 'tools\ops\sentiment_product_dashboard.py'
$log = Join-Path $env:TEMP 'sentiment_product_dashboard.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"=== $stamp ===" | Out-File -FilePath $log -Append -Encoding utf8
& $py -X utf8 $script --render *>> $log
exit $LASTEXITCODE
