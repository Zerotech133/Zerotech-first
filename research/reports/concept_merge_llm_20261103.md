# 长尾概念 LLM 语义归并（W1-2c，2026-11-03）

- 生成：2026-11-03T07:07:20+08:00 ｜ v1.0.0 ｜ 只读输入：**True**
- 输入：`C:\项目\老田的AI助手\research\reports\knowledge_multisource_20261103.json`
- 向量：fallback_lexical（模型 None，0 条，0.0s，降级原因：['OllamaError: batch: TimeoutError: timed out; single: TimeoutError: timed out']）
- 边界判定：已跳过· 调用 0 次，失败 0
- 阈值：余弦 ≥0.85 直接合并；0.75~0.85 交 LLM 判定（最多 40 对）

## 一、核心结果（是否达标）

| 指标 | W1-2b 基线 | W1-2c 后 |
|---|---:|---:|
| 概念簇数 | 1029 | **1029** |
| 多源确认数（concept_synonyms 口径） | 23 | **23** |
| 新增确认多源 | — | 0 |
| 合并组 / 合并成员 | — | 0 / 0 |
| 向量自动合并 / LLM 接受 | — | 0 对候选 / 0 接收 |

> **结论：多源确认 23 < 30，❌ 未达标。**
> 未达标根因：单源长尾簇的**来源高度分散且语义重复有限**——多数簇的语义近邻来自同一来源（合并后仍是单源），或向量在字符层面差异大（尤其 fallback 向量）；要再提升需扩大输入源（书/研报/更多博主）或人工标注种子对。

## 二、合并组清单（按成员数）

（无 LLM/向量合并组）

## 三、合并映射（可回滚）

- 共 **0** 条映射（原簇 → 目标簇，含方法/相似度/理由）；
- 回滚方式：忽略 `mapping` 即恢复 W1-2 的 1029 簇基线（原 concept_synonyms 输出不变）。

## 四、误并风险清单

（无高风险合并组）

## 五、口径与局限

1. 语义合并仅在单源长尾簇内进行；多源簇不参与二次合并（避免稀释既有证据）。
2. bge-m3 不可用时回退字符 n-gram 向量（embedding.source=fallback_lexical），召回弱、需复核；LLM 连续失败会熔断，边界对一律不合并（保守）。
3. 多源确认数按 concept_synonyms 口径（独立 (source_type,source_key) ≥2）重算；合并后概念本身的答案一致性/冲突未重算，置信仍为命名层面。
4. 映射表可回滚：忽略 mapping 记录即恢复 W1-2 的 1029 簇基线。
5. 只读输入，不改学习卡/units/治理；本次未真实调 LLM 的部分以 mock/降级路径标注。

## 六、复现

```powershell
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/concept_merge_llm.py
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/tests/test_concept_merge_llm.py
```
