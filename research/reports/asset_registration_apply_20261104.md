# 数据仓注册执行器运行报告（task-165 · W7）

- 生成：2026-11-04T03:41:27+08:00；模式 **apply**；任务 task-165；结论 **PASS**
- 输入草案：C:\项目\老田的AI助手\research\reports\data_asset_metadata_draft_20261104.json（自动项 36/48，人工项 12 不写）
- catalog：v35（256 表） → v36（292 表）；新增 **36** 表
- registry：physical 256 → 292；display 258 → 258
- 备份：catalog `C:\项目\老田的AI助手\research\reports\backups\catalog_before_task165_20261104-034127.json`；registry `C:\项目\老田的AI助手\research\reports\backups\warehouse_registry_before_task165_20261104-034127.json`
- 回滚命令：`Copy-Item 'C:\项目\老田的AI助手\research\reports\backups\catalog_before_task165_20261104-034127.json' 'C:\项目\老田的AI助手\quant_backtest\data\catalog.json' -Force; Copy-Item 'C:\项目\老田的AI助手\research\reports\backups\warehouse_registry_before_task165_20261104-034127.json' 'C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json' -Force`

## 一、dry-run diff

- 新增 36 项；id 冲突 []；store 缺失 []；空 schema []；重复 id []；diff_clean=True

新增 id 清单：

- automation_runs
- news_ai_briefs
- news_ai_reviews
- news_jev_review_queue
- news_quotes
- news_runs
- paper_decision_snapshots
- paper_equity_marks
- paper_ledger
- paper_orders
- paper_positions
- paper_positions_snapshot
- quant_chanlun_pool
- quant_nine_dim_fc
- quant_nine_dim_fc_meta
- quant_nine_dim_fina
- quant_nine_dim_rc
- quant_nine_dim_rc_meta
- research_hypotheses
- xq_xq_status_classified
- yt_bridge_faq_comment
- yt_bridge_video_topic
- yt_dim_channel
- yt_dim_topic
- yt_dim_video
- yt_distill
- yt_fact_comment
- yt_fact_faq_semantic
- yt_fact_knowledge_unit
- yt_fact_transcript
- yt_fact_transcript_seg
- yt_fact_transcript_signal
- yt_meta_etl
- yt_meta_faq_build
- yt_rag
- yt_transcripts

## 二、写后复检证据

- metadata_consistency：rc=0，code=0，tables=292，physical=292，issues=[]
- registry_align --check：rc=0，match_before=True，match_after=None
- registry_align --apply：rc=0，rebuild={'performed': True, 'backup': 'C:\\项目\\老田的AI助手\\quant_backtest\\data\\warehouse_registry.json.bak-20261104-034127', 'backup_rel': 'quant_backtest/data/warehouse_registry.json.bak-20261104-034127', 'stats': {'catalog_version': 36, 'catalog_tables': 292, 'old_physical_count': 256, 'added': 36, 'physical_count': 292, 'display_count': 258}, 'verify': {'ok': True, 'checks': {'json_valid': True, 'catalog_sha256_match': True, 'physical_count_match': True, 'display_count_match': True}}}
- kg_sync_task.log 追加：asset_registration date=2026-11-04T03:41:28+08:00 action=apply catalog=v36 sha=d1547c92fe470bd7 added=36 tables=292 physical=292 display=258 PASS

## 四、人工项（不写清单）

| id | label | 需人工字段 |
|---|---|---|
| research_shadow | research_shadow | schema; freq; date_col; primary_key |
| yt_raw_zip | yt_raw_zip（948 ZIP/清单/sha256） | schema; date_col; primary_key |
| yt_normalized | yt_normalized（videos/comments/channel_map parquet） | date_col; primary_key |
| yt_cleaned_comments | yt_cleaned_comments（清洗+PII脱敏） | date_col; primary_key |
| yt_knowledge_units | yt_knowledge_units（知识单元） | date_col; primary_key |
| yt_faq | yt_faq（FAQ 语义归并 133 条） | date_col; primary_key |
| yt_index | yt_index（62,401 docs bge-m3） | date_col; primary_key |
| yt_learning_cards | yt_learning_cards（学习卡） | schema; freq; date_col; primary_key |
| yt_briefs | yt_briefs（简报） | schema; freq; date_col; primary_key |
| yt_review | yt_review（复核） | date_col; primary_key |
| yt_reports | yt_reports（报告） | date_col; primary_key |
| yt_learning.db | yt_learning.db（星型模型 9 表，统一取数入口） | schema; freq; date_col; primary_key |

## 五、边界

- 只写 catalog/registry 元数据；不改数据文件（store 只读）；不 commit。
- registry 由官方 registry_align --apply 重建（自带备份/校验/kg 日志），执行器负责 catalog 写入与总回滚。
- 12 个人工项按批准范围不写；待人工补全元数据后另单注册。

## 六、批次内元数据修正（审计后）

- 修正原因：批次内元数据审计：6 个条目采样误推断 date_col/静态标记，经核对 sqlite PRAGMA 与样例行后修正并重建 registry
- 最终：catalog v37（292 表），registry physical=292 display=258，metadata_consistency code=0，registry_align --check match=True
- 修正后备份：catalog `C:\项目\老田的AI助手\research\reports\backups\catalog_before_correction_20261104-034244.json`；registry `C:\项目\老田的AI助手\research\reports\backups\warehouse_registry_before_correction_20261104-034244.json`
- 修正明细：
  - xq_xq_status_classified: date_col sentiment → labeled_at；static False → False；freq event → D
  - quant_nine_dim_rc_meta: date_col value → None；static False → True；freq event → static
  - quant_nine_dim_fc_meta: date_col value → None；static False → True；freq event → static
  - yt_dim_channel: date_col total_views → None；static False → True；freq static → static
  - yt_meta_faq_build: date_col value → None；static False → True；freq static → static
  - automation_runs: date_col trade_date → None；static False → False；freq unknown → event
