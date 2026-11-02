# GlobalFutSync 每日任务安装回执 + TdxSync 三批配置核对

> 任务：`task-42`（共享任务板 · 车道3，L2 计划任务增改）｜执行：wa-stores｜时间：**2026-11-02 10:16:49 – 10:19:54（Asia/Shanghai）**
> 依据：task-42、`deliverables/2026-11-02_L2待批材料_v1.md` §1.3-2、`docs/audit/2026-11-02_xtcloud停更排查.md`
> 边界：**只做计划任务增改 + 回执**——未改数据/代码（验证性触发所写产物=该任务自身设计产出 `data/tdx/ts_global_fut/`）、未 commit、未重启任何服务。
> 证据：`C:\Temp\global_fut_sync_install_20261102_101649.log`、`C:\Temp\global_fut_sync_verify_20261102_101700.log`、`C:\Temp\GlobalFutSync_installed_20261102_101649.xml`、`C:\Temp\global_fut_sync_trigger_fix_20261102_102117.log`。

---

## 0. 结论

| 项 | 结果 |
|---|---|
| ① TdxSync 三批配置核对（`\ZeroBound\TdxSync_A_高频/B_财务/C_低频`） | ✅ **已存在且配置合规**：Trigger **17:35/17:40/17:45**、Action=`py311 -X utf8 ...tdx_sync.py --tables <批>`、cwd=`quant_backtest`、ExecutionTimeLimit **PT2H**、MultipleInstances **IgnoreNew**、User=`t`（Interactive/Limited）、State=Ready、NextRun=今日 17:35/17:40/17:45；⚠️ 三任务 `DisallowStartIfOnBatteries=True`（本机为笔记本，见 §5 R1） |
| ② GlobalFutSync 安装 | ✅ 新建 `\ZeroBound\GlobalFutSync`：每日 **17:50**、Action=`C:\Program Files\Python311\python.exe -X utf8 "…\quant_backtest\global_fut_sync.py"`（**无 `--quick`**）、Start In=`quant_backtest`、ExecutionTimeLimit **PT30M**、MultipleInstances **IgnoreNew**、User=`t`（Interactive/Limited）；**安装前无同名任务**（无旧配置覆盖）。⚠️ **异常与复位**：安装后 10:17–10:20 该任务 Trigger 被**外部会话**改为 18:15（安装时 XML 备份为 17:50，见 §2 “外部修改异常”）；已按 task-42 规格**重置回 17:50**（10:21:18），45s 复核稳定、NextRun=今日 17:50 |
| ③ 触发验证 | ✅ `schtasks /Run` 后 **State=Running 160s → LastTaskResult=0**（LastRun=2026-11-02 10:17:00）；数据落盘证据：4 个 parquet mtime **10:17:32–10:19:45**、`_manifest.json` 4 品种 `fetched_at=2026-11-02 10:17:00`、`max=20261102`（今日） |
| ④ 回滚 | `schtasks /Delete /TN "\ZeroBound\GlobalFutSync" /F`；配置已导出 XML 备份（`C:\Temp\GlobalFutSync_installed_20261102_101649.xml`） |

---

## 1. TdxSync 三批任务配置核对（Read-Only 实测）

> 采集命令：`Get-ScheduledTask -TaskPath '\ZeroBound\' -TaskName <name>` + `Get-ScheduledTaskInfo`；时间：2026-11-02 10:16:3x。

