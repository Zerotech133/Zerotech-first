# test_loop.ps1 - 关羽线持续用例测试循环（2026-10-28，P4）
#
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_loop.ps1 -SuiteSet fast -Rounds 2 -IntervalSec 30
#   -SuiteSet: fast(portal+k_stock) | portal | kstock | agent | all
# 产物: _reviews_20261026/test_loop_<ts>.jsonl（每轮每套房 rc/新增失败/日志路径）
# 事件: 启动发 task_start；出现新增失败发 guard_fail（DEVTOOL_AGENT=GUANYU）
# 退出码: 0=全部轮次无新增失败；1=有新增失败
param(
    [ValidateSet('fast', 'portal', 'kstock', 'agent', 'all')]
    [string]$SuiteSet = 'fast',
    [int]$Rounds = 1,
    [int]$IntervalSec = 30,
    [switch]$StopOnNewFail
)
$ErrorActionPreference = 'Continue'
$repo = 'C:\项目\老田的AI助手'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$outDir = Join-Path $repo '_reviews_20261026'
if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$jsonl = Join-Path $outDir ("test_loop_{0}.jsonl" -f $ts)
$env:DEVTOOL_AGENT = 'GUANYU'
$env:DEVTOOL_IDE = 'DSH-GUANYU'
$env:DEVTOOL_MODEL = 'deepseek-flash'
$taskId = 'TEST_LOOP_' + $ts
$eventsPy = Join-Path $repo 'tools\events.py'
$gate = Join-Path $repo 'tools\test_gate.ps1'

& python $eventsPy emit --kind task_start --task $taskId --agent GUANYU --ide DSH-GUANYU --model deepseek-flash --detail ("持续用例测试 SuiteSet=$SuiteSet Rounds=$Rounds") | Out-Null

$suites = switch ($SuiteSet) {
    'fast'   { @('portal', 'kstock') }
    'portal' { @('portal') }
    'kstock' { @('kstock') }
    'agent'  { @('agent') }
    'all'    { @('portal', 'kstock', 'agent') }
}

$totalNew = 0
$stop = $false
for ($r = 1; $r -le $Rounds -and -not $stop; $r++) {
    foreach ($s in $suites) {
        $log = Join-Path $env:TEMP ("test_loop_{0}_r{1}_{2}.log" -f $s, $r, $ts)
        Write-Host ("[round {0}/{1}] suite={2} ..." -f $r, $Rounds, $s)
        & powershell -NoProfile -ExecutionPolicy Bypass -File $gate -Suite $s *> $log
        $rc = $LASTEXITCODE
        $new = 0
        $txt = @()
        if (Test-Path -LiteralPath $log) { $txt = @(Get-Content -LiteralPath $log -Encoding Unicode -ErrorAction SilentlyContinue) }
        foreach ($line in $txt) {
            $m = [regex]::Match($line, '新增失败\s+(\d+)\s+项')
            if ($m.Success) { $new = [int]$m.Groups[1].Value }
        }
        $totalNew += $new
        $rec = [pscustomobject]@{
            ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'); round = $r; suite = $s
            rc = $rc; new_failures = $new; log = $log
        }
        Add-Content -LiteralPath $jsonl -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8
        Write-Host ("  -> rc={0} 新增失败={1}" -f $rc, $new)
        if ($new -gt 0) {
            & python $eventsPy emit --kind guard_fail --task $taskId --agent GUANYU --ide DSH-GUANYU --model deepseek-flash --detail ("test_loop suite=$s round=$r 新增失败=$new") | Out-Null
            if ($StopOnNewFail) { $stop = $true; break }
        }
        if ($r -lt $Rounds) { Start-Sleep -Seconds $IntervalSec }
    }
}
Write-Host ''
Write-Host ("===== TEST LOOP DONE: rounds={0} suites={1} total_new_failures={2} =====" -f $Rounds, ($suites -join ','), $totalNew)
Write-Host ("JSONL: {0}" -f $jsonl)
if ($totalNew -eq 0) { exit 0 } else { exit 1 }
