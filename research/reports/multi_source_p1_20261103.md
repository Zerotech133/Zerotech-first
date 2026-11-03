# P1「多源补 3」扩输入重算 + 人工签核（2026-11-03）

> 目标：把人工签核多源概念从 **27/30** 提升到 **≥30**。本批为 W1-2c 之后的**数据扩充重算**（v4）：新增老木 units、一期 v2 候选、第二期 effective 候选（排除指标说明书），重跑 `knowledge_multisource` → `concept_synonyms` → `concept_merge_llm`（本机 ollama `bge-m3` 语义归并），并按既有标准人工签核新增合并组。

## 一、结论（是否达标）

| 指标 | 结果 |
|---|---|
| v4 规则+同义归并后多源确认（concept_synonyms 口径） | **19** |
| v4 bge-m3 语义归并后自动确认多源 | **29**（新增 10 组） |
| 人工签核：接受 / 驳回 | **9 / 1** |
| **人工签核后多源数** | **28 / 30 → ❌ 未达标，差距 2** |
| 对比 v2 人工双签 | 27 → 28（+1） |

**差距 2 的根因**（按影响排序）：
1. **第二期输入由全量 869 → effective 46**（按要求排除指标说明书类噪声）：v2 中依赖「指标说明书」等来源的多源概念（如 `均线战法`、`- 注意`）在 v4 不再成立，基线净损失；
2. **bge-m3 自动合并新增 10 个多源组，其中 1 组驳回**（`房地产行业板块表现` vs `计算机行业板块表现` = 不同对象，按标准不可并），故 +9 而非 +10；
3. **0.75–0.85 边界对（952 对）未做 LLM 判定**：qwen3.5:9b 显存被其它会话占用，本次只做 bge-m3 自动合并（`--no-llm`）；开启 LLM 边界判定预计可再补 1–2 个多源组，是最接近达成 30 的路径；
4. 老木 units 286 条中仅 135 条可映射到既有概念（190 条记录，其余为日记/无概念命中），对多源增量贡献有限。

## 二、v4 输入（全部只读，sha256 前后校验不变）

| # | 输入 | 条数 | 说明 |
|---|---|---:|---|
| 1 | `portal/static/kb_units_all.json` | 516 | 知识单元（书/研报/策略经典） |
| 2 | `<TEMP>/laomu_units_mapped_v4.json` | 190 | **新增**：老木 units 286 条按既有概念词表映射派生（135 单元命中；source 身份与 laomu 候选同一解析口径，避免假多源） |
| 3 | `portal/static/kb_learning_cards.json` | 400 | 学习卡 |
| 4 | `research/reports/card_candidates_视频号学习_8博主_v2_20261103.json` | 772 | **新增**：一期 v2 候选（8 博主，effective 后） |
| 5 | `research/reports/card_candidates_laomu_notes_20261102.json` | 202 | 老木候选（既有） |
| 6 | `research/reports/card_candidates_视频号学习_第二期_effective_20261103.json` | 46 | **新增**：第二期 effective（原 869 → 排除 823 条指标说明书类，保留 46） |

## 三、三段流水线结果（v4）

### 1) knowledge_multisource v4
- 读入记录 1734 条（去重后）→ 概念 **1646** 个；规则口径多源 **9**（高 4 / 中 5）；待仲裁冲突 0。
- 输出：`knowledge_multisource_20261103_v4.json/md`（`read_only_verified=true`）。

### 2) concept_synonyms v4（规则/同义/包含归并）
- 1646 → **1596** 簇（噪声剔除 15，合并簇 20，覆盖 43 概念）；多源 9 → **19 确认**（0 待确认）。

### 3) concept_merge_llm v4（本地 bge-m3 语义归并）
- 向量：ollama `bge-m3`（batch，GPU 可用，整体 58s，无失败）；边界 LLM 判定本次跳过（`--no-llm`，qwen3.5:9b 显存被占用）。
- 1596 → **1487** 簇；自动候选对 171（余弦 ≥0.85），合并组 **74**（覆盖 109 概念）；多源 19 → **29 自动确认**（新增 10）。
- 输出：`concept_merge_llm_20261103_v4.json/md`（含可回滚 mapping 88 条 + 传递 21 条；`adjudication` 段）。

## 四、人工签核表（新增多源合并组，标准：同义/子集可并；序数/方向/不同对象驳回）

