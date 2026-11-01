# guard 标记解析修复 + v3 离线重评（2026-11-01）

## 背景与口径

- 修复：kb_qa_guard 新增 `marker_mode`（默认 `strict_line`）——只统计行首/明确标注的【事实】/【推断】，「事实：」「推断：」前缀；排除 markdown 标题（`## 推断`）、「【推断】如下」标题行、说明文字、few-shot/示例行与 ``` 代码块；`legacy` 保留旧子串行为供兼容对比。
- 数据：原始 v3 答案未持久化（只有 guard 回退后 160 字 preview）；为做修复前后离线对比，重新采集 10 条 v3 原始答案后离线重算 legacy vs strict_line（不改变已存结果）
- 已存 preview 检查：wave26 v3 结果仅存 guard 回退后 160 字 preview，无法完整离线重算。

## 修复前后对比（同一批 10 条 v3 原始答案，离线重算）

| 指标 | legacy（修复前口径） | strict_line（修复后） | Δ |
|---|---|---|---|
| guard 通过率 | 0%（0/10） | 10%（1/10） | +10% |
| 可答率（guard 后 success） | 0% | 10% | +10% |
| fallback 率 | 100% | 90% | -10% |
| 平均推断比例 | 1.0 | 0.8 | -0.200 |
| 平均引用数 | 2.5 | 2.5 | +0.00 |

## 结论

**不值得合入** —— 标记解析修复未显著改善 v3 通过率 → v3 prompt 不值得合入，保留 v1

- guard 通过率：legacy 0%（0/10）→ strict_line 10%（1/10）。
- 可答率（guard 后 success）：0% → 10%；fallback 率：100% → 90%。
- 平均推断比例：1.0 → 0.8；平均引用：2.5 → 2.5。

## 逐条对比

| # | 类别 | 问题 | legacy pass/infer | strict pass/infer | legacy→strict 状态 |
|---|---|---|---|---|---|
| 1 | 风险限制 | 量学理论的结论在什么条件下可能不成立？ | False/1.0 | False/0.0 | fallback → fallback |
| 2 | 关键数据 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | False/1.0 | False/1.0 | fallback → fallback |
| 3 | 驱动因素 | 智能算力规模占比的核心驱动因素是什么？ | False/1.0 | False/1.0 | fallback → fallback |
| 4 | 如何识别 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | False/1.0 | False/1.0 | fallback → fallback |
| 5 | 影响如何 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | False/1.0 | False/1.0 | fallback → fallback |
| 6 | 是什么 | 海上风电项目规划的结论由哪些证据支撑？ | False/1.0 | False/1.0 | fallback → fallback |
| 7 | 竞争格局 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | False/1.0 | False/1.0 | fallback → fallback |
| 8 | 驱动因素 | 伏击涨停的灵魂的景气度受哪些变量影响？ | False/1.0 | False/1.0 | fallback → fallback |
| 9 | 风险限制 | 网络安全软技能需求的主要风险与不确定性有哪些？ | False/1.0 | True/0.0 | fallback → success |
| 10 | 关键数据 | 品牌AI心智总得分有哪些可核验的统计口径或样本？ | False/1.0 | False/1.0 | fallback → fallback |

## 风险与说明

- 风险：wave25/26 仅持久化了 v3 答案的 160 字 guard 回退后 preview（原文未存），无法完全离线重算；本报告额外重新采集 10 条 v3 原始答案（同一 prompt/模型，约 1-2 分钟）再做离线对比，已注明偏差。
- 风险：n=10 为小样本；如需决策需扩样 30–50 条。
- 风险：strict_line 仅统计行首/明确标注，模型若把标记写在句子中间会漏计（可能存在低估计）；legacy 会过计。
- 风险：guard 通过率提升仍可能被引用/集中度问题限制（见逐条 citation_* 字段）。

- 标记解析修复评估仅供研究参考，不构成投资建议。
- 耗时 80.7s
