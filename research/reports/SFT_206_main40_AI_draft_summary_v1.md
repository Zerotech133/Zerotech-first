> **2026-11-05 修正说明：** `holdout-027` 的 reference 是跨行错配。本报告是修正前的 AI-draft 40 条历史口径，base31/40、RAG29/40 **包含无效的 027**，不再作为准确率使用。排除 027 后 AI-draft 口径为 base30/39、RAG28/39；权威修正版见 `SFT_206_main40_independent_ai_plus4_v1.md`（36 条独立 AI + 4 条人工，027 已排除）。

# 主审 40 条汇总（AI-DRAFT ONLY，非人工审阅）

- 源文件：`C:\Users\t\Desktop\206人工抽检_40条_审阅表_v1_旧模板备份_20261104.xlsx`
- 源文件 SHA256：`7D0C08D07902FC5D53C49D5961617BA2031657AD042686AD734816D61C543508`
- 文件时间：2026-11-04T22:28:49.580051
- 审阅状态：**AI 草稿（reviewer 全部为 AI-draft）**
- 禁止用途：不得作为人工审阅结果；不得据此切换 canonical；不得上线/进影子盘。

## 总体
- 样本数：40
- base 正确：31/40（77.5%）
- RAG 正确：29/40（72.5%）
- 引用可追溯：30/40（75.0%）
- winner：base 14 / rag 21 / tie 5

## 交叉结果
- both_correct: 22
- base_only: 9
- rag_only: 7
- both_wrong: 2
- base-only IDs：holdout-014, holdout-017, holdout-018, holdout-019, holdout-020, holdout-100, holdout-122, holdout-143, holdout-153
- rag-only IDs：holdout-021, holdout-023, holdout-024, holdout-049, holdout-066, holdout-071, holdout-078
- both-wrong IDs：holdout-022, holdout-034

## 按 category
| category | n | base 对 | RAG 对 | base-only | rag-only | both-wrong |
|---|---:|---:|---:|---:|---:|---:|
| A_concept_qa | 12 | 8 | 9 | 2 | 3 | 1 |
| B_conclusion_rewrite | 3 | 2 | 2 | 0 | 0 | 1 |
| C_reading_comprehension | 3 | 1 | 3 | 0 | 2 | 0 |
| D_multi_source | 1 | 0 | 1 | 0 | 1 | 0 |
| E_variant | 18 | 17 | 13 | 5 | 1 | 0 |
| unknown | 3 | 3 | 1 | 2 | 0 | 0 |

## winner 与正确性交叉
winner 是质量偏好，不是正确性判定；双方都对时仍可分出偏好。

| 结果 | n | winner base | winner rag | winner tie |
|---|---:|---:|---:|---:|
| both_correct | 22 | 5 | 14 | 3 |
| base_only | 9 | 9 | 0 | 0 |
| rag_only | 7 | 0 | 7 | 0 |
| both_wrong | 2 | 0 | 0 | 2 |

## 引用
- citation_ok=0 IDs：holdout-014, holdout-017, holdout-018, holdout-019, holdout-020, holdout-022, holdout-100, holdout-122, holdout-143, holdout-153
- RAG 判对但 citation=0 IDs：无
- RAG 判错但 citation=1 IDs：holdout-034
- 本样本中所有 RAG 判对样本都有可追溯引用；RAG 判错样本中仅 holdout-034 引用为 1。

## 边界
- 这是 AI 根据自动指标和参考答案生成的草稿，不是老田的人工判断。
- 样本来自主审 40 的 AI 抽取，不能代表 206 总体。
- 在本文件明确升级为人工审阅前，不得作为 canonical 切换依据。
