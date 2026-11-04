# 2026-11-04 catalog v39/256 × warehouse_registry physical 304/display 258 根因调查报告 v1

- 任务：task-5（ARCH1 只读调查）｜执行：arch1-catalog-p0｜生成：2026-11-04 14:0x
- 性质：**只读调查**。除本报告外未写任何文件；未改数据根、未重建/覆盖 catalog/registry、未 merge、未重启服务、未新建 worktree。
- 结论：**不是"重建时序中间态"，而是一次可复现的登记层语义冲突 + 一次事后掩盖。**

---

## 0. 结论速览（TL;DR）

**根因一句话**：
`quant_backtest/build_catalog.py` 每天 06:20 链从扫描结果**从零重建** catalog，没有任何"外部注册资产持久化通道"；2026-11-04 03:41/03:59 的 task-165/task-169 注册（共 48 项外部 SQLite/目录/语料资产）是**直接写 catalog.json 派生文件**的。06:24 的例行 `catalog_build` 把 catalog 覆盖回扫描可复现的 256 表（v39），48 项整批丢失；随后 `registry_rebuild.py` 按"catalog 已不存在的旧条目**保守保留、绝不静默丢弃**"的设计把 48 项留成 orphan，于是 registry 仍是 304 物理。06:43 链尾 `registry_align --apply` 的严格校验发现了 304≠256 并**正确回滚报错**；06:44 会话侧改用宽松的 `registry_rebuild --apply` 重建，把 registry 的 `catalog_sha256` 刷成 v39 的 sha，于是 `metadata_consistency` 只剩 warning、merge 门转绿——**漂移被 sha 对齐掩盖，计数矛盾原样保留**。

- **集合差（实测，非猜）**：catalog 256 与 registry 304 的差是 **48 项，方向为 registry 多、catalog 少**；48 项 = task-165 批次1 的 36 项（03:41，v35→v36）+ task-169 批次2 的 12 项（03:59，v37→v38）。清单见 §2.2。catalog 无一项在 registry 中缺失（256−304 = 空集）。
- **铁证**：`quant_backtest/data/catalog_changelog.jsonl` 的 2026-11-04T06:24:56 条目 `version=39`，`removed` 恰为该 48 项全名单、`added=[]`（§2.3）。
- **哪边是预期真源**：
  - 按**物理表级文件契约**：catalog 是扫描自动生成真源，registry 是派生的资格台账，契约要求 `registry.physical_count == len(catalog.tables)`（SOP v2 明文）→ 256 是"物理表集合"的真源；
  - 按**业务授权**：48 项是 2026-11-03T17:12 老田（A0）批准的 DATA4-P1 决策清单中的真实资产（48/48 store 现场存在），属于 dataset/资源级资产，**不能以"catalog 自动重建为 256"为理由静默删除**；
  - 因此：**catalog 是"物理表"真源，registry 304 是"已批准注册集合"的真实快照，当前状态是两层登记口径混用**。正确终态二选一：**(A) 48 项保留并持久化 → 304/304 一致**（推荐）；(B) A0 正式变更决策、把 48 项迁到数据集级 registry → 再收敛到 256/256。
- **修复**：本次未执行。分级见 §6/§7/§8；最低批准链 = **DATA1 执行/签核数据根写入（L1）+ A0 批准口径与门禁代码变更（治理道）**；若改 KgDailySync 链/计划任务则升 **L2（老田/A0 五要素）**。

---

## 1. 事实时间线（kg_sync_task.log + 备份 + 报告实证）

来源：`Get-Content C:\Temp\kg_sync_task.log -Tail 200`（全程只读；rc 不适用）。

| 时间（2026-11-04） | 事件 | 事实读数 |
|---|---|---|
| 03:41:27 | `registry_align --apply`（task-165 批次1 触发） | catalog=v36/sha `d1547c92…`，physical=292，display=258 |
| 03:41:28 | `asset_registration` apply（36 自动项） | added=36，tables=292，physical=292，PASS |
| 03:42:45 | `registry_align --apply`（纠正） | catalog=v37/sha `92fd2757…`，physical=292 |
| 03:42:45 | `asset_registration_correction`（fixed=6） | catalog=v37，tables=292，PASS |
| 03:59:31 | `registry_align --apply`（task-169 批次2 触发） | catalog=v38/sha `0de8839e…`，physical=304，display=258 |
| 03:59:31 | `asset_registration`（12 人工项） | added=12，tables=304，physical=304，PASS |
| **06:24:56/57** | **`catalog_build exit=0`（KgDailySync 第 1 步）** | **catalog=v39 / 256 表 / sha `50531cf3…`；48 项被 removed** |
| 06:43:24 | `registry_align exit=1` | 先重建成功（aligned=True、verify=True、metadata_code=0、physical=304），但严格复核 `meta.physical_count==len(catalog.tables)` 失败（304≠256）→ **自动回滚**，restore catalog_sha=`0de8839e…`（v38） |
| 06:43:25 | `kg_sync OK` | 链判定"数据链已完成"，对齐失败只 WARN/告警，不阻断 |
| **06:44:48** | **`registry_rebuild date=…tables=304 sha=50531cf37e35cc74 exit=0`** | 会话侧改用宽松 tool 重建：48 项 orphan 保守保留，`catalog_sha256` 刷成 v39 sha → metadata 转绿（仅 warning） |
| 07:20 | P1 并行开发回执 §六 | "元数据门已转绿：metadata_consistency 07:20 PASS（仅 WARN 304/256 计数差；warehouse_registry 06:44:48 已对齐 catalog v39 sha）" |
| 07:22 / 09:41 | dev_events 提交 `83ab982cd`/`1c3085bef`/`726add391`/`1695d02f4`/`bfd8fbbeb` | 把数据仓对齐断言"动态化"为 `catalog256 / registry304` 双口径，`physical>=catalog` 即通过 → 漂移被测试门合法化 |

