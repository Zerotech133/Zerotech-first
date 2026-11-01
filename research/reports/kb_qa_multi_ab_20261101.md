# 多单元聚合扩样 A/B（single vs multi，2026-11-01）

> v3 问题库前 **30** 条（申请 30）· top-k=8 · single=`kb_qa.ask` / multi=`kb_qa_multi` 聚合 · 复用 v3 前 12 条 single 基线 · qwen3.5:9b 串行

## 结论与建议

**建议设为默认** —— 建议将 multi 设为问答默认模式（A/B 通过）

- 可答率：single 33%（10/30）→ multi 80%（24/30），Δ+47%。
- 配对：改善 14 条 / 回退 0 条；McNemar p=0.000122。
- 平均引用：2.4 → 2.25；单条平均耗时：8.35s → 8.61s（比值 1.03）。

### 判定阈值

| 判据 | 阈值 | 实测 | 结果 |
|---|---|---|---|
| 可答率提升 ≥15% | ≥15% | +47% | ✅ |
| 回退（single success→multi fail）不明显增多 | 回退数 ≤ max(1, 10%·改善数) | 回退 0 / 改善 14 | ✅ |
| 耗时可接受 | multi/single 单条耗时比 ≤2.5 | 1.03 | ✅ |
| McNemar 配对检验显著 | p<0.05 | p=0.000122 | ✅ |
| 样本量足够 | ≥30 条（LLM 慢可减并注明） | 30 条 | ✅ |

## A/B 汇总

| 指标 | single | multi | Δ |
|---|---|---|---|
| 可答率 | 33%（10/30） | **80%**（24/30） | +47% |
| 成功/拒答/降级 | 10 / 20 / 0 | 24 / 6 / 0 | — |
| 平均引用（成功样本） | 2.4 | 2.25 | -0.15 |
| 单条平均耗时(s) | 8.35 | 8.61 | 比值 1.03 |
| 平均聚类 | — | 3.0 | — |
| 直接事实/推断标记 | — | 22 / 32 | — |

## 配对 McNemar 检验

| 配对 | 数量 |
|---|---|
| a：both success | 10 |
| b：single fail → multi success（改善） | 14 |
| c：single success → multi fail（回退） | 0 |
| d：both fail | 6 |
| **McNemar 精确 p** | **0.000122** |

## 按模板类别

| 类别 | 条数 | single | multi | Δ |
|---|---|---|---|---|
| 风险限制 | 4 | 0% | 100% | +100% |
| 如何识别 | 5 | 20% | 100% | +80% |
| 影响如何 | 4 | 0% | 75% | +75% |
| 关键数据 | 4 | 25% | 75% | +50% |
| 驱动因素 | 5 | 40% | 60% | +20% |
| 是什么 | 4 | 100% | 100% | +0% |
| 竞争格局 | 4 | 50% | 50% | +0% |

## 答案类型分布（multi）

- direct_fact+inference：11 条
- inference：7 条
- refusal：6 条
- answer：5 条
- direct_fact：1 条

改善样本（single 拒答→multi 成功，14 条）：q2_b5a0a0e045、q2_aa49124184、q2_a593448ce5、q2_3d1abf0240、q2_ef78939178、q2_0d7db24320、q2_b74f8cebda、q2_5cb2ec9a7b、q2_960b202685、q2_9efedd1a83、q2_56b3d0673e、q2_a4026386c5、q2_764c00190b、q2_0e42bbf120

## 逐条对比

