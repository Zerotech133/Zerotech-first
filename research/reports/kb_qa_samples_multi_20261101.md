# 门户问答样例 multi+guard 升级（2026-11-01）

> v3 问题库前 12 条 · kb_qa.ask(mode=multi, guard=True, on_guard_fail=fallback) · qwen3.5:9b 串行 · 单条 timeout 300s。

## 状态统计

| 指标 | 值 |
|---|---|
| 样本数 | 12 |
| 成功（可答） | 3 |
| 拒答（无依据） | 1 |
| 降级 fallback | 8 |
| guard 通过 / 未通过 | 4 / 8 |
| 平均引用数 | 2.25 |
| 平均推断比例 | 0.5 |
| 总耗时 | 120.9s |

## 逐条状态

| # | 类别 | 问题 | 状态 | guard | action | 引用 | 推断比 | 耗时(s) |
|---|---|---|---|---|---|---|---|---|
| 1 | 风险限制 | 量学理论的结论在什么条件下可能不成立？ | no_evidence | True | pass | 3 | 0.0 | 9.72 |
| 2 | 关键数据 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | success | True | pass | 2 | 0.0 | 9.29 |
| 3 | 驱动因素 | 智能算力规模占比的核心驱动因素是什么？ | fallback | False | fallback | 2 | 0.6667 | 8.03 |
| 4 | 如何识别 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | fallback | False | fallback | 3 | 1.0 | 12.55 |
| 5 | 影响如何 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | fallback | False | fallback | 3 | 0.6667 | 10.4 |
| 6 | 是什么 | 海上风电项目规划的结论由哪些证据支撑？ | success | True | pass | 3 | 0.5 | 9.7 |
| 7 | 竞争格局 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | success | True | pass | 2 | 0.0 | 10.5 |
| 8 | 驱动因素 | 伏击涨停的灵魂的景气度受哪些变量影响？ | fallback | False | fallback | 2 | 1.0 | 6.36 |
| 9 | 风险限制 | 网络安全软技能需求的主要风险与不确定性有哪些？ | fallback | False | fallback | 2 | 0.0 | 14.22 |
| 10 | 关键数据 | 品牌AI心智总得分有哪些可核验的统计口径或样本？ | fallback | False | fallback | 2 | 1.0 | 6.34 |
| 11 | 影响如何 | 中国畜禽肉产品主要出口市场的传导路径与受益环节是什么？ | fallback | False | fallback | 1 | 0.5 | 12.65 |
| 12 | 竞争格局 | 优先行动：支持本土生态系统的进入壁垒与差异化优势是什么？ | fallback | False | fallback | 2 | 0.6667 | 11.12 |

## 主题分布

- 量化/技术分析：2 条
- 其他/综合：2 条
- AI算力/半导体：2 条
- 宏观/策略/贸易：2 条
- 消费/医药：2 条
- 新能源/电池：1 条
- 金融/资本市场：1 条

## 说明

- 原门户样本已备份到 `C:\项目\老田的AI助手\research\reports\kb_qa_samples_backup3_20261101.json`（backup3，创建=True）；
- portal/static/kb_qa_samples.json 已升级为 schema v2（mode=multi + guard 字段）；
- 页面 /kb_reports.html 问答示例区块需适配新字段（multi/guard/fallback 标记）；
- 答案由本地 LLM 生成，仅供研究参考，不构成投资建议。
