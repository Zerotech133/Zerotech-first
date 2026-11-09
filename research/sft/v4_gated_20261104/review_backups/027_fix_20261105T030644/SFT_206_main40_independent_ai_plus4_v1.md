# 主审 40：36 条独立 AI 复核 + 4 条人工修正

> **性质：不是完整人工审阅。** 36 条为独立 AI 复核；4 条为老田人工修正。`citation_ok` / `time_sensitive` 未人工逐条复核。

- 独立复核源：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\main40_independent_review_36.json`
- 独立复核 SHA256：`6D6E1755079CB59754473A40B762C912C4C10A618B1131A9CF00A1B64B84F2C5`
- 人工修正源：`C:\项目\老田的AI助手\research\reports\SFT_206_main40_disagreements_18_corrected_summary_v1.json`

## 主审 40 汇总
| 指标 | 36 条 AI-independent | 4 条人工修正 | 40 条合并 |
|---|---:|---:|---:|
| base 正确 | 20/35 | 0/4 | 20/39 |
| RAG 正确 | 27/35 | 2/4 | 29/39 |
| both_correct | 15 | 0 | 15 |
| base_only | 5 | 0 | 5 |
| rag_only | 12 | 2 | 14 |
| both_wrong | 3 | 2 | 5 |
| uncertain | 1 | 0 | 1 |
| winner base | 5 | 0 | 5 |
| winner rag | 12 | 2 | 14 |
| winner tie | 18 | 2 | 20 |
| winner uncertain | 1 | 0 | 1 |
| citation_ok=1 | 29 | 0 | 29 |
| citation_ok=0 | 0 | 0 | 0 |
| citation_unknown | 7 | 4 | 11 |

- 36 条中 `holdout-027` 的参考文本是上证指数行情描述，与“均线粘合”问题不匹配，无法判定，已记 uncertain；因此 correctness 已知数为 35/36，40 条合并为 39/40。

## 36 条独立复核 vs 旧 AI 草稿（仅同一批 36 条，供差异检查）
| 指标 | 旧 AI 草稿 | 独立 AI | 一致/不一致/未知 |
|---|---:|---:|---:|
| base 正确 | 27/36 | 20/35 | 29/6/1 |
| RAG 正确 | 29/36 | 27/35 | 34/1/1 |
| winner base/rag/tie | 10/21/5 | 5/12/18 | 21/14/1 |

## 关键差异

- **base 由对改错 6 条**：
  - `holdout-001/002/003/004`：条件交易单风险；旧 AI 判 base 对，独立复核认为 base 缺少参考要求的资金/操作/成本/系统/市场等项，RAG 覆盖六项。
  - `holdout-051`：Riskfolio-Lib 组合优化功能；base 只泛述风险平价，缺 26 种风险度量/HRP-NCO/Black-Litterman 等关键项。
  - `holdout-117`：客座率；base 只讲定义，缺参考中的 86.30%、86.19% 等具体数据。
- **RAG 由对改错 1 条**：
  - `holdout-071`：电力设备周度表现；RAG 的 0.04%/0.11pct 数值对，但把领涨子行业写成风电 2.77%，与参考“光伏 3.86% 居前”矛盾。
- **无法判定 1 条**：
  - `holdout-027`：参考文本是上证指数 3930/3850 点行情描述，与“均线粘合”问题不匹配；base/RAG 均记 null，winner=uncertain。该条应先修数据/参考，不计入准确率。
- **winner 差异 14 条**：多数是旧 AI 在双方都正确时给了 base/rag 偏好，独立复核按规则记 tie；不是新增事实错误。正确性层面的分歧是上面的 6 条 base + 1 条 RAG + 1 条 uncertain。

## 4 条人工修正（已确认）
| id | base | RAG | winner |
|---|---|---|---|
| holdout-017 | 0 | 0 | tie |
| holdout-019 | 0 | 0 | tie |
| holdout-020 | 0 | 1 | rag |
| holdout-122 | 0 | 1 | rag |

## 解释与边界
- 36 条是独立 AI 复核，不是人工审阅；4 条是老田人工修正，但 citation/time_sensitive 未人工判定。
- 40 条仅 4 条人工修正，不能称为完整人工审阅。
- 36 条独立复核与旧 AI 标签是同一模型家族的不同运行，不是独立人工/外部模型验证。
- 样本来自 AI 分歧相关的主审集，不能外推 206，不能切 canonical 或上线。
- `citation_ok`/`time_sensitive` 的空白不等于 0；是未判定。
- 若需要正式采用，需人工复核 36 条或至少复核独立 AI 与旧 AI 的差异项。