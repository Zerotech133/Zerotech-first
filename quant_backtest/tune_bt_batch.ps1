﻿# K-PUSH-011 tune_bt daily batch launcher (ASCII only, junction path)
$log = 'C:\Temp\tune_bt_batch.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList '-X','utf8','C:\aios\quant_backtest\bt2_tune_batch.py' `
        -RedirectStandardOutput 'C:\Temp\tune_bt_batch_out.log' `
        -RedirectStandardError 'C:\Temp\tune_bt_batch_err.log' `
        -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $p.WaitForExit()
    $rc = $p.ExitCode
    if ($null -eq $rc) { $rc = 1 }
    ('tune_bt exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ('tune_bt ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
