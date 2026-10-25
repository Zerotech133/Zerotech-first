# 统一测量口径（MEASUREMENT CANON）· 2026-10-26

> 工单 `OS-FIX-MEASURE-20261026`。本页回答"哪个数字说了算"。出现冲突时以本页为准；
> 任何状态数字都必须能被三元组（as-of / tool / scope）复核，否则视为未核验。

## 1. 权威源表（失败数只有一个权威）

| 指标 | 权威产物 | 读法 | 性质 |
|---|---|---|---|
| 计划任务失败数 | `portal/data/task_exit_audit/task_exit_summary.json` + `task_exit_ledger.jsonl`（`agent_platform/tools/task_exit_audit.py` 产出） | 只有 `failures[]` / `failed` 才是失败权威 | **唯一失败权威** |
| 执行面健康 | `/api/execution/health`（`portal/api_execution.py` + `portal/execution_manifest.json` 白名单） | `summary` 计数 + `worst`（红= fail > 黄= stale > 灰= never） | manifest 视图，**不是第二本账** |
| 合规健康卡 | `tools/gen_health_card.py` → `health_card.json` 的 `checks[]` | 每项绿/黄/红；N/A 必须写清"不可采集" | 聚合视图 |
| 警报上游 | `/api/alerts` 的 `source_health`（6 源 status / as_of） | as_of 是生产者完成时间，不是文件 mtime | 采集侧证据 |

### 1.1 退出码语义（execution_health 必须与账本 `EXIT_SEMANTICS` 同口径）

| 退出码 | 含义 | execution_health status | 进 failed / worst？ |
|---|---|---|---|
| 0 / 267009 / 267011 / 267014 | 成功 / 运行中 / 尚未跑 / 被终止 | ok / never / stale 等原判级 | 否 |
| 3 | 上游证据不可信 → 脚本主动拒跑 | `upstream_untrusted` | **否** |
| 0xC000013A（3221225786） | 被取消 / 强杀（关机、注销、时限） | `cancelled` | **否** |
| 0x800710E0（2147946720） | 启动器已在运行 / 启动请求被拒（IgnoreNew） | `launcher_refused` | **否** |
| 其它非零 | 真失败 | `fail` | 是 |
| manifest `enabled:false` | 已停用的旧条目 | `disabled` | 否（不入红绿） |

- `result_ok` 语义 = "是否成功执行"：`upstream_untrusted` / `cancelled` / `launcher_refused`
  三者仍为 `False`（确实没成功执行），但 **status 不染红、不进 worst**；
  R3 watchdog 只对 `fail`/`stale` 响，天然不误报。
- manifest 2026-10-26 变更：`AgentWatchdog2`、`HermesWebUI-Guard` 置 `enabled:false`
  （已由 AgentWatchdog / HermesWebUI-Guard2·Next-Guard 覆盖，避免 stale 假红）。
- **写盘 ≠ 生效**：`api_execution.py` 由 8091 进程启动时 import（manifest 每次热读、代码不热更）。
  本次只本地提交，**未重启、未合并**；生效需 L2 重启审批。改前读数见 §3。

### 1.2 健康卡判级口径

| 检查 | 口径 |
|---|---|
| `ops.task_launch_failures` | 不可采集（沙箱/权限会话里 `schtasks` 报错 → `available=false`）= **N/A，判黄不判红**，detail 写明"需管理员会话复核"；只有 ops 块缺失（采集器未运行）或 `available=true` 且真拿到 `launch_failures` 才红。采不到 ≠ 任务故障 |
| `ops.alert_watch_liveness` | 心跳新鲜（≤15min）且 rc=3 / `upstream_ok=false` = **黄（不是绿）**：本体在跑、脚本按设计拒跑不是停摆，但上游 6 源 stale → 告警内容不可用；周末/非交易日 detail 追加"可能属预期，交易日需补源时间戳"。rc=0 且 `upstream_ok=true` 才绿；rc 非 0/3 黄；心跳缺失/过期回退状态文件旧口径（>26h 红） |
| `task_audit.failures` | 沿用账本：无失败=绿（不是"没数据"）；未知退出码单列，不静默吞 |

## 2. 数字书写规则（强制）

1. 任何状态数字必须带三元组：`（as-of YYYY-MM-DD HH:MM，tool=…，scope=…）`。
2. **禁止跨工具混用**：健康卡数字 ≠ 账本数字 ≠ `/api/execution/health` 数字；引用前先问三件事。
3. `STATUS.md` 只写核验过的数字，且每个数字带三元组；未核验写"待核验"，不写估计值。
4. 采集失败写 N/A（并注明原因），不写 0、不判绿。
5. 口径变更必须同步测试：本单落了 `portal/test_execution_mixin.py`、`portal/test_alert_source_health.py`、
   `tools/tests/test_ops_tools.py`。

