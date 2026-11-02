# NoticeBackfill 超时修复回执（PT1H → PT8H）

> 任务：`task-68`（共享任务板 · 车道1，P0）｜执行：wa-stores｜时间：**2026-11-02 14:41 – 14:56（Asia/Shanghai）**
> 依据：`deliverables/2026-11-02_今晚明早自动化窗口预检_v1.md` §7 R2（P0：NoticeBackfill 06:45 超时截断）
> 边界：**只改任务配置 + 本回执**；未改 `kg_notice_llm.py` 业务逻辑、未改 wrapper 脚本、未写 catalog/registry、未 commit、未重启服务。
> 证据：`C:\Temp\task68\`（改动前 XML、PT8H XML、修复/验证/清理日志）。

---

## 0. 结论

| 项 | 结果 |
|---|---|
| 根因 | 任务 `\NoticeBackfill`（06:45 每日）**ExecutionTimeLimit=PT1H**，而单日待判量需约 3–10h → 每天 07:45 被调度器 **267014 终止**（今日只判 **910/11,062**；out 日志 3422s/910 条 ≈ **3.76s/条**）。判定结果按 200 条 flush 持久化，因此每日推进但**永远追不上当日增量+剩余量**。 |
| 修复 | 提高时限 **PT1H → PT8H**（最小改动，优先方案 a）。**未加分批参数**：`--limit` 确实存在，但仅在**非 `--backfill` 模式**生效；`--backfill` 模式内部忽略 limit（脚本 L364-368），要在 wrapper 加 `--limit` 等于无效参数，改代码则违反边界 → 选择 a（详见 §2）。 |
| 权限约束（重要） | 修改**既有任务**需提升权限：`Set-ScheduledTask` / Schedule.Service COM / `schtasks /Create /XML`（含 S4U）均 **Access denied**（当前 token=Medium integrity、审批提示禁用，无法 UAC 提权）。可行路径=`schtasks /Change /DISABLE`（实测可用）+ **新建 PT8H 任务（默认 Interactive principal）**。 |
| 实施 | 新建 `\ZeroBound\NoticeBackfill`（**PT8H**、每日 06:45、同 Action/cwd、IgnoreNew、StartWhenAvailable、允许电池）；**停用旧 `\NoticeBackfill`**（PT1H、S4U，保留可回滚）。两个同名任务分属不同文件夹，明确记录。 |
| 有界验证 | ✅ 14:46:43 `schtasks /Run` → 稳定 **Running 7 分钟**（无秒退/无超时），**380/10,129 条，1.18s/条，fail=0**；进度落盘 45,012→**45,212**（+200，200 条 flush 粒度生效）；14:54:14 主动 End（避免 8h 非窗口长跑）→ LastResult=**267014**（主动终止，预期值，非故障）；已清理 End 遗留的 1 个 python 子进程。 |
| 容量评估 | 验证时待判 **10,129** 条；按实测 1.18s/条 ≈ **3.3h < PT8H（余量 2.4×）**；若按早间最差 3.76s/条 ≈ 10.6h（>8h），但进度持久化 → 最坏跨 2 日收敛。 |
| 冲突 | 06:45+8h=**14:45**，早于当晚 17:30 起窗口（TdxSync 四批 17:35–18:05 等）→ **无冲突**；与 06:45–07:45 的 KG 晨链重叠为**既有**情况（PT1H 时同样重叠），PT8H 仅把尾端延到 14:45（白天轻任务）。 |
| 回滚 | 删除新任务 + `schtasks /Change /ENABLE` 旧任务（§5，30 秒内完成） |

---

## 1. 根因证据（只读复核）

| 证据 | 实测 |
|---|---|
| 任务定义（改前） | `Get-ScheduledTask -TaskName NoticeBackfill`：State=Ready、**ExecutionTimeLimit=PT1H**、MultipleInstances=IgnoreNew、DisallowStartIfOnBatteries=False（允许电池）、Principal=**t/S4U/Limited**、Trigger=每日 06:45、Action=`powershell -NoProfile -ExecutionPolicy Bypass -File "C:\aios\quant_backtest\start_notice_backfill.ps1"`（WorkDir=`C:\aios\quant_backtest`） |
| 最近运行 | LastRun=**2026-11-02 06:45:01**、LastResult=**267014**（0x41306 被终止）、Next=11-03 06:45 |
| 输出日志 | `quant_backtest\logs\notice_backfill.out`（**PowerShell 重定向 UTF-16LE、每次运行 overwrite**）：首行 `G6b 判定: 待判 11062（已判 44075 跳过）`；末行 `910/11062 (3422.7s, 3.76s/条, fail=28)` → **1h 内只判 910 条**，按此时速 11,062 条需 **~11.5h** |
| 进度机制 | `kg_notice_llm.py::run()`：`load_done()` 读 `data/semantica/notice_llm.parquet` 已判 id 跳过；**每 `batch_flush=200` 条落盘**（L215-217）→ 断点续跑有效，但每日 1h 仅 ~900 条，收敛极慢 |
| 判定规模 | `notice_llm.parquet` 改前 **45,012** 行；`events.jsonl` 51MB（61,993 条 notice，规则命中 12,500） |
| 参数核查 | `python kg_notice_llm.py --help`：仅 `--limit/--eval/--backfill`；`--limit` 只在非 backfill 主路径使用（L382），**`--backfill` 分支（L364-368）不读 limit** → 无法用 wrapper 参数做"每次只判 N 条"，除非改代码（边界禁止） |

---

## 2. 修复方案与取舍

| 方案 | 内容 | 结论 |
|---|---|---|
| **a（选用）提高 ExecutionTimeLimit 到 PT8H** | 不动业务代码/参数，让单次跑完当日 backlog（实测 3.3h 量级） | ✅ 本轮执行 |
| b 加显式分批（`--limit N`） | `--limit` 在 `--backfill` 模式无效；非 backfill 模式又不做回填（events.jsonl 不变）→ 需改代码才能实现真分批 | ❌ 未采用（违反"不改业务逻辑"边界），已在回执记录 |
| c 改 wrapper 拆分/重试 | 同 b，且涉及脚本改动+语义变化 | ❌ 不做 |
| 权限替代 | 既有任务 Principal=**S4U**，修改需提升权限；当前 Medium 无法提权（approval prompts disabled） | 用"新建任务 + 停用旧任务"实现同等效果 |

**口径说明**：任务描述建议"优先 a+b 保守组合"，但 b 在本脚本的 `--backfill` 模式下不可行（见上），故本轮只做 a；a 的 8h 上限已由实测覆盖（3.3h，最差 10.6h 跨日）。若 Lead 要求 b，需另开 L1 代码单（给 `--backfill` 加 `--max-minutes/--limit` 参数，属业务逻辑变更）。

---

## 3. 变更清单（配置层，无代码/数据改动）

| # | 对象 | 变更前 | 变更后 | 证据 |
|---|---|---|---|---|
| 1 | **新建** `\ZeroBound\NoticeBackfill` | 不存在 | Daily **06:45**；Action=`powershell -NoProfile -ExecutionPolicy Bypass -File "C:\aios\quant_backtest\start_notice_backfill.ps1"`，WorkDir=`C:\aios\quant_backtest`；**ExecutionTimeLimit=PT8H**；MultipleInstances=IgnoreNew；StartWhenAvailable=True；DisallowStartIfOnBatteries=False；Principal=**t/Interactive/Limited**；State=Ready；Next=11-03 06:45 | `C:\Temp\task68\NoticeBackfill_new_PT8H.xml` |
| 2 | **停用** 旧 `\NoticeBackfill` | Enabled、PT1H、S4U | **Disabled**（定义与 PT1H 原样保留，可一键恢复；`schtasks /Change /ENABLE` 实测可用） | `C:\Temp\task68\NoticeBackfill_before_20261102_144411.xml` |
| 3 | wrapper / 代码 / 数据 | — | **未改**（`start_notice_backfill.ps1`、`kg_notice_llm.py`、catalog、registry 均零改动） | `git status` 无相关改动 |

> 命名说明：旧任务在根目录 `\NoticeBackfill`，新任务在 `\ZeroBound\NoticeBackfill`；`Get-ScheduledTask -TaskName NoticeBackfill` 会同时返回两个，监控脚本需按 TaskPath 区分（建议后续管理员合并回单任务，§6）。

---

## 4. 有界验证（`schtasks /Run` + 7 分钟观察 + 主动 End）

日志：`C:\Temp\task68\validate_20261102_144642.log`、`cleanup_*.log`；14:46:43 触发。

| 时间 | 观测 |
|---|---|
| 14:46:42 | RUN 前基线：out=7,222B（上次运行遗留，overwrite 模式本次将被覆盖）、notice_llm=45,012 行 |
| 14:46:43 | `schtasks /Run /TN "\ZeroBound\NoticeBackfill"` → SUCCESS；5s 后 State=Running，python 进程=1 |
| 14:47:19 → 14:54:14 | **每 30s 采样 14 次全部 State=Running、kg_notice_llm 进程=1、LastResult=267009（运行中）**；out 日志从 222B 持续增长到 **3,004B**（无秒退/无 1h 前异常） |
| 实测进度 | out 首行 `G6b 判定: 待判 10129（已判 45012 跳过）`；末行 `380/10129 (447.6s, 1.18s/条, fail=0)` → **1.18 s/条，0 失败**（远快于早间 3.76s/条，Ollama 空载） |
| 进度落盘 | `notice_llm.parquet` **45,012 → 45,212 行（+200，batch_flush=200 生效）**；mtime=14:50:31 |
| 14:54:14 | 主动 `schtasks /End`（避免非窗口 8h 长跑）→ SUCCESS；State=Ready、LastRun=14:46:43、**LastResult=267014（主动终止，预期值）**、Next=11-03 06:45 |
| 清理 | End 后残留 **1 个 `kg_notice_llm` python 子进程**（任务 End 只杀 wrapper）→ 已 `Stop-Process` 清理，复核残留=0 |
| 其他 | `events.jsonl` 未改动（mtime 仍 07:41，验证在回填步骤前被停止）；Ollama 健康 200；8091/8787 未受影响 |

**验证结论**：新任务配置**可正常调度执行、运行超过 7 分钟无异常、判定链路与进度落盘正常**；PT8H 上限足以覆盖当前 backlog（10,129 条 × 1.18s ≈ 3.3h）。

> 说明：本次验证写入 **+200 条判定结果**（测试性质，属正常业务数据，非污染——判定逻辑与生产一致）；被终止时未 flush 的 <200 条会丢失（by design，下次重判）。

---

## 5. 回滚预案

```powershell
# 1) 删除新任务
Unregister-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'NoticeBackfill' -Confirm:$false
#    或：schtasks /Delete /TN "\ZeroBound\NoticeBackfill" /F
# 2) 重新启用旧任务（PT1H/S4U，定义未改动）
schtasks /Change /TN "NoticeBackfill" /ENABLE
# 3) 复核
Get-ScheduledTask -TaskName 'NoticeBackfill' | Select-Object TaskPath,State
schtasks /Query /TN "\NoticeBackfill" /V /FO LIST | Select-String 'Status|Next Run Time|Task To Run'
```
**回滚影响**：恢复到 PT1H 每天 06:45（继续每日截断，`notice_llm.parquet` 已有进度不丢）；无数据/代码/服务影响。回滚耗时 <30 秒。

---

## 6. 剩余风险 / 待办（需 Lead/管理员）

| # | 风险/待办 | 说明 | 建议 |
|---|---|---|---|
| R1 | **新任务 Principal=Interactive（需登录会话）** | 旧任务 S4U 可无人值守；新任务在用户 `t` 已登录时运行（当前 console session Active、登录于 09:07）。若 06:45 前被注销 → 任务不跑（静默） | **管理员用五要素把 PT8H 写回原 S4U 任务**（`$s=(Get-ScheduledTask -TaskName 'NoticeBackfill').Settings; $s.ExecutionTimeLimit='PT8H'; Set-ScheduledTask -TaskName 'NoticeBackfill' -Settings $s`，需提升权限），然后删除本替代任务 |
| R2 | **最差速度下 8h 仍可能截断** | 早间 06:45 与 KG 晨链并发，速度可能回到 3.76s/条（10,129 条≈10.6h > 8h） | 进度按 200 条持久化，最坏跨 2 日收敛；明早观察 14:45 前的完成情况，若仍截断再评估 PT10H/批处理（L2） |
| R3 | **同名两任务**（\NoticeBackfill 停用 + \ZeroBound\NoticeBackfill 启用） | 监控/审计按名称查询会混（如 `schtasks /Query /TN NoticeBackfill`） | 管理员合并回单任务后消除；期间按 TaskPath 区分，本回执已写明 |
| R4 | **End/终止会留 python 子进程** | 本次已清理；历史上 267014 终止同理会留（旧任务反复出现，可能长期积累） | 建议后续 wrapper 用 `Start-Process -Wait` 或任务设置 `StopIfGoingOnBatteries` 不变的前提下交给管理员核对残留进程（改 wrapper 属另单） |
| R5 | out 日志 **overwrite 模式** | 每次运行覆盖 `notice_backfill.out`，历史只在 `*.gz` 轮转 | 非阻塞；若需窗口复盘建议保留近 N 次（另单） |
| R6 | 判定分母随 events.jsonl 每日重建变化 | 今日 06:45 待判 11,062；14:46 验证时 10,129（已判 45,012→45,212） | 以运行时首行为准，无需处理 |

---

## 7. 证据索引

| 类别 | 路径 |
|---|---|
| 改动前任务 XML（旧 PT1H/S4U） | `C:\Temp\task68\NoticeBackfill_before_20261102_144411.xml` |
| 新任务 XML（PT8H） | `C:\Temp\task68\NoticeBackfill_new_PT8H.xml` |
| 修复过程日志 | `C:\Temp\task68\fix_20261102_144614.log`、`battery_*.log` |
| 有界验证日志 | `C:\Temp\task68\validate_20261102_144642.log`（30s 采样 14 次） |
| 清理日志 | `C:\Temp\task68\cleanup_*.log`（残留 python 1→0） |
| 判定进度（只读核算脚本） | `C:\Temp\task68\progress_check.py` / `progress_before.json` |
| 任务定义 | `schtasks /Query /TN "\ZeroBound\NoticeBackfill" /V /FO LIST`；`schtasks /Query /TN "\NoticeBackfill" /V /FO LIST` |
| 运行日志 | `C:\项目\老田的AI助手\quant_backtest\logs\notice_backfill.out`（UTF-16LE）、`notice_backfill_status.log`、`notice_backfill.err` |
| 进度文件 | `C:\项目\老田的AI助手\quant_backtest\data\semantica\notice_llm.parquet`（45,212 行）、`events.jsonl`（未改） |
