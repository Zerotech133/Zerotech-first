# houfeng_daily_task.ps1 - 候风每日信号产线（pull -> select -> register）
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
    # 但 -Width 只管 Out-File 自己的折行，管不到 $m 里已混进来的换行：native 命令
    # （python.exe）的输出进 PS5.1 时会被按当前控制台宽度包装，而计划任务启动的
    # 会话没有控制台、宽度与手工运行不同 —— 同一份日志里 PAGE 行手工跑完整、
    # 调度跑被拦腰截断（实测 2026-11-16 13:08）。故此处显式压成单行。
    $m = ($m -replace "`r?`n", ' ').Trim()
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
    # 空池台账：池空是业务结论不是进程失败，记下来但不中断（见下方 POOL-EMPTY 注释）
    $emptyPools = @()

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
    # 观察池产物路径必须在这里定义：后面 $haveBoth 走 skip 时会跳过整个 else 分支，
    # 若把定义留在分支内，末尾 Test-Path 会吃到 $null 并抛参数校验错误。
    $watchSel = Join-Path $fp ('selection_' + $asof + '_wf.json')
    # 当日完成标记：文件名带 asof，跨日自动失效，无需清理旧标记。
    # 放在 data/ 下（factor_proto/.gitignore 已忽略该目录，不进对外仓）。
    $doneMark = Join-Path (Join-Path $fp 'data') ('.houfeng_done_' + $asof)
    $haveBoth = (Test-Path $selMain) -and (Test-Path $selAuto)

    # 跳过条件 = 当日标记存在 **且** 主池产物仍在。
    # 为什么不能拿两个产物（$haveBoth）当判据：auto 池空是当日常态（连续多个交易日
    # 无因子过闸门），池空时 select_asof 不落文件 —— 用 $haveBoth 会让跳过在
    # 「池空」这种最常见状态下永远失效，优化等于没做（11-16 11:05 实测仍跑满 242 秒）。
    # 主池产物（selMain）才是「跑过选股」的最低凭证：它被删掉就该重跑，
    # 而 auto 产物缺失只代表池空，不是没跑。
    # 标记必须带 empty= 字段才算有效：旧格式标记（只有 asof/verdict/at）不认，
    # 会自动重跑一次生成新格式。格式升级不需要人工删文件，也不会让不完整
    # 的标记被当成有效凭证。
    $markText = ''
    $doneMarkOk = $false
    if (Test-Path $doneMark) {
        try {
            $markText = [System.IO.File]::ReadAllText($doneMark)
            $doneMarkOk = ($markText -match 'empty=')
        } catch { $doneMarkOk = $false }
    }
    # 主池产物缺失必须分两种原因，不能一概当成「没跑」：
    #   ① 池空 —— select_asof 池空时不落文件，这是**结论本身**（NO-PICK / WATCH-ONLY）；
    #   ② 产物丢失或写失败 —— 异常。标记若称 PICK / CANDIDATES-ONLY，缺失就不是池空。
    # 只对 ① 认可缺失，② 必须重跑；两者在文件系统上长得一模一样，只能靠标记的 verdict 分开。
    $mainOk = (Test-Path $selMain)
    if (-not $mainOk -and $doneMarkOk -and ($markText -match 'verdict=(NO-PICK|WATCH-ONLY)')) {
        $mainOk = $true
    }
    $doneToday = $doneMarkOk -and $mainOk
    if ($doneToday) {
        Write-TaskLog ('SKIP select: ' + $asof + ' 当日已完成（标记 ' + (Split-Path $doneMark -Leaf) + '），跳过拉数与选股，只重生成页面')
        # 空池台账必须按产物实际存在性重算，不能照抄标记里的 empty=：
        # 那是某一次运行的快照，若产物后来被恢复或清理，照抄就把旧结论当事实继承
        # （11-16 11:44 实测：人为写 NO-PICK 后恢复产物，跳过路径仍报 empty=[auto,main,wf]，
        #  而实际只有 auto 空 —— 一个错值被后续所有跳过运行继承下去）。
        # Test-Path 反推是可靠的：池空时 select_asof 不落文件，产物在即池非空。
        # 标记里的 empty= 从此只用于格式版本闸，不再作为结论来源。
        $emptyPools = @()
        if (-not (Test-Path $selAuto))  { $emptyPools += 'auto' }
        if (-not (Test-Path $selMain))  { $emptyPools += 'main' }
        if (-not (Test-Path $watchSel)) { $emptyPools += 'wf' }
    } elseif ($haveBoth) {
        # 产物在但无标记：可能是上一轮跑到选股后崩溃/被中断，或标记写入失败。
        # 不再重跑选股（产物是幂等的，重跑只会慢），末尾会补写标记。
        Write-TaskLog ('SKIP select: ' + $asof + ' 的两个 selection 文件已存在（无完成标记，末尾补写）')
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

        if ($autoOut -match '\[FATAL-POOL\]') {
            # 2026-11-16 拆码：池空=晋升流水线治理拒绝（候选全部被闸门拦下），重试不会改变
            # 结果，与「数据未落地可等重试」语义相反。
            #
            # 语义变更（老田批准）：池空只记日志行，不中断。此前这里以退出码 4 终止会让进程当场
            # 退出，后面的 main 池与 wf 观察池根本没轮到执行 —— 「不能出票」被放大成
            # 「什么都拿不到」，可买与可观察被混成一件事。退出码只答「跑完了吗」，
            # 业务结论走日志行 + 产物文件。
            Write-TaskLog 'POOL-EMPTY: auto 池为空（治理拒绝，重试不会改变结果）'
            $emptyPools += @('auto')
        }
        # 下面三个判定必须是 elseif 链：池空时 select_asof 是 raise SystemExit(2)，rc=2。
        # 若写成独立 if，池空分支不再 exit 之后会被下一句「rc -ne 0 => 崩溃」当场误判成 ABORT。
        # 同理合并/覆盖闸门也是 SystemExit(2)，只有真 Traceback 才是崩溃。
        elseif ($autoOut -match '\[FATAL(-COVERAGE)?\]') {
            # 闸门拒绝（通常=深市两融尚未发布）-> 留给下一个触发点重试
            Write-TaskLog 'RETRY-LATER: auto 池被闸门拒绝，等下一个触发点'
            exit 3
        }
        elseif ($autoOut -match 'Traceback' -or $autoRc -ne 0) {
            # 崩溃不是闸门拒绝：重试也没用，直接失败留痕
            Write-TaskLog ('ABORT: auto 选股异常退出（rc=' + $autoRc + '），上几行即为原因')
            exit 1
        }

        # 3) 选股：main 池（A1 闸门池）
        Remove-Item Env:ZKG_POOL -ErrorAction SilentlyContinue
        $mainOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String -Width 4096)
        $mainRc  = $LASTEXITCODE
        Write-TaskOut 'main' $mainOut 8
        if ($mainOut -match '\[FATAL-POOL\]') {
            Write-TaskLog 'POOL-EMPTY: main 池为空（治理拒绝，重试不会改变结果）'
            $emptyPools += @('main')
        }
        # 同 auto：池空是 SystemExit(2)，不是崩溃，必须走 elseif 链
        elseif ($mainOut -match '\[FATAL(-COVERAGE)?\]') {
            Write-TaskLog 'RETRY-LATER: main 池被闸门拒绝'
            exit 3
        }
        elseif ($mainOut -match 'Traceback' -or $mainRc -ne 0) {
            Write-TaskLog ('ABORT: main 选股异常退出（rc=' + $mainRc + '），上几行即为原因')
            exit 1
        }

        # 3.5) 观察池：wf 稳健集。与可买池分开产出、分开陈述，两个数字绝不合并。
        # 为什么排在可买池之后：可买结论一旦成立就没有看观察池的必要；只有可买为空时，
        # 观察池才是「今天还有没有值得盯的东西」的答案。该池只过 walk-forward 稳健闸，
        # 未过组合层样本外闸 -> 可观察，不可买，不构成任何买入依据。
        if (Test-Path $watchSel) {
            Write-TaskLog ('SKIP watch: ' + $asof + ' 的 wf 观察池产物已存在（如需重算请先移除该文件）')
        } else {
            $env:ZKG_POOL = 'wf'
            $wfOut = (& $py -X utf8 'select_asof.py' 2>&1 | Out-String -Width 4096)
            $wfRc  = $LASTEXITCODE
            Write-TaskOut 'watch' $wfOut 8
            if ($wfOut -match '\[FATAL-POOL\]') {
                Write-TaskLog 'POOL-EMPTY: wf 观察池也为空'
                $emptyPools += @('wf')
            } elseif ($wfOut -match '\[FATAL(-COVERAGE)?\]') {
                Write-TaskLog 'WARN: wf 观察池被闸门拒绝，可买结论不受影响'
            } elseif ($wfOut -match 'Traceback' -or $wfRc -ne 0) {
                # 不 exit：观察池失败不改变可买结论，但必须留痕，否则「有观察池」会被静默谎报
                Write-TaskLog ('WARN: wf 观察池异常退出（rc=' + $wfRc + '），可买结论不受影响')
            }
            # ZKG_POOL 清掉：cohort_track 按文件名后缀分流（_wf 明确 continue，不进台账），
            # 并不读这个变量；但留着 wf 语境会让后续子进程误判池来源，属防御性清理。
            Remove-Item Env:ZKG_POOL -ErrorAction SilentlyContinue
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

    # —— 业务结论：走日志行 + 产物文件，不走退出码 ——
    # 退出码只答「跑完了吗」：正常完成（有票/无票/仅观察/跳过）一律 0，崩溃与阻断才非 0。
    #
    # 判据是「多因子共识数」，不是「文件存不存在」：select_asof 只要有因子命中就落盘，
    # 单因子命中 109 只也会写出文件，但 confluence_multi=0 —— 台账不登记、不构成批次。
    # 把「有文件」读成「有票」，就是把候选当成了买票。
    # 注意 PS 陷阱：@($null).Count 等于 1，必须先判真再计数。
    $nCand = 0
    $nConc = 0
    foreach ($sf in @($selMain, $selAuto)) {
        if (Test-Path $sf) {
            try {
                $sj = Get-Content $sf -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($sj.union) { $nCand += @($sj.union).Count }
                if ($sj.confluence_multi) { $nConc += @($sj.confluence_multi).Count }
            } catch {
                Write-TaskLog ('WARN: 无法解析 ' + (Split-Path $sf -Leaf) + '，该池结论按 0 计')
            }
        }
    }
    $hasWatch = Test-Path $watchSel
    $nWatch = 0
    if ($hasWatch) {
        try {
            $wj = Get-Content $watchSel -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($wj.union) { $nWatch = @($wj.union).Count }
        } catch { $nWatch = -1 }
    }
    if ($nConc -gt 0) { $verdict = 'PICK' }
    elseif ($nCand -gt 0) { $verdict = 'CANDIDATES-ONLY' }
    elseif ($hasWatch) { $verdict = 'WATCH-ONLY' }
    else { $verdict = 'NO-PICK' }
    Write-TaskLog ('VERDICT: ' + $verdict + ' cand=' + $nCand + ' conc=' + $nConc + ' watch=' + $nWatch + ' empty_pools=[' + ($emptyPools -join ',') + ']')

    # 写当日完成标记：只在 VERDICT 之后。上面任何一条 exit 3 / exit 1 都走不到这里，
    # 因此「可重试的失败」不会留下标记，重试权不被吃掉。
    # 池空也是完整结论（POOL-EMPTY 已落日志），同样写标记 —— 当日结论不会再变。
    # 用 .NET API 而非 Set-Content：本机 PS5.1 处理含中文的路径不可信，WriteAllText 更稳。
    try {
        # empty= 是必填字段：既是空池台账的跨运行载体，也是标记格式的版本闸。
        $markBody = 'asof=' + $asof + ' verdict=' + $verdict + ' empty=' + ($emptyPools -join ',') + ' at=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        [System.IO.File]::WriteAllText($doneMark, $markBody, [System.Text.Encoding]::ASCII)
        # 留痕：没有这行，事后无法区分「写了标记」与「静默没写」，只能靠猜文件在不在。
        Write-TaskLog ('DONE-MARK: ' + (Split-Path $doneMark -Leaf) + ' | ' + $markBody)
    } catch {
        # 不中断：标记写不进去只是退化为每次都跑（慢），不影响结论正确性。
        Write-TaskLog ('WARN: 当日完成标记写入失败（' + $_.Exception.Message + '），后续触发点会重复跑')
    }

    # 生成当日页：与本次运行同生命周期，避免「跑了但页面没更」的错位。
    # 失败只 WARN 不改退出码 —— 退出码只答产线跑完了吗，页面是呈现层。
    $pageOut = (& $py -X utf8 (Join-Path $portal 'build_houfeng_today.py') 2>&1 | Out-String -Width 4096)
    if ($LASTEXITCODE -ne 0 -or $pageOut -match 'Traceback') {
        Write-TaskOut 'page' $pageOut 4
        Write-TaskLog 'WARN: 当日页生成失败，页面可能与数据不一致'
    } else {
        $pageLine = ($pageOut -split "`r?`n" | Where-Object { $_ -match '^TODAY:' } | Select-Object -Last 1)
        Write-TaskLog ('PAGE: ' + $pageLine)
    }

    # 宿主首页「三平台今日一句」：借候风跑完的这个时刻刷一次。
    # 为什么挂这里而不另开 08:50 独立任务：候风是首页三张卡的主驱动，它没跑完时
    # 刷出来的首页就是半成品；且多一个调度对象就多一个会悄悄失效的东西。
    # 代价是一天刷 2-3 次（三个触发点，跳过的触发点也走到这里），每次约 9 秒，
    # 写的是同一份内容 —— 幂等，无副作用。
    # 与 PAGE 同款约定：失败只 WARN 不改退出码，首页是呈现层不是产线。
    # 注意：本脚本只保证「刷新发生在候风结论定稿之后」，不等于三平台联合调度 ——
    # 缠论 observe_board.json 与中台 system_health.json 各按自己的时点更新。
    $platPy  = Join-Path $root 'portal\build_platform_today.py'
    $platOut = (& $py -X utf8 $platPy 2>&1 | Out-String -Width 4096)
    if ($LASTEXITCODE -ne 0 -or $platOut -match 'Traceback') {
        Write-TaskOut 'plat' $platOut 6
        Write-TaskLog 'WARN: 首页三平台数据刷新失败，首页可能停在上一交易日'
    } else {
        $platLine = ($platOut -split "`r?`n" | Where-Object { $_ -match '^(HOME|OK)' } | Select-Object -Last 1)
        Write-TaskLog ('PLAT: ' + $platLine)
    }
    # 候风平台入口页（/houfeng/）：与今日页共用同一个 build()，两页不会说不同的话。
    # 放在 ps1 里而不是塞进 build_platform_today.py：那个脚本是宿主层聚合器，
    # 不该去写某个平台自己的页面 —— 谁的门户谁生成。
    # 注意：本脚本的 $portal 实指 portal\daily_report（第 26 行），不是 portal 本身。
    # 入口页生成器在 portal 根下，必须用 $root 拼，不能沿用 $portal ——
    # 13:55 首次实跑就是照抄 $portal 而报 Errno 2（单测全过、生产路径参数错）。
    $idxPy  = Join-Path $root 'portal\build_houfeng_index.py'
    $idxOut = (& $py -X utf8 $idxPy 2>&1 | Out-String -Width 4096)
    if ($LASTEXITCODE -ne 0 -or $idxOut -match 'Traceback') {
        Write-TaskOut 'idx' $idxOut 6
        Write-TaskLog 'WARN: 候风入口页生成失败，/houfeng/ 可能停在上一交易日'
    } else {
        $idxLine = ($idxOut -split "`r?`n" | Where-Object { $_ -match '^IDX:' } | Select-Object -Last 1)
        Write-TaskLog ('IDX: ' + $idxLine)
    }
    Write-TaskLog '===== houfeng_daily done ====='
    exit 0
} catch {
    Write-TaskLog ('EXCEPTION: ' + $_.Exception.Message)
    # 异常时同样重生成：页面必须反映「今天失败了」，不能停在昨天的结论上。
    try { & $py -X utf8 (Join-Path $portal 'build_houfeng_today.py') 2>&1 | Out-Null } catch { }
    # 首页同理：候风失败时首页该显示「候风读取失败」，不能还挂着昨天的结论。
    try { & $py -X utf8 (Join-Path $root 'portal\build_platform_today.py') 2>&1 | Out-Null } catch { }
    # 入口页同理：失败时它该显示「未采集」，不能挂着昨天的结论。
    try { & $py -X utf8 (Join-Path $root 'portal\build_houfeng_index.py') 2>&1 | Out-Null } catch { }
    exit 1
}
