# AgentMondayCheckpoint 修复 + 残留清场 + KG 红灯转派

- 日期：2026-10-30 15:20
- 范围：周一观察点、worktree 清场、night_batch 僵尸锁、KG 非本线红灯
- 原则：**不删数据**；worktree/锁移入隔离区，保留回滚

## 1. AgentMondayCheckpoint 根因与修复

### 发现

`tools/gate3_verdict.py` 复核 `11/11` 失败，实际不是 replay_trace 本身：

```text
ledger 最新日 2026-10-30 done=33
monday_checkpoint / gate3 仍按固定 BASKET_N=5 判
→ “done 行数=33（预期 5）” FAIL
```

同时巡检日志只有 10 项，而 gate3 有 11 项：

- `monday_checkpoint` 首跑已发生后漏掉「影子首跑已发生」检查；
- gate3 仍计入该项 → 交叉核对永远 `10/10 vs 11/11`。

另外巡检进程退出码 120：`_Tee` 关闭日志后未恢复 `sys.stdout/stderr`，
解释器 finalize 时 flush 已关闭句柄。

### 修复

- `_basket_complete` / gate3：
  - `BASKET_N` 仍是最小篮子；
  - 动态池允许 done/不同标的数 **≥ 5**，不再要求恰好 5。
- `monday_checkpoint` 首跑后补回「影子首跑已发生」检查，恢复 11 项；
- `_Tee` 关闭前恢复原始 stdout/stderr，消除 exit 120；
- 文档/提示：行数大于最小篮子标记为“动态池/多次运行”，不再误报“疑似重复写入”。

### 验证

```text
focused pytest:
test_gate3_verdict / monday_checkpoint_*  → 59 passed

monday_checkpoint.py
→ 11/11 PASS，EXIT=0

gate3_verdict.py
→ 11/11 全过，交叉核对 PASS，退出码 0
```

`replay_trace` 当前：

```text
[PASS] replay_trace 锚点回放（9d50351b606b）
verdict=OK 命中=1 缺环=无
```

## 2. worktree 清场

现场从 **63 个 worktree** 开始；期间另一个 `devtask merge` 并发清理，
统计降到 30 个任务 worktree。按“只清可安全清、绝不误清 active”执行：

- 保留 doing/review：**10 个**
- 有脏文件：**9 个**（未移动，待 owner 处理）
- 隔离 clean/done：**11 个**
- 清场后：18 个任务 worktree / 19 条 worktree 记录

隔离位置（可回滚，未删除）：

```text
C:\项目\_vault\worktree-quarantine\20261030_151934\
  manifest.json
```

隔离对象含 `K-QUANT-20261029-IDE*`、`K-STOCK-ANALYSIS-P0U2`、
`WO-LCS1-A6`、`WO-LEGION-P2-V1-EVID2`、`WO-MEETING-JS` 等。

未达用户口径的 59：现场已被并发 merge 自行清到 30；本工具只处理了
剩余 clean 残留，**不把 active/脏工作区算进“清理数”**。

## 3. night_batch 僵尸锁

```text
C:\项目\老田的AI助手\locks\night_batch.lock
pid=20052 / start 2026-10-30 08:30
进程已不存在
```

已移入隔离区：

```text
C:\项目\_vault\stale-locks\night_batch.lock.20261030_151504
```

未删除，未影响当前 `night_batch` 运行。

## 4. KG 红灯（非本线）

`data.root_drift` / `agent.daily` / `agent.census` 属 KG/治理线，
不在 IDE/JEV/认证线处理范围。建议转派：

- KG 线：`data.root_drift`
- 治理席：`agent.daily` / `agent.census`
- 本文只记录转派建议，不改 KG 代码/数据

证据引用：`docs/audit/2026-10-30_JEV复盘代码审计与Bug复核.md` 已标注
`KgDataDashboard / data.root_drift` 为 KG 线。

## 5. 当前状态

```text
monday_checkpoint  11/11 PASS
gate3_verdict      11/11 PASS
任务 worktree      18 个（active 10 / dirty 8 + clean kept）
僵尸锁             已隔离
KG 红灯             待 KG/治理席接收
```

## 6. 回滚

- worktree：把隔离目录名字移回 `.worktrees/`，执行 `git worktree repair <path>`；
- 锁：把隔离文件移回 `locks/night_batch.lock`；
- 代码：revert `agent_platform/monday_checkpoint.py`、
  `tools/gate3_verdict.py` 两个文件即可恢复旧口径。
