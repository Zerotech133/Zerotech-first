# 全量数据更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不污染生产库、不补零伪造数据的前提下，完成来源资格核验、近期增量、2023 年以来历史回补、血缘归档和健康看板更新，并让每批数据可恢复、可对账、可回读。

**Architecture:** 采用 `source → landing/staging → curated warehouse → published read layer` 四层路径。所有写入先经过 `RunStore`、资源锁、发布锁和来源回执，按 20～50 个交易日分批；每批完成后记录文件指纹、唯一键、行数、空值和实际查询结果，只有发布后回读一致才允许进入下一批。

**Tech Stack:** Python 3、SQLite、Parquet、pandas、PyArrow、Tushare Pro、TDX/xtcloud、`RunStore`、Windows `LockFileEx` 发布锁、pytest。

**Spec:** 本计划基于 `AGENTS.md`、`quant_backtest/warehouse_sync_spec.py`、`quant_backtest/docs/` 中的数据中台设计和当前 `K-STOCK-211` 估值/JEV修复回执。

## Global Constraints

- 任务单先行；每个实施任务使用独立 git worktree，提交信息带 `K-STOCK-xxx`。
- `production_write=false`、`global_activation=false` 保持到所有来源回执、RunStore、资源锁、发布锁、发布后指纹和回读对账齐全。
- 历史补拉按 20～50 个交易日分批，失败必须从回执恢复，不能重复拉取。
- 真实来源缺失时保留 `blocked/source_empty`，禁止补零、复制邻日或把合法空值伪装成有效值。
- 生产写入必须经过 `qualify_execution_plan`、`publication_guard(resource, mode='write')` 和 `commit_table`。
- 所有 token、Cookie、URL 和认证头只能进入脱敏字段；任务输出落盘到 `work/`。

## Review Focus

- 远端返回空页、重复页或 token 失效时，批次必须进入 `source_empty/blocked`，不能标记成功。
- 同一 `ts_code + trade_date`、板块键或报告期键重复时，必须在 staging 拒绝并输出重复键清单。
- 失败重试必须复用 `request_id` 和已完成 unit，不能重复写同一分片。
- PE 亏损空值、停牌换手率空值和未解释空值必须分开统计。
- 发布后文件指纹、manifest、目录清单和实际查询结果不一致时，发布必须失败并保留恢复点。

---

### Task 1: 建立全量刷新基线和来源资格清单

**Files:**
- Modify: `quant_backtest/data_health.py`
- Modify: `quant_backtest/build_catalog.py`
- Modify: `quant_backtest/warehouse_update.py`
- Modify: `quant_backtest/warehouse_sync_runtime.py`
- Create: `quant_backtest/test_full_refresh_baseline.py`
- Create: `work/full_refresh_baseline_20261013.json`

**Interfaces:**
- Consumes: `data/tdx/_manifest.json`、`data/tushare/_manifest.json`、`warehouse_metadata.snapshot()`、`RunStore`。
- Produces: `build_full_refresh_baseline(data_root) -> dict`，字段包含 `table`、`source`、`latest`、`rows`、`unique_key`、`missing_source_evidence`、`qualification`。

- [ ] **Step 1: 写失败测试**：用临时 TDX/Tushare manifest 构造 `ts_daily_basic`、`ts_margin_detail`、`tdx_sector_daily`、`tdx_sector_min` 四张表，断言基线同时记录最新日期、实际行数、重复键和来源资格。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_full_refresh_baseline.py`，预期函数未定义。
- [ ] **Step 3: 实现基线扫描**：复用 `build_catalog.py` 的 schema 探测，调用 `warehouse_sync_runtime.read_local_state()`，不得把 manifest 的 `done` 当作真实覆盖。
- [ ] **Step 4: 加入实际查询探针**：每张关键表至少执行一次 `SELECT COUNT(*)`、`MIN/MAX(date)`、唯一键计数和目标日查询，把结果写入 `work/full_refresh_baseline_20261013.json`。
- [ ] **Step 5: 运行通过并提交**：执行 `pytest`、`python -m py_compile`、`git diff --check`，提交 `K-STOCK-xxx 建立全量刷新基线`。

### Task 2: 统一 Tushare/TDX 受控适配层和分片计划

**Files:**
- Modify: `quant_backtest/tushare_sync.py`
- Modify: `quant_backtest/tdx_sync.py`
- Modify: `quant_backtest/warehouse_sync_runtime.py`
- Modify: `quant_backtest/warehouse_runs.py`
- Create: `quant_backtest/full_refresh_plan.py`
- Create: `quant_backtest/test_full_refresh_plan.py`

**Interfaces:**
- Consumes: Task 1 基线、`warehouse_update.build_plan()`、来源 registry。
- Produces: `build_full_refresh_plan(table, start_date, end_date, batch_days=30) -> dict`、`execute_refresh_batch(plan, store, dry_run=True) -> dict`。

- [ ] **Step 1: 写失败测试**：断言 2023-01-01 至最新交易日被切成 20～50 个交易日窗口，窗口首尾无重叠，并为每个窗口生成稳定 `unit_id`。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_full_refresh_plan.py`。
- [ ] **Step 3: 实现计划生成**：从交易日历生成窗口；计划必须包含 `registry_digest`、`baseline_digest`、`plan_digest`、`request_id` 和 `retry_of`。
- [ ] **Step 4: 接入 RunStore**：用 `RunStore.create_run()` 建立 run，使用 `warehouse_sync_runtime.Heartbeat` 每 10 秒续租；相同 `resource` 已有锁时返回 `RunBusy`。
- [ ] **Step 5: 加入失败恢复**：从 `source_receipt`、`progress` 和 `commit_complete` 事件恢复，已完成 unit 不重复请求。
- [ ] **Step 6: 运行并提交**：只执行 `dry_run=True` 回归，提交 `K-STOCK-xxx 统一全量刷新计划与恢复逻辑`。

