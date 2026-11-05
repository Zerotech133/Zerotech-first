# cyq_perf 日补数安全预检（2026-11-04）

- 任务：task-85；目标日 **20261103**；默认 **dry-run**，本任务**不执行 `--execute`**
- 脚本：`C:\项目\zhice-os-v2-windows-20260920\code\scripts\cyq_daily_backfill.py`
- 结果：**status=blocked**（目标 raw 片不存在）；dry-run **rc=0**；未写数据仓/DB/计划任务，未拉外部数据
- 背景：task-82 诊断——cyq_perf 是 T+1 延迟源；raw 由 KgDailySync 06:20 的 bclass_sync 补前一交易日；curated 层无每日自动桥接

## 1. 预检结果（dry-run）

| 检查 | 实测 | 结论 |
|---|---|---|
| raw 最新日期 | **20261102**（最新片 `d_20261102.parquet`，5,571 行，mtime 2026-11-03T09:34:10） | 目标日 20261103 尚未落 raw |
| 目标 raw 片 | `data/tushare/cyq_perf/d_20261103.parquet` **不存在** | **blocked**（不拉外部数据） |
| 行数阈值 | 默认 ≥5,000（未进入，因目标片不存在） | 等 raw 片出现后校验 |
| curated 最新 | **20261102**；1,524,922 行；277 日；11 字段 | 落后目标日 1 个交易日 |
| curated 重复键 | **0**（`(ts_code,trade_date)`） | 通过（但尚未追加） |
| curated sha256/大小 | 23,628,036 B；mtime 2026-11-03T21:02:42 | 基线可备份 |
| 备份目录 | `C:\Temp` 存在且可写；磁盘余量足够（> curated 的 1.2×） | 通过 |
| 计划备份路径 | `C:\Temp\cyq_perf.parquet.bak-20261104T032413`（dry-run **不创建**） | 通过（路径唯一） |
| 目标新增行数 | 未计算（raw 片缺失） | 等 ready 后预计约 5,570 行，以实际为准 |

**dry-run 输出（关键行）**：

```text
[cyq-backfill] mode=dry-run target=20261103 threshold=5000 status=blocked
[cyq-backfill] raw latest=20261102 target_slice=MISSING rows=None
[cyq-backfill] curated date_max=20261102 rows=1524922 dup=0
[cyq-backfill] backup planned=C:\Temp\cyq_perf.parquet.bak-20261104T032413 dir_ok=True/True enough_space=True
[cyq-backfill] BLOCKED: 目标 raw 片不可用: raw 目标片不存在（不拉外部数据，等待 KgDailySync 06:20）
[cyq-backfill] dry_run_rc=0
```

**rc**：dry-run **rc=0**（预检成功执行；blocked 仅表示数据未就绪，不代表脚本错误）。
补充安全验证：`--execute` 不带 `--confirm` → **rc=2，拒绝执行，且已确认未写任何文件/未产生备份**。

## 2. 为什么不自动拉数

- 本脚本**不 import tushare、不发网络请求**；目标 raw 片缺失时只输出 blocked，等待上游 KgDailySync 补 raw。
- 这是有意的安全边界：补数写入必须是“raw 片先到、预检通过、显式确认”的顺序，不能在缺口上直接拉外部数据绕过 raw/lineage。

## 3. 06:20 执行顺序（下一步）

当前时间约 2026-11-04 03:2x；KgDailySync 计划 **06:20** 触发（链内 `bclass_sync → kg_bclass.py sync`，按 T+1 语义补 raw `d_20261103.parquet`）。

### Step 1：等/验证 06:20 晨链补 raw

```powershell
# 等 06:20 KgDailySync 跑完（或先查 KgDailySync 的 LastRunTime/日志）
Get-Item 'C:\项目\老田的AI助手\quant_backtest\data\tushare\cyq_perf\d_20261103.parquet'
# 期望：文件存在，行数约 5,570，字段 11 列
```

