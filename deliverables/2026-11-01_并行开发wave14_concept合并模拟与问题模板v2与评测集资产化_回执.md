# 并行开发 wave14 · concept合并模拟 / 问题模板v2 / 评测集资产化 · 回执

> 时间：2026-11-01 09:55–10:10；3 条支线并行（AN/AO/AP），Lead 集成+提交。

## 一、AN concept 合并影响模拟（副本，不动真实数据）
| 项 | 结果 |
|---|---|
| 模拟 | 按 wave13 AL alias_map 合并 2 组重复单元（保留主单元、剔除被合并单元，仅内存/临时副本） |
| 收益（alias 重映射口径） | **@1 30→31（Δ+1）、@5 32→32（Δ+0）** |
| 风险 | 不做 alias 重映射（merged_raw 口径）：**@5 -1**（问题库丢失被剔除来源→假 miss） |
| 结论 | **有条件可合并**：需人工确认（跨研报 claim 全同）+ 备份 + 更新问题库引用 + 审批；本工具不执行真实合并 |

## 二、AO 问答问题模板 v2
| 项 | 结果 |
|---|---|
| 规模 | **50 条**（7 类模板均衡：是什么/影响如何/竞争格局/关键数据/风险限制/如何识别/驱动因素） |
| 检索自检 | **@1 96%（48/50）、@5 100%**（v1 93.75% → +2.25pp） |
| 可答率抽验 | **33%（2/6）**：成功 2、降级 0、无依据 4 → 根因=单条单元信息量不足，非检索失败 |
| 建议 | 扩抽验到 10–12 条；生成阶段加 claim 覆盖度过滤；关键数据/风险类措辞贴近原文 |

## 三、AP 冻结评测集 v2 资产化
| 项 | 结果 |
|---|---|
| 资产 | `eval_set_20261101_v2.json`（89 查询、graded 标注、content_hash=sha256）+ manifest.json |
| 版本 | version 2；语法分布：主题44/AND10/同义词8/OR8/短语6/排除5/短5/长3 |
| 校验 | `verify` 全绿（hash_match=true、规范字段 n≥60/每查询≥1 grade-2/题型覆盖）；freeze 不合规拒绝入库 |
| 后续 | 建议把 `eval_registry verify` 接入 test_gate；评测报告头部带 set_id+hash 保证可追溯 |

## 四、集成验收
| 项 | 结果 |
|---|---|
| 联合测试 | AN + AO + AP = **16 passed** |
| registry | +3 摘要 |
| skill_registry | +3 → **77** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步
1. **concept 合并待你确认**（2 组跨研报重复：东盟 MSME、跨境电商四大挑战）→ 确认后我执行备份+合并+更新引用+重跑评测；
2. 问答：扩抽验到 10–12 条；问题生成加 claim 覆盖度过滤；
3. 评测：下一轮严格按 eval_set v2 资产 + 规范 v1 执行（结果带 set_id/hash）；
4. 学习卡 20 候选仍待勾选；三件审批仍待（8091/定时任务/飞书推送）。

## 六、交付清单
- AN：`tools/kb_corpus/merge_sim.py`、`tools/tests/test_merge_sim.py`、`research/reports/merge_sim_20261101.{json,md}`
- AO：`tools/kb_corpus/kb_qa_bank_v2.py`、`tools/tests/test_kb_qa_bank_v2.py`、`research/reports/kb_qa_questions_v2_20261101.{json,md}`、`portal/static/kb_qa_questions_v2.json`
- AP：`tools/kb_corpus/eval_registry.py`、`tools/tests/test_eval_registry.py`、`research/reports/eval_sets/eval_set_20261101_v2.json`、`research/reports/eval_sets/manifest.json`、`docs/plans/2026-11-01_评测集资产管理_v1.md`