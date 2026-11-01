# 8091 问答 HTTP 端到端上线验收（2026-11-01）

> 真实 HTTP：`http://127.0.0.1:8091/api/kb/qa` · retrieval 3 / single 2 / multi 3（multi 默认 v4+segment+citation_retry，真实 LLM 串行，单条 180s）· 边界 3 例。不重启 8091、不压测。

## 结论

**正常路径可用，但存在需修项（边界校验/灰度字段）→ 修复后重新验收再对外** —— 判定：`conditional`（go=可对外灰度 / conditional=修复后 / no-go=不可对外）

### 需修项

- multi 通道 meta.prompt_version 未显示 v4，灰度可能未生效（需查 api 默认）。
- multi 通道 meta.guard_marker 未显示 segment，守卫分段模式可能未生效。

## 验收矩阵

| 通道 | 请求数 | HTTP 200 | LLM 调用 | 平均耗时 | 平均引用 | guard 通过 | fallback |
|---|---|---|---|---|---|---|---|
| retrieval | 3 | 3 | 否 | 2.06s | — | — | — |
| single | 2 | 2 | 0 | 11.71s | 3.0 | — | — |
| multi | 3 | 3 | 2 | 27.24s | 1.67 | 0 | 0 |

## multi 灰度字段校验

- prompt_versions：['v1']
- guard_markers：['strict']
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
| 1 | retrieval | 量学理论的结论在什么条件下可能不成立？ | 200 | 2.0s | 0 | v1 | strict | True | None | False |
| 2 | retrieval | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | 200 | 2.02s | 0 | v1 | strict | True | None | False |
| 3 | retrieval | 智能算力规模占比的核心驱动因素是什么？ | 200 | 2.17s | 0 | v1 | strict | True | None | False |
| 4 | single | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | 200 | 11.47s | 3 | v1 | strict | True | None | False |
| 5 | single | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | 200 | 11.94s | 3 | v1 | strict | True | None | False |
| 6 | multi | 海上风电项目规划的结论由哪些证据支撑？ | 200 | 11.19s | 3 | v1 | strict | True | None | False |
| 7 | multi | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | 200 | 9.48s | 2 | v1 | strict | True | None | False |
| 8 | multi | 伏击涨停的灵魂的景气度受哪些变量影响？ | 200 | 61.05s | 0 | v1 | strict | True | None | False |

## 风险与说明

- 风险：本次为低频串行验收（retrieval 3/single 2/multi 3），未做限流/并发/长稳压测。
- 风险：multi 含真实 LLM，单条耗时可受模型负载影响；本验收未触发限流。

- HTTP 端到端验收记录仅供上线评审参考，不构成投资建议。
- 总耗时 111.36s
