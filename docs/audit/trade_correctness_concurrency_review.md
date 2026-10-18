# 量化交易闭环代码评审报告（交易正确性 / 并发 / 数据一致性）

- 评审对象：`src/zhice_os_v2`（FastAPI + SQLAlchemy 量化交易闭环）
- 评审范围：workflow_service / approval_execution / conflict_resolver / execution / trade_plan / position_ops / risk / pipeline / trade_execution_pipeline / position_sizing / simulated broker / models + 支撑代码（scheduler、approval.py、positions.py、account_stats、exit_policies、domain/execution/*）
- 评审日期：2026-02（静态精读 + grep 调用点验证，未改代码）
- 严重级别：P0 会算错钱 / P1 逻辑漏洞 / P2 健壮性 / P3 质量

---

## 0. 结论摘要（一页纸）

| # | 级别 | 发现 | 位置 |
|---|------|------|------|
| 1 | **P0** | 同一授权被多条路径**重复全额执行**，且同一 `position_id` 被**覆盖而非累加** → 超预算买入 + 持仓/成交记录互相矛盾 | `workflow_service.py:1133-1170`、`725-807`、`execution_service.py:36`、`sqlalchemy_db.py:495-503` |
| 2 | **P0** | 执行链路**无幂等键、授权无终态**：approval 执行后永远保持 APPROVED，order_id/position_id 由 `request_id` 派生 → 任何路径重放都命中同一行 | `approval.py:11-16`（无 EXECUTED）、`workflow_service.py:764`、`approval.py(router):58-81` |
| 3 | **P1** | spec 信号平仓路径 `_close_position` 绕过 **T+1 与可卖性（涨跌停/停牌）** 检查 | `workflow_service.py:861-866` vs `:980-985`、`market_rules.py:36-55` |
| 4 | **P1** | 手动批准立即执行 + 盘中入场循环**双路径执行同一授权**（3 次封顶，全量 qty） | `api/routers/approval.py:76`、`workflow_service.py:1097-1174` |
| 5 | **P1** | 盘前/盘中**跨进程无锁**：API 手动触发 vs scheduler、catch-up 与 09:25 job 竞态；CN 盘中 job 无 `max_instances/coalesce` 声明 | `api/routers/workflow.py:15-28`、`scheduler.py:89-116,209-239` |
| 6 | **P1** | 部分成交剩余量不追踪；入场循环二次执行**按全量 qty 重买**，仓位预算被翻倍 | `workflow_service.py:1133-1139`、`simulated.py:143-155` |
| 7 | **P2** | `save_trade_plan`/`save_approval` upsert **无状态守卫** → 同日盘前重跑会把 CANCELLED/REJECTED 计划与授权**复活**为 PENDING/APPROVED | `sqlalchemy_db.py:296-315,388-417`、`workflow_service.py:585-596` |
| 8 | **P2** | `settings.sim_max_positions / sim_max_total_exposure_pct / trade_approval_expire_seconds` **定义但全库无读取**；仓位约束 C2 风险预算、`POSITION_RATIO_TOO_HIGH`、`LIQUIDITY_INSUFFICIENT` 规则**恒不生效**（调用点未传参） | `settings.py:36,45-46`、`position_sizing_service.py:25-26,115`、`risk_service.py:36-42` |
| 9 | **P2** | `OrderResult.fee`（佣金/印花税/过户费/滑点）**全库无消费者**——费用只是装饰，不进成本/盈亏 | `simulated.py:189-203`（grep `.fee` 0 命中） |
| 10 | **P2** | 平仓 pnl 落库**两步非原子**（先 save_position 后裸 UPDATE pnl）；异常路径 CLOSED 行 pnl 恒 NULL | `workflow_service.py:1368-1398` |
| 11 | **P2** | ST 股涨跌停幅（5%）检测**失效**：`_load_security_names` 恒返回 `{}`，`can_sell/can_buy` 拿到 name=None → ST 按 10% 判板 | `workflow_service.py:289-291`、`cn.py:44-46` |
| 12 | **P2** | API 层 `positions.py` 等 **7+ 处异常路径 session 不关闭**（泄漏连接） | `positions.py:30-55,66-82,100-109,117-159,172-198,204-220` |
| 13 | **P2** | 双后端不一致：`_realized_pnl` 等 12+ 个 workflow 助手**无视 `persistence_backend` 直读生产 PG**；memory 后端隔离形同虚设 | `position_sizing_service.py:42`、`workflow_service.py:74,111,134,165,203,237,320,344,367,415,1594` |
| 14 | **P3** | 死代码：`PositionOpsService.add_position/tiered_take_profit`、`evaluate_take_profit`、`t_plus_one_locked` 全库无调用点 | `position_ops_service.py:35,145`、`entry_signal.py:66,75` |

> 标注说明：**「未修且危险」**＝上表 1–6；**「已修但残留同构问题」**＝第 7、12、13 项及 §7 列表（修了 A 形态、同构 B 形态仍在）。

---

## 1. 交易正确性

### 1.1 [P0] 同一授权重复全额执行 + 持仓被覆盖（未修且危险）

**链路**：盘中入场循环对每个 APPROVED 授权执行 `_auto_execute`（`workflow_service.py:1169`）→ `ExecutionService.apply_fill` 建仓 `position_id=f"pos-{result.order_id}"`（`execution_service.py:36`），而 order_id 恒为 `f"{approval.request_id}-sim"`（`workflow_service.py:764`）。

- 循环对**同一授权**最多执行 `MAX_ENTRIES_PER_PLAN=2` 次（`workflow_service.py:1133-1134,1186`），每次都是**全量 approval.qty**（`workflow_service.py:768`），不区分"首建/加仓"、不追踪部分成交剩余量。
- 第二次执行的 `position_id` 与第一次**完全相同** → `save_position`（`sqlalchemy_db.py:471-504`）走 UPDATE 分支**整体覆盖** `open_qty/open_price`，而非批次制"新建独立 lot"。
- `_record_fill` 的 `fill_id=fill-{order_id}`（`workflow_service.py:1451`）靠 `on_conflict_do_nothing` 去重 → **fill 表只留第一条**，而 position 行却是最后一次执行的值 → 两表对同一笔交易口径不一致。

**影响**：① 总买入量 = 2× 预算（超出 C1 组合预算制仓位上限）；② 持仓账显示 1 个 lot 的量，成交明细显示 2 笔 → 权益/盈亏/仓位统计错；③ 若两次成交价不同，持仓成本静默变成后一次的价格。**会算错钱（模拟账本），且无任何幂等键兜底。**

**触发场景（生产可达）**：CN `sim_auto` 开启 → 盘前自动批准 → 09:35–14:45 入场窗内 E1/E2/E3 满足 → 首次成交；同轮后续周期价≥上次成交价再次满足 → 第二次全量重买。spec 信号路径同样会被入场循环重复执行（计划 EXECUTED 不在 `_DEAD_PLAN` 排除集，`workflow_service.py:1323`）。

**佐证（设计意图 vs 实现）**：`PositionOpsService.add_position`（`position_ops_service.py:35-72`）才是"加仓建独立 lot"的正确实现，但**全库无调用点**（死代码）——入口循环实际走的是覆盖式 `apply_fill`。

### 1.2 [P0] 执行无幂等键、授权无终态（未修且危险）

- `ApprovalStatus` 枚举只有 PENDING/APPROVED/REJECTED/EXPIRED/CANCELLED，**没有 EXECUTED/FILLED**（`domain/approval/approval.py:11-16`）。授权执行后状态永停 APPROVED。
- 唯一防重机制是 `_today_entry_counts` 按当日 BUY intent 计数（`workflow_service.py:1342-1366`）+ `MAX_ENTRIES_PER_PLAN=2`，这是**计数兜底而非幂等**：它只限制次数，不防止"全量重买"和"覆盖"。
- `OrderRequestModel.idempotency_key` 列存在（`models.py:345`），但 `_record_fill` 写入的是 `order_id` 派生值（`workflow_service.py:1493`），**没有基于该键的查重逻辑**（无 SELECT-FOR-UPDATE、无唯一约束消费、无状态机流转检查）。

**影响**：任何"执行→失败→重试"或"多路径同时命中同一授权"都会导致重复执行且互相覆盖。P0 根因。

### 1.3 [P1] spec 信号平仓绕过 T+1 与可卖性检查（未修且危险）

- `run_intraday` 的止盈/止损平仓有双重守卫：T+1 锁（`workflow_service.py:980-985`，`not profile.same_day_sell_allowed and _is_today(...)`）与 `can_sell`（涨跌停/停牌，`:1002-1004,1034-1036`）。
- 但 `run_spec_signals` 的退出路径**直接** `self._close_position(held[ts], px, exit_kind="SIGNAL_EXIT")`（`workflow_service.py:861-866`），`_close_position` 本身**不检查 T+1、不检查可卖性**（`:1368-1398`），按任意现价（含跌停封死价）成交。
- 生产可达性：CN 盘中 `run_spec_signals_job` 每 5 分钟注册（`scheduler.py:370-377`）；只要存在 ACTIVE 的 CN intraday spec 策略，当日信号开仓后同周期 exit 信号即触发**当日平仓**——违反 CN T+1。

**影响**：CN 当日开仓当日被信号平掉（T+1 违规）；跌停封死时模拟盘"顺利止损"（虚假安全感，与 `market_rules.py:1-10` 的设计初衷直接矛盾）。

### 1.4 [P1] 手动批准立即执行 + 入场循环双路径执行（未修且危险）

`POST /approvals/{id}/approve`（`api/routers/approval.py:58-81`）：`center.approve` → `db.save_approval` → **立即** `execution.execute_approved(req, ctx)`（`:76`）→ 直接撮合建仓。该授权随后**仍满足** `_query_entry_approvals` 条件（APPROVED + BUY + 计划非作废，`workflow_service.py:1324-1340`）→ 盘中循环最多再执行 2 次。共 3 次全量执行，position_id 全同（`approval.py router` 的 orchestrator order_id=`{request_id}-sim`，`execution_orchestrator.py:47`；入场循环同款 `workflow_service.py:764`）。

**叠加影响**：① 手动批准的执行**不写任何 fill/intent/execution 记录**（`_record_fill` 只被 workflow 调用，`approval_execution_service.py:40-48` 只调 `apply_fill`）→ 交易记录链缺失、`_today_last_fill_prices`（加仓不摊薄参照）漏算该笔；② `execute_approved` 的建仓**不传 trade_plan_id**（`approval_execution_service.py:40-46`）→ `v2_position.trade_plan_id` 恒 NULL（该缺陷在 workflow 路径已修，此处残留同构问题）。③ orchestrator 走 `get_switches()`（`dependencies.py:49-50` 恒 `{}`）→ 默认 `StrategySwitch` 全 False → 手动批准在 API 进程**实际什么都不做**（`execution_orchestrator.py:36-40`）——除非 sim_auto 持久化开启后由入场循环补刀。

### 1.5 [P2] 部分成交剩余量不追踪 + 二次执行按全量重买

`apply_fill` 接受 PARTIALLY_FILLED 并按 `filled_qty` 入账（`execution_service.py:24-28`，这部分是对的），但：
- `SimulatedBroker._fill_market` 对市价单"剩余量撤单"（`simulated.py:4-5,143-155`）——剩余不挂单；
- 入场循环**不读 `OrderResult.remaining_qty`**，下一轮对同一授权按**全量 qty** 重新下单（`workflow_service.py:1137-1169`）。

**影响**：60% 部分成交后，二次执行再买 100% → 累计 160% 意图量，position 行却只显示末次成交。与 1.1 同根，单列以强调"剩余量丢失"这一独立缺陷。

### 1.6 [P2] SELL 超卖被静默吞掉

`ExecutionService._apply_sell`（`execution_service.py:48-70`）FIFO 逐 lot 消耗；若 `filled_qty > 总持仓`，循环结束后 `remaining > 0` **无任何报错/拒单**，直接返回 last lot。卖出超过持有的部分凭空消失。

### 1.7 [P2] 平仓 pnl 落库两步非原子（已修但残留：同一模式还有 fill 两步）

`_close_position`（`workflow_service.py:1368-1398`）：先 `save_position`（此时领域对象 `pnl_amount=None` → UPDATE 分支跳过 pnl，`sqlalchemy_db.py:500-502`），**再**单独 `_persist_position_pnl` 裸 UPDATE（`:1378-1383`）。两步两个事务：
- 第二步失败/进程崩溃 → 持仓 CLOSED 但 `pnl_amount` 恒 NULL（`_persist_position_pnl` 失败静默返回 False，`:355-356`）；
- 同时 `_close_position` 里的 `realized` 计算与 `_persist_position_pnl` 里的 pnl 计算**重复实现**（`:1381-1387` vs `:359-379`），口径漂移风险。

### 1.8 [P2] 手续费/印花税建模"装饰性"——fee 全库无消费者

`SimulatedBroker._build_fill` 认真算了佣金（含最低佣金）+ 印花税（仅卖）+ 过户费 + 滑点（`simulated.py:183-203`），`OrderResult.fee` 也有字段——但 **grep 全库 `.fee` 零消费**：`apply_fill` 的 `open_price = filled_price` 不含费（`execution_service.py:42`）；`_close_position` 平仓价即现价、费用简化不计（`workflow_service.py:1369` 注释自认）。成本/盈亏/权益全部不含交易成本 → 模拟 PnL 系统性偏高（CN 印花税 0.05% 仅卖单边已漏）。

### 1.9 [P2] ST 涨跌停幅度检测失效

`_load_security_names` 恒返回 `{}`（`workflow_service.py:289-291`，注释自认"V2 无 security 主数据表"）→ `can_sell/can_buy` 收到 `name=None`（`:1003-1004,1034-1035`）→ `CNProfile.price_limit_pct` 中 `"ST" in (name or "").upper()` 恒 False（`cn.py:44-46`）→ **ST 股按 10% 判板**，实际应为 5%。跌停封死的 ST 股在 -6% 处仍判"可卖"，模拟盘照常止损。另注意 `is_st_like(None)` 返回 True（`cn.py:74-77`）与 `price_limit_pct` 的行为方向相反——无名标的被判 ST 剔除（选股侧）却在涨跌停侧按 10% 处理，自相矛盾。

### 1.10 [P2] 涨停封死买入拦截的绕过点

`can_buy`（涨停封死/停牌拦截）只在入场循环被调用（`workflow_service.py:1162-1165`）。以下两条买入路径**无 can_buy**：
- `_signal_entry`（spec 信号，`workflow_service.py:876-924`）→ `_auto_execute` 直接撮合，涨停一字板也能"买入"；
- CRYPTO 盘前即时执行（`workflow_service.py:664-665`，CRYPTO 无涨跌停，影响有限）。

### 1.11 [P3] T+1 移动止损注释与实现不一致

`run_intraday` docstring/注释声称"移动止损价仍可上移"（`workflow_service.py:931,979`），但 T+1 分支 `continue` 位于移动止损逻辑**之前**（`:980-985`）→ 当日开仓的持仓移动止损实际**完全跳过**。要么改注释要么改代码。

### 1.12 仓位上限（max_positions / 单票占比）真实生效性核查（找调用点结论）

| 约束 | 是否真实生效 | 证据 |
|---|---|---|
| C0 持仓数（distinct） | ✅ 生效（含盘中复检） | 盘前 `size_new_position`（`workflow_service.py:580-584`）→ `compute_position_size` C0（`position_sizing.py:73-75`）；盘中按 sv 运行时快照复检 + 建仓后并入集合（`workflow_service.py:1123-1151`）——已修"盘前只查一次顶穿"问题 |
| C1 预算制 | ✅ 生效 | `position_sizing.py:84-87` |
| C1t 总天花板 | ✅ 生效（但数值硬编码，见 §5） | `position_sizing.py:89-91`；`position_sizing_service.py:103-106` 用 `profile.max_total_exposure_pct()`（CRYPTO 0.50 / 其他 0.80） |
| **C2 风险预算** | ❌ **恒不生效（死约束）** | `qty_by_risk` 仅当 `stop_loss_price` 传入才启用（`position_sizing.py:100-104`）；两个调用点 `workflow_service.py:580-584,886` **均不传 stop_loss_price** → 恒 inf |
| **单票占比规则** | ❌ **恒不生效（死规则）** | `risk_service.py:40-42` 判 `plan.max_position_ratio > 0.10`；但 `TradePlanService.create_from_candidate` 不携带 `risk.max_position_ratio`（`trade_plan_service.py:27-37`），plan 恒为默认 0.05 → 永不 BLOCK |
| **流动性规则** | ❌ **恒不生效（死规则）** | `check_plan` 的 `turnover` 参数两个调用点都不传（`workflow_service.py:620-623,898`）→ `LIQUIDITY_INSUFFICIENT` 永不触发 |
| 单标的上限（C1b） | ✅ 生效 | `position_sizing.py:93-96`（slot×2 封顶） |

### 1.13 [P2] ref_price 兜底 100 硬编码

盘前无行情价时 `ref_price = Decimal("100")`（`workflow_service.py:575`）→ 计划止盈/止损、仓位数量全按假价 100 计算：50 元股票只买一半预算、300 元股票超买 3 倍；盘中实际成交价是真实价（`_auto_execute` live_price）→ 数量与预算错配。缺行情应跳过该候选而非用魔数兜底。

---

## 2. 并发与幂等

### 2.1 [P1] 跨进程无锁：API 手动触发 vs scheduler（未修且危险）

- `api/routers/workflow.py:15-28` 暴露 `POST /workflow/pre-market|intraday|post-market` 直接调 `WorkflowService.run_*`；
- scheduler 独立进程（`scheduler.py`）同时跑 `run_pre_market_job`（09:25，`:209-215`）、`run_intraday_job`（每 5 分钟，`:228-239`）、`run_spec_signals_job`（每 5 分钟，`:370-377`）；
- **没有任何分布式锁 / 数据库锁 / 幂等占位**。两个进程同时跑 `run_intraday`：双方都先 `list_open_positions` 读到同一 OPEN 持仓（TOCTOU）→ 双方都触发 `_close_position` → 双平仓（fill_id 幂等挡住 fill 表，但 position 行被写两次、replay 建两次、pnl 落两次；若两次现价不同则 close_price/pnl 取后写者）。
- 盘前同理：两个进程并发 `run_pre_market` 都通过 `held` 检查（都未建仓）→ 同一 plan/approval 双写（id 确定性导致 upsert 幂等，但 CRYPTO 的 `_auto_execute` 即时执行会双撮合，position 覆盖）。

### 2.2 [P1] scheduler 进程内 catch-up 与定时 job 竞态

`_catch_up_on_start`（`scheduler.py:74-111`）启动 +30s 及 09:00–15:00 每 15 分钟巡检，判据"当日无候选池记录"（`95-106`）是**check-then-act**：与 09:25 的 `run_pre_market_job` 是**不同 job**，`max_instances=1` 只约束同 job 自身 → 两 job 可并发执行同一市场盘前。CRYPTO 盘前 `pre_market_startup_crypto` 与每 4h job 同构。

### 2.3 [P2] upsert 无状态守卫 → 计划/授权状态被复活（已修 id 唯一化、残留状态守卫缺失）

`save_trade_plan` UPDATE 分支**无条件** `plan_status = str(plan.status.value)`（`sqlalchemy_db.py:296-315`），`save_approval` UPDATE 分支无条件 `row.status = request.status.value`（`:388-417`）。同日盘前重跑时新域对象 status=PENDING → 把上一轮 **CANCELLED/REJECTED 的计划与授权复活**：
- 若冲突组结构变化（另一策略不再选该标的），被裁决 CANCELLED 的败者计划+授权被重新激活 → 盘中入场循环会执行一个本应作废的计划；
- 用户人工 REJECTED 的授权被重跑覆盖回 PENDING/APPROVED（若 sim_auto 则直接自动批准）→ **人工决定被静默推翻**。

**建议方向（未改代码）**：UPDATE 分支只允许 PENDING→非 PENDING 单向流转或加 WHERE status='PENDING' 条件；授权状态更新同理。

### 2.4 [P2] 双关闭竞态（run_intraday TOCTOU）

见 2.1。`_close_position` 自身不检查 `position.status`（`workflow_service.py:1368-1376`），依赖调用方守卫；两个并发调用方（intraday + spec_signals 每 5 分钟同进程交错）可对同一持仓双平。

### 2.5 幂等机制盘点（现状）

| 机制 | 现状 | 评价 |
|---|---|---|
| candidate_id/plan_id/request_id 确定性 id | ✅ 唯一约束兜底 upsert | 幂等写入，但导致 2.3 状态复活 |
| fill_id/execution_id/request_id/intent_id `on_conflict_do_nothing` | ✅ `workflow_service.py:1469-1518` | 只幂等"记录"，不幂等"持仓" |
| `OrderRequestModel.idempotency_key` | ⚠️ 有列（`models.py:345`）无查重逻辑 | 死字段 |
| SELECT-FOR-UPDATE / 乐观锁 | ❌ 全库无 | 平仓/入场均无行锁 |
| approval 终态 | ❌ 无 EXECUTED | P0 根因之一 |

---

## 3. 数据一致性（memory vs sql 双后端）

### 3.1 [P2] get_session() 绕过注入端口清单（架构违规，核心文件内）

业务服务本应只依赖 `PersistencePort`，以下位置**直接 `get_session()` 直连生产 PG**（其中 `_record_risk`/`_run_entry_loop`/`_record_fill`/`_persist_portfolio_snapshots` 有 `persistence_backend != "sql"` 护栏，其余**无护栏**）：

| 文件:行 | 用途 | 有无后端护栏 |
|---|---|---|
| `workflow_service.py:74` | `_sv_for_type` | ❌ |
| `workflow_service.py:111` | `_strategy_type_status` | ❌ |
| `workflow_service.py:134` | `_set_plan_confidence` | ❌ |
| `workflow_service.py:165` | `_record_risk` | ✅ |
| `workflow_service.py:203` | `_load_position_stops` | ❌ |
| `workflow_service.py:237` | `_load_plan_exits` | ❌ |
| `workflow_service.py:320` | `_closed_today_symbols`（盘前防重开护栏！） | ❌ |
| `workflow_service.py:344` | `_save_position_stop` | ❌ |
| `workflow_service.py:367` | `_persist_position_pnl` | ❌ |
| `workflow_service.py:415` | `_persist_candidate_pool` | ❌ |
| `workflow_service.py:1096` | `_run_entry_loop` | ✅ |
| `workflow_service.py:1196/1227/1256/1294/1351` | 快照/分钟线/fill 价/开盘价/入场计数 | ❌ |
| `workflow_service.py:1467` | `_record_fill` | ✅ |
| `workflow_service.py:1594` | `_persist_portfolio_snapshots` | ✅ |
| `position_sizing_service.py:42` | `_realized_pnl`（仓位约束的净值基数！） | ❌ |
| `account_stats_service.py:62` | `_strategy_names` | ❌ |
| `conflict_resolver_service.py:36` | 冲突裁决 | ❌ |
| `pipeline_service.py:29` | 决策流聚合 | ❌ |
| `scheduler.py:93` / `scheduler_jobs.py:74,737` | catch-up / 策略枚举 / 扩展数据 | ❌ |
| `alert_service.py:67` | send_alert 落库（docstring 称"统一走端口"，实际仍直连） | ⚠️ |

### 3.2 [P2] memory 后端隔离形同虚设（同一业务操作双后端行为差异）

- `persistence_backend=memory` 时，`_realized_pnl`（`position_sizing_service.py:36-53`）**仍直读生产 PG** → 内存后端跑盘前/仓位计算会混入生产库的已实现盈亏（同类问题项目已在 `_run_entry_loop`/`_record_risk`/`_record_fill` 修过——`workflow_service.py:1090-1092,151,1421-1423`——**残留同构问题**）。
- `list_candidates_by_strategy`：memory 按 `_by_sv` 索引（`memory.py:81-82`），SQL 需要 join candidate_pool 反查 sv（`sqlalchemy_db.py:265-288`）→ **单独 `save_candidate`（pool_id=""）在 SQL 后端查不到、memory 后端查得到**，行为不一致。
- `save_candidate_pool`：memory 写 pool.candidates（`memory.py:72-75`），SQL 的 `pool_type` 硬编码 "STRATEGY"（`sqlalchemy_db.py:242`）与 workflow 落库的 "PREMARKET"（`workflow_service.py:429`）口径不同。
- 其余核心操作（save_position/save_approval/save_trade_plan/expire_stale_pending_plans）两后端语义对齐（均 upsert）。

### 3.3 [P3] `SqlAlchemyDatabase(session=...)` 注入会话的 with 关闭语义

`self._session_factory = lambda: session if session is not None else get_session()`（`sqlalchemy_db.py:57`）+ 每个方法 `with self._session_factory() as db:` → **注入的会话在第一个方法调用结束即被 close()**；后续方法复用它（Session.close() 后可复用，行为隐蔽）。当前仅测试代码注入（`tests/test_sqlalchemy_db.py:20` 等），生产未注入 → 潜在风险，建议显式区分"外部会话不关闭"。

---

## 4. 会话管理

### 4.1 [P2] 异常路径 session 不关闭（泄漏）

`api/routers/positions.py` 是盘中每 5 分钟热路径（`run_intraday`/`_get_latest_prices`/`_get_pre_closes`），其中 **7+ 处 `session.close()` 写在 try 块内**，except 路径直接 return、session 泄漏（连接要等 GC 才归还，`pool_pre_ping` 下长时间异常可耗尽连接池）：
`positions.py:30-55`（`_get_daily_close_prices`）、`:66-82`、`:100-109`、`:117-159`、`:172-198`、`:204-220`（`_get_realized_pnl_by_strategy`）、`account_stats_service.py:62-79`。

### 4.2 [P2] 多语句事务原子性缺口

- `_close_position`：save_position（CLOSED）→ 裸 UPDATE pnl → `_record_fill`（fill+intent+request+execution 四表）——**三个独立事务**，任意一步失败产生中间态（CLOSED 无 pnl / 有 pnl 无 fill）。`conflict_resolver` 的计划 CANCELLED + 授权 CANCELLED 在一个事务内（`:95-96` 一次 commit）——这块是对的。
- `run_pre_market` 的 计划→风控→授权→(CRYPTO)成交 全链路无事务边界（每步独立 commit），进程崩溃会留下"有计划无授权/有授权无持仓"的半成品（幂等 id 使其可重跑修复，但中间态对读侧可见）。

### 4.3 [P3] 正例（已修好的部分）

`workflow_service.py` 的 18 个 session 全部 try/finally 关闭（如 `:74-94`、`:415-467`、`:1088-1184`）；`conflict_resolver`（`:36-105`）、`risk_service`（`:68-77`）、`pipeline_service`（`:29-33`）均正确 finally 关闭——**服务层纪律好，问题集中在 API 层与无护栏助手**。

---

## 5. 硬编码 / 魔数清单（脱离 settings 的）

| 文件:行 | 值 | 说明 |
|---|---|---|
| `workflow_service.py:695` | `0.5` | `_OUTLIER_PRICE_DEVIATION` 离群价阈值，类常量 |
| `workflow_service.py:1186` / `position_ops_service.py:17` | `2` | `MAX_ENTRIES_PER_PLAN` **两份拷贝**（entry loop 用前者、add_position 用后者，互相不知道对方） |
| `position_ops_service.py:19-21` | `0.10/0.20/0.5` | T1/T2 分批止盈阈值（死代码携带） |
| `position_sizing_service.py:25-26` | `10 / 0.80` | `DEFAULT_MAX_POSITIONS`/`DEFAULT_MAX_TOTAL_EXPOSURE_PCT`——**settings.sim_max_positions/sim_max_total_exposure_pct（settings.py:45-46）全库无读取**，改 settings 无效 |
| `position_sizing_service.py:102` | `0.10 if market=="CRYPTO" else 0.05` | 按市场名字符串判断，绕过 `profile.max_position_ratio()`（crypto.py:92-93） |
| `position_sizing.py:28-30` | `0.05 / 0.02 / 2.0` | 模块级常量（risk budget 等） |
| `market_rules.py:16` | `30` 分钟 | STALE_MINUTES（`alert_data_stale` 文案同值 `alert_service.py:109`） |
| `risk_service.py:83,87` | `120 / 30` 分钟 | 快照新鲜度阈值 |
| `workflow_service.py:559` | `[:3]` | 每策略 top-3 建计划（与 scheduler max_candidates=20 并存） |
| `workflow_service.py:575` | `Decimal("100")` | 缺行情价兜底（见 1.13） |
| `workflow_service.py:823,849` | `{"1m",...}` / `max(windows)*3+10` | 周期分类 / 历史窗口 |
| `conflict_resolver_service.py:23` | PROD=0/STAGING=1/... | 策略等级序 |
| `workflow_service.py:25-39` | `_EXIT_KIND_TEXT`/`_SV_BY_STYPE` | 文案与 sv 映射硬编码（有文档化护栏 `:49-53`） |
| `settings.py:39-42` | 各市场初始资金 | 属 settings，合规；但 `account_stats_service.py:18-25` 用 dict 映射，增市场需改两处 |

---

## 6. 死代码清单（grep 全库验证，0 调用点）

| 符号 | 位置 | 备注 |
|---|---|---|
| `PositionOpsService.add_position` | `position_ops_service.py:35-72` | 正确"加仓建独立 lot"实现，却无人调用（1.1 的应有实现） |
| `PositionOpsService.tiered_take_profit` | `position_ops_service.py:145-196` | 分批止盈整段死 |
| `entry_signal.evaluate_take_profit` | `entry_signal.py:66-72` | 止盈评估无人调用（workflow 内联实现） |
| `entry_signal.t_plus_one_locked` | `entry_signal.py:75-90` | T+1 判定无人调用（workflow 用 `_is_today` 内联） |
| `OrderResult.fee` 全链路 | `simulated.py:189-203` | 算完没人用（1.8） |
| `settings.sim_max_positions / sim_max_total_exposure_pct / trade_approval_expire_seconds` | `settings.py:36,45-46` | 定义无人读 |
| `RiskService.check_plan` 的 `turnover`/`POSITION_RATIO_TOO_HIGH` | `risk_service.py:36-42` | 调用点不传参 → 规则永不触发 |
| `compute_position_size` 的 `stop_loss_price`（C2） | `position_sizing.py:100-104` | 调用点不传 → 恒 inf |
| `TradeExecutionPipeline` | `trade_execution_pipeline.py:30-149` | 仅 `POST /trade/simulate` 演示端点用（`trade.py:40-74`），且第 8 步**强制以 +2% 假价平仓**造 replay（`:130-137`），会向生产库写入伪造复盘数据 |

---

## 7. 未修且危险 / 已修但残留同构问题（汇总）

### 未修且危险（建议优先处理）
1. **P0** 执行无幂等键 + 授权无终态 + 同 position_id 覆盖（§1.1/1.2/1.4）
2. **P0** 入场循环"加仓"= 全量重买 + 覆盖持仓（§1.1/1.5）
3. **P1** spec 信号平仓绕 T+1/可卖性（§1.3）
4. **P1** 跨进程盘前/盘中并发无锁（§2.1/2.2）

### 已修但残留同构问题
| 已修（代码注释可见的修复） | 残留同构 |
|---|---|
| fill_id/request_id 超 64 字符 md5 压尾（`workflow_service.py:1442-1449`） | `replay_service.py:25-28` 复制了同算法、`_persist_portfolio_snapshots.py:1599-1601` 第三份拷贝——同一问题 3 处手写实现，未收敛为公共工具 |
| fill 记录幂等（on_conflict_do_nothing） | **position 行仍非幂等**（覆盖非累加）——幂等只修了一半 |
| 计划/授权 id 唯一化消除双形态（`workflow_service.py:591-594` 等注释） | upsert 无状态守卫 → 状态复活（§2.3）——幂等修复引入新语义缺陷 |
| memory 后端隔离（`_run_entry_loop`/`_record_risk`/`_record_fill` 有护栏） | `_realized_pnl`/`_closed_today_symbols`/`_load_plan_exits` 等 12+ 处仍无护栏直读生产库（§3.1/3.2） |
| workflow 路径补写 trade_plan_id（`workflow_service.py:782-784`） | `approval_execution_service.py:40-46`（手动批准路径）仍未传 trade_plan_id → 恒 NULL |
| `_load_plan_exits` 已防跨策略串价（sv 键 + 活跃态过滤，`:221-286`） | 同日止盈价仍基于盘前 ref_price 而非实际成交价（见 §1 末注）→ 入场价高于 ref 时止盈提前触发 |

---

## 8. 修复优先级建议（仅供参考，未改代码）

1. **执行终态 + 幂等**：approval 增加 EXECUTED/FILLED 终态；执行入口做"该 approval 当日已成交数+已成交 qty"的 DB 级原子校验（SELECT FOR UPDATE 或 `UPDATE ... WHERE status='APPROVED'` 条件更新）；二次执行只下**剩余/加仓部分**并**新建独立 lot**（复用 `PositionOpsService.add_position`）。
2. **统一平仓守卫**：`_close_position` 内部收敛 T+1 + can_sell 检查（或强制调用方通过统一出口）。
3. **跨进程锁**：盘前/盘中入口加 DB 级互斥（如 pg advisory lock 或 v2_app_state 锁行 + 心跳过期）。
4. **状态守卫**：save_trade_plan/save_approval 的 UPDATE 分支加 status 前置条件。
5. **配置接线**：`sim_max_positions/sim_max_total_exposure_pct/trade_approval_expire_seconds` 接进 position_sizing/expiry；C2 风险预算传 stop_loss_price。
6. **费用落账**：`OrderResult.fee` 进持仓成本与 realized pnl。
