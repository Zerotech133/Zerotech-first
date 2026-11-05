# 2026-11-05 · 数据备份与计划任务治理：统一口径声明（权威）

> **本文件是 W-10，属本轮 GOV-W 白名单新增文件。**
> 定位：AC3「三处口径自洽」的**唯一权威声明件**。因 git 工作区在本轮落地前已有
> 110 处既有未提交改动（含 `ops/db_dump_daily.py`），按硬边界「只新建文件、不碰
> 任何已 modified 文件」，**不修改原始文件，改由本文档统一声明覆盖**。
> 凡既有文档/注释与本文冲突，一律**以本文为准**；原文保留作为历史痕迹，不删不改写。

---

## 零、一句话定性（唯一对外口径）

> **加密主链健康，缺的是明文副链排班与格式闸门。**

台账技术事实层（验收用，保持原文）：**明文副链断档 + 两库真空**。
两层分工见 W-11 §叙事口径/验收口径对照表；本文不引入第二条 slogan。

---

## 一、口径 A：备份链定性 —— 撤回「P0 db 备份断链」

### 1.1 被撤回的错误结论

`docs/changes/20261105_nextstep_guard_and_backup_gap.md`（2026-11-05 13:24 建档）
标题写作「db 备份**断链**」，§三列为「待批（**P0**）：db 日备份排班」。
该定性**过重且不准确**，予以撤回。

### 1.2 更正后的定性（本文生效）

| 链 | 宿主任务 | 状态 | 产物 |
|---|---|---|---|
| **加密主链** | `\BackupDaily`（每日 07:45/07:56，历史 rc=0） | **健康，一直未断** | `zhice_db.sql.gz.enc` / `quant_code.tar.gz` / `neo4j_kg.tar.gz`，AES-256-CBC + HMAC-SHA256，`C:\backups_mirror` keep-4，异地 byte-identical，vault 14d |
| **明文副链** | 无自动排班（`\DbDumpDaily` 未注册） | **断档**（不是断链） | `backups/db` 下 `.sql.gz`，由 `ops/db_dump_daily.py` 生成 |

**关键事实**：`backups/db` 至今只有 9 个日期目录（10-09~10-15、10-29、11-05），
其中 10-09~10-15 是换机前旧机日排班产物，10-29 是三个假 gzip，
**11-05 那次是人工跑的**。因此「db dump 每周至少 1 次成功」这条结论
**从来没有真正成立过**（老田 2026-11-05 裁定），一并作废。

**根因更正**：不是「主备份断了」，而是
**两条链职责重叠 + 缺少单一事实来源 + 明文副链无排班 + 格式闸门缺失（R1~R4）**。
`\BackupDaily` 保留，不删、不停、不改。

### 1.3 两库真空（阶段 1，需 L2，未批准）

`cainiao_mysql` 与 `quant_pg` 在加密主链中无覆盖；`zerobound-neo4j` 零备份（D5 待业务裁定）。
`neo4j-kg` 由 `\BackupDaily` 的 tar 覆盖，`kg_neo4j` 的 `must:false` 是
`db_dump_daily.py` 的**有意设计**，不是假绿 bug（此前误判已撤回）。

---

## 二、口径 B：`KEEP_DAYS` 与 `--keep-days`

| 项 | 权威结论 | 证据 |
|---|---|---|
| `KEEP_DAYS` | **14**，源码常量（`ops/db_dump_daily.py` 第 43 行），`rotate(KEEP_DAYS)` 第 179 行调用 | 全仓 worktree 副本均为 14；`git log -S"KEEP_DAYS = 60"` 为空 |
| `--keep-days` | **从未实现**。`main()` 不解析 `sys.argv`，旧 docstring 的 `[--keep-days 14]` 是幻觉，传了会被静默忽略 | `db_dump_daily.py` 第 17-18 行已自行注明 |
| 轮转风险 | `KEEP_DAYS=14` 会让首次排班运行删掉 14 天前产物，属**数据删除**动作 | `db_dump_daily.py` 第 26 行「排班前必读」 |
| 14 / 60 | **待裁定 D3，未批准**。本轮不改常量、不排班、不轮转 | 治理方案 §四 D3 |

**体积口径（订正后，十进制 / 二进制双列，禁止混用）**：

- 原始 SQL 文本总量 `15,132,051,150 B ≈ 14.09 GiB ≈ 15.13 GB`
- gzip 压缩产物 `1,066,759,563 B ≈ 1017 MiB ≈ 1.07 GB`
- 保留 14 天约 **14.9 GB**；保留 60 天约 **64.0 GB**（十进制）
- 折二进制约 13.9 GiB / 59.6 GiB
- 10-29 单日三个 `.sql.gz` 合计 `11,375,100,939 B` 为**纯 SQL 文本冒充 gzip**（见口径 C）

> 数字串味防护：以上数值均经 `ord()` 码点 round-trip 复核，未凭打印结果二次解析。

---

## 三、口径 C：10-29 假 gzip 与格式闸门

### 3.1 事实（2026-11-05 实测）

