# 多源口径统一裁决（2026-11-04，W5 canonical v1）

> 目标：冻结「多源概念」唯一口径，解决导出 **28** vs KPI **29** 分歧。结论：**冻结 count = 29 / 30（差 1，未达标）**；导出 28 为旧口径少计 1 条 legacy 组，KPI 29 与 canonical 一致。

## 一、三种（实为五种）口径盘点

| 口径 | 数值 | 来源 | 性质 | 是否入选 |
|---|---:|---|---|---|
| v2 人工双签 | **27** | `concept_merge_review_b_20261103.json` | 旧管线（v2 输入集）人工口径 | ❌ 被 v4+legacy 口径取代（历史指标保留） |
| 导出 v2 | **28** | `export_governed_v2_20261104.md`（v4 同义词簇 ∩ v4 多源 + v2 已批组） | 下游导出实算 | ⚠️ 少计 legacy 组 1 条 → 改为读 canonical |
| v4 自动组级 | **29** | `concept_merge_llm_20261103_v4.json` stats.multi_after_confirmed | 语义合并自动（无人工签核） | ❌ 过程指标，不是知识结论 |
| v4 概念级人工签核 | **28** | 同上 adjudication（自动 29 − 驳回 1：房地产/计算机 不同对象） | 人工签核 | ✅ 组成部分 |
| v4_llm 自动 | **30** | `concept_merge_llm_20261103_v4_llm.json`（LLM 边界判定，未人工签核） | 自动（观察队列） | ❌ 未签核，不进 KPI |
| KPI/P1 并集 | **29** | `multi_source_final_p1_20261103.json` | 人工签核并集（v4 基线+已批+原材料组） | ✅ 与 canonical 一致 |
| **canonical（冻结）** | **29** | `multi_source_canonical_20261104.json` | **唯一口径** | ✅ |

## 二、冻结定义（canonical v1）

1. **单位=概念级**：同一概念的同义/子集/语义合并组视为 1 个概念；组级数只作过程指标。
2. **独立来源 ≥2**：来源 = (source_type, source_key)；同博主/同书/同研报只算 1 源。
3. **人工签核**：规则/同义归并的确定性多源概念免逐条签核；**语义/LLM 合并组必须人工签核**。
4. **partial/子集**：按合并组计 1 概念（成员不重复计数）。
5. **驳回组不计**：如 `房地产行业板块表现 / 计算机行业板块表现`（不同对象）。
6. **仅自动未签核组不计**：v4_llm 自动 30 中新增组进入观察队列，签核后按版本 +N。
7. **legacy 携带**：历史人工已批、当前管线未复现的组可携带（须成员存在且来源≥2，标 `legacy_signed`）。

**count 构成（29）** = v4 规则基线 **19** + v4 语义合并人工签核 **9** + legacy 人工签核 **1**（`主要原材料价格波动`，v2 已批、v4 输入替换后未复现）= **29**。

> legacy 白名单仅「原材料组」1 条：其余 v2 多源组（均线战法/一根K线拉起/贵金属金银/Meta Muse 等）已被 v4 签核覆盖或依赖已排除的「指标说明书」来源 → 不计数，记入 canonical `excluded`（14 条可审计）。

## 三、裁决理由（为什么是 29 不是 28/30）

- **28 缺 1**：导出只从 v4 产物聚合，未携带「原材料组」这条历史人工已批概念（`multi_source_final_p1` 的并集口径含它）→ 导出应改读 canonical（count=29）。
- **30 虚 1**：v4_llm 自动 30 新增组未人工签核（其中含边界合并低字符重合组），按定义不计；签核后另行 +N。
- **27 旧 2**：v2 管线输入集已被 v4 取代（第二期 effective 化、老木 units 纳入），保留为历史指标。
- 结论：**29 是唯一同时满足「概念级去重 + ≥2 独立来源 + 人工签核」的可复现口径**。

## 四、影响面与下游改动

| 系统 | 影响 | 处理 |
|---|---|---|
| 导出（`export_governed_data.py`） | 旧口径 **28** → canonical **29** | 本批提供 canonical 指针；导出脚本读取源切换为下一步（**不在本任务写范围**，`multi_source_canonical_20261104.json` 已登记 consumers） |
| KPI（`data_governance_overview.py`） | 原硬编码 29 来源 `multi_source_final_p1` | ✅ 已改为读 canonical（count/target/auto_not_counted 动态），KPI 显示 29（canonical 冻结口径；机器 30 未签核） |
| 训练数据矩阵（`training_data_matrix.py` V17） | 原 `multi_source_final_p1` | ✅ V17 改为读 canonical → 输出 `29 / 30` |
| 微调数据/结论库 | 多源标记应使用 canonical 概念清单与来源 | 读取 `multi_source_canonical_20261104.json#concepts`（29 条，含 sources/sign_off/group） |
| Docs/报表 | 禁止再用 27/28/30 旁口径 | 以本报告 + canonical JSON 为准 |

## 五、产物与测试

- canonical：[`research/reports/multi_source_canonical_20261104.json`](C:/项目/老田的AI助手/research/reports/multi_source_canonical_20261104.json)（definition/count=29/concepts 29 条/excluded 14 条/auto_not_counted/source_artifacts sha256/reconciliation/consumers）
- 工具：[`tools/kb_corpus/multi_source_canonical.py`](C:/项目/老田的AI助手/tools/kb_corpus/multi_source_canonical.py)（`build_canonical` / `canonical_count` / `load_canonical` / `self_check`；`--write`）
- 消费端：`training_data_matrix.py#V17`、`data_governance_overview.py#multi_source` 已切换读 canonical
- 测试：[`tools/tests/test_multi_source_canonical.py`](C:/项目/老田的AI助手/tools/tests/test_multi_source_canonical.py)（5 tests：schema/冻结 count、构成自洽 19+9+1=29、驳回与噪声排除、重建稳定与来源 sha、消费端与导出口径对照一致）

复现：
```powershell
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/multi_source_canonical.py --write
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/tests/test_multi_source_canonical.py
```

## 六、未达标与下一步
- **29/30，差 1**（未达标）：与 P1 结论一致，当前无新资料下不做注水式 +1（唯一候选「房地产/计算机」为错误合并，已驳回）。
- 达 30 的合法路径：①对新资料做人工签核后按版本 +N（canonical 支持 incremental 更新）；②对观察队列中 v4_llm 自动组人工复核（预计 1–2 组可过）；③每季度复核 legacy 白名单（当前仅原材料组）。
- 本批不 commit；只写范围内 6 个文件。