| 合并组 | 结论 | 理由 |
|---|---|---|
| AI 量化 / AI 量化的深度 / 量化 | ✅ 可并（子集） | 同一主题上位写法+子集；来源跨博主 |
| Meta AI 智能体 Muse / Meta个人智能体Muse / Muse 智能体 | ✅ 可并（同义） | 同一产品名的三种截断表述 |
| 出现一个新的同级别中枢 / 又形成一个同级别中枢 | ✅ 可并（同义） | 同一走势描述的同义改写 |
| 分形结构 / 底分形结构 | ✅ 可并（子集） | 子集关系（按 v2 已批口径） |
| 国防军工子板块分化 / 国防军工子板块表现 | ✅ 可并（同义） | 同对象、措辞不同 |
| 开盘半小时快速跳水 / 开盘直接大跳水 | ✅ 可并（同义） | 同一盘面现象 |
| **房地产行业板块表现 / 计算机行业板块表现** | **❌ 驳回** | **不同对象（房地产 vs 计算机），按标准不可并** |
| 技术指标 / 计算技术指标 | ✅ 可并（子集） | 同对象+动作修饰 |
| 纺织品出口增长 / 纺织品出口趋势 | ✅ 可并（同义） | 同对象同义表述 |
| 贵金属 / 贵金属金银 | ✅ 可并（子集） | 子集关系（按 v2 已批口径） |

**签核计算**：19（规则口径）+ 9（接受的新增多源组）= **28**；驳回 1 组不计数。

## 五、口径与局限
1. 多源口径沿用 `concept_synonyms`：独立来源 = (source_type, source_key)，同一博主/书/研报只算 1 源，合并簇已知来源去重后 ≥2 即确认多源；人工签核只裁决「新增合并组」是否成立。
2. v4 为**全量重跑**（输入集合变化），签核数 28 与 v2 人工双签 27 不可逐条对齐；v2 已签组在 v4 中部分因输入替换而消失（指标说明书类来源被排除），属预期。
3. `laomu_units_mapped_v4.json` 为**临时映射中间件**（%TEMP%），映射规则=「老木单元 claim 命中既有概念词表（长词优先、最多 2 命中、过滤日记/口播噪声）」；非仓库交付物，不影响只读输入。
4. bge-m3 语义合并阈值 0.85，边界 0.75–0.85 未判定（LLM 占用）；语义合并仍可能误并，已由人工签核驳回 1 组，其余按同义/子集通过。
5. 未 commit；未改学习卡/units/治理 JSON；本批只写 7 个报告文件。

## 六、下一步（补齐差距 2 的最近路径）
1. **开启 LLM 边界判定**（ollama 空闲时）：`python tools/kb_corpus/concept_merge_llm.py --input research/reports/knowledge_multisource_20261103_v4.json --out/--md ..._v4.json/md`（去掉 `--no-llm`），对 952 个边界对判定，预计再补 1–2 个多源组 → 达 30；
2. 若仍不足：把第二期 effective 中 46 条之外的少量非指标说明书候选（人工白名单）补入输入，或补充第 3 位博主的同主题语料；
3. 对本次签核通过的组补答案一致性/冲突复核（当前签核只裁决命名层面）。

## 七、复现
```powershell
C:\项目\py312\python.exe -X utf8 tools\kb_corpus\knowledge_multisource.py `
  --input units=portal/static/kb_units_all.json `
  --input units=%TEMP%\laomu_units_mapped_v4.json `
  --input cards=portal/static/kb_learning_cards.json `
  --input candidates=research/reports/card_candidates_视频号学习_8博主_v2_20261103.json `
  --input candidates=research/reports/card_candidates_laomu_notes_20261102.json `
  --input candidates=research/reports/card_candidates_视频号学习_第二期_effective_20261103.json `
  --out research/reports/knowledge_multisource_20261103_v4.json --md research/reports/knowledge_multisource_20261103_v4.md
C:\项目\py312\python.exe -X utf8 tools/kb_corpus\concept_synonyms.py `
  --input research/reports/knowledge_multisource_20261103_v4.json `
  --out research/reports/concept_synonyms_20261103_v4.json --md research/reports/concept_synonyms_20261103_v4.md
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/concept_merge_llm.py `
  --input research/reports/knowledge_multisource_20261103_v4.json `
  --out research/reports/concept_merge_llm_20261103_v4.json --md research/reports/concept_merge_llm_20261103_v4.md --no-llm
```
