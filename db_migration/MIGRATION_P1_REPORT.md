# P1 迁移报告：三个活跃 SQLite 库 → PostgreSQL quant_meta（快照导入）

- **执行时间**: 2026-10-16 18:05–18:30 (UTC+8)
- **执行人**: db-impl-p1 (Engineer Agent)
- **范围**: 仅快照导入 + 校验。**未** 切换任何消费者、**未** 修改任何业务代码、**未** 删除/重命名任何源 .db 文件、**未** 触碰 quant_system 库内任何对象。

---

## 1. 目标环境

| 项 | 值 |
|---|---|
| PG 实例 | Docker `quant-pg`（postgres:15），127.0.0.1:5442 |
| 目标数据库 | `quant_meta`（本次新建，owner=quant_user，LC_COLLATE='C'，LC_CTYPE='C'） |
| Schema 规划 | `news` / `quant` / `tdx_info`（对应三个源库） |
| 口令来源 | `C:\aios\quant_backtest\.env.local` 的 `XMAP_PG_PASSWORD`（全程未打印明文） |
| quant_meta 最终大小 | **333 MB** |
| 源库现状 | `quant_system` 未受影响（public.stock_basic 5597 行完好） |

> LC_COLLATE='C' 的原因：与 SQLite BINARY 排序字节序一致，保证深度校验（有序流式逐行比对）结果确定。

## 2. 迁移统计

### 汇总

| 源库 | 大小 | 目标 schema | 表数 | 总行数 | 耗时 | 校验 |
|---|---|---|---:|---:|---:|---|
| `C:\aios\news_collector\news.db`（活跃库） | 39.7 MB | news | 5 | 75,733 | 27.9 s | **PASS** |
| `C:\aios\quant_backtest\data\quant.db` | 182.6 MB | quant | 6 | 23,885 | 434.4 s | **PASS** |
| `C:\aios\quant_backtest\data\tdx\tdx_info.db` | 196.7 MB | tdx_info | 3 | 581,730 | 93.1 s | **PASS** |
| **合计** | | | **14** | **681,348** | | **OVERALL PASS** |

### 明细（行数 / COPY 耗时）

- **news**: ai_briefs 21 / ai_reviews 13 / news 45,118 (25.8s) / quotes 28,749 (0.6s) / runs 1,832
- **quant**: chanlun_pool 10,339 (0.6s) / nine_dim_fc 4,413 / nine_dim_fc_meta 1 / nine_dim_fina 4,718 (108.0s) / nine_dim_rc 4,413 (319.4s) / nine_dim_rc_meta 1
- **tdx_info**: stock_news 301,084 (62.1s) / stock_notice 133,118 (12.6s) / stock_report 147,528 (12.4s)

### 索引（共 5 个，全部重建）

- quant.chanlun_pool: `idx_pool_code(code)`, `idx_pool_detect(detect_date)`
- tdx_info.stock_news / stock_notice / stock_report: `ix_* (ts_code)`

## 3. 校验结果（validation_p1.json）

1. **全表行数比对**：14/14 表双侧行数一致。
2. **深度校验**（每库 2 张大表）：
   - news.news / news.quotes：PK 的 COUNT、COUNT(DISTINCT)、MIN/MAX 双侧一致；各抽 1,000 行（rowid 取模步进）逐字段比对 **0 差异**。
   - quant.chanlun_pool / nine_dim_fina：同上，**0 差异**。
   - tdx_info.stock_news / stock_report（无主键表）：**全量有序流式逐行比对**（301,084 + 147,528 行，ORDER BY 全列、NULLS FIRST、C collation），**0 差异**。
3. 结论：**OVERALL PASS**。

## 4. 类型映射与特例

| SQLite | PostgreSQL | 说明 |
|---|---|---|
| INTEGER PRIMARY KEY（rowid 别名，含 AUTOINCREMENT） | `bigserial` + PRIMARY KEY | news.runs.id；灌数后 `setval(pg_get_serial_sequence(...), MAX(id)+1, false)` 保持序列连续 |
| INTEGER | `bigint` | |
| TEXT / CHAR / CLOB / 无声明 | `text` | |
| REAL / FLOA / DOUB | `double precision` | |
| BLOB | `bytea`（\x hex，本次无 BLOB 数据） | |
| 复合主键 | `PRIMARY KEY (code, ts)` 等 | news.quotes (code,ts)、quant.chanlun_pool (code,type,dt) |

其他特例：
- **PG 不允许 schema 限定的索引名**（`CREATE INDEX "schema"."idx"` 报语法错误）→ 索引建在表所在 schema 内、名字不带前缀。
- **SQLite 不支持 `COUNT(DISTINCT a,b)`** → 深度校验的复合键去重改用 `(SELECT COUNT(*) FROM (SELECT DISTINCT ...))` 子查询。
- news.content 等长文本含换行/制表/反斜杠 → COPY text 格式转义 `\\ \t \n \r`，`\N` 表示 NULL。

## 5. 快照方式（活跃库安全）

- 使用 Python `sqlite3` **backup API**（`src.backup(dst)`）对每个源库做在线快照，绝不直接复制文件；尤其 news.db 定时任务+门户双访问，backup API 可与并发写共存。
- 快照前设 `busy_timeout=60s`，遇 `database is locked` 自动重试（最多 8 次、间隔 5s），不强杀任何连接。本次三库均一次成功，未发生锁等待。
- 快照文件（迁移后仍保留，供对照/复查）：
  - `C:\aios\db_migration\snapshots\news_snap.db`（39.9 MB）
  - `C:\aios\db_migration\snapshots\quant_snap.db`（182.6 MB）
  - `C:\aios\db_migration\snapshots\tdx_info_snap.db`（201.3 MB）
