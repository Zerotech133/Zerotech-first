# zhice-OS V2 全面测试与评审报告

**日期**：2026-08-20
**范围**：前后端全功能测试 + 逻辑合理性评审
**方法**：后端 117 端点扫测 + 逐表落库核对 + Playwright 前端 E2E + 3 路代码评审子代理 + DB 实证
**标准**：接口 200 ≠ 落库正确（逐表核对）；只报能从代码/数据确证的问题

---

## 一、测试执行总结（通过项）

| 项 | 结果 |
|---|---|
| 后端服务 | uvicorn `127.0.0.1:8102`；前端 vite `5174`；PostgreSQL 数据充足（250 万日K / 893 单/成交 / 540 持仓 / 1268 计划 / 1732 决策复盘） |
| 后端 GET 扫测 | 77 个 GET 端点**全部健康**（auth 401 为缺 token 的预期；model-ops active 404 因 models 表空） |
| 鉴权链路 | login→token→/auth/me、/auth/users 正常（**但 login 无密码校验**，见 SEC-1） |
| 写入落库 | `settings` PUT→GET→`v2_app_state` 三方一致 ✓（非 no-op）；alert 创建→ack→前端可见 ✓（**但仅存内存**，见 P1-7） |
| 前端渲染 | 8 组 × 全部 tab 均可加载，无 JS 运行时错误（仅 antd `valueStyle` 弃用警告） |
| 数据自洽 | 持仓页 已实现(-351,469)+浮动(-12,555)=总盈亏(-364,024) ✓ 内部一致 |

前端 8 组结构：总览 / 策略管理 / 候选池 / 计划与信号 / 风控与执行 / 交易 / 复盘研究 / 系统与设置；4 市场 cn/us/hk/crypto。

---

## 二、P0 — 会算错钱 / 产生错误数据

### P0-1　跨市场跨币种直接相加（多处）
- **cross_market.py:11** 汇率 `FX_TO_CNY={"USD":7.2,"HKD":0.92,"USDT":7.2}` 后端硬编码、无实时源；USDT 直接复用 7.2。
- **portfolio_service.py:16-17** `/portfolio/summary` 把四市场 `open_price*qty` 与 replay pnl 无折算直接求和。
- **report_service.py:23** 日报 `replay_report(market=None)` 把 USD+CNY+HKD+USDT 的 pnl 直接相加，**调度器每日以 market=None 调用**（scheduler_jobs.py:83）→ 每日复盘总盈亏是错的。
- **为什么错**：crypto profile 自己都注明"禁跨币种求和"；真实汇率漂移后合并权益系统性失真。
- **整改**：接入实时汇率表（带 as_of），按 market 分组折算后再合计；无源时标 stale 不静默用 7.2；日报默认出分市场维度。

### P0-2　平仓不写已实现盈亏（已实证）
- **positions.py:311-325** `close_all_positions` / **execution_service.py** 旁路平仓不写 `pnl_amount`、不落 fill。
- **实证**：`v2_position` 中 **CLOSED 429 笔有 15 笔 `pnl_amount` 为 NULL**；这些笔的已实现盈亏被静默计 0，且无 fill/退出原因。
- **根因**：`PositionLot` 领域对象无 pnl 字段，`save_position`（sqlalchemy_db.py:289-314）从不写 `pnl_amount`，realized 全靠 `workflow_service._persist_position_pnl` 一条旁路；其它平仓入口都漏写。
- **整改**：所有平仓入口统一走 `_persist_position_pnl` + `_record_fill`（带 realized_pnl/exit_reason）。

### P0-3　卖出授权凭空新建多头仓（潜伏，long-only 未爆）
- **execution_service.py:22** `apply_fill` 把成交无条件写成 `direction="BUY" / OPEN`；`execute_approved` 的 SELL 授权同样进此路径 → 卖出不减仓反而新增一笔多头。
- **实证佐证**：`v2_fill_record` **无方向列**，当前全部持仓方向都是 BUY；主平仓走 workflow 路径（367/893 fill 带 realized），故此 bug 是**审批-执行旁路上的潜伏雷**，一旦做空/用该路径平仓即算错。
- **整改**：`apply_fill` 接 `side`，SELL 走减仓/平仓（复用 `PositionOpsService.partial_close`）。

