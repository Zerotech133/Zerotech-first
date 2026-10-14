# 旧 worktree 清场审计（K-STOCK-371）

> 2026-10-14 12:11-12:16 · 执行：Trae·A · 范围：`.worktrees/` 13 个历史工单残场
> 方法：三源交叉审计（tasks.json 状态 × git ahead(master) × worktree 注册表/进程扫描）

## 一、清理结果（本单已执行）

| 残场 | 类型 | 证据 | 处置 |
|---|---|---|---|
| K-ORG-015a | 空转 doing（10-13 起 29h） | 零提交零脏文件，worker 未接管 | worktree remove + 分支 -d + tasks.json 标 aborted |
| proj_c_nlq_001 | 空转 doing（10-13 起 29h） | 同上 | 同上 |
| K-STOCK-300/350/351/354 | 无 .git 纯文件残尸（0 MB 空壳） | 工单已 done+merged；不在 worktree 注册表 | 目录进回收站 |
| 分支 task/--title / task/--help | 畸形单残支（10-14 08:44 误操作） | 已完全并入 master | git branch -d |
| 分支 task/K-STOCK-155 | 孤儿残支（worktree 已注销） | ahead=0 已并入 master | git branch -d |

清场后 `.worktrees/`：13 → 8（全部有未收口成果或活跃中）。

## 二、保留项与理由（未动，需后续单处理）

| 残场 | 状态 | 保留理由 / 建议去向 |
|---|---|---|
| K-ORG-068 | **doing（12:08 刚开）** | e2e 彩排活跃单，绝不可动 |
| K-STOCK-211 | doing，**33 提交未合并** + 27 脏文件 | A股估值修复成果未收口——**建议单开抢救单**（review 后 merge 或 cherry-pick） |
| K-STOCK-212/213/222/227 | review，各 1 提交未合并 | QA1 已判「通过(待merge)」（K-ORG-025a R1/R3 批）——**建议单开收口单**跑 devtask merge 三守卫 |
| K-STOCK-307 | doing，1 提交未合并 | 云反代 B 方案（10-14 01:05），可能续做 |
| C:\Users\Administrator\.codex\worktrees\*（11 个） | review 积压（145~160 系） | Codex 时代产物，成果已回流 AI/111111 载体但工单未流转——超本单范围，留待组织线处理 |

## 三、判例（教训）

1. **无 .git 目录的「脏文件」是镜像假象**：`.worktrees/K-STOCK-300` 等四个目录没有 .git，`git -C <目录> status` 会向上爬到主仓、报的是**主仓**的状态（porcelain 路径恒为仓根相对）——看似残场内有当日产物，实为空壳。判定残场必须先查 `.git` 存在性（file=worktree / dir=克隆 / 无=纯残尸）。
2. **删除安全三源交叉**：tasks.json 状态 × `git cherry master <branch>`（patch 等价）× `git worktree list` 注册表——三者全绿才动手；分支一律 `-d` 试探（拒绝即留），禁 `-D` 兜底。
3. `git for-each-ref` 输出接 PowerShell 管道时 `| ForEach-Object Line` 会吞掉纯字符串行（字符串无 .Line 属性）——枚举输出直接打印或先 Select-String。
