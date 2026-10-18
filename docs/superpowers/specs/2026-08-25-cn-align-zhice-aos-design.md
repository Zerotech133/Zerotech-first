# 设计：本地 v2 CN 市场对齐云端 zhice-aos（含缠论 v1）

- 日期：2026-08-25
- 目标系统：本地 `zhice-os-v2`
- 基准系统：云端 `zhice-aos`（`159.75.220.212:/opt/zhice/zhice-aos`）
- 状态：待用户审阅

## 1. 背景与结论

用户希望本地 v2 的 CN 板块「完整参照云端 zhice-aos 的 CN 运行逻辑与缠论 v1」。

经逐项调研（见差距分析），**本地 CN 板块已是云端的同源 1:1 移植，大头已完成**：
- 缠论 v1 结构引擎（czsc 分笔/中枢/几何买卖点 b1/b2/b3/MACD 底背驰/量价同步/评分 `0.40信号+0.30背驰+0.20中枢+0.10量价`）与云端**逐字一致**，同用 `czsc` 1.0.1。
- CN 盘前（regime 路由→候选池→建计划→冲突裁决→风控→APPROVED，不建仓）、盘中（E1[-2%,+5%]/E2 站开盘价/E3 动能 + 09:35–14:45 窗 + 加仓 2 次 + T+1 + can_buy/can_sell + 熔断 + 滑点 0.1%）、盘后（复盘/决策复盘/晨晚报/组合快照）、调度器（CN job 为云端超集）、取价（结算优先+快照兜底+复权）—— 全项一致。

因此本设计只收敛**少数参数与接线差距**，不做架构改动。

## 2. 范围（用户确认口径）

纳入 4 处对齐；**价格带保持本地 3~500 不动**；**止损数值一律不改（保持本地 6%，不采用云端 8%/7%）**。

| 编号 | 改动 | 说明 |
|---|---|---|
| A | 候选池 N：10→20 | 盘前 CN 每策略候选数上限对齐云端 ≤20 |
| B | 每策略计划 top-3 | 建计划前按 `candidate_score` 降序取前 3，对齐云端 `max_plans=3` |
| C | run_intraday 按策略读 ExitPolicy（**纯接线，不改数值**） | 盘中止盈/硬止损/浮动止损改为按持仓所属策略取 `get_exit_policy_for_version`，替换硬编码 6%/20%/8% |
| D | 冲突裁决时序 | `resolve_plan_conflicts` 从「建计划循环之前」移到「所有计划落库之后、进风控之前」 |

**明确不动**：缠论引擎（逐字一致）、E1/E2/E3、入场时间窗、加仓上限、T+1、can_buy/can_sell、调度器、取价口径、缠论选股价格带（3~500）、`exit_policies.py` 全部数值（止损/止盈/浮动均保持本地现值）。

## 3. 详细设计

### 改动 A — 候选池 N 10→20
- 位置：`application/services/workflow_service.py` 的 `run_pre_market`（CN 分支）调用 `registry.run(st, ..., max_candidates=N)` 处。
- 变更：CN 盘前 `max_candidates` 由默认 10 提升到 20。
- 影响面：仅盘前 CN 选股候选数；下游建计划有 top-3 截断（改动 B）兜住，不会因 N 变大而爆量。

### 改动 B — 每策略计划 top-3 截断
- 位置：`workflow_service.py` 建计划循环（`for st, candidates ...`）。
- 变更：对每个策略的候选，先按 `candidate_score` 降序排序，只取前 3 个进入 `create_from_candidate` 建计划；其余候选保留在候选池（`v2_candidate_asset`）但不建计划。
- 与现状差异：当前无显式 top-3，靠 `size_new_position` 仓位预算间接收敛。改为「显式 top-3 + 预算过滤」两道，先 top-3 后预算。
- 口径确认：排序键 = `candidate_score`（各策略自算分），降序，取 3。

### 改动 C — run_intraday 按策略读 ExitPolicy（纯接线）
- 现状：`run_intraday` 盘中出场用硬编码 `DEFAULT_STOP_LOSS_PCT=6% / DEFAULT_TAKE_PROFIT_PCT=20% / 移动止损激活 8%`；`exit_policies.py` 的按策略参数表未被消费（仅当计划已带 stop/tp 价时用 plan_exits 覆盖）。
- 变更：盘中对每个持仓，按其 `strategy_version_id` 调 `get_exit_policy_for_version(sv_id, db)` 取 `ExitPolicy`，用其 `take_profit_pct / stop_loss_pct / use_trailing / trailing_activate_pct / trailing_trail_pct` 驱动止盈、硬止损、浮动止损三段判定，替换硬编码常量。
- 建仓侧：确保建计划时初始 `stop_loss_price / take_profit_price` 也来自同一 `ExitPolicy`（若已如此则不动，仅核对）。
- **数值**：`exit_policies.py` 现有数值**全部保留不改**。缠论 CHAN 止损保持 6%（不采用云端 8%），CHAN_V2 等保持 6%（不采用云端 7%）；止盈/浮动参数本就与云端一致。本改动只做「让 run_intraday 真正使用这张表」的接线，不调参。
- 效果：盘中出场从「一刀切 6%/20%/8%」变为「按策略差异化」（如 CHAN 止盈 18%/浮动激活 5%、MEAN_REVERSION 止盈 6%/不用浮动）。

### 改动 D — 冲突裁决时序
- 现状：`resolve_plan_conflicts(market)` 在建计划循环之前调用。
- 变更：移到所有策略的计划都 `create_from_candidate` 落库之后、进入逐计划风控之前，对齐云端「计划全部落库后再统一裁决」。
- 口径不变：仍按 `PROD>STAGING>DRAFT`、同级置信度最高保留，败者置 `CANCELLED`。

## 4. 测试

仿 `tests/` 现有风格新增/调整单测，覆盖：
- 改动 A/B：盘前 CN 选股产出 ≤20 候选、每策略 ≤3 计划、top-3 按 score 取。
- 改动 C：给定某持仓策略类型，`run_intraday` 出场判定使用对应 `ExitPolicy`（如 CHAN 止盈 18%、止损 6%），非硬编码 20%/6%；MEAN_REVERSION 不启用浮动止损。
- 改动 D：多策略同标的冲突在「全部计划落库后」被正确裁决，败者 CANCELLED。
- 回归：缠论选股价格带仍为 3~500；E1/E2/E3、T+1 行为不变。
- 全量 `ruff` + `pytest` 绿。

## 5. 风险与非目标

- **风险**：改动 C 把盘中出场从统一口径改为按策略——TREND/CHAN 止盈放宽到 20%/18%、MEAN_REVERSION 收紧到 6% 且不浮动，会改变既有持仓的出场行为（更贴合各策略意图，但与「历史统一 6%/20%/8%」不同）。属预期内的对齐效果。
- **非目标**：不改缠论引擎、不改价格带、不动止损数值、不引入云端多出的 job（本地已是超集）、不做架构重构。