06:43 失败细节（`C:\Temp\kg_registry_align_out.log` 尾部实读）：
`"error": "对齐后校验失败，已回滚到备份 …warehouse_registry.json.bak-20261104-064323：aligned=True verify=True metadata_code=0 physical=304"`，`"rolled_back": true`，`restore.catalog_sha256 = 0de8839e…`（= catalog v38 sha），回滚后 `metadata_after_rollback.code=1 / issues=["registry_catalog_sha_mismatch"]`。

> 判定："06:40 元数据门阻断是并发重建中间态"（P1 回执 §二）**不成立**：06:24 的 catalog 缩表是完成态（changelog 落盘、文件 mtime 06:24:56、内容稳定），06:44 之后仍是 304/256（本次 13:54 复测未变）。"中间态"只是 06:24–06:44 的 20 分钟窗口；06:44 的 rebuild 不是修复，而是**把矛盾从 FAIL 降级成 WARN**。

---

## 2. 集合差：256 vs 304 到底是什么（实测，不猜）

### 2.1 集合运算结果

复算脚本（内联 python，只读；rc=0）对当前 `quant_backtest/data/catalog.json` 与 `warehouse_registry.json` 求集合并互差：

| 运算 | 结果 |
|---|---|
| `len(catalog.tables)` | **256**（version=39，updated=2026-11-04T06:24:56，sha256 `50531cf37e35cc74…`） |
| `len(registry.physical_tables)` / `physical_count` | **304** / **304** |
| `len(registry.display_assets)` / `display_count` | **258** / **258** |
| `registry.catalog_sha256 == 当前 catalog 实算 sha256` | **True**（`50531cf3…`，即 06:44 rebuild 造成） |
| `catalog − registry.physical` | **∅（空集）** → registry 覆盖 catalog 全部 256 表，registry 没有"漏登记" |
| `registry.physical − catalog` | **48 项**（§2.2 全清单） |
| `registry.display − catalog` | ∅（display 244 个去重名全在 catalog 内） |
| `catalog − registry.display` | 12 项（`crypto_daily/quarantine_prod_overwrite/ts_eco_cal/ts_fund_company/us_legacy/xq_capital_flow/xq_comment/xq_earningforecast/xq_hot_deal/xq_inner_trade/xq_kline_d/xq_northbound`，属 display 子集口径，与本次根因无关，仅说明 258 不能当 304 的对照） |

### 2.2 48 项全清单（按类别；批次归属已核）

**类别 A｜行情仓外 SQLite / 平台运行（19 项，均批次1）**
- `news_collector/news.db` → `news_runs, news_ai_briefs, news_ai_reviews, news_quotes, news_jev_review_queue`（5）
- `k_stock/data/paper.db` → `paper_orders, paper_ledger, paper_positions, paper_equity_marks, paper_positions_snapshot, paper_decision_snapshots`（6）
- `quant_backtest/data/quant.db` → `quant_chanlun_pool, quant_nine_dim_rc, quant_nine_dim_rc_meta, quant_nine_dim_fina, quant_nine_dim_fc, quant_nine_dim_fc_meta`（6）
- `C:/aios/automation_runs`（目录资产）→ `automation_runs`（1）
- `research/hypotheses.db` → `research_hypotheses`（1，批次1）

**类别 B｜开源情报 SQLite（1 项，批次1）**
- `quant_backtest/data/xueqiu/xueqiu_info.db` → `xq_xq_status_classified`（注册 asset_id 带 `xq_` 前缀；该库物理表名 `xq_status_classified`，与 catalog 既有 `xq_status/xq_comment/xq_hot_post` 不同名，未非重复覆盖）

