# multi 答案质量评估（single vs multi：引用/支撑/幻觉）

- 生成：2026-11-01T10:44:57+08:00；不调 LLM：复用 wave15 v3 answer_validation(single) 与 kb_qa_multi comparison(multi) 原文答案；multi 无 citation 页码元数据（页码判定 n/a，仅校验 id 存在性）
- 有效子集：12 对（wave17 AW 30 条仅存统计/无原文，故采用有原文的 12 对）；复用：kb_qa_eval_v2.eval_sample_v2（引用/支撑/幻觉 v2）+ kb_qa_schema_v2 schema v2
- AW 参考（30 条统计，无原文）：{"total": 30, "single": {"success": 10, "rate": 0.3333, "no_evidence": 20, "fallback": 0, "avg_citations": 2.4, "avg_elapsed_seconds": 8.35, "reused_count": 12}, "multi": {"success": 24, "rate": 0.8, "no_evidence": 6, "fallback": 0, "avg_citations": 2.25, "avg_elapsed_seconds": 8.61, "avg_clusters":

## 汇总对比

| 指标 | single | multi | Δ |
|---|---|---|---|
| 可答率 | 25.0% | 75.0% | 50.0% |
| 平均引用数 | 1.25 | 1.667 | 0.417 |
| 引用存在率 | 100.0% | 55.0% | -45.0% |
| 支撑率（全样本） | 18.8% | 19.4% | 0.7% |
| 支撑率（有答案） | 75.0% | 25.9% | -49.1% |
| 幻觉 flag 率 | 50.0% | 66.7% | 16.7% |
| 幻觉样本数 | 5 | 3 | None |
| 平均推断标记 | 0.0 | 1.417 | None |
| 平均答案长度 | 203.4 | 297.1 | 93.7 |

## 配对子集（双方均有答案）

- n=3；支撑率 single 0.75 → multi 0.2667（Δ=-0.4833）
- 幻觉 flag 数 single 0 → multi 0；平均引用 2.333 → 2.333

## 逐条

| id | single 状态 | multi 状态 | single 支撑率 | multi 支撑率 | single 引用 | multi 引用 | single 幻觉 | multi 幻觉 |
|---|---|---|---|---|---|---|---|---|
| q2_b5a0a0e045 | refusal | inference | 0.0% | 20.0% | 0 | 3 | 0 | 0 |
| q2_aa49124184 | refusal | inference | 0.0% | 30.0% | 0 | 1 | 2 | 0 |
| q2_bcbfd79337 | refusal | refusal | 0.0% | 0.0% | 2 | 0 | 1 | 2 |
| q2_a593448ce5 | refusal | inference | 0.0% | 16.7% | 5 | 2 | 0 | 3 |
| q2_3d1abf0240 | refusal | inference | 0.0% | 33.3% | 0 | 2 | 1 | 0 |
| q2_1e95f989df | fact | inference | 100.0% | 20.0% | 3 | 3 | 0 | 0 |
| q2_6076398cc7 | fact | inference | 25.0% | 20.0% | 1 | 2 | 0 | 0 |
| q2_f93ae02b9d | refusal | refusal | 0.0% | 0.0% | 0 | 0 | 0 | 0 |
| q2_ef78939178 | refusal | fact | 0.0% | 20.0% | 0 | 3 | 1 | 3 |
| q2_87eff8d7a3 | refusal | refusal | 0.0% | 0.0% | 0 | 0 | 1 | 0 |
| q2_0d7db24320 | refusal | inference | 0.0% | 33.3% | 1 | 2 | 0 | 0 |
| q2_4f34cbb059 | fact | inference | 100.0% | 40.0% | 3 | 2 | 0 | 0 |

## 结论：multi 是否牺牲质量换可答率？

**multi 以部分答案质量换可答率（支撑率下降或幻觉率上升）**（stance=multi_with_guard）

- 可答率 single 0.25 → multi 0.75（Δ=+0.5000）
- 支撑率（全样本）single 0.1875 → multi 0.1944（Δ=+0.0069）
- 幻觉 flag 率 single 0.5 → multi 0.6667（Δ=+0.1667）
- 引用存在率 single 1.0 → multi 0.55
- 双方均有答案子集（n=3）支撑率 Δ=-0.4833

### 口径与局限

- multi 引用元数据（页码）未持久化，multi 侧页码校验 n/a，引用只验证 unit_id 存在性；
- multi citation 从答案标记抽取，可能漏计未打标记的引用；
- 支撑度为字符 bigram 覆盖率，长答案（multi 平均更长）天然更易命中，需谨慎解读；
- 有效样本 12 对（AW 30 条中仅 12 条有原文可评估），结论为冒烟级；
- 不调 LLM、不改答案原文；评估为规则启发式。
