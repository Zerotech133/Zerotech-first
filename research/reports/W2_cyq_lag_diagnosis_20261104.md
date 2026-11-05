# cyq_perf 数据日滞后诊断（2026-11-04）

- 任务：task-82；诊断 W2-5a 快照中 `chip_concentration` 因子 unavailable 的原因（cyq_perf 最新 20261102，而 daily as_of=20261103）
- 性质：**只读诊断**；不执行数据写入、不改计划任务、不写 DB、不触发补数
- 结论：**已定位（diagnosed）**，不是 insufficient；滞后 **1 个交易日**，主因是 **cyq_perf 为 T+1 延迟源，且其日更入口 KgDailySync 06:20 晚于 03:xx 的 W2 快照；TushareSync 夜间增量与 CuratedDailyRefresh 都不覆盖 cyq_perf**
- 诊断命令 rc：**0**（只读文件/任务计划/日志查询；无写入）

## 1. 三源实况（真实日期/行数/字段）

| 源 | 路径 | 最新日期 | 行数 | 日期范围/数量 | 字段 | mtime |
|---|---|---|---:|---|---|---|
| curated | `data/tushare_curated/cyq_perf.parquet` | **20261102** | 1,524,922 | 20250901–20261102（277 日，5,848 代码） | 11 列：`ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate` | 2026-11-03T21:02:42 |
| raw | `data/tushare/cyq_perf/d_20261102.parquet` | **20261102** | 5,571（最新片）；全量 277 文件共 1,524,922 | 20250901–20261102 | 同上（11 列） | 2026-11-03T09:34:10 |
| tdx | `data/tdx/ts_cyq_perf/part_0051.parquet` | **20261102** | 5,571（最新片）；52 part 共 8,142,089 | 20200102–20261102（1,654 日） | 同上（11 列） | 2026-11-03T17:35:42 |
| daily（对照） | `data/tushare/daily/d_20261103.parquet` | **20261103** | 5,570 | — | daily 11 字段 | 2026-11-03T20:30:58 |

代码格式：`ts_code` 为 6 位数字 `.SH/.SZ/.BJ`（如 `000001.SZ`）；`trade_date` 为 `YYYYMMDD` 字符串。三源同日格式一致；tdx 和 raw 的 20261103 均不存在，说明**不是 curated 合并单层的问题，而是源侧就没到 11-03**。

## 2. 为什么 daily 能到 20261103、cyq 只到 20261102

### 2.1 daily 的双通道（对照）

- **20:30 TushareIncrementGuard**：`increment_guard.py` 的 `TABLES=[ts_daily, ts_adj_factor, ts_daily_basic, ts_margin_detail, ts_index_daily, ts_moneyflow]`；`data/tushare/daily/d_20261103.parquet` mtime **2026-11-03 20:30:58**，与其 20:30 触发吻合。
- **22:30 CuratedDailyRefresh**：`cmd.exe ... python -X utf8 -m analysis.curated_update --auto`；`C:\Temp\curated_daily.log` 明确显示 `[fetch] daily 20261103 rows=5570` 并 append 到 curated，所以 daily as_of=20261103。
- **关键**：`TushareIncrementGuard` 与 `CuratedDailyRefresh` 的固定表清单都**不含 cyq_perf**（见 §3）。

### 2.2 cyq_perf 的唯一日更入口：KgDailySync 06:20 的 bclass

- `KgDailySync` 任务每日 **06:20** 触发 `start_kg_sync.ps1`；链内第 5.5 步 `bclass_sync` 执行 `kg_bclass.py sync`。
- `kg_bclass.py` 的 `_recent_open_days(2)` 注释：**“筹码是 T+1 延迟源（T 日数据当晚才出），晨链 06:20 拉‘今日’必空”**；实际按开市日历取最近 2 个 `d <= today`，用 `_pull_one(..., keep_empty=False)`：今日数据未出→0 行不落片；前一日→落片。
- 2026-11-03 的实际运行是 **W1-07 手工重跑**（`kg_sync_task.log`: `w1-07 manual_rerun started=2026-11-03T09:06:25 by=lead`），链内 `bclass_sync exit=0 2026-11-03T09:34:17`；`kg_bclass_sync_out.log` 输出 `特征 kg_cyq.parquet：5848 标的（快照日 20261102）`；raw `d_20261102.parquet` mtime **09:34:10** 与之吻合。
- 09:06 时 Tushare 还没有 11-03 当日的 cyq_perf（T+1 源），所以只拉到 11-02；**下一次 06:20（11-04）** 才会把 11-03 当 T-1 补进 raw（这是设计自愈路径，不是永久断流）。
- **但 W2 因子日快照于 2026-11-04T03:02 生成，早于 06:20 的 KgDailySync**，因此快照时刻 cyq 最新只能是 20261102 → `chip_concentration` unavailable（task-77 已按数据不可用如实处理）。

