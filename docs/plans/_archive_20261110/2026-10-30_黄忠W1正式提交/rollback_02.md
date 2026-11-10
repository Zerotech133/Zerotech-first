# 回滚点 02：任务板审计 F401 修复回滚（WO-BOARD-AUDIT-FIX-20261030）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



> 回滚对象：`tools/task_board_audit.py` 删除 `import sys` 的修复
> 修复提交：ac6f54158（2026-10-30 00:22，1 行删除）· 合并提交：31fd4f06a（merge task/WO-BOARD-AUDIT-FIX-20261030）
> 校验：`git merge-base --is-ancestor ac6f54158 HEAD` → 退出码 0（2026-10-30 实测）；回滚演练 2026-10-30 01:09 已在 worktree 实测（见第四节）

## 第一步：备份（或 git revert）

```powershell
cd C:\项目\老田的AI助手

# 1) 文件级备份（回滚前保留当前版本，便于二次对照）
Copy-Item tools\task_board_audit.py `
  C:\项目\_vault\backups\task_board_audit.py.before-f401-revert-20261030 -Force
Test-Path C:\项目\_vault\backups\task_board_audit.py.before-f401-revert-20261030   # 期望 True

# 2) 首选：git revert 真实修复提交（生成回滚提交，恢复 import sys）
git revert --no-edit ac6f54158
# 通过线：git log -1 --format=%s 显示 "Revert ..."；git show --stat HEAD 显示 tools/task_board_audit.py 1 insertion

# 备选：手工恢复备份（无 git 环境时）
# Copy-Item C:\项目\_vault\backups\task_board_audit.py.before-f401-revert-20261030 tools\task_board_audit.py -Force
```

## 第二步：生效（无常驻服务，无需重启）

- `tools/task_board_audit.py` 是**纯 CLI 只读工具**（`argparse` + `git` 只读子命令，不写 tasks.json），非常驻服务、无进程缓存；回滚提交合并到 master 后，下次调用直接载入新文件（源文件 mtime 变化，Python 自动重编译字节码）。
- 生效确认（可立即手动跑一次，不必等每日 09:30 对账）：
  ```powershell
  C:\项目\py312\python.exe -X utf8 tools\task_board_audit.py --json
  ```
  通过线：工具正常输出 JSON；`ok=true` 或（不可读 tasks.json 时）`ok=false` 且 error 明确写「不可读」——不得崩溃、不得静默。
- worktree 内无 `.worktrees/tasks.json`（工作树隔离），该场景只走不可读兜底；有效对账须在 master 工作区跑（2026-10-30 01:10 实测：`ok=true`、`open_tasks=36`、summary=branch_missing 18 / ahead_unmerged 15 / merged_but_open 3）。

## 第三步：验证（F401 恢复 + 测试不误伤）

```powershell
# 3.1 文件级 F401：回滚后必须重新报错（证明原修复真实有效）
C:\项目\py312\python.exe -X utf8 -m ruff check --select F401 tools\task_board_audit.py
# 通过线（回滚后）：rc=1，输出含 "F401 [*] `sys` imported but unused" 且位置 tools\task_board_audit.py:12:8
# 复位（再 revert 回滚提交）后通过线：rc=0、输出 "All checks passed!"、F401 命中数=0

# 3.2 相关 pytest 不误伤（未用 import 不影响功能）
C:\项目\py312\python.exe -X utf8 -m pytest -q -p no:cacheprovider -o addopts= tools/tests/test_task_board_audit.py
# 通过线：3 passed / 0 failed，rc=0

# 3.3 全仓 F/E9 棘轮口径（注意基线余量）
C:\项目\py312\python.exe -X utf8 tools\ruff_ratchet.py --check --json
# 回滚前实测：ok=true、baseline_total=48、current_total=7、regressions=[]、F401=3
# 回滚后实测：ok=true、current_total=8（F401=4）——因基线 F401=13 有余量，棘轮不会报 regression；
# 故「F401 是否恢复」以 3.1 文件级 ruff 为决定性判据，棘轮只作辅助。
```

## 四、2026-10-30 回滚演练实测记录（已实际执行并复位）

1. `git revert --no-commit ac6f54158` → 工作树 `tools/task_board_audit.py` 恢复 `import sys`（第 12 行）。
2. `ruff check --select F401 tools/task_board_audit.py` → `F401 [*] sys imported but unused → tools\task_board_audit.py:12:8`，`rc=1`。
3. `pytest -q tools/tests/test_task_board_audit.py` → **3 passed in 0.12s**，`rc=0`。
4. `ruff_ratchet --check --json` → `ok=true`、`current_total=8`、`regressions=[]`（F401=4，未超基线 13）。
5. `git reset --hard HEAD` 复位 → `ruff check --select F401` 回到 `All checks passed!`，工作树 `git status` 干净（无遗留暂存/改动）。

原始输出交付副本：`C:\项目\deliverables\2026-10-30_黄忠W1正式提交_证据\rollback02_revert_drill.txt`。

## 不可逆点与边界

- 回滚只涉及 1 行 import，无数据/配置/端口变更，可再次 revert 恢复，成本极低。
- 回滚会使静态检查重新出现 1 处 F401；不影响 `task_board_audit.py` 运行时功能（实测 pytest 3 passed）。
- 若回滚提交与后续对该工具的改动冲突，需人工按 `git status` 冲突文件手工合并后重跑 3.1/3.2 两条通过线。
