# 看门狗 + GubaCrawl 今晚首跑演练与验收清单 v1

> 2026-11-02 14:21–14:45 ｜ 工作目录 `C:\项目\老田的AI助手` ｜ 共享 task-64（首跑演练·车道3）
> **只读声明**：未改任务/数据/代码，未 commit、未重启；所有仿真用 `--no-state`/临时目录/`C:\Temp` 留档。生产基线 `C:\Temp\tdx_sync_probe_state.json` mtime 保持 **14:20:05** 未被覆盖；guba 数据保持 **432 文件 / 237 空 / 5,065,142 B** 未变。证据留档 `C:\Temp\task64_*`。

## 〇、结论摘要（先看 5 行）

1. **看门狗严格判定已演练：rc=1，fail-visible 正确**。当前（首夜前）`--now 19:05`：四任务 `LastResult=267011`（未运行）→ `task_never_run×4`；数据级 `zero_row_all_key_tables` + `no_part_updated_since_window`（尚未同步，**属首夜前预期**）。今晚 18:20 只记录（`ready_after=18:30` 未到），**19:20 起进入严格判定**。
2. **看门狗正常路径已 mock 验证：rc=0**。mock tasks（4×Result=0/LastRun 18:05）+ 临时 data root（7 表 rows +100、part mtime 今日 17:45）+ 当日基线 → **rc=0、fail=0、`rows_delta_total=700`、7 表全 ok**。
3. **GubaCrawl launcher 无需改动即兼容 15 码/自动轮换**：旧 argv `--pool --pages 2 --limit 60` 被新采集器接受；默认 `batch-size=15` + `day_of_year % 4` 自动轮换 → **今晚批 3/4，15 码**；11-02..11-05 四天批次 2/3/0/1 **并集 60/60**（脚本验证；不轮换固定批则 4 天只有 15 码）。
4. **今晚两个风险点**：① GubaCrawl 受站方限流可能仍触发 `rc=2`（fail_rate>0.20，launcher 现有 `alert_fail_wrap` 链会告警）；② `TdxSyncProbe` 已安装（每小时 :20、LastRun 14:20 Result=0），但其任务动作**只 `exit $LASTEXITCODE`，未接 alert_fail_wrap** → 看门狗失败时只有 TaskScheduler `LastTaskResult=1` 可见，**不会主动告警**，今晚需人工看。
5. **验收硬指标**：看门狗 19:20/20:20 `rc=0` 且 `rows_delta_total>0`；GubaCrawl `attempted=15`、`fail_rate≤0.20（failed≤3）`、`new_posts≥1`、**新增 0 字节文件=0（空文件数不得 >237）**。

## 一、环境快照（as-of 2026-11-02 14:24）

| 对象 | 当前状态 | 今晚动作 |
|---|---|---|
| `\ZeroBound\TdxSync_A_高频` | Ready；LastRun=1999-11-30（从未跑）；Result=**267011** | **17:35** 首跑 |
| `TdxSync_B_财务` / `C_低频` / `D_缺口` | 同上 267011 | **17:45 / 17:55 / 18:05** |
| `\ZeroBound\TdxSyncProbe`（看门狗） | **Ready；LastRun=14:20:01 Result=0；NextRun=15:20**；Trigger=TimeTrigger 12:20 起 `PT1H` 重复；Battery 放行；`ExecutionTimeLimit=PT10M`；动作=py312 `tdx_sync_probe.py --json *>> C:\Temp\tdx_sync_probe_task.log; exit $LASTEXITCODE` | 每小时 **:20**；**18:20 记录、19:20 首次严格判定** |
| 看门狗基线 `C:\Temp\tdx_sync_probe_state.json` | `day_baseline.day=2026-11-02`、`captured_at=14:20:02`、7 表 rows（`ts_daily 4,248,146` 等） | 19:20 将与 14:20 基线对比 rows 增量 |
| `\GubaCrawl` | Ready；LastRun=**11-01 17:50:01 Result=0**；NextRun=**17:50 今日**；launcher 仍 `--pool --pages 2 --limit 60`（未改） | 17:50 首跑（新采集器默认 15 码/轮） |
| guba 数据基线 | **432 files / 237 empty / 5,065,142 B**（`data/guba`，全部 `.jsonl`） | 跑后对比空文件数/文件数 |
| 采集器版本 | `quant_backtest/guba_crawl.py`（20,052 B，13:56，P0 修复 + 自动轮换版）；`start_guba_crawl.ps1`（旧版，10-12，**argv 兼容**） | — |
| HEAD | `5dc01c477`（含 guba P0/轮换修复 commits `10598c616`/`56d61d0f6`/`5af554da6`） | — |

