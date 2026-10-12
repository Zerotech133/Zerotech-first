# 数据源缺口补齐与受控发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在保持生产写入关闭的前提下，逐项核对并补齐板块日线/分钟、融资融券、股票覆盖、A 股估值字段、JEV 判断和盘前产物缺口，最终通过跨表对账、来源回执、RunStore、资源锁、发布锁、发布后指纹和回读验证。

**Architecture:** 所有缺口先由 `data_integrity.scan_integrity` 生成稳定的键清单，再进入隔离 staging。每个请求保存原始响应、请求参数、来源时间、响应哈希和规范化文件；“无供给、停牌、退市、未上市、合法空值”分别记录原因，不补零、不复制前值。只有候选回执、跨表对账和发布锁全部通过，才允许进入受控发布预案。

**Tech Stack:** Python 3.11、pandas/pyarrow、requests、SQLite、Parquet、JSONL、现有 `RunStore` 与 `data_health`。

**Spec:** `AGENTS.md`、`quant_backtest/data_integrity.py`、`quant_backtest/tdx_sync.py`、`quant_backtest/data_health.py`。

## Global Constraints

- `global_activation=false`、`production_write=false`，完成所有门槛前不得写生产仓。
- TDX/ Tushare 远端实际供给必须先探针验证；接口无权限或返回空数据时记录 blocked/source_empty，不生成伪数据。
- 每批 20–50 个交易日；每个缺键最多一次请求，失败可从 receipt/manifest 恢复。
- 保留完整原始响应和 SHA-256；Parquet 仅作为规范化副本。
- 所有主键、日期、交易时间和单位必须经过校验；重复键、日期越界、字段合同变化直接阻断该批次。
- 任何长跑命令输出落到 `work/`；Windows 删除走回收站；生产入口使用 8734。

## Review Focus

- 当前板块定义代码与历史有效期不同步时，不能用当前定义覆盖历史日期；测试覆盖新增、停用和重命名板块。
- TDX 远端总行数大于本地但 manifest 已 `done` 时，必须以逐键/逐日对账结果为准；测试覆盖远端/本地行数不一致。
- 融资明细缺键同时遇到停牌、退市或非融资标的时，必须留下资格证据并从“缺失”分类为合法无供给；测试覆盖三类标的。
- A 股估值字段存在合法空值（亏损股 PE、停牌日换手率）时不能用 0 填充；测试覆盖单位、空值和报告期边界。
- JEV/盘前任务中途失败或过期时，健康页必须显示 stale/blocked，并能从最后一条回执续跑；测试覆盖幂等重试。

### Task 1: 建立缺口快照、键清单和来源资格基线

**Files:**
- Create: `quant_backtest/gap_remediation.py`
- Test: `quant_backtest/test_gap_remediation.py`
- Output: `work/gap_baseline_20261012.json`

**Interfaces:**
- `build_gap_snapshot(integrity_report: dict) -> dict`
- `write_key_manifest(snapshot: dict, path: Path) -> None`
- `qualify_source(table: str, keys: list[dict], probe: Callable) -> dict`

- [ ] **Step 1: Write failing tests** for extracting the exact 51 sector-day keys, 14 sector-minute keys, 12,858 margin coverage candidates and 14 listed-stock candidates from metric details.
- [ ] **Step 2: Run `pytest quant_backtest/test_gap_remediation.py -q` and confirm failure because the module is absent.
- [ ] **Step 3: Implement deterministic key extraction, sorted manifests, source/market/date metadata and explicit blocked reasons; never convert empty responses to success.
- [ ] **Step 4: Run the focused tests and write `work/gap_baseline_20261012.json`; assert `production_write=false`.

### Task 2: 补齐 51 个板块日线键和 51 个板块定义对账键

