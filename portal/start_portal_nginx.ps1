# ai-portal 反代容器启动脚本（挂载源钉死 C:\项目\老田的AI助手\portal）
# 用法：powershell -ExecutionPolicy Bypass -File start_portal_nginx.ps1
# 行为：等引擎就绪 -> 幂等重建容器 -> nginx -t 自检 -> 打印状态与 resolver 核对
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandUseErrorActionPreference = $false }

$Name   = "ai-portal"
$Portal = "C:\项目\老田的AI助手\portal"   # 挂载源唯一事实源（换机恢复：E: -> C:\项目）
$Image  = "nginx:alpine"
$Port   = "8080:80"

Write-Host "[1/4] 等待 Docker 引擎就绪…" -ForegroundColor Cyan
for ($i = 1; $i -le 30; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "      引擎就绪" -ForegroundColor Green; break }
    if ($i -eq 30) { throw "Docker 引擎 60s 内未就绪，请先启动 Docker Desktop" }
    Start-Sleep -Seconds 2
}

Write-Host "[2/4] 重建容器 $Name（挂载源：$Portal）…" -ForegroundColor Cyan
$exists = docker ps -a --format "{{.Names}}" | Select-String -SimpleMatch $Name
if ($exists) { docker rm -f $Name *> $null }
docker run -d --name $Name --restart unless-stopped -p $Port `
    -v "${Portal}:/usr/share/nginx/html:ro" `
    -v "${Portal}\nginx.conf:/etc/nginx/conf.d/default.conf:ro" `
    $Image | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker run 失败（退出码 $LASTEXITCODE）" }
Start-Sleep -Seconds 2

Write-Host "[3/4] nginx 配置自检…" -ForegroundColor Cyan
docker exec $Name nginx -t
if ($LASTEXITCODE -ne 0) { throw "nginx -t 未通过，请检查 nginx.conf" }

Write-Host "[4/4] 状态与 resolver 核对…" -ForegroundColor Cyan
docker ps --filter "name=$Name" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
$ns = (docker exec $Name cat /etc/resolv.conf | Select-String '^\s*nameserver' | Select-Object -First 1).ToString().Trim().Split()[1]
$rs = [regex]::Match((Get-Content "${Portal}\nginx.conf" -Raw), 'resolver\s+(\d+\.\d+\.\d+\.\d+)').Groups[1].Value
"resolver(nginx.conf) = $rs"
"nameserver(容器)      = $ns"
if ($ns -eq $rs) { "✅ 匹配 —— DNS 反代无隐患" }
else { "❌ 不匹配 —— 请把 nginx.conf 的 resolver 改为 $ns 后 docker restart $Name" }
Write-Host "`n门户地址： http://localhost:8080/" -ForegroundColor Green
