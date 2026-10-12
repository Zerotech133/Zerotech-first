# 三年历史数据接入与可恢复 staging 下载器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 从 TuShare 交易日历生成 2023-01-01 至最近已完成交易日的三年历史批次，按 20–50 个交易日分片下载到隔离 staging，保存完整响应、规范化 Parquet、回执和可恢复清单，并输出可审计的缺口报告。

**Architecture:** 保留现有 `history_backfill.py` 的单日校验和生产写入禁用约束，在其上增加通用表规格、原始响应归档和批次计划/执行入口。执行器先读取本地 `trade_cal`，只对缺失或不合格回执请求远端，采用临时文件原子替换；所有结果进入 `data/tushare_staging/history_3y`，不触碰 `data/tushare`。

**Tech Stack:** Python 3.11、pandas、requests、Parquet、JSON、pytest。

**Spec:** `docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md`；仓库约束见 `AGENTS.md`。

## Global Constraints

- `global_activation=false`、`production_write=false`，任何批次不得发布生产仓。
- 每批 20–50 个交易日；失败可依据 receipt/manifest 恢复，重复执行不得重复请求合格单元。
- 保存完整原始响应载荷及 SHA-256，不能只保存转换后的表格。
- 不补零、不复制前一交易日、不把合法空响应标记为合格数据。
- 长跑命令输出落盘到 `work/`，入口绑定本地文件路径，不使用含中文路径的脚本文件名。
- 最小改动原则：不修改现有同步器的生产写入逻辑。

## Review Focus

- 交易日历缺失或存在重复日期时，计划必须失败并给出明确错误；测试覆盖空日历和重复日期。
- 已有 Parquet 但回执不完整时，执行器必须重新请求；测试覆盖断点恢复。
- TuShare 返回空数据、错误码或日期越界时，批次必须保留原始响应并标记失败；测试覆盖三种状态。
- 原始 JSON 与 Parquet 任一写入失败时不得生成合格回执；测试覆盖原子写入路径。
- 三年边界必须按日历截断到最近已完成交易日；测试覆盖开始日、结束日和周末。

### Task 1: 三年批次计划与完整性审计

**Files:**
- Create: `quant_backtest/three_year_history.py`
- Test: `quant_backtest/test_three_year_history.py`

**Interfaces:**
- `load_trade_dates(calendar_root: Path, start: str, end: str) -> list[str]`
- `build_batches(dates: Sequence[str], batch_size: int = 40) -> list[dict]`
- `audit_batches(stage_root: Path, batches: Sequence[dict], tables: Sequence[str]) -> dict`

- [ ] **Step 1: Write failing tests** for calendar normalization, 40-day batching, and missing/invalid receipt detection.
- [ ] **Step 2: Run `pytest quant_backtest/test_three_year_history.py -q` and confirm failure because the module is absent.
- [ ] **Step 3: Implement only the planning/audit functions; reject duplicate or out-of-range dates and enforce 20–50 batch size.
- [ ] **Step 4: Re-run the focused tests and then `pytest quant_backtest/test_history_backfill.py -q`.

### Task 2: 通用可恢复下载执行器

**Files:**
- Modify: `quant_backtest/history_backfill.py`
- Modify: `quant_backtest/three_year_history.py`
- Test: `quant_backtest/test_three_year_history.py`

**Interfaces:**
- `run_three_year(table_specs, dates, stage_root, batch_size=40) -> dict`
- `TableSpec(name: str, api_name: str, primary_key: tuple[str, ...])`

- [ ] **Step 1: Add failing tests** asserting raw JSON, Parquet and receipt are all written for a successful response and only missing units call `_post`.
- [ ] **Step 2: Run the focused test and verify the expected missing-function failure.
- [ ] **Step 3: Add `raw_path`/`raw_sha256` to receipts and implement atomic raw payload writes plus resumable batch manifests; keep `production_write=False`.
- [ ] **Step 4: Run focused tests and existing history-backfill tests.

### Task 3: 只读首批与三年执行入口

**Files:**
- Create: `quant_backtest/run_three_year_history.py`
- Create: `work/three_year_history_runbook_20261012.md`

**Interfaces:**
- CLI flags: `--start`, `--end`, `--batch-size`, `--table`, `--dry-run`, `--stage-root`.

- [ ] **Step 1: Add a dry-run test that produces a plan without network calls.
- [ ] **Step 2: Implement CLI loading the local SSE calendar and defaulting to 2023-01-01 through the latest completed date.
- [ ] **Step 3: Run dry-run to create the plan and audit report under `work/`; run one 20–50 day staging batch only after the plan passes.
- [ ] **Step 4: Run the full relevant test set and record row counts, failures, and remaining gaps; do not publish to production.

## Verification Checklist

- [ ] `pytest quant_backtest/test_three_year_history.py quant_backtest/test_history_backfill.py -q`
- [ ] `python -m py_compile quant_backtest/three_year_history.py quant_backtest/run_three_year_history.py`
- [ ] `git diff --check`
- [ ] Dry-run report confirms `production_write=false` and `global_activation=false`.
- [ ] Staging audit lists every missing batch/date/table and does not claim completion from directory counts alone.