**Files:**
- Modify: `quant_backtest/tdx_sync.py` through a staging adapter; keep existing production writer unchanged.
- Create: `quant_backtest/sector_gap_backfill.py`
- Test: `quant_backtest/test_sector_gap_backfill.py`
- Output: `work/sector_daily_backfill_receipt_20261012.json`

**Interfaces:**
- `probe_sector_daily(key: dict) -> dict`
- `stage_sector_daily(keys: list[dict], stage_root: Path) -> dict`
- `reconcile_sector_definition(definition_rows, daily_rows, validity_rows) -> dict`

- [ ] **Step 1: Add failing tests** for exact `/api/data/table/tdx_sector_daily` responses: one matching row passes; `total != 1`, code/date mismatch, missing required columns, null OHLCV, and duplicate keys fail.
- [ ] **Step 2: Run the focused tests and verify the expected failures.
- [ ] **Step 3: Probe all 51 keys with `limit=2`, record request/response hashes and remote supply status, then stage only rows that match the requested code/date and schema.
- [ ] **Step 4: Reconcile against `tdx_sector_def` using validity windows; classify historical retired/new codes instead of forcing current definitions onto old dates.
- [ ] **Step 5: Run key uniqueness, daily coverage, definition reconciliation and readback queries; produce a candidate receipt with `added`, `blocked`, `rejected`, `source_empty` counts.

### Task 3: 补齐 14 个板块分钟断面和 14,771 个分钟字段空值

**Files:**
- Create: `quant_backtest/sector_minute_gap_backfill.py`
- Modify: `quant_backtest/data_integrity.py` only for receipt-aware metrics.
- Test: `quant_backtest/test_sector_minute_gap_backfill.py`
- Output: `work/sector_minute_backfill_receipt_20261012.json`

**Interfaces:**
- `probe_sector_minute(code: str, trade_date: str, start_time: str, end_time: str) -> dict`
- `validate_sector_minute_frame(frame: DataFrame) -> dict`
- `stage_sector_minute(keys: list[dict], stage_root: Path) -> dict`

- [ ] **Step 1: Write failing tests** for `code/trade_date/trade_time` uniqueness, required OHLCV/amount null counting, valid time ordering, and date boundary checks.
- [ ] **Step 2: Verify focused tests fail before implementation.
- [ ] **Step 3: Probe the remote minute table for each of 14 missing code/date surfaces; use bounded pagination and preserve `trade_time` exactly.
- [ ] **Step 4: Separate legitimate no-supply windows from malformed rows; never fill volume/amount with zero, and retain rejected rows in the receipt.
- [ ] **Step 5: Recompute `sector_minute_daily_coverage`, `sector_minute_required_nulls`, duplicate keys and calendar gaps from staging, then compare against the pre-run snapshot.

### Task 4: 修复融资融券覆盖、关键字段空值和股票最新覆盖

**Files:**
- Create: `quant_backtest/margin_gap_reconciliation.py`
- Modify: `quant_backtest/history_backfill.py` to reuse raw-response receipts for date batches.
- Test: `quant_backtest/test_margin_gap_reconciliation.py`
- Output: `work/margin_coverage_reconciliation_20261012.json`

**Interfaces:**
- `classify_margin_gap(key, stock_basic, suspend_d, delist_evidence) -> str`
- `reconcile_margin_dates(expected_dates, receipts, eligibility) -> dict`
- `reconcile_latest_stock_coverage(trade_date, daily, stock_basic, suspend_d) -> dict`

- [ ] **Step 1: Add failing tests** for margin-eligible, suspended, delisted and non-margin stocks; assert null `rzye/rzrqye` is classified rather than replaced.
- [ ] **Step 2: Verify the tests fail for the current unclassified gap output.
- [ ] **Step 3: Build a date-level expected set from `trade_cal`, `stock_basic`, official suspension/delisting evidence and margin eligibility; request only unresolved dates/codes from Tushare.
- [ ] **Step 4: Validate raw response, primary keys, field nulls and date coverage; keep source-empty/blocked receipts separate from valid rows.
- [ ] **Step 5: Recompute the 12,858 coverage candidates, 2 required-field nulls and 14 latest-stock warnings; require unexplained counts to reach zero before qualification.

