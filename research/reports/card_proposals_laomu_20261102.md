# 老木候选卡预审报告（v1）

> 生成：2026-11-02T17:32:36+08:00｜候选 203｜基线卡 144

| 结果 | 数量 |
|---|---|
| ✅ pass（可合并） | **28** |
| 🔧 revise（需修改） | 0 |
| ❌ reject（驳回） | 175 |
| 清洗掉 Markdown 噪声的 concept | 127 |

## 拒绝/修改原因分布

| 原因 | 次数 |
|---|---|
| concept_no_domain_term | 115 |
| concept_filler_word | 37 |
| concept_action_phrase | 28 |
| concept_sentence_like | 23 |
| concept_too_short | 19 |
| concept_meta_or_person | 16 |
| concept_non_noun_ending | 12 |
| concept_generic_or_header | 11 |
| answer_too_short | 9 |
| concept_sentence_punct | 7 |
| concept_question_like | 5 |
| concept_no_cjk | 4 |
| concept_sentence_ending | 3 |
| concept_broken_phrase | 1 |
| duplicate_existing_concept | 1 |

## 通过样例（前 12）

- AI 量化（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 天才交易员的十大风控防线（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 突破幅度（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 收盘位置（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 成交量（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 大周期压力（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 流动性（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 识别假突破（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 均线向上（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 关键位突破（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 成交量配合（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）
- 多周期一致性（laomu_notes · 20261102_老木创业笔记_交易老木29条视频文案汇编.md · L）

## 驳回样例（前 12）

- 两类视频的区别：concept_meta_or_person,concept_no_domain_term
- 月 500 万美刀交易量的 AI 量：concept_sentence_like
- 我还会公开这个平台的所有收入和支出：concept_sentence_like,concept_filler_word,concept_meta_or_person
- 我是老木，一个做 AI 量化系统的技术员：concept_sentence_like,concept_filler_word,concept_meta_or_person,concept_sentence_punct
- 看这张图：concept_action_phrase,concept_filler_word,concept_no_domain_term
- 说得直接一点：concept_action_phrase,concept_filler_word,concept_no_domain_term
- 那你可能会说：concept_filler_word,concept_meta_or_person,concept_no_domain_term
- 再给大家看一个数据：concept_non_noun_ending,concept_filler_word,concept_meta_or_person
- 平台能不能做大做强：concept_question_like
- 我是老木，这是我的 AI 量化技术员创业：concept_sentence_like,concept_filler_word,concept_meta_or_person,concept_sentence_punct
- 素材为 5 月实盘录屏：concept_meta_or_person
- 今天给大家展示一段 AI 教科书级别的实：concept_sentence_like,concept_filler_word,concept_meta_or_person

> 本报告仅为机器预审；通过的卡由 precheck 规则判定，仍需 DATA1/老田抽审确认后执行 card_merge --apply。