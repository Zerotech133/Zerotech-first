# 计划任务注册表↔脚本对应审计（只读）

- 审计时点：2026-10-08 09:42:50
- 范围：全机非 Microsoft 计划任务（TaskPath 不含 \Microsoft\）
- 方法：Get-ScheduledTask 全量导出 → 脚本引用提取 → 文件存在性核验 → 状态/结果码分类；反向对账 quant_backtest+portal 调度型命名脚本（start_/watchdog/sync_/register_ 等前缀）
- 铁律：本审计只读，不修改/删除任何任务

## 总量卡

| 指标 | 值 |
|------|------|
| 非微软任务总数 | **105**（评审@10-08 晨为 98 → 一日再增 7，范围失控持续） |
| ✅ OK（脚本在、最近跑成） | 66 |
| ℹ NO_SCRIPT（动作无脚本引用，内联命令类） | 22 |
| 🟠 SCRIPT_MISSING（声明脚本不存在） | **2** |
| ⚠ LASTRUN_FAILED（最近一次结果码异常） | **10** |
| ◾ TRIGGER_REFUSED（触发被拒：条件不满足/已有实例，非脚本失败） | 6 |
| 🗑 DISABLED（已禁用） | 3 |
| 👻 孤儿调度脚本（无任务引用） | 6 |

## 🟠 脚本缺失任务（注册表指向已不存在的文件）

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `OpenClaw Gateway` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | 缺失：C:\Users\Administrator\.openclaw\gateway.cmd |
| `量化前戏-FastAPI-Server` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | 缺失：c:\Users\Administrator\WorkBuddy\20260324114946\quant-foreplay\scripts\autostart_server.bat |

## ⚠ 最近运行失败任务（结果码非 0/运行中/未运行/被拒）

说明：0x41301=正在运行，0x41303=尚未运行，0x41306=被终止，0x800710E0=触发被拒（条件不满足/已有实例）——均不计入失败。

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `AI工作站Ollama服务` | Ready | 登录 | 2026-10-07 07:01:24 | 0xFFFFFFFF | 0xFFFFFFFF |
| `AI盘前分析` | Ready | 每周 | 2026-10-07 14:30:00 | 0x00000001 | 0x00000001 |
| `ChanlunScanNotify` | Ready | 每日 | 2026-10-07 15:41:01 | 0x00000003 | 0x00000003 |
| `ChanlunScanUS` | Ready | 每日 | 2026-10-08 09:00:01 | 0x8007042B | 0x8007042B |
| `CryptoSync` | Ready | 每日 | 2026-10-08 08:10:01 | 0x8007042B | 0x8007042B |
| `JEVBackfill-Hourly` | Ready | 定时 | 2026-10-08 08:57:36 | 0x8007042B | 0x8007042B |
| `OpenClaw Gateway` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | 0x00000001 |
| `RtdxRtMinPull` | Ready | 定时 | 2026-10-05 18:45:00 | 0x8007042B | 0x8007042B |
| `_sentinelEOD` | Ready | 定时 | 2026-10-05 14:16:19 | 0x8007042B | 0x8007042B |
| `量化前戏-FastAPI-Server` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | 0x00000001 |

## ◾ 触发被拒任务（0x800710E0，多为看门狗批量触发时已有实例）

实证口径：AI工作站门户API 上次触发即此码，但任务 Running 且 8091 健康——被拒≠服务异常。连续多日被拒且服务也未运行的才需排查。

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `AI工作站告警转发` | Ready | 每日,登录 | 2026-10-08 09:33:16 | 0x800710E0 | E:\老田的AI助手\jev-eval\alert_forward.ps1 |
| `AI工作站引擎探活` | Ready | 定时 | 2026-10-08 09:33:16 | 0x800710E0 | 内联动作 |
| `AI工作站持仓异动监控` | Ready | 定时 | 2026-10-08 09:33:16 | 0x800710E0 | C:\aios\quant_backtest\start_quant_watch.ps1 |
| `AI工作站门户API` | Running | 开机,每日 | 2026-10-08 09:40:01 | 0x800710E0 | C:\aios\portal\start_portal_api.ps1 |
| `AI盘后复盘` | Ready | 每日 | 2026-10-08 09:33:16 | 0x800710E0 | 内联动作 |
| `FutQuantNotify` | Ready | 定时 | 2026-10-07 01:47:07 | 0x800710E0 | C:\aios\quant_backtest\start_futquant_notify.ps1 |

