# 订阅者跟单模拟盘（Subscription Mirror Sim）设计

- 日期：2026-09-11
- 分支：`feat/subscription-mirror-sim`（基于 main）
- 相关记忆/特性：[[subscription-observation]]（订阅只读观察平台运行）、[[multi-user-overview]]（USER/账户/运行实例/执行模式）
- 生产：207.56.221.140（Ubuntu24 amd64 Docker Compose，`MULTI_USER_EXECUTION_MODE=legacy`，两市场 `SCHEDULER_MARKETS=US,CRYPTO`，通知 `broadcast`）。**未经明确授权不部署**。

## 1. 背景与目标

订阅内置策略当前只给**只读观察**平台统一运行（持仓/成交/信号），不给订阅者个人账户。本设计新增能力：

**订阅者可自助为已订阅的内置策略开一个"跟单模拟盘"**——按平台该策略的实时组合权重，把自己设定的初始本金等权重地跟进去，得到个人的模拟持仓与盈亏。

硬约束与产品决策（均经用户拍板）：
1. **用户自助、可选**：用户自己在订阅策略详情页操作；不开也完全正常（默认仍是只读观察）。
2. **绝不影响订阅策略的消息推送**：既有"订阅→信号扇出"链路零改动、收件人集合不变、不重复推送。
3. **跟单方式 = copy-trading 组合镜像**（非用户独立撮合、非切 enforced）。
4. **定仓 = 按仓位百分比**；**分母 = 平台该策略实时总持仓市值**（组合成分镜像，用户随平台换仓逐步趋向满仓）。
5. **只跟未来、从空仓起步**：开户前平台已持有且之后未动的标的永不进入用户篮子。
6. **撤订/过期 → 停止跟单 + 冻结只读**（持仓/盈亏保留可查）；**重新订阅 → 恢复跟单**。
7. **入口 = 订阅策略详情页**；**每订阅策略限一个跟单账户**。
8. **legacy 执行模式下必须可用**（不依赖 enforced）。

## 2. 架构与隔离边界（方案 A：独立子系统）

跟单模拟盘是一套**与现有 `v2_user_account`/`v2_user_strategy_instance`、enforced 执行链、`record_fill`/订阅扇出/通知路由完全解耦**的独立子系统。理由：

- 复用 `v2_user_strategy_instance` 会把账户绑到 enforced 执行链——一旦平台将来切 `enforced`，这些实例会突然开始真实 owned 撮合，与"镜像跟单"语义冲突且双跑。故**不复用**。
- 独立表 + 独立定时任务，保证：legacy 下照跑、切 enforced 也不误触发、推送零影响。

数据源（只读）：平台该策略的持仓/成交，取自既有 `v2_position` / fill 链（与订阅观察同一数据口径，仅平台 legacy 实例 `ustrat-legacy-*`）。

## 3. 数据模型（迁移 0032）

三张新表；downgrade 直接 drop（无历史兼容包袱）。

### `v2_mirror_sim_account`
| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `mirror_account_id` | String(64) PK | 生成 `msim-{hex}` |
| `user_id` | FK v2_user.user_id | owner，取自 JWT |
| `strategy_id` | FK v2_strategy.strategy_id, **RESTRICT** | 订阅的内置策略 |
| `market` | String(16) | 取自策略 |
| `initial_capital` | Numeric(24,8) CHECK `>0` | 用户设定 |
| `cash_balance` | Numeric(24,8) CHECK `>=0` | 现金；初始=initial_capital |
| `status` | String(16) CHECK IN(`active`,`frozen`) | 撤订→frozen |
| `created_fill_cursor` | String(64) nullable | 开户时平台该策略最新成交 id；实现"只跟未来" |
| `created_at` / `updated_at` | tz-aware | |
| | **UNIQUE(user_id, strategy_id)** | 每策略限一个 |

### `v2_mirror_sim_position`
`id` PK / `mirror_account_id` FK(cascade) / `symbol` / `quantity` Numeric / `avg_cost` Numeric；**UNIQUE(mirror_account_id, symbol)**。

### `v2_mirror_sim_fill`（模拟成交审计流水）
`id` PK / `mirror_account_id` FK / `symbol` / `side`(BUY/SELL) / `quantity` / `price` / `reason`（触发来源，如 `rebalance@<sweep_ts>`）/ `created_at`。（再平衡天然幂等收敛，不设 source 唯一键。）

