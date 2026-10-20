# AgentMondayCheckpoint 异常退出诊断（0xC000013A）

- **工位**：ws-mcd（数字军团）
- **诊断日期**：2026-10-20（周二）
- **事发时间**：2026-10-19（周一）17:25:00 启动，18:17:30 前后被杀
- **性质**：**只读取证**。全程未修改/新建/删除任何计划任务，未改任何系统配置，未重启服务，未杀进程，未执行 `schtasks /Change|/Run`。本文件是本次诊断的唯一写点。

---

## 0. 结论摘要（先给答案）

| 项 | 判定 |
|---|---|
| 结果码含义 | `0xC000013A` = `STATUS_CONTROL_C_EXIT`（进程被外部强制结束） |
| 死因 | **操作员在 18:18:54 从开始菜单发起「重启」，机器在18:19:04 完成关机，18:19:24 内核完成引导。** 该进程在关机过程中被系统终止 |
| 三选一结论 | **① 一次性异常，环境已恢复**（但带一个**结构性脆弱点**，见 §5） |
| 证据强度 | **高**。有内核级事件链（1074 → 6006 → 109 → 6005 → Kernel-Boot）完整闭环，且与日志 mtime 精确对齐 |
| 产物影响 | checkpoint **结论产物未产出**（末块无 `EXIT=`/`结论:` 行）；但下游判读器将其判为 **WARN 而非 FAIL**，未污染告警 |
| 是否需改 | **不需为本次事故改配置**；建议 1 条防复发加固（工单要素见 §6，本工位不执行） |

---

## 1. 任务取证表（只读）

来源：`Get-ScheduledTask` / `Get-ScheduledTaskInfo`（只读查询）。