| 项 | TdxSync_A_高频 | TdxSync_B_财务 | TdxSync_C_低频 |
|---|---|---|---|
| TaskPath | `\ZeroBound\` | `\ZeroBound\` | `\ZeroBound\` |
| State | Ready | Ready | Ready |
| Trigger | Daily，**17:35** | Daily，**17:40** | Daily，**17:45** |
| 下次运行 | 2026-11-02 17:35:00 | 2026-11-02 17:40:00 | 2026-11-02 17:45:00 |
| Action.Execute | `C:\Program Files\Python311\python.exe` | 同左 | 同左 |
| Action.Arguments | `-X utf8 "…\quant_backtest\tdx_sync.py" --tables ts_daily,ts_daily_basic,ts_index_daily,ts_daily_info,ts_moneyflow,ts_margin,ts_margin_detail,ts_limit_cpt_list,ts_limit_step,ts_stk_limit,ts_stk_auction_c,ts_cyq_perf,ts_hk_daily,ts_fx_daily,ts_fut_daily,ts_opt_daily,ts_repo_daily,ts_sge_daily`（18 表） | `… --tables ts_income,ts_income_vip,ts_balancesheet,ts_balancesheet_vip,ts_cashflow,ts_cashflow_vip,ts_fina_indicator,ts_fina_indicator_vip,ts_fina_mainbz,ts_forecast,ts_forecast_vip,ts_express,ts_express_vip,ts_dividend,ts_dividend_vip,ts_disclosure_date,ts_fina_audit,ts_stk_holdernumber`（18 表） | `… --tables ts_stock_basic,ts_hk_basic,ts_index_basic,ts_index_classify,ts_index_member_all,ts_index_monthly,ts_index_weekly,ts_index_weight,ts_kpl_concept,ts_ths_member,ts_tdx_index,ts_trade_cal,ts_suspend_d,ts_namechange,ts_repurchase,ts_stk_managers,ts_stk_rewards,ts_top10_holders`（18 表） |
| Action.WorkingDirectory | `C:\项目\老田的AI助手\quant_backtest` | 同左 | 同左 |
| ExecutionTimeLimit | **PT2H** | **PT2H** | **PT2H** |
| MultipleInstances | **IgnoreNew** | IgnoreNew | IgnoreNew |
| StartWhenAvailable | True | True | True |
| DisallowStartIfOnBatteries | **True**（见 R1） | True | True |
| Principal | UserId=`t`，LogonType=Interactive，RunLevel=Limited | 同左 | 同左 |
| LastRun / LastResult | 从未运行（1999-11-30 占位）/ **267011**（=`SCHED_S_TASK_HAS_NOT_RUN`，非失败） | 同左 | 同左 |
| 旧 `\TdxSync`（--all 单跑） | **State=Disabled**（Action 仍为 `start_tdx_sync.ps1`；两轮 2h 终止的旧链已停用，可回滚重新 ENABLE） | — | — |

**核对结论**：三批与 `deliverables/2026-11-02_L2待批材料_v1.md` §1.3-2 的拆批设计一致（17:35/17:40/17:45、py311、`--tables`、cwd、2h、IgnoreNew、User 口径）——**无需改动**；唯一提示是三任务电池策略为 True（本机 1 块电池，见 §5 R1）。

---

## 2. GlobalFutSync 任务配置（安装后实测）

> 安装命令（实际执行）：`Register-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'GlobalFutSync' -Action $action -Trigger $trigger -Settings $settings -Description $desc -Force`；其中 `$action = New-ScheduledTaskAction -Execute 'C:\Program Files\Python311\python.exe' -Argument '-X utf8 "C:\项目\老田的AI助手\quant_backtest\global_fut_sync.py"' -WorkingDirectory 'C:\项目\老田的AI助手\quant_backtest'`；`$trigger = New-ScheduledTaskTrigger -Daily -At '17:50'`；`$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries`。

| 配置项 | 实测值（`Get-ScheduledTask` / `schtasks /Query /V`） |
|---|---|
| 任务名/路径 | `GlobalFutSync` @ `\ZeroBound\` |
| 安装前状态 | **不存在**（新建，无旧任务覆盖；无需旧配置备份） |
| 安装后 State | **Ready** |
| Trigger | Daily，**2026-11-02T17:50:00+08:00**，Enabled=True |
| 下次运行 | **2026/11/2 17:50:00** |
| Action.Execute | `C:\Program Files\Python311\python.exe` |
| Action.Arguments | `-X utf8 "C:\项目\老田的AI助手\quant_backtest\global_fut_sync.py"`（**无 `--quick`**；脚本默认 full：`fetch_history(y0=2010)` 逐年分窗，4 品种） |
| Start In（WorkingDirectory） | `C:\项目\老田的AI助手\quant_backtest` |
| ExecutionTimeLimit | **PT30M** |
| MultipleInstances | **IgnoreNew** |
| StartWhenAvailable | True |
| DisallowStartIfOnBatteries | True（默认值；台式/插电场景无影响，笔记本场景见 R1） |
| Principal | UserId=`t`，LogonType=**Interactive**，RunLevel=**Limited**（与三批任务/人类任务口径一致） |
| 描述 | 外盘商品期货日线采集（东方财富源；全量4品种，禁止--quick；每日17:50；2026-11-02 L2 安装） |
| XML 备份 | `C:\Temp\GlobalFutSync_installed_20261102_101649.xml`（`schtasks /Query /TN "\ZeroBound\GlobalFutSync" /XML`） |
| 环境预检 | py311 = **Python 3.11.9**，pandas **3.0.1**、pyarrow **26.0.0** 均在位（`to_parquet` 可用；安装前已预检） |

### 2.1 ⚠️ 外部修改异常与复位（必须由 Lead 确认）

| 时间 | 事件 | 证据 |
|---|---|---|
| 10:16:50 | 安装完成，Trigger=`2026-11-02T17:50:00+08:00`，NextRun=17:50 | 安装日志 + `C:\Temp\GlobalFutSync_installed_20261102_101649.xml`（内 `<StartBoundary>2026-11-02T17:50:00+08:00</StartBoundary>`） |
| 10:17:00–10:20:51 之间 | Trigger 被**外部会话/进程**改为 **18:15**（描述、Action、Settings 均未变，仅 StartBoundary 变） | 10:20:51 复核：`StartBoundary=2026-11-02T18:15:00+08:00`、`Start Time: 18:15:00`、NextRun=18:15；非本席位操作（本席位期间仅执行 `/Run` 与读取） |
| 10:21:18 | 按 task-42 规格**复位为 17:50**：`Set-ScheduledTask -Trigger (New-ScheduledTaskTrigger -Daily -At '17:50')` | `C:\Temp\global_fut_sync_trigger_fix_20261102_102117.log` |
| 10:21:22 / 10:22:09 | 复核：`StartBoundary=2026-11-02T17:50:00+08:00`、NextRun=今日 **17:50**、State=Ready、LastResult=0；45s 后再次复核**仍为 17:50**（未见二次回改） | 同上日志 |

> **请 Lead 裁定**：若 18:15 是**其他车道/会话有意**为避开 TdxSync_C（17:45 起，2h 上限，最长跑到 19:45）的资源竞争而调整，请明确最终时点（17:50 vs 18:15），本席位按批准值再改一次即可；在收到裁定前，本回执以 **task-42 规格的 17:50** 为准。另：TaskScheduler Operational 事件日志不可用（无事件），无法从日志定位改动人/进程。

---

## 3. 触发验证（`schtasks /Run` → LastTaskResult=0）

### 3.1 时间线（日志 `C:\Temp\global_fut_sync_verify_20261102_101700.log`）

> 注：`schtasks /Run` 为手动触发，不受 Trigger 时点影响；本次运行与 §2.1 的 Trigger 被外部改动（10:17–10:20）互不影响，`LastTaskResult=0` 有效。

| 时间（CST） | 事件 |
|---|---|
| 10:17:00.020 | 验证开始；BEFORE：LastRun=1999-11-30（从未运行）、LastResult=267011 |
| 10:17:00.288 | `schtasks /Run /TN "\ZeroBound\GlobalFutSync"` → `SUCCESS: Attempted to run…` |
| 10:17:11 – 10:19:43 | 轮询：**State=Running**（t+10s … t+150s，每 10s 一次，共 15 次） |
| 10:19:54.254 | **State=Ready，LastRun=2026-11-02 10:17:00，LastTaskResult=0**（耗时 ≈ **160s**，符合预期的 2.5–3min） |
| 10:19:54 | AFTER：`schtasks /Query /V` 摘要 `Last Result: 0`、`Next Run Time: 2026/11/2 17:50:00` |

### 3.2 数据落盘证据（任务真实产出，非人工构造）

| 产物 | BEFORE（安装前 10:16:49） | AFTER（10:19:54） | 变化 |
|---|---|---|---|
| `GF_HG00Y.parquet` | 210,228B / 10:07:01 | 210,228B / **10:17:32** | 重写 |
| `GF_GC00Y.parquet` | 239,956B / 10:07:31 | 239,956B / **10:18:00** | 重写 |
| `GF_CL00Y.parquet` | 236,803B / 10:08:01 | 236,795B / **10:18:46** | 重写 |
| `GF_PA00Y.parquet` | 186,913B / 10:08:56 | 186,913B / **10:19:45** | 重写 |
| `_manifest.json` | 1,321B / 10:08:56 | 1,321B / **10:19:46**，4 品种 `fetched_at=2026-11-02 10:17:00`、`max=20261102` | 更新 |

`_manifest.json` 关键字段（本次运行写入）：
```
GF.HG00Y COMEX铜   ok=true rows=3914 min=20110715 max=20261102 fetched_at=2026-11-02 10:17:00
GF.GC00Y COMEX金   ok=true rows=4338 min=20100104 max=20261102 fetched_at=2026-11-02 10:17:00
GF.CL00Y NYMEX原油 ok=true rows=4319 min=20100104 max=20261102 fetched_at=2026-11-02 10:17:00
GF.PA00Y NYMEX钯金 ok=true rows=3448 min=20130624 max=20261102 fetched_at=2026-11-02 10:17:00
（另有 4 个历史遗留键 GF.SI00Y/GF.AHD/GF.ZSD/GF.CAD = ok:false，见 R4，不影响本次 4 品种）
```

> **日志说明**：按 task-42 指定的 Action 原样安装（py311 直启、无重定向），计划任务运行时的 stdout 不会落盘；本次“日志落 C:\Temp”以**验证日志**（安装/状态轮询/`schtasks /Query /V` 摘要/清单与 manifest 快照）满足。若后续需要任务运行 stdout 留存，可另批将 Action 改为 `cmd /c ""py311" … >> C:\Temp\global_fut_sync_task.log 2>&1"`（属 Action 变更=L2，需另批）。

---

## 4. 回滚预案

```powershell
# 1) 删除任务（新装任务，无旧配置需恢复）
schtasks /Delete /TN "\ZeroBound\GlobalFutSync" /F
# 2) 验证已删除
schtasks /Query /TN "\ZeroBound\GlobalFutSync"     # 预期：ERROR 找不到
# 3) 如需恢复（回滚本次删除）：用安装时 XML 重新注册
schtasks /Create /TN "\ZeroBound\GlobalFutSync" /XML "C:\Temp\GlobalFutSync_installed_20261102_101649.xml" /F
# 4) 数据侧无需回滚：脚本只写自有表 data/tdx/ts_global_fut/（4 parquet + _manifest.json），
#    不触碰既有生产表（脚本 docstring 纪律），且数据可重复采集覆盖。
```
**回滚影响**：仅失去每日 17:50 自动更新（ts_global_fut 回到“无调度、需人工补采”状态）；不影响其他任务/服务/数据。

---

## 5. 影响面与风险

| 项 | 说明 |
|---|---|
| 运行时点 | 每日 **17:50**，与 TdxSync 三批（17:35/17:40/17:45）**错峰 5 分钟**；距 KgDailySync(06:20)/dw_snapshot(06:40) 等无冲突 |
| 资源/网络 | 单次 ≈ **160s**（实测），访问东方财富 `*.push2his.eastmoney.com`（4 品种 × 2010→2027 年窗，`SLEEP=0.7/0.8s` 自律）；30min 上限足够；IgnoreNew 防叠跑 |
| 数据写入 | 仅 `data/tdx/ts_global_fut/`：4 个 `GF_<qcode>.parquet` + `_manifest.json`（覆盖式重写；脚本声明“只写自己的新表，不碰既有生产表”） |
| 与 TdxSync 关系 | 独立任务/独立产物；`ts_global_fut` 不在 xtcloud 181 表内（本地自产），不受 TdxSync 拆批影响 |
| 权限 | User=`t`/Interactive/Limited（非提权）；非管理员运行 |
| R1 风险 | **本机为笔记本（`Win32_Battery` 1 块）**：四任务（GlobalFutSync + TdxSync 三批）均 `DisallowStartIfOnBatteries=True` → **若 17:50 时在电池供电，任务不会启动**（静默不跑，NextRun 顺延次日）。缓解：保持插电；如确需电池运行，可另批把这 4 个任务设为 `-AllowStartIfOnBatteries`（L2 改任务，需批准） |
| R2 风险 | 东财 host 级限流（10-15 判例）：脚本已内置多 host 轮转+指数退避；本次运行 0 失败（4/4 ok） |
| R3 风险 | 若东财接口变更/secid 失效：manifest 会记 `ok:false`，任务 LastTaskResult 仍为 0（脚本对单品种失败返回 0）→ 需 watchdog 检查 manifest `ok` 比例（建议后续接入监控） |
| R4 观察 | `_manifest.json` 保留 4 个历史遗留键（`GF.SI00Y/GF.AHD/GF.ZSD/GF.CAD`，`ok:false`），来自 10-15 旧版本；本次 4 品种不涉及，无害；如需可另单清理 |
| R5 观察 | 本次验证触发写入了今日数据（max=20261102，含今日盘中不完整日线），属任务正常行为；17:50 正式跑会再覆盖一次 |
| R6 风险（并发写入） | **Trigger 在安装后被外部会话改为 18:15，已复位 17:50**（§2.1）。多车道并行期间共享计划任务可能被再次改动 → 建议 Lead 复核准入时点（`schtasks /Query /TN "\ZeroBound\GlobalFutSync" /V /FO LIST` 看 Start Time） |

---

## 6. 未验证 / 边界

1. **首次自动触发（今日 17:50）尚未发生**：本次为手动 `/Run` 验证；17:50 自动触发的 LastTaskResult 需执行后观察（建议 Lead 晚间复核）。
2. **未验证电池场景**：本机有电池，R1 的 `DisallowStartIfOnBatteries=True` 行为未实测（未在电池状态等待 17:50）。
3. **未改数据/代码**：除任务自身运行写入 `ts_global_fut` 产物外，无任何人工数据/代码改动；未 commit、未重启服务。
4. **未验证 stdout 落盘**：按 spec 的 Action 无重定向，任务 stdout 不落盘（见 §3.2 日志说明；建议见该段）。
5. XML 备份仅在 `C:\Temp`（易失）；如需长期留存建议 Lead 决定是否归档到 `deliverables/`（本单 writeScope 仅回执，未做）。
6. **Trigger 最终值（17:50）以本回执 §2.1 的复位后实测为准**；因期间出现外部改动，建议 Lead 在今日 17:50 前再核一次 `Start Time`（命令见 §7），若 18:15 是并行车道有意调整，请按批准值再改。

---

## 7. 复现/复核命令

```powershell
# 三批配置
Get-ScheduledTask -TaskPath '\ZeroBound\' | Where-Object { $_.TaskName -like 'TdxSync_*' } |
  ForEach-Object { $i=Get-ScheduledTaskInfo -TaskPath $_.TaskPath -TaskName $_.TaskName;
    [pscustomobject]@{Name=$_.TaskName;State=$_.State;Trigger=$_.Triggers[0].StartBoundary;
      Args=$_.Actions[0].Arguments;Limit=$_.Settings.ExecutionTimeLimit;MI=$_.Settings.MultipleInstances;
      User=$_.Principal.UserId;Logon=$_.Principal.LogonType;Next=$i.NextRunTime;LastResult=$i.LastTaskResult} } |
  Format-List