## 🗑 已禁用任务（留档不跑）

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `OpenClaw Gateway` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | C:\Users\Administrator\.openclaw\gateway.cmd |
| `Optimize Push Notification Data File-S-1-5-21-235612444-2096941894-2179544483-500` | Disabled | 空闲 | 2026-03-01 01:59:16 | 0x00000000 | 无脚本引用 |
| `量化前戏-FastAPI-Server` | Disabled | 登录 | 2026-09-24 17:18:44 | 0x00000001 | c:\Users\Administrator\WorkBuddy\20260324114946\quant-foreplay\scripts\autostart_server.bat |

## ✅ 正常任务（脚本存在且最近结果正常）

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `AIColAudit` | Ready | 每日 | 2026-10-08 06:10:01 | 0x00000000 | C:\aios\quant_backtest\col_audit_task.ps1 |
| `AI个股分析轮询` | Ready | 定时 | 2026-10-08 09:36:29 | 0x00000000 | e:\老田的AI助手\news_collector\stock_analyze.py |
| `AI工作站35b研判服务` | Ready | 无触发器 | 2026-10-06 04:41:34 | 0x00000000 | E:\老田的AI助手\llamacpp_35b\launch_35b.py |
| `AI工作站统一日报` | Ready | 每日 | 2026-10-08 03:00:01 | 0x00000000 | C:\aios\news_collector\start_daily_report.ps1 |
| `AI工作站预警推送` | Ready | 每日 | 2026-10-07 16:05:01 | 0x00000000 | E:\老田的AI助手\news_collector\alert_push.py |
| `AI工作站预警推送晨` | Ready | 每日 | 2026-10-08 07:10:01 | 0x00000000 | E:\老田的AI助手\news_collector\alert_push.py |
| `AI工作站预警监听` | Ready | 定时 | 2026-10-08 09:30:01 | 0x00000000 | E:\老田的AI助手\news_collector\alert_watch.py |
| `BkWarden` | Ready | 定时 | 2026-10-08 09:37:01 | 0x00000000 | C:\Temp\bk_warden.ps1 |
| `ChanlunRefill` | Ready | 每日 | 2026-10-08 06:30:01 | 0x00000000 | C:\aios\quant_backtest\start_chanlun_scan.ps1 |
| `ChanlunSample` | Ready | 每日 | 2026-10-08 06:00:01 | 0x00000000 | C:\Temp\chanlun_sample_wrapper.ps1 |
| `ChanlunScan` | Ready | 每日 | 2026-10-07 15:35:01 | 0x00000000 | C:\aios\quant_backtest\start_chanlun_scan.ps1 |
| `ChanlunScanHK` | Ready | 每日 | 2026-10-07 17:30:01 | 0x00000000 | C:\aios\quant_backtest\start_chanlun_scan_hk.ps1 |
| `DockerWatchdog` | Ready | 开机,定时 | 2026-10-08 09:37:13 | 0x00000000 | C:\Temp\docker_watchdog_last.ps1 |
| `FeishuBotWatchdog` | Ready | 定时 | 2026-10-08 09:40:01 | 0x00000000 | E:\\老田的AI助手\\portal\\start_feishu_bot.py |
| `FutQuant` | Ready | 每日 | 2026-10-08 09:30:01 | 0x00000000 | C:\aios\quant_backtest\start_fut_daily.ps1 |
| `GoldShadow` | Ready | 每日 | 2026-10-07 18:30:01 | 0x00000000 | C:\aios\quant_backtest\start_gold_shadow.ps1 |
| `GubaCrawl` | Ready | 每日 | 2026-10-07 17:50:00 | 0x00000000 | E:\老田的AI助手\quant_backtest\start_guba_crawl.ps1 |
| `JevEvalMonthly` | Ready | 每日 | 2026-10-07 20:27:16 | 0x00000000 | C:\aios\jev-eval\run_eval.ps1 |
| `JevFOCUS首期报告` | Ready | 定时 | 2026-09-29 02:35:00 | 0x00000000 | E:\老田的AI助手\jev-eval\remind_report.ps1 |
| `JevReviewBackfill` | Ready | 每日 | 2026-10-07 20:30:00 | 0x00000000 | C:\aios\jev-eval\run_backfill.ps1 |
| `K266-DocServices` | Ready | 定时 | 2026-10-08 09:41:01 | 0x00000000 | E:\老田的AI助手\quant_backtest\k266_services.ps1 |
| `K33ShadowCheck` | Ready | 每周 | 2026-10-02 16:06:31 | 0x00000000 | C:\aios\quant_backtest\start_k33_shadow.ps1 |
| `KG-Cl1Sync` | Ready | 每日 | 2026-10-07 18:10:01 | 0x00000000 | C:\aios\quant_backtest\start_cl1_sync.ps1 |
| `KG-TdxSync` | Ready | 每日 | 2026-10-07 17:40:01 | 0x00000000 | E:\KGStockAnalysis\ops\kg_tdx_sync_launcher.py |
| `KG-TushareSync` | Ready | 每日 | 2026-10-08 00:10:01 | 0x00000000 | E:\KGStockAnalysis\ops\kg_tushare_sync_launcher.py |
| `KgDailySync` | Ready | 每日 | 2026-10-08 06:20:01 | 0x00000000 | C:\aios\quant_backtest\start_kg_sync.ps1 |
| `KgDigest` | Ready | 每日 | 2026-10-08 07:42:07 | 0x00000000 | C:\aios\quant_backtest\start_kg_digest.ps1 |
| `KgExtractShadow` | Ready | 每日 | 2026-10-08 06:50:01 | 0x00000000 | C:\aios\quant_backtest\start_kg_extract_shadow.ps1 |
| `KgResonanceNotify` | Ready | 每日 | 2026-10-08 08:05:01 | 0x00000000 | C:\aios\quant_backtest\start_resonance_notify.ps1 |
| `MissCheck` | Ready | 每日 | 2026-10-07 21:30:01 | 0x00000000 | C:\aios\quant_backtest\start_miss_check.ps1 |
| `MorningReport` | Ready | 每日 | 2026-10-08 07:00:01 | 0x00000000 | C:\aios\quant_backtest\start_morning_report.ps1 |
| `NamechangeSync` | Ready | 每周 | 1999-11-30 00:00:00 | 0x00041303 | C:\aios\quant_backtest\start_namechange.ps1 |
| `NineDimScan` | Ready | 每日 | 2026-10-08 06:40:01 | 0x00000000 | C:\aios\quant_backtest\start_nine_dim.ps1 |
| `NoticeBackfill` | Ready | 每日 | 2026-10-08 06:45:01 | 0x00000000 | E:\老田的AI助手\quant_backtest\start_notice_backfill.ps1 |
| `PG-Robot` | Running | 登录 | 2026-10-08 01:12:37 | 0x00041301 | C:\aios\quant_backtest\p_g_robot.py |
| `PostHolidayCheck` | Ready | 每日 | 2026-10-08 08:30:01 | 0x00000000 | C:\aios\quant_backtest\start_post_holiday.ps1 |
| `RiskGuardR3Weekly` | Ready | 每周 | 1999-11-30 00:00:00 | 0x00041303 | E:\KGStockAnalysis\riskguard_r3_weekly.ps1 |
| `RiskGuardShadowDaily` | Ready | 每日 | 1999-11-30 00:00:00 | 0x00041303 | E:\KGStockAnalysis\riskguard_shadow_daily.ps1 |
| `RpsDaily` | Ready | 每周 | 2026-10-07 16:05:00 | 0x00000000 | C:\aios\quant_backtest\start_rps_daily.ps1 |
| `SellPointAI` | Ready | 每日 | 2026-10-07 15:52:01 | 0x00000000 | E:\老田的AI助手\quant_backtest\sellpoint_ai.py |
| `StockCorrBacktest` | Ready | 每日 | 2026-10-07 20:35:01 | 0x00000000 | C:\aios\news_collector\start_sentiment_bt.ps1 |
| `StrongEod` | Ready | 每日 | 2026-10-07 20:48:20 | 0x00000000 | C:\aios\quant_backtest\strong_scan.py; C:\aios\quant_backtest\strong_card.py |
| `SubPushEod` | Ready | 每日 | 1999-11-30 00:00:00 | 0x00041303 | C:\aios\quant_backtest\subscribe.py |
| `SubPushIntraday` | Ready | 每日 | 2026-10-08 09:30:01 | 0x00000000 | C:\aios\quant_backtest\subscribe.py |
| `TbeStats` | Ready | 每日 | 2026-10-08 06:45:01 | 0x00000000 | C:\aios\quant_backtest\start_tbe_stats.ps1 |
| `TdxSync` | Ready | 每日 | 2026-10-07 17:30:01 | 0x00000000 | C:\aios\quant_backtest\start_tdx_sync.ps1 |
| `TriplesOutcomeBackfill` | Ready | 每日 | 2026-10-07 15:35:01 | 0x00000000 | C:\aios\quant_backtest\start_triples_backfill.ps1 |
| `WeakDailyHealth` | Ready | 每日 | 2026-10-07 15:55:01 | 0x00000000 | C:\aios\quant_backtest\weak_track_health.py |
| `WeakMonthlyArchive` | Ready | 无触发器 | 2026-10-03 03:35:27 | 0x00000000 | C:\aios\quant_backtest\weak_track_archive.py |
| `WeakMonthlyGuide` | Ready | 无触发器 | 1999-11-30 00:00:00 | 0x00041303 | C:\aios\quant_backtest\weak_track_guide.py |
| `WeakMonthlyLearn` | Ready | 无触发器 | 2026-10-03 03:15:39 | 0x00000000 | C:\aios\quant_backtest\weak_track_learn.py |
| `WeakMonthlyReview` | Ready | 无触发器 | 2026-10-07 20:48:09 | 0x00000000 | C:\aios\quant_backtest\weak_monthly_review.py |
| `WeakMonthlySummary` | Ready | 无触发器 | 2026-10-02 22:16:19 | 0x00000000 | C:\aios\quant_backtest\weak_track_summary.py |
| `WeakScan` | Ready | 每周 | 2026-10-05 15:35:00 | 0x00000000 | C:\aios\quant_backtest\start_weak_scan.ps1 |
| `WeakTrackCheck` | Ready | 每日 | 2026-10-07 15:30:01 | 0x00000000 | C:\aios\quant_backtest\start_weak_track.ps1 |
| `WeakWeeklyReport` | Ready | 每周 | 2026-10-05 15:50:00 | 0x00000000 | C:\aios\quant_backtest\weak_track_weekly.py |
| `ZhiceWatchdog` | Ready | 定时 | 2026-10-08 09:38:01 | 0x00000000 | e:\老田的AI助手\zhice.ps1 |
| `公众号选题撰文` | Ready | 每日 | 2026-10-08 09:30:01 | 0x00000000 | C:\aios\gzh\gzh_writer.py |
| `自选股行情快照` | Ready | 每周 | 2026-10-08 09:37:17 | 0x00000000 | C:\aios\news_collector\start_quote.ps1 |
| `量化盘前作战计划` | Ready | 每周 | 2026-10-07 14:15:00 | 0x00000000 | C:\aios\quant_backtest\start_quant_morning.ps1 |
| `金融新闻AI晨报` | Ready | 每日 | 2026-10-08 08:00:01 | 0x00000000 | C:\aios\news_collector\start_news_ai.ps1 |
| `金融新闻收盘复盘` | Ready | 每周 | 2026-10-07 15:30:00 | 0x00000000 | C:\aios\news_collector\start_news_review.ps1 |
| `金融新闻日报` | Ready | 每日 | 2026-10-08 08:30:01 | 0x00000000 | C:\aios\news_collector\start_news_digest.ps1 |
| `金融新闻采集` | Ready | 每日,登录 | 2026-10-08 09:30:01 | 0x00000000 | C:\aios\news_collector\start_news_collect.ps1 |
| `金融量化季度调参` | Ready | 无触发器 | 2026-10-01 10:00:00 | 0x00000000 | C:\aios\quant_backtest\start_quant_retune.ps1 |
| `金融量化每日扫描` | Ready | 每日 | 2026-10-07 16:00:01 | 0x00000000 | C:\aios\quant_backtest\start_bt_scan.ps1 |