### Task 3: 先补健康扫描明确缺口和最近 30 个交易日

**Files:**
- Modify: `quant_backtest/full_refresh_plan.py`
- Modify: `quant_backtest/data_health.py`
- Modify: `quant_backtest/valuation_field_reconcile.py`
- Create: `quant_backtest/test_recent_refresh.py`
- Create: `work/recent_refresh_receipts/`

**Interfaces:**
- Consumes: Task 2 的 `execute_refresh_batch`、本地交易日历、Tushare/TDX 只读回执。
- Produces: 每批 `source_receipt.jsonl`、`commit_intent`、`commit_complete`、`verification.json`。

- [ ] **Step 1: 写失败测试**：覆盖 `stock_basic`、估值字段、板块日线 51 键、板块分钟 14 键和融资缺口；无合格远端供给时断言为 `blocked`。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_recent_refresh.py`。
- [ ] **Step 3: 执行只读来源探针**：按表和交易所记录请求范围、返回行数、空页原因和官方停牌/退市证据；Token 错误、VPN 不通或权限不足不得转成成功。
- [ ] **Step 4: 生成最近 30 日 staging**：每批 20～30 个交易日，按事实源唯一键去重，运行 `join_daily_basic_from_dir()` 只读验证三字段。
- [ ] **Step 5: 回读对账**：验证查询结果、分片 manifest、行数、唯一键、日期连续性和字段空值，未通过的批次保留 staging 不发布。
- [ ] **Step 6: 运行测试并提交回执**。

### Task 4: 按年度回补 2023 年以来历史数据

**Files:**
- Modify: `quant_backtest/run_annual_history_backfill.py`
- Modify: `quant_backtest/run_explicit_history_backfill.py`
- Modify: `quant_backtest/tushare_sync.py`
- Create: `quant_backtest/test_annual_refresh.py`
- Create: `work/annual_refresh_receipts/2023/`、`2024/`、`2025/`、`2026/`

**Interfaces:**
- Consumes: Task 3 已验证的来源资格、分片计划和成功回执。
- Produces: 按年度、按表、按窗口可恢复的 `RunStore` 运行记录和归档 manifest。

- [ ] **Step 1: 写失败测试**：模拟某个年度第 3 批失败，重跑时只请求失败批次；模拟重复键时批次状态为 `partial/blocked`。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_annual_refresh.py`。
- [ ] **Step 3: 先回补 2023 年**：完成后执行年度级覆盖、键对账和随机查询回读，全部通过才进入 2024 年。
- [ ] **Step 4: 顺序回补 2024、2025、2026**：每年 20～50 个交易日一批，失败从 receipt 恢复，不重复拉取已完成窗口。
- [ ] **Step 5: 年度收口**：写入年度 checksum、最新日期、行数、唯一键和空值分布，运行 `data_health` 和 `build_catalog`。

### Task 5: 数据血缘、归档和发布后指纹

**Files:**
- Modify: `quant_backtest/build_catalog.py`
- Modify: `quant_backtest/warehouse_commit.py`
- Modify: `quant_backtest/warehouse_runs.py`
- Create: `quant_backtest/lineage_manifest.py`
- Create: `quant_backtest/test_lineage_manifest.py`

**Interfaces:**
- Consumes: `source_receipt`、`commit_complete`、Parquet schema、manifest。
- Produces: `build_lineage_manifest(run_id) -> dict`，包含 source/api/request_scope、staging_path、target、sha256、primary_key、row_counts、published_at 和 readback_digest。

