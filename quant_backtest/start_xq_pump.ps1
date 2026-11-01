# XueqiuPump wrapper (B1-b: xueqiu_crawl.py -> xq_pump 收口替代, 2026-11-01)
# Three-iron-rule wrapper: python out/err to separate files, ASCII status log, rc!=0 -> alert_fail
# 部署位置: <quant_backtest>\start_xq_pump.ps1 (本仓或 C:\aios\quant_backtest\)
# 计划任务接入需老田五要素批准（见 归档决策文档 B1-b runbook 节）
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$py = 'C:\Program Files\Python311\python.exe'
$out = Join-Path $root 'logs\xq_pump.out'
$err = Join-Path $root 'logs\xq_pump.err'
$log = Join-Path $root 'logs\xq_pump_status.log'
Add-Content -Path $log -Value ("starting " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii

# -- 配置（可被环境变量覆盖）--------------------------------------
# 抽取类型: posts,hot = 与旧任务 --mode all 等价（timeline+hot）；加 comments 可选
$types = if ($env:XQ_PUMP_TYPES) { $env:XQ_PUMP_TYPES } else { 'posts,hot' }
# 轮次与间隔: 沿用 2026-10-29 DSH-Lead 批准的近端密度方案（3 轮 x 240s）
$rounds = if ($env:XQ_PUMP_ROUNDS) { [int]$env:XQ_PUMP_ROUNDS } else { 3 }
# 数据库: 显式指向生产雪球库（xq_pump 表 schema 与 xueqiu_common 对齐, 幂等 upsert 可双写共存）
#   本仓 = data\xueqiu\xueqiu_info.db；C:\aios 部署时改为 C:\aios\quant_backtest\data\xueqiu\xueqiu_info.db
$db = if ($env:XQ_PUMP_DB) { $env:XQ_PUMP_DB } else { Join-Path $root 'data\xueqiu\xueqiu_info.db' }
# 每类型单次新增上限（防失控）
$maxNew = if ($env:XQ_PUMP_MAX_NEW) { [int]$env:XQ_PUMP_MAX_NEW } else { 1000 }
# DRY 自测模式: 置 XQ_PUMP_DRY=1 时不加 --live（FakeSession, 零触网零新增, rc=0）
$dry = if ($env:XQ_PUMP_DRY) { $true } else { $false }

$typeList = $types.Split(',')
$finalRc = 0
Push-Location $root
try {
    for ($i = 1; $i -le $rounds; $i++) {
        foreach ($t in $typeList) {
            $t = $t.Trim()
            if (-not $t) { continue }
            $liveArg = @()
            if (-not $dry) { $liveArg = @('--live') }
            & $py -X utf8 -m xq_pump.cli run --type $t --db $db --max-new $maxNew @liveArg 1>> $out 2>> $err
            $rc = $LASTEXITCODE
            Add-Content -Path $log -Value ("round=$i type=$t rc=$rc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
            if ($rc -ne 0) { $finalRc = $rc }
        }
        if ($i -lt $rounds) { Start-Sleep -Seconds 240 }
    }
} finally {
    Pop-Location
}
Add-Content -Path $log -Value ("exit=$finalRc " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -Encoding ascii
if ($finalRc -ne 0) {
    & $py -X utf8 (Join-Path (Split-Path $root -Parent) 'news_collector\alert_fail_wrap.py') xq_pump $finalRc $err 1>$null 2>$null
}
exit $finalRc