注意：若 06:20 晨链失败或该片仍缺失/行数<5,000，保持 blocked；不要在缺片时执行写 curated 的操作。

### Step 2：重跑 dry-run（必须 ready 才进入下一步）

```powershell
cd C:\项目\zhice-os-v2-windows-20260920\code
& .\.venv\Scripts\python.exe scripts\cyq_daily_backfill.py
# 期望：status=ready；target_slice 存在；rows >= 5000；dup=0；备份目录可写
# rc 期望 0；如果仍 blocked，不要执行 --execute
```

### Step 3：授权后受控执行（本 task-85 **未执行**）

```powershell
cd C:\项目\zhice-os-v2-windows-20260920\code
& .\.venv\Scripts\python.exe scripts\cyq_daily_backfill.py --execute --confirm
# 脚本顺序：备份 curated → 校验备份 sha256 → 仅追加缺失 key → 原子写 → 校验 max/行数/重复键
# 失败自动用备份回滚，rc=1；成功 rc=0
```

`--execute` 必须同时带 `--confirm`；只带 `--confirm` 或只带 `--execute` 都会被拒绝（rc=2，不写文件）。

### Step 4：执行后验证 + 重跑 W2 快照

```powershell
# curated max=20261103；行数增加；重复键 0；备份文件存在且 sha256 匹配
python -X utf8 -c "import pandas as pd; p=r'C:\项目\老田的AI助手\quant_backtest\data\tushare_curated\cyq_perf.parquet'; d=pd.read_parquet(p,columns=['ts_code','trade_date']); print(d.trade_date.max(), len(d), int(d.duplicated(['ts_code','trade_date']).sum()))"

cd C:\项目\zhice-os-v2-windows-20260920\code
$env:PYTHONPATH='src'
& .\.venv\Scripts\python.exe scripts\w2_factor_daily.py
# 期望 chip_concentration 从 unavailable 变为 ok/partial
```

## 4. 风险与防护

| 风险 | 防护 |
|---|---|
| raw 目标片缺失/晨链失败 | 脚本 status=blocked，不拉外部数据、不写 curated；等待/修复晨链 |
| raw 行数不足/含空片 | dry-run 阈值 ≥5,000；raw 片行数不足 → blocked |
| 目标片日期不纯/重复键 | 校验 `trade_date` 全为 20261103、`(ts_code,trade_date)` 重复=0；不满足 blocked |
| curated 已含目标日 | `already_applied`，不重复追加；dry-run 可判定 |
| curated 已有重复键 | blocked，需先数据治理，不在本脚本内自动去重 |
| 备份不可写/空间不足/路径已存在 | dry-run 检查目录、可写、磁盘余量、路径唯一；不通过 blocked |
| 执行中断/校验失败 | 执行前备份并校验 sha256；失败自动用备份回滚；exec 不用外部 API |
| 并发写 curated（refresh_curated 全量重建等） | 执行前确认无其他数据根/整理层全量操作；本 task 不执行写入 |
| 误执行 | `--execute` 必须 `--confirm`；本 task-85 只验证 dry-run；未改计划任务/DB |

## 5. 边界与复现

- 本任务只读检查并写本报告；**不写数据仓、不写 DB、不改计划任务、不拉 Tushare 外部数据、未执行 `--execute`**。
- 脚本 dry-run 无任何写文件动作（测试后已核对 curated/raw mtime 未变，C:\Temp 无新备份）。
- 报告/脚本路径：
  - 脚本：`C:\项目\zhice-os-v2-windows-20260920\code\scripts\cyq_daily_backfill.py`
  - 数据：`quant_backtest/data/tushare/cyq_perf/`、`quant_backtest/data/tushare_curated/cyq_perf.parquet`
  - 备份目录：`C:\Temp`（实际备份仅在 `--execute --confirm` 成功后产生）
- 结论：**当前 blocked（目标 raw 片缺失），dry-run rc=0；等 06:20 KgDailySync 补 raw 后重跑预检，ready 再按授权执行。**
