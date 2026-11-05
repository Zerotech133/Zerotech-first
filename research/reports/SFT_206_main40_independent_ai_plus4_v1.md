# 主审 40：36 条独立 AI 复核 + 4 条人工修正（027 已排除）

> **性质：不是完整人工审阅。** 36 条为独立 AI 复核；4 条为老田人工修正。`citation_ok` / `time_sensitive` 未人工逐条复核。  
> **2026-11-05 修订：`holdout-027` reference 错配已修复，并明确排除出准确率分母；本报告不再把 027 计为 uncertain 或计入 40。**

- 独立复核源（027 排除版）：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\main40_independent_review_36_excl027_v1.json`
- 独立复核 SHA256：`480630923A9CE9A4D3C15870227FFD79D07462EA7A81E8200D7DB14E2C13F9B5`
- 修复后的复核包：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\main40_independent_review_packet_36_reference_fixed_v1.json`
- 人工修正源：`C:\项目\老田的AI助手\research\reports\SFT_206_main40_disagreements_18_corrected_summary_v1.json`
- Reference 修复记录：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\holdout_027_reference_fix_v1.json`

## holdout-027：reference 错配与修复

- 原 reference（错误，来自另一行）：
  > 上证指数在突破 3930 点后进入止跌向上预判阶段，需等待回踩确认是否守住 3850 点。30 分钟上涨延续且强于预期，下午回调仅用 1 分钟横盘完成以盘代跌，未出现理想低吸点，操
- 修复后 reference（同源知识库原文）：
  > 均线是使用最广泛的指标。很多人喜欢用均线粘合。作为信号，那均线粘合是什么?简单来说就是，不同周期均线非常接近。成本集中，属于形态信号。那均线粘合指标怎么编呢?
- 来源：`C:\项目\知识库\20_清洗\视频号学习_8博主\clean\20261103_指标公式入门(1).md`，第 271 行，标题 `20260912_2003 均线粘合`；源文件 SHA256 `3E3F3E06C994BBBB3DA61177633983FB5383EC9E665D87E26759606F5BA3F0D3`。
- 处理：只修 reference，不重新判定 base/RAG；`holdout-027` 标记为 `EXCLUDED_REFERENCE_MISMATCH`，**不计入准确率**。冻结的原始评测文件/预测未改；修复版单独另存。

## 主审 40 汇总（027 不计入）
| 指标 | 36 条 AI-independent | 4 条人工修正 | 40 条合并 |
|---|---:|---:|---:|
| 计入准确率 | 35 | 4 | 39 |
| 排除 | 1 | 0 | 1 |
| base 正确 | 20/35 | 0/4 | 20/39 |
| RAG 正确 | 27/35 | 2/4 | 29/39 |
| both_correct | 15 | 0 | 15 |
| base_only | 5 | 0 | 5 |
| rag_only | 12 | 2 | 14 |
| both_wrong | 3 | 2 | 5 |
| excluded | 1 | 0 | 1 |
| winner base | 5 | 0 | 5 |
| winner rag | 12 | 2 | 14 |
| winner tie | 18 | 2 | 20 |
| winner excluded | 1 | 0 | 1 |
| citation_ok=1 | 28 | 0 | 28 |
| citation_ok=0 | 0 | 0 | 0 |
| citation_unknown | 7 | 4 | 11 |
| time_sensitive=1 | 0 | 0 | 0 |
| time_sensitive=0 | 35 | 0 | 35 |
| time_unknown | 0 | 4 | 4 |

- `holdout-027` 不再以 “uncertain” 计入：36 条计入 35、40 条计入 39；它标记为 **excluded**，不按 uncertain/对/错计算。
- 修正后：36 条 base 20/35、RAG 27/35；40 条合并 base 20/39、RAG 29/39。
- AI 判定口径下的比例（仅按计入项，不是人工准确率）：36 条 base 57.1%、RAG 77.1%；40 条合并 base 51.3%、RAG 74.4%。
- 旧 AI 草稿 36 条同为排除 027 后：base 26/35、RAG 28/35，winner base 10 / rag 20 / tie 5。
- 旧 AI 草稿完整 40 条（base31/40、RAG29/40）仍包含 027，**不再作为修正后准确率**。

## 36 条独立复核 vs 旧 AI 草稿（仅同一批 36 条，027 已排除）
| 指标 | 旧 AI 草稿 | 独立 AI | 一致/不一致/排除 |
|---|---:|---:|---:|
| base 正确 | 26/35 | 20/35 | 29/6/1 |
| RAG 正确 | 28/35 | 27/35 | 34/1/1 |
| winner base/rag/tie | 10/20/5 | 5/12/18 | 21/14/1 |

## 关键差异

- **base 由对改错 6 条**：`holdout-001/002/003/004`（条件交易单风险清单缺少资金/操作/成本/系统/市场等项）、`holdout-051`（Riskfolio-Lib 缺关键功能）、`holdout-117`（客座率缺具体数据）。这 6 条与 027 无关。
- **RAG 由对改错 1 条**：`holdout-071`（数值对，但领涨子行业写成风电 2.77%，与参考“光伏 3.86% 居前”矛盾）。
- **027 不再是 “无法判定”**：reference 已修复但**不重判**，明确排除；其旧 AI 标签和旧人工判定均不参与任何准确率。
- **winner 差异 14 条**：多数是旧 AI 在双方都正确时给了偏好，独立复核按规则记 tie；不是新增事实错误。

## 4 条人工修正（已确认，均计入）
| id | base | RAG | winner |
|---|---|---|---|
| holdout-017 | 0 | 0 | tie |
| holdout-019 | 0 | 0 | tie |
| holdout-020 | 0 | 1 | rag |
| holdout-122 | 0 | 1 | rag |

## 解释与边界
- 36 条是独立 AI 复核，不是人工审阅；4 条是老田人工修正，但 citation/time_sensitive 未人工判定。
- 027 只修 reference、不重判；修复后的 reference 可用于后续复核/重跑，但本报告不把它算进准确率。
- 40 条仅 4 条人工修正，不能称为完整人工审阅；36 条独立 AI 与旧 AI 是同一模型家族不同运行，不是外部/人工验证。
- 样本来自 AI 分歧相关的主审集，不能外推 206，不能切 canonical 或上线。
- `citation_ok`/`time_sensitive` 的空白不等于 0；是未判定。
