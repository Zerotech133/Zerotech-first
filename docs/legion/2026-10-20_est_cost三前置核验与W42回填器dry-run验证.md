# est_cost 三前置核验 与 W42 回填器 dry-run 零写入验证（2026-10-20）

- **执行工位**：ws-pre（只读核验 + dry-run，零改动）
- **核验时点**：2026-10-20 12:32 ~ 12:50
- **纪律**：全程只读；未改计划任务、未改配置、未注册、未建计划任务、未 commit/push、未发飞书、未杀进程重启服务；**全程未带 `--write` 调用任何一次**；pytest 以 `-p no:cacheprovider` + `PYTHONDONTWRITEBYTECODE=1` 运行，不落缓存。
- **授权依据**：`docs/legion/2026-10-19_QTM0拍板执行单_十一项.md` #9（est_cost 解冻三前置）与 #10（W42 挂载点方案B）；`docs/legion/2026-10-19_心跳降级补跑与est_cost解冻排期预研单.md` §2.1（三前置可机检验证命令与通过判据）。

> **本文档只记实测证据指向，不记推断。缺证据一律记「待核」，不以「大概/应该/按例」代替证据。**

---

## 任务一：est_cost 解冻三前置逐条核验

### 1.1 逐条结论表

| # | 前置条件 | 拍板要求的通过判据（预研单 §2.1 原文口径） | 本次实测结论 | 关键证据 |
| --- | --- | --- | --- | --- |
| ① | **141 用例基线绿** | `python -m pytest tests/ -q` 全量绿，用例数与 141 基线口径一致 | **PASS（绿）/ 计数口径已漂移，须 lead 裁定** | `226 passed in 9.85s`，EXIT=0（本次实跑）；历史 141 原始产物 `automation_runs/health_drill/pytest_tests.log:3` = `141 passed in 7.60s` |
| ② | **首跑验收过（f5ecb295）** | brief.json 存在 + `source=tianfeng-heartbeat` + id 为 `INTEL-<date>-<两位seq>` + `validate()` 无违例 + 验收结论回填台账 D1 行 | **FAIL** | `memories/intel/` 下**仅 2026-10-18/**；10-19/10-20 目录均不存在；两日 run 记录 `status=degraded`（`TimeoutError: timed out`）；台账 D1 行 H1=✗ |
| ③ | **门 3 周一观察无异常** | observe **退出码 0**（无 FAIL 且有 PASS）**且**人工四计数 done/degraded/parse_fail/rec_none 全零 | **FAIL（退出码 2，非 0）**；四计数实体证据另附 | 本次实跑 `check_observation_point.py --data-root data` → **EXIT=2**，PASS 3 / PENDING 1 / **FAIL 1**（判据 e 三方对账） |

### 1.2 前置①：141 基线绿 —— 实测复核

**结论：测试全绿 = PASS。但「141」这一具体计数已被后续实装累加漂移，判据原文「用例数与 141 基线口径一致」严格意义上不成立，须 lead 裁定口径。**

实跑命令与输出（本次，2026-10-20 12:36）：

```
cd C:/项目/digital-legion
PYTHONDONTWRITEBYTECODE=1 "C:/Program Files/Python311/python.exe" -m pytest tests/ -q -p no:cacheprovider
→ 226 passed in 9.85s
→ PYTEST_EXIT=0
```

**141 原始产物的实体证据（非文档自述）**：

- 路径：`C:\项目\老田的AI助手\automation_runs\health_drill\pytest_tests.log`
- 第 3 行：`141 passed in 7.60s`（同文件 L4-L23 为 pytest atexit safe-delete 拦截的 SystemExit: 1，发生在全绿之后，不影响判例——与回执 `2026-10-18_全链健康演练回执.md:18` 记载同构）
- 该日志 mtime = 2026-10-18 21:52，与回执检查项 1 的执行时点吻合。

**计数漂移链（均有产物/提交佐证）**：

| 时点 | 计数 | 增量来源 |
| --- | --- | --- |
| 2026-10-18 | 141 | 基线（ce1b8042 演练回执检查项 1） |
| 2026-10-19 | 153 | M3-A0+A+sim-exec 新增 12（`d84ad00`/`3000667`/`3f44a87`），台账:149 记载 |
| 2026-10-19 | 176 | W21 演练新增 19（`97df6b3`） |
| 2026-10-20 | **226** | M4 W22 供给面新增 50（`3785940`，仓 HEAD） |

**须 lead 裁定的一点**：预研单 §2.1 判据原文为「用例数与 141 基线口径一致」，而 §三.3 已自记「141 基线的精确构成未在台账展开……若数不符先在台账登记再解冻」。本次实测 226 ≠ 141。**「绿」这一实质要件成立；「141 计数一致」这一字面要件不成立。** 建议按「基线绿 + 计数只增不减（无删用例）」口径认定 PASS——本次 226 为全绿 0 failed/0 error/0 skipped，无用例被删故基线未被侵蚀。此为口径决策，非本工位可自行裁定。

**附：.pytest_cache 中存在 3 条历史 lastfailed 记录**（`.pytest_cache/v/cache/lastfailed`，mtime 2026-10-20 11:03）：`test_capability_surface.py` 的 3 条用例。本次实跑 226 全绿、该三条通过 → 判定为该缓存写入时刻的**中间态或阳性对照自检残留**，非当前真实失败。此项仅作提示，不影响 ① 结论。

### 1.3 前置②：首跑验收（f5ecb295）—— 实测复核

**结论：FAIL。首跑未产出简报，验收判据 H1 不成立。**

**先纠一处引用错误（重要）**：`f5ecb295` **不是 git commit**，是 automation 任务 ID。原任务书要求「在 digital-legion 仓 `git log -1 f5ecb295` 核实该提交存在」——该核实路径不成立，实测如下：

```
cd /c/项目/digital-legion && git log -1 f5ecb295
→ fatal: ambiguous argument 'f5ecb295': unknown revision or path not in the working tree.

# 全仓扫描（10 个 git 仓库，含 digital-legion / sandbox / 老田的AI助手 / zerobound-kg 等）
for d in $(find . -maxdepth 4 -type d -name ".git"); do git -C "$d" cat-file -t f5ecb295; done
→ 10/10 全部 "fatal: Not a valid object name f5ecb295"
```

**其真实身份（automation list/view 实测）**：

- id = `f5ecb295-b518-4465-892d-31b333d15a3c`
- name = 「心跳首跑验收（M1 心跳 08:30 首跑后）」
- scheduleType=`once`，scheduledAt=`2026-10-19T08:40`，status=`ACTIVE`
- prompt 三步：①PowerShell 查 `AgentLegionHeartbeat` 的 LastRunTime/LastTaskResult ②跑 `tools/verify_heartbeat.py --since-days 2`（预期 2/2）③读 `plans/heartbeat/run-*.json` 与 `memories/intel/2026-10-19/brief.json`

**验收判据实测结果（逐条）**：

| 判据 | 拍板要求的通过条件 | 实测 | 判定 |
| --- | --- | --- | --- |
| H1 产物 | `intel/<date>/brief.json` 存在，含 1..N 条 | `C:\Users\t\.hermes\profiles\tianfeng\memories\intel\` 下**仅 `2026-10-18/`** 一个子目录；`2026-10-19/`、`2026-10-20/` **目录不存在** | ✗ |
| H2 契约校验 | `IntelItem.validate()` 无违例 | D1/D2 无 brief 可校验（**待核**，非绿非 FAIL） | 待核 |
| H3 运行记录 | `run-*.json` 每轮一条，status ∈ {ok, degraded} | `run-2026-10-19-083003.json`、`run-2026-10-20-083002.json` 均在，六字段齐全，`status="degraded"` | ☑ |
| H4 降级记录 | degraded.json 机器可读 + 退出码 2 | `degraded.json` 实存（`degraded=true`、`reason=inference_failed`、`probe_records` 1 次 ok 63ms、`at=2026-10-20T08:32:02`）；两日 run 均带 `{"type":"degraded"}` 事件 | ☑ |
| H5 shadow_rows | status_counts 附 shadow_rows | 两日 `status_counts` 仅 4 键（probe_ok/probe_attempts/items_valid/items_skipped），**无 shadow_rows**——推理在影子收编前即超时失败 | 待核 |

**首跑（D1 2026-10-19）实际形态**（`plans/heartbeat/run-2026-10-19-083003.json` 原文）：

```json
{ "started_at": "2026-10-19T08:30:03", "finished_at": "2026-10-19T08:32:06",
  "status": "degraded",
  "status_counts": {"probe_ok":1,"probe_attempts":1,"items_valid":0,"items_skipped":0},
  "error": "推理失败：TimeoutError: timed out",
  "events": [{"type":"degraded","reason":"inference_failed","at":"2026-10-19T08:32:06"}] }
```

- 探活 1 次即 ok（63ms），**推理侧 120s 超时**——非探活侧故障。
- `automation_runs/heartbeat_wiring/run1.log` 记 2026-10-18 19:30 那次接线验证是成功的（`OK: 3 条情报入简报 → .../intel/2026-10-18/brief.json`），该 brief 即 H1/H2 判据可核的唯一一份（3 条、`INTEL-2026-10-18-01..03`、`source=tianfeng-heartbeat`）——但它**产自 10-18 手工接线跑，非 10-19 计划任务首跑**。
- automation prompt 内写的预期「verify_heartbeat 2/2」**未达成（实际 1/2）**，与台账:169 记载一致。
- **D2（2026-10-20）同形态复发**：`run-2026-10-20-083002.json` 逐字同构（`degraded` / `TimeoutError: timed out`），`degraded.json` mtime 2026-10-20 08:32。

**判读**：按台账 §三.1「心跳 degraded ≠ 失败」，两日均为**被设计捕获并如实落盘的降级**，H3/H4 记「降级被正确记录」，**不判 FAIL**。但前置②的判据是「**首跑验收过**」，其内核是「简报产出且契约合规」（预研单 §2.1 判据列明 `source` / `id` 格式 / `validate()` 无违例）——**简报零产出，验收不可能通过**。故前置② = **FAIL**（非「未到点」：时点已到且已跑，是跑了但没出简报）。

### 1.4 前置③：门 3 周一观察 —— 实测复核

**结论：FAIL。observe 退出码 = 2（非 0），且人工四计数在 10-19 为 done=0。未按纪律把「退出码 0」当成功——本次实测退出码恰恰不是 0。**

#### 1.4.1 时点判定（先答「是否未到点」）

- **门 3 观察点是每周一任务，不在今日（周二）时点上**。`Get-ScheduledTaskInfo -TaskName AgentMondayCheckpoint`（只读）实测：
  - `LastRunTime = 2026/10/19 17:25:00`
  - `LastTaskResult = 3221225786`（= `0xC000013A`，STATUS_CONTROL_C_EXIT，异常终止）
  - `NextRunTime = **2026/10/26 17:25:00**`
- **本周一门 3（10-19）已到点并已跑**，故对前置③**不记「未到点」，记实测结果**。下一个门 3 时点为 10-26（下周一），**未到点**。
- 佐证：`monday_checkpoint.log` 内 `2026-10-20` 出现次数 = **0**（今日无新 observe 段），最后一段起于行 332 `M5 刀4 周一观察点护航巡检 2026-10-19 17:25:00`，尾部止于 `[wait] …第 36/120 次重试前等 90s`，无 EXIT 行。

#### 1.4.2 observe 退出码实测（本次亲自跑，只读）

按操作单 7aef4361 §17:25 的 SOP 口径（cwd = `agent_platform`，`--data-root data`）：

```
cd C:/项目/老田的AI助手/agent_platform
PYTHONDONTWRITEBYTECODE=1 "C:/Program Files/Python311/python.exe" tools/check_observation_point.py --data-root data
```

实测输出（退出码矩阵：0=无FAIL且有PASS / 1=全PENDING中性 / 2=存在FAIL）：

```
  [PASS]    a_last_run_marker            last_run.log 含 10 行 [strategy] 成功标记
  [PASS]    b_strategy_runs_anchored     strategy_runs.jsonl 20 行 / 带 run_id 20 行（≥1 达标）
  [PENDING] c_checkpoint_observe_sections monday_checkpoint.log 无两 observe 段（17:25 新版 checkpoint 未跑，首跑前属预期形态）
  [PASS]    d_shadow_m5_rows             shadow_ledger.jsonl 含 5 行 m5.v1
  [FAIL]    e_verdict_key_reconcile      三方对账 FAIL：4 行 verdict_key
            （d0d8c3b4ba8b0058 / 96b35dfa3cfe1943 / e885517b03e991b5 / 11e9b38eae310892）
            未被证据仓 strategy.runs 实体覆盖（无对应证据行）
汇总: PASS 3 / PENDING 1 / FAIL 1
结论: 存在 FAIL——先查清再判门（退出码 2）
→ SOP_OBSERVE_EXIT=2
```

**判据 e FAIL 的成因（非代码缺陷）**：按操作单 §16:40 第 2 项，门 3 判读**必须先跑 `evidence_collect collect --root . --domains strategy` 收编证据**，否则判据 e 必然真实 FAIL（原文：「不先收证据判据 e 会真实 FAIL」）。本次为严守只读红线**未执行 evidence_collect**（该命令会写证据仓 `evidence.db`），故判据 e 的 FAIL 属**证据未收编导致**，须如实标注而非直接归因为链路故障。

> 附：`agent_platform/data/evidence/evidence.db` mtime 仍为 2026-10-17 19:30（本次未写）；同目录 `evidence.db-shm` / `evidence.db-wal` mtime 为 2026-10-20 12:44，属其它常驻进程对 sqlite 的正常共享内存/预写日志活动，**非本次巡检产生**（巡检脚本自述「零写面：不创建目录、不追加账本、不写日志」，见 `check_observation_point.py:37`；实测脚本内无任何 `write_text` / `mkdir` / `os.remove` 调用）。

#### 1.4.3 人工四计数实体证据（SOP 原命令口径复算）

按操作单 §17:25 第 3 步原命令，对 `C:\aios\agent_platform\data\shadow\shadow_ledger.jsonl` 复算：

| 日期 | done | degraded | parse_fail | rec_none | syms | 判读 |
| --- | --- | --- | --- | --- | --- | --- |
| **2026-10-19（本周一门 3）** | **0** | 0 | 0 | 0 | `[]` | **FAIL**——done=0<5，门 3 本身应已亮红 |
| 2026-10-20（今日影子链） | 5 | 0 | 0 | 0 | `['000858','300750','600036','600519','601318']` | 影子链今日已恢复落账 |

**四计数的历史留档**（`automation_runs/escort_monday/fourcount_20261019.log`，51 字节全文）：

```
done=0 degraded=0 parse_fail=0 rec_none=0 syms=[]
```

**关键区分（按任务书纪律要求，不把任一项当成功）**：
- done=0 **不是**「四计数全零=通过」。按 SOP §17:25 判读表：`done<5` → **门 3 本身应已红**；`degraded/parse_fail/rec_none` 三项全 0 只说明**不存在 P1 假绿洗白**（是「未落账」形态，不是「假绿」形态）。护航复盘 `escort_review_20261019.md:32-33` 原文即此判读：「done=0<5 → 按操作单『done<5 → 门 3 本身应已红』→ **FAIL**」「与假绿形态区分：degraded/parse_fail/rec_none 三项全 0，不存在降级洗白；是『未落账』而非『假绿』」。
- **根因实体证据**：`agent_platform/data/shadow/last_run.log` 10-19 17:10:02 的 469 字节纯 traceback（`run_shadow.py:367` 打开 `last_run.log` 遇 `PermissionError: [Errno 13]`，进程即刻退出，LastResult=1），当日 5 标的判决从未落账；`strategy_runs.jsonl` 至今不存在。
- **17:25 检查点异常收口**：`AgentMondayCheckpoint` LastTaskResult=`0xC000013A`，日志止于 `[wait] 36/120`（mtime 18:17:30），**早于预告的 20:25 耗尽点**，终止原因未诊断（台账 E9 待查项）。

#### 1.4.4 门 3 相关产物清单（实体路径）

| 产物 | 路径 | 状态 |
| --- | --- | --- |
| 四计数日志 | `automation_runs/escort_monday/fourcount_20261019.log` | 在，done=0 |
| 巡检日志 | `automation_runs/escort_monday/inspection_20261019.log` | 在，PASS 0 / PENDING 5 / FAIL 0，退出码 1（全 PENDING 中性） |
| 护航复盘 | `automation_runs/escort_monday/escort_review_20261019.md` | 在，总判 **FAIL**（:8） |
| 护航收口 | `automation_runs/escort_monday/escort_wrapup_20261019_1830.md` | 在，总判 **FAIL**（:8） |
| 影子账本 | `agent_platform/data/shadow/shadow_ledger.jsonl` | 在，10-20 计 5 行 |
| 检查点日志 | `agent_platform/data/shadow/monday_checkpoint.log` | 在，369 行，2026-10-20 段 **0** |
| 证据收编 | `automation_runs/escort_monday/evidence_precheckpoint_20261019.log` | 在，`collected:0 / verdict:PASS`（无 17:10 落账可收） |

### 1.5 任务一总结论

| 前置 | 结论 |
| --- | --- |
| ① 141 基线绿 | **PASS**（226 passed / EXIT=0）；「141 计数一致」字面要件不成立，计数口径须 lead 裁定 |
| ② 首跑验收过（f5ecb295） | **FAIL**——D1/D2 连续两日推理超时降级，简报零产出（H1 ✗），验收不可能通过 |
| ③ 门 3 周一观察无异常 | **FAIL**——本周一（10-19）已到点已跑，observe 退出码 **2**（判据 e FAIL）+ 四计数 done=0；下一时点 10-26 未到点 |

**是否已具备解冻条件：否。三前置中 2 项 FAIL（②③），不满足「三前置齐」要件。est_cost 接线应维持冻结。**

按预研单 §2.4 排期建议口径「三前置全绿次日实施，不设默认解冻日，前置不齐即顺延」——本轮顺延，且**不设默认日期**（拍板执行单 #9 明文）。

**建议 lead 后续处置方向（本工位不自行执行）**：
1. ② 的根因是推理侧 120s 超时（探活 63ms 正常），非探活侧。台账 §三.6 已触发「跨日复发」→ 报 lead 走系统处置，须老田拍板；D3（10-21）若再次降级即走该路径。**不手动补跑**（红线①）。
2. ③ 的 FAIL 需拆两层看：**链路层**（10-19 PermissionError 未落账 + checkpoint 0xC000013A 异常终止，根因待排查 E9）与**判读层**（本次判据 e FAIL 系未跑 evidence_collect 所致，须在下次门 3 按 SOP「先收编、后巡检」顺序复跑才能得真绿）。10-26 才是下一个真实门 3 时点。
3. ① 的计数口径建议由 lead 一句裁定（建议按「绿 + 计数只增不减」认定），避免每次核验重复争议。

---

## 任务二：W42 回填器 dry-run 零写入验证

**脚本**：`C:\项目\老田的AI助手\agent_platform\tools\legion_meeting_backfill.py`（505 行）
**红线遵守**：本次共执行 3 次调用，**全部不带 `--write`**（`--help` 1 次 + dry-run 2 次），零违规。

### 2.1 `--help` 实测

```
PYTHONDONTWRITEBYTECODE=1 "C:/Program Files/Python311/python.exe" agent_platform/tools/legion_meeting_backfill.py --help
→ HELP_EXIT=0
```

输出（参数解析正常，四参数齐备）：

```
usage: legion_meeting_backfill.py [-h] [--date DATE] [--root ROOT] [--write] [--self-test]

W42 晨会纪要第④段回填器（方案B；草稿=账本投影，零自动定稿）

options:
  -h, --help   show this help message and exit
  --date DATE  回填日期 YYYY-MM-DD（缺省当天）
  --root ROOT  仓库根目录（缺省按脚本位置推断）
  --write      真写入（缺省 dry-run 零写入）
  --self-test  阳性对照自检（离线夹具，零真实数据）
```

### 2.2 dry-run 实跑（缺省即 dry-run）

**第 1 次：当日（2026-10-20，缺省日期）**

```
cd C:/项目/老田的AI助手
PYTHONDONTWRITEBYTECODE=1 ".../python.exe" agent_platform/tools/legion_meeting_backfill.py
→ DRYRUN_EXIT=0
```

输出：

```json
{ "date": "2026-10-20", "disposition": "失源",
  "detail": "复盘卡链目录缺失：C:\\项目\\老田的AI助手\\agent_platform\\data\\daily_review\\2026-10-20",
  "files": {} }
```

**判读**：脚本**正常启动、参数解析正确、优雅降级不崩**（退出码 0，非 traceback）。当日 `daily_review/2026-10-20/` 目录不存在——`LegionDailyReview` NextRun=17:40，本次核验时点 12:36 未到点，属**预期形态**（台账 E4 同款）。`daily_review/` 下现仅 `2026-10-17/`、`2026-10-19/` 两个目录。

**第 2 次：对已有数据的 2026-10-19（验证完整回填路径）**

```
PYTHONDONTWRITEBYTECODE=1 ".../python.exe" agent_platform/tools/legion_meeting_backfill.py --date 2026-10-19
→ DRYRUN_1019_EXIT=0
```

输出汇总（机器可解析 JSON，`indent=2`）：

| 项 | 值 |
| --- | --- |
| `date` | `2026-10-19` |
| `disposition` | `OK` |
| `files` 条数 | **13**（与当日 13 场晨会一一对应） |
| `disposition` 分布 | `待填→草稿已生成（dry-run）` × 13 |
| `state` 分布 | `OK` × 13 |
| `written` | **13/13 全为 `false`** |

逐场部门解析正确性核对（`dept` 字段与文件名一一对应，无错位）：

```
2026-10-19_academy_晨会.md      dept=academy      state=OK  written=False
2026-10-19_chief_line_晨会.md   dept=chief_line   state=OK  written=False
2026-10-19_data_晨会.md         dept=data         state=OK  written=False
2026-10-19_dev_晨会.md          dept=dev          state=OK  written=False
2026-10-19_intel_晨会.md        dept=intel        state=OK  written=False
2026-10-19_invest_晨会.md       dept=invest       state=OK  written=False
2026-10-19_legal_晨会.md        dept=legal        state=OK  written=False
2026-10-19_marketing_晨会.md    dept=marketing    state=OK  written=False
2026-10-19_ops_runtime_晨会.md  dept=ops_runtime  state=OK  written=False
2026-10-19_product_晨会.md      dept=product      state=OK  written=False
2026-10-19_quality_晨会.md      dept=quality      state=OK  written=False
2026-10-19_risk_晨会.md         dept=risk         state=OK  written=False
2026-10-19_trading_晨会.md      dept=trading      state=OK  written=False
```

**参数解析核对**：`--date` 生效（两次跑分别命中当日与指定日）；`--write` 缺省即 dry-run 生效（`main()` :498 `write=args.write`，`args.write` 缺省 False → `run(write=False)`）；`--root` 缺省按脚本位置推断（`ROOT = Path(__file__).resolve().parents[2]` :35）。

### 2.3 零写入验证（运行前后文件清单 / mtime / md5 三重对比）

**快照范围**：`agent_platform/data/meetings/`（36 文件）+ `agent_platform/data/daily_review/`（2 目录）+ `agent_platform/tools/legion_meeting_backfill.py` + `.worktrees/tasks.json`。

**证据 A —— 文件清单与 mtime（`ls -la --time-style=full-iso`，49 行）**：

```
diff /tmp/w42snap/before.txt /tmp/w42snap/after.txt
→ NO_DIFF（文件清单与 mtime 零变化）

diff /tmp/w42snap/before.txt /tmp/w42snap/after2.txt   # 含第 3 次 cwd 变体跑动后
→ STILL_NO_DIFF（三次跑动后仍零变化）
```

**证据 B —— 内容哈希（`md5sum`，38 行 = 36 晨会 md + tasks.json + 回填器脚本）**：

```
diff /tmp/w42snap/before_md5.txt /tmp/w42snap/after_md5.txt
→ NO_DIFF（内容哈希零变化）
```

**证据 C —— git 工作区**：`git status --porcelain agent_platform/ .worktrees/` 输出中，`data/meetings/`、`data/daily_review/`、`.worktrees/tasks.json` **均无条目**（列出的 `test_shadow_log_open_retry.py` 改动与 `intel_inbox/`、`backup_20261018_team_quality.json` 未跟踪项均为本次核验前既存，与本脚本无关）。

**证据 D —— 抽样 mtime 定点核对（`meetings/` 首末文件）**：

| 文件 | mtime（before = after = after2） |
| --- | --- |
| `2026-10-17_academy_晨会.md` | `2026-10-17 03:01:19.357187800 +0800` |
| `2026-10-19_academy_晨会.md` | `2026-10-19 09:00:00.636479400 +0800` |
| `.worktrees/tasks.json` | `2026-10-20 09:46:23`（跑动前后一致） |

**零写入结论：确认成立。** dry-run 前后文件清单、mtime、内容哈希三者全部零变化，无新增、无改动、无删除。

### 2.4 `--write` 写入点分析（代码级静态核验，未执行）

**唯一落盘点**：`legion_meeting_backfill.py:339`

```python
if write:
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    written = True
```

全文件 `write_text` / `mkdir` / `open(...,"w")` / `os.remove` / `shutil` / `json.dump` 扫描结果：**除 :339 外，其余全部落在 `_self_test()` 的离线 tempfile 夹具内**（:412-441、:470-480，写入 `tempfile.TemporaryDirectory()`，零真实数据触碰）。**生产路径无第二个写入点。**

**写入路径**：`path` 来自 `run()` :378 的 `meet_dir.glob("%s_*_晨会.md" % date)`，其中
`meet_dir = root / "agent_platform" / "data" / "meetings"`（:355）。
→ 实际落盘 = `C:\项目\老田的AI助手\agent_platform\data\meetings\<date>_<dept>_晨会.md`，**原地覆盖，无新建文件**。

**写入内容形态 = 草稿，非定稿**，三重保险（代码级）：

1. **草稿头行**（:295）：`- decisions:（草稿 OK|INCONCLUSIVE|DEGRADED），待部务会确认`——段级三态显式声明，绝无「已决议」表述。
2. **lessons 行尾固定候选标记**（:298、:190-196）：`- lessons:（草稿候选，待部务会拍板后入知识环）` + 每条 `（候选，部务会拍板后入知识环）`——零自动定稿，grep 100% 命中，入知识环唯一通道是部务会拍板，脚本零触碰 knowledge 环。
3. **出处锚覆盖率 100%**（:182-196 `_entry`/`_lesson`）：每条目必带 `（源：<账本路径>｜<工单ID/日期锚>）`；禁创作语汇命中则整条拒载为 `[拒载] …（源：…）`，拒载行**不复述命中词**，保证全文 grep 零命中。

**是否覆盖当日占位 —— 是，但受三重守卫约束**：

- 替换范围（:315-336）：定位第④段「决议与教训」标题（:51 `SECTION_TITLE_RE`，按标题不按段号，防 academy 第五段漂移）之后，**仅替换 `- decisions:（待填）` 与 `- lessons:（待填…）` 两行占位**，命中区间 `lines[start..end]` 整体换成草稿块。
- 守卫①**幂等**（:311-312）：检测到 `DRAFT_LINE_RE` 草稿头行 → 判「已回填」跳过，**二次跑不重复叠加**。
- 守卫②**无占位即跳过**（:313-314、:326-327）：无占位行或无第④段标题 → 跳过不动。
- 守卫③**失源不阻断**（:348-349、:382-385 docstring 与实装）：`tasks.json` 不可读 / 复盘卡链目录缺失 → 全局「失源」报告、骨架原状不动；单文件回填异常 → 该文件降级保留「（待填）」占位，**closed on failure：回填失败绝不阻断骨架，占位逻辑永不删除**。
- **回滚方式**（:25 docstring）：`meetings/` 已入 git，单文件 revert 即回「（待填）」原状。

### 2.5 阻塞点核查

| # | 潜在阻塞 | 核查结果 | 证据 |
| --- | --- | --- | --- |
| B1 | **对 cwd 敏感** | **无阻塞** | `ROOT` 由 `__file__.resolve().parents[2]` 推断（:35），非 cwd 相对。实测第 3 次从 `C:\` 起跑（`cd /c && python .../legion_meeting_backfill.py --date 2026-10-19`），输出与在仓根跑**逐字一致**，`CWD_INDEPENDENT_EXIT=0` |
| B2 | **当日数据缺失会崩** | **无阻塞（优雅降级）** | 当日 `daily_review/2026-10-20/` 不存在时，输出 `disposition=失源` + 明确 detail，**退出码 0，无 traceback**（`run()` :362-364 前置 return） |
| B3 | **写入路径不存在** | **无阻塞** | `meet_dir` `daily_review` `.worktrees/tasks.json`（195101 字节，mtime 2026-10-20 09:46）三者实测均存在。`ROOT/tools/validate_json.py` 存在（:37-40 的 sys.path 注入指向 `C:\项目\老田的AI助手\tools\`，该目录下 `validate_json.py` 实存） |
| B4 | **依赖包缺失** | **无阻塞** | `from validate_json import check_tasks_doc`（:40）导入成功，脚本正常启动 |
| B5 | **幂等性**（重复跑是否叠加草稿） | **代码级成立，本次未实证** | :311-312 `DRAFT_LINE_RE` 幂等守卫 + `_self_test` 内「幂等：二次跑判已回填」断言（:464-465）。**本次未带 `--write`，故真实数据上的幂等行为未实证——记为待批项（需一次带 `--write` 的受控跑才能验）** |
| B6 | **挂载机制**（拍板执行单 §5.2 已证伪） | **阻塞——非脚本问题** | `tools/legion_scheduler.py` 的 `dispatch_one()` 只 POST 派工条目到 8096→8093，**不执行任何脚本**；故注册进 `schedules.json` 不会跑回填。这正是老田已拍板「建真 Windows 计划任务」的原因，与本单「五要素待一句批准」一致 |

### 2.6 任务二总结论

**技术侧是否具备挂载条件：具备。**

- ✅ 可正常启动（`--help` EXIT=0，两次 dry-run 均 EXIT=0，无异常无 traceback）
- ✅ 参数解析正确（`--date` / `--root` / `--write` / `--self-test` 四参数齐备，`--help` 完整呈现）
- ✅ 输出可解析（`json.dumps(..., indent=2)` 机器可读，13 场逐条 `dept`/`state`/`written` 结构完整）
- ✅ **确认为零写入**（文件清单 + mtime + md5 三重对比全 NO_DIFF，git 工作区无新增）
- ✅ 无 cwd 敏感、无当日数据缺失崩溃、无写入路径缺失、无依赖缺失
- ✅ `--write` 写入点唯一（:339）、写入内容为草稿（三重保险）、覆盖当日占位受幂等/无占位/失源三重守卫约束

**唯一阻塞在脚本之外**：挂载机制（B6）——`schedules.json` 不执行脚本，须真 Windows 计划任务，属老田铁律五要素批准范围，本轮未擅动。

**待批项（须老田一句批准后由 lead 执行，本工位不执行）**：
1. **Windows 计划任务五要素**：任务名、触发器（周一/每日 17:55）、执行体（`C:\Program Files\Python311\python.exe`）、参数（`agent_platform\tools\legion_meeting_backfill.py --write`）、工作目录（`C:\项目\老田的AI助手`）。
2. **是否允许一次受控 `--write` 跑**以实证 B5 幂等性与真实写入形态（本次按红线未执行，记为待批项）。
3. **`--date` 语义确认**：缺省当天 + 17:55 触发时，`daily_review/<当日>/` 须已由 `LegionDailyReview`（NextRun 17:40）产出——**错峰仅 15 分钟窗口**，若复盘链延迟则回填器走「失源」分支空跑（不崩、不写、但当日不回填）。此依赖须在任务触发器设计时明确。

---

## 三、缺证据清单

> 以下各项**无法用现有产物核验**，一律记「待核」。不以「大概/应该/按例」代替证据。

| # | 缺证据项 | 缺什么 | 阻塞的判据 | 补齐条件/时点 |
| --- | --- | --- | --- | --- |
| N1 | **D1/D2 心跳简报产物** | `intel/2026-10-19/brief.json`、`intel/2026-10-20/brief.json` **均不存在**（目录亦不存在） | 前置② 的 H1/H2 | 需心跳推理成功落盘。按台账 §三.1「当日不补跑」，**D1 简报已不可补齐** |
| N2 | **D1/D2 `shadow_rows` 计数** | 两日 run 记录 `status_counts` 无该键（推理失败发生在影子收编前，`collect_legacy_shadow()` 未被调用） | 前置② 的 H5 | 需一次 `status=ok` 的心跳。**H5 在两日均「未采集」，非「中性 0」** |
| N3 | **D1 退出码独立留档** | 计划任务 `LastTaskResult` 只反映最近一次（D2 08:30 = 2）；D1 那次无独立留档 | 前置② 的 H4（D1 半项） | 已有 D2 退出码 2 + D1 run 记录 `status=degraded` 旁证机制正确，D1 自身退出码记待核 |
| N4 | **门 3 判据 e 的真实结论** | 本次判据 e FAIL 系**未跑 evidence_collect** 致（只读红线），非链路真实结论 | 前置③ 的 observe 退出码 | 须按 SOP §17:25「先收编、后巡检」顺序，在 **10-26（下一门 3 时点）** 完整复跑一次方得真值 |
| N5 | **checkpoint `0xC000013A` 终止根因** | 早于预告的 20:25 耗尽点终止，原因未诊断 | 门 3 链路层 | 台账 E9，需系统侧排查；须老田拍板走系统处置 |
| N6 | **回填器真实写入形态与幂等实证** | 真实数据上带 `--write` 的写入结果、二次跑幂等表现 | B5 | **待批**：须老田批准一次受控 `--write` 跑 |
| N7 | **10-20 当日回填可行性** | `daily_review/2026-10-20/` 目录（NextRun 17:40，核验时点 12:36 未到） | W42 当日运行 | 17:40 后复跑 dry-run 可验 |

---

## 四、本次核验的动作清单（可追溯）

**执行（只读 / dry-run，共 8 次调用）**：
1. `git log -1 f5ecb295`（digital-legion）→ fatal；全仓 10 个 `.git` 扫 `git cat-file -t f5ecb295` → 10/10 非 git 对象
2. `automation_update list` + `view f5ecb295-...` → 确认其为 automation 任务 ID
3. `python -m pytest tests/ -q -p no:cacheprovider`（digital-legion）→ 226 passed / EXIT=0
4. `check_observation_point.py --data-root agent_platform/data` → EXIT=2（argparse 缺参时先得 EXIT=2，已复跑纠正）
5. `check_observation_point.py --data-root data`（cwd=agent_platform，SOP 口径）→ EXIT=2
6. SOP 原命令四计数复算（10-19 / 10-20）→ done=0 / done=5
7. `Get-ScheduledTaskInfo -TaskName AgentMondayCheckpoint`（只读）→ LastTaskResult=3221225786 / NextRun=2026-10-26 17:25
8. `legion_meeting_backfill.py --help` / 无参 dry-run / `--date 2026-10-19` dry-run / 从 `C:\` 起跑 `--date 2026-10-19` dry-run（**共 4 次，全部不带 `--write`**），前后 3 份快照 diff

**未执行（红线）**：未带 `--write` 调用任何一次；未改计划任务、未改配置、未注册、未建计划任务、未 commit/push、未发飞书、未杀进程重启服务、未跑 evidence_collect（会写证据仓）。

**唯一写点**：本文件 `docs/legion/2026-10-20_est_cost三前置核验与W42回填器dry-run验证.md`。快照文件落 `%TEMP%\w42snap\`（系统临时目录，非仓内）。

---

*核验：ws-pre 工位，2026-10-20 12:32~12:50。全程只读 + dry-run；缺证据一律记「待核」，无推断充数。est_cost 解冻三前置现为 1 PASS / 2 FAIL，不具备解冻条件，接线维持冻结。*
