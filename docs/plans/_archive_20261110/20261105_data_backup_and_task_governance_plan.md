# 数据资产与计划任务治理方案（2026-11-05）

状态：**方案待裁定，本轮未执行任何排班、删除、任务变更**。全部结论来自只读取证。
盘点脚本（临时）：`C:\Temp\analyze_tasks.py` / `analyze_tasks2.py`；任务快照：`C:\Temp\tasks_inventory_20261105.json`（492 条）。
相关前序：`docs/changes/20261105_nextstep_guard_and_backup_gap.md`、`docs/changes/20261105_k260_cainiao_crypto_closure.md`。

---

## 〇 先做一处定性修正

前一轮把 `backups/db` 断档写成「P0 db 备份断链」，**定性过重**。实情是：

- 加密主链 `\BackupDaily`（07:45 每日）**一直健康**：11-05 `failed=0`，产物 2.4G，含 `zhice_db.sql.gz.enc` 807M；流程为 gzip → AES-256 加密 → `C:\backups_mirror` keep-4 → 异地字节一致性校验 → vault 14 天留存，全链路 `rc=0`。
- 断掉的只是**明文副链** `ops/db_dump_daily.py`：492 个计划任务无任何引用，`backups/db` 只有 9 个目录（10-09~10-15 换机前旧机日排班 + 10-29 / 11-05 人工）。
- 因此根因不是「忘了排班」，而是**两条链职责重叠、且没有单一事实来源**说清谁负责哪个库。

真正**没有任何链覆盖**的是 `cainiao_mysql` 与 `quant_pg`；`zerobound-neo4j` 仍是零备份（旧挂账 P1）。

---

## 一 事实底账

### 1.1 两条备份链对照

| 维度 | `\BackupDaily` 主链 | `db_dump_daily.py` 副链 |
|---|---|---|
| 状态 | `Ready`，11-05 `rc=0` | 无排班，脚本可用 |
| 覆盖 | `zhice_os_v2` 单库 + 4 个 tar + `neo4j-kg` | 3 库 + 2 个 Neo4j（`must:False` 设计内 FAIL） |
| 压缩 | 容器内 `pg_dump \| gzip -c`，宿主 `Test-Gzip` 全量解压校验 | 宿主 `gzip.open` 收流（K-STOCK-414 后正确，11-05 起生效） |
| 加密 | AES-256-CBC + HMAC-SHA256 → `.enc` | 无 |
| 镜像/异地 | `C:\backups_mirror` keep-4 + 异地 byte-identical + vault 14d | 无 |
| 轮转 | `$KeepCount = 4` | `KEEP_DAYS = 14`（回收站 + exists 复核） |
| 恢复演练 | **从未做过解密恢复演练** | `\JevRestoreDrill` 每月 1 日，只验 `quant_pg` |
| 11-05 产物 | 2.4G（其中 zhice `.enc` 807M） | 1.0G（其中 zhice gz 800M —— **与主链同一库重复**） |
| 耗时 | 07:56 → 08:33 ≈ 37 min（含 vault 上传 278.6s） | 10:08:43 → 10:25:05 ≈ 16.4 min（zhice 单库 830s） |

其余备份链：`\ResticTrialDaily`(02:20 `rc=0`) 覆盖的是文件/配置资产（`_vault`、`registry`、`catalogs`、`secrets`、`deliverables` + SFTP 异地），**不覆盖数据库**；`\KG-Neo4jWeeklyDump`(周五 05:30) 覆盖 `neo4j-kg`。

### 1.2 数据库覆盖矩阵

| 资产 | 主链 | 副链 | 其它 | 判定 |
|---|---|---|---|---|
| `zhice_os_v2` (11 GB) | ✅ 每日加密+异地 | ✅ 重复 | — | **冗余**，可择一 |
| `cainiao_mysql` | ❌ | ✅ 但断排班 | — | **真空** |
| `quant_pg` | ❌ | ✅ 但断排班 | b4 演练的数据源 | **真空** |
| `neo4j-kg` | ✅ tar | 设计内 FAIL | 周 dump | OK |
| `zerobound-neo4j` | ❌ | 设计内 FAIL | ❌ | **零备份（P1 挂账）** |

### 1.3 计划任务全集（492 条，字节级复核）

