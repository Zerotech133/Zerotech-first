# 执行回执 · citation_retry 灰度切默认

> 2026-11-01 23:25–23:40；依据 wave37 扩样 30 条（guard 100%、+10pp、耗时 1.071×）。

## 一、变更
| 层 | 变更 |
|---|---|
| `kb_qa.ask` | 默认 `citation_retry=True`（`KB_QA_CITATION_RETRY=0` 回滚）；payload 记录 `citation_retry_enabled` |
| CLI | `--citation-retry` / `--no-citation-retry`（默认读 env） |
| `kb_qa_multi` | v4 路径引用自检重试（<2 单元且未拒答→强化指令重试一次） |
| HTTP 预接线 | `citation_retry=1|0`（默认 1）；缓存键含；meta 记录 citation_retry/citation_retry_info |
| 回滚 | env=0 / CLI `--no-citation-retry` / HTTP `citation_retry=0` |

## 二、依据（wave37 扩样 30 条）
| 指标 | off | **on** | Δ |
|---|---|---|---|
| guard 通过率 | 90.0% | **100.0%** | +10.0pp |
| fallback 率 | 10.0% | **0.0%** | -10pp |
| 引用可解析/支撑 | 96.7%/96.7% | **100%/100%** | +3.3pp |
| 平均耗时 | 16.02s | 17.17s | 1.071× |

## 三、验证
| 项 | 结果 |
|---|---|
| 核心测试 | **76 passed**（kb_qa/api/multi/guard/retry/扩样/复评） |
| 语法门 | PASS（3976 文件） |
| 默认行为 | CLI ask 默认 v4+segment+retry；HTTP 预接线默认 v1+strict+retry=1（未接 8091） |

## 四、边界与监控
1. 重试仅触发于引用<2 的样本（本次 30 条触发 4 次），正常样本不受影响；
2. 灰度期观察 fallback/耗时；若异常：env 0 一行回滚；
3. HTTP 通道仍待 8091 接线审批（当前 WIRED=False）。

## 五、涉及文件
- `tools/kb_corpus/kb_qa.py`、`portal/api_kb_qa.py`
- `tools/tests/test_kb_qa.py`、`tools/tests/test_api_kb_qa.py`
- `docs/plans/2026-11-01_研报问答_v1.md`、`agent_platform/catalogs/strategy_registry.json`