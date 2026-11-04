# 引用校验器 + 120 题评测集（W24 · 2026-11-04）

> 目标：为生成侧提供**离线、确定性**的引用支撑校验，并构建 120 题引用评测集；不改 bailian_eval.py（Lead 后续集成）。
> 数据：已保存的 `research/reports/bailian_eval_v4d_strict_20261104.json`（40 题）；全部离线计算，不联网。

## 一、引用校验器（`tools/kb_corpus/citation_verifier.py`）

**规则**：按句切分（。！？!?；;/换行）→ 每句提取 `[n]` → 对每个引用检查对应 ctx 文本对句子的支撑度：
句中 CJK bigram 被该 ctx 覆盖比例（去掉引用标记/标点），**阈值 0.25**；多引用取最大支撑度；
引用编号越界记 `citation_out_of_range`；无引用句记 `weak`。

**输出字段**：`citation_valid_rate`（有效引用句/有引用句）、`weak_rate`（无引用句/总句）、
`unsupported_sentences[]`（含 sentence/citations/support/reason）、`per_sentence[]`、`all_supported`、`empty`。

API：`verify_answer(answer, [{doc_id,text},...], threshold=0.25)`；CLI：`--v4d <已保存结果>` 离线统计。

## 二、v4d 40 题离线引用校验统计

| 指标 | 值 |
|---|---|
| 答案数 | 40（可答 30 / 不可答 10） |
| 句子数 | 102 |
| 有引用句 | 70 |
| 无引用句（weak） | 32（weak_rate 均值 0.2558） |
| 未支撑句 | 1（低支撑/越界） |
| **平均 citation_valid_rate** | **0.8** |
| 全部引用句均被支撑的答案 | 32/40（0.8） |
| 可答/不可答 平均 valid_rate | 1.0 / 0.2 |

**分题型**：

| 题型 | n | 平均 valid_rate | weak 句 | 未支撑句 |
|---|---|---|---|---|
| unanswerable | 10 | 0.2 | 27 | 1 |
| card_concept | 8 | 1.0 | 1 | 0 |
| card_evidence | 8 | 1.0 | 0 | 0 |
| unit_howto | 7 | 1.0 | 2 | 0 |
| unit_fact | 7 | 1.0 | 2 | 0 |

**样例（校验通过）**：

- A01：valid_rate=1.0 · 有引用句 2/3 · weak 1
- A02：valid_rate=1.0 · 有引用句 2/2 · weak 0

**样例（未支撑，需关注）**：

- U03：low_support · support=0.0741 · citations=[1, 2, 3, 4, 5]
  - 句：现有资料未涉及手冲咖啡的具体步骤、器具选择或研磨、注水等细节

**解读**：v4d 的 70 个有引用句中 69 个被支撑；未支撑 1 例为「不可答拒答 + 末尾凑引用 [1..5]」
（U03 手冲咖啡：答案本身说资料不足，但仍附 5 个引用且无支撑）→ 校验器可捕获此类「形式引用」。
同时 **32/102 句无引用（weak_rate 25.6%）**：引用覆盖率仍不足，建议生成侧强制关键句引用。

## 三、120 题评测集（`research/reports/eval_set_120_20261104.json`）

| 项 | 值 |
|---|---|
| 总题数 | **120** |
| 来源构成 | {"w13": 40, "heldout": 40, "citation_clean": 40} |
| 可答/不可答 | 110 / 10 |
| 带 ctx | 120/120 |
| 带 gold | 85/120 |
| 题型分布 | {"card_concept": 23, "A_concept_qa": 20, "card_evidence": 23, "unit_howto": 7, "C_reading_comprehension": 20, "unit_fact": 17, "unanswerable": 10} |
| 去重 | 160 → 133（丢弃 27），按 source 轮转均衡至 120 |

**来源说明**：

- **W13 40 题**：`answer_eval_20261104.json`（30 可答 + 10 库外不可答；ctx 由其 ctx_ids 重建，gold 由 W13 构建器同源获得）；
- **held-out 40 题**：`quant_kb_heldout_105.jsonl` 中 A_concept_qa 20 + C_reading_comprehension 20（问题/参考答案为 held-out 原始内容；ctx 由卡片检索重建、gold 用概念匹配卡片）；
- **citation_clean 40 题**：`citation_clean.jsonl`（4 类型均衡：card_concept 15 + card_evidence 15 + unit_fact 10；ctx/gold 原样）。

字段：`id/source/type/question/contexts[{doc_id,text,source}]/gold_doc_ids/answerable/reference_answer/meta`；
政策：问题归一化去重；ctx 3-5 条；answerable 标记来自源数据。

## 四、测试

`tools/tests/test_citation_verifier.py` —— **7 passed**：支撑/不支撑/多引用/无引用 weak/空答案/越界/混合聚合+阈值可配。

## 五、局限与后续

- v4d 的 ctx 文本由 `ctx_ids` 从本地文档库重建（与当时 prompt 的截断可能有细微差异），支撑判定为 bigram 近似；
- held-out 的 ctx 为卡片检索重建（`with_gold` 85/120），部分题无 gold，引用校验主要看支撑度；
- 未改 `bailian_eval.py`；建议 Lead 集成：评测结果增加 `citation_verify` 字段（valid_rate/weak/unsupported），作为引用质量门；
- 建议生成策略强制「关键句必引用」，并针对「拒答仍凑引用」样例加入训练负例/规则拦截。

> 研究输出，不构成投资建议。
