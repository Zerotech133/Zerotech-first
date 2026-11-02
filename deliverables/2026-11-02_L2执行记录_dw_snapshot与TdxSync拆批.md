# L2 执行记录：dw_snapshot 计划任务安装 + TdxSync 拆批止血（2026-11-02 10:10）

> 依据：老田「按计划组织实施」+ `deliverables/2026-11-02_L2待批材料_v1.md` 五要素；执行：DSH-Lead

## 一、dw_snapshot 每日任务（已安装并验证）

| 项 | 结果 |
|---|---|
| 任务 | `\ZeroBound\DataWarehouseSnapshot`（每日 06:40，20 分钟上限，IgnoreNew） |
| Action | `C:\项目\py312\python.exe -X utf8 "C:\项目\老田的AI助手\tools\dw_snapshot_daily.py"`，cwd=主仓 |
| 注册 | ✅ `REGISTER_OK`；Next Run=2026-11-03 06:40；Status=Ready |
| 触发验证 | 首次触发异常 `0xC000013A`（无日志），**第二次触发 LastTaskResult=0**，日志新增 10:06:47/24.8s 记录 |
| 验收 | `generated_at`=当日、catalog v20/256、lagging=19、10 项校验全过 |
| 回滚 | `Unregister-ScheduledTask -TaskName DataWarehouseSnapshot -TaskPath \ZeroBound\` |

## 二、TdxSync 拆批止血（已执行）

**前置校核（task-39，10:05–10:06 实测）**：
- DNS 已恢复：`rookie.xtcloud-tech.com → 218.70.196.246`（2/2 成功）；TCP 48088 OK；HTTP GET `/api/data/status` 200；
- **11-01 失败主因不是 DNS**（errno11001 最后写于 10-21 历史残留），而是 **2h 超时终止（Last Result 267014）** + **HTTP200 零行软失败**（日志 1,281 条假完成拦截，约 77 表）；
- 结论：**无需钉 hosts**；必须拆批 + 零行表记录交 DATA1。

| 动作 | 结果 |
|---|---|
| 备份 | `C:\Temp\tdx_fix_20261102_100832\`（旧任务 XML/文本、start_tdx_sync.ps1、tdx_sync_task.log） |
| 停旧任务 | `schtasks /Change /TN "\TdxSync" /DISABLE` → Status=**Disabled**（可 `/ENABLE` 回滚） |
| 新建 A 批 | `\ZeroBound\TdxSync_A_高频` 17:35（18 表：daily/daily_basic/index_daily/moneyflow/margin/hk_daily/fx_daily/fut_daily/opt_daily 等） |
| 新建 B 批 | `\ZeroBound\TdxSync_B_财务` 17:40（18 表：income/balancesheet/cashflow/fina_indicator/fina_mainbz/forecast/express/dividend 等，含 _vip） |
| 新建 C 批 | `\ZeroBound\TdxSync_C_低频` 17:45（18 表：stock_basic/hk_basic/index_*/kpl_concept/ths_member/trade_cal/suspend_d/namechange/repurchase 等） |
| 验证 | 3 任务 Status=Ready，Next Run 今日 17:35/17:40/17:45 |
| 回滚 | `schtasks /Change /TN "\TdxSync" /ENABLE` + `schtasks /Delete /TN "\ZeroBound\TdxSync_*" /F` |

**未执行（需低峰窗口）**：50 张停滞表的补采（3 批 17/17/16，命令见 L2 材料 §1.3-4）——今晚 17:35 三批先验证增量，若 OK 再排补采；`global_fut_sync.py` 每日任务（另需 L2）。

## 三、11-05 窗口（已登记提醒）

- 任务：8091 受控重启（加载 DWH-105 + S1-04 F3/F4）+ nginx `/api/dw` 白名单与 `/dw.html` 接线 + 检查 dw_snapshot/TdxSync 首夜结果；
- 提醒：`schedule-508baedd`（2026-11-05 06:00 Asia/Shanghai）。