# 黄忠 W1 正式四件套提交（WO-HZ-W1FORMAL-20261030）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



> 提交人：黄忠（HUANGZHONG，交付验收总长）· 载体：DSH 会话（DeepSeek Harness / deepseek-flash）
> 提交日期：2026-10-30 · 工单：WO-HZ-W1FORMAL-20261030 · 分支：task/WO-HZ-W1FORMAL-20261030
> 工作树：`.worktrees/WO-HZ-W1FORMAL-20261030`（基于 master 27ee0665e 开出；禁止直接改 master 工作区）
> 性质：docs-only 提交（仅 9 个 md 文件），由 Lead 统一合并，本单不自跑 merge。

## 一、本目录文件（共 9 个）

| 文件 | 内容 | 对应工单证据 |
|---|---|---|
| `README.md` | 交付说明、目录导航、复现入口、自评摘要 | 本单 |
| `criteria_01.md` | 验收判据：验收会证据端点接入 | WO-HQ-ACCEPT-20261028（合并 38fd8fc29） |
| `criteria_02.md` | 验收判据：任务板审计 F401 清零 | WO-BOARD-AUDIT-FIX-20261030（合并 31fd4f06a） |
| `criteria_03.md` | 验收判据：研究院角色属性一致性 | WO-ACAD-ROLE-ATTR-20261029（合并 37bbcfca3） |
| `gate_review_01.md` | 七检解读：全绿（2026-10-30 现场实跑） | quality_gate.py 原始输出 |
| `gate_review_02.md` | 七检解读：语法门拦截真实案例 | WO-SYNTAX-GATE-20261028（合并 c066407e5） |
| `rollback_01.md` | 回滚点：ZHAOYUN 会议绑定覆盖 | WO-HQ-P2B-20261029（289c742a0） |
| `rollback_02.md` | 回滚点：任务板审计 F401 修复 | WO-BOARD-AUDIT-FIX-20261030（ac6f54158） |
| `self_review.md` | 自评分数（数字）+ 问题清单（5 条） | 本单 |

## 二、复现入口（全部命令在 worktree 内执行）

```powershell
cd C:\项目\老田的AI助手\.worktrees\WO-HZ-W1FORMAL-20261030
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"

# 七检（全绿证据见 gate_review_01.md）
C:\项目\py312\python.exe -X utf8 agent_platform\tools\quality_gate.py --json

# 语法门（拦截案例与复现见 gate_review_02.md）
C:\项目\py312\python.exe -X utf8 tools\check_syntax.py --fail-on-warning

# 三份判据的回归命令（合计 12 passed）
C:\项目\py312\python.exe -X utf8 -m pytest -q -p no:cacheprovider -o addopts= `
  agent_platform/tests/test_hq_acceptance.py `
  agent_platform/tests/test_academy_role_attrs.py `
  tools/tests/test_task_board_audit.py

# F401 门（判据 02）
C:\项目\py312\python.exe -X utf8 -m ruff check --select F401 tools/task_board_audit.py
C:\项目\py312\python.exe -X utf8 tools\ruff_ratchet.py --check --json

# 批改自测
C:\项目\py312\python.exe -X utf8 tools\huangzhong_grade.py --dir docs\plans\2026-10-30_黄忠W1正式提交 --json
```

## 三、证据原则

1. 所有 SHA/工单号均为本仓真实提交：`git cat-file -t` 与 `git merge-base --is-ancestor <sha> HEAD` 已逐条核对为 master 祖先，见各判据「核对命令」行。
2. 所有测试数/扫描数/端口数均为 2026-10-30 01:00–01:15 现场实测（非引用历史报告），原始输出副本随交付物落 `C:\项目\deliverables\2026-10-30_黄忠W1正式提交_证据\`。
3. 引用历史记录处均标注来源路径与时点（例：语法门 2893 文件为 2026-10-28 记录；本日现场扫描 3059 文件，差异原因=仓内 py 文件增长，已注明）。
4. 缺件不编造：`hq_acceptance.gather` 对缺失证据返回 `ok=false` + `warnings`，判据按「fail-visible」验收。

## 四、自评与批改

- 自评：94/100（分项见 `self_review.md`）。
- 自动批改：`tools/huangzhong_grade.py`，Rubric=可测性 25 + 证据链 25 + 七检 25 + 回滚 25 + 自评加分 0–5，≥80 通过。
- 批改单：`work/huangzhong/grade_<时间戳>.md`（worktree 内），交付副本见 `C:\项目\deliverables\2026-10-30_黄忠W1正式提交批改单_<分数>.md`。

## 五、范围与纪律声明

- 本单只新增 `docs/plans/2026-10-30_黄忠W1正式提交/` 下 9 个 md；未改代码、未改 registry、未动他人产物。
- master 工作区存在其它会话未提交改动（gzh/、jev-eval/、quant_backtest/、tools/datawarehouse/），本单未触碰、未暂存。
- 未执行 devtask merge（Lead 统一合并）；未执行任何线上重启（所有重启步骤均为回滚预案中的操作单，不预执行）。
