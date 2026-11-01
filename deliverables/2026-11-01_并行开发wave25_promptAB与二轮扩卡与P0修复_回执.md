# 并行开发 wave25 · prompt v2 A/B / 学习卡第二轮扩卡（+P0 覆盖修复） · 回执

> 时间：2026-11-01 14:05–14:25；CN/CO 并行；另发生并修复一起数据覆盖事故（见下）。

## 一、P0 数据覆盖事故（已修复并单独提交 45a56a903）
| 项 | 结果 |
|---|---|
| 事故 | 并发会话 14:10 运行 `learning_cards.py`，把 70 张学习卡覆盖回 20 张 |
| 恢复 | 从 HEAD（e5b7a5696，70 张）`git checkout` 恢复；digest 已刷新 |
| 根因 | 生成器直接覆盖 OUT_JSON，不保留人工合并/修订卡 |
| 修复 | `learning_cards.py` 抽出 `merge_cards`：现有卡全量保留，仅追加 concept 不重复的新卡；新增回归测试 4 项；重跑生成器验证 70→70 不缩水 |
| 状态 | 已提交（45a56a903），门禁 PASS |

## 二、CN multi 答案质量提升（prompt v2 A/B）→ **不合入**
| 指标 | v1（现 prompt） | v2（新 prompt） | Δ |
|---|---|---|---|
| 可答率 | 50%（6/12） | **0%（0/12）** | -50pp |
| guard 通过率 | 50% | **8%** | -42pp |
| fallback 率 | 50% | 92% | +42pp |
| 平均推断比例 | 0.46 | **0.92** | +0.45 |
- **结论：v2 未优于 v1，保留现有 prompt**（样本 12 条 effective 子集，需扩样复核；如再试需按方向调整：v2 指令过严导致模型更多推断/拒答）。

## 三、CO 学习卡第二轮扩卡（候选 40 张）
| 项 | 结果 |
|---|---|
| 候选 | **40 张**（17 个薄弱主题，每主题 ≤3；概念定义14/关键事实13/影响传导13） |
| 覆盖 | 深度覆盖（≥3卡/主题）**71%→81%**；单元覆盖 **13.9%→21.9%**（109/498） |
| 安全 | 可追溯 100%、与现有 70 张零重复、pending_review；未改现有卡/清单 |
| 审核 | `portal/static/card_proposals_v3.json` + md 勾选位，等人工 decisions |

## 四、集成验收
| 项 | 结果 |
|---|---|
| 测试 | CN + CO = **10 passed**；P0 修复测试 4 + digest/修订 14 |
| registry | +3 摘要 |
| skill_registry | +2 → **100** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步
1. 学习卡 40 新候选待人工审核（可扩审核页支持 v3 JSON）；
2. prompt 方向：v2 失败，若继续需重新设计（减少指令约束/分步生成）；
3. 三件审批仍待：8091 接线、定时任务、飞书推送。

## 六、交付清单
- CN：`tools/kb_corpus/kb_qa_prompt_v2.py`、`tools/tests/test_kb_qa_prompt_v2.py`、`research/reports/kb_qa_prompt_v2_20261101.{json,md}`
- CO：`tools/kb_corpus/card_proposal_v3.py`、`tools/tests/test_card_proposal_v3.py`、`research/reports/card_proposals_v3_20261101.{json,md}`、`portal/static/card_proposals_v3.json`
- P0：`tools/kb_corpus/learning_cards.py`、`tools/tests/test_learning_cards_preserve.py`（已单独提交）