## 4. 镜像折算：目标权重再平衡

**触发**：独立定时任务 `mirror_sim_job`（APScheduler IntervalTrigger 每 5 分钟，建议值可配；仅 sql 后端；`max_instances=1` + `coalesce`）。逐 `active` 账户处理，单账户失败隔离（per-account 事务，异常回滚+日志，继续其它）。

**平台版本口径**：策略→平台运行版本沿用订阅观察同一映射（strategy_id → 平台 legacy 实例 `ustrat-legacy-*` 绑定的 strategy_version），持仓/成交只取该平台版本。

**"只跟未来"**：标的成为**受跟标的**的条件 = 平台对它存在 `fill_id > created_fill_cursor` 的**入场成交**（用户开户后平台才建/再建的仓）。每 sweep 由该谓词动态计算受跟集合。

**每 sweep（逐 active 账户）**：
1. 先核对订阅状态（见 §6 生命周期）。
2. 读平台该策略版本当前持仓 `{symbol: 平台数量}`，最新市价算 `平台市值(S)`。
3. 分母 `平台总持仓市值 = Σ 所有平台持仓市值`（含未受跟标的 → 用户逐步趋向满仓）。若为 0 → 目标全 0（防除零）。
4. 受跟标的目标权重 `w(S)=平台市值(S)/平台总持仓市值`；目标 `用户市值(S)=w(S)×用户当前权益`（权益=现金+持仓市值）。
5. **再平衡**：**先卖后买**释放现金；按最新市价把每受跟标的买/卖到目标市值；平台已清仓的受跟标的目标 0 → 卖出。买入受可用现金封顶、**不透支**。
6. **防churn阈值**：仅当 `|目标市值−当前市值|` 超过权益的阈值（如 1%）才下调整单。
7. 每笔调整写 `v2_mirror_sim_fill`，更新持仓（数量/均价）与现金。

**收敛幂等**：平台状态不变时二次 sweep 无新单。

**盯市盈亏**（读视图）：持仓按最新市价（订阅观察同一"日线基+新鲜快照"取价源）算市值与未实现盈亏；总权益=现金+持仓市值；总盈亏=总权益−initial_capital。缺价标陈旧。

## 5. API（owner 隔离，`/my/` 前缀）

- `POST /my/mirror-accounts` `{strategy_id, initial_capital}` → 建账户。
  - 校验：活跃会员（`require_active_membership`）+ 对该策略有活跃订阅（`is_subscription_active`）+ 策略为内置可观察（`owner_user_id IS NULL` 且 PUBLIC）+ 该 user+strategy 无账户。
  - 置 `created_fill_cursor`=平台该策略最新成交 id、`status=active`、`cash_balance=initial_capital`。
  - 错误：`mirror.membership_required` / `mirror.subscription_required` / `mirror.not_observable_strategy` / `mirror.invalid_capital` / `mirror.account_exists`(409)。
- `GET /my/mirror-accounts` → 本人账户摘要列表。
- `GET /my/mirror-accounts/{strategy_id}` → 详情：账户 + 持仓（盯市：数量/均价/现价/市值/未实现盈亏）+ 总权益/总盈亏 + 近期模拟成交。**冻结也可查**；看自己账户不要求订阅仍活跃（仅建账户与继续跟单要求订阅活跃）。owner 隔离（他人 404）。

**YAGNI**：暂不做手动关闭/重置、多账户、跨市场手数取整（入口按策略市场约束，当前为 CRYPTO）。

## 6. 生命周期（由 `mirror_sim_job` 对账，不碰 admin 撤订路径）

每 sweep 开始对账订阅状态：
- 订阅活跃 + 账户 `active` → 正常再平衡。
- 订阅已撤销/过期 → 账户转 `frozen`（停止再平衡，持仓/盈亏冻结可查）。
- 重新订阅 + 账户 `frozen` → 恢复 `active` 续跟。

撤订后最迟一个 sweep 周期内冻结（换取零侵入 admin 撤订路径，保持隔离）。admin 的 B2 撤订交易清理只作用于 enforced owned 实例，与本独立子系统无交集。

