# 股票主数据与九维因子补齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在生产写入关闭的前提下，补齐 `stock_basic` 主数据并核验 `fina`、`margin_detail`、`forecast`、`block_trade`、`news` 来源，重新计算股票覆盖率。

**Architecture:** 先以本地数据仓和可验证的 Tushare 原始回执为读层，所有空结果保留为合法空，不补零、不复制前一日。来源回执写入 staging，覆盖率和九维回放从同一快照读取；只有回执、RunStore、发布锁和回读对账齐全后才允许生产写入。

**Tech Stack:** Python、SQLite/datamart、Parquet、Tushare API、pytest。

## Global Constraints

- `global_activation=false`、`production_write=false` 持续有效。
- 不把合法空结果写成 0，不复制前一交易日，不绕过来源资格门槛。
- 修改前保留现有用户和其他 Agent 改动；所有长跑输出落到 `work/` 日志。
- 运行回放必须使用完整来源快照，避免公网回源造成结果漂移。

## Review Focus

- `stock_basic` 缺码时，行情仍可读，但评分必须记录主数据缺失原因。
- API 成功且零行时，状态必须是 `valid_empty_response`，不能转成数值 0。
- `margin_detail` 任一交易日缺失时，不得伪造周增幅。
- `fina`、新闻和估值字段缺失时，覆盖率应下降或按体系性缺失剔除分母。
- 回放输出行数下降时，必须能按代码追溯 `cov` 和未判定因子。

### Task 1: 主数据基线与补齐

**Files:**
- Modify: `quant_backtest/nine_dim.py`（主数据读取与缓存）
- Test: `quant_backtest/test_nine_dim_backtest.py`（主数据缺码回归）
- Create: `work/stock_basic_recovery_20261012.json`

- [x] 读取本地 `stock_basic`、行情代码并生成缺码清单。
- [x] 以本地 datamart 的 `source_code=0` 等价读回执核对活跃主数据；未发现可补的活跃缺码。
- [x] 确认 `603056.SH` 为 `list_status=D` 已退市代码，并保存 `stock_basic_recovery_20261012.json`（原文链接 `../../work/stock_basic_recovery_20261012.json` 已失效）。
- [x] 修复粗筛：主数据缺失代码不再进入当前候选池，并加入回归测试。

### Task 2: 五类因子来源核验

**Files:**
- Reuse: `quant_backtest/nine_dim_source_snapshot.py`
- Create: `work/factor_source_qualification_20261012.json`

- [x] 对 `fina`、`margin_detail`、`forecast`、`block_trade`、`news` 按交易日和代码核验。
- [x] 记录请求参数、`source_code`、行数、字段空值率、合法空状态和响应哈希。
- [x] 将 `forecast/report_rc/block_trade/fina_indicator` 标为 `qualified_staging`；`margin_detail` 因 `20261001` 合法空、`news` 因尚无稳定 `ts_code` 外键继续保留为 `blocked/candidate_ready`，原因写入 `factor_source_qualification_20261012.json`（原文链接 `../../work/factor_source_qualification_20261012.json` 已失效）。

### Task 3: 覆盖率重算与回归

**Files:**
- Reuse: `work/diagnose_20261008_missing.json`
- Create: `work/coverage_recompute_20261012.json`

- [x] 用统一 v4 snapshot（含 `fina_indicator`）重跑目标锚点，记录候选数、评分数、跳过数及每码 `cov`。
- [x] 对比补齐前后 `stock_basic` 缺码和 `score=None` 数量，结果写入 `coverage_recompute_20261012.json`（原文链接 `../../work/coverage_recompute_20261012.json` 已失效）。
- [x] 运行九维相关测试、`py_compile` 和三项仓库门禁；生产门禁仍关闭。
