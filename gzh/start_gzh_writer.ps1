# gzh writer launcher (ASCII only, junction path)
$log = 'C:\Temp\gzh_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\gzh\gzh_writer.py' `
        -RedirectStandardOutput 'C:\Temp\gzh_out.log' `
        -RedirectStandardError 'C:\Temp\gzh_err.log' `
        -PassThru -WindowStyle Hidden
    $p.WaitForExit()
    $rc = $p.ExitCode
    ('gzh exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py gzh_writer $rc C:\Temp\gzh_err.log 1>$null 2>$null
    }
    exit $rc
} catch {
    ('gzh ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
