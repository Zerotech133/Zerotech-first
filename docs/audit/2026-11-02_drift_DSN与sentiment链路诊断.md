# drift DSN 修复诊断 + sentiment_bt/jev_impact 链路排查（S1-04/S1-05）

> 任务：`task-15`（共享任务板）｜执行：wa-govern（D 席支援）｜采样：**2026-11-02 07:03–07:14（Asia/Shanghai）**
> 依据：《数据仓研发计划 v3 执行版》S1-04（drift PG DSN → `drift.exists=true`）、S1-05（sentiment_bt/jev_impact 生成任务排查，恢复或标 deprecated）
> 边界：**只读诊断**——未改生产配置/未重启/未 commit/未运行产物生成器；凭据全部掩码；所有命令与输出均为本机实测。
> 结论先行：**S1-04 的验收指标（drift.exists=true）当前已达成且写通道活跃**（07:05/07:14 两次实测；07:10 有当日新告警入库）；**"Errno 22"症状当前不可复现**，根因定位到 `jev_common.db_rows` 的 **docker 降级通道在 portal S4U/Session0 上下文失败**，并给出补丁方案与复验命令；**S1-05 不是脚本废弃、是"生成任务缺失"**（产物已陈旧 353.1h），给出"先标 deprecated（L1 当日可做）→ 再决定恢复（L2 需批准）"的决策建议。

---

## 一、S1-04：drift 哨兵 DSN 诊断

### 1.1 现状实测（结论：已恢复）

| 检查 | 命令（只读） | 结果（2026-11-02） |
|---|---|---|
| 直连 API 8091 | `curl http://127.0.0.1:8091/api/daily/data` | 200；`drift.exists=true`、`open_count=4`（07:05:40）→ **`open_count=5`（07:14:05）**、`error` 为空 |
| 同源入口 8734（nginx） | `curl http://127.0.0.1:8734/api/daily/data` | 200；`drift.exists=true`、`open_count=5`；页面 `/data_daily.html` 200（17,269B） |
| PG 直连（jev_common DSN） | `python -c "import psycopg2;psycopg2.connect(**jev_common.DB_DSN)"` | `DIRECT_OK (1, 'zhice_os_v2','zhice')` |
| `jev_common.db_rows`（drift 实际查询） | `db_rows("SELECT count(*) FROM v2_alert WHERE title LIKE %s",("JEV判定漂移%",))` | `DB_ROWS_OK [(4,)]`（07:05）→ 当前 5 |
| 写通道活性 | 最近告警 `created_at` | 最新 **`2026-11-01 23:10:03+00:00` = 2026-11-02 07:10:03 CST**（`\JevDriftDaily` 07:10 当日运行入库）→ **读/写两条链都活** |
| portal 进程 | `Get-CimInstance Win32_Process -Filter "ProcessId=14412"` | 8091 持有者 pid **14412，CreationDate=2026-11-02 03:25:22**；任务 Principal=`t` / **S4U** / Limited；action=`C:\aios\portal\start_portal_api.ps1` |

现存 OPEN 漂移告警 5 条，最早 10-17、最新 11-01（CST 11-02 07:10）：`scope`×4 + `event_type`×1，**无 ack/close 机制，已连续 16 天 OPEN**（见 §1.6 风险 R3）。

### 1.2 DSN 来源链（脱敏）

`jev-eval/jev_common.py` L284–314（唯一一份 `jev_common.py`，全仓无副本）：

```
_zhice_pg_password(): env ZHICE_PG_PASSWORD → 文件 C:\项目\zhice-os-v2-windows-20260920\code\.env.local 的 V2_DB_PASSWORD → 旧默认口令（源码兜底常量，已掩码 z******_***）（兜底）
DB_DSN = { host: env ZHICE_PG_HOST|127.0.0.1, port: env ZHICE_PG_PORT|5446,
           user: env ZHICE_PG_USER|zhice, password: 上述链,
           dbname: env ZHICE_PG_DB|zhice_os_v2 }
PG_CONTAINER = env ZHICE_PG_CONTAINER|zhice-db
降级通道：db_rows/db_run 直连失败 → docker exec zhice-db psql（sql 中 %s → :'pN' 变量绑定）
```

实测脱敏输出：`HOST=127.0.0.1 PORT=5446 USER=zhice DB=zhice_os_v2 PW_len=32 PW_source=file(V2_DB_PASSWORD)`（本会话无 `ZHICE_PG_*` 环境变量）。
容器侧：`docker ps` → `zhice-db | postgres:15 | 0.0.0.0:5446->5432/tcp | Up 14 hours`；端口 5442=`quant-pg`、5432=`infinity`、5437?（无）、5443 关闭。