## 3. 当前权威读数（示例，全部带三元组）

- 任务失败 **0**（as-of 2026-10-26 00:08，tool=`agent_platform/tools/task_exit_audit.py --scan`，scope=军团系计划任务 46 项）：
  `failed=0 / upstream=1（AlertWatch rc=3）/ cancelled=1（AgentMondayCheckpoint 0xC000013A）/ terminated=1（NoticeBackfill）/ disabled=3 / never_run=2`。
- 执行面（as-of 2026-10-25 23:49，tool=`GET /api/execution/health`，scope=`portal/execution_manifest.json` 22 项）：
  `ok=18 / fail=2 / stale=2`。本单改后**重启前**不变；重启后预期 `fail 2→0`（AlertWatch→upstream_untrusted、
  AgentMondayCheckpoint→cancelled）、`stale 2→0`（两条 disabled），以 `?force=1` 实测为准。
- 健康卡（as-of 2026-10-25 23:49，tool=`tools/gen_health_card.py`，scope=治理检查全量）：
  `17 绿 / 4 红 / 6 黄`；其中沙箱 `ops.task_launch_failures` 属"不可采集假红"、AlertWatch 上游不可信按旧口径不在正确颜色上，
  本单分别改为 N/A 黄 / 上游不可信黄。

## 4. 已知留口（本单未修，下一单）

1. **AlertWatch 周末/非交易日跳过**：`news_collector/alert_watch.py` 与 `alert_push.py` 在主仓工作树有其它会话
   未提交改动，本单**禁止改**（避免撞车）；周末 as_of 跨日仍会 rc=3（提示性拒跑）。下一单在 alert_watch 侧加交易日历跳过。
2. **交易日节假日日历未接**：健康卡 `_is_non_trading_day()` 只判周末（`weekday>=5`），节假日只能靠人。
   未接日历前，节假日上游 stale 只按"黄 + 可能属预期"呈现，不判绿也不强行判红。
3. **weak / cross / council 3 源无生产者时间戳**：`source_health` 中这 3 源无 producer 完成时间证据（as_of=null），
   `stale` 属预期，**不进 CORE_SOURCES**；不得为了变绿放宽时间戳校验，处置在数据生产侧（补 completed_at / 生成时间）。
4. **AlertWatch 18h 窗口只改了服务端**：`portal/api_common.py` 的 `ALERT_FRESH_WINDOW` 默认 5→**1080 分钟（18h）**，
   口径="当日 EOD 批次即当期"，跨日仍 stale；`alert_push.py` 消费端默认值仍为 5（并行 WIP 未同步）。
   临时用同一环境变量 `ALERT_FRESH_WINDOW_MIN` 两端对齐，长期随下一单一起改。
5. `267014 / 267009` 在部分工具仍按旧口径计失败（账本已单列 terminated / running），以 §1.1 为准逐步收口。

