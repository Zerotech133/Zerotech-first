# qualify_probe 三改回执（sqlite 探针 / 静态豁免白名单 / 抽样改进）

> STATUS: done（只改工具+测试+回执；**本轮不 apply、不写 registry/catalog/数据**；未 commit、未重启）

> 2026-11-02 09:55 ｜ 工单：task-32 ｜ 文件：`tools/datawarehouse/qualify_probe.py` + `tools/datawarehouse/tests/test_qualify_probe.py`

## 一、三项改动

### ① sqlite store 探针（替代 parquet-only 误判）

- `_is_sqlite_store(entry)`：layout=sqlite 或 store 后缀 `.db/.sqlite/.sqlite3`。
- `probe_sqlite_table(store, table, entry)`：`sqlite3.connect("file:...?mode=ro")` 只读打开，取表存在性 / `count(*)` 行数 / 日期列 `max()`（优先 catalog `date_col`，回退 trade_date/pub_date/notice_date/ann_date/created_at/updated_at 等）；**不写、不连远端、不读凭据**。
- `probe_table`：sqlite 表不再走 `_iter_parquet`（原逻辑→0 parquet→`no_parquet` bad）；卡片新增 `sqlite{path,table,rows,latest,date_col_used,columns,error}`，`latest_source=sqlite_max`；表缺失/读取失败分别 fail-visible（`sqlite_table_missing`/`sqlite_error`）。

### ② 静态豁免显式白名单

- 新增 `STATIC_EXEMPT_WHITELIST`（7 张）：已声明 3 张 `tdx_sector_def/tdx_sector_member/ts_broker_recommend` + catalog `date_status=not_applicable` 的明确静态表 `stock_company/sw_classify_l1/sw_l1_member/chinese-dictionary-of-financial-sentiment`。
- 豁免条件收窄为「白名单 OR catalog `static_no_date=true`」；**不再因 `date_col` 缺失自动豁免**。卡片新增 `exempt_source=whitelist/catalog_static_no_date`；payload `date_policy` 新增 `static_exempt_whitelist` 与 `exempt_sources`。
- 24 张未声明动态表（ts_news/ts_major_news/ts_report_rc/ts_cn_*/ts_us_*/ts_ths_member 等）不再豁免 → 回到 `no_date_column` → **evidence_partial**。

### ③ 抽样复核改进（多 part 表）

- `select_samples(..., layout)`：`layout=part` 改 `part_spread` 策略（首/末/等距 part + mtime 最新 + 最大），不再只取 mtime 最新 4 个（mtime 最新 part 可能是旧数据）。
- `part_footer_latest(files, date_col)`：part 布局**全量（≤`MAX_PART_FOOTER_SCAN`=80 文件）footer 统计扫描**（所有 row group，只读 metadata），取真实最大业务日；卡片新增 `part_scan{files_scanned,row_groups,max_files,latest_file}`，`latest_source=footer_all_parts`。

## 二、测试

```
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/datawarehouse/tests/test_qualify_probe.py
→ 53 passed（rc=0；改动前 45，净增 8：sqlite 探针 4 + 白名单 payload 1 + part 抽样 2 + 无 date_col 收窄断言 1）
C:\项目\py312\python.exe -X utf8 -m py_compile tools/datawarehouse/qualify_probe.py tools/datawarehouse/tests/test_qualify_probe.py
→ rc=0
```

## 三、dry-run before → after（只读，未 apply）

### 3.1 ① sqlite 3 表（stock_news/stock_notice/stock_report）

| 表 | before verdict | before reasons | after verdict | after 证据 | 行数 | 最新日 | lag |
|---|---|---|---|---|---:|---|---:|
| `stock_news` | evidence_bad | no_parquet | **evidence_ok** | sqlite 表 stock_news | 380365 | 20261101 | 1 |
| `stock_notice` | evidence_bad | no_parquet | **evidence_ok** | sqlite 表 stock_notice | 214167 | 20261101 | 1 |
| `stock_report` | evidence_bad | no_parquet | **evidence_ok** | sqlite 表 stock_report | 149776 | 20261101 | 1 |

