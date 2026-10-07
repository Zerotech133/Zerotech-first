# kg daily sync launcher (ASCII only, junction path)
# 0) catalog rebuild (G7: freshness ground truth)  0.5) semantica events build
# 0.7) notice LLM shadow judge + backfill (G6b)
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
#       laotian) run KGStockAnalysis dev scripts via absolute path, EXP_
#       layer only; intel_link exits 1 on main-graph drift (fail-closed).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$log = 'C:\Temp\kg_sync_task.log'
$py = 'C:\Program Files\Python311\python.exe'
$wd = 'C:\aios\quant_backtest'
try {
    foreach ($step in @(
        @{name='catalog_build';     script='build_catalog.py';       arg=''},
        @{name='evt_build';        script='event_graph.py';         arg='build'},
        @{name='notice_llm';       script='kg_notice_llm.py';       arg='--backfill'},
        @{name='pull_global_idx';  script='pull_global_idx.py';     arg=''},
        @{name='kg_evt_sync';      script='kg_build_p3_evt.py';     arg=''},
        @{name='intel_link';       script='E:\KGStockAnalysis\kg_intel_link.py';       arg='--write'},
        @{name='mentions_rebuild'; script='E:\KGStockAnalysis\kg_mentions_rebuild.py'; arg='--live --src daily-chain'},
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
        @{name='kg_brief';         script='kg_brief.py';            arg=''}
    )) {
        # rooted script paths (e.g. KGStockAnalysis dev scripts) bypass $wd join
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
            & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\quant_backtest\alert_fail.py ("kg_sync:" + $step.name) $code $errL 1>$null 2>$null
            exit 1
        }
    }
    ('kg_sync OK ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 0
} catch {
    ('kg_sync ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
