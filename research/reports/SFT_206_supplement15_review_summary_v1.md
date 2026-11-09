# 206 补充 15 条审阅汇总（人工审阅）

- 来源：`C:\Users\t\Desktop\206人工抽检_补充15条_审阅表_v1.xlsx`
- 样本：15 条；10 条 `worst_char_regression` + 5 条 `unknown_ood_refusal`
- 自动列：base/RAG char_f1 已带；人工列来自 Excel。
- 审阅人：老田
- 审阅状态：已由用户确认为本人审阅；仍为定向样本，不代表总体估计。

## 汇总数字

- base_correct：**13/15**
- rag_correct：**3/15**
- winner：base 12 / rag 1 / tie 2
- citation_ok：**4/15**（只评 RAG 引用）
- time_sensitive：0/15
- 双方都对：['holdout-156', 'holdout-164']；双方都错：['holdout-040']
- 仅 base 对：['holdout-124', 'holdout-146', 'holdout-074', 'holdout-184', 'holdout-190', 'holdout-168', 'holdout-151', 'holdout-177', 'holdout-149', 'holdout-192', 'holdout-189']
- 仅 RAG 对：['holdout-077']

## 分组

- `unknown_ood_refusal`（n=5）：base 5/5 对，RAG 0/5 对，winner={'base': 5}，citation_ok=0/5。
- `worst_char_regression`（n=10）：base 8/10 对，RAG 3/10 对，winner={'rag': 1, 'base': 7, 'tie': 2}，citation_ok=4/10。

## 关键样例

- **holdout-077**（worst_char_regression，base char 0.3924 / RAG char 0.1631）：base=0，rag=1，winner=rag，citation_ok=1。base 把「缠论工具」说成方法论体系，未答出它是自动标识中枢与买卖点的软件；RAG 准确，但未遵守「一句话」约束
- **holdout-040**（worst_char_regression，base char 0.2303 / RAG char 0.1207）：base=0，rag=0，winner=tie，citation_ok=1。两版均未答出 reference 的「扫破低点后收回形成候选 + 低周期 CISD 收盘确认」；base 作威科夫式推测，RAG 正确辨析 SEPA 的 C2 与缠论分形属不同体系，但同样未命中
- **holdout-156**（worst_char_regression，base char 0.5795 / RAG char 0.4453）：base=1，rag=1，winner=base，citation_ok=1。base 覆盖低点上移、包含关系与「跟随至空头标志」的出场条件；RAG 仅覆盖前两点、遗漏出场条件，但来源标注更细
- **holdout-164**（worst_char_regression，base char 0.6327 / RAG char 0.5248）：base=1，rag=1，winner=tie，citation_ok=1。两者均完整给出排序、报告名与页码，RAG 额外附 PDF 路径，完成度相当

## 解释与边界

- **这是定向最差样本，不是总体估计。** 12/15 的 RAG 失配是选题机制导致的，不能外推为 RAG 全量通过率。
- **holdout-077 显示自动 char_f1 会误判：** 自动判 RAG 回退（base f1 0.3924 > rag 0.1631），人工判 base 错、RAG 对；说明字符重叠不等于事实正确。
- **OOD 过度拒答是主要风险：** 5 条 `unknown_ood_refusal` 中 base 5/5 对、RAG 0/5 对，RAG 均无引用并拒答；包括 SQL、数列、成语翻译、生活类等知识库外问题。
- **citation_ok 不等于回答正确：** holdout-040 双方都错，但 RAG citation_ok=1（引文支撑其辨析，却未答到 reference）。
- 结论仍是不切 canonical；应设计低置信检索→基础模型回退/领域外路由，再做主审 40 和预注册验证。

## 15 条明细

