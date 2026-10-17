# GOV-FIX-004 账本保全与只读映射报告

> 生成时间：2026-10-17（只读审计）
> 范围：旧 devtask 账本与 Agent Platform JSONL 账本
> 纪律：未修改、未复制覆盖、未删除任何原账本或 worktree；未做路径批量替换。报告仅记录快照与候选项，后续迁移/清理须人工批准。

## 1. 总控口径

依据 `docs/governance/governance-control-ledger-2026-10-17.md` §GOV-15：旧账本与 JSONL 账本必须区分 raw/history 与 current；先保全、校验、建立映射，不直接修正状态或删除记录。

## 2. 文件快照

| 账本 | 绝对路径 | 大小（bytes） | mtime（本地时区） | SHA-256 |
|---|---|---:|---|---|
| Legacy devtask | `C:\项目\老田的AI助手\.worktrees\tasks.json` | 146,970 | 2026-10-17T18:08:51.079435+08:00 | `b4dfc18ce0c10945b68dfdbd79a451f3114ac52bcde6beff7991294bea5cf589` |
| Agent Platform | `C:\项目\老田的AI助手\agent_platform\data\org\tasks\tasks_202610.jsonl` | 16,466 | 2026-10-17T10:14:51.854841+08:00 | `2c8d5b8de6788fd969df7338d3d5efe586b82c41295089eee54e801a5675459c` |

## 3. 双统计与映射

### 3.1 Legacy `.worktrees/tasks.json`

- 结构：JSON object，原始 key/对象数（raw）`217`。
- raw 状态：`done=172`、`aborted=45`；未发现 `doing/review/todo`。
- 字段缺失：`8` 条缺少 `desc/branch/worktree`（均为 `K-PUSH-003/004/005/006/007/008/009`、`K-OPS-001`）；这些记录不能据此推断目录归属。
- 空 worktree：`8` 条，与上述字段缺失集合一致。
- 历史重试后缀：`-aborted-<6位时间>` 形成 9 个重复族（包括 `K-ORG-064`、`K-ORG-068`、`K-ORG-071`、`K-ORG-072` 及其他带后缀条目）。以去除该后缀后的 canonical key 做“当前候选”折叠：`208` 个 canonical 任务，`done=171`、`aborted=37`。这是分析统计，不是对原账本的改写。
- E: 旧路径引用：`148` 条（含历史/已终止记录）；不能直接视为活动写入。
- C: 当前仓路径引用：`36` 条；其中均为 `done/aborted` 历史记录。
- 交叉映射：与 JSONL 的 task_id 集合无直接同名重叠；两账本不可按 key 直接合并。

### 3.2 Agent Platform `tasks_202610.jsonl`

- raw 行数：`24`；可解析 task 记录 `23`，事件记录 `1`（`K-STOCK-162` 的 `event=task_start`），无解析失败行。
- task_id 出现次数：`T-20261008-001/002/003` 各 `3` 次；`T-20261008-004/005/006/007`、`T-20261009-008/009/010` 各 `2` 次；共 `10` 个 task_id。
- current 统计：按 task_id 最后一个 task 快照（跳过 event 行）取 `10` 个：`done=4`、`cancelled=6`；无 `open/doing` 当前项。
- current 状态中 `assigned_agent` 均为空；不能把历史 `history` 中的执行人当作当前 owner。

## 4. stale / 孤儿候选

以下均为“待人工复核候选”，不是自动清理结论：

1. **旧 E: 引用（stale-path 候选）**：148 条，多数状态为 `done/aborted`。E: 路径已不属于当前仓事实源，不能按路径存在性直接回写；需逐条建立历史映射和回滚证据。
2. **C: 当前仓 worktree 缺失候选**：账本中有 36 条 C: worktree 引用，但当前 `.worktrees/` 目录只有 `K-ORG-068`、`K-ORG-134` 两个目录；因此历史引用目标大多已不存在。状态全为 `done/aborted`，未发现活动状态指向缺失目录。
3. **实际目录的孤儿候选**：`K-ORG-068` 实际目录仅对应 4 条 `aborted` 历史记录，且无 active 状态；`K-ORG-134` 实际目录对应 1 条 `done` 记录。二者都不应删除，需人工确认是否为保留/残留 worktree。
4. **缺字段候选**：8 条 K-PUSH/K-OPS 记录缺 `desc/branch/worktree`，其中不能判定目录归属；不得靠猜测补齐。
5. **JSONL 独有候选**：10 个当前 task_id（及 `K-STOCK-162` event）不在 legacy key 中。它们属于另一账本域；不能按“未出现在 legacy”判定孤儿。当前快照仅 `done/cancelled`，没有 active 项。
6. **K-PUSH-010**：legacy 中为 `done`，worktree 记录指向当前仓但目录不存在；按总控台账要求（GOV-15）保留为 stale 候选，先人工确认，不删除、不改状态。

## 5. 工作区状态（只读观察）

- `git status --short --branch`：分支 `master`；存在并行开发改动，不能用清理/重置方式“收口”。
- 状态摘要：`M`/`A`/`??` 均存在（约 `6` 条 unstaged modified、`3` 条 staged modified、`1` 条 staged added、`98` 条 untracked，按 porcelain 前缀计数；部分文件名含中文，计数用于风险提示）。
- 当前 `.worktrees/` 目录仅：`K-ORG-068`、`K-ORG-134`。
- 该报告为新文件，未覆盖治理目录内已有文件。

## 6. 可复现命令（只读）

```bash
python - <<'PY'
import hashlib, json, pathlib
root = pathlib.Path(r'C:/项目/老田的AI助手')
for p in [root/'.worktrees/tasks.json', root/'agent_platform/data/org/tasks/tasks_202610.jsonl']:
    s = p.stat()
    print(p.resolve(), s.st_size, s.st_mtime, hashlib.sha256(p.read_bytes()).hexdigest())
print(len(json.loads((root/'.worktrees/tasks.json').read_text(encoding='utf-8'))))
print(len((root/'agent_platform/data/org/tasks/tasks_202610.jsonl').read_text(encoding='utf-8').splitlines()))
PY

git -C 'C:/项目/老田的AI助手' status --short --branch
```

## 7. 建议动作（需要批准）

- 将上述两份 SHA-256 与报告一起作为只读保全证据保存；每次迁移前重新计算并对比。
- 先为 canonical key、JSONL task_id 建立显式映射表，再讨论历史/当前投影；保留 raw/history，不原地清理。
- 对 E: 引用、缺字段记录、K-PUSH-010 与两个实际目录分别人工确认 owner、窗口、scope、rollback 后再决定是否迁移或隔离。
- 在未获批准前禁止对 `.worktrees/tasks.json`、`tasks_202610.jsonl` 做状态修正、字段补齐、删除、批量路径替换或重写。
