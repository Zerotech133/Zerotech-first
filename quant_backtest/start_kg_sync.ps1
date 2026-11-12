# kg daily sync launcher (ASCII only, junction path)
# 0) catalog rebuild (G7: freshness ground truth)  0.5) semantica events build
# 0.7) [W1-07 2026-11-03] notice LLM shadow judge + backfill -> owned by \ZeroBound\NoticeBackfill (removed from this chain)
# 1) global index increment   2) events -> Neo4j
# 3) CL-1 seats/chanlun/volume -> Neo4j
# 4) M2 DRIVE edges          5) candle events (HAS_CANDLE)
# 5.5) B-class sync (limit_list/HOLDS/holder-number feats, kg_bclass)
# 5.7) seat win-rate backfill (TRADED_ON x next-day close, kg_seat_winrate)
# 5.8) topgap backfill (top_list gap/nature/penetration, kg_dt_gap)
# 5.9) k-line similarity DTW (60d log-path, SIMILAR_K edges, kg_sim_k)
# 5.10) trade-date nodes + ON_DATE/NEXT_TRADE_DAY (P3-d, kg_tradedate)
# 6) kg features recompute -> sector-flow tickets (SF 6-step pipeline
#    step5+6; reads topgap from dt_gap at 5.8, pure graph-read + reports,
#    before brief so its failure never blocks the ticket flow)
# 7) distill sample snapshot (M5-W5)   8) graph QA check (G1-8, warn-only)
# 8.5) ontology snapshot export (L1 registry x live counts, warn-only)
# 9) daily intel brief -> feishu (G1-1)
# (graph writers all land before features recompute; distill last,
#  reads everything incl. daily-pool ret backfill; check+brief after all
#  writers so coverage/QA reflect the fresh state; brief skips if QA
#  state unchanged via its own last_date watermark)
# NOTE: PS5.1 Start-Process + Redirect + PassThru must pair with -Wait,
#       otherwise ExitCode is always null (Wait-Process/Refresh do NOT fix it).
# NOTE: notice_llm step never aborts the chain (Ollama down = skip judge,
#       backfill stale rows only, script exits 0 by design).
# NOTE: intel_link + mentions_rebuild steps (added 2026-09-30, approved by
#       laotian) run zerobound-kg（旧名 KGStockAnalysis）dev scripts via absolute path, EXP_
#       layer only; intel_link exits 1 on main-graph drift (fail-closed).
# NOTE: 2026-10-16 machine-migration repair (kg-graph-storage-design 6):
#       - $kg_root variable replaces the dead E:\KGStockAnalysis hardcode
#         (E: drive does not exist on this machine; junction not viable).
#       - new step intel_xq_guba inserted after intel_link (registry-based
#         xueqiu+guba intel backfill/increment, MERGE idempotent).
#       - 2026-11-08 郑玄(KGMAP1) P1 老田批: new step intel_author inserted
#         after intel_xq_guba (IntelAuthor 博主实体层全量幂等重算+AUTHORED_BY
#         挂边; author_key 索引先行, 秒级). rollback: restore
#         C:\项目\backups\start_kg_sync.ps1.bak-kgmapP1-20261108
#       - KG env credentials loaded from .env.local (KG_* keys only,
#         machine-level env intentionally not used, fail-closed).
#       - KG_DATA_ROOT degrades to KG_DATA_ROOT_FALLBACK when the new-root
#         shell lacks catalog.json (design 1.2); both missing = fail fast.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
# KG unified credentials (design 2.4): load KG_* lines from .env.local only
# [20261016收编] 去 C:\aios junction 依赖，一律本机真实路径（章程 §5 治理契约）
$envFile = 'C:\项目\老田的AI助手\quant_backtest\.env.local'
if (Test-Path $envFile) {
    # [20261016换机] 必须 UTF8：默认 GBK 解码中文注释时奇数字节吞换行，
    # 会把紧随的 KG_NEO4J_URI 行并入注释导致 URI 永远加载不上
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^KG_[A-Z0-9_]+=') {
            $k, $v = $_ -split '=', 2
            Set-Item -Path ("Env:" + $k) -Value $v.Trim()
        }
    }
}
# KG_DATA_ROOT fallback degradation (design 1.2): new root is an empty shell
# on this machine -> use quant_backtest/data; both missing catalog -> abort
# [20261017探针加固] 仅凭 catalog.json 存在性判根不足：KGDataRoot 回收当日曾出现
# 删除竞态写出的 tables:{} 空壳清单，若被选根，每日链会把空仓当数据根。
# 判据升级：catalog 存在且 tables 非空才算可用根；空壳根降级 fallback 并记 WARN。
function Test-CatalogUsable([string]$root) {
    $cat = Join-Path $root 'catalog.json'
    if (-not (Test-Path $cat)) { return $false }
    try {
        $j = Get-Content $cat -Raw -Encoding UTF8 | ConvertFrom-Json
        $t = $j.tables
        if ($null -eq $t) { return $false }
        return (@($t.PSObject.Properties).Count -gt 0)
    } catch { return $false }
}
$kg_new_root = 'C:\项目\KGDataRoot'
$kg_fallback = 'C:\项目\老田的AI助手\quant_backtest\data'
if (Test-CatalogUsable $kg_new_root) {
    $env:KG_DATA_ROOT = $kg_new_root
} elseif (Test-CatalogUsable $kg_fallback) {
    $env:KG_DATA_ROOT = $kg_fallback
    $env:KG_DATA_ROOT_FALLBACK = $kg_fallback
    if (Test-Path (Join-Path $kg_new_root 'catalog.json')) {
        ('kg_sync WARN: new root catalog is an empty shell (tables empty), degraded to fallback: ' + $kg_fallback) | Out-File 'C:\Temp\kg_sync_task.log' -Append -Encoding utf8
    }
} else {
    ('kg_sync ABORT: no usable catalog.json (missing or tables empty) in either data root: ' + $kg_new_root + ' / ' + $kg_fallback) | Out-File 'C:\Temp\kg_sync_task.log' -Append -Encoding utf8
    exit 1
}
$kg_root = 'C:\项目\zerobound-kg'
# 换机适配(2026-10-16): kg_intel_link.py 默认 news.db 指向旧机 E:/ 路径,
# 用 KG_NEWS_DB 覆盖为新机 junction 内真实库
$env:KG_NEWS_DB = 'C:\项目\老田的AI助手\news_collector\news.db'
# [20261016换机] xueqiu/guba 数据在爬虫产物区（quant_backtest\data），不在 KGDataRoot
$env:KG_XQ_DB = 'C:\项目\老田的AI助手\quant_backtest\data\xueqiu\xueqiu_info.db'
$env:KG_GUBA_DIR = 'C:\项目\老田的AI助手\quant_backtest\data\guba'
if (-not (Test-Path (Join-Path $kg_root 'kg_intel_link.py'))) {
    ('kg_sync ABORT: KG root missing: ' + $kg_root) | Out-File 'C:\Temp\kg_sync_task.log' -Append -Encoding utf8
    exit 1
}
# OPS-1014 (2026-10-09): evt_build died with httpx ConnectTimeout to
# huggingface.co - fastembed re-validates the model online on every start and
# its default cache lives in %TEMP% (wiped by temp cleaners -> forced re-download
# -> blocked direct HF access from the task session). Fix: persistent cache
# (pre-warmed 2026-10-09 via hf-mirror, dim=512 verified) + hard offline mode.
$env:FASTEMBED_CACHE_PATH = 'C:\项目\老田的AI助手\quant_backtest\.fastembed_cache'
$env:HF_HUB_OFFLINE = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$log = 'C:\Temp\kg_sync_task.log'
$py = 'C:\Program Files\Python311\python.exe'
$wd = 'C:\项目\老田的AI助手\quant_backtest'
# [20261016血缘收编] zerobound-kg（旧名 KGStockAnalysis）侧脚本经此变量桥接 import linreg（章程 §4.4）
$env:QUANT_BACKTEST_ROOT = $wd
# [P1-a 2026-11-12 批准落地] DB/图就绪门：7687(Neo4j) 与 5442(quant-pg) 端口就绪才放行，
# 最多等 30 分钟。背景：夜间睡眠后唤醒补跑 09:35 撞 Docker 容器冷启动（~10:40 才起），
# kg_evt_sync 连 7687 refused ×6 后 ABORT。只探测端口、不写库；超时记 WARN 放行，
# 失败仍由既有 exit=1 + 告警链路兜底。夜间正常场景（06:20 端口已就绪）零等待直行。
# 回滚：删除本代码块，或恢复 C:\项目\backups\start_kg_sync.ps1.bak-20261112-P123
$deadline = (Get-Date).AddMinutes(30)
foreach ($port in @(7687, 5442)) {
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $ready) {
        $c = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue
        if ($c.TcpTestSucceeded) { $ready = $true } else { Start-Sleep -Seconds 20 }
    }
    if (-not $ready) {
        ('kg_sync WARN: port ' + $port + ' not ready after 30min, proceed anyway ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    }
}
try {
    foreach ($step in @(
        @{name='catalog_build';     script='build_catalog.py';       arg=''},
        @{name='evt_build';        script='event_graph.py';         arg='build'},
        # W1-07 (2026-11-03 A0 granted): notice_llm --backfill removed from KgDaily chain;
        # backfill is owned solely by \ZeroBound\NoticeBackfill (dedupe; G5/G7 no longer block each other).
        # rollback: restore C:\项目\backups\start_kg_sync.ps1.bak-20261103-W107 and rerun.
        # @{name='notice_llm';       script='kg_notice_llm.py';       arg='--backfill'},
        @{name='pull_global_idx';  script='pull_global_idx.py';     arg=''},
        @{name='kg_evt_sync';      script='kg_build_p3_evt.py';     arg=''},
        @{name='intel_link';       script="$kg_root\kg_intel_link.py";       arg='--write'},
        @{name='intel_xq_guba';    script="$kg_root\kg_intel_xq_guba.py";    arg=''},
        @{name='intel_author';     script="$kg_root\kg_intel_author.py";     arg=''},
        @{name='mentions_rebuild'; script="$kg_root\kg_mentions_rebuild.py"; arg='--live --src daily-chain'},
        @{name='cl1_sync';         script='kg_build_cl1.py';        arg=''},
        @{name='m2_drive_sync';    script='kg_build_m2.py';         arg=''},
        @{name='candle_sync';      script='kg_candle.py';           arg='sync'},
        @{name='bclass_sync';      script='kg_bclass.py';           arg='sync'},
        @{name='seat_winrate';     script='kg_seat_winrate.py';     arg=''},
        @{name='dt_gap';           script='kg_dt_gap.py';           arg=''},
        @{name='sim_k';            script='kg_sim_k.py';            arg=''},
        @{name='tradedate';        script='kg_tradedate.py';        arg=''},
        @{name='kg_features';      script='kg_features.py';         arg=''},
        @{name='sector_flow';      script='kg_sector_flow.py';      arg=''},
        @{name='distill_build';    script='kg_distill.py';          arg='build'},
        @{name='graph_check';      script='kg_check.py';            arg=''},
        @{name='ontology_snap';    script='ontology.py';            arg='--export'},
        @{name='kg_brief';         script='kg_brief.py';            arg=''}
    )) {
        # rooted script paths (e.g. zerobound-kg dev scripts) bypass $wd join
        $sp = if ([IO.Path]::IsPathRooted($step.script)) { $step.script } else { "$wd\" + $step.script }
        $pyargs = @('-X','utf8', $sp)
        if ($step.arg) { $pyargs += $step.arg }
        $p = Start-Process -FilePath $py `
            -ArgumentList $pyargs `
            -WorkingDirectory $wd `
            -RedirectStandardOutput ("C:\Temp\kg_" + $step.name + "_out.log") `
            -RedirectStandardError ("C:\Temp\kg_" + $step.name + "_err.log") `
            -PassThru -WindowStyle Hidden -Wait
        $code = $p.ExitCode
        ($step.name + ' exit=' + $code + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        if ($code -ne 0) {
            ('kg_sync ABORT at ' + $step.name) | Out-File $log -Append -Encoding utf8
            $errL = "C:\Temp\kg_" + $step.name + "_err.log"
            & 'C:\Program Files\Python311\python.exe' -X utf8 C:\项目\老田的AI助手\news_collector\alert_fail_wrap.py ("kg_sync_" + $step.name) $code $errL 1>$null 2>$null
            exit 1
        }
    }
    # P0-1（2026-11-03 计划 v4）：catalog -> registry 自动对齐（KgDaily 链尾，失败告警不静默）
    $alignPy = 'C:\项目\py312\python.exe'
    $alignScript = 'C:\项目\老田的AI助手\tools\datawarehouse\registry_align.py'
    $alignOut = 'C:\Temp\kg_registry_align_out.log'
    $alignErr = 'C:\Temp\kg_registry_align_err.log'
    # R9 (2026-11-06): 链尾对齐加 1 次重试 (60s 退避)，全失败标 STALE 并告警。
    # 背景: 11-05 出现 rc=1，链日志只有一行 WARN，registry 旧一整天无人察觉。
    # 退出码: 0=已对齐/对齐成功; 1=写后校验失败已回滚; 2=前置错误(锁占用/前置未满足/catalog 不可读)。
    # 回滚: cp C:\项目\backups\start_kg_sync.ps1.bak-20261106-R9 <本文件>
    $alignRc = 1
    $alignTry = 0
    $alignMax = 2
    while ($alignTry -lt $alignMax) {
        $alignTry++
        $alignProc = Start-Process -FilePath $alignPy `
            -ArgumentList @('-X','utf8',$alignScript,'--apply','--json') `
            -WorkingDirectory 'C:\项目\老田的AI助手' `
            -RedirectStandardOutput $alignOut -RedirectStandardError $alignErr `
            -PassThru -WindowStyle Hidden -Wait
        $alignRc = $alignProc.ExitCode
        ('registry_align attempt=' + $alignTry + '/' + $alignMax + ' exit=' + $alignRc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        if ($alignRc -eq 0) { break }
        if ($alignTry -lt $alignMax) { Start-Sleep -Seconds 60 }
    }
    if ($alignRc -ne 0) {
        ('kg_sync STALE registry_align rc=' + $alignRc + ' after ' + $alignMax + ' attempts at ' + (Get-Date -Format s) + ': warehouse_registry may be outdated; run tools/datawarehouse/registry_align.py --apply manually') | Out-File $log -Append -Encoding utf8
        & 'C:\Program Files\Python311\python.exe' -X utf8 C:\项目\老田的AI助手\news_collector\alert_fail_wrap.py kg_sync_registry_align $alignRc $alignErr 1>$null 2>$null
    }
    ('kg_sync OK ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 0
} catch {
    ('kg_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
