# houfeng_snap_task.ps1 - 侯风台账盘后快照 + 结算（两个时点共用本脚本）
#
# 为什么是 15:10（第一趟）：snap() 用实时行情接口取「现价 / 今开 / 昨收」，15:00 收盘
#   后接口返回的现价即收盘价，15:10 足够稳定。目的：尽早出一份收盘快照，页面早可用。
#
# 为什么还要 16:40（第二趟）：**15:10 拿不到当日日线**（2026-11-11 判例）——
#   同一时刻 adj_factor 取得到（mktcache/adjf_YYYYMMDD.parquet 15:10:05），
#   但 daily 取不到（daily_YYYYMMDD.parquet 15:50:21 才有）。结算读的是日线，
#   所以 15:10 那趟的当日到期项只能记「待结算」，当日结清要靠 16:40 这趟。
#   第二趟另有一个作用：价格守卫 R1「两轮收盘后快照互比」要求同日两次运行，
#   一天只跑一次时那一级永远不触发。
#
# 顺序：先 --settle 再 --snap。snap 会把 rolling_alpha() 写进 status_latest.json，
#   先结算才能让滚动降级线读到最新样本。
#
# 幂等：settle 只结未结项；snap 覆盖写 status_latest.json。重复触发无副作用，
#   所以 15:10 与 16:40 各跑一次是安全的：第二次顺手补齐第一次的待结算项。
param(
    [switch]$WhatIf,
    [string]$Phases = 'settle,snap,arena,health',
    [string]$Log    = 'C:\Temp\houfeng_snap_task.log',
    [string]$Tag    = 'houfeng_snap'
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Continue'

$py  = 'C:\Program Files\Python311\python.exe'
# 工作目录从脚本自身位置推导（本仓对外可见，不写死本机绝对路径；2026-11-11 脱敏）
if (-not $PSScriptRoot) { throw '需以 -File 方式执行本脚本（$PSScriptRoot 为空时无法推导工作目录）' }
$wd  = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'portal\daily_report'
$log = $Log

# 每行独立取时刻（2026-11-11 修）。此前 $stamp 在脚本开头只算一次，整份日志
# 所有行都是启动时刻：15:10 那趟 settle 行与 snap 完成行同为 15:10:02，而 snap
# 实际跑到 15:14:59。用「产物时间戳 vs 调度启动时刻」反查时，两值相同就没了参照。
function Write-TaskLog([string]$m) {
    # -Width 4096：Out-File 默认按控制台宽度折行，长路径与 JSON 会被切断
    # （2026-11-11 22:46 那次实测：「落盘 → ...coho」/「orts\status_latest.json」
    # 断成两行），折过行的日志没法再被程序解析，也没法 grep 完整路径。
    ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) |
        Out-File $log -Append -Encoding utf8 -Width 4096
}

# 统一处理子进程输出：保留末 $keep 行，被截断时显式记一行（静默截断等于隐藏信息）。
function Write-TaskOut([string]$prefix, [string]$text, [int]$keep = 20) {
    $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    if ($lines.Count -eq 0) { Write-TaskLog ('  ' + $prefix + '| （无输出）'); return }
    if ($lines.Count -gt $keep) {
        $skip = $lines.Count - $keep
        Write-TaskLog ('  ' + $prefix + '| …（前 ' + $skip + ' 行略，仅记末 ' + $keep + ' 行）')
        $lines = $lines[$skip..($lines.Count - 1)]
    }
    $lines | ForEach-Object { Write-TaskLog ('  ' + $prefix + '| ' + $_) }
}

