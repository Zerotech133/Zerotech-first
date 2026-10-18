# VERSION: czsc_shadow_task v1 (2026-10-19, K-ORG-202 CZSC 影子旁路调度包装器)
# CzscShadowDaily 19:55：影子落账编排（主环境跑编排器，venv 子进程做 CZSC 计算）。
# 时序依据：ChanlunLearnDaily 19:40 实测 36s 完成（L1 当日 ledger 是名单来源），
# 本任务 19:55 落依赖就绪空窗；ledger mtime 非当日时守卫自动 skip（降级不红）。
# 影子铁律：czsc_ledger.jsonl 仅观察旁证，永不进 verdict/L3/L4，不产生交易指令。
$ErrorActionPreference = 'Stop'
$log = Join-Path $PSScriptRoot 'data\chanlun_learn\czsc_task.log'
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
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $PSScriptRoot 'czsc_shadow.py') 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P1 exit=' + $code + ' out=' + ($out -join ' | '))
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