> 修复前均 `evidence_bad / no_parquet`（探针只认 parquet）；修复后读取 `tdx/tdx_info.db`（269.9MB，只读）得行数与日期列 max（最新 2026-11-01，lag 1 ≤ 7）→ **evidence_ok**。

### 3.2 ② 静态豁免白名单：24 张未声明动态表 → partial

| 指标 | before | after |
|---|---:|---:|
| evidence_ok | 24 | 0 |
| evidence_partial | 0 | 24 |

> 24 张全部由 `evidence_ok`（旧：无 date_col 自动豁免）→ **evidence_partial / no_date_column**；payload `exempt_sources` 只含白名单表。

白名单 7 张复跑：**全部 evidence_ok**，`exempt_source=whitelist`、`date_check=skipped_static_no_date`（`tdx_sector_def/tdx_sector_member/ts_broker_recommend/stock_company/sw_classify_l1/sw_l1_member/chinese-dictionary-of-financial-sentiment`）。

### 3.3 ③ 抽样改进：7 张 part 表最新业务日修正

| 表 | before latest | before lag | after latest | after lag | verdict（不变） |
|---|---:|---:|---:|---:|---|
| `ts_anns_d` | 20240718 | 837 | **20261016** | 17 | evidence_partial |
| `ts_fund_portfolio` | 20230831 | 1159 | **20260831** | 63 | evidence_partial |
| `ts_index_weekly` | 20250815 | 444 | **20261009** | 24 | evidence_partial |
| `ts_slb_sec` | 20220810 | 1545 | **20240930** | 763 | evidence_partial |
| `ts_top10_floatholders` | 20250328 | 584 | **20261013** | 20 | evidence_partial |
| `ts_top10_holders` | 20260716 | 109 | **20261013** | 20 | evidence_partial |
| `tdx_sector_min` | 20260901 | 62 | **20260924** | 39 | evidence_partial |

> 修复后 probe 最新日 **= catalog freshness**（抽样偏差消除，不再是「假 stale」）；但相对今天仍超 D7 阈值（lag 17–763 天）→ **仍 evidence_partial（真实 stale）**，属数据侧问题（DATA1 补数/查采集管线），不应 apply 为 ok。

## 四、口径变更对既有 registry 的影响（需后续任务处理）

以下 4 张在 task-24 C 批按**旧口径**被写成 `evidence_ok`（无 date_col 自动豁免），新口径下 dry-run 已判 `evidence_partial/no_date_column`，registry 与当前工具口径不一致：

`index_member_all`、`ts_news`、`ts_major_news`、`ts_cctv_news`

建议（下一写回任务）：① 若确认动态数据 → 重新 `--apply` 降级为 partial（或补 catalog `date_col` 后升回）；② 若确属静态 → 评估后显式加入 `STATIC_EXEMPT_WHITELIST`（需 Lead/口径评审，本轮未改名单）。

## 五、下一步 apply 建议（本轮不执行）

1. **sqlite 3 表**：dry-run 已 evidence_ok → 建议立 apply 单写回（unknown→ok，预计 unknown 113→110、evidence_ok 101→104）。
2. **7 张 part 表**：真实 stale，**不 apply**；转 DATA1 数据侧排查（lang 17–763 天）。
3. **24 张未声明动态表**：不 apply；先定口径（补 `date_col` 或显式白名单，见 task-26 §5.3）。
4. **白名单 7 张**：语义不变，无需动作。

## 六、边界与回滚

- 本单**未 apply、未写 registry/catalog/数据**、未 commit、未重启；所有 3 组 dry-run 均为 `--dry-run` 只读。
- 回滚（未 commit，直接还原两个文件）：

```powershell
git checkout -- tools/datawarehouse/qualify_probe.py tools/datawarehouse/tests/test_qualify_probe.py
```

回滚后复跑 `pytest -q tools/datawarehouse/tests/test_qualify_probe.py`（应回到 45 passed）；无 registry/数据影响。
