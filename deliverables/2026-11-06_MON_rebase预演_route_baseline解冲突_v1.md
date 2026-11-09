# 2026-11-06 MON rebase 预演与 route baseline 解冲突

- 预演时间：2026-11-06 03:35–03:37（Asia/Shanghai）
- 预演对象：`task/K-STOCK-ANALYSIS-MON-20261104`，真实 tip `fe2a80350`
- 预演基线：master `c75dab182`
- 方式：`C:\Temp\mon_rebase_sim_20261106` detached worktree（只预演，不改真实分支）
- 结果：**rebase 成功；唯一冲突 `tools/portal_route_baseline.json`；取 master 305 路由后 `--check` rc=0，continue 无后续冲突**
- 真实分支/worktree：预演前后均为 `fe2a80350`、clean；临时 worktree 已删除，仓库无 rebase 残留

## 一、冲突事实

| 侧 | total | generated_at |
|---|---|---|
| rebase ours（master/upstream） | 305 | 2026-11-05T21:41:55 |
| rebase theirs（MON 分支被重放提交） | 298 | 2026-11-04T15:46:03 |

冲突文件仅：`tools/portal_route_baseline.json`。原因：MON 分支旧提交携带 298 路由基线，master 已到 305；不是 MON 引擎/接口冲突。

## 二、预演中实际解冲突步骤

在 `C:\Temp\mon_rebase_sim_20261106` 内执行（等价于真实 worktree）：

```powershell
cd C:\项目\老田的AI助手\.worktrees\K-STOCK-ANALYSIS-MON-20261104
git status --porcelain                 # 必须为空
git rev-parse HEAD                     # 记录当窗 tip
git rebase master

# 冲突时（预期只有 route baseline）
git status --short
git diff --name-only --diff-filter=U
# 预期：UU tools/portal_route_baseline.json

# rebase 语义：ours = 被 rebase 到的 master；theirs = MON 被重放的提交
git checkout --ours -- tools/portal_route_baseline.json

# 确认路由与 master 一致（本预演 rc=0）
python -X utf8 tools/portal_route_inventory.py --check
# 期望：ROUTE INVENTORY OK: 305 条与基线零 diff

# 若 --check 失败，才重生成（必须在真实 MON worktree 内执行，保证 source 路径正确）
# python -X utf8 tools/portal_route_inventory.py --write-baseline
# python -X utf8 tools/portal_route_inventory.py --check

git add -- tools/portal_route_baseline.json
git rebase --continue
# 若再次出现同一文件冲突，重复“取 master 版→check→add→continue”
```

预演结果：`git rebase --continue` 后直接 `Rebasing (4/4)`、`Successfully rebased`，无其它冲突。

## 三、rebase 后验证命令

```powershell
cd C:\项目\老田的AI助手\.worktrees\K-STOCK-ANALYSIS-MON-20261104
git status --porcelain                                   # 空
git log --oneline -5
git diff --name-status master...HEAD
# 期望仅：
# M analysis/monitor.py
# M analysis/tests/test_monitor.py

# route baseline 必须与 master 完全同一 blob（预演：5c5f7cc144baceb1bb28269eccc9ec5d30d280e6）
git rev-parse HEAD:tools/portal_route_baseline.json
git rev-parse master:tools/portal_route_baseline.json

python -X utf8 tools/portal_route_inventory.py --check
# 期望：ROUTE INVENTORY OK: 305 条与基线零 diff

# 引擎/测试/门户 API 保持预期版本
git rev-parse HEAD:analysis/monitor.py                  # 9026585b901632d273b129d82778bcfa4fe9b9a9
git rev-parse HEAD:analysis/tests/test_monitor.py       # faaf06a1b5bd559673180569c0221edec1639aa5
git rev-parse HEAD:portal/api_monitor.py                # 13fe863f1c2e445b47d0b947fd7cc7dd37be75ea（master 版）

# H5 钉扎不得变化
git diff --name-status master...HEAD -- tools/test_gate.ps1 tools/test_baseline.json
# 期望：无输出

# 定向测试（预演 49 passed）
python -X utf8 -m pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py

# PIN 预检
python -X utf8 tools/merge_gated.py --dry-run K-STOCK-ANALYSIS-MON-20261104

# 合并预测（rebase 后应 rc=0）
git merge-tree --write-tree master HEAD

# 完整 fast 门（窗口前当窗 tip 只认新 PASS）
powershell -NoProfile -ExecutionPolicy Bypass -File tools/test_gate.ps1 -Suite fast
```

## 四、预演验证结果

| 项 | 结果 |
|---|---|
| rebase | 成功（4 commits 重放；1 次 route baseline 冲突，0 次其它冲突） |
| 最终领先/落后 | `master...HEAD` = 0 / 3（route baseline 旧提交被折叠后只剩 3 个有效提交） |
| rebase 后变更面 | 仅 `analysis/monitor.py`、`analysis/tests/test_monitor.py` |
| route baseline | 与 master 同 blob `5c5f7cc144baceb1bb28269eccc9ec5d30d280e6` |
| `portal_route_inventory --check` | rc=0，305 条零 diff |
| 定向测试 | 49 passed（8.05s） |
| H5 pin | 无 diff |
| 真实 MON 分支 | 仍 `fe2a80350`，worktree clean，未被改动 |
| 临时 worktree | 已 `git worktree remove --force` 删除；无 rebase-merge/rebase-apply 残留 |

## 五、窗口执行注意

1. 真实 15:30 准备时重查 master/MON tip；若 tip 已再变，本预演结论需重跑，不能直接套用。
2. 冲突解决**不要手改 JSON、不要取分支侧 298**；取 master 305 后跑 `--check`，失败才重生成。
3. rebase 后必须重跑定向测试与完整 fast 门；旧 gate PASS 不复用。
4. 16:30 前不得 merge；16:58 硬停；失败即停，不 train-force、不 `DEVTOOL_MERGE_GATE=0`。
5. 真实 rebase 若出现 route baseline 之外的冲突（尤其 `analysis/monitor.py`/`portal/api_monitor.py`），退出 B 批转 REVIEW，不硬解。
