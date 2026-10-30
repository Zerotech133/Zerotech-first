# test_gate.ps1 - 测试门禁脚本（GY-01，2026-10-27）
#
# 用法（仓库任意目录）:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast
# -Suite: fast(portal+k_stock) | portal | kstock | agent | quant | tools | all
# 退出码: 0=无新增失败(允许既存失败) | 1=有新增失败/收集错误 | 2=脚本错误
# 产物:  C:\Temp\test_gate_<suite>_<ts>.log
#
# 判据: 本次 FAILED/ERROR 减去 tools/test_baseline.json 的 known_failures（tools 套件供夜间/手动全量，不进 fast）
#       得到"新增失败"; 新增=0 才放行。
param(
    [ValidateSet('fast', 'portal', 'kstock', 'ide', 'agent', 'quant', 'tools', 'all')]
    [string]$Suite = 'fast',
    [int]$PytestTimeout = 120
)
$ErrorActionPreference = 'Continue'
$origEAP = $ErrorActionPreference
$baselineBroken = $false
if ($PSScriptRoot) { $repo = Split-Path -Parent $PSScriptRoot } else { $repo = 'C:\项目\老田的AI助手' }
$baselinePath = Join-Path $repo 'tools\test_baseline.json'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'

$known = @{}
$envKnown = @{}
$flaky = @{}
$inWorktree = ($repo -match '[\\/]\.worktrees[\\/]')
if (Test-Path -LiteralPath $baselinePath) {
    try {
        $b = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $b.PSObject.Properties) {
            $known[$prop.Name] = @($prop.Value.known_failures)
            if ($prop.Value.env_failures) { $envKnown[$prop.Name] = @($prop.Value.env_failures) }
            if ($prop.Value.flaky) { $flaky[$prop.Name] = @($prop.Value.flaky) }
        }
    } catch {
        Write-Host "[ERR] baseline 解析失败: $($_.Exception.Message)" -ForegroundColor Red
        $baselineBroken = $true
    }
}

$suites = @()
switch ($Suite) {
    # 2026-10-30 P1：LDE HTTP 契约进 fast 门（单独文件，不拖入整个 agent 套件）
    'fast'   { $suites = @('portal', 'kstock', 'ide') }
    'portal' { $suites = @('portal') }
    'kstock' { $suites = @('kstock') }
    'ide'    { $suites = @('ide') }
    'agent'  { $suites = @('agent') }
    'quant'  { $suites = @('quant') }
    'tools'  { $suites = @('tools') }
    'all'    { $suites = @('portal', 'kstock', 'agent', 'quant', 'ide') }
    default  { Write-Host "[ERR] 未知 Suite: $Suite"; exit 2 }
}
$paths = @{
    'portal' = 'portal/tests'; 'kstock' = 'k_stock/tests'
    'agent'  = 'agent_platform/tests'; 'quant' = 'quant_backtest/tests'
    'tools'  = 'tools/tests'
    'ide'    = 'agent_platform/tests/test_legion_ide_http.py'
}

$newFailures = 0
$summary = @()
Push-Location -LiteralPath $repo
try {
    foreach ($s in $suites) {
        $name = $s
        $path = $paths[$name]
        $log = Join-Path $env:TEMP "test_gate_${name}_$ts.log"
        Write-Host "===== [$name] $path ====="
        python -m pytest $path -q --timeout=$PytestTimeout -p no:cacheprovider *> $log
        $rc = $LASTEXITCODE
        $rcBad = ($rc -ne 0 -and $rc -ne 1)
        if ($rcBad) {
            Write-Host "[$name] pytest 异常退出 rc=$rc（未收集/用法/内部错误，按失败处理）" -ForegroundColor Red
        }
        $lines = @()
        if (Test-Path -LiteralPath $log) { $lines = @(Get-Content -LiteralPath $log -Encoding UTF8) }
        $failed = @()
        if ($rcBad) { $failed += "PYTEST_ABNORMAL_RC_$rc" }
        foreach ($line in $lines) {
            if ($line -match '^(FAILED|ERROR) ') {
                $f = $line -replace '^(FAILED|ERROR) ', ''
                $f = $f -replace ' - .*$', ''
                $failed += $f.Trim()
            }
        }
        $knownList = @($known[$path])
        # 2026-10-28：env_failures 不再仅限 worktree——数据/环境依赖（如行情湖未更新到
        # 当日、worktree 无数据根）在主仓同样成立；仍单独打印，不掩盖真实新增失败。
        if ($envKnown.ContainsKey($path)) { $knownList = @($knownList) + @($envKnown[$path]) }
        $flakyList = @($flaky[$path])
        if ($flakyList.Count -gt 0) { $knownList = @($knownList) + @($flakyList) }
        $new = @()
        foreach ($f in $failed) { if ($knownList -notcontains $f) { $new += $f } }
        $newFailures += $new.Count
        $tail = ''
        if ($lines.Count -gt 0) { $tail = $lines[$lines.Count - 1] }
        $summary += [pscustomobject]@{
            Suite = $name; Rc = $rc; Failed = $failed.Count; Known = $knownList.Count; New = $new.Count; Log = $log
        }
        if ($new.Count -gt 0) {
            Write-Host "[$name] 新增失败 $($new.Count) 项:" -ForegroundColor Red
            $new | Select-Object -First 20 | ForEach-Object { Write-Host "  - $_" }
        } else {
            Write-Host "[$name] 无新增失败(既存 $($failed.Count) 项已登记)" -ForegroundColor Green
        }
        if ($tail) { Write-Host "  $tail" }
    }
} finally {
    Pop-Location
    $ErrorActionPreference = $origEAP
}

Write-Host ''
Write-Host '===== 汇总 ====='
$summary | Format-Table -AutoSize
if ($flaky.Count -gt 0) {
    Write-Host '已知 flaky(不计入新增失败, 需跟踪):'
    foreach ($k in $flaky.Keys) { foreach ($f in $flaky[$k]) { Write-Host "  - [$k] $f" } }
}
if ($baselineBroken) {
    Write-Host 'GATE FAIL: baseline 解析失败（fail-closed，不按已知失败放行）' -ForegroundColor Red
    exit 1
}
if ($newFailures -eq 0) {
    Write-Host 'GATE PASS: 无新增失败' -ForegroundColor Green
    exit 0
} else {
    Write-Host "GATE FAIL: 新增失败 $newFailures 项" -ForegroundColor Red
    exit 1
}

# OCR warn-gate validation marker 2026-10-27
