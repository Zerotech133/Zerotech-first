# 2026-11-04 W0 P1b：两个 unmerged_dirty 工作区回收回执 v1

- 执行：Lead（本会话）｜时间：2026-11-04 22:45–22:46（Asia/Shanghai）
- 批准：老田 2026-11-04 22:44「继续处理两个 unmerged_dirty 工作区，先看 K-QUANT-20261030-OH2 的改动内容再决定」
- 性质：先逐项查看改动 → 证据文件快照到原 task 分支 → 回收 worktree；**分支保留、不合并 master**
- 基线：执行开始时 master `85fd44d8f`；执行期间其他会话推进到 `9fc763d0e`（ACAD1 全池测试隔离），非本回执产生

## 一、K-QUANT-20261030-OH2（任务已 done）

**改动内容核查**：`git status` 仅 6 个未跟踪文件，无 tracked 修改：
`DOCKER_OK.txt`、`OH2_ASCII_OK.txt`、`OH2_DOCKER_NEW.txt`、`OH2_UI_EDIT.md`、`OH2_UI_FINAL.txt`、`OH2_UI_SANDBOX.txt`。
内容均为一行验证标记（如 `docker-ok`、`ascii-ok`、`final-8796-ok`），不是代码改动。任务台账 note 已写明 OpenHands 二期“随选择性提交入 master（6fe40684e/ce44e2569/bd90ace85）”，工作区里的 3 个 feature commit 未被直接 merge，但任务状态 done。

**决定**：不把 branch 合回 master；将 6 个证据文件提交为分支快照，保留审计证据，再移除 worktree。

- 原 tip：`847544a01`（unique=3）
- 快照 commit：`52a8c2da6`（6 files，+6）
- 新 tip：`52a8c2da6`（unique=4，相对当时 master）
- worktree：已移除；分支 `task/K-QUANT-20261030-OH2` 保留

## 二、K-QUANT-20261030-OHLOOP-TEST（任务 aborted，保留 reopen 权）

**改动内容核查**：`git status` 仅 17 个未跟踪文件，无 tracked 修改；均为 6–22 字节的验收标记/说明，如 `APPROVAL_FILE_OK.txt`、`HERMES_SSE_OK.txt`、`OHLIVE_E2E.md`、`SESSION_CROSS_BROWSER.txt`、`SSE_TEST_OK.txt` 等。任务台账明确“分支/证据保留，owner 可 reopen”，不能直接丢弃证据。

**决定**：17 个证据文件提交为分支快照，保留 reopen 所需证据，再移除 worktree；分支保留。

- 原 tip：`a92088dfe`（unique=4）
- 快照 commit：`4545ac190`（17 files，+17）
- 新 tip：`4545ac190`（unique=5，相对当时 master）
- worktree：已移除；分支 `task/K-QUANT-20261030-OHLOOP-TEST` 保留

## 三、执行命令与结果

```powershell
# 两个 worktree 均先确认只有未跟踪证据文件
git -C <wt> add -- <每个证据文件>          # 显式路径，不用 git add -A
git -C <wt> commit -m "W0-RECLAIM-SNAPSHOT-20261104: ... 证据文件留存（worktree 回收前快照；不合并 master）"
git -C <wt> status --porcelain             # 0，确认 clean
git worktree remove <wt>                   # rc=0
git worktree prune                         # rc=0
```

- 两个快照 commit 均通过 pre-commit 凭据扫描（rc=0，0 个未豁免命中）。
- 两个 worktree 目录 `Test-Path` 均为 False。
- `git worktree remove`、`prune` rc=0。

## 四、执行后实测（22:46）

- `tools/w0_inventory.py --json` rc=0：
  - **doing 42 / worktrees 53 / parked 8**
  - 相对 P1 后（41/54/8）：worktree **-1**（本轮移除 2，但其他会话新增 1 个 worktree）；doing **+1**（并发新增 1 个 doing 单，非本轮回收导致）。
  - `worktree_actions`：active_task_review 42 / stale_review 10 / **dirty_review 0** / unregistered_review 1。
- `tools/devtask.py audit-worktrees --json` rc=0：`{"active":52,"violations":[],"ok":true}`。
- `git worktree list` 总计数 **53**（含主区）。
- master 当前 `9fc763d0e`（并发会话推进）；本会话本轮**未合入任何东西**，未 force、未 `DEVTOOL_MERGE_GATE=0`。

## 五、边界声明

- 两个 worktree 的未跟踪证据均已保存到各自 task 分支快照，未丢失；分支全部保留。
- 未合并两个分支到 master；OHLOOP 仍保持 aborted，owner 可后续 reopen/评估。
- 未处理 `_default` 未注册残迹、无 `.git` 目录、`tasks.json.lock`、`.oh-locks`；仍需 A0 逐项确认。
- W0 目标（doing≤8 / worktree≤20 / parked≤8）未达成。

## 六、剩余差距

- worktree **53 → 20**：仍需回收 33 个。
- doing **42 → 8**：仍需关闭 34 个；parked 已满 8。
- 剩余 worktree 构成：unmerged_clean 52 个（42 active_task_review + 10 stale_review）+ 未注册残迹 1；**dirty worktree 已清零**。
- 下一批只能处理：doing/review 的 A0 批量 done/abort 口径、未注册残迹/stale 锁，以及 11-05 16:30 UI/HZ 合入后自动回收的 2 支。
