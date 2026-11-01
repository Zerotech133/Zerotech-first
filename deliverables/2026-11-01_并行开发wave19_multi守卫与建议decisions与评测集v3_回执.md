# 并行开发 wave19 · multi质量守卫 / 学习卡建议decisions / 评测集v3 · 回执

> 时间：2026-11-01 10:50–11:05；3 条支线并行（BE/BF/BG），Lead 集成+提交。

## 一、BE multi 质量守卫（P0 落地）
| 指标 | guard 前（raw multi） | guard 后（fallback） |
|---|---|---|
| 可答率 | 67%（8/12） | **50%（6/12）** |
| 引用可解析率 | 100%（20/20） | **100%（28/28）** |
| 支撑率 | 100% | **100%** |
| 幻觉率 | 0% | **0%** |
| guard 拦截 | — | **6 条**（回退可核验摘录） |
- 拦截原因：insufficient_cited_units 6 / citation_missing 4 / inference_heavy 4；
- **结论**：guard 恢复/保持质量（引用可解析 100%、幻觉 0%），代价可答率 -16.7pp；建议默认 `on_guard_fail=fallback` 并接入 `kb_qa(mode=multi)` 出口；对回退答案需人工抽查信息量。

## 二、BF 学习卡建议 decisions（未 apply）
| 项 | 结果 |
|---|---|
| 建议分布 | pass 36 / revise 24 / reject 0（与 BC 预审一致） |
| dry-run | 更新 60 卡、**approved 36 → 合并新增 36**（总 56）、冲突 0、合并校验通过 |
| 安全 | 未写 checklist/proposals/学习卡（SHA256 不变）；等人工确认后 `--apply` |

## 三、BG 评测集 v3（补盲区）+ 合并复评
| 项 | 结果 |
|---|---|
| v3 资产 | n=**102**（v2 89 + 新增 13），hash `6b778e2c…`，verify 全绿；merged-concept 盲区 2 组→0 |
| 合并复评（v3 102） | 全量 P@5 0.7549→**0.7451（-0.0098）**、NDCG -0.0064 |
| 新增 13 条 | P@5 0.9231→**0.8462（-7.7pp）**、NDCG -5.0pp；受影响 5 负 1 正 |
- **关键结论**：wave15「0 变化=neutral」是评测盲区假阴性；真实覆盖后**简单去重方案非正向** → 不建议真实合并；若合并需先做内容合并（claim/evidence 并入主单元）或 alias 检索，再用 v3 复评。

## 四、集成验收
| 项 | 结果 |
|---|---|
| 联合测试 | BE 5 + BF 5 + BG 4 = **14 passed** |
| registry | +3 摘要 |
| skill_registry | +2 → **91** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步
1. **guard 接入出口**：把 kb_qa_guard 接入 kb_qa.ask(mode=multi) 返回（默认 fallback），并对回退样本人工抽查；
2. 学习卡：人工确认建议 decisions 后 `card_review.py decide --apply` → `--apply --merge`（需审批）；
3. 评测基线切到 v3（102 查询，hash 已记录）；concept 合并改走内容合并/alias 检索方案；
4. 仍待审批：8091 问答接线（multi 灰度预接线就绪）、每日自动化定时任务、飞书推送接线。

## 六、交付清单
- BE：`tools/kb_corpus/kb_qa_guard.py`、`tools/tests/test_kb_qa_guard.py`、`research/reports/kb_qa_guard_20261101.{json,md}`
- BF：`research/reports/card_review_decisions_suggested_20261101.json`、`research/reports/card_review_suggested_preview_20261101.{json,md}`、`tools/tests/test_card_decisions_suggested.py`
- BG：`tools/kb_corpus/eval_set_v3.py`、`tools/tests/test_eval_v3_merge.py`、`research/reports/eval_sets/eval_set_20261101_v3.json`、`research/reports/eval_sets/manifest.json`、`research/reports/merge_sim_v3_20261101.{json,md}`、`research/reports/eval_set_v3_20261101.md`