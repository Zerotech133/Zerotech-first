# 订阅者观察平台策略产出 Design

**日期**：2026-09-10
**状态**：已评审，待写实现计划

## 背景与根因

用户反馈：admin 给 test 账户分配会员并订阅了缠论策略（`v1-cn-strat-4`），但 test 登录后在前端看不到缠论的持仓和交易记录。

系统化调查结论（非 bug，是产品语义缺口）：
- admin 操作完全落库成功（会员 permanent + 订阅 active）。
- **订阅（`v2_strategy_subscription`）当前唯一的功能性消费点**是 `notification_routing` 的信号扇出——即订阅只驱动该策略的入场/出场信号推送（`list_active_subscriber_ids`）。
- 持仓/交易在前端的可见性走 **owner 隔离**：`/my/positions`、`/trades` 只返回 `user_strategy_id ∈ 本人运行实例` 的数据。
- test 拥有 0 个运行实例，平台缠论的持仓属于平台统一运行（`ustrat-legacy-*`），按 owner 隔离天然不归 test。
- 因此「订阅」与「持仓/交易可见」之间缺了一条链路。

产品意图（用户澄清）：**用户订阅内置策略后，应能享受并看到该平台策略的全部产出**——持仓、交易记录、入场/出场信号推送；且订阅的策略出现在「我的策略」列表里（只读观察）。

## 现状要点（已核实）

- 平台缠论持仓：`strategy_version_id = v1-cn-sv-4`（`v1-cn-strat-4` 的版本），共 106 条，其中 105 条 `user_strategy_id = ustrat-legacy-v1-cn-sv-4:CN`。
- 全局持仓 `user_strategy_id` 前缀当前全为 `ustrat-legacy-*`（平台统一运行）；用户私有实例为 `usi-*`（当前库中无，未来会有）。
- 成交数据源：`v2_fill_record`，经 `fill → execution → request → intent.user_strategy_id` 关联实例（`trades.py` 已有此 join 逻辑）。
- **入场/出场信号推送已实现**：`notification_routing` 对内置策略（owner 为 NULL）成交按方向构造 `entry(BUY)/exit(SELL)` 信号，扇出给 `list_active_subscriber_ids(strategy_id)` 经通知 outbox 推送。
- 「我的策略」列表端点 `GET /my/strategies` 后端已返回公共策略 + 本人私有；前端 mine 页当前按 `visibility=PRIVATE` 过滤，把内置的滤掉了。

## 目标

让订阅者在前端只读地看到其**有效订阅**的内置策略的平台统一运行产出：
1. 持仓（只读观察）
2. 交易记录 / 入场出场信号历史（同源，一份数据两种呈现）
3. 订阅的内置策略出现在「我的策略」列表并标记「已订阅」（只读）

信号推送（入场/出场）后端已就绪，本次沿用，不重复实现。

## 架构原则

把「订阅者观察平台策略产出」做成一条**独立、只读、与 owner 隔离完全解耦**的链路：
- 不改动 `/my/positions`、`/trades`、`strategy_detail` 的现有 owner 隔离与裁剪逻辑。
- 新增一个聚合 service + 一组只读端点，端点保持薄。
- **唯一权限门**是订阅校验；**不做数据脱敏**——数据源已限定平台统一运行（`ustrat-legacy-%`），这些 `account_id`/`user_strategy_id` 都是平台系统自己的标识，不涉及任何其他用户隐私，藏之无益、徒增复杂度。

## 单元 1：后端观察链路

**新建** `application/services/subscription_observation_service.py`，承载数据聚合；端点薄。

**数据源与过滤口径**（全部限定平台统一运行）：
- 策略 → 版本集：`db.list_strategy_versions()` 取 `strategy_id` 的所有 `strategy_version_id`。
- **持仓**：`db.list_positions()` 过滤 `strategy_version_id ∈ 版本集 AND user_strategy_id LIKE 'ustrat-legacy-%'`。
- **成交/信号**：`v2_fill_record` join `execution → request → intent`，限定 `intent.user_strategy_id LIKE 'ustrat-legacy-%'` 且 strategy 匹配；每笔按方向投影 `signal = entry(BUY) / exit(SELL)`。

**新端点**（均 `get_current_user` + 订阅校验，**非** owner 校验）：

| 端点 | 作用 |
|---|---|
| `GET /api/v1/my/subscriptions/{strategy_id}/positions` | 该平台策略的持仓 |
| `GET /api/v1/my/subscriptions/{strategy_id}/trades` | 该平台策略的成交/信号（含 entry/exit） |

