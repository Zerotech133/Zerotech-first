# run_legion_gateway_8795.ps1 — 8795 本地网关常驻包装器
# L2 批准：老田 2026-10-27（「批准 L2：8795 常驻（登录触发 + 失败重启）」）
# 任务：LegionGateway8795（AtLogOn 当前用户；失败重启 3 次/1 分钟；仅绑 127.0.0.1:8795）。
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\agent_platform\gateway\serve.py'
$log = 'C:\Temp\legion_gateway_8795.log'
$health = 'http://127.0.0.1:8795/health'

# 已有实例在服务则直接退出（避免重复绑定；任务 IgnoreNew 之外的兜底）
try {
    $r = Invoke-WebRequest $health -TimeoutSec 2 -UseBasicParsing
    if ($r.StatusCode -eq 200) { exit 0 }
} catch { }

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
& $py -X utf8 $script *>> $log
exit $LASTEXITCODE