**类别 C｜知识库/学习派生 corpus_layer（28 项：批次1 16 + 批次2 12）**
- 批次1（16）：`yt_transcripts, yt_distill, yt_rag, yt_dim_channel, yt_dim_topic, yt_dim_video, yt_bridge_video_topic, yt_fact_comment, yt_fact_knowledge_unit, yt_fact_transcript, yt_fact_faq_semantic, yt_bridge_faq_comment, yt_meta_faq_build, yt_fact_transcript_seg, yt_meta_etl, yt_fact_transcript_signal`
- 批次2（12）：`research_shadow, yt_raw_zip, yt_normalized, yt_cleaned_comments, yt_knowledge_units, yt_faq, yt_index, yt_learning_cards, yt_briefs, yt_review, yt_reports, yt_learning.db`
- 存储范围：`C:/项目/知识库/00_数据仓/yt_learning.db`（13 张星型表 + 1 个库级容器 `yt_learning.db`）及 `01_数据分包/10_规范化/20_清洗/20_转写/30_知识单元/35_知识提炼/37_FAQ挖掘/40_索引/50_RAG/60_学习卡/70_简报/80_复核/90_报告`、`research/shadow`。

**合计校验**：19 + 1 + 28 = **48** ✓（其中批次1=36：19+1+16；批次2=12：12）。
**现场存在性**：用 `research/reports/data_asset_metadata_draft_20261104.json` 的 `register_drafts[].store` 逐项 `os.path.exists` 复测 → **48/48 存在，0 缺失**（解析：仓库相对路径按 `C:\项目\老田的AI助手`，绝对路径原样）。

> 48 项全部**不在** `build_catalog.py` 的扫描输入范围内：其 `WAREHOUSES` 只含 `data/tdx`、`data/tushare`、`data/xueqiu`、`data/cnopendata`，加少量固定分支（tdx_info.db/xueqiu_info.db/guba/crypto/us legacy/EXTERNAL_TABLES/xq_* 占位）；上述 SQLite/目录/知识库资产无任何扫描或代码注册入口。

### 2.3 铁证链（可复现）

1. **changelog 直接点名**：`Get-Content C:\项目\老田的AI助手\quant_backtest\data\catalog_changelog.jsonl -Tail 8`
   末条：`{"ts":"2026-11-04T06:24:56","version":39,"added":[],"removed":[48 项全名单],"note":"…"}`。
2. **集合完全回归注册前**：当前 catalog(v39) vs `research/reports/backups/catalog_before_task165_20261104-034127.json`(v35/256) → `added=[] removed=[]`（表名集合完全相同；字段值因重建刷新不同，版本 35→39）。
3. **批次拆分**：v37 备份（`catalog_before_task165_20261104-035930.json`，292 表）− 当前 = 36 项（批次1）；v38 备份不存在，但 registry v38 备份（`warehouse_registry.json.bak-20261104-064323`/`…-064448`，physical 304、catalog_sha `0de8839e…`）− 当前 = 48 项 ✓。
4. **sha 链**：v35 备份 sha `e13b6a08…`、v36 `d1547c92…`、v37 `92fd2757…`、v38 `0de8839e…`、v39 `50531cf3…`，与 kg 日志逐条对应。
5. **注册报告**：`research/reports/asset_registration_apply_20261104.md`（task-165，03:41，36 项，catalog v35→v36）与 `research/reports/asset_registration_batch2_20261104.md`（task-169，03:59，12 项，v37→v38），两份均记录"写后 metadata/registry_align 双 PASS"。
6. **结论差异**：执行包 `deliverables/2026-11-04_数据仓注册执行包_v2.md` §4 审批矩阵写着回滚= "git revert catalog.json 提交"——**该回滚方案无效**：`git check-ignore` 实测 `quant_backtest/data/` 被 `.gitignore:27` 忽略，catalog/registry **不在 git 版本控制内**（`git ls-files --error-unmatch` 报 pathspec 不存在）。

---

## 3. 机制与根因（代码级证据）

### 3.1 `build_catalog.py`：每日从扫描从零重建（根因所在）
- `quant_backtest/build_catalog.py:1457-1569`：`build()` 先 `tables = {}`，随后仅从 `WAREHOUSES` 扫描 + `_register_intel_tables()` + `EXTERNAL_TABLES` 合并，最后全量 `cat = {"version":…,"tables": tables}` 原子替换 `data/catalog.json`（`:1563-1568`）。
- **无保留/合并旧条目逻辑**：上一版 catalog 只被用来读 `prev_version/prev_tables` 做版本号与 changelog（`:1499-1556`），**旧 tables 内容不参与新 tables 构建** → 任何"直接写进 catalog 但不在扫描/代码注册范围内"的条目，下一次重建必然丢失。
- **无备份**：替换前不备份旧 catalog（仅 tmp+replace），本次直接导致 **catalog v38/304 原文件不可回滚**（标准备份区 `research/reports/backups/` 无 v38 catalog；全盘定向搜索未发现 sha=`0de8839e…` 的 catalog 副本）。
- 该行为本身符合其文档契约（"扫描 data/ 下各仓 → data/catalog.json"），**是注册执行器选错了写入对象**，不是 build_catalog 的 bug。

