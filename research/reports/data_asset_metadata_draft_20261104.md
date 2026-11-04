# 数据仓注册/暴露元数据草案（task-162 · W4 · DRAFT ONLY）

- 生成：2026-11-04T03:15:35+08:00；as_of 2026-11-03；**本文件不写 catalog/registry、不改数据根**
- 输入：agent_platform/registry/data_asset_decisions_p1.json；research/reports/datawarehouse_coverage_p1_20261103.json；catalog v35（256 表）；manifest 修复草案
- 汇总：注册草案 **48** 项，其中**可自动生成 36 项（75.0%）**，需人工 12 项；暴露项 242 项 → Top50 分 5 批；manifest lifecycle 建议 3 项

## 一、48 项注册 catalog 草案（按可自动生成优先）

| # | id | label | 域 | layout | schema字段数 | date_col | 主键 | freq | 置信 | 自动 | 证据采样 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | automation_runs | automation_runs | 平台运行 | flat_files | 11 | trade_date | ts_code,trade_date | unknown | high | ✅ | C:/aios/automation_runs |
| 2 | news_ai_briefs | news_db.ai_briefs | 平台运行 | sqlite_table | 7 | date | date | D | high | ✅ | C:/项目/老田的AI助手/news_collector/news.db |
| 3 | news_ai_reviews | news_db.ai_reviews | 平台运行 | sqlite_table | 7 | date | date | D | high | ✅ | C:/项目/老田的AI助手/news_collector/news.db |
| 4 | news_jev_review_queue | news_db.jev_review_queue | 平台运行 | sqlite_table | 13 | queued_at | id | event | high | ✅ | C:/项目/老田的AI助手/news_collector/news.db |
| 5 | news_quotes | news_db.quotes | 平台运行 | sqlite_table | 11 | created_at | code,ts | event | high | ✅ | C:/项目/老田的AI助手/news_collector/news.db |
| 6 | news_runs | news_db.runs | 平台运行 | sqlite_table | 7 | ts | id | event | high | ✅ | C:/项目/老田的AI助手/news_collector/news.db |
| 7 | paper_decision_snapshots | paper.decision_snapshots | 平台运行 | sqlite_table | 14 | ts | id | event | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 8 | paper_equity_marks | paper.equity_marks | 平台运行 | sqlite_table | 13 | date | date,account_id | D | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 9 | paper_ledger | paper.ledger | 平台运行 | sqlite_table | 13 | ts | id | event | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 10 | paper_orders | paper.orders | 平台运行 | sqlite_table | 25 | created_at | id | event | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 11 | paper_positions | paper.positions | 平台运行 | sqlite_table | 7 | updated_at | account_id,stock | event | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 12 | paper_positions_snapshot | paper.positions_snapshot | 平台运行 | sqlite_table | 10 | date | date,account_id,stock | D | high | ✅ | C:/项目/老田的AI助手/k_stock/data/paper.db |
| 13 | quant_chanlun_pool | quant_db.chanlun_pool | 平台运行 | sqlite_table | 19 | dt | code,type,dt | D | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 14 | quant_nine_dim_fc | quant_db.nine_dim_fc | 平台运行 | sqlite_table | 3 | — | code | static | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 15 | quant_nine_dim_fc_meta | quant_db.nine_dim_fc_meta | 平台运行 | sqlite_table | 2 | value | key | event | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 16 | quant_nine_dim_fina | quant_db.nine_dim_fina | 平台运行 | sqlite_table | 4 | fetched_at | code | event | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 17 | quant_nine_dim_rc | quant_db.nine_dim_rc | 平台运行 | sqlite_table | 4 | — | code | static | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 18 | quant_nine_dim_rc_meta | quant_db.nine_dim_rc_meta | 平台运行 | sqlite_table | 2 | value | key | event | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/quant.db |
| 19 | research_hypotheses | hypotheses.hypotheses | 学习派生 | sqlite_table | 12 | created_at | id | event | high | ✅ | C:/项目/老田的AI助手/research/hypotheses.db |
| 20 | xq_xq_status_classified | xueqiu_info.xq_status_classified | 开源情报 | sqlite_table | 12 | sentiment | status_id | event | high | ✅ | C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db |
| 21 | yt_bridge_faq_comment | yt_learning_db.bridge_faq_comment | 学习派生 | sqlite_table | 3 | — | faq_id,ord | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 22 | yt_bridge_video_topic | yt_learning_db.bridge_video_topic | 学习派生 | sqlite_table | 2 | — | video_id,topic | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 23 | yt_dim_channel | yt_learning_db.dim_channel | 学习派生 | sqlite_table | 7 | total_views | channel_id | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 24 | yt_dim_topic | yt_learning_db.dim_topic | 学习派生 | sqlite_table | 2 | — | topic | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 25 | yt_dim_video | yt_learning_db.dim_video | 学习派生 | sqlite_table | 12 | upload_date | video_id | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 26 | yt_distill | yt_distill（知识提炼） | 学习派生 | json_docs | 10 | upload_date | upload_date | event | high | ✅ | C:/项目/知识库/35_知识提炼 |
| 27 | yt_fact_comment | yt_learning_db.fact_comment | 学习派生 | sqlite_table | 8 | collected_at | video_id,comment_id | event | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 28 | yt_fact_faq_semantic | yt_learning_db.fact_faq_semantic | 学习派生 | sqlite_table | 15 | — | faq_id | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 29 | yt_fact_knowledge_unit | yt_learning_db.fact_knowledge_unit | 学习派生 | sqlite_table | 5 | — | unit_id | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 30 | yt_fact_transcript | yt_learning_db.fact_transcript | 学习派生 | sqlite_table | 4 | fetched_at | video_id | event | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 31 | yt_fact_transcript_seg | yt_learning_db.fact_transcript_seg | 学习派生 | sqlite_table | 8 | — | video_id,seq | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 32 | yt_fact_transcript_signal | yt_learning_db.fact_transcript_sig | 学习派生 | sqlite_table | 9 | — | video_id,seq,t_start_s,signal_type,term | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 33 | yt_meta_etl | yt_learning_db.meta_etl | 学习派生 | sqlite_table | 4 | created_at | run_id | event | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 34 | yt_meta_faq_build | yt_learning_db.meta_faq_build | 学习派生 | sqlite_table | 2 | value | key | static | high | ✅ | C:/项目/知识库/00_数据仓/yt_learning.db |
| 35 | yt_rag | yt_rag（4,926 chunks + packed 62,40 | 学习派生 | json_docs | 4 | updated_at | updated_at | event | high | ✅ | C:/项目/知识库/50_RAG |
| 36 | yt_transcripts | yt_transcripts（转写） | 开源情报 | json_docs | 11 | upload_date | upload_date | event | high | ✅ | C:/项目/知识库/20_转写 |
| 37 | research_shadow | research_shadow | 学习派生 | unknown | 0 | — | — | — | low | ❌ | research/shadow |
| 38 | yt_briefs | yt_briefs（简报） | 学习派生 | flat_files | 0 | — | — | — | low | ❌ | C:/项目/知识库/70_简报 |
| 39 | yt_cleaned_comments | yt_cleaned_comments（清洗+PII脱敏） | 开源情报 | unknown | 11 | — | — | unknown | medium | ❌ | C:/项目/知识库/20_清洗 |
| 40 | yt_faq | yt_faq（FAQ 语义归并 133 条） | 学习派生 | json_docs | 3 | — | — | event | medium | ❌ | C:/项目/知识库/37_FAQ挖掘 |
| 41 | yt_index | yt_index（62,401 docs bge-m3） | 学习派生 | json_docs | 4 | — | — | event | medium | ❌ | C:/项目/知识库/40_索引 |
| 42 | yt_knowledge_units | yt_knowledge_units（知识单元） | 学习派生 | json_docs | 6 | — | — | event | medium | ❌ | C:/项目/知识库/30_知识单元 |
| 43 | yt_learning.db | yt_learning.db（星型模型 9 表，统一取数入口） | 学习派生 | flat_files | 0 | — | — | — | low | ❌ | C:/项目/知识库/00_数据仓 |
| 44 | yt_learning_cards | yt_learning_cards（学习卡） | 学习派生 | flat_files | 0 | — | — | — | low | ❌ | C:/项目/知识库/60_学习卡 |
| 45 | yt_normalized | yt_normalized（videos/comments/chan | 开源情报 | flat_files | 9 | — | — | unknown | medium | ❌ | C:/项目/知识库/10_规范化 |
| 46 | yt_raw_zip | yt_raw_zip（948 ZIP/清单/sha256） | 开源情报 | zip_archive | 0 | — | — | event | low | ❌ | C:/项目/知识库/01_数据分包 |
| 47 | yt_reports | yt_reports（报告） | 学习派生 | json_docs | 4 | — | — | event | medium | ❌ | C:/项目/知识库/90_报告 |
| 48 | yt_review | yt_review（复核） | 平台运行 | json_docs | 4 | — | — | event | medium | ❌ | C:/项目/知识库/80_复核 |