| 指标 | 值 | 说明 |
|---|---|---|
| 状态 | Ready 407 / Disabled 76 / Running 9 | — |
| 归属 | 自定义 293 / 系统 199 | 系统含 `\Microsoft\` 199 条 |
| 登录类型 | Interactive 185 / S4U 123 / ServiceAccount 107 / Group 77 | S4U 空转风险面 |
| 自定义真实失败 | 32 | 已排除 267009/267011/2147943140/1073807364 等 Scheduler 状态字 |
| 自定义从未运行 | 24 | `1999-11-30` 哨兵值 |
| 自定义 Disabled | 45 | 含 14+ 个 `DSH-Merge-*/DSH-Prep-*` 一次性残留 |
| 早高峰 06–09 点 Ready | 76 | 06:13 / 07:17 / 08:24 / 09:22 |
| `miss_check.REGISTRY` | 23 条，覆盖 18 个自定义任务 | 覆盖率约 6%；其中 `CainiaoPull` 指向不存在任务（设计上保留报 MISS） |
| 引用 `db_dump_daily` / `cainiao_pull` | 0 / 0 | 与任务清单一致 |

> 打印通道会串味数字（本机已知 `10↔68`、`16↔91`），例如任务清单里出现 `2026-10-16T04:18:34`、文档名 `..._v11_2026-10-16.md`。本方案所有关键计数均经 `ord()` 码点复核，**不引用任何未经复核的日期字面量**。

### 1.4 体积与增长结构

- 11-05 副链：原始 SQL 文本 `15,132,051,150 B ≈ 14.09 GiB` → gzip `1,066,759,563 B ≈ 1017 MiB`，压缩比 **14.2:1**。
  （订正：上一轮曾写成 `15,132,050,150 B`，少 1000 B；已用码点 round-trip 自动解码复核。）
- 分库 gz：`zhice` 800,927,142 B（75%）/ `cainiao` 153,356,441 B / `quant_pg` 112,475,980 B。
- `zhice_os_v2` 物理 `pg_database_size = 11,325,403,951 B`（11 GB），`pg_inherits = 0`（**无分区**）：
  `v2_bar_minute` 16,847,380 行 / 4980 MB；`aw_decision` 3,331,537 行 / 4118 MB；`v2_bar_daily` 4,085,629 行 / 1213 MB，跨度 `2013-07-11 → 2026-11-05`；`v2_adj_factor` 389 MB。
- 反直觉但重要：**逻辑 dump 的 gz (800 MB) 反而小于物理库 (11 GB)**，成本不在存储而在**文本生成 + IO**（830s、约 11.2 GB 文本流）。C 盘余 1108 GB；按十进制口径 14 天约 14.9 GB（13.9 GiB）、60 天约 64.0 GB（59.6 GiB），磁盘不是瓶颈。

### 1.5 逐条核对「这几个问题」

| # | 问题 | 复核结论 | 状态 |
|---|---|---|---|
| Q1 | 备份为什么 ~1GB/天 | 三库全量逻辑导出，已 gzip 14.2:1；数据本身 11 GB 且分钟线持续增长 | 架构性成本，非故障 |
| Q2 | 10-29 的 11 GB 假 `.gz` | 魔数 `2d 2d`、`gzip.open` 全报 `BadGzipFile`；**不是新发现**——`tools/b4_restore_drill.py` 注释 10–15 行已完整记录成因（Windows 下子进程写原 fd，gzip 头在 close 才追加）与修复。残留问题只是**坏档仍在盘上且 manifest 标 `ok=true`** | 已修代码 / 残留待治理 |
| Q3 | `KEEP_DAYS` 14 vs 60 | `git log -S"KEEP_DAYS = 60"` 为空，全部 worktree 副本均为 14 → 60 从未入版本库，日志那句是临时口径 | 口径可收口为 14 |
| Q4 | `--keep-days` 参数 | `main()` 无 argparse，参数从未实现，传了被静默忽略 | 文档幻觉，已改 docstring |
| Q5 | 492 个任务 | 见 §1.3；核心不是数量多，是**只有 23 条在哨兵底册、32 个业务失败无人分诊** | 可观测性缺口 |
| Q6 | 17 分钟/天 | zhice 单库 830s；若裁掉与主链重复的 zhice，全轮 982s → 152s | 可通过去重消解 |
| Q7 | **新 P1：加密主链没验过解密** | `Unprotect-File` 只有定义（backup_daily.ps1:302）与文档注释，**无任何自动化调用或恢复演练**；AES 密钥 `~/.backup_aes_key` 在位（44 B）。每天证明的是「异地字节一致」，不是「能解开恢复」 | 真风险，需补演练 |

---

## 二 根因归并（避免逐条打补丁）

| 类 | 根因 | 表现 |
|---|---|---|
| R1 | **职责重叠 + 无单一事实来源** | zhice 被两条链各 dump 一次；两库真空；没人说得清谁负责哪个库 |
| R2 | **可观测性靠手写底册** | 293 个自定义任务只有 23 条在册；32 个真实失败、24 个从未运行、45 个 Disabled 僵尸无人分诊 |
| R3 | **写入侧缺格式硬闸门** | manifest 信 `rc` 不信魔数，10-29 三个非 gzip 文件仍标 `ok=true`；文档与常量各说各话 |
| R4 | **数据无分层** | 13 年日线 + 1684 万行分钟线单表无分区，唯一策略就是每天全量逻辑导出 |

---

## 三 治理方案（四阶段，标注批准级别）

### 阶段 0 · 立台账、收口径（**无需 L2，纯代码/文档层**）

1. 把 `C:\Temp\analyze_tasks*.py` 固化为 `ops/task_inventory.py`，产出快照 + diff，作为任务侧**单一事实来源**；`miss_check.REGISTRY` 从快照生成关键项，不再手工增补。
2. 修三处口径：`db_dump_daily.py` docstring（已改）、`20261105_nextstep_guard_and_backup_gap.md` §三（把「P0 全链断」改为「明文副链断档 + 两库真空」）、`miss_check.py` 相关注释。
3. 10-29 坏档**只做隔离标记、不删除**：目录内写 `INVALID_GZIP.flag`，manifest 标 `format: plain-sql, recoverable: false`，确保 `b4_restore_drill.py::list_backups()` 与任何恢复流程不会选中它。
4. 哨兵补 `JevRestoreDrill` / `BackupDaily` / `ResticTrialDaily` 关键链；`DbDumpDaily` 未排班前以 `pending` 标记，避免制造假 MISS。

**验收**：盘点脚本可重复运行且数字与 §1.3 一致；坏档不被演练选中；三处口径自洽；哨兵关键链 100% 在册。

### 阶段 1 · 补齐真空（**需 L2 五要素批准**）

核心决策：**`\DbDumpDaily` 排班时，副链要不要裁掉与主链重复的 zhice**。

| 选项 | 做法 | 每日成本 | 评价 |
|---|---|---|---|
| **A（推荐，附前置）** | 副链只 dump `cainiao_mysql` + `quant_pg`；zhice 交给加密主链 | ≈ 152s（2.5 min）、≈ 254 MiB、raw ≈ 3.15 GB | 省 830s / 天；**硬前置：先完成一次 `.enc` 解密恢复演练**（D0），否则等于把 zhice 托付给一条没验过恢复的链 |
| B | 三库原样排班 | ≈ 982s（16.4 min）、≈ 1017 MiB | 最保守、双份冗余；重复成本持续存在 |
| C | 两库并入 `\BackupDaily` 主链 | 主链 +2 库 | 主链失败域变大、长跑更久；**不推荐** |

同批处理：`\CainiaoPull`(19:00) 排班（脚本已 9/9 验收、源镜像 ALL_MATCH）；轮转天数定为 **14**（60 无版本依据）。

### 阶段 2 · 质量闸门（**代码层可做，演练扩面需批准**）

1. 写入即验：gzip 魔数 `1f8b` + 流式解压前 8 MB + sha256，任一失败即 `ok=false` 并推飞书；manifest 记录**真实格式**，不再只信 rc。
2. 扩 `b4_restore_drill.py`：从「只验 `quant_pg`」扩到「验当日全部必保产物」；新增**每月一次 `.enc` 解密抽检**（`Unprotect-File` → gzip 校验 → 行数抽样），补上 Q7。
3. `zerobound-neo4j` 零备份：需裁定停库窗口 dump 或迁 bind mount（旧挂账，本方案不擅自决定）。

### 阶段 3 · 架构性成本（**需业务裁定，非本轮**）

1. **数据分层**：评估 `v2_bar_minute` / `aw_decision` 是否可由上游重建；可重建则按表排除或降频（周/月全量 + 每日增量），不可重建则维持全量。
2. **分区**：`pg_inherits=0`，13 年日线挤在单表；按 `trade_date` 分区后可只备份热分区（大工程，需独立立项）。
3. **任务瘦身**：45 个自定义 Disabled 中 14+ 个 `DSH-*` 一次性残留、24 个从未运行任务 → 清单化分批退役（先禁用观察，删除走回收站）。
4. **错峰**：76 个 Ready 挤在 06–09 点（08 点 24 个）；把无时序依赖任务移到 10 点后，降低 IO/CPU 争抢。注意 03:30 仅 4 个任务，`\DbDumpDaily` 排 03:30 **不加重早高峰**。

---

## 四 待裁定清单

| # | 决策 | 选项 | 建议 | 级别 |
|---|---|---|---|---|
| D0 | `.enc` 解密恢复演练 | 立即做 / 随季度 | **立即做**（A 的硬前置） | 只读演练，建议先报备 |
| D1 | `\DbDumpDaily` 范围 | A / B / C | **A**（D0 通过后） | L2 |
| D2 | `\CainiaoPull` 19:00 排班 | 批 / 不批 | 批 | L2 |
| D3 | 轮转天数 | 14 / 60 | **14** | L2 |
| D4 | 10-29 坏档 | 隔离标记 / 重压缩 / 删除 / 不动 | **隔离标记（不删）** | 无需 |
| D5 | `zerobound-neo4j` | 停库窗口 / bind mount | 待业务裁定 | L2 |
| D6 | 任务瘦身与错峰 | 分批退役 / 维持 | 分批，先禁用观察 | L2 |

## 五 L2 五要素报批卡（阶段 1 主项，批准后才执行）

- **命令全文**（D1 选 A 时）：
  ```powershell
  # 前置 D0：解密演练 rc=0 后
  $a = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
        -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\ops\start_db_dump_daily.ps1"' `
        -WorkingDirectory 'C:\aios\ops'
  $t = New-ScheduledTaskTrigger -Daily -At 03:30
  $p = New-ScheduledTaskPrincipal -UserId 'DESKTOP-ARF447U\t' -LogonType Interactive -RunLevel LeastPrivilege
  Register-ScheduledTask -TaskName 'DbDumpDaily' -Action $a -Trigger $t -Principal $p
  Start-ScheduledTask -TaskName 'DbDumpDaily'   # 验证
  ```
  D2 同形，任务名 `CainiaoPull`、时间 19:00、动作指向 `cainiao_pull.py --apply`。
  选 A 时需先把 `ops/db_dump_daily.py` 的 `DUMPS` 中 `zhice_pg` 项改为可配置开关（默认关闭），并同步 `b4_restore_drill.py` 的必保清单。
- **影响面**：每日 03:30 起约 2.5 分钟（选 B 则 16.4 分钟），写 `backups/db/YYYY-MM-DD/` 约 254 MiB（选 B 约 1017 MiB）；只读 dump，不碰线上库。前置条件：Docker Desktop 与两个/三个容器在 03:30 已运行。磁盘按 14 天约 3.7 GB（选 B 约 14.9 GB，均为十进制）。03:30 档现只有 4 个任务，不加重 06–09 点早高峰。
- **不可逆点**：轮转删除走 `SHFileOperationW FOF_ALLOWUNDO` 进回收站（可恢复），但 10-09~10-15 / 10-29 历史留痕会被移走 → **首次排班前建议先把 10-09~10-15 证据目录另存**；新增任务本身可禁用/注销。
- **替代方案**：① 挂进 `\BackupDaily`（失败域变大，不推荐）；② WorkBuddy automation（依赖会话在线，不稳）；③ 维持人工（21 天仅 2 次，不推荐）；④ 只加哨兵不排班（每晚报 MISS 但不产数据，不解决根因）。
- **回滚预案**：`Disable-ScheduledTask -TaskName 'DbDumpDaily'` 或 `Unregister-ScheduledTask -Confirm:$false`；从回收站恢复被轮转目录；`miss_check.REGISTRY` 删对应条目；`DUMPS` 开关改回；wrapper 文件保留不生效。

## 六 量化验收指标

| 指标 | 现状 | 目标 |
|---|---|---|
| 必保库当日可恢复产物覆盖 | 3/5（zhice/cainiao/quant_pg 中仅 zhice 有主链） | 5/5（含 zerobound-neo4j 裁定后） |
| `.enc` 解密演练 | 0 次 | ≥1 次 `rc=0`，此后每月抽检 |
| gzip 魔数全绿 | 11-05 3/3；10-29 0/3 | 新产物 100%，历史坏档 0 个被演练选中 |
| 关键链哨兵覆盖 | 18/293 自定义任务 | 关键链 100% |
| 单日备份窗口 | 16.4 min（副链，未排班） | ≤ 3 min（选 A） |
| 自定义任务真实失败 | 32 个未分诊 | 全部有归因标签（真故障/设计内/退役） |

## 七 本轮执行边界声明

本轮**只做只读取证 + 出方案**：未新增/修改/启用任何计划任务，未删除或改写任何备份产物，未改动 `db_dump_daily.py` 的 `DUMPS` 与 `KEEP_DAYS`，未执行解密演练。仅新建本文档与工作日志追加。
