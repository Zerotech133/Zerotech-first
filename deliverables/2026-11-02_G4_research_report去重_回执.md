# G4 回执：tushare/research_report 96.3% 重复清理（数据写 · 可回滚）

> 任务：`task-53`（共享任务板 · 车道4，S3-02 G4）｜执行：wa-stores｜执行窗口：**2026-11-02 12:02:47 – 12:04:06（Asia/Shanghai）**
> 依据：task-53、`C:\Temp\kg_sync_task.log`（数据根互斥纪律）、`docs/audit` 数据根章程
> 边界：**只动 `quant_backtest/data/tushare/research_report` + 本回执**；未改 catalog/registry/代码；未 commit、未重启；**原文件一律 move 到备份/归档，不 rm**。
> 证据：`C:\项目\work\research2\`（脚本/分析/计划/结果/复核）、`C:\Temp\research_report_backup_20261102_120247\`（完整备份）、`C:\Temp\research_report_originals_20261102_120247\`（原 38 分片）、`C:\Temp\kg_sync_task.log`（已补记流水）。

---

## 0. 结论

| 项 | 去重前 | 去重后 | 说明 |
|---|---|---|---|
| 文件数 | **38**（`part_0000..0037.parquet`，每个恰好 6,000 行） | **1**（`part_0000.parquet`，canonical 单分片，ZSTD，437,618 B） | 38 片为滚动信息流重复快照叠写 |
| 行数 | **228,000** | **8,402** | 去重 **219,598 行 = 96.31%** |
| 唯一自然键（`trade_date+ts_code+title+report_type`） | 8,402 | **8,402** | 键集合**零丢失、零新增**（EXCEPT 双向=0） |
| 全行唯一数 | 8,419 | 8,402 | 17 个键各有 2 个“同键异元数据”版本行，按“字段最全”保留 1 行（共折叠 17 行） |
| 日期范围 / 交易日数 | 20260713–20261101 / 111 天 | **同左，未变** | 每日唯一键数逐日一致（无 mismatch） |
| catalog / registry | catalog sha `4d78531a…`（v24）、registry sha `3a3dcd87…` | **完全未变**（hash 前后一致，mtime 仍 11:52:40/41） | 符合“不改 catalog/registry”；catalog 仍登记 rows=6000/files=38 → **待 Lead rebuild v25 修正** |
| 回滚 | — | 完整备份 38 片（19,305,366 B）+ 原文件归档（38 片） | 可一键还原（§6） |

---

## 1. 重复机理与证据（只读分析）

- 38 个 `part_NNNN.parquet` **每个固定 6,000 行**（合计 228,000），时间为 2026-09-27 ~ 2026-11-01，说明采集器是“滚动窗口整批重写快照”而非增量 upsert → 同一批研报在连续多天分片中被反复写入。
- 自然键候选实测唯一值：`full_row=8,419`；**`trade_date+ts_code+title=8,402`**；`trade_date+ts_code+title+report_type=8,402`；`trade_date+ts_code+title+inst_csname=8,402`；`title+ts_code=8,402`；`url=8,408`；`trade_date+title+author=8,403` → **8,402 与任务口径一致**，采用 `trade_date+ts_code+title+report_type` 为去重主键（比三列键更保守：report_type 不同不合并）。
- 列：`trade_date,title,report_type,author,name,ts_code,inst_csname,ind_name,url`（与 catalog schema 一致）；日期范围 `20260713–20261101`，111 个交易日；空值：`ts_code` null 107,070（市场类研报无个股代码）、`name` null 107,070、`author` null 12,900、`inst_csname` null 69、`ind_name` null 651；`trade_date/title/report_type/url` 无 null/空。
- 分析原始输出：`C:\项目\work\research2\analyze_before.json`（elapsed 0.5s）。

## 2. Dry-run 计划（执行前留档）

`C:\项目\work\research2\dedup_plan.json`：

```json
{"dedup_key":"trade_date+ts_code+title+report_type","source_files":38,"source_rows":228000,
 "unique_full_rows":8419,"retained_rows":8402,"duplicate_rows_removed":219598,
 "duplicate_pct":96.31,"canonical_files":1,"date_range":["20260713","20261101"],"mode":"dry_run_plan"}
