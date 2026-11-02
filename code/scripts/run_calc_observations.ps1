# 观察计算计划任务包装（L2 已批）
$ErrorActionPreference = 'Continue'
$code = 'C:\项目\zhice-os-v2-windows-20260920\code'
$env:PYTHONPATH = 'src'
Set-Location $code
$log = 'C:\Temp\zhice_obs_calc.log'
try {
    & (Join-Path $code '.venv\Scripts\python.exe') (Join-Path $code 'scripts\calc_observations.py') *>> $log
    $rc = $LASTEXITCODE
    ('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ('ERR ' + $_.Exception.Message + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 1
}