### 需人工补全清单

| id | label | kind | 缺失字段 | 原因 |
|---|---|---|---|---|
| research_shadow | research_shadow | dir | schema,freq,date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_raw_zip | yt_raw_zip（948 ZIP/清单/sha256） | corpus_layer | schema,date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_normalized | yt_normalized（videos/comments/ch | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_cleaned_comments | yt_cleaned_comments（清洗+PII脱敏） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_knowledge_units | yt_knowledge_units（知识单元） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_faq | yt_faq（FAQ 语义归并 133 条） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_index | yt_index（62,401 docs bge-m3） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_learning_cards | yt_learning_cards（学习卡） | corpus_layer | schema,freq,date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_briefs | yt_briefs（简报） | corpus_layer | schema,freq,date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_review | yt_review（复核） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_reports | yt_reports（报告） | corpus_layer | date_col,primary_key | 字段无法自动推断，需人工确认 |
| yt_learning.db | yt_learning.db（星型模型 9 表，统一取数入口） | corpus_layer | schema,freq,date_col,primary_key | 字段无法自动推断，需人工确认 |

## 二、242 暴露项 4 维优先级（Top50 / 5 批）

- 权重：价值 0.4 / 大小 0.2 / 时效 0.2 / 复用 0.2；Top50 按总分排序，每 10 个一批。