### Task 5: 补齐 A 股估值字段、JEV 队列和盘前产物

**Files:**
- Create: `quant_backtest/valuation_field_reconcile.py`
- Modify: `quant_backtest/jev_backfill.py` or the existing JEV queue worker selected by source audit.
- Modify: `quant_backtest/premarket.py` and its scheduler entry only after a dry run.
- Test: `quant_backtest/test_valuation_field_reconcile.py`
- Test: `quant_backtest/test_jev_backfill.py`
- Output: `work/valuation_jev_premarket_reconcile_20261012.json`

**Interfaces:**
- `join_daily_basic_to_kline(kline, daily_basic) -> DataFrame`
- `classify_valuation_null(row) -> str`
- `process_jev_queue(limit: int, resume_from: str | None) -> dict`
- `run_premarket_once(run_date: str, dry_run: bool) -> dict`

- [ ] **Step 1: Write failing tests** for PE null on loss-making stocks, market-cap/turnover unit conversion, 123 JEV resumable rows and stale premarket output.
- [ ] **Step 2: Verify focused tests fail before adding the reconciliation and resume paths.
- [ ] **Step 3: Join `ts_daily_basic` by `(ts_code, trade_date)`, preserve legal null semantics, and expose the three fields through a curated view/feature table rather than mutating source K-line files.
- [ ] **Step 4: Process JEV rows in bounded batches with source hash, model/version, decision state and retry receipt; verify queue count decreases without overwriting prior judgments.
- [ ] **Step 5: Run one premarket dry-run under Python 3.11, verify all upstream sources and artifact freshness, then repair scheduling and publish only after the artifact fingerprint is recorded.

### Task 6: 全局对账、RunStore 门槛和发布前验收

**Files:**
- Modify: `quant_backtest/data_health.py` to consume the new receipts and expose per-day/per-source counts.
- Create: `quant_backtest/gap_release_audit.py`
- Test: `quant_backtest/test_gap_release_audit.py`
- Output: `work/gap_release_audit_20261012.json`

**Interfaces:**
- `audit_gap_release(stage_root: Path, baseline: dict) -> dict`
- `assert_publish_gates(audit: dict) -> None`

- [ ] **Step 1: Add failing tests** for missing source receipt, stale registry qualification, unreleased resource lock, post-publish fingerprint mismatch and readback mismatch.
- [ ] **Step 2: Verify the gate tests fail while `global_activation=false` and pending fields remain.
- [ ] **Step 3: Register each candidate unit in RunStore, acquire/release resource and publication locks, and record registry digest plus source receipt IDs.
- [ ] **Step 4: Run post-write fingerprint and readback reconciliation in an isolated candidate target; do not flip global activation during the first run.
- [ ] **Step 5: Run the complete verification set: `pytest quant_backtest/test_gap_remediation.py quant_backtest/test_sector_gap_backfill.py quant_backtest/test_sector_minute_gap_backfill.py quant_backtest/test_margin_gap_reconciliation.py quant_backtest/test_valuation_field_reconcile.py quant_backtest/test_jev_backfill.py quant_backtest/test_gap_release_audit.py -q`, `python quant_backtest/data_health.py`, `python quant_backtest/tdx_sync.py --status`, `python quant_backtest/verify_tushare_sync.py`, and `git diff --check`.

## Publish Gate

发布前必须同时满足：所有缺口键有合格回执或带证据的 blocked 分类；板块日线/分钟、融资覆盖、定义键和股票覆盖指标为 OK；关键字段空值仅剩有规则解释的合法空值；JEV 队列按阈值清零；盘前产物在 26 小时内；RunStore、资源锁、发布锁、发布后指纹和回读对账全部完成。任一条件不满足就保持 `global_activation=false`。
