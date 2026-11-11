# houfeng_snap_task.ps1 - 侯风台账盘后快照 + 结算（周一至周五 15:10）
#
# 为什么是 15:10：
#   cohort_track.snap() 用实时行情接口取「现价 / 今开 / 昨收」，15:00 收盘后接口返回的
#   现价即收盘价，15:10 足够稳定，不必等 tushare 日线（那要 17:00 后）。
#   _market_closed() 的阈值就是 15:00，15:10 跑才能让当日到期项真正结算；
#   盘中跑（如今天 13:05）只出快照、不结算，所以台账至今只有 6 条结算样本。
#
# 顺序：先 --settle 再 --snap。snap 会把 rolling_alpha() 写进 status_latest.json，
# 先结算才能让滚动降级线读到最新样本。
#
# 幂等：settle 只结未结项；snap 覆盖写 status_latest.json。重复触发无副作用。
param([switch]$WhatIf)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'

$py  = 'C:\Program Files\Python311\python.exe'
$wd  = 'C:\项目\老田的AI助手\portal\daily_report'
$log = 'C:\Temp\houfeng_snap_task.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-TaskLog([string]$m) {
    (($stamp + ' ' + $m)) | Out-File $log -Append -Encoding utf8
}

try {
    if ($WhatIf) {
        Write-Output ('将执行: ' + $py + ' -X utf8 cohort_track.py --settle')
        Write-Output ('将执行: ' + $py + ' -X utf8 cohort_track.py --snap')
        Write-Output ('工作目录: ' + $wd)
        Write-Output ('日志: ' + $log)
        exit 0
    }

    Set-Location $wd
    Write-TaskLog '===== houfeng_snap start ====='

    # 1) 结算：只结到期未结项，当日到期需 15:00 之后
    $setOut = (& $py -X utf8 'cohort_track.py' --settle 2>&1 | Out-String)
    ($setOut.Trim() -split "`r?`n" | Select-Object -Last 12) | ForEach-Object { Write-TaskLog ('  settle| ' + $_) }
    if ($setOut -match 'Traceback') {
        # 结算失败不阻断快照：快照本身仍有价值
        Write-TaskLog 'WARN: settle 抛异常，继续跑 snap'
    }

    # 2) 快照：全市场实时 -> status_latest.json（含 rolling 降级线）
    $snapOut = (& $py -X utf8 'cohort_track.py' --snap 2>&1 | Out-String)
    ($snapOut.Trim() -split "`r?`n" | Select-Object -Last 12) | ForEach-Object { Write-TaskLog ('  snap| ' + $_) }
    if ($snapOut -match 'Traceback') {
        Write-TaskLog 'ABORT: snap 抛异常'
        exit 1
    }

    Write-TaskLog '===== houfeng_snap done ====='
    exit 0
} catch {
    Write-TaskLog ('EXCEPTION: ' + $_.Exception.Message)
    exit 1
}
