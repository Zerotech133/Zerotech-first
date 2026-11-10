# L2 申请：dw_snapshot 每日刷新计划任务（ZeroBound\DataWarehouseSnapshot · 每日 06:40）

> STATUS: pending_approval（L2：新增 Windows 计划任务 + 每日重写 `portal/static/dw_snapshot.json`）
>
> 2026-11-02 ｜ 申请人：DSH-Lead（车道2）｜ 批准人：A0/老田 ｜ 类型：L2（计划任务新增）
>
> 关联：S1-06「dw_snapshot 每日任务/替代 cron + 一致性校验」（DoD：generated_at=当日连续 3 天）；S1-01「8091 重启 + dw_snapshot 06:40 计划任务」。
>
> **本轮范围声明：只交脚本 + 本申请材料；未创建/修改任何 Windows 计划任务（按纪律留待 A0/老田批准后 L2 执行）。**

## 一、命令全文（批准后执行）

### 0) 安装前检查（只读，防覆盖）

```powershell
schtasks /Query /TN "ZeroBound\DataWarehouseSnapshot"    # 期望：找不到 → 可新建；已存在则先评审是否同任务
Get-Content C:\Temp\dw_snapshot_daily.log -Tail 1        # 手动预演日志（见第六节回执）
```

### 1) 手动预演（已实测通过，批准前后均可重跑）

```powershell
cd C:\项目\老田的AI助手
C:\项目\py312\python.exe -X utf8 tools\dw_snapshot_daily.py
# 期望输出 RESULT: OK rc=0；日志追加到 C:\Temp\dw_snapshot_daily.log
```

### 2) 安装任务（管理员 PowerShell；建议 A0/老田批准后由 L2 执行）

```powershell
$py     = "C:\项目\py312\python.exe"
$script = "C:\项目\老田的AI助手\tools\dw_snapshot_daily.py"
$wd     = "C:\项目\老田的AI助手"

$action  = New-ScheduledTaskAction -Execute $py `
             -Argument ('-X utf8 "' + $script + '"') -WorkingDirectory $wd
$trigger = New-ScheduledTaskTrigger -Daily -At "06:40"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
             -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries
# 账号口径：对齐现有 ZeroBound_Daily* 任务（当前均为当前登录账号交互式；安装时用
# Get-ScheduledTask -TaskName ZeroBound_DailyHealth | Select -Expand Principal 核对后同一口径）
Register-ScheduledTask -TaskPath "\ZeroBound\" -TaskName "DataWarehouseSnapshot" `
  -Action $action -Trigger $trigger -Settings $settings `
  -Description "零界·数据仓 dw_snapshot 每日刷新+一致性校验（S1-06）"
```

### 3) 安装后验证（L2 执行人留证）

```powershell
schtasks /Query /TN "ZeroBound\DataWarehouseSnapshot" /V /FO LIST   # 状态/上次结果/下次运行
schtasks /Run   /TN "ZeroBound\DataWarehouseSnapshot"
Start-Sleep -Seconds 45
schtasks /Query /TN "ZeroBound\DataWarehouseSnapshot" /FO LIST      # 期望 LastTaskResult=0
Get-Content C:\Temp\dw_snapshot_daily.log -Tail 1                   # 期望 "rc": 0 且 generated_at=当日
C:\项目\py312\python.exe -X utf8 agent_platform\tools\task_exit_audit.py --scan  # 退出码账本可审计
```

### 4) 验收口径（S1-06 DoD）

- `portal/static/dw_snapshot.json` 的 `generated_at` = 当日，**连续 3 天**；
- 每日日志 1 行 JSON，`rc=0`；失败（dashboard 非 0 / 校验失败）`rc=1`，计划任务 LastTaskResult 非 0 → 可被退出码审计捕获（fail-visible）；
- snapshot 内 `catalog.version` 与当前 `quant_backtest/data/catalog.json` 一致，`lagging_daily` 结构与数量可核对。

## 二、影响面（变更范围 / 系统资源 / 数据）

- **时间/资源**：每日 06:40 单次执行，实测耗时 **19.1s**（首测 27.1s，含 dashboard 内 automation_supervisor 子进程）；CPU/内存峰值低（读 catalog/manifest/backup 目录 + 少量 parquet footer + sqlite 只读）。
- **写文件（2 处，均为既定产物）**：
  1. `portal/static/dw_snapshot.json` —— dashboard 既定产物、git 已跟踪（每次运行产生 diff；页面读它，属预期刷新）；
  2. `C:\Temp\dw_snapshot_daily.log` —— JSONL 追加，一次一行（建议随日志轮转策略保留，非必需）。
