# multi 答案质量评估（single vs multi：引用/支撑/幻觉）

> **本次运行说明（2026-11-05）**：本报告由 `tools/kb_corpus/kb_qa_multi_eval.py eval` 原样生成，输入是 20 题 v4d/Ollama 配对答案。脚本的 **single 列 = v4d**（multi-mode 答案，引用元数据由答案标记重建），**multi 列 = Ollama**（multi-mode 答案），两模型使用同一批生产检索 clusters（k=6）。因此下文标题/结论里的 single/multi 语义应读作 v4d/Ollama；脚本本身未改。
>
> 输入：`research/reports/kb_qa_multi_eval_20261105_{v3bank,multi,idmap,rows_meta}.json`。

- 生成：2026-11-05T02:45:19+08:00；输入答案由 20 题配对生成（v4d 走 llm_router，Ollama 走 qwen3.5:9b），本脚本只做规则评估、不调 LLM；multi 列无 citation 页码元数据（页码判定 n/a，仅校验 unit_id 存在性）
- 有效子集：20 对（v4d vs Ollama；single/multi 列映射）；复用：kb_qa_eval_v2.eval_sample_v2（引用/支撑/幻觉 v2）+ kb_qa_schema_v2 schema v2

## 汇总对比

| 指标 | single | multi | Δ |
|---|---|---|---|
| 可答率 | 55.0% | 60.0% | 5.0% |
| 平均引用数 | 0.9 | 1.2 | 0.3 |
| 引用存在率 | 100.0% | 100.0% | 0.0% |
| 支撑率（全样本） | 26.2% | 25.7% | -0.5% |
| 支撑率（有答案） | 47.6% | 42.8% | -4.8% |
| 幻觉 flag 率 | 5.0% | 5.0% | 0.0% |
| 幻觉样本数 | 1 | 1 | None |
| 平均推断标记 | 0.5 | 0.95 | None |
| 平均答案长度 | 128.7 | 133.7 | 5.0 |

## 配对子集（双方均有答案）

- n=9；支撑率 single 0.463 → multi 0.4259（Δ=-0.037）
- 幻觉 flag 数 single 1 → multi 0；平均引用 1.444 → 1.889

## 逐条

| id | single 状态 | multi 状态 | single 支撑率 | multi 支撑率 | single 引用 | multi 引用 | single 幻觉 | multi 幻觉 |
|---|---|---|---|---|---|---|---|---|
| w13_U01 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |
| w13_U04 | inference | refusal | 57.1% | — | 3 | 0 | 0 | 0 |
| w13_U07 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |
| w13_U10 | inference | refusal | 50.0% | — | 2 | 0 | 0 | 0 |
| w13_A01 | fact | inference | 50.0% | 50.0% | 1 | 2 | 0 | 0 |
| w13_A07 | inference | inference | 40.0% | 40.0% | 1 | 1 | 1 | 0 |
| w13_A13 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |
| w13_A18 | refusal | inference | — | 57.1% | 0 | 3 | 0 | 0 |
| w13_A24 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |
| w13_A30 | fact | inference | 50.0% | 42.9% | 1 | 2 | 0 | 0 |
| heldout_001 | inference | inference | 50.0% | 60.0% | 2 | 2 | 0 | 0 |
| heldout_011 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |
| heldout_021 | refusal | inference | — | 33.3% | 0 | 2 | 0 | 1 |
| heldout_030 | refusal | inference | — | 40.0% | 0 | 2 | 0 | 0 |
| heldout_040 | inference | inference | 33.3% | 33.3% | 1 | 2 | 0 | 0 |
| citation_sft_0003 | fact | inference | 50.0% | 57.1% | 1 | 2 | 0 | 0 |
| citation_sft_0026 | inference | inference | 60.0% | 40.0% | 2 | 2 | 0 | 0 |
| citation_sft_0013 | inference | inference | 33.3% | 40.0% | 2 | 2 | 0 | 0 |
| citation_sft_0039 | inference | inference | 50.0% | 20.0% | 2 | 2 | 0 | 0 |
| citation_sft_0178 | refusal | refusal | — | — | 0 | 0 | 0 | 0 |

## 结论：multi 是否牺牲质量换可答率？

**multi 可答率提升有限，质量对比无显著差异**（stance=keep_retrieval_default）

- 可答率 single 0.55 → multi 0.6（Δ=+0.0500）
- 支撑率（全样本）single 0.2619 → multi 0.2569（Δ=-0.0050）
- 幻觉 flag 率 single 0.05 → multi 0.05（Δ=+0.0000）
- 引用存在率 single 1.0 → multi 1.0
- 双方均有答案子集（n=9）支撑率 Δ=-0.037

### 口径与局限

- multi 引用元数据（页码）未持久化，multi 侧页码校验 n/a，引用只验证 unit_id 存在性；
- multi citation 从答案标记抽取，可能漏计未打标记的引用；
- 支撑度为字符 bigram 覆盖率，长答案（multi 平均更长）天然更易命中，需谨慎解读；
- 有效样本 20 对（v4d/Ollama 配对），结论为冒烟级；
- 不调 LLM、不改答案原文；评估为规则启发式。