> 注：`portal/api_flow.py` L43-50、L497-500 **复制了同一套 DSN/口令回落代码**，两处需同源维护（见 §1.5 F4）。

### 1.3 失败模式矩阵（实测：psycopg2 的报错一定带 libpq 前缀，不会"裸 Errno 22"）

| 变体 | 结果（原文截断） |
|---|---|
| 5446 / 127.0.0.1、localhost、::1、空 host、port 字符串 | **OK** |
| 5432（infinity） | `DatabaseError: Invalid command: syntax error...`（非 Errno22） |
| 5442（quant-pg） | `OperationalError: connection to server at "127.0.0.1", port 5442 failed: FATAL: password authentication failed` |
| 5443（关闭） | `OperationalError: ... Connection refused (0x0000274D/10061)` |
| port=0 / 70000 / -1 | `OperationalError: invalid port number: "..."` |
| host 非法（`127.0.0.1:5446`、空格） | `OperationalError: could not translate host name ...` |

**关键推断**：审计报告记录的报错是页面直接显示的 **`v2_alert 不可达: [Errno 22]`（裸 OSError，无 "connection to server…" 前缀）**。上表 8 类 psycopg2 失败全都带前缀/FATAL，因此该错误**不可能来自 `psycopg2.connect` 直连**，只能来自 `db_rows` 的**降级通道**——即 `_docker_psql_rows()` 里 `subprocess.run(docker exec psql, input=..., capture_output=True)`。Windows 下 WinError 87（ERROR_INVALID_PARAMETER）映射 `errno 22`；S4U/Session0 进程的 std 句柄/docker CLI 通道异常时可抛裸 `OSError: [Errno 22]`。

### 1.4 时间线与根因判定

| 时间 | 事件 | 证据 |
|---|---|---|
| 2026-10-30 20:52:30 | `jev_common` 凭据回落链修复（R5 口令轮换后旧默认口令（z******）认证失败 → 补文件回落链） | commit `daacd6204` `OS-R5-CONSUMER-FIX2` |
| 2026-11-01 23:32/23:57、11-02 00:21/00:31/03:25 | portal 连续重启（受控重启标志） | `C:\Temp\portal_api_task.log`：`restart-flag: killed [...] / cold-start guard OK / starting 2026-11-02T03:25:22` |
| 2026-11-02 00:19:54 | 全链路审计报告落盘，记录 "drift 卡片 `v2_alert 不可达: [Errno 22]`" | `deliverables/2026-11-02_数据仓全链路能力与数据接入审计_报告.md` L38/L46 |
| 2026-11-02 03:25:22 | 当前 portal 进程创建（加载 10-30 后代码） | CIM CreationDate |
| 2026-11-02 07:05–07:14 | 本次复测：API/页面/直连全部正常，07:10 有新告警入库 | §1.1 |

**判定（分层，标注置信度）：**
- **高置信**：错误发生在 **docker 降级通道**（依据=错误串无 libpq 前缀，§1.3）；该通道在 portal（S4U/Session0）内不具备可依赖性 → 直连一失败就会把"降级成功"变成"整体不可达"。
- **中置信**：触发条件是**直连失败**——R5 口令轮换后 `jev_common` 旧默认口令（z******）失配（`ZHICE_PG_PASSWORD` 未注入）或文件回落不可读；10-30 的 `daacd6204` 补上文件回落链后直连恢复，降级通道不再被走到 → 症状消失。
- **待验证**：审计时刻（00:19，进程为 23:57 版、已含 10-30 修复）为何仍失败——最可能是**同一次请求里"文件回落读不到"（S4U 会话 ACL/路径）导致旧默认再次生效**，或审计脚本自身环境与 portal 不同。**复验方法见 §1.5 F6**——必须在 portal 同 Principal（S4U/Limited）环境跑一次探针，才能把中置信升为定论。

### 1.5 最小修复方案（只给 diff 级方案，未落盘）

