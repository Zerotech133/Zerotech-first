# 代码审查、BUG 修复与研发路线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复九维来源快照在日期匹配、缺失回执和畸形载荷上的可复现错误，并建立后续数据治理、RAG 评测、可观测性和策略验收的研发顺序。

**Architecture:** 评分引擎继续只读消费 as-of 来源快照。快照适配层统一日期格式、验证回执边界与行结构，任何无法证明当前交易日的载荷都失败关闭；评分、训练和交易门禁继续由上层资格快照控制。后续研发按“候选治理 → RAG 基线 → 观测修复 → 分层验收 → 平台整合 → 商业化”依次推进。

**Tech Stack:** Python 3.11、pytest、pandas、JSON/Parquet 来源快照、现有 `tools/devtask.py` 评审门。

**Spec:** `AGENTS.md`、`docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md`、来源证据快照与训练门禁文档。

## Global Constraints

- 代码修改必须在独立 worktree 和任务单中完成，提交信息包含 `K-STOCK-148`。
- `training_eligible=false`、`production_eligible=false` 时，不安装训练依赖，不启动 LoRA、模拟成交、交易或收费上架。
- 来源快照只读；缺失、错误、日期越界或畸形载荷必须失败关闭，不能以空数据冒充有效证据。
- 不修改 WorkBuddy 任务数据库、交易员档案、模拟账本、checkpoint、策略或裁决排程。
- 人类入口端口只使用 8734；提交前运行三道评审门。

## Review Focus

- 带 `YYYY-MM-DD` 和 `YYYYMMDD` 的请求/行日期必须命中同一交易日；测试覆盖 ISO 日期。
- `request_params` 缺失或来源回执缺失时，`block_ok` 必须为 `False`；测试覆盖缺失回执。
- `fields` 与行长度不一致时，适配层不得抛异常或产出部分数据；测试覆盖畸形行。
- 日期范围快照只能纳入目标交易日的行，测试覆盖范围内跨日行。
- 评审门失败或训练资格缺证据时，报告必须保留真实失败状态，不得补造成功记录。

### Task 1: 来源快照适配层修复

**Files:**
- Modify: `quant_backtest/nine_dim.py:493-532`
- Test: `quant_backtest/test_nine_dim_tdx.py:142-220`

**Interfaces:**
- Consumes: `source_snapshot.iter_records()` 返回的逐条回执，`tday` 为 `YYYYMMDD` 字符串。
- Produces: `_block_from_snapshot(tday, source_snapshot)` 返回 `(dict, bool)`，`False` 表示不能证明证据完整。

- [x] **Step 1: Write the failing tests**

```python
def test_block_snapshot_normalizes_iso_dates(self):
    block, ok = nd._block_from_snapshot("20260108", iso_date_store)
    assert block["600519.SH"] == [100.0]
    assert ok is True

def test_block_snapshot_missing_receipt_fails_closed(self):
    block, ok = nd._block_from_snapshot("20260108", missing_receipt_store)
    assert block == {}
    assert ok is False

def test_block_snapshot_malformed_row_fails_closed(self):
    block, ok = nd._block_from_snapshot("20260108", short_row_store)
    assert block == {}
    assert ok is False
```

- [x] **Step 2: Run tests to verify the defects**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest -q quant_backtest/test_nine_dim_tdx.py::TestNineDimTdx::test_block_snapshot_normalizes_iso_dates
& 'C:\Program Files\Python311\python.exe' -m pytest -q quant_backtest/test_nine_dim_tdx.py::TestNineDimTdx::test_block_snapshot_missing_receipt_fails_closed
& 'C:\Program Files\Python311\python.exe' -m pytest -q quant_backtest/test_nine_dim_tdx.py::TestNineDimTdx::test_block_snapshot_malformed_row_fails_closed
```

Expected before the fix: first test fails with missing code, second reports `ok=True`, third raises `IndexError`.

- [x] **Step 3: Implement the minimal fix**

1. Normalize request and row dates through `_td8` before exact/range comparison。
2. A record with no date boundary returns `({}, False)`。
3. Require `ts_code`, `trade_date`, `price` and verify each row is a list/tuple long enough for all required indexes。

- [x] **Step 4: Run the focused regression**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest -q quant_backtest/test_nine_dim_tdx.py quant_backtest/test_nine_dim_source_snapshot.py k_stock/tests/test_gaorta_models.py
```

Expected: `29 passed, 3 skipped` in the current environment.

### Task 2: 证据与训练候选治理

**Files:**
- Review: `work/academy-source-evidence-v0.1/candidate-source-snapshot-v0.3.json`
- Review: `work/academy-source-evidence-v0.1/candidate-eval-manifest-v0.3.json`
- Review: `work/academy-source-evidence-v0.1/source-evidence-ledger-v0.1.json`
- Create: `docs/plans/2026-10-13_training-candidate-governance.md`

