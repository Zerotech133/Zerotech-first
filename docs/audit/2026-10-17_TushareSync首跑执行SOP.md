# TushareSync 首跑执行步骤 SOP

> 生成：2026-10-17 05:5x
> 状态：**首跑已于 2026-10-17 01:40:23 成功完成**（`exit=0`，耗时 2h10min）——本档为可复用 SOP + 首跑实况存档
> 适用：新机迁移后的首跑、以及每晚 23:30 例行增量轮（步骤通用，仅耗时预期不同）

---

## 一、组件契约速览

| 项 | 值 |
|---|---|
| 计划任务 | `TushareSync`（每晚 23:30 触发，S4U 会话） |
| 动作链 | start_tushare_sync.ps1（wrapper）→ tushare_sync.py → 落盘 parquet |
| 契约行 | `tushare_sync exit=N <ISO时间>` → `C:\Temp\tushare_sync_task.log`（**唯一权威结果判定源**） |
| 数据目录 | `C:\aios\quant_backtest\data\tushare\`（50 表子目录 + `_manifest.json`） |
| 断点续传 | 文件存在即跳过（幂等，重跑安全） |
| 凭据 | `.env.local` 的 `XT_DATA_KEY`（明文，无 DPAPI） |
| 失败告警 | alert_fail.py 自动推飞书（S4U 已验证可用，无需人工值守发现失败） |

---

## 二、执行步骤（S1-S5）

### S1 预检（触发前 30 分钟，~3 分钟完成）

```powershell
# ① 任务就绪态（期望 Ready / rc=0）
(Get-ScheduledTask -TaskName 'TushareSync').State
(Get-ScheduledTaskInfo -TaskName 'TushareSync').LastTaskResult

# ② 磁盘余量（数据目录在 C:）
Get-PSDrive C | Select-Object Used,Free

# ③ token 探针（tushare 最轻接口，code=0 即有效）
#    python -c "import tushare as ts; pro=ts.pro_api(open('.env.local').read().split('=')[1]); print(pro.trade_cal(exchange='SSE',limit=1))"
```

预检通过标准：State=Ready；余量 > 增量预估（首跑 ~数 GB / 例行轮 ~百 MB 级）；token 探针正常返回。

### S2 触发（23:30 自动，零操作）

- 计划任务自动触发，**无需手动**
- 例外处置：23:35 若 State 仍非 Running：

```powershell
Start-ScheduledTask -TaskName 'TushareSync'
```

### S3 监控时间轴（只读观察，勿干预）

| 时点 | 检查点 | 通过标准 |
|---|---|---|
| 23:35 | State + 数据活动 | Running；idx/sw/etf_mins 开始更新 |
| ~23:5x | 阶段推进 | 三张分钟表完成（先行阶段，与首跑同规律） |
| 00:0x-01:3x | 主体阶段 | `stk_mins` 文件数增长、`_manifest.json` 活跃 |
| ~01:4x 前 | 收尾 | 出 exit 行（例行增量轮应显著快于首跑 2h10min） |

监控命令（可循环执行）：

```powershell
Get-Content 'C:\Temp\tushare_sync_task.log' -Tail 1
(Get-ScheduledTask -TaskName 'TushareSync').State
Get-Item 'C:\aios\quant_backtest\data\tushare\_manifest.json' | Select-Object LastWriteTime
```

### S4 成功判定（次晨一次性核查，三项全过才判成功）

1. **契约行**：`tushare_sync exit=0 <次日ISO时间>`（非 0 一票否决）
2. **manifest 活性**：`_manifest.json` mtime = 当晚
3. **数据新鲜度抽查**：`index_weight\000906_202610.parquet` 等当月文件 mtime 更新为当晚

### S5 失败分支

| 场景 | 特征 | 处置 |
|---|---|---|
| 单轮失败 | exit≠0 + 飞书自动收到告警 | 直接 `Start-ScheduledTask` 重跑（断点续传幂等） |
| 连续两轮失败 | 两晚 exit≠0 | 查 tushare 限频/积分（每分钟调用上限、积分扣减） |
| 权限豁口 | manifest 内 `done:false` + error 含「没有接口访问权限」 | 非致命（exit 仍 0）：先核对代码清单确认该表是否仍在同步范围（可能是历史残留，参照 monetary_policy 案例 2026-10-17）；确在范围才需剔除或升级积分 |
| 任务未触发 | 23:35 仍非 Running | 手动 Start；再失败查计划任务日志（判例：幽灵 Running 态 0x80070490 用 Stop 清态后 Start） |

---

## 三、首跑实况存档（2026-10-16 23:30 → 10-17 01:40:23）

```text
23:30:00   计划任务自动触发（S4U 会话）
23:5x      idx_mins / sw_mins / etf_mins 三表完成（先行阶段）
00:0x-01:3x stk_mins 主体阶段（33198 文件落盘）
01:40:23   契约行 tushare_sync exit=0 —— 成功，总耗时 2h10min
```

**数据成果**：
- 50 表目录全部有数据，总量与迁移基线（22.4 万文件）吻合
- manifest 登记 32 表：31 done=true / 1 done=false——**后者经核实为历史残留**：monetary_policy 已于 2026-09-28 从代码清单剔除（替换为 shibor_lpr），manifest 条目是合并保存模式带入的旧状态，2026-10-17 已顺手清除，现 manifest 全绿（31 表零 done=false）
- 收尾写入验证：index_weight 当月、us_daily 等 01:40:22-23 更新

**并行无冲突**：同晚 20:30 run_backfill、21:00 起每小时 JevBackfill 正常运行，数据域不重叠。

---

## 四、判例关联

- 契约行判例：S4U 任务结果只信契约日志行，不信 LastTaskResult 中间态（0x41301=正在跑属正常）
- 幂等重跑：断点续传=文件存在跳过，任何失败后直接重触任务即安全
- 幽灵 Running 态（docs/audit/2026-10-16 判例档）：任务拒启时先 Stop 清态再 Start
- 明晨核查惯例：契约行 + manifest mtime + 当月文件抽查三件套