### 2.3 curated 层没有每日桥接

- `CuratedDailyRefresh` 的任务命令是 `analysis.curated_update --auto`；`analysis/curated_update.py:30` 的 `TABLES = ["daily", "daily_basic", "moneyflow", "index_daily"]`，**不含 cyq_perf**；`curated_daily.log` 的每次运行也只出现这 4 张表。
- `data/tushare_curated/cyq_perf.parquet` 的 mtime 是 2026-11-03 **21:02:42**，而 CuratedDailyRefresh 的运行记录是 22:30；21:02 没有对应的每日任务日志，属于其他整理/手工操作。无论来源，其内容 max 仍 20261102，说明 curated cyq 跟随的是当时 raw 最新，**不是每日自动增量链的一部分**。
- 全量 builder `tools/refresh_curated.py`（raw→curated）是手工/周任务（约 22–52 分钟、322M+ 行），不是每日调度；因此 raw 06:20 自愈后，curated 不会自动前进，需一次 curated 追加/重建。

### 2.4 TushareSync 夜间增量为什么不覆盖

- `TushareSync` 任务 23:30 执行 `start_tushare_sync.ps1` → `tushare_sync.py --incremental --market all`；2026-11-03 23:30 运行、2026-11-04 00:53:35 完成，**exit 0**。
- 但 `cyq_perf` 只在 `tushare_sync.py` 的 `batch_bclass()`（`--batch bclass`）里（行 1030/1120）；`--incremental` 分支（main 行 1187-1188）不调用 bclass。`--incremental` 的 `incr_raw_by_date` 只补 daily_basic/moneyflow/margin_detail 等，**不含 cyq_perf**。
- 所以 TushareSync 夜链即使全绿，也不会更新 cyq；不能把“TushareSync exit 0”误判为 cyq 已覆盖。

## 3. 组件覆盖矩阵

| 组件 | 触发 | 是否覆盖 cyq_perf | 证据 |
|---|---|---|---|
| TushareSync | 每日 23:30 | ❌ | 任务 action 为 `--incremental`；cyq 只在 `batch_bclass()`（需 `--batch bclass`）；main 行 1187-1188 走 incremental；日志 exit 0 但无 cyq 输出 |
| CuratedDailyRefresh | 每日 20:40 / 22:30 | ❌ | `analysis/curated_update.py:30 TABLES` 无 cyq_perf；`C:\Temp\curated_daily.log` 仅 4 表 |
| KgDailySync（bclass_sync） | 每日 06:20 | ✅（仅 raw） | `start_kg_sync.ps1` 第 5.5 步；`kg_bclass.py sync` 的 `_recent_open_days(2)` + `_pull_one('cyq_perf', keep_empty=False)`；2026-11-03 09:06 手工重跑、09:34:17 exit 0，raw 最新 20261102 |
| TushareIncrementGuard | 每日 20:30 | ❌ | `increment_guard.py TABLES` 为 ts_daily/ts_adj_factor/ts_daily_basic/ts_margin_detail/ts_index_daily/ts_moneyflow |
| tools/refresh_curated.py | 手工/周 | 可全量（非每日） | raw→curated 全量重建脚本；本任务不执行 |
| 本次 W2 快照 | 03:xx（当前） | — | 早于 06:20 KgDailySync，chip 只能看到 20261102 |

任务计划实查（2026-11-04 03:xx）：

- `TushareSync`：last 2026-11-03 23:30:02，result 0，next 2026-11-04 23:30
- `CuratedDailyRefresh`：last 2026-11-03 22:30:00，result 0，next 2026-11-04 20:40
- `KgDailySync`：last 2026-11-03 09:06:25（手工重跑），result 0，next **2026-11-04 06:20**
- `TushareIncrementGuard`：last 2026-11-03 20:30:14，result 0，next 2026-11-04 20:30

## 4. 根因（按影响排序）