## ℹ 无脚本引用任务（内联命令/系统动作）

| 任务 | 状态 | 触发 | 上次运行 | 结果码 | 脚本/说明 |
|------|------|------|----------|--------|-----------|
| `AI工作站N1晨检` | Ready | 每日 | 2026-10-08 07:30:01 | 0x00000000 | 内联动作 |
| `AI工作站异地同步` | Ready | 每日 | 2026-10-08 04:30:01 | 0x00000000 | 内联动作 |
| `AI工作站每日备份` | Ready | 每日 | 2026-10-08 06:47:27 | 0x00000000 | 内联动作 |
| `AI量化RPS共振` | Ready | 每日 | 2026-10-07 23:35:01 | 0x00000000 | 内联动作 |
| `AI量化模拟建仓` | Ready | 每日 | 2026-10-07 23:50:01 | 0x00000000 | 内联动作 |
| `DockerDesktopLaunch` | Ready | 无触发器 | 2026-10-07 12:37:29 | 0x00000000 | 内联动作 |
| `Git for Windows Updater` | Ready | 每日 | 2026-10-08 06:29:32 | 0x00000000 | 内联动作 |
| `MicrosoftEdgeUpdateTaskMachineCore{D7272775-FE9D-4F95-9699-56AD01BD450F}` | Ready | 登录,每日 | 2026-10-07 19:44:02 | 0x00000000 | 内联动作 |
| `MicrosoftEdgeUpdateTaskMachineUA{C3B339CC-AC5E-4FFE-AB65-410EE81B2EE9}` | Ready | 每日 | 2026-10-08 09:27:16 | 0x00000000 | 内联动作 |
| `OneDrive Reporting Task-S-1-5-21-235612444-2096941894-2179544483-500` | Ready | 定时 | 2026-10-08 08:25:48 | 0x00000000 | 内联动作 |
| `OneDrive Standalone Update Task-S-1-5-21-235612444-2096941894-2179544483-500` | Ready | 定时 | 1999-11-30 00:00:00 | 0x00041303 | 内联动作 |
| `OneDrive Startup Task-S-1-5-21-235612444-2096941894-2179544483-500` | Ready | 登录 | 2026-10-07 01:47:47 | 0x00000000 | 内联动作 |
| `PytestWeekly` | Ready | 每周 | 2026-10-07 20:47:58 | 0x00000000 | 内联动作 |
| `TushareSync` | Ready | 每日 | 2026-10-07 23:30:01 | 0x00000000 | 内联动作 |
| `WpsUpdateLogonTask_Administrator` | Ready | 登录 | 2026-10-07 01:59:47 | 0x00000000 | 内联动作 |
| `WpsUpdateTask_Administrator` | Ready | 定时 | 2026-10-08 09:37:57 | 0x00000000 | 内联动作 |
| `WpsWakeWnsLogonTask` | Ready | 登录 | 2026-10-07 01:38:46 | 0x00000000 | 内联动作 |
| `GoogleUpdaterTaskSystem156.0.8067.0{9A7E719E-3674-4683-BB45-0F0EB94E60DA}` | Ready | 登录,每日 | 2026-10-08 08:47:55 | 0x00000000 | 内联动作 |
| `RunPlatformExperienceHelperOnUnlock` | Ready | SessionStateChange | 2026-10-08 09:28:11 | 0x00000000 | 内联动作 |
| `RunPlatformExperienceHelper_Daily` | Ready | 每日 | 2026-10-07 11:22:55 | 0x00000000 | 内联动作 |
| `RunPlatformExperienceHelper_Metrics` | Ready | 定时 | 2026-07-09 11:20:31 | 0x00000000 | 内联动作 |
| `QuarkUpdaterTaskUser1.0.0.21{DBA216F7-2C04-4216-B37F-38504C049690}` | Ready | 登录,每日 | 2026-10-08 09:01:10 | 0x00000000 | 内联动作 |

## 👻 孤儿调度脚本候选（脚本在、注册表无任何任务引用）

> 浅扫口径：portal 与 quant_backtest 目录下 start_/watchdog/sync_/register_/sched 前缀脚本；文件名（小写）未出现在任何任务动作文本中。候选≠僵尸——可能由其他目录任务、wsl/容器或手动流程引用，处置前须逐个确认。

- `E:\老田的AI助手\portal\register_feishu_watchdog_task.py`
- `E:\老田的AI助手\portal\start_portal_nginx.ps1`
- `E:\老田的AI助手\portal\sync_publish.py`
- `E:\老田的AI助手\quant_backtest\k266_clip_watch.py`
- `E:\老田的AI助手\quant_backtest\test_weak_watch.py`
- `E:\老田的AI助手\quant_backtest\weak_track_watch.py`

## 台账对照

- INTEL_RD_PLAN 资产表：「计划任务 6→实 98 @10-08 评审」→ 本次实核 **105**（含 3 禁用）；台账行按本报告更新
- 证据：C:/Temp/sched_tasks_dump.json（全量导出原始件）+ deliverables/sched-task-audit-2026-10-08.json（本审计结构化结果）