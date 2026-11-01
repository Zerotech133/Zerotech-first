# 并行开发 wave17 · 多单元A/B / 样本schema v2 / 学习卡审核工作流 · 回执

> 时间：2026-11-01 10:25–10:40；3 条支线并行（AW/AX/AY），Lead 集成+提交。

## 一、AW 多单元聚合扩样 A/B（n=30）
| 指标 | single | multi | Δ |
|---|---|---|---|
| 可答率 | 33.3%（10/30） | **80%（24/30）** | **+46.7pp** |
| 成功/拒答/降级 | 10/20/0 | 24/6/0 | — |
| 平均引用 | 2.4 | 2.25 | -0.15 |
| 单条耗时 | 8.35s | 8.61s | **1.03×（可接受）** |
- 配对 McNemar：改善 14 / **回退 0** / 都失败 6，**p=0.000122（显著）**；
- **5/5 判据达标 → 建议 multi 设为问答默认模式**（single 作 fallback）；建议先 CLI/HTTP 层加 `--multi` 灰度，再扩 50 条复跑。

## 二、AX 问答样本 schema v2 + 幻觉规则调优
| 项 | 结果 |
|---|---|
| schema v2 | 17 条加 expected_refusal/citations_required/answer_type/schema_version；answer_type=fact13/inference1/refusal3 |
| 幻觉误报 | 5 项 → **2 项（-60%）**：消除列表序号/小整数误报（q1「3」、expanded「1」「2」） |
| 剩余候选 | q3「2030」、q5「LLM」（建议人工复核） |
| 不变指标 | 引用存在率 100%、平均支撑率 66.36% |
| 兼容/回滚 | 字段只增不改；迁移前字节级备份可回滚 |

## 三、AY 学习卡审核工作流
| 项 | 结果 |
|---|---|
| 清单 | **60 条候选**（AE 20 + AV 40）、19 主题、**100% 可追溯** |
| 工具 | `card_review.py`：build → decide（校验 decisions）→ dry-run 预览 → `--apply`/`--merge`（备份+校验） |
| 验证 | demo decisions（pass2/revise1/reject1）→ 预计新增 2、校验通过；非法 decisions exit 1；dry-run 不写盘 |
| 状态 | **未 apply**，等人工审核（☐通过/修改/驳回）后一键合并 |

## 四、集成验收
| 项 | 结果 |
|---|---|
| 联合测试 | AW 6 + AX 6 + AY 6 = **18 passed** |
| registry | +3 摘要 |
| skill_registry | +3 → **86** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步
1. **multi 默认模式落地**：在 CLI/未来 HTTP 层加 `--multi` 灰度（默认 multi、single fallback），扩 50 条复跑；
2. 学习卡：等人工 decisions（60 候选清单已就绪，工具一键合并）；
3. 仍待审批：8091 问答接线、每日自动化定时任务、飞书推送接线。

## 六、交付清单
- AW：`tools/kb_corpus/kb_qa_multi_ab.py`、`tools/tests/test_kb_qa_multi_ab.py`、`research/reports/kb_qa_multi_ab_20261101.{json,md}`
- AX：`tools/kb_corpus/kb_qa_schema_v2.py`、`tools/kb_corpus/kb_qa_eval_v2.py`、`tools/tests/test_kb_qa_eval_v2.py`、`research/reports/kb_qa_eval_v2_20261101.{json,md}`、`research/reports/kb_qa_samples_backup2_20261101.json`、`portal/static/kb_qa_samples.json`、`docs/plans/2026-11-01_问答样本schema_v2.md`
- AY：`tools/kb_corpus/card_review.py`、`tools/tests/test_card_review.py`、`research/reports/card_review_checklist_20261101.{json,md}`、`docs/plans/2026-11-01_学习卡审核工作流.md`