## 5. 验收命令

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'
& 'C:\Program Files\Python311\python.exe' -m pytest portal/test_execution_mixin.py portal/test_alert_source_health.py -q -p no:cacheprovider
& 'C:\Program Files\Python311\python.exe' -c "import json;json.load(open('portal/execution_manifest.json',encoding='utf-8'));print('manifest OK')"
```

> `news_collector/test_alert_delivery_p0.py` 的 12 个失败是 master 既有问题（测试 mock 的 `strftime` 缺
> `%Y-%m-%d %H:%M:%S` 键，`alert_watch._write_heartbeat` 用到），与本次改动无关，属并行 WIP 区，本单未改。

## 6. 2026-10-26 追加：交易日历 / 生产者时间戳（OS-FIX-MEASURE2-20261026）

> 本单只本地提交，**未合并、未重启**；`portal/api_common.py` 由 8091 进程启动时 import，
> 生效需 L2 重启审批（重启前线上仍走旧口径）。

### 6.1 交易日历 artifact（健康卡休市语义的唯一来源）

| 项 | 值 |
|---|---|
| 路径 | `portal/data/trade_cal_open_days.json`（gitignored 运行时数据，不随 git 分发） |
| schema | `trade_cal_open_days/v1`：`{"schema","exchange":"SSE","range":["2020-01-02","2027-12-31"],"count":1941,"open_days":["YYYY-MM-DD",…]}` |
| 语义 | `open_days` = SSE `is_open=1` 的交易日列表；**不在列表内 = 休市**（覆盖周末 + 法定节假日 + 调休，不再用 weekday 猜） |
| 生成方式 | 由 lead 从本地 TuShare `trade_cal`（exchange=SSE、is_open=1）导出为上述 schema；年报/交易日历更新后重新生成覆盖即可 |
| 读取方 | `tools/gen_health_card.py` 的 `_is_non_trading_day()`；默认路径 `Path(__file__).parents[1]/portal/data/trade_cal_open_days.json`，`ZB_TRADE_CAL` 可覆盖 |
| 降级纪律 | 文件缺失/坏 JSON/空 `open_days` → 回退 `weekday>=5`（旧口径），**绝不抛异常**；测试覆盖坏 JSON 与缺失两种回退 |

### 6.2 健康卡休市语义

- `_is_non_trading_day()` 只用于给 `ops.alert_watch_liveness` 黄项 detail 追加
  「当前为周末/非交易日：可能属预期，交易日需补源时间戳」——**不参与判绿/判红**：
  非交易日不能成为故障的免罪符。
- 新增分支（与 OS-FIX-ALERT2 联动）：心跳新鲜 + `rc=0` 且 `upstream_ok is None` →
  **绿**，detail =「非交易日静默（预期）：无 EOD 源预期，按设计跳过；交易日恢复校验」。
  语义依据：该组合是 alert_watch 非交易日跳过路径
  `_write_heartbeat(0, None, 0, "non_trading_day…")` 的生产者契约，不是"未记录校验结果"的未知态。
- 其余 alert_watch 口径不变：rc=3 / `upstream_ok=false` → 黄；rc 非 0/3 → 黄；心跳
  缺失/过期回退旧状态文件口径（>26h 红）。

### 6.3 生产者时间戳边界（cross / council / weak）

| 源 | 生产者证据 | 判定边界 |
|---|---|---|
| cross | `build_cross()` 产物 `completed_at`（北京时间 ISO+08:00）+ `trade_date`（各源信号日 dt 最大值，取不到 `None`）；`src_state[sid].anchor` 为新增字段（向后兼容） | 两者齐备才进 `dated()`；缺一 → `stale/unproven_completion`。`trade_date` = 信号覆盖到哪一天，**不是聚合器运行日**；`generated_at` 墙钟永远不能当完成证据 |
| council | `_council_stats()` 的 `ts` = 最新业务日（`d == max(d)`）留痕行自身 `ts` 归一为 +08:00 ISO（原取 shadow stats.updated，与 d 不同日 → 恒 unproven） | `d` 与 `ts` 日期一致、为当日且在 18h 窗口内才 ok；缺 ts → unproven |
| weak | `reports/weak_scan_last.json`（schema `weak_scan_last/v1`：`completed_at`/`trade_date`/`source`/`case_count`），由 `quant_backtest/weak_scan.py` 在**成功出口原子写**（含零命中：`case_count=0` 也写） | api_common 仅在 marker 有效**且** `weak_track_*.jsonl` 在案时用 `dated()`；marker 缺失 → unproven（不拿 CSV/mtime 顶替）。**marker 只在真实跑过一轮 weak_scan 后才出现**——未跑仍是 unproven，这是诚实状态不是缺陷 |

### 6.4 周末/非交易日规则（统一）

1. 交易日 = `open_days` 命中；周末/法定节假日/调休休市日一律非交易日。
2. `api_common.dated()` 的"当期"判据仍是 `trade_date == today 且 anchor 日期 == today`
   且 ≤18h 窗口——**非交易日没有当期生产者证据，因此信源 stale 属预期**（不得为绿放宽）。
3. 健康卡休市文案只影响 detail；日报/STATUS 引用源健康必须带三元组（as-of/tool/scope），
   并把"非交易日"写进说明。
4. alert_watch 非交易日跳过属 OS-FIX-ALERT2 范围；本单只消费其心跳契约。

### 6.5 本单对 §4 留口的处置

- §4.2「交易日节假日日历未接」→ **已接**（§6.1/6.2），留口关闭。
- §4.3「weak/cross/council 无生产者时间戳」→ **已接线**：cross/council 生产端补字段，
  weak 补成功出口 marker + api_common 只认 marker；marker 首现要等下一轮真实
  `start_weak_scan.ps1` 运行，之前维持 unproven。
- §4.4（AlertWatch 18h 只改服务端）/§4.5（267014/267009 旧口径）本单未动。