| 批 | 排名 | label | 域 | size_mb | latest | value | size | fresh | reuse | 总分 |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 1 | tushare.express | 接口数据 | 0.3 | 2026-10-28 | 1.0 | 0.8575 | 1.0 | 1.0 | 0.9715 |
| B1 | 2 | tushare.index_weight | 接口数据 | 1.5 | 2026-11-01 | 1.0 | 0.8024 | 1.0 | 1.0 | 0.9605 |
| B1 | 3 | tushare.fx_daily | 接口数据 | 0.1 | 2026-10-30 | 1.0 | 0.8689 | 1.0 | 0.928 | 0.9594 |
| B1 | 4 | tushare.index_global | 接口数据 | 2.6 | 2026-11-01 | 1.0 | 0.7628 | 1.0 | 0.9718 | 0.9469 |
| B1 | 5 | tushare.moneyflow_hsgt | 接口数据 | 0.1 | 2026-10-30 | 1.0 | 0.8689 | 1.0 | 0.8333 | 0.9404 |
| B1 | 6 | tushare.major_news | 接口数据 | 2.1 | 2026-11-01 | 1.0 | 0.7799 | 1.0 | 0.866 | 0.9292 |
| B1 | 7 | tushare.hk_basic | 接口数据 | 8.4 | 2026-11-01 | 1.0 | 0.6168 | 1.0 | 0.8819 | 0.8997 |
| B1 | 8 | tushare.index_member_all | 接口数据 | 0.1 | 2026-10-29 | 1.0 | 0.8689 | 1.0 | 0.6236 | 0.8985 |
| B1 | 9 | tushare.shibor | 接口数据 | 0.2 | 2026-11-01 | 0.8 | 0.8631 | 1.0 | 1.0 | 0.8926 |
| B1 | 10 | tdx.ts_margin | 接口数据 | 0.3 | 2026-10-31 | 0.8 | 0.8575 | 1.0 | 1.0 | 0.8915 |
| B2 | 11 | tushare.shibor_lpr | 接口数据 | 0.4 | 2026-11-01 | 0.8 | 0.8521 | 1.0 | 1.0 | 0.8904 |
| B2 | 12 | tushare.cb_basic | 接口数据 | 7.3 | 2026-11-01 | 1.0 | 0.6399 | 1.0 | 0.7817 | 0.8843 |
| B2 | 13 | tdx.ts_namechange | 接口数据 | 0.1 | 2026-10-29 | 0.8 | 0.8689 | 1.0 | 0.8165 | 0.8571 |
| B2 | 14 | tushare.limit_list_d | 接口数据 | 5.4 | 2026-11-01 | 0.8 | 0.6838 | 1.0 | 1.0 | 0.8568 |
| B2 | 15 | tushare.sw_classify_l1 | 接口数据 | 0.6 | 2026-11-01 | 0.8 | 0.8419 | 1.0 | 0.8165 | 0.8517 |
| B2 | 16 | tushare.stk_holdernumber | 接口数据 | 24.3 | 2026-10-30 | 1.0 | 0.3713 | 1.0 | 0.8165 | 0.8376 |
| B2 | 17 | tushare.sf_month | 接口数据 | 0.3 | 2026-11-01 | 0.8 | 0.8575 | 1.0 | 0.6455 | 0.8206 |
| B2 | 18 | tdx.ts_adj_factor | 接口数据 | 15.1 | 2026-10-30 | 0.8 | 0.4984 | 1.0 | 1.0 | 0.8197 |
| B2 | 19 | tushare.us_basic | 接口数据 | 33.1 | 2026-11-01 | 1.0 | 0.2701 | 1.0 | 0.8165 | 0.8173 |
| B2 | 20 | tushare.stk_managers | 接口数据 | 7.2 | 2026-09-30 | 1.0 | 0.6421 | 0.5 | 0.928 | 0.814 |
| B3 | 21 | tushare.cyq_perf | 接口数据 | 44.7 | 2026-11-01 | 1.0 | 0.155 | 1.0 | 0.8975 | 0.8105 |
| B3 | 22 | knowledge_docs | 学习派生 | 12.3 | 2026-11-01 | 0.75 | 0.5441 | 1.0 | 1.0 | 0.8088 |
| B3 | 23 | tushare.research_report | 接口数据 | 19.3 | 2026-11-01 | 0.8 | 0.4368 | 1.0 | 1.0 | 0.8074 |
| B3 | 24 | tushare.adj_factor | 接口数据 | 234.6 | 2026-10-30 | 1.0 | 0.0 | 1.0 | 1.0 | 0.8 |
| B3 | 25 | tushare.anns_d | 接口数据 | 279.3 | 2026-11-01 | 1.0 | 0.0 | 1.0 | 1.0 | 0.8 |
| B3 | 26 | tushare.margin_detail | 接口数据 | 195.9 | 2026-10-30 | 1.0 | 0.0 | 1.0 | 1.0 | 0.8 |
| B3 | 27 | tushare.report_rc | 接口数据 | 85.5 | 2026-10-31 | 1.0 | 0.0 | 1.0 | 1.0 | 0.8 |
| B3 | 28 | tushare.stock_company | 接口数据 | 299.7 | 2026-11-01 | 1.0 | 0.0 | 1.0 | 1.0 | 0.8 |
| B3 | 29 | cnopendata | 接口数据 | 9.4 | 2026-10-21 | 0.8 | 0.5969 | 0.8 | 1.0 | 0.7994 |
| B3 | 30 | tdx.ts_rt_min | 接口数据 | 11.4 | 2026-10-23 | 0.8 | 0.5598 | 0.8 | 1.0 | 0.792 |
| B4 | 31 | major_news | 开源情报 | 2.1 | 2026-11-01 | 0.675 | 0.7799 | 1.0 | 0.8165 | 0.7893 |
| B4 | 32 | tushare.top10_floatholders | 接口数据 | 15.7 | 2026-09-30 | 1.0 | 0.4892 | 0.5 | 0.9129 | 0.7804 |
| B4 | 33 | tushare.hk_hold | 接口数据 | 66.6 | 2026-10-30 | 1.0 | 0.0 | 1.0 | 0.866 | 0.7732 |
| B4 | 34 | tdx.ts_index_weight | 接口数据 | 0.0 | 2026-10-13 | 0.8 | 0.875 | 0.8 | 0.5528 | 0.7656 |
| B4 | 35 | tdx.ts_trade_cal | 接口数据 | 0.1 | 2026-10-13 | 0.8 | 0.8689 | 0.8 | 0.5528 | 0.7643 |
| B4 | 36 | tushare.quarantine_prod_overwrite | 接口数据 | 0.7 | 2026-10-17 | 0.8 | 0.837 | 0.8 | 0.5774 | 0.7629 |
| B4 | 37 | research_report | 开源情报 | 19.3 | 2026-11-01 | 0.675 | 0.4368 | 1.0 | 1.0 | 0.7574 |
| B4 | 38 | tdx.ts_ths_member | 接口数据 | 1.7 | 2026-10-13 | 0.8 | 0.7946 | 0.8 | 0.5774 | 0.7544 |
| B4 | 39 | tushare.stk_pledge | 接口数据 | 0.1 | 2026-10-19 | 0.8 | 0.8689 | 0.8 | 0.5 | 0.7538 |
| B4 | 40 | tdx.ts_emotion_daily | 接口数据 | 0.0 | 2026-10-15 | 0.8 | 0.875 | 0.8 | 0.4714 | 0.7493 |
| B5 | 41 | tdx.ts_global_fut | 接口数据 | 0.7 | 2026-10-15 | 0.8 | 0.837 | 0.8 | 0.5 | 0.7474 |
| B5 | 42 | tdx.ts_sge_basic | 接口数据 | 0.0 | 2026-10-13 | 0.8 | 0.875 | 0.8 | 0.441 | 0.7432 |
| B5 | 43 | tdx.dknife_snap | 接口数据 | 12.9 | 2026-10-13 | 0.8 | 0.534 | 0.8 | 0.7638 | 0.7396 |
| B5 | 44 | tushare.sw_l1_member | 接口数据 | 0.2 | 2026-09-28 | 0.8 | 0.8631 | 0.5 | 0.6872 | 0.7301 |
| B5 | 45 | tdx.ts_emotion_ladder | 接口数据 | 0.1 | 2026-10-15 | 0.8 | 0.8689 | 0.8 | 0.3727 | 0.7283 |
| B5 | 46 | tdx.ts_cb_call | 接口数据 | 0.1 | 2026-10-31 | 0.8 | 0.8689 | 1.0 | 0.1667 | 0.7271 |
| B5 | 47 | tdx.ts_hk_tradecal | 接口数据 | 0.1 | 2026-10-31 | 0.8 | 0.8689 | 1.0 | 0.1667 | 0.7271 |
| B5 | 48 | tdx.ts_moneyflow_hsgt | 接口数据 | 0.1 | 2026-10-31 | 0.8 | 0.8689 | 1.0 | 0.1667 | 0.7271 |
| B5 | 49 | tdx.ts_us_tbr | 接口数据 | 0.1 | 2026-10-31 | 0.8 | 0.8689 | 1.0 | 0.1667 | 0.7271 |
| B5 | 50 | tdx.ts_us_tradecal | 接口数据 | 0.1 | 2026-10-31 | 0.8 | 0.8689 | 1.0 | 0.1667 | 0.7271 |