## 二、演练 1：看门狗「当前首夜前」严格判定

```powershell
# 任务要求命令（会写生产 state/log）；本次为只读演练，加 --no-state 并把日志指向 C:\Temp：
python tools/datawarehouse/tdx_sync_probe.py --json --now 2026-11-02T19:05:00+08:00 --no-state --log C:\Temp\task64_probe_sim1.log
```

**结果（rc=1，符合预期）**：

| 字段 | 值 |
|---|---|
| rc / code | **1 / 1** |
| summary.fail / warn | **6 / 0** |
| **fail_codes** | `task_never_run ×4`、`zero_row_all_key_tables`、`no_part_updated_since_window` |
| tasks | A/B/C/D 全部 `verdict=never_run`、`last_result=267011`、`last_run=1999-11-30`（从未运行） |
| tables | 7 张全 `verdict=suspect`（对 14:20 基线 delta=0；part mtime 仍为 10-30/10-31，早于今日 17:30） |
| window | `after_ready=true`（`--now 19:05` 已过 18:30 阈值）、`trading_day=true`；A1 未传 `--state` → probe 只读加载生产 state（不写），故数据级按 14:20 基线判 delta=0 |

**判读**：rc=1 且 **fail-visible 正确**；6 项在首夜前的构成完全合理——
- `task_never_run×4` = 四任务今晚 17:35 才首跑（当前 Result=267011 "任务尚未运行"，非失败）；
- `zero_row_all_key_tables` + `no_part_updated_since_window` = 数据还没有同步（TdxSync 未跑），是"首夜前"的必然；今晚若同步成功，这 2 项应消失；
- 若 19:20 仍出现这 2 项 → 即"任务跑了但数据零行"的假绿，按 §六 处置。

> 附：A2 用生产基线副本（`Copy-Item` 到 `C:\Temp\task64_state_copy.json`）复跑同样 rc=1/6 fails → 判定稳定、基线读取正常。**生产 state 未被写**（mtime 14:20:05 不变），仿真日志写 `C:\Temp\task64_probe_sim1/2.log`。

## 三、演练 2：看门狗「正常路径」mock 验证（rc=0 + rows_delta_total>0）

构造物（全部 `C:\Temp\task64_mock\`，不碰生产）：

| 文件 | 内容 |
|---|---|
| `tasks.json` | 四任务 `found=true, last_result=0, last_run=2026-11-02T18:05:00+08:00` |
| `data/tdx/_manifest.json` | 7 张关键表 rows=**1100** |
| `data/tdx/<table>/part_0001.parquet` | 每表一个分片，mtime=**今日 17:45** |
| `state.json` | `day_baseline.day=2026-11-02`、每表基线 rows=**1000** |

```powershell
python tools/datawarehouse/tdx_sync_probe.py --json --now 2026-11-02T19:05:00+08:00 `
  --tasks-json C:\Temp\task64_mock\tasks.json `
  --data-root  C:\Temp\task64_mock\data `
  --state      C:\Temp\task64_mock\state.json `
  --log        C:\Temp\task64_mock\probe.log