订阅策略列表**不新建端点**——复用现有 `GET /billing/subscriptions`（已返回本人订阅 strategy_id/effective_status/is_permanent，见 B3-6）；策略名称/市场从 `GET /billing/strategies` 或 catalog join 取。

- 进入即校验 `is_subscription_active(user.user_id, strategy_id)`，否则 **403**（未订阅/过期/撤销都挡在门外）。
- **复用现有持仓/成交序列化**（`_position` 等），返回完整字段，不构造脱敏 view。
- 持仓字段：`ts_code/market/direction/open_price/close_price/open_qty/close_qty/pnl_amount/status/opened_at/closed_at`（沿用 Position 现有字段）。
- 成交字段：在成交 view 基础上加 `signal`（entry/exit）。

## 单元 2：前端「我的策略」纳入订阅

**新建** `services/subscriptionObservationApi.ts`（对齐现有 `api` 封装 + AbortSignal）：
- `getSubscriptionPositions(strategyId, signal?)` / `getSubscriptionTrades(strategyId, signal?)`
- 订阅策略列表复用现有 `listSubscriptions`（`/billing/subscriptions`）+ `listGrantableStrategies`/catalog 取名称市场，不新增列表端点。

**修改** `MyStrategiesPage.tsx`：
- 额外调 `listMySubscriptions`，把已订阅的内置策略以「已订阅」分组 / `Tag` 显示在「我的策略」里。
- 订阅策略为**只读**：不渲染编辑/删除/发布/新建版本等自建操作；与自建私有策略视觉区分。
- 点击订阅策略行 → 进 `StrategyDetailPage`。

## 单元 3：前端详情页观察区 + E2E

**修改** `StrategyDetailPage.tsx`，当前用户对该策略 `is_subscribed` 时新增两块（Tab 或分区）：
- **平台持仓**：标的/市场/方向/开平价/数量/盈亏/状态/开平时间。
- **成交 · 信号**：成交时间/标的/方向/价格/`signal`（入场/出场）——交易记录与信号历史同表，`signal` 列标注。
- 两块各自独立 loading、失败互不影响；非订阅者不渲染（后端 403 兜底）。

## 测试策略

- **后端**：`subscription_observation_service` 单测——过滤口径（只 `ustrat-legacy-%`、版本匹配、排除 `usi-*`）、entry/exit 投影；端点测试——订阅者 200 / 未订阅 403 / 订阅撤销后 403。
- **前端**：`subscriptionObservationApi` 契约测试；`MyStrategiesPage` 订阅策略只读标记（无自建操作按钮）；`StrategyDetailPage` 观察区渲染 + 未订阅不渲染。
- **E2E**：admin 给 test 订阅缠论 → test 登录 → 「我的策略」看到已订阅缠论（只读）→ 详情页看到平台持仓与成交/信号。

## 合规决策（重要，审计留痕）

本功能经用户明确决策，**放开** `trades.py` 既有的合规红线「内置策略逐笔成交（含实现盈亏）对非 ADMIN 一律隐藏」。
- **依据**：付费订阅授权——用户订阅了该内置策略，即有权查看其产出（含逐笔成交与盈亏）。
- **边界**：仅限用户**本人有效订阅**的内置策略（非全量内置）；只读；订阅校验挡门；退订/过期即不可见。
- **不改动** `/trades`、`/my/positions` 等既有 owner-scoped 端点的红线行为——放开只发生在新的订阅观察端点内，老端点对非 ADMIN 仍隐藏内置成交。
- 数据口径确认：内置策略的持仓与成交归属标识均为 `ustrat-legacy-*`（持仓 105/106、成交 913/976），口径一致；`trades.py` 注释所述「intent.user_strategy_id IS NULL」已过时（Phase B 起 legacy 用合成 id），少量 NULL 无法关联策略自然排除。

## 安全边界

- 订阅校验 `is_subscription_active` 挡门（403）；退订/过期即不可见——这是放开红线后的**唯一授权门**，必须严格、有测试覆盖（未订阅/退订后 403）。
- 数据只取平台 `ustrat-legacy-%` → 天然排除其他用户私有实例（`usi-*`）。
- 全链路只读，不提供任何写操作。

## 无迁移

纯读现有数据（`v2_position` / `v2_fill_record` / `v2_strategy_subscription`），不改 schema。

## 非目标（本次不做）

- 不让订阅者部署自己的运行实例（订阅 ≠ 部署）。
- 不改动信号推送机制（已实现，沿用）。
- 不做跨策略聚合页（入口就近于「我的策略」与策略详情页）。