## 三、manifest 3 项 lifecycle 建议（DATA3 复核）

| table | 当前 lifecycle | 建议 | 理由 | 前置条件 |
|---|---|---|---|---|
| quarantine_prod_overwrite | lifecycle_deprecated | **归档** | 生产覆盖隔离区数据，manifest 修复计划已标注 lifecycle_deprecated；不纳入暴露面，保留只读归档以备审计 | DATA3 确认隔离区保留策略与保留期；归档前只读快照 sha256 |
| xq_comment | lifecycle_planned | **保持跳过** | lifecycle_planned（规划未激活）：尚未完成抓取/ETL，当前无完整存储；直接激活会产生空表元数据与覆盖假象 | DATA2 完成评论抓取与 ETL 后，再以 lifecycle_active 补 manifest 键并登记 |
| xq_quote_snap | lifecycle_deprecated | **归档** | lifecycle_deprecated（快照类已被 xq_quote 替代）；保留历史快照只读，禁止新增写入 | DATA3 核对替代关系；manifest 键保留但标记 deprecated |

## 四、校验

- 草案 id 唯一：True；与现有 catalog v35 id 冲突：无
- 路径存在 48/48；schema 就绪 43；date_col 27；主键 36；freq 44
- **可自动生成：36/48 = 75.0%**；需人工 12 项（见清册）
- 校验结论：PASS（草案结构自洽，可进入审批后写入）

## 五、局限与不做的边界

- catalog 草案基于采样（sqlite PRAGMA/parquet schema/文件列表），未做全量校验；schema_scope=sampled。
- 48 项中 corpus_layer（知识库 01~90 层/ZIP 归档）多为非表格资产，schema 需人工确认 catalog 粒度（是否逐层入册）。
- 暴露面优先级为启发式 4 维打分（价值/大小/时效/复用），复用面用仓内 py 引用次数近似，不含门户访问日志。
- 本文件为草案：不写 catalog/warehouse_registry、不改数据根、不触发外部抓取；写前须 DATA1 审批。
