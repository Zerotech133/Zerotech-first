# 缠论 CHAN_V7「增强版」策略优化 — 设计文档

- 日期：2026-09-01
- 作者：zhice-os-v2
- 状态：待评审
- 基线证据：`v1-cn-sv-4`(CHAN) 90 笔已平仓的实证分析（胜率 50%、盈亏比 1.24、总盈亏 +35,951；持仓天数模拟甜区 10–15 交易日）

## 1. 背景与目标

对缠论 v1（注册名 `CHAN`，纯日线单级别、几何法二买/三买选股）的模拟交易做实证分析后，确定 4 项优化。核心约束：

- **`CHAN`(v1) 是 A/B 框架的纯净对照组，且是当前唯一在实盘下单的策略**。本设计**不修改 `CHAN` 的任何选股/出场行为**，全部改进落在新版本 `CHAN_V7`，两者并行跑、可量化对比后再决定 promote。
- 已有实验框架：`strategy_registry_service.py:24-40` 注册 `CHAN`/`CHAN_V2`~`V6`；`exit_policies.py:41-138` 按 `strategy_type` 差异化出场；出场链在 `workflow_service.py:1031-1120`。

四项优化：

1. **诊断并修复"近期当日平仓"漏损**（全局，与版本无关）
2. **入场加三门**：周线方向门 + 大盘 regime 门 + 30 分钟确认（`CHAN_V7`）
3. **收紧止损 6%→5% + 15 交易日时间止损**（`CHAN_V7`）
4. **动态出场**：跌破日线笔中枢下沿即出，叠加在机械出场之上（`CHAN_V7`）

（第 5 项止盈 18% 偏高本轮不动，`CHAN_V7` 沿用 18% 止盈。）

## 2. 现状架构（改动锚点）

| 关注点 | 位置 | 现状 |
|---|---|---|
| v1 几何选股 | `chan/strategy.py::select_candidates` | 纯几何 `detect_bs_point`，不调用任何门 |
| v2 规则书选股 + 门开关 | `chan/strategy_v2.py`（`weekly_filter`/`market_gate`/`intraday_confirm` 参数） | `CHAN_V3~V6` 用 `partial` 注册 |
| 三门函数（已存在） | `engine.py:212 weekly_trend_permits_long`、`market_regime.py:73 market_regime_permits_entry`、`intraday.py:64 intraday_confirms_buy` | 已实现，v1 未调用 |
| 出场参数 | `exit_policies.py` `EXIT_POLICY_REGISTRY[strategy_type]` | `ExitPolicy` **无时间维度**、无动态出场钩子 |
| 出场链 | `workflow_service.py:1031-1120` | 止盈→移动止损→止损；有 T+1 锁(1048) |
| 策略注册 | `v2_strategy` / `v2_strategy_version` 表 | 仅 `CHAN`/均值回归有真实持仓 |
| 交易日历 | `v2_market_calendar` 表（18,992 行） | 用于时间止损"交易日"计数 |

## 3. 详细设计

### 3.1 第 1 项：诊断"当日平仓"漏损（先诊断，后修）

**现象**：`CHAN` 8 月末批 12 笔中 9 笔 `hold_days=0` 当日平仓、多笔恰亏 ~0.1%（双边手续费），与出场链的 T+1 锁（`workflow_service.py:1048`）矛盾。

**诊断步骤（不先改代码）**：
1. 取这 9 笔 `position_id`，关联 `v2_execution_record` / `v2_fill_record` / `v2_decision_review`，读 `exit_kind`、成交时间戳、触发原因。
2. 判定来源属于以下哪类：
   - (a) 回测/重放批量写入的同日时间戳（非真实盘中）→ 属数据口径，非 bug，标注并从实盘绩效中剔除；
   - (b) 某路径绕过 T+1 锁在当日强平 → 真 bug，定位后修；
   - (c) "信号消失即平"之类的隐藏出场逻辑 → 确认是否预期。
3. **修复动作待诊断结论确定**，在实施计划中作为"诊断 → 条件修复"两步执行，不预设修法。

**验收**：给出这 9 笔的确定来源，若为 bug 则修复并回归；若为数据口径则记录口径并修正绩效统计。

### 3.2 第 2 项：CHAN_V7 入场三门

**实现方式**：给 `chan/strategy.py::select_candidates` 增加三个可选门开关参数（`weekly_filter=False`、`market_gate=False`、`intraday_confirm=False`），**默认全 False，保证 `CHAN` 对照组行为逐字节不变**。`CHAN_V7` 通过 `partial(select_candidates, weekly_filter=True, market_gate=True, intraday_confirm=True)` 注册（与 `strategy_v2` 的 `CHAN_V3~V6` 模式一致）。

**门的调用顺序**（在几何 `detect_bs_point` 选出候选之后、评分排序之前）：
1. **大盘 regime 门**（全局）：当日大盘 regime 不允许 → 直接返回空候选（整体空仓）。
2. **周线方向门**（逐股）：个股周线非多头 → 剔除。
3. **30 分钟确认**（逐股）：分钟级买点不确认 → 剔除。复用 `intraday.py`，数据取 `v2_bar_minute`（CN 有覆盖）。

**风险与监控**：三门叠加会显著减少候选。实施时须记录"过门前/过门后候选数"，若长期近乎 0 信号则回报，评估放宽（如降为双门）。复用现成门函数，不新写选股逻辑。