1. **RC1（主因）：cyq_perf 是 T+1 延迟源，日更入口 KgDailySync 06:20 晚于 W2 快照 03:xx**
   - 11-03 09:06 手工重跑只能拿 11-02（11-03 数据尚未发布）；
   - 11-04 03:02 快照发生在 11-04 06:20 晨链前，所以 cyq 最新 11-02。
2. **RC2：TushareSync 夜间增量不含 cyq_perf**（只在手工 `--batch bclass`），夜链 exit 0 不等于覆盖。
3. **RC3：CuratedDailyRefresh 不含 cyq_perf，curated 层缺每日 raw→curated 桥接**；即使 06:20 raw 自愈到 11-03，curated 仍可能停在 11-02，W2 chip（读 curated）继续 unavailable。
4. **RC4（对照）：daily 的 20:30 + 22:30 双通道正常，所以 daily as_of=20261103**；cyq 没有这两条通道，天然落后。

> 说明：curated cyq 2026-11-03T21:02 的写入时点没有对应 CuratedDailyRefresh 日志，可能是其他整理/手工操作；但根因不依赖这一点——CuratedDailyRefresh 的固定 TABLES 不含 cyq，“curated 无每日桥接”这一结构事实已由代码与日志证明。

## 5. 修复方案（预案，本次不执行）

### 5.1 立即补数（2026-11-04 晨间数据可用后）

> 约束：Tushare `cyq_perf` 单日约 5,500 行；**空数据不得写空片**；执行前确认无其他数据根/整理层全量重建在跑（查 `C:\Temp\kg_sync_task.log` 尾部）；执行前备份 curated 文件。

**Step 0：备份 + 记录基线**

```powershell
Copy-Item 'C:\项目\老田的AI助手\quant_backtest\data\tushare_curated\cyq_perf.parquet' `
  'C:\Temp\cyq_perf.bak-20261104.parquet'
Get-FileHash 'C:\项目\老田的AI助手\quant_backtest\data\tushare_curated\cyq_perf.parquet' -Algorithm SHA256
# 基线：rows=1,524,922; max=20261102; 11 字段
```

**Step 1：补 raw cyq_perf 20261103（推荐等 06:20 自动链；若要立即补，用最小单日拉取）**

- 方案 A（推荐、零手工）：等 **2026-11-04 06:20 KgDailySync** 自动跑完；它会把 11-03 作为 T-1 写入 `data/tushare/cyq_perf/d_20261103.parquet`。跑完后验证文件存在、行数约 5,570、`trade_date=20261103`。
- 方案 B（立即、只补单日，避免 `--batch bclass` 全历史扫描）：

```powershell
cd C:\项目\老田的AI助手\quant_backtest
& 'C:\Program Files\Python311\python.exe' -X utf8 -c `
  "import tushare_sync as T; df=T.pull_paged('cyq_perf', {'trade_date':'20261103'}, 6000); assert len(df) > 5000, len(df); df.to_parquet(r'data\tushare\cyq_perf\d_20261103.parquet', index=False); print('rows', len(df))"
```

- 若返回空/行数 <5,000：说明 API 尚未发布或限频，**不要落空片**，等 06:20 晨链或稍后重试。不要用 `tushare_sync.py --batch bclass` 做单日补数（会扫全历史，慢且消耗额度）。

**Step 2：把 20261103 追加进 curated cyq_perf**

```powershell
cd C:\项目\老田的AI助手
& 'C:\Program Files\Python311\python.exe' -X utf8 -m analysis.curated_update --tables cyq_perf --dates 20261103
```

- `analysis.curated_update` 支持显式 `--tables cyq_perf`（默认 TABLES 不含它，但显式参数走 `append_table`）；`_ts_call` 会从 Tushare 取 `cyq_perf` 当日行并 append-only 写入 curated。
- 期望：curated 行数由 1,524,922 增加约 5,570（约 1,530,492，以实际为准）；max=20261103；11 字段不变；`(ts_code,trade_date)` 无重复。
- **不要**与 `tools/refresh_curated.py` 全量重建并发；不要手工覆盖整个 curated 单文件。

**Step 3：验证并重跑 W2 日快照**

```powershell
& 'C:\Program Files\Python311\python.exe' -X utf8 -c `
  "import pandas as pd; p=r'C:\项目\老田的AI助手\quant_backtest\data\tushare_curated\cyq_perf.parquet'; d=pd.read_parquet(p, columns=['ts_code','trade_date']); print('max',d.trade_date.max(),'rows',len(d),'dup',int(d.duplicated(['ts_code','trade_date']).sum()))"