### 3.2 `apply_asset_registration.py`：直接写派生文件（触发条件）
- `tools/datawarehouse/apply_asset_registration.py:4-17`：显式定位"把草案项安全写入 **catalog.json**（并触发 registry_align --apply）"，安全边界只覆盖"写前备份/原子写/写后复检/失败回滚"（`:476-496`），**没有校验目标表是否在 build_catalog 可复现范围，也没有把注册项回灌到 build_catalog 的输入**。
- 写后复检 PASS 是**瞬时一致**（当时 registry 也同步到 304），无法预防次日重建；工具与 reports 均未提示该风险。
- 与 SOP 冲突：`docs/standards/2026-11-02_数据仓SOP_v2.md:211` 明文 "catalog 的 files/rows/freshness 字段由 build_catalog 自动维护，**禁止手改**"、`:326` "修改 catalog/registry **仅工具写**（build_catalog/registry_rebuild/qualify_probe）"。task-165/169 走了工具，但绕过了"catalog 只能由 build_catalog 生成"的口径。

### 3.3 `registry_rebuild.py`：orphan 保守保留（304 得以保留的直接原因）
- `tools/datawarehouse/registry_rebuild.py:149-178`：旧条目若其 `physical_tables` 全在 catalog → 原样保留；**否则"保守保留原条目并记入报告，绝不静默丢弃"**（orphans）。
- `verify_written`（`:272-286`）只校验 JSON 合法/sha 匹配/自身计数匹配，**不校验 physical_count 与 catalog 表数相等** → 06:44 用它重建可以 rc=0 且 sha 对齐，但 304/256 矛盾不变。
- 该设计意图正确（防静默丢资产），这次恰好把"上游误删"固定成"长期 orphan"。

### 3.4 `registry_align.py`：06:43 已正确发现矛盾并回滚
- `tools/datawarehouse/registry_align.py:17-22`：aligned 判据含"catalog 全部表被 registry 覆盖"，`orphan` 只记 info **不阻断**（`:154-157`）。
- `:303-377` `--apply`：重建后 `ok = aligned and verify and meta.code==0 and meta.physical_count == len(catalog.tables)`；**06:43 正是最后一项 304≠256 失败 → 自动回滚 exit 1**（日志实证见 §1）。
- 关键解读：**链尾自动对齐器行为正确**；问题出在随后人为改用宽松 tool。

### 3.5 `metadata_consistency.py`：count diff 只是 warning（掩盖机制）
- `tools/datawarehouse/metadata_consistency.py:61-83`：`registry_catalog_sha_mismatch`/自身计数不一致是 issue（阻断），但 `physical_count != len(catalog.tables)` 只进 `warnings`（`registry_vs_catalog_table_count_diff:%s/%s`），`code = 1 if issues else 0` → **304/256 状态 rc=0 PASS**（本次 13:54 实测复现）。
- 加上 06:44 rebuild 把 sha 对齐后，metadata 门完全转绿 → merge 列车继续、漂移无告警级别上升。

### 3.6 读数面为什么"打架"
| 面 | 数据源 | 实测读数（2026-11-04 13:54 前后） |
|---|---|---|
| `GET 8091 /api/dataops/overview` | `portal/api_dataops.py:379-396` 只读 `catalog.json` | `catalog.version=39, table_count=256` |
| `GET 8091 /api/dw/stats` | `portal/api_dw_registry.py`；`_catalog_alignment()`（`:96-113`）同时挂 catalog+registry 字段 | `catalog_tables=256` **且** `registry_physical=304, registry_display=258`；另 top 层 `total=314/registered=265/exposed=72`（dataset_inventory_20261102，第三套分母） |
| `/static/dw_snapshot.json`（06:40:49 生成） | dashboard 快照 | `catalog.tables=256`、`catalog.registry.physical=304` |
| SOP 基线 | `2026-11-02_数据仓SOP_v2.md:18,128` | 期望 physical_count == catalog 表数（当时 256/256） |

> 注：`api_dw_registry.py:97` 的注释 "避免与 /api/dataops 的 304 表读数打架" 是历史语义残留——实测 dataops 当前读 256，真正的双口径是 dw/stats 内部同时输出 256 与 304。

### 3.7 为什么此前没有守住
1. 注册批次（04:05 提交 "数据仓48/48注册(catalog v38/304表 PASS)"）与 06:24 日更链只隔一次例行重建，**无跨链回归测试**（build_catalog 单测不覆盖"外部注册资产是否被保留"）。
2. 06:41/06:44 merge 门两次 failing（`dev_events guard_fail`：`catalog v39/256 / registry 304 / registry_catalog_sha_mismatch`）被当作"并发中间态"，06:44 用宽松 tool 抹平 sha 后 07:20 门转绿。
3. 07:22/09:41 五笔提交把对齐测试改成"catalog 与 registry 各自真实口径、`physical>=catalog` 即过"（`portal/tests/test_dw_catalog_alignment.py:13-36` 现文已无 `physical==catalog` 断言）→ **门禁被改成接受漂移**（治理风险，建议 A0 知悉）。
4. `metadata_consistency` 的计数差仅是 warning，日常面无红。

---

## 4. 哪边是预期真源（按"输入范围/历史基线/语义"三证）