```

**结果（rc=0）**：

| 字段 | 值 |
|---|---|
| rc / code | **0 / 0** |
| fail / warn | **0 / 0**（fail_codes=[]） |
| **rows_delta_total** | **700**（7 表 × (1100−1000)，判据 `>0` 成立） |
| tasks | 4×`verdict=ok` |
| tables | 7×`verdict=ok`（示例：`ts_daily delta=100 verdict=ok`）、`part_fresh_after_window=true` |
| baseline | `established_before_run=true`、`updated=true`（临时 state 内） |
| window | `after_ready=true`、`trading_day=true` |

→ **正常路径 rc=0 与 `rows_delta_total>0` 判据均验证通过**；今晚 19:20 只要 TdxSync 四批成功写盘，期望同样 rc=0 且 rows_delta_total 为正。

## 四、GubaCrawl：launcher 与新默认（batch-size=15/自动轮换）兼容性

### 4.1 参数兼容矩阵（launcher 未改，新采集器向后兼容）

| launcher 参数（旧） | 新采集器行为（`guba_crawl.py` 当前版） | 兼容 |
|---|---|---|
| `--pool` | 进入池模式：`pool_codes(pool_days=30, limit=60)` → 60 只候选 | ✅ |
| `--pages 2` | 每码最多 2 页（硬上限 10） | ✅ |
| `--limit 60` | 池候选上限 60 | ✅ |
| （未传）`--batch-size` | **默认 15**（`BATCH_SIZE_DEFAULT=15`）→ 本轮只跑 1 批 15 码 | ✅ 自动 |
| （未传）`--batch-index` | **自动轮换**：`day_of_year % 批数`、稳定分区种子 `AUTO_ROTATE_SEED=20261102` | ✅ 自动 |
| （未传）`--fail-rate` | 默认 **0.20**：`failed/attempted > 0.20` → **rc=2** | ✅ |
| （未传）`--max-attempts` / `--backoff-base` | 默认 3 次 / 5s（5s、10s 退避） | ✅ |
| （未传）`--alert-direct` | 默认不直调；由 launcher `rc≠0 → alert_fail_wrap` 链触发（避免双告警） | ✅ |
| 日志重定向 `1> $out 2> $err` | 仍 UTF-16LE + GBK 乱码（**已知观测债**，不影响 SUMMARY 的 ASCII 数字解析） | ⚠️ |

**launcher argv dry-run**（monkeypatch 选码为空，不联网/不写盘）：`guba_crawl.main(["--pool","--pages","2","--limit","60"])` → **rc=0**，打印 `模式=… 本轮码数=0（batch-size=15）` + `SUMMARY {attempted:0,…}` → 旧 argv 完全被接受。
**采集器修复测试**：`pytest quant_backtest/tests/test_guba_crawl_fix.py -q` → **12 passed**。

### 4.2 今晚实际选择（14:24 计算，若 17:50 前池未变）

- 池 60 只 → 稳定种子洗牌切 4 批（每批 15）；`2026-11-02 day_of_year=306，306%4=2` → **批 3/4**；
- **今晚 15 码**：`300930.SZ, 605567.SH, 301158.SZ, 920751.BJ, 000419.SZ, 002523.SZ, 603280.SH, 688778.SH, 000823.SZ, 601727.SH, 000737.SZ, 000702.SZ, 002183.SZ, 600981.SH, 920086.BJ`
  - 其中仅 `605567/301158` 为已有数据的 ACTIVE，**其余 13 个是当前 237 个空文件码** → 今晚实为"填充空文件"的实战校验（成功则空文件数 237 下降）。
- **4 天覆盖矩阵**（池不变假设）：`11-02 批2 / 11-03 批3 / 11-04 批0 / 11-05 批1` → **并集 60/60**（已用脚本验证 `len(union)==60`）；对照：若固定批不轮换，4 天只覆盖 15/60 → **自动轮换是达成 4 天覆盖的关键**。
- ⚠️ 池是"近 30 天滚动"，若 `chanlun_pool.jsonl` 17:50 前更新，分区成员会变（种子固定只能保证同池内稳定）→ **验收以运行时 SUMMARY 的码数为准**（`mode` 字段会写 `pool batch 3/4（auto-rotate day_of_year）`）。

### 4.3 兼容性结论

> **launcher 无需改动即可按"15 码/天 + 自动轮换"运行**（旧 argv 全兼容，新默认生效）。建议（非阻塞、后续 L1）：① 把 `--fail-rate 0.20 --alert-log logs/guba_crawl.err` 显式写进 launcher，便于告警带上下文；② 修 PS 5.1 `1>` 日志编码（UTF-16LE+GBK 乱码）或改用 python 直接落 UTF-8 日志。

## 五、今晚首跑时间线与验收清单

### 5.1 时间线

| 时间 | 事件 | 必查 |
|---|---|---|
| 17:35 / 17:45 / 17:55 / 18:05 | TdxSync A/B/C/D 首跑 | 每任务 `LastTaskResult∈{0,267009}`、`LastRunTime=今日 17:3x/17:4x/17:5x/18:0x`；`C:\Temp\tdx_sync_core.lock` 结束后不存在 |
| 17:50 | **GubaCrawl 首跑（本轮 15 码）** | §5.2 指标；`logs/guba_crawl_status.log` 的 `exit=`；SUMMARY |
| 18:20 | TdxSyncProbe 第 1 次（记录态） | 期望 `code=0`（`after_ready=false`，只记录；若此时 rc=1 仅代表"未到点"，不算失败） |
| **19:20** | **TdxSyncProbe 首次严格判定** | 期望 **rc=0、fail_codes=[]、rows_delta_total>0、7 表 ok** |
| 20:20/21:20/22:20 | 看门狗继续每小时 | 保持 rc=0；若转 rc=1 立即按 §六 定位 |
| 次日 17:35+ | 次日 TdxSync / GubaCrawl 批 4/4 | 验证轮换（GubaCrawl 码集应与今晚不同） |

### 5.2 GubaCrawl 验收指标（SUMMARY 字段 + 空文件检查）

| 指标 | 通过标准 | 异常判定/处置 |
|---|---|---|
| `attempted` | **=15** | ≠15 → 池/参数变化；核对 `mode` 与 `chanlun_pool.jsonl` 更新 |
| `succeeded`（status=ok：有新增或有历史） | ≥12（≥80%） | 偏低多为限流；看 `failed`/原因 |
| `failed`（重试后仍失败） | **≤3**（`fail_rate≤0.20`） | >3 → 采集器自动 **rc=2**，launcher 告警链触发；查 `failed_codes` 与原因（`no_marker`/`http_4xx`/`exception`） |
| `empty`（页面可访问但无帖） | 合理即可 | 异常增多需查站点/UA |
| `new_posts` | **≥1**（交易日应有新增） | =0 → 全部 empty/失败，需人工评估重抓 |
| **`fail_rate`** | **≤0.20** | >0.20 → **rc=2（验收不通过）**，不得绕过；按告警处置 |
| `max_ts` | ≥ `2026-11-01`（昨日） | 偏旧 → 抓取/解析异常 |
| `duration_s` | 正常（15 码×2 页×1.5s+退避，预计 <3–5 min） | 过长 → 重试/网络问题 |
| **新增 0 字节文件** | **=0**（`empty` 计数不得从 237 增加） | **增加=回归**（P0 修复失效）：立即停采排查（本轮不删文件） |
| 数据收益 | `空文件数` 可下降（今晚 13 个空码若成功） | 下降为佳；文件总数维持 432（本轮码均有文件） |

**验收读取命令（只读）**：
```powershell
# a) launcher 状态（ASCII 可读）：exit=0 正常 / exit=2 = fail_rate 超阈值（已告警）
Get-Content C:\项目\老田的AI助手\quant_backtest\logs\guba_crawl_status.log -Tail 4

