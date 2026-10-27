# LCS-1 回测 harness 派单需求单 v1（交 ARCH1，按研发路由）

> 提出方：ACAD1 / BACK1 ｜ **受理方：ARCH1 曹操（研发部主官）** ｜ 状态：**pending_dispatch（待 ARCH1 建单派工）**
> 日期：2026-10-27 ｜ 工单：`WO-LEGION-LCS1-HARNESS-REQ-20261027` ｜ 建议研发单号：`WO-LCS1-HARNESS-20261027`
> 依据：`deliverables/2026-10-27_STRAT1_LCS1策略Spec_draft_v0.1.md` §7、`deliverables/2026-10-27_BACK1_LCS1回测执行清单_v1.md` §6
> 路由依据：**AGENTS §4 研发需求路由（2026-10-27 A0 L1 批）**——新研发需求优先派 ARCH1/组长（G1 DEV1 / G2 DEV5），不直接落 DSH/治理。本单为需求文档，不含实现代码。

## 1. 背景与问题

- LCS-1 已进入「补证」，Spec v0.1（draft）与回测清单已落；DATA1 冻结回执预计 2026-10-28 12:00 前出。
- 但**当前无 LCS-1 批量回测实现**：
  - `quant_backtest/backtest_engine_v2.py` 仅支持 `--strategy chanlun|double_knife`；
  - `tools/chart_hypothesis.py` 只有单票 CLI（`python tools/chart_hypothesis.py 600337`），无批量/事件研究/产物。
- 结论：**DATA1 READY 也不等于能开跑**；需先实现 harness，否则回测清单无法执行。

## 2. 目标 / 非目标

**目标**：交付 `quant_backtest/lcs1_backtest.py`（批量回测 harness），按 Spec 产出 L1/L2/L3/L4 信号台账、候选池、事件研究、组合回测、成本压力与 S3 对账接口，供 BACK1 在 READY 后照单执行。
**非目标**：不做产品化/交易接入；不改交易/执行链；不改数据根；不落地真实下单；不重定义 LCS-1 标准（L1 必须复用既有实现）。

## 3. 接口契约（最小可行）

```
python -X utf8 quant_backtest/lcs1_backtest.py \
  --start 2023-01-01 --end 2026-10-16 \
  --universe tradable_cn --entry next_open \
  --holding 10 --stop -0.08 \
  --cost-profile freeze_receipt \
  --out work/lcs1_backtest_YYYYMMDD/ [--dry-run] [--symbols 600337.SH]
```

| 约束 | 要求 |
|---|---|
| L1 复用 | **必须 import `tools/chart_hypothesis.py::operators/primary_hypothesis`**，禁止复制/重写算子阈值（防口径漂移） |
| as-of | 所有输入（K线/事件/席位）按信号日截断；单测须覆盖“注入未来数据不得被使用” |
| 只读 | 不写 `quant_backtest/data` 与任何数据根；产物只落 `--out` |
| 成本 | 读冻结回执 `cost_model`，按 `fee_schedule.py`/`fill_model.py` 口径；`--dry-run` 零写 |
| 产物 | 按 `BACK1回测执行清单 §5`：signals_l1/l1_l2/l1_l3、candidates、event_study、portfolio_backtest、cost_stress_x1.5、reconcile 接口、报告数据 |
| 退出码 | 0 成功 / 2 参数/数据缺失（fail-visible，不静默降级） |

## 4. 验收判据（ARCH1/BACK1 终审、DEV4 复核）

| # | 判据 | 证据 |
|---|---|---|
| A1 | CLI 可跑通 `--dry-run`（零写、零网络） | 命令 + 目录前后快照 |
| A2 | L1 五算子抽样一致性：≥20 票随日期跑批结果与 `chart_hypothesis.py` 单票输出逐字段一致（primary/ops/context） | 对账 JSON（差异=0） |
| A3 | as-of 无未来函数：注入 t 日后事件/席位，结果与不注入一致 | 单测 + 报告 |
| A4 | 成本口径：`cost_model` 参数生效；×1.5 压力可算 | 单测 + 产物 |
| A5 | 产物 schema 齐（12 项产物的最小字段集合） | 产物清单核对 |
| A6 | S3 对账接口：可产出一致率报告（阈值 0.99，低于则 fail） | reconcile 输出 |
| A7 | `pytest` 新测试全绿 + `compileall` 通过；不写数据根 | pytest/compile 输出 |
| A8 | merge 四守卫全绿、无 L2；实现文档 + 复现命令入库 | merge 回执 |

## 5. 分派建议（按研发路由）

| 角色 | 席位 | 职责 |
|---|---|---|
| 建单/终审 | **ARCH1**（研发部主官） | 按 devtask 建单、拆组派工、终审验收；必要时指定 G1/G2 组长 |
| 实现主责 | **BACK1 陆逊** | harness 代码 + 单测 + S3 对账接口（回测口径本人最熟） |
| 复核 | **DEV4 杜预**（回测工程师） | 独立复核成本/成交口径与 as-of；不复写 |
| 数据依赖 | DATA1 张松 | 冻结回执（全量跑前置；实现/单测不阻塞） |
| 规格确认 | STRAT1 / ACAD1 | Spec 冻结版本；L2/L3 规则争议裁决 |

- 预估工作量：2–3 人日（代码 1–1.5d + 单测 0.5d + S3 接口 0.5d + 复核 0.5d）。
- 建议工单标题：`LCS-1 回测 harness（复用五算子+as-of+产物契约）`；project=quant。

## 6. 依赖与前置

| 项 | 是否阻塞实现 | 说明 |
|---|---|---|
| DATA1 冻结回执 | 否（阻塞**全量跑**） | 实现/单测可用有限样本或 mock；全量执行必须 READY |
| Spec 冻结 | 否（阻塞正式回测） | 实现按 draft v0.1；规则变更随 Spec 版本 |
| 数据陈旧 | 是（全量跑前） | `ts_index_daily/ts_top_list/ts_top_inst` freshness=20261023，需 DATA1 补齐或说明 |
| 数据根结构变更 | 禁止 | 实现只读，不改 KD 数据根（AGENTS §3 需先读 kg_sync_task.log） |

## 7. 风险与边界

- **口径漂移**：L1 复制阈值即视为验收不通过（A2 拦）；只能 import 复用。
- **未来函数**：事件/席位 as-of；A3 单测强制。
- **范围蔓延**：本单只做 harness，不做产品化/UI/交易接入。
- **数据陈旧**：全量跑前由 DATA1 补齐；不得用旧数据出结论。
- **合规**：回测只产研究结论，`execution_allowed=False`，不构成投资建议。

## 8. 交付物

1. `quant_backtest/lcs1_backtest.py`（含 `--dry-run`）；
2. `quant_backtest/tests/test_lcs1_backtest.py`（A2/A3/A4 覆盖）；
3. 实现说明（`docs/dev/` 或 `deliverables/`，含复现命令与产物 schema）；
4. 本需求单回执：ARCH1 建单号 + 验收结论。

## 9. 请求动作（ARCH1）

- [ ] 建 devtask 单（建议 `WO-LCS1-HARNESS-20261027`，owner BACK1，复核 DEV4）；
- [ ] 按路由拆派并回填工单 note（本需求单编号）；
- [ ] 验收 A1–A8 后回执 ACAD1/BACK1；本需求单置 `dispatched`。
