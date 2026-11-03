# 数据仓注册执行器运行报告（task-169 W11 数据仓注册批次2（12 项 corpus_layer 补全））

- 生成：2026-11-04T03:59:30+08:00；模式 **apply**；任务 task-169 W11 数据仓注册批次2（12 项 corpus_layer 补全）；结论 **PASS**
- 输入草案：C:\项目\老田的AI助手\research\reports\data_asset_metadata_draft_20261104.json（本批 batch=manual，选中 12/48 项，未选中 36 项不写）
- catalog：v37（292 表） → v38（304 表）；新增 **12** 表
- registry：physical 292 → 304；display 258 → 258
- 备份：catalog `C:\项目\老田的AI助手\research\reports\backups\catalog_before_task165_20261104-035930.json`；registry `C:\项目\老田的AI助手\research\reports\backups\warehouse_registry_before_task165_20261104-035930.json`
- 回滚命令：`Copy-Item 'C:\项目\老田的AI助手\research\reports\backups\catalog_before_task165_20261104-035930.json' 'C:\项目\老田的AI助手\quant_backtest\data\catalog.json' -Force; Copy-Item 'C:\项目\老田的AI助手\research\reports\backups\warehouse_registry_before_task165_20261104-035930.json' 'C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json' -Force`

## 一、dry-run diff

- 新增 12 项；id 冲突 []；store 缺失 []；空 schema []；重复 id []；diff_clean=True

新增 id 清单：

- research_shadow
- yt_briefs
- yt_cleaned_comments
- yt_faq
- yt_index
- yt_knowledge_units
- yt_learning.db
- yt_learning_cards
- yt_normalized
- yt_raw_zip
- yt_reports
- yt_review

## 二、写后复检证据

- metadata_consistency：rc=0，code=0，tables=304，physical=304，issues=[]
- registry_align --check：rc=0，match_before=True，match_after=None
- registry_align --apply：rc=0，rebuild={'performed': True, 'backup': 'C:\\项目\\老田的AI助手\\quant_backtest\\data\\warehouse_registry.json.bak-20261104-035931', 'backup_rel': 'quant_backtest/data/warehouse_registry.json.bak-20261104-035931', 'stats': {'catalog_version': 38, 'catalog_tables': 304, 'old_physical_count': 292, 'added': 12, 'physical_count': 304, 'display_count': 258}, 'verify': {'ok': True, 'checks': {'json_valid': True, 'catalog_sha256_match': True, 'physical_count_match': True, 'display_count_match': True}}}
- kg_sync_task.log 追加：asset_registration date=2026-11-04T03:59:31+08:00 action=apply catalog=v38 sha=0de8839e57d7798f added=12 tables=304 physical=304 display=258 PASS exit=0

## 四、人工粒度决策（batch2 · 目录/容器资产登记）

| id | layout | schema(文件级/表级) | date_col | primary_key | freq | static_no_date | 证据 |
|---|---|---|---|---|---|---|---|
| research_shadow | shadow_ledger_dir | subdir,date,ret,net… | date | ['subdir', 'date'] | D | False | store=True, files=12, latest=2026-11-02 |
| yt_raw_zip | zip_batch_manifest | package_id,scope,created_at,counts… | created_at | ['package_id'] | batch | False | store=True, files=2844, latest=2026-10-26 |
| yt_normalized | corpus_parquet_jsonl | video_id,comment_id,parent,text… | published_at | ['video_id', 'comment_id'] | event | False | store=True, files=12, latest=2026-10-30 |
| yt_cleaned_comments | corpus_clean_dir | video_id,comment_id,parent,text… | published_at | ['video_id', 'comment_id'] | event | False | store=True, files=38, latest=2026-11-04 |
| yt_knowledge_units | corpus_jsonl | unit_id,project_id,claim,evidence… | created_at | ['unit_id'] | event | False | store=True, files=7, latest=2026-11-04 |
| yt_faq | faq_assets_mixed | cluster_id,size,like_sum,sample_examples… | — | ['cluster_id'] | event | True | store=True, files=19, latest=2026-11-01 |
| yt_index | index_shards | doc_id,video_id,channel_id,kind… | upload_date | ['doc_id'] | event | False | store=True, files=252, latest=2026-10-30 |
| yt_learning_cards | markdown_daily | file_path,trade_date,title,body_markdown | trade_date | ['file_path'] | D | False | store=True, files=4, latest=2026-11-03 |
| yt_briefs | markdown_daily | file_path,trade_date,title,body_markdown | trade_date | ['file_path'] | D | False | store=True, files=2, latest=2026-11-01 |
| yt_review | review_bundle | file_path,generated_at,seed,total_pool… | generated_at | ['file_path'] | event | False | store=True, files=4, latest=2026-11-01 |
| yt_reports | report_bundle | file_path,generated_at,root,totals… | generated_at | ['file_path'] | event | False | store=True, files=33, latest=2026-10-30 |
| yt_learning.db | sqlite_db_container | dim_channel,dim_topic,dim_video,bridge_video_topic… | — | ['table_name'] | mixed | True | store=True, files=5, latest=2026-11-04 |