```

**计划**：38 片全部移入备份/归档 → 以去重键生成 1 个 canonical 分片 `part_0000.parquet`（保留 `part_` layout 命名，读取层 `part_*.parquet` 通配不受影响；catalog 的 files=38 待 rebuild 修正）。

## 3. 执行步骤（逐步留证，失败自动回滚）

| # | 步骤 | 结果 |
|---|---|---|
| 0 | 读 `C:\Temp\kg_sync_task.log` 尾部（数据根无进行中操作，记录至 11:52:41） | ✅ |
| 1 | 整目录备份 → `C:\Temp\research_report_backup_20261102_120247\`（38 文件 19,305,366 B + `SHA256SUMS.txt`） | ✅ 38/38 SHA256 与源**全一致**（`HASH_MATCH=YES`） |
| 2 | 写一次性脚本 `C:\项目\work\research2\g4_research_report_dedup.py`（analyze / plan / execute；execute 强校验：备份 38 片、目标目录已无 part、键集合双向等值） | ✅ |
| 3 | 原 38 片 **move** → `C:\Temp\research_report_originals_20261102_120247\`（**未 rm**） | ✅ originals=38，store remain=0 |
| 4 | 从**备份**只读源去重 → 写 `research_report\part_0000.parquet`（duckdb `COPY ... (FORMAT PARQUET, COMPRESSION ZSTD)`） | ✅ rc=0，437,618 B |
| 5 | 内建校验（写后）：rows / unique_full / unique_key / keys_only_in_source / keys_only_in_new | ✅ `verify_rows=8402, unique_full=8402, unique_key=8402, keys_only_in_source=0, keys_only_in_new=0` |
| 6 | catalog.json 前后 hash | ✅ `4D78531A…` 前后一致（未改 catalog） |

执行日志：`C:\项目\work\research2\execute2_20261102_120247.log`、`execute_result_20261102_120247.json`。

> 说明：首次执行因脚本 `execute` 阶段仍在读已清空的源目录而失败（rc=1）；保护逻辑**自动把 38 片 move 回 store**（回滚后 38 文件），修正脚本（execute 改从备份读源）后二次执行成功。全过程无文件丢失、无 rm。

## 4. 验证（独立复核 + 门禁记录）

### 4.1 独立复核 `C:\项目\work\research2\verify_after.json`（只读，对比备份源 vs 新分片）

```json
{"old_files":38,"new_files":1,"new_file_names":["part_0000.parquet"],
 "old_rows":228000,"new_rows":8402,"old_unique_keys":8402,"new_unique_keys":8402,
 "keys_lost":0,"keys_added":0,
 "old_date_range":["20260713","20261101"],"new_date_range":["20260713","20261101"],
 "per_date_key_count_mismatch":{},"date_count_old":111,"date_count_new":111,
 "new_file_sha256":"7e4bc3c36a725e5ed4ef72f5adb6ad3f57f06e492859f5c91681661b67de9f6a",
 "new_file_bytes":437618,"store_other_files":[],"ok":true}
```

### 4.2 门禁/台账校验（只读运行，记录前后）

| 工具 | 去重前 | 去重后 | 判读 |
|---|---|---|---|
| `metadata_consistency.py --json` | `code=0`，catalog v24，issues=[]/warnings=[] | **`code=0`**，issues=[]/warnings=[] | ✅ 不受影响（catalog×registry 未变） |
| `catalog_snapshot.py --json`（默认抽样 25 表） | `code=0`，drift_sample.missing_stores=[], file_count_mismatch=[] | `code=1`，drift 命中 `forecast`（见 4.3，**非本单**） | 抽样随机，未抽到 research_report |
| `catalog_snapshot.py --json --limit 256`（全表，执行后补跑） | —（执行前未跑全表） | `code=1`，`file_count_mismatch=["forecast","research_report"]`：**research_report registered 38 / on_disk 1 / diff -37（本单预期漂移）**；forecast 4/18（并发会话，见 4.3）；missing_stores 7 个（PG/sqlite 布局，属全表扫描口径既有噪声） | ✅ **research_report 漂移=本单预期，待 catalog rebuild v25 修正**（Lead 负责）；其余与本单无关 |

输出留档：`catalog_snapshot_before.json`、`catalog_snapshot_after.json`、`catalog_snapshot_after_full.json`、`metadata_consistency_before.json`、`metadata_consistency_after.json`（均在 `C:\项目\work\research2\`）。

### 4.3 并发观察（重要，非本单改动）

- 全表 `catalog_snapshot` 报 `forecast` 漂移（registered 4 / disk 18，且目录 mtime=**2026-11-02 12:03:50**，恰在本次操作窗口内）→ **其他会话正在并发写 `data/tushare/forecast`**，与本单无关（本单只碰 `research_report`，catalog/registry hash 前后一致）。
- 已按 AGENTS 数据根纪律在 `C:\Temp\kg_sync_task.log` 追加流水：
  `g4_research_report_dedup date=2026-11-02T12:04:06+08:00 rows=228000->8402 files=38->1 backup=C:\Temp\research_report_backup_20261102_120247 exit=0`
- 建议 Lead 复核数据根并行操作，避免 G4 类数据写与其它表写交叉。

---

## 5. 影响面

| 面 | 说明 |
|---|---|
| 存储 | `research_report` 18.41 MB / 38 文件 → **0.42 MB / 1 文件**（节省 ≈17.98 MB） |
| 数据量 | 228,000 → 8,402 行（去重，无信息丢失：自然键集合零丢失；17 行同键元数据变体按“字段最全”保留） |
| 读取层 | 依赖 `part_*.parquet` 通配的读法（pandas/duckdb glob）**无需改动**即可读新单分片 |
| catalog/registry | **未改**（Lead 后续 rebuild v25 修正 rows/files/qualification；本单已把预期漂移写入回执） |
| tushare `_manifest.json` | 未改（在 writeScope 外）；其记录的 research_report offset/rows 可能仍为旧值 → 建议 rebuild 时一并复核 |
| 其它表/服务/任务 | 未触碰；无重启、无 commit |
| 复现风险 | 若 tushare 采集器仍按“滚动 6,000 行整批快照”方式写入，**重复会在后续分片中再次累积** → 根治需改采集侧按自然键 upsert（超出本单边界，建议另单） |

## 6. 回滚预案（任选，均不 rm 原数据）

```powershell
$store = 'C:\项目\老田的AI助手\quant_backtest\data\tushare\research_report'
$bak   = 'C:\Temp\research_report_backup_20261102_120247'
$orig  = 'C:\Temp\research_report_originals_20261102_120247'
$ts    = Get-Date -Format 'yyyyMMdd_HHmmss'