- 源 .db 文件原样未动（news.db 仍被定时任务持续写入，PG 内为快照时点数据）。

## 6. 产物清单

| 文件 | 说明 |
|---|---|
| `C:\aios\db_migration\sqlite_to_pg.py` | 通用迁移+校验脚本（`migrate` / `validate` 子命令，幂等：先 DROP SCHEMA CASCADE 重建） |
| `C:\aios\db_migration\snapshots\*.db` | 三库快照 |
| `C:\aios\db_migration\manifest_p1.json` | 迁移清单（表/行数/耗时） |
| `C:\aios\db_migration\validation_p1.json` | 校验明细结果 |
| `C:\aios\db_migration\log_quant.txt` / `log_tdx.txt` / `log_val_*.txt` | 运行日志 |
| `C:\aios\db_migration\MIGRATION_P1_REPORT.md` | 本报告 |

## 7. 遇到的问题（均已解决）

1. psycopg2 `copy_expert` 对 `io.BufferedReader` 包装的生成器流报 "error in .read() call" → 改为直接传入实现 `read(n)` 的 `_GenFile` 原始流对象。
2. `setval`/`pg_get_serial_sequence` 中标识符误作绑定参数（被当成字符串字面量）→ 改为白名单化的标识符内插（schema/表/列均为已知小写安全名）。
3. 上述两条修复前 news 库跑了 3 次失败、quant/tdx 各 1 次失败；**因脚本幂等（DROP SCHEMA CASCADE 重建），最终重跑均为干净全量导入**，无残留半截数据。
4. quant 库 nine_dim_rc/nine_dim_fina 装载慢（单行 JSON 文本大，319s/108s）——COPY 通道本身正常，属数据体积原因，无需优化。

## 8. 消费者切换步骤（**后续执行，本次未做**）

### 8.1 改 DSN 的文件清单（现状 grep 核实）

- **news.db 消费者**（→ `news` schema，改连 quant_meta）：
  `news_collector/news_collector.py`、`jev_feed.py`、`news_ai.py`、`news_digest.py`、`quote_snapshot.py`、`daily_report.py`、`news_doc_board.py`、`news_jev_chart.py`、`stock_analyze.py`、`sentiment_backtest.py`、`a3_blindtest.py`、`jev_backfill.py`、`start_jev_feed.ps1` 等（grep `news.db` 共 15+ 处）
- **quant.db 消费者**：`quant_backtest/db.py`（集中 DSN）、`chanlun_scan.py`、`premarket.py`、`data_health.py`、`build_catalog.py`、`test_nine_dim_tdx.py` 等
- **tdx_info.db 消费者**：`quant_backtest/tdx_read.py`、`tdx_sync.py`、`report_fetch.py`、`test_tdx_read.py`、`test_ui_drilldown.py` 等

建议切换方式：优先收敛到 `quant_backtest/db.py` 单点；news_collector 侧建议先抽一个 `db_path()` 帮助函数再统一替换。DSN 形如：
`host=127.0.0.1 port=5442 dbname=quant_meta user=quant_user password=<XMAP_PG_PASSWORD> options='-c search_path=news,public'`
（口令继续从 `.env.local` 读取，禁止硬编码；三条 schema 对应 `search_path` 为 `news`/`quant`/`tdx_info`。）

### 8.2 双跑 30 天对照方案

1. **切换窗口**：选盘后低峰期（如 22:30 收盘任务结束后）。
2. **写侧**：SQLite 为主继续写（保持现网）；PG 侧由同一批任务以"影子写"方式双写（news_collector 在 commit 后异步 upsert 到 PG；quant/tdx 侧同步任务跑完后调用镜像脚本回放增量）。
3. **对照**：每日 `validate`（本脚本已支持）比对双侧行数 + 抽样，差异记录到 `C:\aios\db_migration\drift_log.json`；行数差异预期仅来自"快照时点之后的新增"，需按 ts/id 增量口径核对。
4. **读侧灰度**：第 1–10 天门户只读流量 10% 走 PG（feature flag），比对接口返回一致性；第 11–20 天 50%；第 21–30 天 100% 读 PG、SQLite 仍保留写。
5. **转正**：30 天零 P1 差异后，将写主切到 PG，SQLite 转为 30 天冷备后归档；从 `db.py` 等移除旧 DSN。
6. **回滚**：任一阶段出现 P1 差异 → flag 拨回 SQLite 读，保留 PG 现场，定位后重新计 7 天观察。

## 9. 遗留事项

1. **本报告时点的 PG 数据是快照静态数据**，SQLite 仍在被定时任务写入——消费者切换（第 8 节）完成前不要把 PG 当真源使用。
2. 快照文件共约 424 MB，转正后可清理或归档。
3. news.runs 的 AUTOINCREMENT 已用 setval 对齐；若切换期间 SQLite 侧继续新增行，切换时需在切换脚本里再对齐一次序列。
4. tdx_info 三表无主键、无唯一约束，PG 侧只建了 ts_code 索引；若后续要去重/幂等 upsert，需业务侧定义键（建议 ts_code+pub_date+title hash）。
5. 双跑期间 daily 任务若产生大量 DELETE/UPDATE，镜像回放脚本需要按主键比对而不是行数（本脚本 validate 已含抽样/流比对能力，可直接复用）。