# b) SUMMARY 提取（out 为 PS5.1 UTF-16LE+GBK 乱码；用 python 解码后按 ASCII 键解析）
python -c "import re,pathlib; t=pathlib.Path(r'C:\项目\老田的AI助手\quant_backtest\logs\guba_crawl.out').read_bytes().decode('utf-16-le','replace'); [print(x) for x in t.splitlines() if 'SUMMARY' in x or '完成：' in x]"

# c) 空文件/文件数/字节数（与 §一 基线对比：432 / 237 / 5,065,142）
$d='C:\项目\老田的AI助手\quant_backtest\data\guba'
"files=$((Get-ChildItem $d -File).Count) empty=$((Get-ChildItem $d -File | ? {$_.Length -eq 0}).Count) bytes=$((Get-ChildItem $d -File | Measure-Object Length -Sum).Sum)"

# d) 任务退出码：LastTaskResult=0 正常；=2 表示失败率超阈值（告警已发）
Get-ScheduledTaskInfo -TaskName 'GubaCrawl' | Select-Object LastRunTime,LastTaskResult
```

### 5.3 TdxSync / 看门狗验收（19:20 判定）

| 检查 | 通过标准 | 命令/证据 |
|---|---|---|
| 四任务运行 | LastResult=0（或 267009 运行中）、LastRunTime 今日 | `Get-ScheduledTaskInfo -TaskName TdxSync_* -TaskPath '\ZeroBound\'` |
| 数据级增量 | `rows_delta_total>0`、`part_fresh_after_window=true`、7 表 ok | 看门狗 19:20 JSON（`C:\Temp\tdx_sync_probe_task.log` 尾部 / `C:\Temp\tdx_sync_probe.log` 末行） |
| 锁 | `C:\Temp\tdx_sync_core.lock` 结束后不存在（或 age<2h） | 看门狗 `lock` 段 |
| 看门狗 rc | 19:20/20:20 **rc=0、fail_codes=[]** | 任务 LastTaskResult + 任务日志 |
| 失败信号 | 若 rc=1，按 §六 映射 fail_codes | `C:\Temp\tdx_sync_probe.log` JSONL |

