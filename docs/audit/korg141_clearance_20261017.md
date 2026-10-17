# K-ORG-141 · 僵尸单清场报告（2026-10-17）

> 依据《老田的AI助手研发方案与研发计划_2026-10-17.md》P0-3 执行；清场动作全部 append-only 留痕（TaskStore 快照行 / devtask aborted 记录），需求不丢、痕迹可溯。

## 一、TaskStore 月度账本（tasks_202610.jsonl）· 6 张 open 单 cancel

均为 A0 于 10-08/09 开出、history 仅 open、9-10 天零流转的积压待办。处置：**cancel**（by=A0，closure 注明转 devtask 执行队列重立项跟踪，需求不丢）。

| 单号 | 级别 | 标题 | 处置结论 |
|---|---|---|---|
| T-20261008-004 | P2 | jev 事件库接入 N1（数据源 v2） | 积压待办，转 devtask 队列 |
| T-20261008-005 | P1 | 两线合流 S2-1~S2-5 执行 | 积压待办（k_stock 未退役，未完成），转队列 |
| T-20261008-006 | P2 | llm.py 负载守卫移植（k_stock 经验） | 积压待办，转队列 |
| T-20261009-008 | P2 | 双刀信号接入 T1/T2 第三事实源 | 部分已落地（quant_double_knife 已移植），转队列 |
| T-20261009-009 | P1 | 双刀 strict/relaxed 分 regime 真实历史回测 | **高价值真待办**（5596 只特征湖），优先重立项 |
| T-20261009-010 | P2 | 双刀 Regime 分层器与 RiskGuard 参数互校 | 积压待办，转队列 |

## 二、devtask 账本（.worktrees/tasks.json）· 10 张死记录 drop

核验标准：分支 × worktree × 提交三重交叉（对齐 K-STOCK-371 清场判例）；**全部 10 张的分支/worktree 均已不存在**，为纯账本僵尸记录。

| 单号 | 原状态 | 处置结论（abort_reason 摘要） |
|---|---|---|
| K-PUSH-003 | review | 空数据单（branch/worktree/desc 全空），开单未落地 |
| K-PUSH-004 | review | 空数据单，同上 |
| K-ORG-073 | doing | 催办单目的已达成——K-STOCK-211 实质已由 K-STOCK-372 收口（33 提交 merge·36 绿，见项目记忆），仅状态未翻转 |
| K-STOCK-211 | doing | 同上，被 372 收口的滞后记录 |
| K-STOCK-374 | doing | 分支已不存在，仅残留 worktree 目录（已 prune/清理）；frpc B 方案 M1 如需实施请重开 |
| legion-1017004821 | doing | legion 端到端验证单，痕迹全失 |
| K-PUSH-001 | review | 有工作描述但痕迹已失无法核验，推送中台修复如需落地请重开走守卫 |
| K-STOCK-412 | review | 飞书 bot 性能修复单，痕迹已失无法核验 |
| K-STOCK-413 | review | 飞书 bot 停机修复单，痕迹已失无法核验 |
| K-STOCK-414 | review | 全项目四路审计单，结论已由 2026-10-17 复盘吸收，如需继续请重开 |

## 三、清场过程新发现与修复

1. **devtask cmd_drop 对空单崩溃（新真 bug）**：`cmd_drop` 直接索引 `t["worktree"]`，K-PUSH-003/004 这类 branch/worktree 键缺失的单会 `KeyError` 崩溃、drop 不生效（本单修复：`t.get()` 防御，分支/worktree 缺失时跳过清理直接落账）。
2. **K-STOCK-374 残留目录**：分支消失但 `.worktrees/K-STOCK-374` 目录残留，`git worktree prune` + 回收站清理。

## 四、清场后状态

- TaskStore：10 distinct 单全部闭环（4 done + 6 cancelled）✓
- devtask：193 单 → 无 doing/review 僵尸（K-ORG-141 本身随本报告 merge 闭环）✓
- 建议重开队列：T-20261009-009（双刀真实回测，P1）优先；双刀互校 / jev 接入 / llm 守卫按需重立项。

*报告完。清场动作均由 A0 执行并 append-only 留痕。*