### P0-4　regime 市场状态恒用上证指数
- **regime_service.py:8-9** `get_regime(market, benchmark="000001.SH")` 对**所有市场**都用 000001.SH 判 BULL/BEAR/SIDEWAYS，market 只回填不参与计算。
- **为什么错**：US/HK/CRYPTO 的择时状态实际是 A 股的状态，误导跨市场策略开关。
- **整改**：按 market 选基准（US=SPX/QQQ、HK=HSI、CRYPTO=BTC），缺数据返回 UNKNOWN。

### P0-5　盈亏日历基准用上一日 close 而非 pre_close
- **pnl_calendar_service.py:66-77,240-255** 核心逐日盯市方向正确，但基准取 `closes.get((sym,prev_day))`（上一交易日原始 close），全程未取 `pre_close` 列。
- **为什么错**：全系统其它口径（positions `_get_pre_closes`、account_stats today_pnl）都用 `pre_close`（已还原除权除息），唯独日历不用 → 除权除息日把分红/送转跳空错算成盈亏，且同一持仓在"账户今日盈亏"和"日历当日格子"对不上。
- **前端佐证**：日历页右上角标注"**按平仓日聚合**"——与"逐日盯市"口径自相矛盾（标签误导）。
- **整改**：批次同时取 pre_close，基准优先用当日行 pre_close，对齐 positions 两级口径；修正 UI 标签。

---

## 三、P1 — 逻辑漏洞

| # | 位置 | 问题 / 为什么 / 整改 |
|---|---|---|
| P1-1 | strategy_performance_service.py:27,31 | **绩效榜按字符串排序（已实证排名错乱）**：`total_pnl` 存成 str 后字典序排序，`"-5411">"-185802"`，亏损榜顺序完全不可信。实测返回 +52886→-81547→-797→…→-185802→-165796。整改：按 float 排序。 |
| P1-2 | position_sizing.py / position_ops_service.py | **正确的 sizing 与部分平仓逻辑是死代码**：全仓无调用点；实盘用 workflow_service.py:404-412 的裸逻辑。审查对象≠运行对象。整改：接入或删除。 |
| P1-3 | workflow_service.py:408 | CN sizing `qty=max(lots,1)*100`：5% 预算买不起一手的高价股被强抬成 1 手，**超预算超风险**；且 budget 硬编码 100 万脱离 settings。整改：不足一手放弃；本位取账户口径。 |
| P1-4 | risk_service.py:29-41 | **风控 WARN 永不产出**：任何 reason→BLOCK，"仅告警"的高价被硬拒；与 risk.py:20 的 PASS_WITH_WARNING 映射矛盾。整改：告警类原因进单独 warnings 列表。 |
| P1-5 | circuit_breaker.py | **熔断权限矩阵装饰性无效**：workflow 自动执行/审批/orchestrator 均不查熔断状态，触发急停后 AUTO 动作照跑。整改：执行前查 `_effective_permission`。 |
| P1-6 | execution_service.py:16 | `PARTIALLY_FILLED` 被丢弃：SimulatedBroker 大单返回部分成交（simulated.py:200），这些股数不落持仓 → 成交丢失。整改：部分成交按 filled_qty 建仓。 |
| P1-7 | trade_execution_pipeline.py:90,103 | `strategy_version_id=1` 硬编码：审批与执行用死值 1，apply_fill 用真实 id → 开关查错策略、approval 与 position 归属对不上。整改：三处统一真实 id。 |
| P1-8 | market.py:107 + 64-98 | **US/CRYPTO 市场概览恒空**：`market in r["ts_code"]` 子串匹配（"US" in "AAPL"=False）+ `and/or` 优先级 bug；`_db_overview` 查 bar 不带 market 过滤、固定只取前 200 标的。整改：用 market 字段过滤，勿子串启发式、勿截断。 |
| P1-9 | reports.py:30,263,349 | 晨/晚报按硬编码 Asia/Shanghai 切当日，US/HK 的 created_at/opened_at 被 CST 边界切错日 → 今日计划数/新建仓错配。整改：按 `get_profile(market).tz`。 |
| P1-10 | account_stats_service.py:156-175 + cross_market.py:28 | 空市场（HK/CRYPTO 无账户）返回 1×initial 作权益并折算进 total_cny → 凭空造权益；活跃市场按 N×initial → 口径不一致不可比。整改：无数据市场归零/标 no_data。 |
| P1-11 | pnl_calendar_service.py:169-184 | 合并视图初始资金 = 单账户 × "当前有持仓的 distinct 策略数"，分母随"今天哪些策略恰好有仓"漂移，pnl_pct 跨月不可比。整改：按固定策略账户数取。 |
| P1-12 | governance_service.py:13-21 | promote 不下线旧 ACTIVE → 同策略多个 ACTIVE 版本共存（对比 model_ops 晋升会 ARCHIVED 旧版）。整改：晋升时原子降级旧 ACTIVE。 |

