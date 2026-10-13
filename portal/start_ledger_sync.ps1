# ledger sync daily launcher (ASCII only, junction path) - K-STOCK-353
# 16:15 runs after 16:00 positions ledger update; output appended to log (long-run rule).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$py = "python"
$args_ = "-X utf8 C:\aios\portal\sync_ledger.py"
$log = "C:\aios\portal\logs\ledger_sync.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] LedgerSync start" | Add-Content $log
$p = Start-Process -FilePath $py -ArgumentList $args_ -NoNewWindow -PassThru -Wait `
     -RedirectStandardOutput "$env:TEMP\ledger_out.txt" -RedirectStandardError "$env:TEMP\ledger_err.txt"
Get-Content "$env:TEMP\ledger_out.txt" | Add-Content $log
Get-Content "$env:TEMP\ledger_err.txt" | Add-Content $log
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] LedgerSync rc=$($p.ExitCode)" | Add-Content $log