`backups/db/2026-10-29/manifest.json`（无 BOM）声明 `total=5 / ok_count=3 / must_fail=0`，
但目录下**实际只有 4 个文件**：

| 文件 | 实测魔数 | `format` | `ok` |
|---|---|---|---|
| `cainiao_mysql_alldb.sql.gz` | `2d2d` | `plain-sql` | `false` |
| `quant_pg_alldb.sql.gz` | `2d2d` | `plain-sql` | `false` |
| `zhice_os_v2.sql.gz` | `2d2d` | `plain-sql` | `false` |
| `manifest.json` | — | — | — |

manifest 中 `kg_neo4j.dump.gz`、`zerobound_neo4j.dump.gz` 两条 `must:false`
**在磁盘上不存在对应文件**。故「`ok_count=3`」是 **manifest 信 rc 不信魔数**导致的假绿。

### 3.2 写入侧根因与修复状态

Windows 下 `gzip.open` + `subprocess.run(stdout=f)` 时子进程写原 fd、
gzip 头在 close 才追加 → 产物为纯 SQL 文本但后缀 `.gz`。
**K-STOCK-414 已修写入侧（宿主侧 gzip 收流，11-05 起生效）**，历史文件不重压缩。

### 3.3 本轮处置（W-08）

新建 `backups/db/2026-10-29/INVALID_GZIP.flag`（八字段，纯新增）：
`files` 聚合 3 个坏档、`format=plain-sql`、`recoverable=false`、
`detected_magic=2d2d`、`policy=mark-only, do-not-delete`、`decision_ref=D4`。

**严禁误读（ADR-05 / ADR-20 / T-11）**：
- flag **只是隔离留痕 + 显式 `--date` 时的人工拦截凭据**，
  **不改变 `b4_restore_drill.py::list_backups()` 的行为，不是运行态过滤闸门**；
- `list_backups()` 只收「含 `quant_pg_alldb.sql.gz`」的日期目录并倒序，
  `main()` 取 `backups[0]`，**不做格式过滤**；
- 默认演练选中 11-05 而非 10-29，是**按日期倒序的既有行为**，与 flag **无因果关系**；
- 自动过滤待 **D4 裁定后实施期**接入 `should_select(verify_artifact(p))`，本轮不实现；
- 既有 `manifest.json` 与三个 `.sql.gz` **零改写、零删除、零改名、零重压缩**。

---

## 四、口径 D：脚本 / 任务 / 注释的职责边界

| 主体 | 权威职责 | 常见误述（作废） |
|---|---|---|
| `jev-eval/backup_daily.ps1`（`\BackupDaily`） | 加密主链，07:45/07:56 每日，rc=0 | 「它不备份 db，所以 db 备份断了」 |
| `ops/db_dump_daily.py` | 明文副链，**当前无自动排班** | 「每日自动跑」「由它每日备份 cainiao 库」 |
| `quant_backtest/cainiao_pull.py` | 九表源 MySQL → 本地镜像同步，9/9 `ALL_MATCH`，62393 行 | 「该库由 `db_dump_daily.py` 每日备份」（误导注释，已纠错） |
| `\CainiaoPull`（19:00） | **任务不存在**，脚本已就绪 | 「已恢复排班」 |
| `\CryptoSync` | 已恢复：wrapper 改接 `export_us_hk.py --market CRYPTO --batch 200` | 「启用旧的直连 Binance `crypto_sync.py`」（会假绿空转） |
| `\K260RegressionDaily`（06:40） | 已重建，图谱 2 条漂移已修 | 「ChanlunRefill 06:30」（旧名） |

---

## 五、覆盖范围与硬边界

- 本文只做**口径声明**，不改任何既有文件、不排班、不轮转、不解密、不删产物。
- D0~D6 七项裁定**全部未批准、不自动生效**；`\DbDumpDaily`、`\CainiaoPull` 排班
  须另走 L2 五要素报批。
- 阶段 1（补齐两库真空）、阶段 3（11GB 库分区 / 日线单表 / 分钟线 1684 万行）
  均为**远期目标态**，不是本轮验收项（ADR-02）。
- 治理方案 §六 量化指标（必保库 5/5、解密演练 ≥1 次、单日窗口 ≤3min、
  哨兵 100%、失败任务全归因）同为**阶段 1/3 目标态**，不得写成本轮达成。

---

## 六、权威声明

三处口径至此自洽为「**明文副链断档 + 两库真空**」：
1. `docs/changes/20261105_nextstep_guard_and_backup_gap.md` —— 「P0 断链」撤回（口径 A）；
2. `ops/db_dump_daily.py` —— `KEEP_DAYS=14` / 无 `--keep-days` / 14-60 待裁定（口径 B）；
3. `docs/plans/20261105_data_backup_and_task_governance_plan.md` —— 更正定性为权威主件（口径 A/C/D）。

因预检发现 `ops/db_dump_daily.py` 等已处于 modified 状态，
**本轮以本文统一声明替代改原文件**，满足 AC3 且不触碰任何既有未提交改动。

[统一口径声明 · W-10]
