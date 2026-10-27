# ADR-006：一单一事 + git worktree 隔离

- **状态**：Accepted（历史回填，2026-10-27 记录；原决策 2026-10-13 K-STOCK-104 方案丙）
- **关联**：P3；`tools/devtask.py`；`AGENTS.md` §3/§4

## 背景

多 Agent/多会话并行开发同一仓库，直接在 master 改动会互相覆盖、无法回滚、无法评审。

## 决定

1. 每个任务单 = 独立分支 `task/<TID>` + 独立 worktree `.worktrees/<TID>/`；
2. `devtask.py new` 建单/建 worktree；`merge` 过评审门后合并；`drop` 放弃留原因；
3. 禁止 master 直接开发；工单状态机 doing→review→done；
4. worktree/分支清理用 `prune --dry-run` 人工确认后 apply。

## 后果

- 正面：并行不互踩、合并可审计、回滚简单（git revert merge）。
- 代价：worktree 数量会涨（需定期 prune）；单机磁盘占用增加（已在 P1 用 prune 收口）。
- 不做：不引入远程 PR 流程（本机单仓），但保留分支语义。
