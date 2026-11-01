# 35b 模型 prompt v3 A/B（2026-11-01）

> v3 问题库前 8 条（申请 8，跳过 0）· 9b+v1 / 9b+v3（复用已有结果）vs **35b+v3**（`qwen3.6:35b-a3b` 现场生成）· 同聚类材料 + guard strict_line。

## 结论

**35b 未能让 v3 可用（未见显著提升）；不建议引入 35b 作为可选模型，保留 9b+v1** —— 建议：`keep_v1`（optional_model=可作可选模型 / optional_model_limited=灰度继续调优 / keep_v1=保留 9b+v1 / hold=先修）

- guard 通过率：9b+v3 0% → 35b+v3 0%（Δ+0%）；9b+v1 50%（Δ vs v1 -50%）。
- 可答率：9b+v3 0% → 35b+v3 0%；9b+v1 50%（Δ vs v1 -50%）。
- 平均推断比例：9b+v3 0.875 → 35b+v3 1.0；平均引用：2.38 → 1.88；平均耗时：7.7s → 13.02s。

## 三臂对比

| 指标 | 9b+v1（基线） | 9b+v3 | **35b+v3** |
|---|---|---|---|
| 可答率 | 50%（4/8） | 0%（0/8） | **0%**（0/8） |
| guard 通过率 | 50% | 0% | **0%** |
| fallback 率 | 50% | 100% | 100% |
| 平均推断比例 | 0.4792 | 0.875 | 1.0 |
| 平均引用数 | 2.38 | 2.38 | 1.88 |
| 平均耗时(s) | 15.73 | 7.7 | 13.02 |
| 错误数 | 0 | 0 | 0 |

## 逐条对比

| # | 类别 | 问题 | 9b+v1 状态/guard | 9b+v3 状态/guard | 35b+v3 状态/guard/推断 |
|---|---|---|---|---|---|
| 1 | 风险限制 | 量学理论的结论在什么条件下可能不成立？ | success/True | fallback/False | fallback/False/1.0 |
| 2 | 关键数据 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | success/True | fallback/False | fallback/False/1.0 |
| 3 | 驱动因素 | 智能算力规模占比的核心驱动因素是什么？ | fallback/False | fallback/False | fallback/False/1.0 |
| 4 | 如何识别 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | success/True | fallback/False | fallback/False/1.0 |
| 5 | 影响如何 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | fallback/False | fallback/False | fallback/False/1.0 |
| 6 | 是什么 | 海上风电项目规划的结论由哪些证据支撑？ | success/True | fallback/False | fallback/False/1.0 |
| 7 | 竞争格局 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | fallback/False | fallback/False | fallback/False/1.0 |
| 8 | 驱动因素 | 伏击涨停的灵魂的景气度受哪些变量影响？ | fallback/False | fallback/False | fallback/False/1.0 |

## 风险与说明

- 风险：35b 首次加载/推理耗时远高于 9b（冷加载可达 2–3 分钟），作为默认模型成本高，需评估吞吐与并发。
- 风险：样本小（默认 10 条，35b 慢时可能更少），结论需扩样 30–50 条复核。
- 风险：35b 输出更长，token 成本与响应时延需真实压测（单条 300s 上限）。
- 风险：本 A/B 仅换模型不换 prompt，若 35b 仍不达标说明 v3 prompt 需重写而非模型升级。

- 35b A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。
- 说明：35b+v3 现场生成（同聚类材料+同 guard strict_line）；9b+v1 / 9b+v3 复用已有结果；35b 冷加载可能较慢，超时/预算用尽时余下跳过并注明
- 总耗时 104.17s
