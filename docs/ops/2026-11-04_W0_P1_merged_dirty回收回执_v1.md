# 2026-11-04 W0 P1：merged_dirty + aborted clean 工作区回收回执 v1

- 执行：Lead（本会话）｜时间：2026-11-04 22:39–22:40（Asia/Shanghai）
- 批准：老田 2026-11-04 22:38「继续回收其余 merged_dirty 和 aborted clean 的工作区」
- 性质：先留存未提交改动 → 再回收 worktree；**分支与提交历史全部保留，不合并 master**
- 基线：master `384d646e6`（执行期间未变）

## 一、回收清单与安全处理

| worktree | 任务状态 | 原分支 tip | 处理 | 新分支 tip |
|---|---|---|---|---|
| `K-ORG-20261031-BUNDLEB5` | done | `d38678c82` | 未提交 `tools/test_gate.ps1`（+66/-19）提交为快照 | `943ab1751` |
| `K-QUANT-20261030-IDEHARDEN` | done | `046ecfd64` | 未提交 `agent_platform/tests/test_legion_ide_http.py`（+6）提交为快照 | `fd1b0811f` |
| `WO-HZ-GATE-20261031` | done | `02fc06f86` | 2 个修改 + 1 个未跟踪测试（`tools/tests/test_huangzhong_tools_suite_gate.py`）提交为快照 | `66db79c1a` |
| `WO-LEGION-LOGINUI-20261030` | done | `94c4081b4` | 未提交 `portal/os_entry_v9_prod.html`（登录态角标，+4/-1）提交为快照 | `becff05dc` |
| `WO-ACAD1-NAV-20261103` | aborted | `beee32c18` | worktree clean，无未提交内容；直接回收 | `beee32c18`（未动） |

- 前四个 worktree 的未提交内容不是垃圾：分别是 test_gate 严格化/bundle bootstrap、IDE undo 测试、黄忠测试聚合入口、登录态角标。为不丢失，先在各自 task 分支提交 `W0-RECLAIM-SNAPSHOT-20261104`，commit 信息明确“worktree 回收前快照；不合并 master”，然后再删除 worktree。
- 四个快照 commit 均通过仓库 pre-commit 凭据扫描（rc=0），未发现未豁免凭据。
- 五个分支全部保留；`WO-ACAD1-NAV-20261103` 的 6 个未合并提交原样保留。

## 二、执行命令与结果

```powershell
# 四个 merged_dirty：先提交快照，确认 clean 后回收
git -C <wt> add -u
git -C <wt> add -- <untracked-file>        # 仅 WO-HZ-GATE
git -C <wt> commit -m "W0-RECLAIM-SNAPSHOT-20261104: ...（worktree 回收前快照；不合并 master）"
git worktree remove <wt>
# aborted clean：无未提交内容，直接回收
git worktree remove .worktrees\WO-ACAD1-NAV-20261103
git worktree prune
```

- 4 个快照 commit：`943ab1751`、`fd1b0811f`、`66db79c1a`、`becff05dc`；每支 unique=1（相对 master），**未合入 master**。
- 5 个 worktree 目录 `Test-Path` 全部为 False。
- 所有 `git worktree remove` rc=0；`prune` rc=0。

## 三、执行后实测（22:40）

- `tools/w0_inventory.py --json` rc=0：
  - **doing 41 / worktrees 54 / parked 8**
  - 相对执行前（41/59/8）：worktree **-5**；doing 不变（这 5 单本就非 doing）。
  - `worktree_actions`：active_task_review 41 / stale_review 10 / dirty_review **2**（剩余 `K-QUANT-20261030-OH2`、`K-QUANT-20261030-OHLOOP-TEST`，均为未合并 dirty，未在本轮批准范围）/ unregistered_review 1。
- `tools/devtask.py audit-worktrees --json` rc=0：`{"active":51,"violations":[],"ok":true}`。
- `git worktree list` 总计数 **54**（含主区）。
- master 仍 `384d646e6`；无 merge、无 force、无 `DEVTOOL_MERGE_GATE=0`。

## 四、边界声明

- 未合并任何快照 commit；未删除任何 task 分支；未改写历史。
- 未处理两个 unmerged_dirty（`K-QUANT-20261030-OH2`、`K-QUANT-20261030-OHLOOP-TEST`），因其内容未经 owner 判定，且不在老田本轮“merged_dirty + aborted clean”范围内。
- 未处理 `_default` 残迹、无 `.git` 目录、`tasks.json.lock`、`.oh-locks`；这些仍需 A0 逐项确认。
- W0 目标（doing≤8 / worktree≤20 / parked≤8）**未达成**；本回执只记录已批准的两类回收。

## 五、剩余差距

- worktree **54 → 20**：仍需回收 34 个。
- doing **41 → 8**：仍需关闭 33 个；parked 已满 8。
- 剩余 worktree 分类：merged_clean 0、merged_dirty 0、aborted clean 0；unmerged_dirty 2、unmerged_clean 51（含 41 doing / 10 review）、未注册残迹 1。
- 下一批只能处理：unmerged_dirty 的 owner 判定、review 10 单终裁、doing 41 单的 A0 批量口径，以及 11-05 16:30 UI/HZ 合入后自动回收的 2 支。