try {
    $ph = @($Phases -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

    if ($WhatIf) {
        if ($ph -contains 'settle') { Write-Output ('将执行: ' + $py + ' -X utf8 cohort_track.py --settle') }
        if ($ph -contains 'snap')   { Write-Output ('将执行: ' + $py + ' -X utf8 cohort_track.py --snap') }
        if ($ph -contains 'arena')  { Write-Output ('将执行: ' + $py + ' -X utf8 paper_arena.py → build_arena.py') }
        if ($ph -contains 'health') { Write-Output ('将执行: ' + $py + ' -X utf8 data_health.py --target prev') }
        Write-Output ('阶段: ' + ($ph -join ','))
        Write-Output ('工作目录: ' + $wd)
        Write-Output ('日志: ' + $log)
        exit 0
    }

    Set-Location $wd
    Write-TaskLog ('===== ' + $Tag + ' start (' + ($ph -join ',') + ') =====')

    # 1) 结算：只结到期未结项，当日到期需 15:00 之后。
    #    注意 15:10 这趟拿不到当日日线，当日到期项会打印「待结算」而不落账，
    #    由 16:40 那趟补齐（见文件头判例）。
    if ($ph -contains 'settle') {
        $setOut = (& $py -X utf8 'cohort_track.py' --settle 2>&1 | Out-String)
        $setRc  = $LASTEXITCODE
        Write-TaskOut 'settle' $setOut
        # 退出码与 Traceback 都要看（2026-11-11 修）：只看 Traceback 会漏掉
        # 用 sys.exit(非0) 退出的情况——那种退出不打印异常栈，却什么都没做。
        if ($setOut -match 'Traceback' -or $setRc -ne 0) {
            # 结算失败不阻断快照：快照本身仍有价值
            Write-TaskLog ('WARN: settle 失败（rc=' + $setRc + '），继续跑 snap；上几行即为原因')
        }
        # 待结算不是故障，但要在日志里显式留一行，免得和「真的没有待结项」混淆
        if ($setOut -match '待结算') {
            Write-TaskLog 'NOTE: 有到期项因当日日线未发布保持未结，等 16:40 那趟补（数字不会错）'
        }
    }

    # 2) 快照：全市场实时 -> status_latest.json（含 rolling 降级线）
    if ($ph -contains 'snap') {
        $snapOut = (& $py -X utf8 'cohort_track.py' --snap 2>&1 | Out-String)
        $snapRc  = $LASTEXITCODE
        Write-TaskOut 'snap' $snapOut
        if ($snapOut -match 'Traceback' -or $snapRc -ne 0) {
            Write-TaskLog ('ABORT: snap 失败（rc=' + $snapRc + '），status_latest.json 未更新')
            exit 1
        }
    }

    # 3) 多策略模拟盘（2026-11-11 立）：把「某天选了什么 × 几种打法 × 后来怎样」
    #    逐日钉下来，产出 arena/latest.json + paper_arena.html。
    #    依赖当日日线（收盘价估值），与 settle 同一个原因：15:10 那趟日线还没发布，
    #    只能是「未就绪、跳过」，不是故障 —— 这两种情况必须分开记，否则
    #    「跳过」会被 WARN 的措辞吞掉（11-11 结算静默失败那个判例）。
    if ($ph -contains 'arena') {
        $arOut = (& $py -X utf8 'paper_arena.py' 2>&1 | Out-String)
        $arRc  = $LASTEXITCODE
        Write-TaskOut 'arena' $arOut 24
        if ($arOut -match '行情未就绪') {
            Write-TaskLog 'NOTE: 当日日线未就绪，arena 跳过（16:40 那趟重跑即可，不丢数据）'
        } elseif ($arOut -match 'Traceback' -or $arRc -ne 0) {
            Write-TaskLog ('WARN: arena 失败（rc=' + $arRc + '），模拟盘页面不会更新；上几行即为原因')
        } else {
            $bdOut = (& $py -X utf8 'build_arena.py' 2>&1 | Out-String)
            $bdRc  = $LASTEXITCODE
            Write-TaskOut 'page' $bdOut 6
            if ($bdOut -match 'Traceback' -or $bdRc -ne 0) {
                Write-TaskLog ('WARN: 页面构建失败（rc=' + $bdRc + '），latest.json 已更新但 html 未刷新')
            }
        }
    }

    # 4) 数据巡检：断言上一交易日的数据确实落到了表里（2026-11-11 立）
    #    只检 prev：当天的日线还在交易所侧，检 today 必然报零采集。
    #    这一检正对着「11-10 整天没采数据、旧脚本却判七表全绿」那次漏判 ——
    #    旧判据先取 max(trade_date) 再查那天齐不齐，于是永远自洽。
    #    不阻断：巡检不过说明数据侧有洞，快照与结算本身仍然有效。
    #    只在 15:10 第一趟做，16:40 那趟跳过（同一天同一批数据，重复检没有信息）。
    if ($ph -contains 'health') {
        $dhOut = (& $py -X utf8 'data_health.py' --target prev 2>&1 | Out-String)
        $dhRc = $LASTEXITCODE
        Write-TaskOut 'data' $dhOut 40
        if ($dhOut -match 'Traceback' -or $dhRc -ne 0) {
            Write-TaskLog ('WARN: 数据巡检未达标（rc=' + $dhRc + '），上一条判定行即为原因')
        }
    }

    Write-TaskLog ('===== ' + $Tag + ' done =====')
    exit 0
} catch {
    Write-TaskLog ('EXCEPTION: ' + $_.Exception.Message)
    exit 1
}
