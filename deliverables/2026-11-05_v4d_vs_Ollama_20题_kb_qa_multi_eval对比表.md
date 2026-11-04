# v4d vs Ollama 20 题配对评测（kb_qa_multi_eval 对比表）

日期：2026-11-05
评测脚本：`tools/kb_corpus/kb_qa_multi_eval.py eval`（现成脚本，未改代码）
问题集：120 题评测集分层抽 20 题（4 不可答 + 6 W13 + 5 held-out + 5 citation）
检索：生产 RAG，`retrieve_clusters(k=6)`，每题只检索一次，两模型共用同一批材料
生成：`answer_multi(prompt_version="v4", citation_retry=True)`；v4d 走 `llm_router`（百炼），Ollama 用 `qwen3.5:9b`

> 列映射说明：`kb_qa_multi_eval` 原生是 single vs multi 对比；本次把 **single 列映射为 v4d**（multi-mode 答案 + 从答案标记重建引用元数据），**multi 列映射为 Ollama**（multi-mode 答案）。表内指标由脚本原样计算，未人工改数。

## 一、汇总对比表

| 指标 | v4d | Ollama | 差值（Ollama−v4d） |
|---|---:|---:|---:|
| 可答率（脚本口径） | 55.0% | 60.0% | +5.0pp |
| 平均引用数 | 0.90 | 1.20 | +0.30 |
| 引用存在率 | 100.0% | 100.0% | 0.0 |
| 支撑率（全样本） | 26.2% | 25.7% | -0.5pp |
| 支撑率（有答案） | 47.6% | 42.8% | -4.8pp |
| 幻觉 flag 率 | 5.0% | 5.0% | 0.0 |
| 幻觉样本数 | 1 | 1 | 0 |
| 平均推断标记 | 0.50 | 0.95 | +0.45 |
| 平均答案长度（字） | 128.7 | 133.7 | +5.0 |

**双方均有答案的子集（n=9）**：支撑率 46.3% → 42.6%（Δ=-3.7pp）；平均引用 1.44 → 1.89；幻觉 flag 1 → 0。

## 二、拒答行为拆分（脚本汇总表不含，按逐题数据统计）

| 分组 | v4d | Ollama |
|---|---:|---:|
| 可答题误拒（共 16 题） | 7（43.8%） | 4（25.0%） |
| 不可答题正确拒答（共 4 题） | 2（50.0%） | **4（100%）** |
| 不可答题被错误作答 | 2（U04 世界杯冠军、U10 贝尔不等式） | 0 |

## 三、怎么读这张表

1. **脚本自动结论**：「multi 可答率提升有限，质量对比无显著差异」（stance=keep_retrieval_default）。映射到本次对比，即 v4d 与 Ollama 在这套引用/支撑/幻觉指标上差距不大。
2. **Ollama 覆盖更多可答题**：可答题实际作答 12/16，v4d 只有 9/16；v4d 误拒更多。
3. **v4d 答得更“贴材料”**：只统计真正作答的可答题，v4d 支撑率 46.3%（n=9）略高于 Ollama 42.8%（n=12），但覆盖少 3 题。
4. **v4d 的安全性问题仍在**：4 道不可答题里 v4d 答了 2 道（且带引用），Ollama 4/4 全部干净拒答；这与上一轮同模式 20 题评测的结论一致。
5. **引用存在率 100% 不代表答案正确**：两模型都只引用检索材料里的 unit；v4d 的 2 道不可答题答案引用了无关材料，仍属“可核验引用但答错”。

## 四、结论与建议

- **继续用 Ollama 作为 multi 默认**；**v4d 保留用于 single**。本轮不改生产配置。
- 这套脚本指标下两者质量差异不显著，但拒答安全性（不可答 2/4 vs 4/4）与可答覆盖（9/16 vs 12/16）都偏向 Ollama；v4d 的局部支撑率优势不足以抵消“答错但带引用”的风险。
- 若后续想让 v4d 接手 multi，需要先修「不可答误答」与「多单元引用覆盖」，再用同脚本复评 40 题并做人工盲评；不建议只调 citation_retry。
- 口径限制：single/multi 列是映射关系；v4d 侧有页码校验、Ollama 侧只有 unit_id 存在性校验；单次运行、temperature=0.1、无人工盲评；脚本的“可答率”会把错误作答也计为 answered，不能当准确率看。

## 五、产物

- 脚本原始报告：[kb_qa_multi_eval_20261105.md](research/reports/kb_qa_multi_eval_20261105.md)
- 报告数据：[kb_qa_multi_eval_20261105.json](research/reports/kb_qa_multi_eval_20261105.json)
- 评测输入（可复现）：[v3bank](research/reports/kb_qa_multi_eval_20261105_v3bank.json)、[multi](research/reports/kb_qa_multi_eval_20261105_multi.json)、[idmap](research/reports/kb_qa_multi_eval_20261105_idmap.json)、[rows_meta](research/reports/kb_qa_multi_eval_20261105_rows_meta.json)
- 上一轮同模式对比：[multi_pair_eval_20261105.md](research/reports/multi_pair_eval_20261105.md)