| # | 文件/位置 | 改动（最小） | 作用 | 级别 |
|---|---|---|---|---|
| F1 | `jev-eval/jev_common.py::_docker_psql_rows` L338-383 | ①`subprocess.run` 外层捕获 `OSError` 并抛 `RuntimeError(f"docker 降级不可用: {type(e).__name__}: {e} (cmd=docker exec {PG_CONTAINER} psql)")`；②SQL 传输由 `input=` 改 `-c`（单语句）或 `-f` 临时文件，去掉对 std 句柄的依赖；③入口加一次 `shutil.which("docker")`+`docker version` 探活缓存（失败即"降级通道不可用"显式标记） | 消除 S4U 下裸 Errno22，把"降级失败"变成可读错误 | L1（代码，需 worktree+评审门/fast 测试） |
| F2 | 同上 `db_rows` L386-417 | 直连失败日志补 DSN provenance（`host:port/db`、`pw_source`∈{env,file,default}）、异常类型与 traceback 首行（现仅 WARN 无 provenance） | 下次可在日志里直接判定是凭据还是通道问题 | L1 |
| F3 | `portal/api_daily_data.py::drift_section` L174-194 | `out["error"]` 增结构化字段：`{"error","dsn":"127.0.0.1:5446/zhice_os_v2","pw_source":"file","fallback":"docker","errno":22}`；并 `jc.log(..., level="error")` 落一条带 traceback 的 JSON 日志；`str(e)[:80]` 截断改为保留类型+errno | 页面自解释，避免"裸 Errno22"无法归因 | L1 代码；**portal 生效需重启 8091=L2** |
| F4 | `portal/api_flow.py` L43-50/L497-500 | 与 `jev_common` 重复的 DSN/口令回落链抽公共函数（或 import jev_common），两处同源 | 防双份漂移（R5 类问题复发） | L1 |
| F5 | `tools/gen_health_card.py` | 新增 `data.drift_sentinel` 项：调 8091 `/api/daily/data` 取 drift.exists/open_count/最新告警 age；直连探针失败判黄（不判红，fail-visible） | 哨兵"停写但页面不报错"可被健康卡发现 | L1（若需新增定时任务=L2） |
| F6 | 复验（不改码） | 在 portal 同环境跑：`schtasks` 不可新建，改用**受控一次性**：在 S4U 任务内调用 `python -X utf8 -c "import sys;sys.path.insert(0,r'C:\aios\jev-eval');import jev_common as jc;print(jc.DB_DSN['host'],jc.DB_DSN['port'],jc.DB_DSN['dbname'],len(jc.DB_DSN['password']));print(jc.db_rows('select 1'))"`，输出落 `C:\Temp\drift_probe.log` | 定论"文件回落是否可读 + 降级通道是否可用" | 需 L2（新增/借用计划任务） |

**推荐执行序**：F5（观测先上）→ F2/F3（可诊断）→ F1（根治降级通道）→ F4（去重）→ F6（S4U 复验）。S1-04 的 **DoD 已可由 §1.1 证据判达标**，本 patch 集是"防复发+可观测"后续小单，不必阻塞 Sprint1 其它项。

### 1.6 风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 直连凭据再次轮换而文件回落路径失效（R5 类） | drift 再次静默不可达，页面仅显示裸错误 | F2/F3/F5；口令轮换清单加入 `zhice .env.local V2_DB_PASSWORD` 与 `jev_common` 回落链同批校验 |
| R2 | docker 降级通道在 S4U/Session0 不可用 | 任何 PG 直连失败都被放大成整块不可达 | F1；或明确"降级通道仅开发态可用，生产 fail-visible" |
| R3 | 5 条 OPEN 漂移告警最早 10-17，无 ack/close | 告警疲劳；无法区分"新漂移"与"旧账" | 增 ack/auto-resolve（JS 连续回落→CLOSE；或按周归档），在 data_daily 显示"未处置天数" |
| R4 | `jev_common` 由 portal **进程内缓存**（`_jev`/`_conn`） | 修代码不重启=旧行为继续（AGENTS §7 "修复写盘≠生效"） | 变更后必须重启 8091（L2）并用进程创建时间 > 文件 mtime 验证 |

---

## 二、S1-05：sentiment_bt / jev_impact 生成任务排查

### 2.1 产物现状（实测：陈旧 353.1h）

| 产物 | 路径 | mtime | 大小 | age（data_daily health 07:05 实测） |
|---|---|---|---|---|
| sentiment_bt_result.json | `news_collector/data/` | **2026-10-18 13:59:00** | 840,731 B | **353.1h（14.7 天）** |
| jev_impact.json | `news_collector/data/` | **2026-10-18 13:59:01** | 397 B | **353.1h** |
| （同族）jev_decay.json | 同上 | 2026-10-18 04:02:10 | 980 B | 陈旧 |
| （同族）jev_resonance.json | 同上 | 2026-10-18 12:09:46 | 865 B | 陈旧 |
| sentiment_bt_pushed.json | 同上 | 2026-10-14 20:35:12 | 72 B | 推送水位停更 |

### 2.2 消费方（2 处，其中 1 处**无新鲜度门槛**）

