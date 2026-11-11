# houfeng_daily_task.ps1 - 侯风每日信号产线（pull -> select -> register）
#
# 为什么放在早上而不是盘后：
#   tushare margin_detail 沪市 T+0、深市 T+1。当天 18:10 跑，深市两融必然为空，
#   select_asof 的覆盖闸门（asof 日非空数 / 近5日均 >= 80%）必然拒绝。
#   实测 11-10 23:01 跑 -> 47.5% 全 SH 被拒；11-11 早上跑 -> SH 2018 / SZ 2119 / BJ 352 通过。
#
# 三个触发点（08:40 / 09:40 / 10:40）互为重试：
#   深市两融具体几点齐没有历史日志可查，所以不赌单一时点。
#   幂等守卫：当日两个 selection 文件都已存在则跳过选股，只补登记，重复触发无副作用。
#
# 不做的事：不跑快照（开盘前快照无意义）、不广播、不改任何策略参数。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'

$py     = 'C:\Program Files\Python311\python.exe'
$fp     = 'C:\项目\zerobound-kg\factor_proto'
$dailyDir = 'C:\项目\老田的AI助手\quant_backtest\data\tushare\daily'
$log    = 'C:\Temp\houfeng_daily_task.log'
$stamp  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-TaskLog([string]$m) {
    (($stamp + ' ' + $m)) | Out-File $log -Append -Encoding utf8
}

function Get-AsOf {
    # 以主工程 daily 的末片日期为目标日：末片必然是一个已收盘交易日
    $parts = Get-ChildItem $dailyDir -Filter 'd_*.parquet' -ErrorAction Stop |
             Sort-Object Name
    if (-not $parts -or $parts.Count -lt 1) { throw 'daily 目录没有分片' }
    $last = $parts[-1].BaseName -replace '^d_', ''
    $today = Get-Date -Format 'yyyyMMdd'
    if ($last -eq $today -and $parts.Count -ge 2) {
        # 末片是今天 => 今天数据可能不完整，退回上一个交易日
        $last = $parts[-2].BaseName -replace '^d_', ''
    }
    return $last
}

try {
    Set-Location $fp
    Write-TaskLog '===== houfeng_daily start ====='

    # token 没有 .env 兜底（pull_daily_delta.py:40 只读环境变量），缺失时给明确原因
    if (-not $env:TUSHARE_MCP_TOKEN) {
        Write-TaskLog 'ABORT: 环境变量 TUSHARE_MCP_TOKEN 为空。该变量是 User 级，计划任务须以当前登录用户运行。'
        exit 5
    }

    $asof = Get-AsOf
    Write-TaskLog ('target asof=' + $asof)

    # 陈旧守卫：目标日离今天超过 5 个自然日说明上游断了，跑出来的信号没有意义
    $dAsOf  = [datetime]::ParseExact($asof, 'yyyyMMdd', $null)
    $lagDay = ((Get-Date).Date - $dAsOf.Date).Days
    if ($lagDay -gt 5) {
        Write-TaskLog ('ABORT: asof 落后 ' + $lagDay + ' 个自然日，上游数据可能中断，不跑')
        exit 4
    }

    $selMain = Join-Path $fp ('selection_' + $asof + '.json')
    $selAuto = Join-Path $fp ('selection_' + $asof + '_auto.json')
    $haveBoth = (Test-Path $selMain) -and (Test-Path $selAuto)

    if ($haveBoth) {
        Write-TaskLog ('SKIP select: ' + $asof + ' 的两个 selection 文件已存在')
    } else {
        # 1) 拉增量（只补目标日，靠去重覆盖，不删不改历史）
        $pullOut = (& $py -X utf8 'pull_daily_delta.py' --asof $asof --start $asof 2>&1 | Out-String)
        ($pullOut.Trim() -split "`r?`n" | Select-Object -Last 6) | ForEach-Object { Write-TaskLog ('  pull| ' + $_) }
        if ($pullOut -match 'Traceback') {
            Write-TaskLog 'ABORT: pull_daily_delta 抛异常'
            exit 1
        }

        # 2) 选股：auto 池（晋升池，含情绪因子 -> 两融闸门生效）
        $env:ZKG_POOL = 'auto'
        $env:ZKG_ASOF = 'auto'
        $env:ZKG_PANEL_CACHE = (Join-Path $fp '.panel_cache')
        $autoOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String)
        ($autoOut.Trim() -split "`r?`n" | Select-Object -Last 8) | ForEach-Object { Write-TaskLog ('  auto| ' + $_) }

        if ($autoOut -match '\[FATAL\]') {
            # 闸门拒绝（通常=深市两融尚未发布）-> 留给下一个触发点重试
            Write-TaskLog 'RETRY-LATER: auto 池被闸门拒绝，等下一个触发点'
            exit 3
        }

        # 3) 选股：main 池（A1 闸门池）
        Remove-Item Env:ZKG_POOL -ErrorAction SilentlyContinue
        $mainOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String)
        ($mainOut.Trim() -split "`r?`n" | Select-Object -Last 8) | ForEach-Object { Write-TaskLog ('  main| ' + $_) }
        if ($mainOut -match '\[FATAL\]') {
            Write-TaskLog 'RETRY-LATER: main 池被闸门拒绝'
            exit 3
        }
    }

    # 4) 登记新批次（幂等：已登记的不会重复写）
    $regOut = (& $py -X utf8 (Join-Path 'C:\项目\老田的AI助手\portal\daily_report' 'cohort_track.py') --register 2>&1 | Out-String)
    ($regOut.Trim() -split "`r?`n" | Select-Object -Last 10) | ForEach-Object { Write-TaskLog ('  reg| ' + $_) }

    Write-TaskLog '===== houfeng_daily done ====='
    exit 0
} catch {
    Write-TaskLog ('EXCEPTION: ' + $_.Exception.Message)
    exit 1
}
