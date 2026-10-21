# GOV-FIX-004 账本保全复核记录

> 复核时间：2026-10-17（只读）  
> 复核范围：`.worktrees/tasks.json` 与 `agent_platform/data/org/tasks/tasks_202610.jsonl`  
> 对照基准：`docs/governance/GOV-FIX-004-ledger-preservation-2026-10-17.md`

## 1. 复核纪律

本次仅重新读取、哈希、统计和比对；未修改、覆盖、复制、删除或重写任一原账本，也未清理任何目录。

## 2. 文件快照复算

| 账本 | 绝对路径 | 复算 size（bytes） | 复算 mtime（本地时区） | 复算 SHA-256 | 与基准报告 |
|---|---|---:|---|---|---|
| Legacy devtask | `C:\项目\老田的AI助手\.worktrees\tasks.json` | 146,970 | 2026-10-17T18:08:51.079434800+08:00 | `b4dfc18ce0c10945b68dfdbd79a451f3114ac52bcde6beff7991294bea5cf589` | 一致 |
| Agent Platform | `C:\项目\老田的AI助手\agent_platform\data\org\tasks\tasks_202610.jsonl` | 16,466 | 2026-10-17T10:14:51.854841400+08:00 | `2c8d5b8de6788fd969df7338d3d5efe586b82c41295089eee54e801a5675459c` | 一致 |

基准报告的 mtime 记录为 `...079435+08:00` 与 `...854841+08:00`；本次文件系统纳秒读数分别为 `...079434800` 与 `...854841400`，按基准报告精度（微秒/约六位小数）一致。两份文件的 size 与 SHA-256 完全一致，未发现账本内容变化。

## 3. Legacy JSON 复算

- 顶层类型为 JSON object，raw key/对象数：`217`。
- raw 状态：`done=172`、`aborted=45`；未发现 `doing/review/todo`。
- 缺少 `desc`、`branch`、`worktree` 任一字段：`8` 条，集合为 `K-PUSH-003/004/005/006/007/008/009`、`K-OPS-001`；空 `worktree` 同为 `8` 条。
- 按 `-aborted-<6位时间>` 去除后缀折叠 canonical key：`208` 个。按重复族中出现 aborted 即保守归为 aborted，得到 `done=171`、`aborted=37`；与基准报告一致。该折叠仅为统计投影，不改变 raw 账本。
- worktree 路径引用：旧 `E:` 前缀 `148` 条；当前仓 `C:` 前缀 `36` 条（统一反斜杠/斜杠及大小写后计数）；与基准报告一致。两类记录状态均仅为 `done/aborted`。
- Legacy 与 JSONL 的 task_id/key 无直接同名重叠；两账本仍应视为不同账本域。

## 4. Agent Platform JSONL 复算

- 原始行数：`24`；全部可解析，无解析失败。
- 记录构成：task 记录 `23` 条，event 记录 `1` 条（`K-STOCK-162` 的 `event=task_start`）。
- task_id：`10` 个；出现次数为 `T-20261008-001/002/003` 各 `3` 次，`T-20261008-004/005/006/007` 与 `T-20261009-008/009/010` 各 `2` 次。
- current 投影：每个 task_id 取最后一个 task 快照（跳过 event），共 `10` 个；状态 `done=4`、`cancelled=6`，无 `open/doing`。
- current 快照的 `assigned_agent` 均为空；未将历史 `history` 中执行人误计为 current owner。

以上各项与基准报告 §3 完全一致。

## 5. 差异结论

未发现实质差异：两份账本的 SHA-256、size、mtime（按报告精度）以及 raw/current 统计均与 `GOV-FIX-004-ledger-preservation-2026-10-17.md` 一致。本复核文件为新增证据文件；原账本保持原样。

## 6. 可复现只读命令

```bash
sha256sum \
  'C:/项目/老田的AI助手/.worktrees/tasks.json' \
  'C:/项目/老田的AI助手/agent_platform/data/org/tasks/tasks_202610.jsonl'

stat -c '%n|size=%s|mtime=%y' \
  'C:/项目/老田的AI助手/.worktrees/tasks.json' \
  'C:/项目/老田的AI助手/agent_platform/data/org/tasks/tasks_202610.jsonl'
```

统计方法为 Python `json.loads` 读取 legacy object、逐行 `json.loads` 读取 JSONL，并在内存中进行 raw/current 投影；未写回数据源。