1. `portal/api_daily_data.py` L139–171：`bt_section()` / `impact_section()` 直接读上述 JSON，返回 `exists/generated_at/...`；`health_section()` 会报 age（页面能看到 353.1h）。
2. **`news_collector/daily_report.py::_data_line()` L93–122**（K-ORG-186 M2 新增）：读同两文件拼「📊 数据实证」行，**只判 `bt.get("n")` 是否存在，无 generated_at/age 门槛** → 飞书日报会把 **10-18 的回测数字当当日实证展示**。`DailyReport` 任务 10-15 起每日 03:00/20:30 均 rc=0（今日 03:00:01 rc=0），即该误读**每天在发生**。
3. `portal/data_daily.html` 节二/三 + 页脚仍标注上游 `sentiment_backtest / jev_impact`。

### 2.3 生产侧：脚本活着，**调度没了**（判定：非废弃，属"生成任务缺失"）

| 项 | 证据 |
|---|---|
| 生成脚本 | `news_collector/sentiment_backtest.py`（26,533B，mtime **10-29**，CLI `--push`，原子写 JSON）；`news_collector/jev_impact.py`（3,335B，mtime 10-27，复用 `sentiment_backtest.run()` → 写 jev_impact.json）。两者 `ast.parse` 通过 |
| 启动器 | `news_collector/start_sentiment_bt.ps1`（调 Python311 + `--push`，日志落 `C:\Temp\sentiment_bt_task.log`） |
| 最后一次运行 | `C:\Temp\sentiment_bt_task.log` 尾：**`sentiment_bt exit=0 2026-10-14T20:35:13`**；`C:\Temp\sentiment_bt_out.log` mtime 10-14 20:35（内含 `saved -> ...sentiment_bt_result.json`、`PUSH_OK level=1300 n=1334`） |
| 计划任务 | **全任务 Actions 全量扫描：没有任何任务引用 `sentiment_backtest.py` / `jev_impact.py` / `start_sentiment_bt.ps1`**（仅 `DailyReport`/`XueqiuDailyReport` 引用各自 `start_daily_report.ps1`，非本链） |
| 10-18 的产物从哪来 | 10-18 13:59 双产物同时写入、且 `n=2308` ≠ 10-14 日志的 `n=1334` → **是 10-18 一次人工/临时刷新**，不是计划任务 |
| git 维护状态 | 近 8 次提交含 `d51ffef8c`（K-ORG-186 M2 数据日报链）、格式化/ruff 清理 —— 脚本持续被维护，**不存在"代码废弃"标记** |

### 2.4 替代链评估（`tools/jev_audit.py` 已覆盖"校准"角色）

- 调度：`\JevDriftDaily` 07:10 的 P4 调用 `tools/jev_audit.py --days 45`（不阻塞退出码）；今日 07:10 正常运行。
- 产物：`agent_platform/data/jev_audit/{jev_audit.db 30.3MB、latest.json、baseline_<date>.json、posterior_<date>.jsonl}`，最新 **2026-11-02 07:10:17**。
- 覆盖：判定账本 `judged_total=111,489`、T+1 后验 `priced_total=21,249`、方向准确率/conf 分桶/事件类型/朴素基线（`coverage`, `stale_trade_days`, `assessment`）→ **校准类指标已被日更替代**。
- **不覆盖**：sentiment_bt 的「情绪分 × 次日收益分组（conf/scope）相关性」与 jev_impact 的「impact 分层 CAR」——这两项仍有独立信息价值，不是纯重复。

### 2.5 决策建议（恢复 vs deprecated，含推荐）

| 方案 | 内容 | 优点 | 成本/风险 | 级别 |
|---|---|---|---|---|
| **A 恢复** | 新增计划任务 `SentimentBT`：每日 **07:20**（JevDriftDaily 07:10 之后、早报 08:20 前）依次跑 `sentiment_backtest.py` → `jev_impact.py`；产物原子写；验收 age<48h 连续 3 天 | 两处消费方立即恢复当日数据；payload 与页面契约不变 | 新增任务=L2；首跑需确认运行时长；`--push` 水位停在 10-14，首跑**建议不带 `--push`** 防补推旧里程碑卡 | **L2**（任务）+ L1（首跑验证） |
| **B 标 deprecated** | API bt/impact 加 `deprecated:true, stale_since:"2026-10-18"`；`data_daily.html` 标"已停更（历史）"；`daily_report._data_line` 加 48h 新鲜度门槛（超期整块跳过并注明） | 当日可做、零风险、止血"旧数当新数"误读 | 放弃两项实证指标 | **L1**（代码） |
| **C 混合（推荐）** | **先 B 止血（L1）→ 再按 A 评估恢复（L2）** | 不阻塞 Sprint1 其它项；审批周期内先把误读关掉 | 需要两小单 | L1→L2 |