**2026-09-02 修订：已降为双门（周线 + 30 分钟），去掉大盘 regime 门。** 上线首日 CHAN_V7 候选为 0，诊断确认：v1 选股满额 20、周线门与 30 分钟门均不砍，唯一元凶是大盘门一票否决——其判据（等权指数 < 26 周慢 EMA 且 EMA 下行）在指数反弹初期严重滞后（指数近 6 周 +9.8% 反弹，但慢 EMA 未转头），导致长期空仓、A/B 无建仓数据。去大盘门后候选恢复 20。系统性风险改由周线方向门兜底。

### 3.3 第 3 项：收紧止损 + 时间止损

**ExitPolicy 扩展**（`exit_policies.py`）：`ExitPolicy` 增加 `max_hold_days: int | None = None`（时间止损，交易日数；None=不启用，保证既有策略向后兼容）。

**新增注册项** `EXIT_POLICY_REGISTRY["CHAN_V7"]`：
- `stop_loss_pct = 0.05`（6%→5%）
- `take_profit_pct = 0.18`（沿用 CHAN，本轮不动）
- `use_trailing = True`、`trailing_activate_pct = 0.05`、`trailing_trail_pct = 0.035`（沿用 CHAN）
- `max_hold_days = 15`

**出场链改造**（`workflow_service.py`）：在止盈/移动止损/止损判断链中，追加**时间止损**分支——若 `policy.max_hold_days` 非空，且从 `opened_at` 到当前的**交易日数 ≥ max_hold_days**（用 `v2_market_calendar` 计数），则强制平仓，`exit_kind="TIME_STOP"`。时间止损与 T+1 锁兼容（仍受 `same_day_sell_allowed` 约束）。

### 3.4 第 4 项：中枢下沿动态出场（叠加）

**ExitPolicy 扩展**：增加 `dynamic_stop: str | None = None`；`CHAN_V7` 设 `dynamic_stop = "CHAN_ZS"`。

**语义**：机械止损/止盈/时间止损**全部保留**，额外叠加一条提前离场：持仓期形成的**日线笔中枢下沿**被现价跌破 → 平仓，`exit_kind="CHAN_EXIT"`。中枢计算复用 `engine.py::build_zs_list`（czsc 笔）。

**执行时机（性能约束）**：中枢计算较重，原设计为**按日频评估**（每日收盘后对 `CHAN_V7` 持仓算一次日线中枢）。

**实现说明（2026-09-01 落地修订）**：实际实现将 CHAN_ZS 动态出场内联到盘中出场链 `run_intraday`（`workflow_service.py`），以 `policy.dynamic_stop == "CHAN_ZS"` 严格门控——仅 CHAN_V7 进入，CHAN 及其它 `dynamic_stop=None` 的策略是 no-op。选择内联而非独立日频 job 的理由：(1) CHAN_V7 为 STAGING、持仓规模小，盘中重算中枢的开销可接受；(2) 复用既有出场链的 `can_sell`/T+1/平仓/复盘链路，避免重复实现；(3) 有 try/except + warning 兜底，异常不影响其它持仓。**后续若 CHAN_V7 持仓规模上升，再抽为独立日频 job + 中枢缓存优化。**

### 3.5 版本落地

- `v2_strategy` 增一行：`strategy_id` 形如 `v1-cn-strat-7-v7`，`market='CN'`，`strategy_type='CHAN_V7'`，`status='ACTIVE'`（供调度器选股/回测）。
- `v2_strategy_version` 增一行：`strategy_version_id` 形如 `v1-cn-sv-7`，`status='STAGING'`（**不直接实盘下单**，先 A/B 观察）。
- 调度器将 `CHAN_V7` 纳入每日选股与回测流程。

## 4. 数据契约（口径修正）

分析中发现 `v2_position.pnl_pct` 两批口径不一致（LONG 批百分数、BUY 批小数，差 100 倍）。**本设计不依赖该字段**；`CHAN_V7` 落库时统一 `pnl_pct` 为小数比率口径（`pnl_amount / (open_price*open_qty)`），并在实施计划中加一个校验用单测。历史数据是否回填口径由后续单独决定，不在本轮范围。

## 5. 测试策略

- **对照组回归（最关键）**：断言 `select_candidates` 三门默认 False 时，`CHAN` 输出与改动前逐条一致。
- 门过滤：周线/大盘/30分钟各自的允许/拒绝分支。
- 时间止损：持仓交易日数达 `max_hold_days` 触发 `TIME_STOP`；未达不触发；T+1 边界。
- 动态出场：跌破中枢下沿触发 `CHAN_EXIT`；未跌破不触发。
- 端到端：**须真实跑通一遍选股→建仓→出场，不能只靠打桩**（历史教训：单测打桩曾掩盖 czsc 运行时问题；`czsc` 须钉 `==0.10.12`）。

## 6. A/B 验收指标

`CHAN`(对照) vs `CHAN_V7` 并行，用 `v2_backtest_run` / 归因表对比：胜率、盈亏比、总收益、平均持仓天数、"3–10 天死亡地带"亏损占比、信号数量。目标：V7 盈亏比与总收益优于 v1，且死亡地带亏损显著收窄。

## 7. 范围外（本轮不做）

- 第 5 项止盈 18% 调整。
- `v2_position.pnl_pct` 历史数据回填。
- `CHAN_V7` 直接 promote 到实盘下单（先 STAGING 观察 A/B）。
- 无关重构。
