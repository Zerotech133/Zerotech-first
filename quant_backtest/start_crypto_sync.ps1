# crypto_daily daily export launcher (ASCII only, junction path)
# WO-QT-20261021-094: crypto_sync.py (direct Binance) is intentionally disabled.
# CRYPTO bars now come from the unified supply layer zhice-db v2_bar_daily,
# exported by export_us_hk.py --market CRYPTO -> data/crypto/*.parquet.
# 2026-11-05: rewired this wrapper. Enabling the old task as-is produced a
# no-op green run (rc=0, "CRYPTO SYNC DISABLED") while data stayed frozen.
# Revert: copy start_crypto_sync.ps1.bak-20261105-crypto-export back.
$log = 'C:\Temp\crypto_sync_task.log'
('wrapper entered ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = '1'
try {
    Push-Location 'C:\aios\quant_backtest'
    & 'C:\Program Files\Python311\python.exe' -X utf8 -u C:\aios\quant_backtest\export_us_hk.py --market CRYPTO --batch 200 `
        1> C:\Temp\crypto_export_out.log 2> C:\Temp\crypto_export_err.log
    $rc = $LASTEXITCODE
    Pop-Location
    ('crypto_export(CRYPTO) exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py crypto_sync $rc C:\Temp\crypto_export_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    ('crypto_export(CRYPTO) ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py crypto_sync 9 $log 1>$null 2>$null
    exit 1
}
