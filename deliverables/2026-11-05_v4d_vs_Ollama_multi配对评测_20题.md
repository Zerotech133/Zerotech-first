# v4d vs Ollama multi 配对评测（20 题）— 决策摘要

日期：2026-11-05
结论：**保持 Ollama 作为 multi 生产默认；v4d 继续只用于 single。**

## 一、评测方法

- 问题：从 `research/reports/eval_set_120_20261104.json` 分层抽 20 题 —— 4 不可答 + 6 W13 可答 + 5 held-out + 5 citation 可答。
- 检索：生产 RAG `kb_qa_multi.retrieve_clusters(question, k=6)`，每组问题只检索一次，两个模型使用**完全相同**的 clusters。
- 生成：`answer_multi(..., prompt_version="v4", citation_retry=True)`；v4d 走 `llm_router`（百炼），Ollama 走本地 `qwen3.5:9b`。
- 指标：拒答、引用数/有效引用、`kb_qa_guard.compute_guard_metrics`（citation_missing、insufficient_cited_units、inference_heavy、citation_invalid）、时延、成本。
- 自动偏好规则：不可答题拒答 +3；可答题非拒答 +2、守卫 PASS +2、≥2 有效引用 +1、幻觉引用 -2；降级/错误 -2。

## 二、核心结果

| 指标 | v4d（百炼） | Ollama（qwen3.5:9b） |
|---|---:|---:|
| 拒答率（可答 16 题，越低越好） | 38%（6/16） | **25%（4/16）** |
| 拒答率（不可答 4 题，越高越好） | 50%（2/4） | **100%（4/4）** |
| 质量守卫通过率（可答 16 题） | 25%（4/16） | **69%（11/16）** |
| 平均引用数 | 1.00 | **1.25** |
| 平均答案字数 | 145 | 184 |
| 平均耗时 | **5.4s** | 6.8s |
| citation_retry 触发 | 16/20 | 1/20 |
| 成本（20 题） | ¥0.0960 | 0 |

自动偏好：**v4d 1 / Ollama 10 / 平 9**。

## 三、关键发现

1. **不可答题误答是 v4d 的硬伤**：4 题里 2 题用无关研报事实作答（世界杯冠军 → 公司营收/地产板块；贝尔不等式 → AI4S 湿实验），且带“可核验引用”。这类“有引用但答错”比直接拒答更危险。
2. **v4d multi 的引用能力不足**：可答题守卫通过率仅 25%，多数答案只有 1 个引用单元，无法满足 multi 的「≥2 个不同单元」；citation_retry 触发 16/20 仍无改善，说明是生成能力问题，不是提示问题。
3. **拒答判定失准**：v4d 对可答题更常误拒（6/16），同时对不可答题又更常误答（2/4），两个方向都偏。
4. **时延优势缩小**：v4d 5.4s vs Ollama 6.8s，仅快约 20%；3 题样本时“快 3–7 倍”不代表 20 题分布。
5. **9 个平局里有 4 个是双方都拒答了可答题**，实际质量差距比 1:10 的比分更大。

## 四、决策与后续

- **不切回 v4d multi**，生产保持 `KB_QA_LLM_PROVIDER=ollama`。
- v4d 保留在 single 路径（strict prompt 下 citation 中位 0.90），用于需要引用准确率的单轮问答。
- 若要再评估 v4d multi：先补「多单元引用」训练样本或调整 decoding/prompt 结构，而不是继续加 citation_retry；再跑 40 题配对 + 人工盲评。
- 本次未做任何生产配置变更。

## 五、产物

- 完整逐题报告：[multi_pair_eval_20261105.md](research/reports/multi_pair_eval_20261105.md)
- 原始数据：[multi_pair_eval_20261105.json](research/reports/multi_pair_eval_20261105.json)
- 评测脚本：[tools/kb_corpus/multi_pair_eval.py](tools/kb_corpus/multi_pair_eval.py)
- 测试：[tools/tests/test_multi_pair_eval.py](tools/tests/test_multi_pair_eval.py)

> 注：自动偏好/守卫是工程启发式，不是人工质量结论；建议对 10 题做人工盲评复核。