---

## 四、P2 — 健壮性 / 一致性

- **P2-1 裸 except 吞异常返回空**（market.py 多处、positions.py:54/120/155、candidates.py、workflow_service pnl 写回 `except:pass`）：DB 故障被伪装成"无数据"，与 hk/crypto/概念表真空无法区分，故障静默；也是 P0-2 realized 漏写的帮凶。整改：区分"查询成功但空"与"异常"，异常记日志/返回 5xx。
- **P2-2 已实现盈亏毛/净不自洽**：workflow_service `(close-open)*qty` 毛额 vs pnl_calendar 扣佣金+印花税净额 → 持仓 realized 与日历 realized 是两个数。整改：统一扣费口径。
- **P2-3 现价缺失兜底开仓价**（positions.py:262、strategy_detail.py:68）：停牌/无行情持仓显示"浮盈 0 / 现价=开仓价"，用户无法区分"没涨跌"与"拿不到价"。整改：返回 null/标 stale。
- **P2-4 float_pnl 不分方向**（positions.py:265）：`(current-open)*qty` 恒正向，空头符号相反（long-only 侥幸对）。整改：按 direction 取号。
- **P2-5 调度全局任务无时区/无交易日限定**（scheduler.py:131-133）：daily_review/governance/decay 的 CronTrigger 无 timezone，UTC 部署会漂到次日凌晨且周末照跑。整改：显式 `timezone="Asia/Shanghai"` + `day_of_week="mon-fri"`。
- **P2-6 run_pre_market 用 date.today()**（scheduler_jobs.py:39-48）而非 `get_profile(market).trading_date()` → 候选池 pool_date 落错日。
- **P2-7 红线阈值硬编码且多副本**（accounts_ext.py:29、governance_ext.py:30-32：日亏 2%、单票 20%、组合 80%）→ 一处改一处忘则判定不一致。整改：集中到配置单一真源。
- **P2-8 组合仓位红线用被封顶 100% 的 position_ratio**（governance_ext.py:295）：真实 130% 仓位只显示 100%，超配幅度被掩盖。整改：用未封顶原始比率判定。
- **P2-9 candidates 异常路径 session 未关闭**（candidates.py:44-67,93-142）→ 连接泄漏。整改：try/finally close（对齐 plans.py 写法）。

---

## 五、P3 — 代码质量