**证据 1｜脚本输入范围**
- `build_catalog.py:78-98` 的扫描范围 = 行情仓 `quant_backtest/data/{tdx,tushare,xueqiu,cnopendata}` + 固定分支；48 项 store 全在其外。→ 256 是**该脚本可复现的物理表全集**。
- `registry_rebuild.py:1-10,149-178` 自述 = "按 catalog 全量对齐**物理表**资格台账"；registry 无独立扫描输入，物理集合由 catalog 派生。→ 304 的 48 项是**派生集合的越界遗留**。
- `registry_align.py:17-22` 的 aligned 判据与 `2026-11-02_数据仓SOP_v2.md:128` "physical_count 与 catalog 表数一致" → **契约期望 304==256 不成立，即不一致**。

**证据 2｜历史基线**
- 10-27 起 registry v2 语义：physical 从 245 起随 catalog 增长（245→256→292→304），**catalog 一直是物理表的唯一定义源**；SOP v2 表格记录 256/256 一致为常态。48 项是 11-04 凌晨新增的"catalog 直写"输入，未经 build_catalog 扫描路径 → 历史基线支持"catalog 缩回 256 是自动重建的预期结果，48 项丢失是注册机制缺陷"。

**证据 3｜语义 + 授权**
- 48 项业务性质 = SQLite 表/目录/语料容器（dataset/资源级），与 catalog 的"行情仓物理 parquet 表"粒度不同；task-162 执行包 §1.4 明确"48 项 catalog 注册，DATA4 提交 → **DATA1 审批**"，`agent_platform/registry/data_asset_decisions_p1.json` 的 `approval.approved_by=老田, approved_at=2026-11-03T17:12:26, scope=DATA4 注册/忽略清单（P1）`、summary.register=48 → **48 项是已获 A0 级批准的资产**，真实存在（48/48 store 存在），不能因自动重建而静默失去登记。

**裁决建议（供 DATA1/A0 拍板）**
- **推荐终态 = 304/304 一致**：保留 48 项已批准注册，把其变成 build_catalog 可复现的持久输入（方案 A）。理由：①不撤销已批准决策；②满足"physical==catalog"契约；③registry 的 orphan 保留设计本来就不允许静默删。
- **备选终态 = 256/256**：仅当 A0 正式决定"48 项只作 dataset 级资产、不占物理表登记"时成立 → 需把 48 项迁入 `dataset_registry/data_catalog.yaml` 并给 registry 加显式 `--drop-orphans` 能力（默认 fail-closed），同时更新 portal 双口径（方案 A2）。**不得用手工删 registry 或手工改 catalog 达成。**

---

## 5. 影响面

1. **合并门/治理**：06:40 P1 回执记录的 metadata 门阻断即此问题的直接表现；06:44 后门转绿但矛盾仍在；07:22/09:41 的断言放宽把 `catalog256/registry304` 写进测试基线 → 后续任何真实漂移更难被发现（棘轮松动）。
2. **读数不唯一**：`/api/dataops/overview`=256；`/api/dw/stats`=256+304/258；`/static/dw_snapshot.json`=256/304；SOP 基线 256/256；数据集盘点=314/265/72。同一"数据资产有多少"出现 4 个分母，验收/汇报口径冲突（今日数据简报已红警、DATA1/A0 inbox 已投递）。
3. **登记 ≠ 可读**：datamart/duck_lake/表预览等取数层以 catalog 路由；48 项已"注册"但 catalog 无条目 → 取数层查不到（登记假象），而其 registry 资格/覆盖统计又计入 304 → 覆盖率/欠账口径虚高。`yt_learning.db` 还存在"库级容器 + 13 张表级"双登记，覆盖统计需按表级去重（batch2 报告 §六已自述）。
4. **持续复发**：只要不修持久化机制，**每天 06:20 链都会把 catalog 重建回 256**；链尾 `registry_align` 会每天 exit=1 回滚并告警（或又被 registry_rebuild "抹平"），且 `catalog_changelog` 每天记录一次 48 项 removed。
5. **回滚能力缺口**：build_catalog 不备份旧 catalog，本次 v38 原文件已不可恢复（只能按 draft 重放注册重建等价内容）；执行包写的 "git revert catalog.json" 回滚方案因数据目录 gitignore 而无效。
6. **不受影响**：parquet 数据文件/48 项 store 本身未动；display_count 258 未变；服务未重启；8091 读数实时读文件（无需重启即可刷新）。

---

## 6. 安全修复方案（命令级；**本次仅建议，未执行**）

> 总原则：**不得手改 catalog/registry**（SOP:211）；不得用扩/缩分母掩盖（07:15 复检 §二）；数据根写入前必读 `C:\Temp\kg_sync_task.log` 尾部并补记（AGENTS §3）；先备份后写、原子写、写后复检。

