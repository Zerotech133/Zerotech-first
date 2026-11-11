# daily_screening_task.ps1 - ZeroBound daily screening pipeline
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'
$repo = 'C://aios'
$python = 'C:\Program Files\Python311\python.exe'
$gate = 'C://aios//tools//scheduled//data_freshness_gate.py'
$log = 'C://Temp//zero_daily_screening.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
try {
    Set-Location $repo
    # 2026-11-07 S2：daily_screening 取 latest_date()，而日线由 increment_guard 每日
    # 20:30 才落盘。不先校验 -> rc=0 地产出隔日数据（11-06 实证 date=20261105）。
    # 闸门 fail-closed：滞后 rc=2、无法校验 rc=3 时都不跑，宁可没有报告也不发旧数据。
    $g = & $python -X utf8 $gate --json 2>&1
    $grc = $LASTEXITCODE
    ($stamp + ' freshness_gate rc=' + $grc + ' ' + ($g -join ' ')) | Out-File $log -Append -Encoding utf8
    if ($grc -ne 0) {
        ($stamp + ' daily_screening SKIPPED rc=' + $grc + ' (data not fresh; refuse to emit stale report)') | Out-File $log -Append -Encoding utf8
        exit $grc
    }
    $out = & $python -X utf8 tools/research/daily_screening.py --rs-mode off 2>&1
    $rc = $LASTEXITCODE
    ($stamp + ' daily_screening rc=' + $rc) | Out-File $log -Append -Encoding utf8
    ($out | Select-Object -Last 8) | Out-File $log -Append -Encoding utf8
    exit $rc
} catch {
    ($stamp + ' daily_screening EXCEPTION: ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}

