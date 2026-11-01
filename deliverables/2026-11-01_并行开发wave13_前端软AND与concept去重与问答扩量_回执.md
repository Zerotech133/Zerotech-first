# 并行开发 wave13 · 前端软AND / concept去重 / 问答扩量 · 回执

> 时间：2026-11-01 09:50–10:05；3 条支线并行（AK/AL/AM），Lead 集成+提交。

## 一、AK 前端软 AND 同步
| 项 | 结果 |
|---|---|
| 实现 | `kb_reports.html` 内联软 AND：默认 soft（覆盖率≥0.5）+ 同义词权重0.6 + `#and-mode` 切换（strict/any 回退） |
| 一致性 | JS 常量与 `search_v2.py` `DEFAULT_*` 逐一对齐，**测试守护**（后端改默认会触发测试失败） |
| 验证 | **8 passed** + node --check + HTTP 200（32KB）；v1/v2 切换/学习卡/简报/H18 区块保留 |

## 二、AL 同 concept 重复单元审计（只读）
| 项 | 结果 |
|---|---|
| 统计 | 498 单元 / 485 唯一 concept → **重复组 11 个（24 单元）** |
| 可合并 | **2 组**（东盟 MSME、跨境电商四大挑战：跨研报 claim 全同）→ 保留主单元、另一条 alias |
| 仅标注 | 9 组（量学同书不同序言、涨停密码同页不同表述、AI 传导链同书不同章、SNPO 括号变体、评级定义、军工周度、资本市场改革/障碍等） |
| 安全 | **未改知识单元数据**（SHA1 不变）；alias_map 13 条，合并/标注均需人工复核 |
| 用途 | 回答 wave12 AG 的 @1 miss 根因（同 concept 重复单元），为数据层改进提供清单 |

## 三、AM 问答样本扩量
| 项 | 结果 |
|---|---|
| 真实运行 | 请求 12 条 → **成功 6 / 降级 0 / 无依据 6**；平均引用 2.08 条；耗时 97s |
| 合并 | 原 5 + 新 12 = **17 条**（去重、原样本优先）；备份 5 条原样本已存 |
| 无依据原因 | LLM 按 prompt 规则拒答（「证伪条件/数据多少」类问题超出单条单元信息量），**非检索失败**（@5=100%） |
| 验证 | **7 passed**（mock）+ HTTP 200（samples=17）；回滚=用 backup 覆盖 |

## 四、集成验收
| 项 | 结果 |
|---|---|
| 联合测试 | AK 8 + AL 6 + AM 7 = **21 passed** |
| registry | +3 摘要 |
| skill_registry | +2（concept_dedup/kb_qa_expand）→ **74** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步
1. 数据层：按 AL 清单人工复核 2 组可合并单元（合并后重跑评测看 @1 是否提升）；
2. 问答：改写问题模板（补「是什么/影响如何」类）提升成功样本占比；
3. 学习卡：20 候选待人工勾选合并；
4. 仍待审批：8091 问答接线、每日自动化定时任务、飞书推送接线。

## 六、交付清单
- AK：`portal/kb_reports.html`、`tools/tests/test_kb_search_frontend.py`、`docs/plans/2026-11-01_研报检索v2前端灰度_v1.md`
- AL：`tools/kb_corpus/concept_dedup.py`、`tools/tests/test_concept_dedup.py`、`research/reports/concept_dedup_20261101.{json,md}`
- AM：`tools/kb_corpus/kb_qa_expand.py`、`tools/tests/test_kb_qa_expand.py`、`research/reports/kb_qa_samples_expanded_20261101.json`、`research/reports/kb_qa_samples_backup_20261101.json`、`portal/static/kb_qa_samples.json`、`docs/plans/2026-11-01_问答样本扩量_v1.md`