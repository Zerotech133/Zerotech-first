# 问答链路端到端验收（2026-11-01）

> CLI：v3 前 10 条 · `kb_qa.ask(mode=multi, guard=True, on_guard_fail=fallback)` 真实调用 · HTTP：`portal/api_kb_qa.handle` **进程内**验证（不启动服务、注入 mock）· 门户：http://127.0.0.1:8734

## 验收结论

**三段验收全部通过，可进入 8091 接线 L2 审批**（三段：CLI=通过 / HTTP=通过 / 门户=通过）

- 上线判定：**go**

## 一、CLI 端到端

- 请求 10 条，实际 10 条，跳过 0 条；pass=True
- 状态：success **5** / no_evidence 0 / fallback 5
- guard：passed 5 / failed 5；平均引用 2.4 条；平均耗时 15.82s

| # | 类别 | 问题 | 状态 | guard | 引用 | 可解析率 | 支撑率 | 推断比 | 耗时 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 风险限制 | 量学理论的结论在什么条件下可能不成立？ | success | True/pass | 4 | 1.0 | 1.0 | 0.0 | 15.8s |
| 2 | 关键数据 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | success | True/pass | 2 | 1.0 | 1.0 | 0.0 | 18.03s |
| 3 | 驱动因素 | 智能算力规模占比的核心驱动因素是什么？ | fallback | False/fallback | 2 | 0.0 | 0.0 | 0.5 | 14.47s |
| 4 | 如何识别 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | success | True/pass | 3 | 1.0 | 1.0 | 0.3333 | 17.69s |
| 5 | 影响如何 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | fallback | False/fallback | 1 | 1.0 | 1.0 | 0.5 | 13.92s |
| 6 | 是什么 | 海上风电项目规划的结论由哪些证据支撑？ | success | True/pass | 3 | 1.0 | 1.0 | 0.5 | 16.52s |
| 7 | 竞争格局 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | fallback | False/fallback | 2 | 1.0 | 1.0 | 1.0 | 18.11s |
| 8 | 驱动因素 | 伏击涨停的灵魂的景气度受哪些变量影响？ | fallback | False/fallback | 2 | 0.0 | 0.0 | 1.0 | 11.3s |
| 9 | 风险限制 | 网络安全软技能需求的主要风险与不确定性有哪些？ | success | True/pass | 3 | 1.0 | 1.0 | 0.4 | 20.11s |
| 10 | 关键数据 | 品牌AI心智总得分有哪些可核验的统计口径或样本？ | fallback | False/fallback | 2 | 0.0 | 0.0 | 0.0 | 12.24s |

## 二、HTTP 预接线（进程内）

- WIRED=`False`（未注册到运行中的 8091）；用例 5/5 通过

| 用例 | 参数 | 状态码 | 期望 | 结果 | meta |
|---|---|---|---|---|---|
| retrieval | {"mode": "retrieval"} | 200 | 200 | ✅ | mode=retrieval/llm=False/guard={"enabled": true, "applicable": false, "applied": false, "on |
| single | {"mode": "single"} | 200 | 200 | ✅ | mode=single/llm=True/guard={"enabled": true, "applicable": false, "applied": false, "on |
| multi | {"mode": "multi"} | 200 | 200 | ✅ | mode=multi/llm=True/guard={"enabled": true, "applicable": true, "applied": true, "on_f |
| multi_guard0 | {"mode": "multi", "guard": "0"} | 200 | 200 | ✅ | mode=multi/llm=True/guard={"enabled": false, "applicable": true, "applied": false, "on |
| guard_invalid | {"mode": "multi", "guard": "2"} | 400 | 400 | ✅ | mode=None/llm=None/guard=null |

## 三、门户核查

| 项 | URL | 状态码 | 内容 | 结果 |
|---|---|---|---|---|
| kb_reports.html | http://127.0.0.1:8734/kb_reports.html | 200 | bytes=33828 含问答区块 | ✅ |
| kb_qa_samples.json | http://127.0.0.1:8734/static/kb_qa_samples.json | 200 | bytes=38819 schema=2 samples=12 | ✅ |

## 风险与说明

- 风险：HTTP 验收为进程内 handle 调用（未启动 8091），不代表真实网关/鉴权/超时链路已就绪。
- 风险：HTTP 用例中 single/multi 使用注入 mock（不真调 LLM），仅验证参数/状态码/信封契约。
- 风险：CLI 验收条数受 LLM 时间预算限制（默认 10 条），结论为 effective 子集。
- 风险：multi+guard 的 fallback 答案信息密度低于原答案，上线前需继续人工抽查。
- 风险：WIRED=False：本轮不注册、不重启 8091；正式接线需 L2 五要素审批。

- 验收记录仅供研究参考，不构成投资建议；HTTP 验收不启动服务。
- 总耗时 162.11s