### 方案 A（推荐，根治）：给 build_catalog 加"持久注册资产"通道 → 恢复 304/304
**A-1 代码（治理道；ARCH1 预审 + A0 批准；本次不动）**
1. 新增持久注册输入 `agent_platform/catalogs/external_assets_registry.json`：由 `research/reports/data_asset_metadata_draft_20261104.json` 的 `register_drafts`（48 项）生成，保留 id/source/store/layout/schema/date_col/code_col/primary_key/freq/static_no_date/desc/审批依据（task-162/165/169 + 老田 2026-11-03 批准）。
2. 修改 `quant_backtest/build_catalog.py::build()`：在 `tables.update(EXTERNAL_TABLES)` 附近合并该注册表（**扫描结果优先、注册项不覆盖同名扫描表**，与 `_register_intel_tables` 同语义），条目打 `source/registered_by=external_assets_registry` 标记；`_changelog` 对注册表新增/缺失写 note。
3. 回归测试（新增）：构造 tmp 根 + 空扫描仓 + overlay → 断言 48 项保留、重跑幂等、同名扫描表不被覆盖、changelog 不产生误 removed。
4. 硬化（与 A-1 同批或紧随）：见 §7。

**A-2 数据恢复（DATA1 执行，L1；待代码合入后）**
```powershell
$repo = 'C:\项目\老田的AI助手'; $py = "$repo\.venvs\phoenix\Scripts\python.exe"
# 0) 数据根互斥预检（尾部必须是成功收尾，无进行中）
Get-Content C:\Temp\kg_sync_task.log -Tail 20        # 期望末条 kg_sync OK / exit=0
# 1) 备份（带 sha256，证明可回滚）
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item "$repo\quant_backtest\data\catalog.json"            "$repo\research\reports\backups\catalog_pre_fix_$ts.json"
Copy-Item "$repo\quant_backtest\data\warehouse_registry.json" "$repo\research\reports\backups\warehouse_registry_pre_fix_$ts.json"
Get-FileHash "$repo\research\reports\backups\catalog_pre_fix_$ts.json" -Algorithm SHA256
# 2) 用修复后的 build_catalog 重建（预计 v40 / 304 表，changelog added=[] removed=[]）
& $py -X utf8 "$repo\quant_backtest\build_catalog.py"          # 期望 rc=0
# 3) 只读 diff 预览（期望 physical 前304 保留304 新增0 后304，orphan_entries=[]）
& $py -X utf8 "$repo\tools\datawarehouse\registry_rebuild.py" --dry-run --json
# 4) 官方严格对齐（备份+重建+metadata 复检+kg 日志；期望 aligned=True、physical=304、metadata code=0 warnings=[]、rc=0）
& $py -X utf8 "$repo\tools\datawarehouse\registry_align.py" --apply --json
# 5) 复检
& $py -X utf8 "$repo\tools\datawarehouse\metadata_consistency.py" --json   # 期望 rc=0、warnings=[]
& $py -X utf8 "$repo\tools\datawarehouse\registry_align.py" --check --json # 期望 rc=0、match_before=True
# 6) 读数复验（8091 实时读文件，无需重启）
(Invoke-WebRequest 'http://127.0.0.1:8091/api/dataops/overview' -UseBasicParsing).Content   # 期望 table_count=304
(Invoke-WebRequest 'http://127.0.0.1:8091/api/dw/stats' -UseBasicParsing).Content           # 期望 catalog_tables=304、registry_physical=304
# 7) 补记 kg 日志（catalog_build / registry_rebuild / registry_align 各一行）
```
预期 rc：2/3 步 rc=0；4 步 rc=0 且 `rolled_back=false`；5 步 rc=0；6 步 HTTP 200 且两处均 304。
**回滚**：`Copy-Item <catalog_pre_fix_$ts.json> …\catalog.json -Force; Copy-Item <warehouse_registry_pre_fix_$ts.json> …\warehouse_registry.json -Force` → 回到当前 304/256 warning 态（不劣化）；如 A-1 代码需回滚：`git revert <commit>`（代码在 git 内，数据不在）。

### 方案 B（止血，今日可用但不持久）：重放已批准注册，恢复 304/304
> 等价于把 06:24 的丢失反向补回，**当天有效**；若 11-05 06:20 前方案 A 代码未上线，**次日必然复发**。需 DATA1 审批执行（同一批已获老田批准的数据，仍属数据根写入）。
```powershell
$repo='C:\项目\老田的AI助手'; $py="$repo\.venvs\phoenix\Scripts\python.exe"
Get-Content C:\Temp\kg_sync_task.log -Tail 20                          # 互斥预检
& $py -X utf8 "$repo\tools\datawarehouse\apply_asset_registration.py" --apply --json --batch all --expect 48 `
    --report-prefix research/reports/asset_registration_replay_20261104   # 内部含 registry_align --apply + 双复检；期望 rc=0、catalog v40/304、physical 304
