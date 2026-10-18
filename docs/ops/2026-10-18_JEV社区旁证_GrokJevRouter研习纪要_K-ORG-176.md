# JEV 社区旁证研习纪要：Grok Bot + Jev Router（K-ORG-176）

> 日期：2026-10-18 · 来源：视频号「学霸妈妈讲AI」第三条软文（今日发布）提及的
> 「Grok Bot + Jev 组合」→ 独立核实两个开源实现 → 与零界 JEV 对照收编。
> 本纪要用于存档旁证与差距点，不改变生产策略。

## 一、视频话术审计（延续既有三级标注）

| 视频话术 | 核实结果 | 定性 |
|---|---|---|
| "Grok Bot + Jev 组合" | 真实存在：colinmcdermott/grok-jev-router、Bodila51/grok-bot-jev 均可访问 | ✅ 真概念 |
| "Jev 语义路由决策" | 真实：Jev 分类 → 代码裁决 → 执行方执行 | ✅ 真模式 |
| "极速动作响应" | 夸大：Jev 只判定不执行；53.8s→4.1s 组复用缓存，作者自标 caveat | ⚠️ 夸大 |
| "分布式控制中枢" | 不存在：dependency-free 单机 Python 工具 | ✗ 包装 |
| "毫秒级向量检索" | 无关：链路无向量库 | ✗ 缝合 |
| "自愈调度" | 不存在：真实的是 shadow→active + review.py 周报 + kill switch | ✗ 包装 |
| "零样本分类" | Jev 尚可但无公开可复现验证（madewithjev 明示） | ⚠️ 半真 |

**未核实的数字（归档标注 [unverified]，不得入框架文档）**：
- "实测路由 154ms vs 860ms"——两仓库 README 与 ab_results.md 均无此数字；实测为 536~580ms/次
- "61.4% 零样本分类率"——搜索零命中
- "七动作"实为 Bodila51 仓的动作集；colinmcdermott 仓为 4 动作 + bypass（轻微混并）

## 二、核心设计拆解（colinmcdermott/grok-jev-router/jev/questions.py）

"The one file to review"——判定问题与阈值全部集中于此：

- 问题类型三类：`choice`（lane）、`score`（risk 四档分值）、`noul`（reversible/external/
  necessary/done/looping 校准概率）
- `THRESHOLDS` 字典：9 个阈值集中带注释
- `decide()` 优先级链：done → external → risk_critical → irreversible → looping →
  lane_confidence 不足 → 兜底 ask_human；硬规则覆盖模型选择的 lane
- `signals` 全量落盘（每个中间概率都进返回体）
- `meter`：ms / tokens / cost / model 版本

## 三、与零界 JEV 对照 & 可抄点

我们的 JEV 跑在同一个 TypeSafe 平台（systemone，model=jev-latest），问题 schema 同构，
学习成本为零。

| 优先级 | 可抄点 | 落地 | 状态 |
|---|---|---|---|
| A1 | 概率全量落盘（分布熵 > 单一 conf） | bias_probs 全量存 jev JSON | ✅ K-ORG-176 已落地 |
| A2 | meter 计量（ms/model/src） | 云/本地判定各带 meter | ✅ K-ORG-176 已落地 |
| B1 | decide() 式集中阈值表 + 硬规则→ask_human | E4 分歧哨兵 / 复核队列参考实现 | ◻ E4 前置 |
| B2 | record + review 分歧率周报 | E4 跨模型分歧哨兵直接对齐 | ◻ E4 |
| C | 路由动作集（领域不同，不抄） | — | — |

## 四、旁证结论

mu（35 判定点）、grok-jev-router（4 动作+阈值）、yz-substrate（26 点）三组独立实现
收敛到同一「判定点 + 级联 + veto + shadow」架构；router 类项目均卡在"无后验验证 +
无台账审计"，正是 P5 回流零件 + P4 账本的差异化空间。本单仅落地 A1/A2 零成本项。

## 五、关联工单

- K-ORG-176（本单）：A1 概率全量 + A2 meter
- E4（K-ORG-170 计划号）：分歧哨兵将消费 bias_probs