cd C:\项目\zhice-os-v2-windows-20260920\code
$env:PYTHONPATH='src'
& .\.venv\Scripts\python.exe scripts\w2_factor_daily.py
```

- 期望：`max=20261103`、dup=0；W2 JSON 中 `chip_concentration` 从 `unavailable` 变为 `ok|partial`，coverage 接近本日 universe（3,859 左右）。

### 5.2 长期修复（需 L2 审批，本任务不改）

1. **新增 `CuratedCyqRefresh` 计划任务（推荐）**：每日 **06:45**（KgDailySync 06:20 之后）运行：
   ```powershell
   cd C:\项目\老田的AI助手
   & 'C:\Program Files\Python311\python.exe' -X utf8 -m analysis.curated_update --auto --tables cyq_perf --include-today-hour 25
   ```
   - `--include-today-hour 25` 使 `missing_dates` **永不纳入今日**（T+1 源），只补上一交易日及更早缺口；
   - 只读预演（本任务执行，未写）：`today=20261104` → `{'cyq_perf': ['20261103']}`；`today=20261105` → `{'cyq_perf': ['20261103','20261104']}`，符合 T+1 语义。
   - 需要 L2 变更审批；不修改现有 CuratedDailyRefresh/CuratedWeekly。
2. **快照调度对齐**：若 W2 因子日快照必须在 03:00 左右跑，明确接受 chip 为 T-1；若要 8 因子当日齐全，应把快照排到 `CuratedCyqRefresh`（06:45）之后，或在脚本中显式使用 `latest_common_date`。
3. **监控**：数据质量/因子日报增加 `curated cyq max < 最近已收盘交易日` 告警；W2 JSON 的 `unavailable` 可作为临时监控源。
4. **代码级备选（不推荐）**：把 cyq_perf 加入 `analysis.curated_update.TABLES` 并做 T+1 cutoff，或让 `kg_bclass` 顺带写 curated；涉及数据分层职责/代码变更，需另立任务评审。

## 6. 风险

- Tushare 频率/额度：`cyq_perf` 单日 ~5,500 行，异常重试需退避；不要并发多次补同一天。
- append-only 不全局去重：`analysis.curated_update` 只跳过已存在 key；执行前后检查 `duplicated(['ts_code','trade_date'])`（历史上 2026-11-03 05:52 `analysis_curated_extras` 曾出现 no-dedupe 事故并回滚）。
- curated 文件大（23.6MB/152万行），写入用 tmp+os.replace；避免与全量 `refresh_curated.py`（22–52 分钟、322M+ 行）并发。
- 计划任务变更属 L2：需 lead/A0 审批，本任务不执行。
- tdx 最新同为 20261102，不能用来绕过；tdx 只适合交叉校验。
- 不要拿 20261102 旧数据“补齐”成 20261103；W2 已按 PIT 标 unavailable 是正确行为。

## 7. 只读边界与复现

- 本任务只读：三源 parquet、任务计划（schtasks 查询）、日志、代码；**未写数据仓、未写 DB、未改计划任务、未触发 Tushare 补数**。
- 诊断命令 rc=0（所有只读查询成功；无写入）。
- 机器可读证据：`C:\Temp\w2_cyq_lag.json`（含三源 rows/dates/fields/mtime、任务覆盖矩阵、时间线、根因、修复方案与风险）。
- 关键代码/日志位置：
  - `analysis/curated_update.py:30`（TABLES 不含 cyq_perf）
  - `quant_backtest/tushare_sync.py:1030/1120`（cyq 在 batch_bclass；incremental 不覆盖）
  - `quant_backtest/kg_bclass.py`（`_recent_open_days`、cyq `_pull_one`、`kg_cyq.parquet`）
  - `quant_backtest/start_kg_sync.ps1`（bclass_sync 步骤）
  - `quant_backtest/start_tushare_sync.ps1`（--incremental --market all）
  - `C:\Temp\kg_sync_task.log`、`C:\Temp\kg_bclass_sync_out.log`、`C:\Temp\curated_daily.log`
- 结论：**已定位；按 §5.1 在 11-04 晨间数据可用后补 20261103 + curated append 即可修复；长期按 §5.2 加 06:45 晨间桥接任务。**