| # | 类别 | 问题 | single 状态/引用/耗时 | multi 状态/引用/耗时 | 聚类 | 答案类型 |
|---|---|---|---|---|---|---|
| 1 | 风险限制 | 量学理论的结论在什么条件下可能不成立？ | no_evidence/0/0.0s | success/0/9.01s | 3 | direct_fact+inference |
| 2 | 关键数据 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | no_evidence/0/0.0s | success/2/7.99s | 3 | direct_fact+inference |
| 3 | 驱动因素 | 智能算力规模占比的核心驱动因素是什么？ | no_evidence/2/0.0s | no_evidence/0/5.67s | 3 | refusal |
| 4 | 如何识别 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | no_evidence/5/0.0s | success/2/10.26s | 3 | inference |
| 5 | 影响如何 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | no_evidence/0/0.0s | success/3/6.77s | 3 | direct_fact+inference |
| 6 | 是什么 | 海上风电项目规划的结论由哪些证据支撑？ | success/3/0.0s | success/3/7.48s | 3 | direct_fact+inference |
| 7 | 竞争格局 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | success/1/0.0s | success/2/8.62s | 3 | answer |
| 8 | 驱动因素 | 伏击涨停的灵魂的景气度受哪些变量影响？ | no_evidence/0/0.0s | no_evidence/0/2.75s | 3 | refusal |
| 9 | 风险限制 | 网络安全软技能需求的主要风险与不确定性有哪些？ | no_evidence/0/0.0s | success/0/9.75s | 3 | answer |
| 10 | 关键数据 | 品牌AI心智总得分有哪些可核验的统计口径或样本？ | no_evidence/0/0.0s | no_evidence/0/3.69s | 3 | refusal |
| 11 | 影响如何 | 中国畜禽肉产品主要出口市场的传导路径与受益环节是什么？ | no_evidence/1/0.0s | success/2/7.2s | 3 | direct_fact+inference |
| 12 | 竞争格局 | 优先行动：支持本土生态系统的进入壁垒与差异化优势是什么？ | success/3/0.0s | success/0/8.8s | 3 | direct_fact+inference |
| 13 | 如何识别 | 如何识别全球风电市场趋势相关的机会或信号？ | success/3/9.98s | success/2/10.25s | 3 | direct_fact+inference |
| 14 | 是什么 | 区域收入分化的核心结论与主要依据是什么？ | success/1/10.12s | success/3/12.08s | 3 | answer |
| 15 | 关键数据 | 电气化率的关键参数或阈值有哪些？ | success/5/12.19s | success/3/11.39s | 3 | inference |
| 16 | 如何识别 | 计算机行业板块表现的验证方式与观测窗口是什么？ | no_evidence/0/7.12s | success/3/11.67s | 3 | direct_fact |
| 17 | 风险限制 | 供应链风险管理的限制条件与适用边界是什么？ | no_evidence/0/7.52s | success/3/9.76s | 3 | answer |
| 18 | 影响如何 | 中国畜禽肉产品贸易总体规模的传导路径与受益环节是什么？ | no_evidence/0/8.06s | success/2/10.47s | 3 | direct_fact+inference |
| 19 | 是什么 | 传媒主体投资并购策略的核心结论与主要依据是什么？ | success/2/8.47s | success/2/8.88s | 3 | direct_fact+inference |
| 20 | 竞争格局 | 宁夏新能源发展目标的竞争格局与主要参与者有哪些？ | no_evidence/1/6.62s | no_evidence/0/5.06s | 3 | refusal |
| 21 | 驱动因素 | 购房成本节约测算的核心驱动因素是什么？ | success/2/10.47s | success/2/9.62s | 3 | inference |
| 22 | 影响如何 | 伏击涨停的参与模式对产业链或行业格局的影响如何？ | no_evidence/0/5.53s | no_evidence/2/6.69s | 3 | refusal |
| 23 | 关键数据 | 企业信用结构性分化的证据链由哪些数据构成？ | no_evidence/0/7.19s | success/2/8.69s | 3 | inference |
| 24 | 如何识别 | GPU 算力价格调整需要跟踪哪些指标或事件？ | no_evidence/4/11.97s | success/3/9.72s | 3 | answer |
| 25 | 竞争格局 | 纺织品出口增长的市场份额或集中度如何？ | no_evidence/2/6.28s | no_evidence/0/5.97s | 3 | refusal |
| 26 | 是什么 | 大中华区零售市场趋势具体指什么，研报如何定义或描述？ | success/3/8.89s | success/3/7.98s | 3 | direct_fact+inference |
| 27 | 风险限制 | 钠电池产能现状的结论在什么条件下可能不成立？ | no_evidence/0/7.42s | success/3/12.31s | 3 | inference |
| 28 | 驱动因素 | Pace 计划目标的产业趋势与演进方向如何？ | success/1/8.14s | success/2/10.55s | 3 | inference |
| 29 | 驱动因素 | 涨停基因的景气度受哪些变量影响？ | no_evidence/2/7.38s | success/4/9.47s | 3 | inference |
| 30 | 如何识别 | 快递行业量价走势的验证方式与观测窗口是什么？ | no_evidence/0/6.88s | success/3/9.76s | 3 | direct_fact+inference |

## 风险与后续

- 风险：multi 聚合允许推断，存在跨单元过度综合/错误拼接风险；引用校验不验证语义支撑，需人工抽查。
- 风险：single 基线中前 12 条复用 v3 旧结果（不同时间点），与本次 multi 存在时间差，可能影响耗时/状态可比性。
- 风险：样本受 LLM 时间预算限制（LLM 慢时减条），未达 30 条时统计功效有限。
- 风险：耗时受 Ollama 负载影响大（v3 single 与本次 multi 测量时点不同），latency_ratio 仅作参考。
- 风险：若切换默认，建议先灰度（页面/CLI --multi 可选）+ 影子对比，再全量。
- 下一步：若通过：在 kb_qa CLI/HTTP（未接线）增加 --multi 选项并灰度；保留 single fallback（multi 降级时自动回退）。
- 下一步：扩样到 50 条（全量问题库）复跑本 A/B，验证 Δ 与 McNemar 稳定性。
- 下一步：对 multi 的答案抽样人工复核直接事实/推断边界与引用语义支撑。

## 说明

- A/B 结果仅供研究参考，不构成投资建议；multi 允许推断，引用/事实需人工复核。
- single 为 kb_qa.ask（单单元标准问答）；multi 为 kb_qa_multi 多单元聚合；前 12 条 single 复用 wave15 v3 已记录结果（reused=true，耗时不参与 single 均值）
- 耗时 408.59s；配置 {"n_requested": 30, "n_actual": 30, "reuse_v3_single": true, "k": 8, "model": "qwen3.5:9b", "timeout_seconds": 240.0, "deadline_seconds": 480.0, "single_skipped": 0, "multi_skipped": 0}
