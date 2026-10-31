# Hermes 赵云能力卡定时刷新（48h 阈值；L2 注册任务 HermesCapsRefresh）
# 调用 tools/refresh_hermes_capabilities.py --apply；日志 C:\Temp\hermes_caps_refresh.log
$ErrorActionPreference = "Continue"
$repo = "C:\aios"
$log = "C:\Temp\hermes_caps_refresh.log"
$py = "C:\Program Files\Python311\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Push-Location -LiteralPath $repo
try {
    $out = (& $py -X utf8 tools\refresh_hermes_capabilities.py --apply 2>&1 | Out-String)
    $rc = $LASTEXITCODE
    ($out.TrimEnd()) | Out-File -LiteralPath $log -Append -Encoding utf8
    ("[{0}] refresh_hermes_capabilities rc={1}" -f $stamp, $rc) | Out-File -LiteralPath $log -Append -Encoding utf8
    exit $rc
}
finally {
    Pop-Location
}
