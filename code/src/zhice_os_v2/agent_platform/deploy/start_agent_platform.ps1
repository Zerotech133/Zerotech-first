# agent_platform 幂等拉起脚本（T05，复刻 start_jev_stack.ps1 模式）
# 用途：watch-daemon 常驻 + ack-serve 常驻，缺哪个拉哪个，已在跑的跳过。
# 计划任务注册（管理员 PowerShell 一次性执行）：
#   schtasks /Create /TN AgentWatchdog /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"`%LOCALAPPDATA%\Programs\agent_platform\start_agent_platform.ps1`"" /SC MINUTE /MO 5 /F
# （本脚本不自行注册；OllamaWatchdog 的 zhice_patrol 亦可顺带拉起本脚本）
$ErrorActionPreference = 'Continue'

# ★代理变量归一（2026-10-18）：宿主/父进程会注入小写 http_proxy/https_proxy，
#   PS5.1 Start-Process 内部环境字典按大写规范名预置后加小写键 →
#   「已添加项。字典中的关键字:HTTP_PROXY」异常，daemon 拉起全灭且 exit 0 掩盖。
#   处置：删掉全部大小写变体，以大写规范名设回（值保留，代理语义不变）。
$hp = $env:http_proxy;  if (-not $hp) { $hp = $env:HTTP_PROXY }
$hs = $env:https_proxy; if (-not $hs) { $hs = $env:HTTPS_PROXY }
Remove-Item Env:http_proxy  -ErrorAction SilentlyContinue
Remove-Item Env:HTTP_PROXY  -ErrorAction SilentlyContinue
Remove-Item Env:https_proxy -ErrorAction SilentlyContinue
Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
if ($hp) { $env:HTTP_PROXY = $hp }
if ($hs) { $env:HTTPS_PROXY = $hs }

$Py  = 'C:\项目\py312\python.exe'
$Code = 'C:\项目\zhice-os-v2-windows-20260920\code'
$LogDir = Join-Path $Code 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$ProcLog = Join-Path $LogDir 'aw-proc'

function Get-AwProcs {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'agent_platform\.cli (watch-daemon|ack-serve)' }
}

$started = @()
$failed  = @()
if (-not (Get-AwProcs | Where-Object { $_.CommandLine -match 'watch-daemon' })) {
  $p = Start-Process $Py -ArgumentList '-m','agent_platform.cli','watch-daemon','--market','CRYPTO' `
    -WorkingDirectory $Code -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$ProcLog.watch.out" -RedirectStandardError "$ProcLog.watch.err"
  if ($p) { $started += 'watch-daemon' } else { $failed += 'watch-daemon' }
}
if (-not (Get-AwProcs | Where-Object { $_.CommandLine -match 'ack-serve' })) {
  $p = Start-Process $Py -ArgumentList '-m','agent_platform.cli','ack-serve' `
    -WorkingDirectory $Code -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$ProcLog.ack.out" -RedirectStandardError "$ProcLog.ack.err"
  if ($p) { $started += 'ack-serve' } else { $failed += 'ack-serve' }
}
if ($started.Count -gt 0) {
  ("[{0}] pulled up: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), ($started -join ',')) |
    Out-File -FilePath (Join-Path $LogDir 'aw-stack.log') -Append -Encoding utf8
}
if ($failed.Count -gt 0) {
  ("[{0}] FAILED: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), ($failed -join ',')) |
    Out-File -FilePath (Join-Path $LogDir 'aw-stack.log') -Append -Encoding utf8
  exit 1   # 拉起失败必须让 LastTaskResult 可见（防 exit 0 假绿）
}
exit 0
