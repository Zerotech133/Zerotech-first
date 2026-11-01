# 执行回执 · HTTP 默认切换 v4+segment 与复验（8091）

> 2026-11-01 23:55–2026-11-02 00:02；依据 HTTP 验收 conditional 的 2 项需修。

## 一、变更
| 项 | 内容 |
|---|---|
| 代码 | `portal/api_kb_qa.py`：`DEFAULT_PROMPT='v4'`、`DEFAULT_GUARD_MARKER='segment'` |
| 重启 | K-RESTART 哨兵 + 门户API任务：旧进程 [7760,43432,48412] → 新进程；import 预检通过 |
| 生效验证 | meta：`prompt_version=v4`、`guard_marker=segment`、`citation_retry=True` |

## 二、复验收（真实 HTTP）
| 项 | 结果 |
|---|---|
| 正常路径 | retrieval 3 / single 2 / multi 3 = **8/8 HTTP 200** |
| 边界 | topk=0 / q 空 / 非法 mode → **3/3 400** |
| multi 灰度 | prompt=**v4**、guard=**segment**、retry=**True**（3/3） |
| 结论 | **go —— 可对外灰度发布** |

## 三、测试与门禁
| 项 | 结果 |
|---|---|
| api 测试 | 28 passed（默认/切换/缓存键适配） |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 四、回滚
- query 级：`prompt=v1`、`guard_marker=strict`（无需重启）；
- 全量回滚：改常量 + K-RESTART 重启。

## 五、涉及文件
- `portal/api_kb_qa.py`、`tools/tests/test_api_kb_qa.py`
- `research/reports/qa_http_live_acceptance_20261101.{json,md}`（复验版）
- `docs/plans/2026-11-01_问答HTTP接线L2提案_v1.md`、`agent_platform/catalogs/strategy_registry.json`