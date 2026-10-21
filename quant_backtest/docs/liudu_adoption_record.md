# 六度选股 · N3 采纳门签字记录（含证据评估，最终签字槽留老田）

> N3 采纳门 = **BACK1 回测锚定口径** + **STRAT1 策略采纳** 双签字。
> 双签字齐全后，六度选股方可解锁 R1 试运行。
> 下列「AI 评估」基于仓库内既有真实产物审阅，**不构成采纳结论**；最终签字权在老田。

---

## 一、证据审阅（2026-10-21 主会话复核，真实产物非合成）

### BACK1 —— ST-PIT 回测锚定

| 项 | 证据 | 状态 |
|---|---|---|
| 锚定纯函数 | `market_scan.is_st_pit_blocked` 已建；`tests/test_n3_st_pit_blocked.py` **7 passed**（含逐事件 PIT 不回写未来、边界当日即剔、closed 区间、ann 防御性、str/int 同义、真实加载集成） | ✅ 函数级已验证 |
| 锚定价值实测 | `reports/a3_st_pit_diff_report.json`（10-21 12:22）：28129 事件 / 5036 只 / 961 ST 区间；PIT 比 legacy 少剔 388 事件；**lookahead 影响 541 事件（140 只）= legacy 剔除的 58.2%**——即 legacy 会用"未来 ST"回写删除过去信号 | ✅ 实证锚定必要 |
| 公告时点规则 | `announcement_rule_equivalence: "NOT_VERIFIED"`（仅验证 ann≤start、lag 中位数 −3 天；**未验证逐事件公告可知时点或完整公告过滤规则**） | ⚠️ 未证实 |
| 真实数据接线 | 门禁测试 `_load_st_pit_intervals` 真实 `ts_namechange.parquet` **本环境缺失 → 被 skip**；函数注释自陈"供 BACK1/STRAT1 签字采纳**后在引擎侧接入**" | ⚠️ 未引擎接线 |
| 报告口径 | a3 报告 `scope=OFFLINE_READONLY_NOT_ADOPTION_EVIDENCE` | 说明性 |

**BACK1 评估**：函数与实测均证明 ST-PIT 锚定**必要且逻辑正确**，但 (a) 公告过滤规则未完全证实、(b) 真实 parquet 未在本环境加载/引擎未接线。属**条件就绪**：补齐 (a)(b) 后可签字。

### STRAT1 —— 策略采纳

| 项 | 证据 | 状态 |
|---|---|---|
| 真实产出 | `reports/xunyou_pool_20261020.json` + `xunyou_report_20261020.md`：融合 105 / buy 76 / T1 20 / T2 7 | ✅ 能跑出池 |
| **数据源污染** | **T2 含 4 个非 A 股代码：`ON`、`RVSB`、`TXN`、`NNBR`**（美/外股 ticker）——选股产物混入非 A 股标的 | ❌ 硬缺陷 |
| 对账可比性 | `reports/a4_xunyou_vs_picks_report.json`：`comparability=NOT_COMPARABLE`，Jaccard=0.0437；报告自陈 `SCOPE=READONLY...NOT_ADOPTION_EVIDENCE`、"两链不可直接互替" | ⚠️ 不可比 |
| 契约未证实 | 20261020 汇报信列 6 项 UNPROVEN：`SOURCE_FILE_MISSING`、`DUPLICATE_STRATEGY_SYMBOL`、`BUY_SELL_CONTRADICTION`、`SIX_STRATEGY_CONTRACT_UNPROVEN`、`PG_PERSISTENCE_UNPROVEN` 等 | ❌ 未证实 |
| 范围标记 | 所有产出 `RESEARCH_ONLY_NOT_PRODUCTION` / `BLOCK_CUTOVER` | 说明性 |

**STRAT1 评估**：策略能产出候选池，但存在**数据源污染（非 A 股混入 T2）**与**六项契约未证实**，且 A4 对账明确不可比、非采纳证据。**当前不具备采纳条件**——污染须在 N1 universe=market 实跑强制过滤 + N5 数据源治理后才能消除；契约须经专门验证闭环。

---

## 二、AI 建议（非结论，供老田裁决）

- **总体建议：暂缓采纳（BLOCK），不签字解锁 R1。**
- **BACK1**：可随 (a) 公告过滤规则补全验证、(b) 真实 `ts_namechange.parquet` 到位后引擎接线，单独具备签字条件。
- **STRAT1**：须先解两硬阻塞——①消除非 A 股污染（N1 universe=market 实跑强制 + N5 源治理）；②SIX_STRATEGY_CONTRACT / PG_PERSISTENCE 等契约证实闭环。

---

## 三、解阻塞清单（STRAT1 签字前置）

1. N1：universe=market 在**真实选股链**强制过滤非 A 股（当前 trial 已决议，但真实 run 未接）。
2. N5：八类数据源治理，消除外源 ticker 泄漏（凭证 + 源清洗）。
3. 契约验证：补齐 `SIX_STRATEGY_CONTRACT_PROVEN` / `PG_PERSISTENCE_PROVEN` 等门禁。
4. A4 对账：达到 `COMPARABLE` 且结论非 `BLOCK_CUTOVER` 后方可作为采纳证据。

---

## 四、签字结论（留老田填）

- **BACK1（回测锚定口径）结论**：`[采纳 / 驳回 / 条件采纳]` —— 待老田
- **STRAT1（策略采纳）结论**：`[采纳 / 驳回 / 条件采纳]` —— 待老田
- **总体采纳结论**：`[采纳 / 驳回 / 暂缓]` —— 待老田
- 签字人：老田 ｜ 签字时间：`<待填>` ｜ 备注：`<待填>`