- **P3-1 数值字段 str() 序列化**（report_service / replay / strategy_performance）：除引发 P1-1 排序 bug 外，浮点尾数原样透前端。整改：返回数值。
- **P3-2 盈/亏/平分类跨端点不一致**：report_service `pnl<=0` 记 loss vs reports.py `pnl>0/<0`（平局不算）vs strategy_detail `losses=total-wins`。整改：统一 breakeven 规则。
- **P3-3 equity_prev<0 时百分比符号翻转**（pnl_calendar_service.py:108,132）：只挡 0 未挡负。整改：`if equity_prev>0`。
- **P3-4 decision_reviews MISSED 兜底 FAIR**（decision_reviews.py:44-61）与 docstring"MISSED 无 FAIR"矛盾。
- **P3-5 model-ops active 非确定**（model_ops.py:126）：多 active 时返回依赖 dict 顺序；注册表在内存重启丢失。
- **P3-6 硬编码常量**：STALE_MINUTES=30、SINGLE_POSITION_CEILING_MULT、CN min_commission=0（A股 5 元最低佣金未建模）、demo `close_price=ref_price*1.02` 伪造 2% 盈利。

---

## 六、前端专项

- **F-1 写死 market=CN（已实证）**：MarketOverviewPage、CalendarPage、WorkspaceLayout:53、HomeBigscreen:111 均 `?market=CN`。**实证**：切"美股/盘前"后市场概览涨幅榜显示的是 `920087.BJ 秋乐种业` 等 A 股北交所股票。
- **F-2 待授权/候选计数口径打架**：工作台顶部"待授权 41 / 候选 33" vs 闭环漏斗"待授权 17 / 候选 580"，同屏矛盾；底栏 APPROVAL 41 在 CN/US 都是 41（候选池 33 vs 10 却随市场变）→ 授权计数未按市场过滤。
- **F-3 流水线阶段与操作不匹配**：策略名显示为"**0**"（归属缺失兜底）；"当前阶段=复盘"的已复盘项仍挂"通过/拒绝"按钮；漏斗候选 580 vs tab 全部 581 差 1。
- **F-4 盈亏日历**：标签"按平仓日聚合"与逐日盯市口径矛盾（见 P0-5）；格子"44笔·胜1/亏0"数字对不上；正数配色不一致（+4196 红 / +236 绿）。
- **F-5 HomeBigscreen 绕过统一 API 封装**：自实现 get() 无 !ok 检查、失败静默 `.catch(()=>{})`，大屏端点异常无任何反馈。
- **F-6 硬编码兜底汇率 LOCAL_FX**（HomeBigscreen:70 `{USD:7.2}`）：与后端 P0-1 同病。
- **F-7 死代码/空壳**：BigscreenPage.tsx 全项目无 import（实际用 HomeBigscreen）；ExportsPage 无任何后端调用（需确认是否空壳）。

---

## 七、安全

- **SEC-1 login 无密码校验**（auth.py:19）：`authenticate(username)` 只凭用户名发 token。整改：加凭据校验。
- **SEC-2 DB 口令明文硬编码**（scheduler_jobs.py:19-24,121-123，如 `password="lmkj929292"`）：入版本控制、泄露风险。整改：迁环境变量/密钥管理。

---

## 八、建议整改优先级（路线图）

1. **立即（会算错钱）**：P0-1 跨币种混算、P0-2 平仓漏写 realized（已有 15 笔坏数据）、P0-5 盈亏日历 pre_close、P1-1 绩效榜排序。
2. **本周（交易正确性）**：P0-3 apply_fill 方向、P1-2 死代码 sizing 接入、P1-4 风控 WARN、P1-5 熔断生效、P1-6 部分成交、P0-4 regime 分市场。
3. **两周（跨市场一致性）**：P1-8/9/10/11 市场过滤与时区、P2-1 异常吞没、P2-2 费用口径、P2-5/6 调度时区、F-1 前端写死 CN。
4. **持续（质量/安全）**：SEC-1/2、P2-7 配置化红线、P1-12 promote 降级、P3 系列、F-2~F-7 前端一致性。

---

## 附：本次测试遗留
- 内存中残留一条测试告警 `AUDIT_TEST_ALERT`（WARN/ACKED），重启后端即消失，无需处理。
- 破坏性写端点（positions/close 全平、circuit-breaker/trigger、governance/demote）未在生产数据上实跑，改为代码评审——如需实跑请在隔离数据集确认。