- [ ] **Step 1: 写失败测试**：缺少来源回执、指纹不一致或回读 digest 不一致时断言发布拒绝。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_lineage_manifest.py`。
- [ ] **Step 3: 实现血缘清单**：将每个 unit 的来源、转换、去重、发布和回读结果写入 `data/lineage/`，敏感字段脱敏。
- [ ] **Step 4: 接入发布锁**：用 `publication_guard(resource, mode='write')` 包住物化和 manifest 更新；崩溃后只允许 recovery 流程接管。
- [ ] **Step 5: 发布后回读**：重新打开发布文件，计算 SHA256、行数、唯一键和随机查询结果，与 `commit_complete` 对账。
- [ ] **Step 6: 运行测试并提交**。

### Task 6: 完善数据健康监控和看板

**Files:**
- Modify: `quant_backtest/data_health.py`
- Modify: `portal/` 数据仓健康页面对应 handler（不扩展 `portal_api.py` 巨石）
- Create: `quant_backtest/test_full_refresh_health.py`

**Interfaces:**
- Consumes: Task 5 血缘清单、各仓 manifest、实际查询结果。
- Produces: 健康指标 `daily_unique_stock_coverage`、`daily_unique_sector_coverage`、`critical_nulls`、`cross_table_key_reconciliation`、`query_result_checks`、`source_freshness`。

- [ ] **Step 1: 写失败测试**：构造最新日期正常但历史断档、跨表键不一致和查询空结果，断言均能告警。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest -q quant_backtest/test_full_refresh_health.py`。
- [ ] **Step 3: 实现按日指标**：按交易日统计股票/板块唯一覆盖，不能只看最新日期或 manifest `done`。
- [ ] **Step 4: 实现关键列空值和跨表键对账**：输出合法空值、未解释空值、缺键、重复键和来源资格。
- [ ] **Step 5: 接入实际查询结果**：对 `daily_history`、`cb_daily`、`income`、`top10_holders`、`top10_floatholders` 等入口记录查询行数、最新日期和错误类型。
- [ ] **Step 6: 更新看板**：展示已更新、未更新、正在更新、阻断原因、进度、体量和最近一批 receipt 链接。
- [ ] **Step 7: 运行页面回归和端口/导航评审门并提交**。

### Task 7: 受控首个生产更新和收口

**Files:**
- Modify: `quant_backtest/warehouse_sync_runtime.py`
- Modify: `quant_backtest/warehouse_commit.py`
- Create: `work/controlled_release_YYYYMMDD.json`

**Interfaces:**
- Consumes: Tasks 1～6 的所有资格、回执、血缘、健康和回读结果。
- Produces: 一个可审计的受控生产 run；失败自动保留旧版本并可恢复。

- [ ] **Step 1: 发布前门禁**：确认来源资格、旧写者迁移、资源锁、发布锁、RunStore、计划/基线 digest 全部匹配；任一缺失则 `blocked`。
- [ ] **Step 2: 只选择一个小批次**：先发布一个已通过回读的 20～30 交易日窗口，不做全量并发。
- [ ] **Step 3: 执行生产提交**：调用 `commit_table()`，记录 `commit_intent`、`commit_complete` 和发布后指纹。
- [ ] **Step 4: 发布后回读**：执行实际查询和看板健康扫描；结果与 receipt、manifest、血缘清单逐项对账。
- [ ] **Step 5: 失败处理**：任何不一致都停止后续批次，保留旧版本、staging 和错误回执，不关闭保护开关。
- [ ] **Step 6: 收口**：只有首批稳定并通过复核后，才逐批扩大到最近 30 日和年度历史；每批独立提交和回执。

## 验证命令

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m pytest -q quant_backtest/test_full_refresh_baseline.py quant_backtest/test_full_refresh_plan.py quant_backtest/test_recent_refresh.py quant_backtest/test_annual_refresh.py quant_backtest/test_lineage_manifest.py quant_backtest/test_full_refresh_health.py
python -m py_compile quant_backtest/full_refresh_plan.py quant_backtest/lineage_manifest.py
python tools/_mixin_integrity.py --check
python tools/unify_nav.py --check
python tools/check_ports.py --strict
git diff --check
```

## Self-review

- 来源资格、分批恢复、估值字段、JEV、历史回补、血缘、发布锁和健康看板均有独立任务覆盖。
- 计划没有把远端 `done` 当作实际供给，也没有安排补零或绕过发布锁。
- 每个新增接口均在任务的 Interfaces 中定义，并在后续任务中按同名接口消费。
- 最容易出错的空页、重复键、失败恢复、合法空值和发布后漂移均已列入 Review Focus 和测试步骤。
