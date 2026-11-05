# 主审 40 条：18 条分歧样本清单（AI-DRAFT ONLY，非人工审阅）

- 来源：`C:\Users\t\Desktop\206人工抽检_40条_审阅表_v1_旧模板备份_20261104.xlsx`
- SHA256：`7D0C08D07902FC5D53C49D5961617BA2031657AD042686AD734816D61C543508`
- 文件时间：2026-11-04T22:28:49.580051
- 标签来源：**WorkBuddy AI 草稿（reviewer=AI-draft）**
- 用途：仅作为人工复核的起点；不得当作人工结论，不得据此切换 canonical 或上线。

## 分歧构成
- 仅 base 正确（RAG 错）：9 条
- 仅 RAG 正确（base 错）：7 条
- 双方都错：2 条
- 合计：18 条

## 清单
| 序 | 类型 | id | category | base对 | RAG对 | winner | cite | 问题摘录 |
|---:|---|---|---|---:|---:|---|---:|---|
| 1 | 仅 base 正确（RAG 错） | holdout-014 | E_variant | 1 | 0 | base | 0 | 实战中「MACD指标的D0轴之上」怎么用？ |
| 2 | 仅 base 正确（RAG 错） | holdout-017 | A_concept_qa | 1 | 0 | base | 0 | 什么是「- 05 最优执行轨迹——冲击成本与」？ |
| 3 | 仅 base 正确（RAG 错） | holdout-018 | E_variant | 1 | 0 | base | 0 | 实战中「最优执行轨迹」怎么用？ |
| 4 | 仅 base 正确（RAG 错） | holdout-019 | E_variant | 1 | 0 | base | 0 | 「最优执行轨迹」的要点是什么？ |
| 5 | 仅 base 正确（RAG 错） | holdout-020 | E_variant | 1 | 0 | base | 0 | 用一句话解释「最优执行轨迹」。 |
| 6 | 仅 base 正确（RAG 错） | holdout-100 | E_variant | 1 | 0 | base | 0 | 用一句话解释「熟悉交易工具和平台」。 |
| 7 | 仅 base 正确（RAG 错） | holdout-122 | A_concept_qa | 1 | 0 | base | 0 | 什么是「机队规模变化」？ |
| 8 | 仅 base 正确（RAG 错） | holdout-143 | unknown | 1 | 0 | base | 0 | 在 macOS 或 Linux 终端里，怎么查看某个端口被哪个进程占用并结束它？请给具体步骤。 |
| 9 | 仅 base 正确（RAG 错） | holdout-153 | unknown | 1 | 0 | base | 0 | 我想系统梳理世界文明起源，可以按哪些具体步骤来做？ |
| 10 | 仅 RAG 正确（base 错） | holdout-021 | A_concept_qa | 0 | 1 | rag | 1 | 什么是「波浪尺」？ |
| 11 | 仅 RAG 正确（base 错） | holdout-023 | A_concept_qa | 0 | 1 | rag | 1 | 什么是「左翼K线」？ |
| 12 | 仅 RAG 正确（base 错） | holdout-024 | E_variant | 0 | 1 | rag | 1 | 「左翼K线」的要点是什么？ |
| 13 | 仅 RAG 正确（base 错） | holdout-049 | C_reading_comprehension | 0 | 1 | rag | 1 | 以横子为例，19085到20684计算得到哪个位置？ |
| 14 | 仅 RAG 正确（base 错） | holdout-066 | C_reading_comprehension | 0 | 1 | rag | 1 | 文中认为判断有效放量应避免哪些问题？ |
| 15 | 仅 RAG 正确（base 错） | holdout-071 | D_multi_source | 0 | 1 | rag | 1 | 「电力设备板块周度表现」在多个来源中如何表述？ |
| 16 | 仅 RAG 正确（base 错） | holdout-078 | A_concept_qa | 0 | 1 | rag | 1 | 什么是「缠论工具」？ |
| 17 | 双方都错 | holdout-022 | A_concept_qa | 0 | 0 | tie | 0 | 什么是「不灵怠」？ |
| 18 | 双方都错 | holdout-034 | B_conclusion_rewrite | 0 | 0 | tie | 1 | 关于「SKDJ和均线指标」，知识库的结论是什么？ |

完整问题、参考答案、base/RAG 回答、RAG 来源和 AI notes 见 CSV/XLSX。

## 复核建议
- `仅 base 正确`：重点看 RAG 是否拒答、答非所问或引错来源；这些是 RAG 的硬失败。
- `仅 RAG 正确`：看 base 是否漏掉参考要点；这些是 RAG 的真实增益。
- `双方都错`：优先核对参考答案本身是否清晰，避免用模糊题判模型。
- 人工复核时请以 `question/reference/base/RAG` 原文为准，不要采信 AI 的 `notes`。

## 边界
- 本文件不是人工审阅结果；`reviewer=AI-draft`。
- 不能从这 18 条推算 206 总体通过率；它们是为分歧优先抽样。
- 未切换 canonical、未上线、未训练 SFT。