**Interfaces:**
- Consumes: 来源许可证、来源日期回执、样本 SHA256、`split_group`、离线审核回执。
- Produces: 可复核的 `training_eligible` 清单；缺证据样本继续标记 `evaluation_only`。

- [ ] **Step 1: 生成只读缺口报告**

运行候选快照重建与清单校验脚本，输出来源数、`source_date_missing`、哈希漂移和缺签名项；只写 `work/academy-source-evidence-v0.1/` 报告目录。

- [ ] **Step 2: 冻结时间切分**

为每个候选样本写入 `train/validation/test`、`split_group` 和样本 SHA256，禁止同一来源组跨切分泄漏。

- [ ] **Step 3: 复核训练门禁**

只有许可证、本人离线签名、标准定义和日历完整性全部齐全时才允许状态变为 `ready_for_isolated_lora_review`；否则保持 `training_eligible=false`。

### Task 3: 只读 RAG 基线与评测

**Files:**
- Review: `agent_platform/` RAG 入口与索引实现
- Review: `work/agent-academy-prototype/rag-baseline-v0.1/`
- Create: `docs/plans/2026-10-14_rag-baseline-evaluation.md`

**Interfaces:**
- Consumes: CORE、策略、风险、工具课程文档和已冻结候选清单。
- Produces: 检索命中率、引用完整性、拒答率、时序泄漏报告；不改变金融门禁。

- [ ] **Step 1: 固定评测集**

按课程、策略、风险、工具四类建立带来源和时间戳的只读问题集。

- [ ] **Step 2: 运行门禁评测**

要求回答附可定位来源，缺证据时拒答，不能引用未来日期；失败项只进入报告，不进入训练集。

- [ ] **Step 3: 评审 LoRA 是否必要**

只有 RAG 基线在引用完整性、拒答和时序泄漏三项达到门槛，且训练清单状态已就绪，才提交隔离 LoRA 评审。

### Task 4: P1 可观测性与策略验收

**Files:**
- Review: `tools/` 任务与告警封装
- Review: `k_stock/` 市场账本消费和调度探测
- Create: `docs/plans/2026-10-15_strategy-acceptance-matrix.md`

**Interfaces:**
- Consumes: 任务 `rc`、日志、结果三联证据及市场数据源状态。
- Produces: 缠论、九维、weak、双刀的分层验收矩阵和影子期报告。

- [ ] **Step 1: 统一失败留痕**

让 `alert_fail_wrap` 统一记录任务返回码、日志路径、结果摘要和异常分类；禁止只写“成功/失败”单值。

- [ ] **Step 2: 补市场账本与调度探测**

验证 `/market` 账本消费、`k_stock` 调度和三源无雇佣跳过证据，仍保持只读。

- [ ] **Step 3: 分层验收**

为每个策略固定样本量、时间窗、基准、交易成本、缺数据拒绝和暂态结构证据；影子期未通过不得转 live。

### Task 5: 平台整合与商业化前置

**Files:**
- Review: `docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md`
- Review: `tools/check_ports.py`、`tools/unify_nav.py`
- Create: `docs/plans/2026-10-16_platform-and-market-readiness.md`

**Interfaces:**
- Consumes: 已通过的课程、RAG、策略验收和版本证据。
- Produces: 统一导航、端口登记、worktree 隔离、算法市场版本/许可证/回滚设计。

- [ ] **Step 1: 先做 P4 小批拆分**

每项拆分有独立任务单、独立评审门和可回滚提交，再接入菜鸟总管首页。

- [ ] **Step 2: 建立算法市场证据包**

每个可售模型必须携带版本、训练来源、许可证范围、评测摘要和回滚点。

- [ ] **Step 3: 保持商业化门禁**

标准定义与日历完整性未认证前，训练、模拟成交、交易和收费上架均保持关闭。

## Verification and Review Gates

代码修复完成后在 worktree 执行：

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest -q quant_backtest/test_nine_dim_tdx.py quant_backtest/test_nine_dim_source_snapshot.py k_stock/tests/test_gaorta_models.py
& 'C:\Program Files\Python311\python.exe' -m py_compile quant_backtest/nine_dim.py quant_backtest/test_nine_dim_tdx.py
& 'C:\Program Files\Python311\python.exe' tools/_mixin_integrity.py --check
& 'C:\Program Files\Python311\python.exe' tools/unify_nav.py --check
& 'C:\Program Files\Python311\python.exe' tools/check_ports.py --strict
```

只有测试和三道评审门全绿，且没有新增训练资格证据时，才可进入 `review`；不得据此启用任何金融执行能力。
