# UI / HZ rebase 执行回执 v1（2026-11-04）

> 指令来源：老田 2026-11-04 11:04 对话「执行rebase」（显式指令，覆盖默认“不代他人 rebase”边界）
> 执行：DSH-Lead（守点）｜时间：2026-11-04 11:05–11:09 Asia/Shanghai
> 范围：`task/UI-UNIFY-MANIFEST-20261103`、`task/HZ-FINAL-DATE-20261104`（均在 `.worktrees/`，执行前 clean；无 MERGE_HEAD/index.lock/rebase 状态）
> 纪律：仅在各自 worktree 内 rebase；未合并、未改 master、未 `reset --hard`/`clean`、未绕 H5。

## 一、执行结果

| 分支 | old sha | new sha | rebase rc | ahead/behind | PIN_GATE | 测试 | worktree | 结论 |
|---|---|---|---|---|---|---|---|---|
| UI-UNIFY-MANIFEST-20261103 | `5e233b27e` | `5bdc9e6b4` | 0 | 2 / **0** | ✅ **OK** | **3 failed / 17 passed**（见 §二） | clean | **不可合**（测试红） |
| HZ-FINAL-DATE-20261104 | `b472a5343` | `bc8568da5` | 0 | 1 / **0** | ✅ **OK** | **79 passed**（rc=0） | clean | **可合候选**（待 16:20 预检/窗口） |

- 命令：`git -C .worktrees/<TID> rebase master`；UI 重放 2 提交、HZ 重放 1 提交，均无冲突。
- 三支现状：UI/HZ/W1 **behind=0**；`merge_gated --dry-run` 三支 **PIN OK**。

## 二、UI 测试失败详情（rebase 后暴露，非 rebase 损坏）

`python -X utf8 -m pytest portal/test_page_manifest.py -q` → **3 failed, 17 passed**（5.15s）：

1. `test_current_tree_check_passes`：manifest 违规 3 条
   - `missing_page`：`portal/d1_training.html` 缺 manifest（需 `--generate` 登记）
   - `route_baseline_drift`：manifest total=293 vs 现行 route total=298（需 `tools/portal_route_inventory.py --write-baseline` 同批）
   - `route_drift`：`portal/quant.html` 规范路由 `/quant` vs manifest 记 `/quant-full`（改路由/文件须同批 `--generate`）
2. `test_current_manifest_covers_production_pages`（缺页连带）
3. `test_real_manifest_not_modified_by_check`（check 会改 manifest 的连带失败）

**根因**：分支基于旧 master（behind 曾 330）；master 在此期间新增页面/改路由/扩 route 基线，分支 manifest 未同步。**属 owner 内容修复**：同批执行 `--generate` + `--write-baseline` + 更新测试期望，复跑 20 用例与 fast 门后重排合并。
**本回执不代改分支内容**（改 manifest/route baseline 会进入合并内容，须 owner 确认）。

## 三、回滚方式

- 分支回滚到 rebase 前：`git -C .worktrees/<TID> reset --keep <old_sha>`
  - UI：`reset --keep 5e233b27e`；HZ：`reset --keep b472a5343`
- 使用 `--keep`（干净树），**不用 `reset --hard`**；不删 worktree/不 clean。
- rebase 过程无冲突，可选 `rebase --abort` 已不适用（rebase 已完成）；回滚以上面 ref 重置为准。

## 四、对 16:30 的影响

- 可合候选：**HZ-FINAL-DATE（79 passed / PIN OK / behind 0）**、**W1-PILOT-TOOLFIX（PIN OK）** → 最多 2 单；
- UI-MANIFEST：**不可合**（3 测试红），16:30 不合，等 owner 修复后在 21:40/下一窗口重排；
- 16:20 按当时 master 重跑 `merge_gated --dry-run` + 定向测试 + 容量/脏树检查；**不绕 H5、不 --train-force**。

## 五、证据

- rebase 输出：`Successfully rebased and updated refs/heads/task/...`（rc=0）；
- `merge_gated --dry-run` 三支：UI/HZ/W1 均 `PIN OK`；
- UI 测试：`3 failed, 17 passed in 5.15s`（失败明细见 §二）；
- HZ 测试：`79 passed in 3.84s`（rc=0）；
- 两 worktree `git status --porcelain` = clean；`git rev-list --count *..master` = 0（behind）。