| id | 组 | base_f1 | rag_f1 | base_ok | rag_ok | winner | citation | 备注 |
|---|---|---:|---:|---:|---:|---|---:|---|
| holdout-077 | worst_char_regression | 0.3924 | 0.1631 | 0 | 1 | rag | 1 | base 把「缠论工具」说成方法论体系，未答出它是自动标识中枢与买卖点的软件；RAG 准确，但未遵守「一句话」约束 |
| holdout-124 | worst_char_regression | 0.2991 | 0.0923 | 1 | 0 | base | 0 | RAG 检索到的是股评与科技内容、与航空无关故拒答；base 一句话定义准确，但未给出 reference 的净增 9 架 / 3426 架 / 较上年末 +1.2% 数据 |
| holdout-146 | worst_char_regression | 0.4545 | 0.2686 | 1 | 0 | base | 0 | base 准确引用资料[1]并补充[2]背景、注明资料未给技术定义；RAG 称找不到依据，而资料[1]原文即为依据，属漏检 |
| holdout-074 | worst_char_regression | 0.2178 | 0.0649 | 1 | 0 | base | 0 | base 一句话定义准确；RAG 明确表示无法给出定义，仅列特征，未完成「一句话解释」 |
| holdout-156 | worst_char_regression | 0.5795 | 0.4453 | 1 | 1 | base | 1 | base 覆盖低点上移、包含关系与「跟随至空头标志」的出场条件；RAG 仅覆盖前两点、遗漏出场条件，但来源标注更细 |
| holdout-184 | worst_char_regression | 0.3505 | 0.2165 | 1 | 0 | base | 0 | RAG 因资料为金融内容拒答；base 完整给出差值 3/5/7/9→11→37 与 n²+1 两种解法，答案正确 |
| holdout-190 | worst_char_regression | 0.3339 | 0.2088 | 1 | 0 | base | 0 | RAG 因资料为金融内容拒答；base 给出 SELECT/FROM/WHERE 分步写法与示例，覆盖 reference 全部步骤 |
| holdout-168 | worst_char_regression | 0.3618 | 0.2397 | 1 | 0 | base | 0 | RAG 因资料为量化交易内容拒答；base 正确指出不必强求对应，并给出直译加注、意译、借用习语、保留原词四种处理 |
| holdout-040 | worst_char_regression | 0.2303 | 0.1207 | 0 | 0 | tie | 1 | 两版均未答出 reference 的「扫破低点后收回形成候选 + 低周期 CISD 收盘确认」；base 作威科夫式推测，RAG 正确辨析 SEPA 的 C2 与缠论分形属不同体系，但同样未命中 |
| holdout-164 | worst_char_regression | 0.6327 | 0.5248 | 1 | 1 | tie | 1 | 两者均完整给出排序、报告名与页码，RAG 额外附 PDF 路径，完成度相当 |
| holdout-151 | unknown_ood_refusal | 0.2762 | 0.1746 | 1 | 0 | base | 0 | RAG 因资料无关拒答；base 给出矛盾法入手思路与甲乙丙案例，推理与结论一致 |
| holdout-177 | unknown_ood_refusal | 0.2482 | 0.1466 | 1 | 0 | base | 0 | RAG 因资料为虾滑供应链内容拒答；base 给出先软化后擦拭的思路与小苏打加洗洁精的具体做法 |
| holdout-149 | unknown_ood_refusal | 0.2836 | 0.2024 | 1 | 0 | base | 0 | RAG 因资料为量化交易内容拒答；base 列出只讲过程不讲结果、信息堆砌、只提问题不给方案等误区，并给出结论先行等改法 |
| holdout-192 | unknown_ood_refusal | 0.2029 | 0.1239 | 1 | 0 | base | 0 | RAG 因资料为量化与 AI 工程内容拒答；base 列出背景堆砌、陈词滥调、缺乏冲突等误区并给具体切入点建议 |
| holdout-189 | unknown_ood_refusal | 0.211 | 0.1338 | 1 | 0 | base | 0 | RAG 因资料为股票指标公式内容拒答；base 给出锁白平衡、关 HDR、控曝光、避镜头边缘等要点，与 reference 一致 |

## 一致性检查

- 0/1 字段、winner 与正确性组合、notes 均一致；未发现空白。