# 方案 A（推荐，快速还原为 38 片原状）：把新产物移出，再把归档原文件 move 回 store
New-Item -ItemType Directory -Force "C:\Temp\research_report_dedup_result_$ts" | Out-Null
Move-Item "$store\part_*.parquet" "C:\Temp\research_report_dedup_result_$ts" -Force   # 去重结果留档
Move-Item "$orig\part_*.parquet" $store -Force                                          # 原 38 片还原
# 验证：应回到 38 文件 / 228,000 行
& C:\项目\py312\python.exe -X utf8 -c "import duckdb;print(duckdb.connect().execute(\"select count(*) from read_parquet('C:/项目/老田的AI助手/quant_backtest/data/tushare/research_report/part_*.parquet')\").fetchone())"

# 方案 B（从完整备份复制还原）
Copy-Item "$bak\part_*.parquet" $store -Force
# 验证 38 文件 SHA256 与备份一致（备份目录内 SHA256SUMS.txt）
Get-ChildItem $store -Filter part_*.parquet | Sort-Object Name | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
```

回滚**不涉及** catalog/registry（本单未改，无需回滚）；回滚后仍建议 Lead 复核 catalog 与磁盘一致性（rebuild 或维持旧登记）。

## 7. 未验证 / 边界（如实报告）

1. catalog/registry 的 `research_report` rows=6000/files=38 **仍是旧值**：本单按边界不改，需 Lead rebuild v25（或另行授权）修正为 8,402/1；本回执已把全表 `catalog_snapshot` 的预期漂移（-37）记录在案。
2. tushare `_manifest.json` 的 research_report 进度/行数未同步（writeScope 外）。
3. 采集侧“滚动 6,000 行快照”重复根因未修（超出本单）；若不同步修 ETL，**重复会重新累积**。
4. 未做全量下游回归（读取层以 `part_*.parquet` glob 读，新单分片 schema 与原一致；仅以 duckdb/pyarrow 读取抽验，见 §4.1）。
5. 全表 `catalog_snapshot` 的 `forecast`/`missing_stores` 等 issue 与本单无关（并发会话/既有口径），仅如实记录，未处置。
6. 未 commit、未重启服务；数据根并行会话活动已记录（§4.3）。

## 8. 证据索引

| 类别 | 路径 |
|---|---|
| 备份（完整 38 片 + SHA256SUMS） | `C:\Temp\research_report_backup_20261102_120247\` |
| 原文件归档（move 出的 38 片） | `C:\Temp\research_report_originals_20261102_120247\` |
| 去重脚本 | `C:\项目\work\research2\g4_research_report_dedup.py`（analyze/plan/execute） |
| 独立复核脚本/结果 | `C:\项目\work\research2\verify_after.py` / `verify_after.json` |
| 分析/计划 | `analyze_before.json` / `dedup_plan.json` / `variant_stats.txt` |
| 门禁前后 | `catalog_snapshot_before.json`、`catalog_snapshot_after.json`、`catalog_snapshot_after_full.json`、`metadata_consistency_before.json`、`metadata_consistency_after.json` |
| 执行日志 | `execute2_20261102_120247.log`、`execute_result_20261102_120247.json` |
| 新产物 | `quant_backtest\data\tushare\research_report\part_0000.parquet`（sha256 `7e4bc3c3…9f6a`，437,618 B） |
| 数据根互斥流水 | `C:\Temp\kg_sync_task.log`（末 3 行含本单记录） |