## 7. 前端（订阅策略详情页 `StrategyDetailPage`）

- 订阅者查看已订阅内置策略、且该策略尚无跟单账户 → 显示 **"开模拟盘"** 按钮 → 弹窗填**初始本金**（币种取市场，如 USDT）→ 建成刷新。
- 页内新增 **"我的模拟盘"** 区：账户状态(active/frozen)、总权益/现金/总盈亏、持仓表（现价盯市+未实现盈亏）、近期模拟成交；`frozen` 显示"订阅已失效，已冻结，重新订阅恢复跟单"。
- 新增 `frontend/src/services/mirrorSimApi.ts`（owner 隔离读写，复用 api 层 token/401/错误码翻译）。

## 8. 推送隔离（硬约束的落地与验证）

- 跟单子系统全部在独立表 + 独立任务内，**零调用** `record_fill` / `route_subscription_signal` / `route_fill` / `notify_fill`。
- 订阅信号扇出收件人 = `list_active_subscriber_ids(strategy_id)`（订阅表），与是否存在跟单账户**无关**，不叠加、不重复。
- 207 通知模式 `broadcast` 下各 `route_*` 本就 no-op；本设计不改变该行为。
- **专项测试**断言：跟单任务/建账户流程不触发任何 `record_fill`/扇出/广播调用；订阅收件人集合在建账户前后不变。

## 9. 错误处理与边界

1. 现金不足：先卖后买释放现金，买入按可用现金封顶，`cash_balance>=0` 不透支。
2. 缺现价：跳过该标的本轮再平衡，下轮重试，不崩账户。
3. 平台空仓/总市值=0：目标全 0（防除零），卖出转现金。
4. 防churn阈值（权益 ~1%）避免噪声交易。
5. 单账户失败隔离（per-account 事务）。
6. 建账户去重 `UNIQUE(user_id,strategy_id)` → 409。
7. 策略删除 FK **RESTRICT**（有跟单账户不许删策略）。
8. 数量精度：CRYPTO 用 Decimal 小数量；分市场手数取整留后续。

## 10. 测试策略

- **单元**：建账户门禁（无会员/无活跃订阅/非内置/重复各拒、cursor 置最新成交）；再平衡引擎（forward-only 排除开户前标的、分母=平台总市值、买卖到目标、现金封顶、防churn阈值、平台空仓、缺价跳过、除零防护）；冻结/恢复（撤订→frozen、复订→active、冻结可查）；盯市盈亏；收敛幂等（二次 sweep 无新单）。
- **API**：建/列/详情 + owner 隔离 + 403/404/409 + errors.json(zh/en) `mirror.*` 新码。
- **迁移 0032**：upgrade/downgrade + 约束（unique/check/FK RESTRICT）。
- **real_pg**：sweep 对真实持仓数据 + 冻结对账的轻量线性化。
- **前端**：mirrorSimApi 契约 + 详情页"开模拟盘"/"我的模拟盘"视图（vitest）；E2E 追加可选。
- **🔒 推送隔离专项**：见 §8。

## 11. 门禁与部署

- 门禁：后端 `pytest -q` + `ruff check src tests` + `mypy`（无新增债）+ 前端 vitest/build + real_pg 专项；`alembic heads` 单一 head=0032。
- 部署：需用户另行明确授权；207 为 Docker Compose，迁移 0032 加表（不碰既有数据），scheduler 注册 `mirror_sim_job`。两市场配置不变。

## 12. 单元划分（供 writing-plans 拆解）

- **单元 1（迁移+领域+仓储）**：0032 三表 + ORM + domain 值对象 + 仓储（账户/持仓/成交读写、平台持仓/成交读取谓词）。
- **单元 2（再平衡引擎+服务+任务）**：`mirror_sim_service`（建账户校验、sweep 再平衡、订阅对账冻结/恢复、盯市视图）+ `mirror_sim_job` scheduler 注册。
- **单元 3（API+错误码）**：`/my/mirror-accounts` 三端点 + schemas + errors.json + 架构白名单（若跨表直连）。
- **单元 4（前端）**：mirrorSimApi + StrategyDetailPage 开模拟盘/我的模拟盘 + vitest（+E2E 可选）。
- **单元 5（推送隔离专项 + 门禁总核）**。
