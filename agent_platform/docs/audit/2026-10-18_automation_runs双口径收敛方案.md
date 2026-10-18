# automation_runs 双口径收敛方案（G 路 · 纯文档 · 零操作）

- **日期**：2026-10-18
- **作者**：dev-automation-plan（老田个人 AI 组 G 路开发代理）
- **性质**：只读调查 + 方案文档。全程零文件操作——未迁移、未删除、未重命名、未修改 `C:\项目\老田的AI助手\automation_runs\` 与 `C:\aios\automation_runs\` 内任何实际文件与目录。
- **拍板入口**：《2026-10-18_今日待拍板事项总裁决清单.md》**06 项**（仓根 automation_runs/ 去留，选项 A/B/C）。本方案为 06 项提供决策依据，执行待老田拍板。
- **关键判例**：0e70a40f（10-18 07:34）「定时任务产物统一收纳 C:\aios\automation_runs（老田拍板：产物/日志/临时文件不再散落）：数据资产（xueqiu_info.db/派生表）不动」。

---

## 〇、调查结论先行（颠覆前提的事实）

**「双口径」实为同一物理目录的两条路径，并非两个并存目录。**

- `C:\aios` 是一个 **Junction**，指向 `C:\项目\老田的AI助手`（创建时间 2026-10-15 22:48:00，`fsutil reparsepoint` 与 `Get-Item Target` 均证实）。
- 因此 `C:\aios\automation_runs` ≡ `C:\项目\老田的AI助手\automation_runs`，两者条目、文件数、字节数、mtime **完全一致**（本轮盘点逐项比对吻合，仅 xq_classify 在两次盘点间隔内多写 1 次，恰证明同一目录实时可见）。
- 《并行会话冲突地图》§一G「仓根又冒出一个同名目录——两处并存」的前提**不成立**；「谁在写仓根目录未认领」之谜已解：**遵守 0e70a40f 判例、用 `C:\aios\automation_runs` 路径写的脚本，就是仓根目录的写入方**（经 junction 落回仓根）。
- `tools/execution_watchdog.py:19` 已有注释明示此事实：「C:\aios junction 即仓内 automation_runs，经 repo 路径写 = 同一落点」。

因此 06 项选项 A 的「仓根版归并入 C:\aios 后移除」在物理上无从执行（同一目录无法自我并入）；选项 B 的「仓内规范化收纳 + .gitignore 口径」与 A 在物理层面等价。**真正的收敛工作是路径口径统一 + .gitignore 判定 + C:\Temp 散落日志改造**，见 §三。

---

## 一、两目录现状对照表

统计时点：2026-10-18 11:35–12:00。两目录逐条一致，合并为一张表。

| 任务名（子目录） | 物理位置 | 文件数 | 大小 | 最近 mtime | 谁在写（证据见 §二） |
|---|---|---|---|---|---|
| `xq_backfill` | 同一目录（仓根/junction 双路径） | 67 | 281K | 10-18 11:35 | `xq_backfill_auto.py`、`xq_collect_1000.py`（C:\aios 口径，0e70a40f 已改） |
| `xq_classify` | 同上 | 8 | 354K | 10-18 12:00 | `xq_classify.py`（C:\aios 口径）；**status.log 12:00 仍在活跃写入，在途** |
| `xq_report` | 同上 | 6 | 29K | 10-18 11:43 | `xq_daily_report.py`（C:\aios 口径，M-RT3 情报日报） |
| `execution_watchdog` | 同上 | 5 | 9.0K | 10-18 11:48 | `tools/execution_watchdog.py`（仓内相对口径） |
| `tools` | 同上 | 3 | 12K | 10-18 06:53 | 并行会话手投探针件（seed_cursor.py / xq_deep_check.py / xq_ollama_probe.py） |
| `_archive_from_quant_backtest` | 同上 | 4 | 17K | 10-18 06:53 | 0e70a40f 迁移时的归档副本（backfill 状态文件 + tools 探针的旧版） |
| `.workbuddy` | 同上 | 5 | ~8K | 10-18 06:22 | WorkBuddy automation 自身的 memory 记录（平台件，非任务产物） |
| `retro_brief`（**尚不存在**） | — | 0 | — | — | `tools/retro_brief.py` 已指向此落点但尚未产生文件（首次运行前） |
| （散件）`fix_task_logon.ps1` | 同上根 | 1 | 1.2K | 10-17 18:15 | 登录修复散件，非任务产物，疑似应归档 |

**git 状态**：`automation_runs/` 为 untracked（`??`），**未被 .gitignore 忽略**（`.gitignore` 中无 automation_runs/aios 条目）——产物持续污染 `git status`。

---

## 二、写侧盘点（文件:行号级证据）

### 2.1 已指向 `C:\aios\automation_runs`（合规口径，8 处）

| 文件:行号 | 内容 |
|---|---|
| `quant_backtest/xq_backfill_auto.py:24` | `RUNS_ROOT = r"C:\aios\automation_runs"`（注明 2026-10-18 老田拍板） |
| `quant_backtest/xq_collect_1000.py:44,46` | `CURSOR_PATH`/`SEED_CURSOR_PATH` → `C:\aios\automation_runs\xq_backfill` |
| `quant_backtest/xq_classify.py:40` | `LOG_DIR = Path(r"C:\aios\automation_runs") / "xq_classify"`（注明拍板） |
| `quant_backtest/xq_daily_report.py:28` | `OUT_ROOT = r"C:/aios/automation_runs/xq_report"` |
| `quant_backtest/xq_seed_auto.py:27` | `RUNS_ROOT = r"C:\aios\automation_runs"` |
| `portal/api_dataops.py:23` | `RUNS = r"C:/aios/automation_runs"`（读） |
| `portal/api_xq_intel.py:21` | `REPORT_ROOT = Path(r"C:/aios/automation_runs/xq_report")`（读） |
| `jev-eval/n1_check.ps1:265` | `$ExWdLog = 'C:\aios\automation_runs\execution_watchdog\last_run.json'`（读心跳） |

### 2.2 指向仓内相对路径（落点相同、口径不同，4 处）

| 文件:行号 | 内容 | 说明 |
|---|---|---|
| `tools/execution_watchdog.py:47` | `STATE_DIR = _REPO / "automation_runs" / "execution_watchdog"` | 写；:19 注释已声明与 C:\aios 等价 |
| `tools/retro_brief.py:62,64` | `BRIEFS_DIR`/`STATE_DIR = _REPO / "automation_runs" / "retro_brief"` | 写 |
| `portal/api_brief.py:30` | `BRIEFS_DIR = _REPO_ROOT / "automation_runs" / ...` | 读 |
| `portal/api_evolution.py:33` | `WATCHDOG_DIR = _REPO_ROOT / "automation_runs" / ...` | 读 |

### 2.3 仍散落 `C:\Temp`（违反判例的临时文件落点，重点改造面）

全仓含 `C:\Temp` 重定向的 .ps1 共 **30 个**（grep `-rl 'C:\Temp' --include=*.ps1`），覆盖 news_collector（start_news_collect/news_digest/news_review/news_ai/quote/sentiment_bt/daily_report/alert_push/jev_feed/jev_feed_verify 等）、quant_backtest（tune_bt_batch/start_kg_sync/start_bt_scan/start_chanlun_scan/start_adj_factor_sync/start_strategy_daily/start_resonance_notify/start_seat_profile/col_audit_task/k266_services/deploy_tushare_sync）、jev-eval（n1_check/auto_verify_jobs/offsite_sync）、ops（start_legion_ops/start_pg_robot_watch）、portal（start_portal_api/start_todays_picks）、gzh（start_gzh_writer）、db_migration（start_meta_sync）。

指定核查的两件（行级证据）：

- `quant_backtest/tune_bt_batch.ps1:3,9-11,13,17`：日志落 `C:\Temp\tune_bt_batch.log`、stdout/stderr 落 `C:\Temp\tune_bt_batch_out.log`/`_err.log`（脚本自身已用 junction 路径 `C:\aios\quant_backtest\bt2_tune_batch.py` 调 py，仅日志散落）。
- `quant_backtest/start_kg_sync.ps1:76,79,90,138-139`：WARN/ABORT 落 `C:\Temp\kg_sync_task.log`，各步 stdout/stderr 落 `C:\Temp\kg_<step>_out.log`/`_err.log`。

### 2.4 判例 0e70a40f 已完成的迁移（本方案不重复执行）

07:34 判例已迁：backfill 53 + classify 5 + tools 3 共 61 文件；xq_backfill_auto.py / xq_collect_1000.py / xq_classify.py 三件源码常量改新路径；状态文件保内容迁移；wrapper 实测 skip done=true 零请求。数据资产（xueqiu_info.db/派生表）未动——符合「数据资产不迁」原则。

---

## 三、收敛方案（分步，全部待拍板后执行）

### 第 1 步：路径口径统一（写侧改造清单）

**拍板子项 1-a：唯一路径口径选哪种？**
- **推荐：绝对 junction 路径 `C:\aios\automation_runs\<任务名>\`**。理由：① 0e70a40f 判例原文即此口径；② 计划任务（schtasks）多在非仓 cwd 启动，绝对路径最稳；③ 现状 8 处已合规，改 4 处即可。
- 备选：仓内相对路径 `_REPO/automation_runs`。仅当老田担心 junction 被删时选；改 8 处且与判例原文不符。

**第 1 步改造点（若选 junction 口径，共 4 文件 6 行）：**

| 文件:行号 | 改法 |
|---|---|
| `tools/execution_watchdog.py:47` | `_REPO/"automation_runs"/...` → `Path(r"C:/aios/automation_runs")/...`，并在 :19 注释保留 junction 等价说明 |
| `tools/retro_brief.py:62,64` | 同上，`BRIEFS_DIR`/`STATE_DIR` 改绝对 junction 路径 |
| `portal/api_brief.py:30` | 读侧同步改（保持读写口径一致，便于 §五 grep 验收） |
| `portal/api_evolution.py:33` | 读侧同步改 |

### 第 2 步：C:\Temp 散落日志收编（30 个 .ps1，按任务名落 `C:\aios\automation_runs\<任务名>\`）

- **优先批（与今日主线相关，3 个）**：`tune_bt_batch.ps1`（`tune_bt_batch.log/out.log/err.log` → `C:\aios\automation_runs\tune_bt_batch\`）、`start_kg_sync.ps1`（`kg_sync_task.log` 与 `kg_<step>_*.log` → `C:\aios\automation_runs\kg_sync\`）、`jev-eval/n1_check.ps1` 自身的 C:\Temp 巡检落点。
- **常规批（27 个）**：news_collector/ops/portal/gzh/db_migration 各启动脚本逐一改重定向，**每个脚本一个子目录，以计划任务名命名**；建议分批走，每批改完跑一次对应计划任务验证。
- 注意：部分脚本非本组禁区内的在途件改造前先对表冲突地图（news_ai.py/sentiment_backtest.py 相关启动脚本属 D 批在途区，见 §四）。

### 第 3 步：.gitignore 口径（对应 06 项 B 的「定口径」半边）

- 在 `.gitignore` 增加 `automation_runs/`（若老田选「产物不入库」——推荐，判例口径本就是产物/日志/临时文件不进版本库）。
- 若老田希望部分机读投影（如 xq_report/daily/latest.json）入库供 portal 离线消费，则改为白名单式：忽略整目录 + `!automation_runs/xq_report/daily/`。**推荐先整目录忽略，portal 消费走 api_dataops/api_xq_intel 实时读盘，已够用。**

### 第 4 步：仓内现存内容处置（因两路径同体，无需「迁移」，仅分类定性）

| 类别 | 内容 | 建议 | 风险 |
|---|---|---|---|
| 活跃任务产物（xq_backfill/xq_classify/xq_report/execution_watchdog） | 86 文件 | **原地保留**，无需 Move（已处判例落点） | xq_classify 在途写入，任何操作待收口 |
| `_archive_from_quant_backtest`（4 件） | 0e70a40f 迁移副本 | **保留 1 周后可删**：与活跃状态文件内容重叠，仅回滚保险用 | 删除前核对 backfill_state.json 是否仍被 wrapper 读取（现已指向 xq_backfill\ 下新件，应无依赖） |
| `tools/` 探针件（3 件） | 会话手投 | 原地保留或并入 `_archive`；若属一次性探针可随归档同删 | xq_ollama_probe.py 可能被复用，删除前问询当日会话 |
| `fix_task_logon.ps1`（散件） | 登录修复脚本 | **移出**至仓内 `ops/` 或文档附件目录（它不是任务产物） | 低；无运行时依赖 |
| `.workbuddy/`（5 件） | WorkBuddy 平台 memory | **原地保留**，勿纳入收敛范围 | 平台自管，动它可能破坏 automation 记忆 |

### 第 5 步：验证判据（收敛完成后逐条核对）

1. `grep -r "automation_runs" --include=*.py --include=*.ps1`（仓内可执行件）：除注明 junction 等价的注释外，所有写点均指向 `C:/aios|C:\aios` 口径，仓内相对写点 = 0。
2. `git check-ignore automation_runs` 返回命中；`git status` 不再出现 `?? automation_runs/`。
3. `ls C:\Temp\*.log` 中不再新增今日计划任务名前缀的新日志（存量 kg_*/tune_bt_* 等留待自然过期或手动清）。
4. 计划任务巡检：次日核对各 schtasks 产出——`C:\aios\automation_runs\<任务名>\` 下有当日新件，且 `jev-eval/n1_check.ps1` 心跳读取（:265）正常 exit=0。
5. 数据资产核对：`xueqiu_info.db` 及派生表路径未变（不迁原则的红线验证）。

---

## 四、风险与红线

1. **并行会话在途件（最高优先）**：`xq_classify_status.log` mtime 12:00（本文写作时仍在活跃追加），`xq_report/latest.json` 11:43，`execution_watchdog` 11:48——今日 07:48–11:30 时段 classify v1.2、M-RT3 日报、watchdog 三条线均有并行会话（冲突地图 §二）。**第 1 步 4 文件改造必须等 classify v1.2 会话与 watchdog 线收口后执行**，且 execution_watchdog.py 属冲突地图禁区清单（仓根 automation_runs/ 整体列为「散件待认领」）。协调权在老田。
2. **数据资产不迁的边界判定**：判例原文「数据资产（xueqiu_info.db/派生表）不动」。边界划法——**凡被查询/回测/情报线消费的持久数据（db 文件、派生表、游标状态文件中的业务数据）不迁**；仅「产物/日志/临时文件」（out/err/log/json 投影/心跳件）落 automation_runs。注意 `xq_backfill/backfill_state.json` 与 `seed_cursor.json` 属**状态文件**：0e70a40f 已将其按「产物」迁入 automation_runs 且实测 wrapper 正常——维持现状，不因本方案回改。
3. **junction 单点**：全口径押在 `C:\aios` junction 上，junction 被误删则绝对路径全断。缓解：不删除 junction（建议登记为运行时基建，参照 `docs/CURRENT_RUNTIME_MATRIX_2026-10-17.md` 已含 C:\aios 引用）；若选仓内相对口径则此风险消失但违背判例原文。
4. **门户读点同步**：`api_dataops.py`/`api_xq_intel.py`（C:\aios 口径）与 `api_brief.py`/`api_evolution.py`（仓内口径）并存期间功能不受影响（同一目录）；但第 1 步改造若只改写不改读，会导致 §五 grep 验收无法归零——读写必须同批改。
5. **C:\Temp 改造的面广**：30 个 ps1 分布 8 个目录，其中多个属冲突地图在途禁区（D 批 news_collector jev_* 线等）——常规批改造须逐个对表冲突地图，禁止搭车改在途件。

---

## 五、结尾声明

本方案为**纯文档产物，零操作**：调查阶段对 `C:\项目\老田的AI助手\automation_runs\` 与 `C:\aios\automation_runs\` 仅做只读统计与 grep 定位，未迁移、未删除、未重命名、未修改任何实际文件与目录，未改任何 .py/.ps1/.gitignore。文中全部改造点（4 文件路径常量 + 30 个 C:\Temp 散落日志 + .gitignore + 归档清理）**均待老田对总裁决清单 06 项拍板后由对应批次执行**；xq_classify 等并行会话在途件的收口顺序，协调权在老田。

*本文件为唯一写入物（含其单独 add + commit）。全程只读，禁区零接触。*
