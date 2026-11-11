# factor_daily_task.ps1 - FACTOR1 因子挖掘自动产线（FACTOR1/蒋琬）
# 只做“跑数 + 落日报 + 更新证据”，不广播、不推送、不写策略库。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C://aios'
$python = 'C:\Program Files\Python311\python.exe'
$gate = 'C://aios//tools//scheduled//data_freshness_gate.py'
$log = 'C://Temp//factor_daily_task.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-TaskLog([string]$message) {
    ($stamp + ' ' + $message) | Out-File $log -Append -Encoding utf8
}

try {
    Set-Location $repo

    # 数据未到最新交易日时不生成旧日报（fail-closed），避免“跑成功但数据是隔日”的假证据。
    # 本机包装器/计划任务下 $LASTEXITCODE 会被留空；改为直接校验 gate 的 JSON
    # status，避免“gate 已通过但 rc 读成空”导致误跳过。
    $gateRaw = (& $python -X utf8 $gate --json 2>&1 | Out-String).Trim()
    Write-TaskLog ('freshness_gate ' + $gateRaw)
    if ($gateRaw -notmatch '"status"\s*:\s*"OK"') {
        Write-TaskLog 'factor_daily SKIPPED (freshness gate not OK)'
        exit 2
    }

    $daily = 'C://aios//tools//research//factor_daily.py'
    $out = (& $python -X utf8 $daily --months 12 2>&1 | Out-String)
    $outText = $out.Trim()
    $rc = if ($outText -match 'Traceback|ERROR|Exception') { 1 } else { 0 }
    Write-TaskLog ('factor_daily rc=' + $rc)
    ($outText -split "`r?`n" | Select-Object -Last 8) | ForEach-Object { Write-TaskLog $_ }
    exit $rc
} catch {
    Write-TaskLog ('factor_daily EXCEPTION: ' + $_.Exception.Message)
    exit 1
}