# GlobalFutSync 配置
schtasks /Query /TN "\ZeroBound\GlobalFutSync" /V /FO LIST
# 期望：Start Time 17:50:00；Task To Run ...py311 -X utf8 ...global_fut_sync.py；Start In ...quant_backtest
# 若 Start Time 被并发会话改动，按 task-42 规格复位（只改 Trigger，保留 Action/Settings）：
#   $t = Get-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'GlobalFutSync'
#   Set-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'GlobalFutSync' -Trigger (New-ScheduledTaskTrigger -Daily -At '17:50')
# 触发验证（重新触发一次，无参数=full）
schtasks /Run /TN "\ZeroBound\GlobalFutSync"
# 状态/结果
Get-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'GlobalFutSync' | Select-Object State
Get-ScheduledTaskInfo -TaskPath '\ZeroBound\' -TaskName 'GlobalFutSync' | Select-Object LastRunTime,LastTaskResult,NextRunTime
# 产物/manifest
Get-ChildItem C:\项目\老田的AI助手\quant_backtest\data\tdx\ts_global_fut | Select-Object Name,Length,LastWriteTime
Get-Content C:\项目\老田的AI助手\quant_backtest\data\tdx\ts_global_fut\_manifest.json -Raw -Encoding UTF8
# 回滚
schtasks /Delete /TN "\ZeroBound\GlobalFutSync" /F
```