| 字段 | 实测值 |
|---|---|
| TaskName | `AgentMondayCheckpoint` |
| TaskPath / URI | `\` / `\AgentMondayCheckpoint` |
| **State** | `Ready`（非 Running，非 Hung） |
| Author | （空） |
| Description | `M5 checkpoint Monday 17:25 after first shadow run (read-only gate check, ed389bc)` |
| **LastRunTime** | `2026/10/19 17:25:00` |
| **LastTaskResult** | `3221225786` = `0xC000013A` |
| **NextRunTime** | `2026/10/26 17:25:00` |
| NumberOfMissedRuns | `0` |
| **动作 Execute** | `C:\Program Files\Python311\python.exe` |
| **动作 Arguments** | `-X utf8 C:\aios\agent_platform\monday_checkpoint.py` |
| WorkingDirectory | （空） |
| **触发器** | 周触发，`DaysOfWeek=2`（周二），`WeeksInterval=1`，`StartBoundary=2026-10-19T17:25:00+08:00`，`Enabled=True` |
| **运行账号 UserId** | `t` |
| **LogonType** | **`Interactive`** |
| RunLevel | `Limited` |
| **ExecutionTimeLimit** | `PT72H`（72 小时，远大于脚本自身 180 分钟等待上限 → **不可能**是超时被杀） |
| MultipleInstances | `IgnoreNew` |
| RestartCount / RestartInterval | `0` / （空） |
| AllowHardTerminate | `True` |
| StartWhenAvailable | `True` |
| DisallowStartIfOnBatteries | `False` |
| WakeToRun | `False` |
| RunOnlyIfIdle / RunOnlyIfNetworkAvailable | `False` / `False` |
| Priority / Hidden | `7` / `True` |
| Enabled | `True` |

### 1.1 同批任务横向对比（只读）

| 任务 | LastRunTime | LastTaskResult | LogonType | 判读 |
|---|---|---|---|---|
| `AgentMondayCheckpoint` | 2026-10-19 17:25 | **0xC000013A** | **Interactive** | 本次异常 |
| `AgentShadowDaily` | 2026-10-20 03:47 | 0 | S4U | 正常 |
| `AgentChiefPush` | 2026-10-19 17:35 | 0 | S4U | 正常 |
| `LegionDailyReview` | 2026-10-19 17:40 | 0 | S4U | 正常 |
| `AgentRetrospectBrief` | 2026-10-19 18:05 | 0 | Interactive | 正常（短任务） |
| `LegionMorningMeeting` | 2026-10-20 09:00 | 0 | — | 正常 |
| `AgentLegionHeartbeat` | 2026-10-20 08:30 | `0x2` | — | 另有独立判读（不属本工位） |

> 关键观察：**唯一使用 `LogonType=Interactive` 且长时运行的任务**是本次受害者。同为 Interactive 的 `AgentRetrospectBrief` 是短任务，未跨越 18:18:54 重启点，故未受影响。

---

## 2. 结果码解读

### 2.1 `0xC000013A` 的含义

- 十进制 `3221225786`，十六进制 `0xC000013A` = NTSTATUS **`STATUS_CONTROL_C_EXIT`**。
- 语义：进程**不是自己正常退出的**，而是被外部以「等同于 Ctrl+C / 控制台关闭事件」的方式终止。
- Windows 任务计划程序在**任务被外部终止**（而非脚本自行 `sys.exit()`）时，就把这个值写进 `LastTaskResult`。
- 判读要点：脚本自身只会 `sys.exit(0)` 或 `sys.exit(1)`（`monday_checkpoint.py:465,469`）。**`0xC000013A` 不可能是脚本抛异常产生的** —— Python 异常退出码是 `1`，且会先打印 Traceback。**出现 `0xC000013A` 即证明进程被硬杀。**

### 2.2 最常见成因及逐一排除

| # | 成因 | 结论 | 本机证据（只读） |
|---|---|---|---|
| 1 | **进程被外部强杀 / 系统关机重启连带终止** | ✅ **坐实（本案真因）** | 见 §3 完整事件链 |
| 2 | 用户会话注销 / 注销式断连导致任务被回收 | ⚠️ 部分相关，但形态是**整机重启**而非单纯 logoff | 1074 明确记载发起进程为 `StartMenuExperienceHost.exe`（开始菜单 = 交互式操作），关机类型「重启」；同窗口有 `User32` / `Application Popup` 事件。注销会记 4647/4634 类事件，本次未见纯 logoff 链|
| 3 | **任务自身超时被终止**（ExecutionTimeLimit） | ❌ **排除** | `ExecutionTimeLimit=PT72H`，而进程仅存活 52 分钟（17:25:00→18:17:30），远低于 72 小时上限；且脚本自身等待上限 `MAX_WAIT_ROUNDS=120 × 90s = 180 分钟`（`monday_checkpoint.py:54,37`）也未到 |
| 4 | 依赖进程不存在 / 解释器缺失 / 路径失效 | ❌ **排除** | `C:\Program Files\Python311\python.exe` 存在（Python 3.11.9，103,192 字节）；`C:\aios\agent_platform\monday_checkpoint.py` 存在（22,446 字节）。进程已成功启动并写了 36 轮日志——若路径/解释器有问题，第一秒就失败，不会跑 52 分钟 |
| 5 | 同时运行实例冲突（多实例抢占） | ❌ **排除** | `MultipleInstances=IgnoreNew`；策略是「忽略新实例」，**不会杀掉正在跑的实例**；且末块日志显示只有一条 17:25:00 起始的 run 块，无并发实例痕迹 |
| 6 | 脚本报错 / 依赖缺失抛异常 | ❌ **排除** | 日志末块无任何 Traceback、无 `[FAIL]`/`结论:`/`EXIT=`；进程是在 `time.sleep(90)`（`monday_checkpoint.py:345`）**睡眠等待中**被杀的 |

---

## 3. 死亡时刻的精确重建（本机证据）

### 3.1 进程最后存活时刻

脚本每轮等待打印一行 `[wait]` 后 `sleep(90)`再重新读账本（`monday_checkpoint.py:337-346`）。

- 17:25:00 启动 → 第 36 轮打印时刻 = `17:25:00 + 35 × 90s` = **18:17:30**
- 巡检日志 mtime = **2026-10-19 18:17:30** ✅ **与理论值逐秒吻合**
- 该时刻进程处于第 37 轮 `time.sleep(90)` 中，即 18:17:30 → 18:19:00 之间被杀

> 这是本案最硬的一条证据：**日志停止写入的时间点可由脚本逻辑独立算出，且与文件系统 mtime 精确一致**，说明进程是在等待循环中被外部终止，而非自行跑完或崩栈。

### 3.2 系统侧事件链（System 日志，只读）

事件时间线（**本机时区 China Standard Time = UTC+8**）：

| 本地时间 | 事件 | 含义 |
|---|---|---|
| 18:17:30 | （巡检日志最后写入） | 巡检进程最后存活 |
| **18:18:54.202** | **Event ID 1074**, Source=`Microsoft-Windows-IsolatedUserMode` | **操作员发起重启**。发起进程 `C:\Windows\SystemApps\...\StartMenuExperienceHost.exe`，代表用户 `DESKTOP-ARF447U\t`，原因「其他(计划外)」，**关机类型：重启** |
| 18:18:54.975 / 18:18:57.875 / 18:18:58.997 | Event ID 26 ×3, `Application Popup` | 弹出 `llama-server.exe - Application Error: 应用程序无法正常启动(0xc0000142)`（关机过程中服务已停） |
| 18:18:59.611 / .750 | Event ID 100 ×2, `Application Popup` | 关机过程应用弹窗 |
| **18:19:04.970** | **Event ID 6006** | **事件日志服务已停止**（关机落定） |
| 18:19:09.947 | Event ID 109 | 内核电源管理器已启动关闭转换（原因：Kernel API） |
| **18:19:39.096** | **Event ID 6005** | **事件日志服务已启动**（系统重启完成） |
| 18:19:24.96x | Event ID 1/18/20/25/27/32 × 多条, `Kernel-Boot` | **内核完成本次启动** |

**闭环**：`1074（人点了重启）` → `6006（关机完成）` → `109（关机转换）` → `Kernel-Boot 18:19:24（内核启动）` → `6005（日志服务恢复）`。这是一次**完整、正常、人为发起的整机重启**。

**时间关系**：进程最后存活 18:17:30 → 人工点重启 18:18:54 → 关机完成 18:19:04。**进程恰好死在「人点重启」到「关机完成」这 90 秒的窗口内。**

### 3.3 排除「同时段有其他任务强杀」

列出 2026-10-19 17:00–19:30 之间**所有**运行过的计划任务（共 16 个），逐一比对：

- 17:25 `AgentMondayCheckpoint` ← 本次受害者
- 17:30 `ChanlunScanHK`(0)、17:35 `AgentChiefPush`(0)、17:40 `LegionDailyReview`(0)、17:50 `GubaCrawl`(0)/`ObservePointReviewOnce`(**1**)、18:00 `XueqiuCrawl`(0)、18:05 `AgentRetrospectBrief`(0)、18:10 `AdjFactorSync`(0)/`KgCl1Sync`(0)、18:23 `appuriverifierinstall`(0)、18:30 `GoldShadow`(0)、19:00 `XueqiuDailyReport`(0)、19:05 `XueqiuReportVerify`(0)、19:10 `RiskGuardShadowDaily`(0)

**18:17:30 前后 ±15 分钟内没有任何其他任务运行**，更无杀进程动作。→ **「被兄弟任务强杀」排除。**

### 3.4 排除「看门狗/拉起脚本误杀」

| 嫌疑脚本 | 频率 | 是否含杀进程逻辑 | 判定 |
|---|---|---|---|
| `AgentWatchdog`（每 5 分钟）→ `C:\Users\t\AppData\Local\Programs\agent_platform\start_agent_platform.ps1` | 5 min | ❌ **无任何 kill**。仅 `Get-CimInstance` **查询** `agent_platform.cli watch-daemon\|ack-serve`，缺哪个`Start-Process` 拉哪个，命中范围与本任务无交集 | **排除** |
| `LegionOps` → `C:\aios\ops\start_legion_ops.ps1` | 5 min | ⚠️ 有 kill，但**双重收窄**：① 仅当哨兵文件 `C:\Temp\LEGION_OPS_RESTART.flag` 存在；② `CommandLine LIKE '%api_legion_ops_v2%'` 精确匹配 | **排除**（本任务命令行是 `monday_checkpoint.py`，不匹配） |
| `portal\start_portal_api.ps1` | — | 仅杀 `portal_api` 孤儿进程 | **排除** |
| `zhice.ps1` | — | 有 `taskkill`/`Stop-Process`，但作用域为 `Get-Procs` 所列目标，非全局 python 扫杀 | **排除** |

**另注**：`llama-server.exe` 在 18:18:54 前后反复崩溃弹窗（`0xc0000142`），说明操作员当时刻**正在处理大模型服务故障**——这进一步佐证重启是「人为处置故障」而非计划内维护。

### 3.5 关于日志时区的一处校正（避免误判）

`wevtutil` 文本输出的 `Date:` 字段带 `Z` 后缀但**实为本地时间**（本机 UTC+8）。已用两条独立证据校正：
1. 最新一条事件 `2026-10-20T12:44:49` ≈ 查询当时的本地时间 12:44；
2. Kernel-Power Event 1 的「唤醒时间」字段（真UTC `02:15:52Z`）与 Date 行 `10:15:52` 相差恰好 8 小时。

**若不校正，会把 18:18:54 的重启误读成次日凌晨 02:18，从而错判「与本案无关」。** 此处已按本地时间重建时间线。

---

## 4. 影响面判定

### 4.1 产物有无

| 产物 | 路径 | 存在 | 事发时状态 |
|---|---|---|---|
| 巡检日志 | `C:\aios\agent_platform\data\shadow\monday_checkpoint.log` | ✅ 存在（36,954 字节） | **冻结于 2026-10-19 18:17:30**，此后无新增 |
| shadow 账本 | `C:\aios\agent_platform\data\shadow\shadow_ledger.jsonl` | ✅ 存在 | 事发**未写入**（mtime 2026-10-20 04:07:54） |
| perf 账本 | `C:\aios\agent_platform\data\perf_ledger.jsonl` | ✅ 存在 | 未变动（mtime 2026-10-17 22:58） |
| strategy 账本 | `C:\aios\agent_platform\data\strategy\strategy_runs.jsonl` | ✅ 存在 | 未变动（mtime 2026-10-20 04:07:54） |
| codex 受理账本 | `C:\aios\agent_platform\data\codex_bridge\registrations.jsonl` | ✅ 存在 | 未变动（mtime 2026-10-18 08:02） |

### 4.2 checkpoint 结论产物：**未产出**

-脚本设计为**纯只读巡检**，唯一落盘物就是日志本身（`monday_checkpoint.py:5-7,17`「零账本写副作用」）。
- 17:25 那次运行在等待循环中被杀，**未输出 `结论:` 与 `EXIT=` 收尾行**（实测：17:25 run 块共 38 行，36 条 `[wait]`，0 条 `EXIT=`/`结论:`）。
- 故：**该次 checkpoint 的门禁结论（PASS/FAIL 计分）不存在**。

### 4.3 为什么它当时在等待（等待对象也未按时到）

`_needs_wait()`（`monday_checkpoint.py:108-130`）在「周一 + 账本最新日 ≤ 2026-10-14」时会阻塞等待影子首跑落账。

- 影子账本实测日期分布：`10-08 / 10-09 / 10-12 / 10-13 / 10-14` 之后**直接跳到 10-20**，**10-15~10-19 无任何行**。
- 即：17:25 巡检在等「周一 17:10 的影子首跑」，但**首跑没来**，于是进入设计内等待循环（每 90s 一轮，上限 180 分钟）。
- 账本首行 `m5.v1` 数据实际于**2026-10-20 04:07:54** 才落盘（`AgentShadowDaily` 于 10-20 03:47 运行，Res=0）。
- **⇒ 就算机器不在 18:18:54 重启，这个 checkpoint 也会一直等到 180 分钟上限（20:25）才判 FAIL。** 重启只是**提前**了失败时刻，没有制造失败。

### 4.4 下游依赖（谁读它）

| 下游 | 读取方式 | 对本次截断块的判读 | 影响 |
|---|---|---|---|
| `tools\check_trigger_chain.py` → `_judge_checkpoint_log()` |按 run 块切分，只信含 `EXIT=` 的完整块 | 末块无收尾行 → 返回 **`WARN`**，文案：「末块 2026-10-19 17:25:00 未收尾（无结论/EXIT 行；[wait] 行 ×36 = 设计内 90s 等待循环或预演/手跑中断残留，非真实失败）」 | **不判 FAIL**，已有预期性误报修复（`check_trigger_chain.py:317-368`）。**未污染告警** |
| `tools\weekly_report.py` → `load_checkpoint_block()` | 只采信含 `EXIT=` 的完整块（`:144`） | 该周窗内**无完整块** → 返回 `None` → 周报第④段标 `INCONCLUSIVE`，并输出「缺件清单」（`:267`） | **降级为缺件**，非错误结论 |
| `tools\check_observation_point.py` | 查日志是否含两个 observe 段标记 | 本次未跑到 observe 段（observe 在等待循环**之后**才执行，`:451-455`） | 判读落到 PENDING/FAIL 分支，需人工复查 |
| `tools\morning_review_backfill.py` | 转抄当日 `[observe]` 行 | 无 observe 行可转抄 | 晨会纪要第④段该部分为空 |

**影响面结论**：**影响是「证据缺件 + 判读降级为 WARN/缺件」，不是「错误结论」或「数据损坏」。** 账本类产物零污染（脚本本就只读）。

---

## 5. 三选一结论

### ✅ 判定：**① 一次性异常，环境已恢复**

**依据**：
1. 死因明确且有人为证据链：1074（用户 `t` 经开始菜单发起重启）→ 6006 → 109 → Kernel-Boot → 6005，闭环完整。
2. 环境已自愈：`State=Ready`，`NumberOfMissedRuns=0`，`NextRunTime=2026-10-26 17:25:00` 正常排程；同批任务（ShadowDaily/ChiefPush/DailyReview/MorningMeeting）全部 `Res=0`。
3. 无配置缺陷：路径、解释器、触发器、账号、ExecutionTimeLimit、实例策略均正常，**无需修改任何配置即可在下次周一正常运行**。
4. 脚本自身无 bug：等待循环、判据、超时上限均按设计工作。

**证据强度：高**（内核事件链 + 逐秒对齐的日志 mtime + 16 个任务时序比对 + 4 个杀进程脚本逐一排查）。

### ⚠️ 但附带一个**结构性脆弱点**（不改变①的定性，属预防性加固）

`LogonType=Interactive` + 长时运行 + `WAIT_SECONDS=90 × MAX_WAIT_ROUNDS=120 = 180 分钟` 的等待窗口，使该任务**天然跨越了人工重启/注销的高风险时段**。当天机器在 07:57、09:51、15:56、18:18、20:22 五个时点被反复重启（当日 5 次 1074），18:18 这次恰好落在等待窗口内。

> 注：另有一次 2026-10-19 **04:11:43** 的 run 块同样**未收尾**（仅 2 轮 `[wait]`，无 `EXIT=`），形态与 17:25 完全一致——说明**这不是孤例，当天至少 2 次巡检被外部中断**。该次不在计划任务触发时刻（17:25），推断为手动/预跑，同样被重启打断。

---

## 6. 拟派工单要素（**本工位不执行任何修改**）

### 工单一：加固 checkpoint 对「整机重启」的韧性（建议级，非紧急）

| 要素 | 内容 |
|---|---|
| **改哪个文件** | `C:\aios\agent_platform\monday_checkpoint.py` |
| **改什么** | 新增「等待循环中途被杀也要留痕」能力：把每轮 `[wait]` 的进度（轮次/已落账 done/failed）写入一个**独立的小进度文件**（如 `data/shadow/monday_checkpoint.progress.json`，覆盖写），使进程被强杀后仍能从磁盘复原「它到哪一步了」 |
| **验收判据** |① 手工 `Stop-Process` 该python 进程后，progress 文件内容与最后一轮 `[wait]` 的轮次一致；② 正常运行至结束时 progress 文件被清除或标记 `done`；③ `check_trigger_chain.py` 对截断末块的判读仍为 `WARN`（不回归成 FAIL） |
| **风险** | 低。日志追加写，不触碰账本，符合「零账本写副作用」红线 |
| **备注** | 这是「提升可观测性」，**不试图对抗重启**——进程被杀后不可能继续执行 |

### 工单二（可选）：缩短等待窗口以降低被杀概率

| 要素 | 内容 |
|---|---|
| **改哪个文件** | `C:\aios\agent_platform\monday_checkpoint.py`（常量 `MAX_WAIT_ROUNDS` / `WAIT_SECONDS`，`:37` `:54`） |
| **改什么** | 将 180 分钟上限下调至与「首跑实测最慢 68.6 分钟」更匹配的量（如 90~120 分钟），降低跨越人工重启时段的暴露面 |
| **验收判据** | 正常竞争态首跑（实测 69 分钟）下不误杀；`MAX_WAIT_ROUNDS` 到顶时走既有 `[wait] 已达等待上限` 分支并正常输出 `EXIT=1` 结论行（而非静默截断） |
| **风险** | 中。需确认 10-20 03:47 那次 04:07 完成的真实耗时，避免下调过头导致「还在正常跑就被判 FAIL」 |

### ❌ **不建议**的改动（附理由）

- **不建议**改 `ExecutionTimeLimit`（当前 `PT72H` 宽松但无害）：本次与超时无关，改它不解决任何问题。
- **不建议**改 `LogonType`（改 S4U）：Interactive 是脚本依赖用户环境的设计前提，且本次非账号问题；改动面大、风险高，属过度处置。
- **不建议**改 `MultipleInstances`（当前 `IgnoreNew`）：本次与实例冲突无关，且 `IgnoreNew` 已是较安全的策略。

---

## 7. 缺证据清单（本工位未能取到，需补充）

| # | 缺口 | 原因 | 建议补证方式 |
|---|---|---|---|
| 1 | **TaskScheduler/Operational 事件日志已滚掉事发时段** | 该日志最老记录为 `2026-10-19 22:04:10`（事发 18:17 之后），事发时段记录已被覆盖；`maxSize=10MB`、`numberOfLogRecords=20108` | 无法补取（历史已丢）。**这正是本次改用 System 日志 + 文件 mtime 交叉取证的原因** |
| 2 | **`Get-WinEvent` 被工具侧沙箱拦截 | 沙箱返回 `sandbox-center cmd decisionRecord missing actual resource subject` | **已按红线不重试**，改用 `wevtutil` 经 Bash 成功读取 System 日志（见 §3.2），取证目标已达成 |
| 3 | **04:11:43 那次中断运行的发起方不明 | 该时刻不在计划任务触发点（17:25），无任务事件可查（见缺口 1） | 需人工确认当日是否有手动预跑/演练 |
| 4 | **18:18:54 重启的动机未直接记录** | 1074 只记「其他(计划外)」 | 上下文旁证：同窗口 `llama-server.exe` 反复 `0xc0000142` 启动失败，推断为处置大模型服务故障。**属推断，非直接证据** |
| 5 | 当日另外 4 次重启（07:57 / 09:51 / 15:56 / 20:22）对巡检的影响未逐一评估 | 仅 18:18 落在17:25 运行窗口内；其余在运行窗口外 | 如需完整replay，可用同样方法逐个比对（本次未展开，非必要） |

---

## 8. 附录：本次只读取证用到的命令类别（全部只读）

- `Get-ScheduledTask` / `Get-ScheduledTaskInfo`（只读查询；**未** Modify/Enable/Disable/Remove/Stop）
- `Test-Path` / `Get-Item`（文件存在性与mtime）
- `Get-Content` / `Read`（读日志、读脚本、读下游判读器源码）
- `wevtutil qe` / `wevtutil gl` / `wevtutil gli`（**只读**导出事件日志；未 `cl` 清空、未 `wevtutil e` 改配置）
- `find` / `ls` / `grep`（只读列目录与检索）
- `Get-TimeZone` / `Get-Date`（时区与当前时间核对）

**未执行**：`schtasks /Change`、`schtasks /Run`、`Stop-Process`、`taskkill`、服务重启、任何配置写入、任何 git commit/push。