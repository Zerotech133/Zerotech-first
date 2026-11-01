# 8091 问答 HTTP 端到端上线验收（2026-11-01）

> 真实 HTTP：`http://127.0.0.1:8091/api/kb/qa` · retrieval 3 / single 2 / multi 3（multi 默认 v4+segment+citation_retry，真实 LLM 串行，单条 180s）· 边界 3 例。不重启 8091、不压测。

## 结论

**8091 /api/kb/qa 端到端验收通过（retrieval/single/multi 正常、边界 400 正确、multi 走 v4+segment+retry）→ 可对外灰度发布** —— 判定：`go`（go=可对外灰度 / conditional=修复后 / no-go=不可对外）

## 验收矩阵

| 通道 | 请求数 | HTTP 200 | LLM 调用 | 平均耗时 | 平均引用 | guard 通过 | fallback |
|---|---|---|---|---|---|---|---|
| retrieval | 3 | 3 | 否 | 2.36s | — | — | — |
| single | 2 | 2 | 2 | 10.87s | 2.0 | — | — |
| multi | 3 | 3 | 3 | 8.65s | 2.33 | 0 | 0 |

## multi 灰度字段校验

- prompt_versions：['v4']
- guard_markers：['segment']
- citation_retry：[True, True, True]

## 边界参数（期望 400）

| 用例 | 状态 | 期望 | 结果 |
|---|---|---|---|
| topk_zero | 400 | 400 | ✅ |
| empty_q | 400 | 400 | ✅ |
| invalid_mode | 400 | 400 | ✅ |

## 逐条记录

| # | mode | 问题 | 状态 | 耗时 | 引用 | prompt | marker | retry | guard passed | fallback |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | retrieval | 量学理论的结论在什么条件下可能不成立？ | 200 | 2.41s | 0 | v4 | segment | True | None | False |
| 2 | retrieval | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | 200 | 2.34s | 0 | v4 | segment | True | None | False |
| 3 | retrieval | 智能算力规模占比的核心驱动因素是什么？ | 200 | 2.34s | 0 | v4 | segment | True | None | False |
| 4 | single | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | 200 | 11.06s | 2 | v4 | segment | True | None | False |
| 5 | single | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | 200 | 10.67s | 2 | v4 | segment | True | None | False |
| 6 | multi | 海上风电项目规划的结论由哪些证据支撑？ | 200 | 8.52s | 3 | v4 | segment | True | None | False |
| 7 | multi | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | 200 | 9.61s | 2 | v4 | segment | True | None | False |
| 8 | multi | 伏击涨停的灵魂的景气度受哪些变量影响？ | 200 | 7.83s | 2 | v4 | segment | True | None | False |

## 风险与说明

- 风险：本次为低频串行验收（retrieval 3/single 2/multi 3），未做限流/并发/长稳压测。
- 风险：multi 含真实 LLM，单条耗时可受模型负载影响；本验收未触发限流。

- HTTP 端到端验收记录仅供上线评审参考，不构成投资建议。
- 总耗时 54.83s
