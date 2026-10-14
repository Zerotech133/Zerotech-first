# Linkage shadow collector daily launcher (ASCII only, junction path)
# Pool = positions.json pending+open CN + news_config watchlist CN.
# Log: C:\Temp\linkage_collect_daily.log (exit line) + _out/_err raw logs.
# exit code semantic: 0=normal / 1=script error (all codes failed)
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\linkage_collect_daily.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\linkage_collect_daily.py' `
        -WorkingDirectory 'C:\aios\quant_backtest' `
        -RedirectStandardOutput 'C:\Temp\linkage_collect_daily_out.log' `
        -RedirectStandardError 'C:\Temp\linkage_collect_daily_err.log' `
        -PassThru -WindowStyle Hidden -Wait
    ("linkage_collect exit=" + $p.ExitCode + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit $p.ExitCode
} catch {
    ('linkage_collect ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
