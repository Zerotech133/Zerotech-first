# 主审 40：AI 基线 + 4 处人工修正（汇总）

> **性质：不是完整人工审阅。** 40 条中仅 holdout-017/019/020/122 共 4 条经老田修正；其余 36 条沿用 AI 判定。

- AI 基线源：`C:\项目\老田的AI助手\research\reports\SFT_206_main40_AI_draft_summary_v1.json`（`review_status=AI_DRAFT_ONLY`）
- 人工修正 4 条来源：`C:\项目\老田的AI助手\research\reports\SFT_206_main40_disagreements_18_corrected_summary_v1.json`（14 条沿用 AI）
- 用户确认：`2=错`；1=对；0=错。

## 主审 40 调整前后
| 指标 | AI 基线 | 4 处修正后 |
|---|---:|---:|
| base 正确 | 31/40 | 27/40 |
| RAG 正确 | 29/40 | 31/40 |
| both_correct | 22 | 22 |
| base_only | 9 | 5 |
| rag_only | 7 | 9 |
| both_wrong | 2 | 4 |
| winner base/rag/tie | 14/21/5 | 10/23/7 |

## 4 处修正明细
| id | AI base/RAG | 备份值 | 修正后 base/RAG | 最终 winner |
|---|---|---|---|---|
| holdout-017 | 1/0 | 0/2 | 错/错 | tie |
| holdout-019 | 1/0 | 2/0 | 错/错 | tie |
| holdout-020 | 1/0 | 2/1 | 错/对 | rag |
| holdout-122 | 1/0 | 0/1 | 错/对 | rag |

## 18 条分歧样本
- AI 基线：base 9/18、RAG 7/18；cross {'base_only': 9, 'rag_only': 7, 'both_wrong': 2}。
- 修正后：base 5/18、RAG 9/18；cross both_correct 0 / base_only 5 / rag_only 9 / both_wrong 4；winner base 5 / rag 9 / tie 4。
- 4 条修正均来自 AI 的 base_only：017/019 变成 both_wrong；020/122 翻成 rag_only。

## 边界
- 主审 40 只有 4 条是人工修正；其余 36 条沿用 AI 判定，不是完整人工审阅。
- 4 条修正来自 18 条 AI 分歧样本，存在选择偏差；不能外推 206，也不能据此切 canonical 或上线。
- citation_ok 未逐条人工复核，仍是 AI 判定。
- winner 是质量偏好，不是正确性判定；tie 包括双方都对/都错等情况。