## 六、异常处置矩阵（本轮只读原则：只定位、不擅自重跑/改任务/删文件）

| 现象（fail_code / 状态） | 判定 | 处置 |
|---|---|---|
| `task_never_run ×4`（19:20 后仍现） | 任务未运行/被禁用/未到点 | 查任务 State/Trigger/LastRunTime；查 launcher 日志；**不手动改任务**，报 Lead |
| `task_failed` | LastResult 真失败 | 读具体 rc 与任务日志；判例 `267014/3221225786 = 强杀/取消`（非失败）按 STATUS 口径归类；记录 |
| `zero_row_all_key_tables` + `no_part_updated_since_window` | 任务 rc=0 但数据零行/未写（**假绿**） | 查 tdx part mtime / manifest rows / 锁；确认后按 P0 升级（对应 2026-10 六表静默断更同类） |
| `stale_lock_residue` | 锁残留 >2h | 看 pid 是否存活；`tdx_sync` 自身有 LOCK-CLEAR 自愈；**不手删**，记录并报 Lead |
| `task_not_run_today` | 今日无运行记录 | 查 Trigger/系统时间/任务被禁 |
| GubaCrawl `rc=2` / `fail_rate>0.2` | 采集失败率超阈值（已告警） | 读 `failed_codes` 与原因：`no_marker`/`http_4xx` 为主=站方限流/反爬 → **不得提高频率**；记录、建议换窗口/UA/延长退避；当日验收记"未通过" |
| GubaCrawl 空文件数 >237 | **P0 回归**（空结果落盘） | 停止重试、保留现场，报 Lead/审计；**不删文件** |
| GubaCrawl 文件数 >432 | 新码首次成功（预期） | 记录；catalog 未刷新前不重建 |
| TdxSyncProbe rc=1 但无告警 | 当前任务动作未接 `alert_fail_wrap`（仅 exit rc） | 以 `LastTaskResult` + 日志人工判定；建议后续 L2 补告警包装（task-44 申请单） |

## 七、边界与证据留档

- **只读**：未改任务/数据/代码；未 commit、未重启；仿真全用 `--no-state` + `C:\Temp`（生产 state mtime 14:20:05 未变；guba 432/237/5,065,142 B 未变）。
- 证据文件：`C:\Temp\task64_A1.json`（严格判定 rc=1）、`C:\Temp\task64_A2.json`（基线副本复跑）、`C:\Temp\task64_mock\`（mock tasks/data/state + 结果）、`C:\Temp\task64_guba_rotation.json`（今晚 15 码 / 4 天轮换矩阵）、`C:\Temp\task64_probe_sim1/2.log`、`C:\Temp\task64_guba_tests.log`（12 passed）。
- 判定口径与代码依据：`tools/datawarehouse/tdx_sync_probe.py`（ready_after=18:30、window_start=17:30、7 张 A 批关键表、失败率/零行/锁判据）；`quant_backtest/guba_crawl.py`（batch=15、AUTO_ROTATE_SEED、fail_rate=0.20、空结果不落盘、失败率 rc=2）；`start_guba_crawl.ps1`（17:50、旧 argv 兼容、rc≠0 告警链）。
