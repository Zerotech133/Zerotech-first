# RiskGuard L4 解耦设计（影子先行 · 会合 10-20 满窗裁决）

> 2026-10-17 · 量化投研板块 · 依据今日实证修订（原方案假设已按代码现状校正）

## 一、现状盘点（今日实探）

| 组件 | 位置 | 状态 |
|---|---|---|
| RiskGuard 四规则 | `C:\aios\zerobound-kg\kg_riskguard.py`（R1 市场质量 / R2 流动性 / R3 事件风险 / R4 仓位集中度） | ✅ v1 完整，shadow 默认、enforce 三重门宁严 |
| 影子账本 | `riskguard_ledger/ledger.jsonl`（32078 行 append-only） | ⚠️ 10-15 迁移断流 → **今日已修复补链** |
| 调度链 | RiskGuardShadowDaily 19:10（R1/R2）+ RiskGuardR3Weekly 周六 06:40（R3） | ✅ 今日重建（wrapper 中文路径换 ASCII junction `C:\aios\zerobound-kg`，判例同 KG-Neo4jWeeklyDump） |
| 校准工具 | `kg_riskguard_calib.py`（历史重放 + Welch t + OOS 对账） | ✅ 铁律：只出数与建议，不改 params.yaml，拍板走老田 |
| 反馈回流 | K-STOCK-401：evaluate() 逐规则落 `portal/data/risk_feedback.jsonl` | ✅ 协作协议 v0 已通 |
| 多策略 L4 | `quant_backtest/strategy_layer`（SignalAggregator + signals_combined） | ⏳ RiskGuard 共享位=预留插槽 |
| 误杀焦点 | critical 拦截 213 样本后验 +0.65% vs 放行 -0.25% | 10-20 满窗裁决待拍板 |

## 二、解耦原则（四原则映射）

- **松耦合**：跨仓（zerobound-kg ↔ quant_backtest）不搞 import 耦合、不上 HTTP 服务——**复用已验证的文件账本协议**（K-STOCK-401 同款）。
- **可下钻**：账本行自带 rule_id / reason / score，前端可逐规则下钻。
- **影子先行**：对多策略信号只记不拦（与 GUARD_KEYS 宁严语义一致），10-20 裁决拍板后才讨论 enforce 升级。
- **可调配**：阈值全部在 params.yaml `riskguard_*`，校准口径独立演进。

## 三、集成方案（三步走）

### 第 1 步 · 数据桥（本次落地）
strategy_layer 新增 `risk_bridge.py`：
- 读 `riskguard_ledger/ledger.jsonl` 最近 R1 市场级行 → 提炼**市场质量水位**（pass/block 比例）
- 读当日 z4 快照 CSV（`docs/planning/z4-fourstate-snapshot-*.csv`）→ **衰竭标的清单**
- 输出 `RiskContext` 只读对象，供 dashboard_api 新端点 `GET /api/risk` 消费
- **不改 signals_combined 表结构**（零侵入，熔断点=文件不存在时返回空上下文）

### 第 2 步 · 前端呈现（本次落地）
`/manage` 与多策略页签加「风控水位」卡：市场质量 pass/block、R3 最新周批、账本连续性（今日修复后从 10-17 起恢复）。RiskGuard 插槽卡从「预留」升级「已接（影子）」。

### 第 3 步 · 评审挂钩（10-20 裁决后）
裁决拍板新阈值 → 若数据支持，风险评审挂到 SignalAggregator 下游（run_daily 融合后对 top 信号调 RiskGuard 单标的 eval，结果进 rationale 附注）——依然影子语义，绝不静默改信号。

## 四、与 10-20 满窗裁决的数据关系

| 数据 | 用途 | 现状 |
|---|---|---|
| 账本历史样本（≤10-14） | calib 历史重放基准 | ✅ 32078 行在 |
| 10-15/16 缺口 | — | ❌ 不可回补（四态快照是时点查询），如实声明 |
| 今晚 19:10 起每日批 | OOS 前向样本恢复连续 | ✅ 调度已重建 |
| R3 周批 | 事件风险样本（今日 06:40 补跑） | ⏳ 运行中 |
| 10-20 当晨 morning_check | 一键出数供裁决 | ✅ 工具在（只读） |

## 五、风险与边界

1. **账本单写者**：ledger.jsonl O_APPEND 单 write 原子追加，两调度批不同时写（19:10 vs 周六 06:40 错峰）；risk_bridge 只读。
2. **junction 依赖**：调度链绑 `C:\aios\zerobound-kg`，若迁移目录需同步重建 junction（已记入判例）。
3. **晨检任务名漂移**：morning_check.ps1 引用 `KG-TdxSync/KG-Cl1Sync` 旧任务名，本机现名 `TdxSync/KgCl1Sync`，晨检①项会显示空——非阻塞，10-20 前顺手修。
