# 门户问答样例刷新（v4 默认 · 真实 LLM）

- 生成：2026-11-02T00:01:42+08:00；mode=multi；prompt=v4；guard_marker=segment；citation_retry=True；model=qwen3.5:9b
- 样本数 8：success **8** / fallback 0 / refusal 0；引用合计 19（均 2.38）；均耗时 15.05s；guard 通过率 1.0

| # | id | 问题 | 状态 | 引用数 | 引用可解析 | 推断占比 | guard | 耗时(s) |
|---|---|---|---|---|---|---|---|---|
| 1 | q2_b5a0a0e045 | 量学理论的结论在什么条件下可能不成立？ | success | 3 | 100.0% | 50.0% | True | 18.5 |
| 2 | q2_aa49124184 | 储气调峰能力建设中研报提供了哪些可验证的关键数据或指标？ | success | 2 | 100.0% | 50.0% | True | 25.75 |
| 3 | q2_bcbfd79337 | 智能算力规模占比的核心驱动因素是什么？ | success | 3 | 100.0% | 50.0% | True | 13.16 |
| 4 | q2_a593448ce5 | 北京市货物贸易净出口需要跟踪哪些指标或事件？ | success | 2 | 100.0% | 60.0% | True | 12.05 |
| 5 | q2_3d1abf0240 | 离岛免税消费数据对相关标的基本面或估值的影响如何？ | success | 2 | 100.0% | 60.0% | True | 12.97 |
| 6 | q2_1e95f989df | 海上风电项目规划的结论由哪些证据支撑？ | success | 3 | 100.0% | 50.0% | True | 13.42 |
| 7 | q2_6076398cc7 | 保险资金投资港股通 ETF的进入壁垒与差异化优势是什么？ | success | 2 | 100.0% | 60.0% | True | 11.89 |
| 8 | q2_f93ae02b9d | 伏击涨停的灵魂的景气度受哪些变量影响？ | success | 2 | 100.0% | 60.0% | True | 12.7 |

## 字段说明

- 保留：mode / guard_enabled / guard_passed / guard_action / citation_resolvable_rate / inference_ratio / inference_heavy / fallback / refusal / status / citations 等；
- 追加：`prompt_version`（v4）、`citation_retry_enabled`（引用自检重试：勾上）、`guard_marker_mode`（segment）；
- 备份：旧样例先复制到 research/reports/kb_qa_samples_backup4_20261101.json（字节级）。
