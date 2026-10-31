# 验收判据 02：任务板审计 F401 清零（WO-BOARD-AUDIT-FIX-20261030）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



> 工单：WO-BOARD-AUDIT-FIX-20261030「修 task_board_audit F401（sys 未用）」
> 任务提交：ac6f54158（-1 行：删除 `import sys`）· 合并提交：31fd4f06a（merge task/WO-BOARD-AUDIT-FIX-20261030）· 2026-10-30 00:53 入 master
> 核对：`git cat-file -t ac6f54158` → commit；`git merge-base --is-ancestor 31fd4f06a HEAD` → 退出码 0（master 祖先，2026-10-30 实测）

## 一、判据表（每条含证据路径/命令 + 通过线）

| # | 判据 | 证据路径/命令 | 通过线（阈值） |
|---|---|---|---|
| 1 | 未用 import 已移除 | `tools/task_board_audit.py:9-12`（import argparse / json / subprocess，**无** `import sys`） | `Select-String -Pattern "^import sys"` 命中数 = 0 |
| 2 | 文件级 F401 清零 | `C:\项目\py312\python.exe -X utf8 -m ruff check --select F401 tools/task_board_audit.py` | **rc=0** 且输出 `All checks passed!`，F401 命中数 = 0（2026-10-30 实测） |
| 3 | 全仓 F/E9 棘轮无回退 | `python -X utf8 tools/ruff_ratchet.py --check --json` | `ok=true`、`regressions=[]`、`current_total=7 <= baseline_total=48`；F401 实测 13→3（2026-10-30） |
| 4 | 审计工具功能不降级 | `tools/tests/test_task_board_audit.py`：`test_classify_all` 覆盖 7 类漂移、`test_audit_missing_tasks` 不可读兜底、`test_audit_empty_tasks` 空板 | **3 passed / 0 failed**，rc=0（2026-10-30 实测：3 passed in 0.12s） |
| 5 | 工具可真实跑出对账 | 在 master 工作区执行 `python -X utf8 tools/task_board_audit.py --json` | `ok=true`；实测 `open_tasks=36`、`summary={branch_missing:18, ahead_unmerged:15, merged_but_open:3}`（2026-10-30 01:10） |
| 6 | 可回滚且可验证 | `git revert --no-commit ac6f54158` → 重跑判据 2/4（见 `rollback_02.md` 演练记录） | 回滚后 ruff 必报 `F401 ... tools\task_board_audit.py:12:8`（rc=1），`pytest` 仍 3 passed；复位后 rc=0 |

## 二、通过线汇总（可计数）

- F401 文件级：命中 `ok=0`（rc=0）。
- 棘轮：`regressions` 长度 `ok=0`；`current_total` 不得大于 `baseline_total`。
- 测试：`passed >= 3`、`failed <= 0`。
- 回滚演练判据：回滚后 `F401 >= 1`（证明原修复真实有效），复位后 `F401 == 0`。

## 三、回归项（修复后不误伤）

| 回归项 | 命令 | 通过线 |
|---|---|---|
| 分类纯函数 7 分支 | `pytest -q tools/tests/test_task_board_audit.py::test_classify_all` | passed |
| 不可读 tasks 兜底 | `pytest -q tools/tests/test_task_board_audit.py::test_audit_missing_tasks` | passed，`ok=false` 且 error 含「不可读」或 `unreadable` |
| 与七检 names/tests 不冲突 | `quality_gate.py` 的 `tests` 项（155 passed, 1 skipped）与 `names` 项（violations=[]） | ok=true（见 `gate_review_01.md`） |
| worktree 隔离已知边界 | worktree 内无 `.worktrees/tasks.json`，工具走不可读兜底分支（`ok=false`）；有效对账须在 master 工作区跑 | 属预期行为，非缺陷 |

## 四、不可逆点与边界

- 改动仅 1 行 import，回滚成本极低（`git revert` 一条命令），无数据/配置副作用。
- F401 修复不影响 `task_board_audit.py` 的只读语义：工具只执行 `git` 只读子命令，不写 tasks.json。
- 棘轮基线存在历史余量（baseline 48 vs current 7），故回滚单个 F401 时 `ruff_ratchet` 不会报 regression（实测 current 7→8 仍 `ok=true`）；决定性判据是**文件级 ruff**，不能只看棘轮。
