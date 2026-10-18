# VERSION: chanlun_learn_task v1 (2026-10-18, K-ORG-192 学习环每日调度包装器)
# ChanlunLearnDaily 19:40：缠论专属学习环 L1-L4 全链幂等跑。
# 时序依据：17:10 AgentShadowDaily 产出 daily_ledger 当日 action（L1 依赖），
# 行情 parquet 早于 17:10 已入库（L2 依赖），19:10 RiskGuard 同源数据先例——
# 本任务 19:40 落依赖全就绪空窗。全链幂等（同键同值零副作用），重跑安全。
$ErrorActionPreference = 'Stop'
# 运行态与账本均在仓内 data/（脚本自解析 BASE，junction C:\aios 同物理仓）
$log = Join-Path $PSScriptRoot 'data\chanlun_learn\task.log'
if (-not (Test-Path -LiteralPath (Split-Path $log))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
}
function W([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}
try {
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
    W 'P0 start'
    # python resolution (house rule, same as jev_drift_task.ps1)
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    # all = L1 观察 → L2 对账 → L3 归因 → L4 草案+thesis（影子铁律只读行情，
    # 只写记忆/草案/账本；同键异值冲突 exit 2 亮牌）
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $PSScriptRoot 'chanlun_learn.py') all 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P1 exit=' + $code + ' out=' + ($out -join ' | '))
    # log cap ~400KB: keep tail
    if ((Get-Item -LiteralPath $log).Length -gt 400KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
        W 'P2 log rotated'
    }
    exit $code
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
