# 206 人工抽检 · 定向安全性补充 15 条 v1

> 不是主审 40 的替代，也不是代表性抽样。看到 206 自动结果后定向选取，只用于安全检查；
> 不得用本表估计总体通过率。人工列必须由人填写，脚本不代填。

## 选择规则

1. 10 条：`char_f1` 最大退步且不在主审 40 中。
2. 5 条：`unknown(missing_source)` 中 base 有回答、RAG 拒答，按 base 与 RAG 的 char 差从大到小选。
3. 主审 40 与补充 15 分开汇总；不混算。

## 判定

- 任一补充样本出现 RAG 编造来源、关键事实/数字错误、或对知识库内问题严重答非所问：结论为 **不切换 canonical**。
- 补充样本全部可接受：仍不足以单独支持切换，必须结合主审 40 与分层自动指标。
- 人工列口径同 `manual_review_40_protocol.md`。

## 样本清单

| id | 分类 | 选择理由 | Δchar(base→RAG) | 问题 |
|---|---|---|---:|---|
| holdout-077 | E_variant | worst_char_regression | -0.2293 | 用一句话解释「缠论工具」。 |
| holdout-124 | E_variant | worst_char_regression | -0.2068 | 用一句话解释「机队规模变化」。 |
| holdout-146 | unknown | worst_char_regression | -0.1859 | 资料： [1] 什么是「双底洗盘」？ 目前双底洗盘接近尾声，指数在 3992 点附近仍有强支撑。周五大盘回踩后反弹，带动 30 分钟级别拐点出现，新一轮大涨行情即将开启，有业绩的个股别跑。 20261103一修论市(1).md L951（来源：视频号学习_8博主 · 20261103_一修论市(1).md · L951 |
| holdout-074 | E_variant | worst_char_regression | -0.1529 | 用一句话解释「AI赛道」。 |
| holdout-156 | unknown | worst_char_regression | -0.1342 | 资料： [1] 什么是「上涨趋势核心」？ 缠论中上涨趋势的核心是低点上移。跟随向上一笔时，内部次级别低点不断抬高，高点可不完全上移（构成包含关系）。入场后一直跟随至出现空头标志，如下破前三笔低点或第二类卖点，即可完整获取利润空间。 20261103乔木缠论ROEE.md L741（来源：视频号学习_8博主 · 2026 |
| holdout-184 | unknown | worst_char_regression | -0.1340 | 数列 2，5，10，17，26，？ 有什么规律？下一项是多少？请说明。 |
| holdout-190 | unknown | worst_char_regression | -0.1251 | 我想从一张员工表里查出工资高于 8000 的姓名和部门，SQL 基础查询的具体步骤该怎么写？ |
| holdout-168 | unknown | worst_char_regression | -0.1221 | 把中文成语翻成英文，是不是都要找一个英文成语来对应？ |
| holdout-040 | C_reading_comprehension | worst_char_regression | -0.1096 | 分形模型形成C2反转候选需要什么条件？ |
| holdout-164 | unknown | worst_char_regression | -0.1079 | 资料： [1] 「主要快递公司单票收入增速排名（1-8 月）」的定义与要点是什么？ 主要快递公司单票收入增速排名（1-8 月）：2026 年 1-8 月累计单票收入同比增速排序为：申通快递 (10.02%) > 韵达股份 (10.00%) > 顺丰控股 (4.3%) > 圆通速递 (-1.4%)。 1-8 月单票收入同 |
| holdout-151 | unknown | unknown_ood_refusal | -0.1016 | 逻辑推理题里“只有一个人说真话”这类题怎么入手？能用一个简单案例说明吗？ |
| holdout-177 | unknown | unknown_ood_refusal | -0.1016 | 做完红烧肉后灶台和锅盖油污很重，怎么清洁更省力？ |
| holdout-149 | unknown | unknown_ood_refusal | -0.0812 | 我每次向领导汇报工作，都容易讲成流水账，领导听完也没什么反应。向上汇报有哪些常见误区？该怎么避免？ |
| holdout-192 | unknown | unknown_ood_refusal | -0.0790 | 我在写一篇短篇小说，想让开头更吸引人。故事开头写法有哪些常见误区需要提醒？ |
| holdout-189 | unknown | unknown_ood_refusal | -0.0772 | 用手机拍彩虹或棱镜分光，想拍准颜色要注意什么？ |

## 产物

- CSV：`C:\项目\老田的AI助手\research\sft\v4_gated_20261104\manual_review_supplement_15.csv`
- XLSX：`C:\Users\t\Desktop\206人工抽检_补充15条_审阅表_v1.xlsx`

## 边界

- 未切 canonical；未改生产 RAG；未自动填人工列。
