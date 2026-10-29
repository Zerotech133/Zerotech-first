# 零界 IDE 独立窗口启动器：优先 Edge/Chrome app 模式，无地址栏/标签页
$ErrorActionPreference = "Stop"
$url = "http://127.0.0.1:8734/ide?standalone=1"
$candidates = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) {
  Write-Host "未找到 Edge/Chrome，改用默认浏览器打开：$url"
  Start-Process $url
  exit 0
}
Start-Process -FilePath $exe -ArgumentList @("--app=$url", "--window-size=1600,1000", "--new-window")
Write-Host "已启动独立 IDE 窗口：$url"