决策理由：
- **research_shadow**：影子盘台账目录（baseline/s08/s10/s11/selection 5 个子资产，各含 ledger.jsonl + shadow_report.json）；以目录资产登记，schema 取 ledger.jsonl 文件级字段，主键=子目录+date，D 频
- **yt_raw_zip**：分包归档：780 个 .zip + 同名 .manifest.json + .sha256（另含 补充更正_v2 子目录）；catalog 粒度=包级资产（不逐文件登记），主键 package_id，date_col=manifest.created_at
- **yt_normalized**：规范化语料目录（videos/comments/channel_map 的 parquet+jsonl）；目录资产 schema 以 comments 主表字段为准，主键=video_id+comment_id，事件时间 published_at
- **yt_cleaned_comments**：清洗+PII 脱敏评论语料（comments_clean + laomu_notes + 两期视频号学习子集）；schema 以 comments_clean parquet 为准（含 pii_masked/text_sha1）
- **yt_knowledge_units**：知识单元 draft/effective 共 7 个 jsonl；文件级 schema，主键 unit_id，按 project_id+created_at 组织
- **yt_faq**：FAQ 挖掘产物（json/jsonl/py/log/npy 混合，19 文件）；目录资产无统一日期列 → static_no_date=true，schema 取 faq_cluster_labels.jsonl，主键 cluster_id，按构建批次(event)更新
- **yt_index**：62,401 docs bge-m3 索引（lite 索引 + full/shards）；目录资产 schema 取 shard jsonl 字段，主键 doc_id，按索引构建批次更新
- **yt_learning_cards**：学习卡 Markdown 文件（cards_YYYYMMDD.md，按日）；目录资产以 file_path 为主键，trade_date 由文件名解析，D 频
- **yt_briefs**：简报 Markdown 文件（briefing_YYYYMMDD.md，按日）；以 file_path 为主键，trade_date 由文件名解析，D 频
- **yt_review**：复核报告成对文件（review_*.json + .md）；以 file_path 为主键，事件时间 generated_at，按复核批次更新
- **yt_reports**：报告集（22 json + 11 md，含 corpus_manifest.json）；以 file_path 为主键，generated_at 为事件时间
- **yt_learning.db**：星型模型库级容器资产（yt_learning.db 共 15 表；其中 13 张星型表已在批次1 单独注册，本项只登记库容器，schema=已注册表名清单，避免与表级资产重复计覆盖）

## 五、未选中项（本批不写）

| id | label | 是否自动项 |
|---|---|---|
| xq_xq_status_classified | xueqiu_info.xq_status_classified | True |
| research_hypotheses | hypotheses.hypotheses | True |
| paper_orders | paper.orders | True |
| paper_ledger | paper.ledger | True |
| paper_positions | paper.positions | True |
| paper_equity_marks | paper.equity_marks | True |
| paper_positions_snapshot | paper.positions_snapshot | True |
| paper_decision_snapshots | paper.decision_snapshots | True |
| quant_chanlun_pool | quant_db.chanlun_pool | True |
| quant_nine_dim_rc | quant_db.nine_dim_rc | True |
| quant_nine_dim_rc_meta | quant_db.nine_dim_rc_meta | True |
| quant_nine_dim_fina | quant_db.nine_dim_fina | True |
| quant_nine_dim_fc | quant_db.nine_dim_fc | True |
| quant_nine_dim_fc_meta | quant_db.nine_dim_fc_meta | True |
| news_runs | news_db.runs | True |
| news_ai_briefs | news_db.ai_briefs | True |
| news_ai_reviews | news_db.ai_reviews | True |
| news_quotes | news_db.quotes | True |
| news_jev_review_queue | news_db.jev_review_queue | True |
| automation_runs | automation_runs | True |
| yt_transcripts | yt_transcripts（转写） | True |
| yt_distill | yt_distill（知识提炼） | True |
| yt_rag | yt_rag（4,926 chunks + packed 62,401） | True |
| yt_dim_channel | yt_learning_db.dim_channel | True |
| yt_dim_topic | yt_learning_db.dim_topic | True |
| yt_dim_video | yt_learning_db.dim_video | True |
| yt_bridge_video_topic | yt_learning_db.bridge_video_topic | True |
| yt_fact_comment | yt_learning_db.fact_comment | True |
| yt_fact_knowledge_unit | yt_learning_db.fact_knowledge_unit | True |
| yt_fact_transcript | yt_learning_db.fact_transcript | True |
| yt_fact_faq_semantic | yt_learning_db.fact_faq_semantic | True |
| yt_bridge_faq_comment | yt_learning_db.bridge_faq_comment | True |
| yt_meta_faq_build | yt_learning_db.meta_faq_build | True |
| yt_fact_transcript_seg | yt_learning_db.fact_transcript_seg | True |
| yt_meta_etl | yt_learning_db.meta_etl | True |
| yt_fact_transcript_signal | yt_learning_db.fact_transcript_signal | True |

## 六、风险与后续

- corpus_layer 以「目录/容器资产」粒度登记（不逐文件），文件级 schema 为采样值；逐文件清单/校验和需后续 manifest 化
- 目录型资产无统一日期列 → 按 task-169 决策标 static_no_date=true / freq=event（静态或批次口径），新鲜度检查对这些资产豁免
- catalog/registry 属 .gitignore 数据文件，本任务不 commit；生产写入以 metadata_consistency+registry_align 复检为准
- yt_learning.db 为库级容器资产，与批次1 已注册的 13 张星型表存在「容器+表」双登记：覆盖统计需按表级去重
- yt_raw_zip 以包级登记（780 包），不含包内逐文件校验；包完整性由 .sha256 文件保障，建议后续补逐文件 manifest
- markdown/py/log/npy 等非表格资产（learning_cards/briefs/faq 等）无行级 schema，下游取数需按文件路径+解析器约定

## 七、边界

- 只写 catalog/registry 元数据；不改数据文件（store 只读）；不 commit。
- registry 由官方 registry_align --apply 重建（自带备份/校验/kg 日志），执行器负责 catalog 写入与总回滚。
- 未选中项按批次边界不写；如有人工项剩余，需人工补全元数据后另单注册。