& $py -X utf8 "$repo\tools\datawarehouse\metadata_consistency.py" --json     # 期望 rc=0、warnings=[]
```
回滚：工具自带 `backups.catalog/registry` + `rollback_command`（报告 JSON 内，`Copy-Item` 恢复）→ 回到 v39/256 + 304/256 当前态。
风险：**不得只做 B 不做 A**（复发 + 每日重复写盘）；B 也不改变"注册机制不可持久"的根因。

### 方案 C（不推荐）：把 registry 收敛到 256
- 需要给 `registry_rebuild` 增加显式 `--drop-orphans`（默认 fail-closed）并**先由 A0 正式变更/撤销 48 项注册决策**；否则等于静默删除已批准登记，违反 registry "绝不静默丢弃"设计，并与 `data_asset_decisions_p1.json`（register=48）冲突。
- 同时必须改 portal 双口径测试/页面，影响面大于 A。

---

## 7. 门禁硬化（防复发；治理道，建议与 A 同批或 24h 内）

1. `metadata_consistency.py:61-83`：把 `registry_vs_catalog_table_count_diff`（以及 orphan 集合非空）从 warning 升级为 **issue（阻断级）**，最迟在下一次日常 06:25 前生效；否则 304/256 仍会 PASS。
2. `registry_rebuild.py::verify_written`：增加 `physical_count == len(catalog.tables)` 校验，不满足则 rc=1 拒绝写盘（保留原 orphan 能力需显式 `--allow-orphan`）；`registry_align` 的严格检查保留（本次已证明有效）。
3. `build_catalog.py`：
   - 用 `prev_tables` 做**丢失告警**：removed 中含"持久注册表/EXTERNAL 类"条目时写 kg 日志 WARN + 事件层 `guard_fail`（fail-visible）；
   - 原子替换前备份旧 catalog（`.bak-<ts>`），补齐本次暴露的"v38 不可回滚"缺口。
4. `apply_asset_registration.py`：写 catalog 前校验目标 id 是否属于 build_catalog 可复现集合（扫描/代码注册/持久注册表），否则拒绝并提示改走注册表输入（避免同类"写进派生文件"再次发生）。
5. 评审门：`portal/tests/test_dw_catalog_alignment.py:13-36` 的 `physical>=catalog` 放宽建议**回退为 `physical==catalog`**（或作为独立 red 校验），防止把漂移固化为基线。

---

## 8. 审批级别（按 AGENTS §7 / ADGS v1.1 / 数据仓 SOP v2）

| 动作 | 车道/分级 | 最少批准 |
|---|---|---|
| 方案 A-1（build_catalog 持久注册通道 + 回归测试） | 治理道（数据源接入/口径） | **ARCH1 预审 + A0 批准**（先 ADR 后实现） |
| 方案 A-2 / B（写 catalog/registry 数据根，L1） | 数据仓 SOP §1:326 **L1**（数据根纪律 + kg 日志互斥） | **DATA1 审批并执行/签核**（执行包 §4 要求 DATA1 审批）；A0 知悉 |
| 修复方向裁决（304 保留 vs 撤销 48 项注册） | 数据口径变更 | **DATA1 提案 + A0 拍板**（撤销需变更 2026-11-03 老田已批 P1 决策） |
| §7 门禁硬化（metadata 阻断级/verify 严格化/测试回退） | 治理道（门禁元标准） | **ARCH1 预审 + A0 批准 + ADR** |
| 若改 KgDailySync 链脚本或新增计划任务（如把 overlay 后置、06:25 对齐任务化） | **L2 道** | **老田/A0 五要素 + 维护窗口**，不得自行改计划任务 |
| 方案 B 的临时重放（若 A0 只要求今日先绿） | L1 紧急止血 | DATA1 单签执行 + 24h 内报备 A0，回执写明"临时止血、复发条件" |

> 结论答复："**数据修复 = DATA1 级（L1，DATA1 审批/执行）；口径与代码（持久化/门禁）= A0 级（治理道，ARCH1 预审 + A0 批准）；改计划任务/链 = L2（老田/A0）**"。

---

## 9. 未确认事项（卡点已排除，仅留审计空耳）

1. **06:44:48 `registry_rebuild --apply` 的执行者未留名**：`kg_sync_task.log` 该行与 `dev_events.jsonl` 均无 operator 字段；结合 `deliverables/2026-11-04_P1并行开发回执_v1.md:45`（07:20 "registry 06:44:48 已对齐 sha，门转绿"）判断为**会话侧为解 merge 门的人工操作**，建议 DATA1 在工单补记 operator/意图（不影响根因结论）。
2. **DATA1 对 48 项注册的独立签核单未见**：执行包要求"DAT4 提交 → DATA1 审批"，`data_asset_decisions_p1.json` 只有老田（A0）批准记录；`asset_registration_*.md` 只记 PASS。建议 DATA1 确认授权链完整性（这决定方案 A/B 是否可直接补录）。
3. **catalog v38 原文件不可恢复**：标准备份无副本（build_catalog 不备份），若 A0 要求"恢复到 03:59 精确态"，只能按 draft 重放生成等价 v40；建议在 §7.3 补齐 catalog 备份能力。
4. 本次未核对 restic/异地备份中是否有 v38 快照（避免长扫描；如需精确恢复请 A0 授权后由 DATA1 查 `C:\项目\_restic_repo`）。

---

## 10. 证据清单（命令 + rc + 路径）

| # | 命令（只读） | rc | 结论/证据 |
|---|---|---|---|
| E1 | `Get-Content C:\Temp\kg_sync_task.log -Tail 200` | — | 06:24:57 catalog_build exit=0；06:43:24 registry_align exit=1；06:44:48 registry_rebuild tables=304 sha=50531cf3… exit=0 |
| E2 | `& $py -X utf8 tools\datawarehouse\metadata_consistency.py --json` | **0** | `{"code":0,"catalog_version":39,"tables":256,"physical_count":304,"issues":[],"warnings":["registry_vs_catalog_table_count_diff:304/256"]}` |
| E3 | `& $py -X utf8 tools\datawarehouse\registry_align.py --check --json` | **0** | `match_before=true`，`diffs_before=[]`，`info_before=[{"kind":"orphan_registry_entries","count":48,...}]` |
| E4 | 内联 python：catalog/registry 集合并互差 | 0 | catalog 256 / physical 304 / catalog−physical=∅ / physical−catalog=48 全名单 |
| E5 | 内联 python：当前 vs `catalog_before_task165_20261104-034127.json`(v35)/`…-035930.json`(v37)/registry v38 备份 | 0 | v35 vs cur added=[] removed=[]；v37−cur=36；registry_v38−cur=48；备份 sha 见 §2.3 |
| E6 | `Get-Content quant_backtest\data\catalog_changelog.jsonl -Tail 8` | — | 06:24:56/v39/removed=48 项/added=[] |
| E7 | `Get-Content C:\Temp\kg_registry_align_out.log -Tail 80` | — | 06:43 apply：aligned=True verify=True metadata_code=0 physical=304 → 严格计数复核失败 → rolled_back=true、restore sha=0de8839e… |
| E8 | `Invoke-WebRequest http://127.0.0.1:8091/api/dataops/overview` | HTTP 200 | `catalog.version=39, table_count=256`；全 JSON 无 304 |
| E9 | `Invoke-WebRequest http://127.0.0.1:8091/api/dw/stats` | HTTP 200 | `total=314/registered=265/exposed=72`；`catalog_tables=256, registry_physical=304, registry_display=258, registry_catalog_sha256=50531cf3…` |
| E10 | 读 `portal/static/dw_snapshot.json`（06:40:49 生成） | — | `catalog.tables=256`、`catalog.registry.physical=304` |
| E11 | 内联 python：48 项 store 存在性 | 0 | 48/48 exists，0 missing |
| E12 | 内联 python：数据文件 sha/mtime | 0 | catalog 299,165B @06:24:56 sha 50531cf3…；registry 788,547B @06:44:48 sha 0b4d67df…（=v38 内容+新 sha/时间） |
| E13 | `git -C <repo> check-ignore -v quant_backtest/data/catalog.json`；`git ls-files --error-unmatch …` | 0 / 1 | `.gitignore:27` 忽略；catalog/registry **未被 git 跟踪** → 执行包 §4 "git revert" 回滚无效 |
| E14 | 代码取证（read） | — | `build_catalog.py:1457-1569` 从零重建/不保留旧 tables/不备份；`registry_rebuild.py:149-178,272-286` orphan 保留+verify 仅自洽；`registry_align.py:17-22,303-377` 严格复核+回滚；`metadata_consistency.py:61-83` count diff 仅 warning；`apply_asset_registration.py:4-17,476-496` 直写 catalog；`start_kg_sync.ps1:107-168` 链尾 registry_align；`api_dataops.py:379-396`；`api_dw_registry.py:96-113`；`tests/test_dw_catalog_alignment.py:13-36` |
| E15 | 制度/授权取证（read） | — | `docs/standards/2026-11-02_数据仓SOP_v2.md:18,128,211,326`；`deliverables/2026-11-04_数据仓注册执行包_v2.md:21,52`；`agent_platform/registry/data_asset_decisions_p1.json` approval=老田@2026-11-03T17:12:26 scope=P1（register=48） |
| E16 | 既有 P0 记录对照（read） | — | `docs/ops/2026-11-04_项目复检_v1.md:19-31`（07:15 已定 P0、DATA1 待决）；`deliverables/2026-11-04_P1并行开发回执_v1.md:16-18,45`（06:40 门阻断/07:20 转绿）；`docs/audit/2026-11-04_代码审查与研发计划复盘_v2.md:30,56`（"非代码可控"表述——**本报告更正：是代码可控制的注册机制缺口**） |

---

## 11. 边界声明

- 本报告为**只读调查**：全程未执行 `build_catalog`/`registry_rebuild --apply`/`registry_align --apply`/`qualify_probe --apply` 等任何写数据根操作；未合并、未提交、未重启 8091/8734、未新建/删除文件（除本报告）。
- 所有"期望 rc"均为建议方案的验收判据，**不是已执行结果**；实际修复须按 §8 批准后由 DATA1 在维护窗口执行并留痕。
- 本报告路径：`docs/audit/2026-11-04_catalog_registry根因_v1.md`；调查任务：task-5（ARCH1）。
