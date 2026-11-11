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

$py = 'C:\Program Files\Python311\python.exe'
# 路径全部从脚本自身位置推导（本仓对外可见，不写死本机绝对路径；2026-11-11 脱敏）。
# tools\scheduled\ 上溯两级 = 主仓根；再上一级 = 项目目录 —— zerobound-kg 是与
# 主仓平级的独立仓，不能从主仓根往下找。改前是三条硬编码绝对路径，换机器即失效。
if (-not $PSScriptRoot) { throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导路径）' }
$root     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$proj     = Split-Path -Parent $root
$fp       = Join-Path (Join-Path $proj 'zerobound-kg') 'factor_proto'
$dailyDir = Join-Path $root 'quant_backtest\data\tushare\daily'
$portal   = Join-Path $root 'portal\daily_report'
$log      = 'C:\Temp\houfeng_daily_task.log'

# 每行独立取时刻（2026-11-11 修）：此前 $stamp 只在开头算一次，整份日志所有行都是
# 启动时刻，分不清「拉数何时完 / 选股何时起」。同族缺陷与修法见 houfeng_snap_task.ps1。
function Write-TaskLog([string]$m) {
    # -Width 4096：Out-File 默认按控制台宽度折行，长路径与 JSON 会被切断，
    # 折过行的日志没法再被程序解析，也没法 grep 完整路径。
    ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) |
        Out-File $log -Append -Encoding utf8 -Width 4096
}

# 统一处理子进程输出：保留末 $keep 行，被截断时显式记一行（静默截断等于隐藏信息）。
function Write-TaskOut([string]$prefix, [string]$text, [int]$keep = 12) {
    $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    if ($lines.Count -eq 0) { Write-TaskLog ('  ' + $prefix + '| （无输出）'); return }
    if ($lines.Count -gt $keep) {
        $skip = $lines.Count - $keep
        Write-TaskLog ('  ' + $prefix + '| …（前 ' + $skip + ' 行略，仅记末 ' + $keep + ' 行）')
        $lines = $lines[$skip..($lines.Count - 1)]
    }
    $lines | ForEach-Object { Write-TaskLog ('  ' + $prefix + '| ' + $_) }
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
        $pullOut = (& $py -X utf8 'pull_daily_delta.py' --asof $asof --start $asof 2>&1 | Out-String -Width 4096)
        $pullRc  = $LASTEXITCODE
        Write-TaskOut 'pull' $pullOut 6
        # 退出码与 Traceback 都要看：只用 sys.exit(非0) 退出时不打异常栈，
        # 会被当成成功继续往下选股（2026-11-11 修）。
        if ($pullOut -match 'Traceback' -or $pullRc -ne 0) {
            Write-TaskLog ('ABORT: pull_daily_delta 失败（rc=' + $pullRc + '），上几行即为原因')
            exit 1
        }

        # 2) 选股：auto 池（晋升池，含情绪因子 -> 两融闸门生效）
        $env:ZKG_POOL = 'auto'
        $env:ZKG_ASOF = 'auto'
        $env:ZKG_PANEL_CACHE = (Join-Path $fp '.panel_cache')
        $autoOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String -Width 4096)
        $autoRc  = $LASTEXITCODE
        Write-TaskOut 'auto' $autoOut 8

        if ($autoOut -match '\[FATAL\]') {
            # 闸门拒绝（通常=深市两融尚未发布）-> 留给下一个触发点重试
            Write-TaskLog 'RETRY-LATER: auto 池被闸门拒绝，等下一个触发点'
            exit 3
        }
        if ($autoOut -match 'Traceback' -or $autoRc -ne 0) {
            # 崩溃不是闸门拒绝：重试也没用，直接失败留痕
            Write-TaskLog ('ABORT: auto 选股异常退出（rc=' + $autoRc + '），上几行即为原因')
            exit 1
        }

        # 3) 选股：main 池（A1 闸门池）
        Remove-Item Env:ZKG_POOL -ErrorAction SilentlyContinue
        $mainOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String -Width 4096)
        $mainRc  = $LASTEXITCODE
        Write-TaskOut 'main' $mainOut 8
        if ($mainOut -match '\[FATAL\]') {
            Write-TaskLog 'RETRY-LATER: main 池被闸门拒绝'
            exit 3
        }
        if ($mainOut -match 'Traceback' -or $mainRc -ne 0) {
            Write-TaskLog ('ABORT: main 选股异常退出（rc=' + $mainRc + '），上几行即为原因')
            exit 1
        }
    }

    # 4) 登记新批次（幂等：已登记的不会重复写）
    $regOut = (& $py -X utf8 (Join-Path $portal 'cohort_track.py') --register 2>&1 | Out-String -Width 4096)
    $regRc  = $LASTEXITCODE
    Write-TaskOut 'reg' $regOut 10
    if ($regOut -match 'Traceback' -or $regRc -ne 0) {
        # 不 exit：登记幂等，下一个触发点会重补；但必须留痕，否则「信号选出来了
        # 却没进台账」会没有任何痕迹（08:40/09:40/10:40 三个触发点，只有末次失败
        # 时就没有下一次可补了，所以这一行 WARN 是唯一线索）。
        Write-TaskLog ('WARN: 登记失败（rc=' + $regRc + '），信号已选出但可能未进台账')
    }

    Write-TaskLog '===== houfeng_daily done ====='
    exit 0
} catch {
    Write-TaskLog ('EXCEPTION: ' + $_.Exception.Message)
    exit 1
}