- **读**：`quant_backtest/data/catalog.json`、`warehouse_registry.json`、`backups/`、`portal/`、`work/` 下只读状态文件（全部只读）。
- **不触碰**：8787/8091 等常驻服务（不重启、不热载）、数据仓真源（不写 parquet/sqlite）、catalog/registry、计划任务外的系统配置；无新增端口、无新常驻进程。
- **时点依据**：06:40 在 06:00 X 系列增量闭环之后、开盘前，保证快照含当日最新状态；与 S1-01 既定「dw_snapshot 06:40」一致。避免与 06:30 前重任务叠加，故取 06:40。
- **命名口径**：任务书建议 `ZeroBound\DataWarehouseSnapshot`；现有同类任务为平铺 `\ZeroBound_<Name>`（如 `\ZeroBound_DailyHealth`）。若 A0 要求统一平铺，等价改名为 `\ZeroBound_DataWarehouseSnapshot`，脚本/触发不变。

## 三、不可逆点

- **无不可逆点**。任务安装可删除；脚本只写 2 个非真源文件（snapshot 为 dashboard 既有产物、日志为新追加文件），不触碰数据仓真源与凭据。
- 唯一持久副作用：每日覆盖式重写 `dw_snapshot.json`（旧内容不保留）——如需历史，依赖 git 或备份；页面/接口消费的是最新快照，符合设计。
- 提醒：覆盖既有同名任务之前必须先 `schtasks /Query` 查重（已列入命令第 0 步）。

## 四、替代方案与已尝试

| 方案 | 说明 | 结论 |
|---|---|---|
| **A（已选）Windows 计划任务 + 本脚本** | 沿用 ZeroBound_Daily* 既有任务体系；退出码可审计（task_exit_audit） | ✅ 选型 |
| B 外部调度器 / Hermes governance_clock | 平台无可直接写文件的常驻 cron；引入新常驻进程扩大影响面 | ❌ 不选 |
| C 手动刷新 + 值班提醒 | 现状即「手动刷新、会再旧」（11-02 00:18 手动刷新），不满足 S1-06 DoD | ❌ 已试过，放弃 |

已尝试/已留证：脚本本地实跑 rc=0（见第六节）；pytest 5 用例通过；py_compile 通过。**计划任务未安装**（本轮纪律）。

## 五、回滚预案（按序执行）

1. 卸载任务：
   `Unregister-ScheduledTask -TaskPath "\ZeroBound\" -TaskName "DataWarehouseSnapshot" -Confirm:$false`
   （等价：`schtasks /Delete /TN "ZeroBound\DataWarehouseSnapshot" /F`）
2. 确认快照仍可用：页面 `dw_snapshot` 区正常；`portal/static/dw_snapshot.json` 不随任务删除而删除（保留最后一份）。
3. 若脚本回归导致 snapshot 损坏：`git checkout -- portal/static/dw_snapshot.json` 恢复入库版本，或手动重跑 `python tools/dw_dashboard.py` 再生成；
4. 日志保留 `C:\Temp\dw_snapshot_daily.log` 供复盘；如需停用但保留任务，可 `Disable-ScheduledTask`（单向不可自动恢复，需显式启用）。

## 六、本地预演回执（2026-11-02，DSH-Lead 车道2）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 语法 | `python -m py_compile tools/dw_snapshot_daily.py tools/datawarehouse/tests/test_dw_snapshot_daily.py` | rc=0 |
| 单测 | `python -X utf8 -m pytest -q tools/datawarehouse/tests/test_dw_snapshot_daily.py` | **5 passed**（成功刷新结构 / generated_at 非当日 / catalog 版本漂移 / dashboard 失败 / lagging 上限与结构） |
| 实跑 | `python -X utf8 tools/dw_snapshot_daily.py` | **rc=0**，耗时 19.1s；10 项校验全过 |
| 快照 | 实跑后 `portal/static/dw_snapshot.json` | `generated_at=2026-11-02T04:38:11+08:00`（当日）；`catalog.version=18`；`catalog.tables=256`；`lagging_daily=19` |
| 日志 | `C:\Temp\dw_snapshot_daily.log` 末行 | `{"tool": "dw_snapshot_daily", ..., "generated_at": "2026-11-02T04:38:11+08:00", "catalog_version": 18, "lagging_count": 19, "ok": true, "rc": 0}` |

> 备注：`lagging_daily=19` 为快照字段如实记录（源侧数据滞后项，如 Tushare 部分表 lag=3），不是本任务失败判据；如需把滞后数纳入告警，可在批准后用 `--max-lagging N` 阈值化（脚本已支持，默认不设限）。
