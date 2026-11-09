> **2026-11-05 修正说明：** 本报告是 027 修复前的历史 206 评测，仍包含 `holdout-027`（reference 跨行错配，已按同源原文修复）。**不得把本报告数字当作“027 已排除”的准确率。** 修复版 eval 副本：`research/sft/v4_gated_20261104/holdout_eval_027_reference_fixed_v1.jsonl`；正式 206 结论如需使用，应排除 027 重算。

# SFT holdout 206 · base vs 生产 KB v2 RAG

- 日期：2026-11-04
- 范围：holdout 206; base qwen3.5:9b vs production KB v2 RAG top4; same questions/references
- 自动指标只衡量字面重合/数字命中，不等同于事实正确、引用可靠或可上线。
- 人工抽检仍待完成：自动列已回填，判断列必须由人填写。

## 1. 总体

| 指标 | base | RAG | 说明 |
|---|---:|---:|---|
| n / ok / errors | 206 / 206 / 0 | 206 / 206 / 0 | 全量 206 |
| char_f1 | 0.1886 | 0.2257 | 字面重合，主观察指标 |
| bigram_f1 | 0.0738 | 0.1104 | 局部语序/短语重合 |
| number_hit | 0.1936 | 0.2240 | 数字命中，非事实核验 |
| 平均延迟 s | 31.87 | 29.46 | RAG 多检索与上下文 |

## 2. Paired 对比（同一题）

| 指标 | base -> RAG | Δ 95% bootstrap CI | 胜/负/平 |
|---|---:|---:|---:|
| char_f1 | 0.1886 -> 0.2257 | +0.0371 (+0.0227~+0.0524) | 135/71/0 |
| bigram_f1 | 0.0738 -> 0.1104 | +0.0366 (+0.0223~+0.0522) | 117/89/0 |
| number_hit | 0.1936 -> 0.2240 | +0.0305 (-0.0092~+0.0742) | 26/23/157 |

## 3. 解释边界

- `char_f1` 上升不等于正确；资料检索可能带来“更像答案”的表述，也可能漏答/错引。
- 本报告不判定 RAG 可上线；先看 paired 方向、失败样本和人工抽检。
- 若 95% CI 跨 0 或人工抽检不支持，则不因平均分上升切 canonical。

## 4. 人工抽检

- 自动回填：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\manual_review_40_auto_filled.csv`
- 审阅稿：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\manual_review_40_auto_filled.md`
- 行数：40；人工列是否仍为空：True
- 必填：`base_correct(0/1)`、`rag_correct(0/1)`、`winner(base/rag/tie)`、`citation_ok(0/1)`、`reviewer`、`notes`；不得由自动脚本代填。

## 5. 最大回退/提升样本（按 char_f1）

| 类型 | id | char_f1 base -> RAG | Δ |
|---|---|---:|---:|
| 回退 | holdout-077 | 0.3924 -> 0.1631 | -0.2293 |
| 回退 | holdout-124 | 0.2991 -> 0.0923 | -0.2068 |
| 回退 | holdout-146 | 0.4545 -> 0.2686 | -0.1859 |
| 回退 | holdout-074 | 0.2178 -> 0.0649 | -0.1529 |
| 回退 | holdout-156 | 0.5795 -> 0.4453 | -0.1342 |
| 提升 | holdout-114 | 0.2179 -> 0.6744 | +0.4565 |
| 提升 | holdout-123 | 0.0948 -> 0.4936 | +0.3988 |
| 提升 | holdout-061 | 0.0966 -> 0.4901 | +0.3935 |
| 提升 | holdout-119 | 0.1783 -> 0.5625 | +0.3842 |
| 提升 | holdout-096 | 0.2767 -> 0.6216 | +0.3449 |

## 6. 结论与下一步

- **不自动切 canonical**；生产索引仍为 `rag_index_corpus.json`，候选为 `rag_index_corpus_v2_20261104.json`。
- 人工抽检 40 条完成并汇总后，才讨论是否提交 canonical 切换；SFT 仍等 GPU，仅作为 RAG 补充。
- 所有升级需另立预注册、OOS/成本/容量门和人工审批。

## 7. 产物

- 机器结果：`C:\项目\老田的AI助手\research\reports\SFT_206_base_vs_RAG_v1.json`
- base：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\holdout_base_206.jsonl`
- RAG：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\rag_kb_206.jsonl`
- 人工审阅稿：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\manual_review_40_auto_filled.md`

## holdout-027 排除后的 206 敏感性（n=205）

> 这是修正后的敏感性口径；**不是**原 n=206 报告的替换，原报告仍保留 027。

- 样本：205（原 206 排除 `holdout-027`）；base/RAG 均无 error 行。
- base：char 0.1889 / bigram 0.0741 / number 0.1933。
- RAG：char 0.2261 / bigram 0.1109 / number 0.2239。
- Δ（RAG-base）：char +0.0372、bigram +0.0368、number +0.0306。
- paired char：W/L/T 134/71/0；bootstrap 95% CI +0.0223 ~ +0.0527。
- 结论方向与原 206 一致：char/bigram 有平均提升，number 仍未显示统计显著性；不因此切 canonical。
- 明细：`C:\项目\老田的AI助手\research\reports\SFT_206_base_vs_RAG_excl027_sensitivity_v1.json`