**如果只能做一件事**：做 B 的 `daily_report._data_line` 新鲜度门槛——因为飞书日报是外发口径，**把 14 天前的回测数字当当日实证发出去**是该链路目前最实际的风险；data_daily 页面本身在 health 区已暴露 age，误读面较小。

### 2.6 风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| S1 | 恢复后 `sentiment_backtest.run()` 读 news.db + quant 行情链路（trade_cal/ts_daily），运行时长与内存未实测 | 07:20 运行可能拖到早报窗口 | 首跑人工计时（只读探测可先跑 `--help`/import 冒烟）；必要时错峰到 07:15 |
| S2 | `--push` 补推里程碑（水位 10-14，n 已从 1334→2308+） | 飞书误发历史卡 | 首跑不带 `--push`，确认后手动更新水位 |
| S3 | 若选 B 但页面文案漏改 | 前端仍展示旧数字 | 页面/页脚/日报三处同批排查（grep `sentiment_bt_result`/`jev_impact`） |
| S4 | 恢复任务新增属 L2（计划任务增删） | 审批周期不确定 | 先 B，A 走五要素待批包 |

---

## 三、验收对照（S1-04 / S1-05 DoD）

| 任务 | DoD（v3 §四） | 当前判定 | 证据 | 建议 |
|---|---|---|---|---|
| S1-04 | SQL 验证 + API `drift.exists=true` | ✅ **已达成**（非阻塞） | §1.1 两次实测；07:10 新告警入库 | Lead 记 K2 达标；连续 3 天观察后关单；F1/F3/F5 另开"防复发"小单 |
| S1-05 | `age<48h` 或显式标记 deprecated | ❌ **未达成**（353.1h 且无标记） | §2.1/§2.2 | 按 §2.5 方案 C：先 L1 标 deprecated+日报门槛，恢复走 L2 |

---

## 四、复验命令（只读，任何人可重跑）

```powershell
# 1) drift 读通道（8091 与 8734 各一次）
(Invoke-WebRequest -Uri 'http://127.0.0.1:8091/api/daily/data' -UseBasicParsing).Content | ConvertFrom-Json |
  Select-Object -Expand drift | ConvertTo-Json -Depth 3
# 期望：exists=true, error 空, open_count≥1

# 2) PG 直连（DSN 只打印脱敏要素）
& 'C:\项目\py312\python.exe' -X utf8 -c "import sys;sys.path.insert(0,r'C:\项目\老田的AI助手\jev-eval');import jev_common as jc;print(jc.DB_DSN['host'],jc.DB_DSN['port'],jc.DB_DSN['dbname'],'pw_len='+str(len(jc.DB_DSN['password'])));print(jc.db_rows('select count(*) from v2_alert'))"
# 期望：127.0.0.1 5446 zhice_os_v2 pw_len=32 → [(N,)]

# 3) 容器内只读核数（对比 API open_count）
docker exec zhice-db psql -U zhice -d zhice_os_v2 -A -t -c "select count(*) from v2_alert where title like 'JEV判定漂移%' and status='OPEN'"

# 4) sentiment 产物新鲜度
Get-ChildItem 'C:\项目\老田的AI助手\news_collector\data\sentiment_bt_result.json','C:\项目\老田的AI助手\news_collector\data\jev_impact.json' |
  Select-Object Name,LastWriteTime,Length

# 5) 页面可达
foreach($u in 'http://127.0.0.1:8734/data_daily.html','http://127.0.0.1:8734/api/daily/data'){ (Invoke-WebRequest $u -UseBasicParsing).StatusCode }
```

---

## 五、本次边界与未做的事（如实报告）

- **未**修改任何生产配置/代码（含 `.env.local`、`jev_common.py`、`api_daily_data.py`）、**未**重启 8091/nginx、**未** commit（工作区保持干净：`git status --porcelain` 对本链三文件为空）。
- **未**运行 `sentiment_backtest.py` / `jev_impact.py`（会写生产产物，违反只读边界）；运行时长只能由历史日志推断（10-14 单次运行时间窗 ≤1min 级，非精确）。
- **未能**在 portal 的 S4U/Session0 会话内直接复现 Errno22（需要以同 Principal 起进程 = 新增/借用计划任务 L2）；§1.4 的中置信结论因此标注为"待验证"，复验方案为 §1.5 F6。
- 本文所有凭据均为"长度/来源"级脱敏，无明文入档。
