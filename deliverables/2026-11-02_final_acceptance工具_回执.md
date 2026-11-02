# final_acceptance 一键终验收工具 · 回执

> 2026-11-02 15:50–16:05 ｜ 工作目录 `C:\项目\老田的AI助手` ｜ 共享 task-74（终验收工具·车道2）
> **只读**：工具仅读取任务状态/日志/数据仓 JSON（+ G10 只读导入 `kpi_report.build_report`），不触发任务、不改数据/catalog/registry、不重启；唯一写入=工具输出的 JSON/MD（占位与验收时）。未 commit。

## 〇、交付物

| # | 文件 | 说明 |
|---|---|---|
| 1 | `tools/datawarehouse/final_acceptance.py` | G1–G10 一键检查；每 Gate 输出 `pass/fail/expected-risk/unknown` + evidence；总判定=PASS/FAIL/UNKNOWN；JSON+MD 输出；退出码 0/1/2 |
| 2 | `tools/datawarehouse/tests/test_final_acceptance.py` | **14 个单测**（总判定/单 fail 阻断/expected-risk 不算失败/unknown 阻断/G1 零行与缺失/G2 严格窗口/G6 版本与陈旧/G8 accepted-risk/JSON schema/Markdown/落盘 json-only） |
| 3 | `research/reports/final_acceptance_20261102.json` | 今日**占位输出**（15:57 生成；pre-T-window 状态，verdict=FAIL 属预期，见 §三） |
| 4 | `research/reports/final_acceptance_20261102.md` | 同日 Markdown 摘要（同内容表格版） |
| 5 | 本回执 | 定义/用法/测试/限制/**明早 09:00 命令** |

## 一、Gate 定义与判据（对齐清单 §1）

| Gate | 检查对象 | 判据（工具实现） | expected-risk |
|---|---|---|---|
| **G1** | 今晚 TdxSync A/B/C/D 四批 | 四任务 `LastResult=0` 且 `LastRunTime` 在 T 窗（≥17:30）；数据级 = TdxSyncProbe 严格窗口记录 `rows_delta_total>0` + `data/tdx/` 7 张关键表最新 `part_*.parquet` mtime > T 窗起点 | 不接受 |
| **G2** | TdxSyncProbe 19:20+ 严格判定 | 19:20 后最近一条记录：`code=0`、`fail=0`、`lock_hits=0`（probe 任务日志优先，回退 JSONL 摘要；无记录→unknown） | 不接受 |
| **G3** | qualify 分布 / K8 复判 | `warehouse_registry.json` 的 `qualification_status` 分布，`unknown<40` 判过 | 不接受 |
| **G4** | XmapSync 明早 06:00 | 任务今日已跑（LastRunTime=验收日）且 `LastResult=0` | 不接受 |
| **G5** | KgDailySync 06:20 链 | 任务 rc=0 且今日已跑；`catalog.json` 当日更新；`registry.catalog_sha256 == SHA256(catalog.json)` | 不接受 |
| **G6** | DataWarehouseSnapshot 06:40 首跑（K3） | 任务 06:40 后 rc=0；`snapshot.generated_at` = 验收日；`dw_snapshot_daily.log` 末行 `ok=true/rc=0` 且当日；`snapshot.catalog.version == catalog.version` | 不接受 |
| **G7** | NoticeBackfill 06:45（PT8H） | `\ZeroBound\NoticeBackfill` 今日已跑且 rc=0（旧任务 Disabled 仅记录） | 不接受 |
| **G8** | A3RotationCanary 07:00 | **rc=1 → expected-risk**（裁定 B accepted-risk，不阻断）；rc=0 → pass（风险已消除）；其它非 0 → fail | ✅ 仅此项 |
| **G9** | SentimentBT 07:20 首跑 | 任务今日 rc=0；`sentiment_bt_result.json`/`jev_impact.json` 存在且 age<48h；无当日 push 痕迹 | 不接受 |
| **G10** | KPI 复跑 | 只读导入 `kpi_report.build_report()`：`ok≥9`、`unknown=0`、`K3=ok`（K10 warn 不计失败，清单口径） | 不接受 |

**总判定**：任一 `fail` → `FAIL`；无 fail 但有 `unknown` → `UNKNOWN`（fail-visible，不硬编码判过）；否则 `PASS`（expected-risk 不阻断）。

## 二、用法

```powershell
cd C:\项目\老田的AI助手
$py = 'C:\项目\py312\python.exe'

# 默认：今日验收（写 research/reports/final_acceptance_<date>.json + .md）
& $py -X utf8 tools\datawarehouse\final_acceptance.py

# 【明早 09:00 终验收】验收日=20261103，T 窗自动取 11-02（写 final_acceptance_20261103.json/.md）
& $py -X utf8 tools\datawarehouse\final_acceptance.py --date 20261103

# 可选参数
#   --night-date 20261102   指定 T 窗日期（默认：验收日 19:20 前 → 前一日；19:20 后手工验收 → 当日）
#   --json-only             只写 JSON（不写/不打印 MD，适合 CI）
#   --json-out X.json --md-out X.md   自定义输出路径
#   --now 2026-11-03T09:00:00+08:00   覆盖当前时间（复核/演练）
# 退出码：0=PASS / 1=FAIL / 2=UNKNOWN / 3=参数错误
# 说明：G1/G2 数据级同源（TdxSyncProbe 严格窗口记录）；probe 未跑时 G1/G2=unknown（不会假装通过）
```

## 三、今日占位输出（pre-T-window，**FAIL 属预期**）

`research/reports/final_acceptance_20261102.json`（15:57，`--date 20261102 --night-date 20261102`）：

| 项 | 值 |
|---|---|
| verdict | **FAIL**（今日 16:00 前运行，T 窗尚未开始） |
| counts | pass=4 / fail=4 / **expected-risk=1** / unknown=1 |
| G1 | fail（四批未跑：LastResult=267011；无 probe rows 增量/part 新 mtime）— 今晚 17:35 后自然转判 |
| G2 | **unknown**（无 19:20 后 probe 记录）— 今晚 19:20 后才有严格记录 |
| G3 | **pass**（unknown=15 < 40；分布 deprecated 8 / evidence_ok 170 / evidence_partial 54 / planned 9 / unknown 15） |
| G4 | pass（XmapSync 今日 14:37 rc=0） |
| G5 | pass（KgDailySync 今日 06:20 rc=0；catalog 当日更新；registry sha 对齐） |
| G6 | fail（`snapshot.catalog.version=28` vs `catalog.version=32` — 快照 14:05 生成，catalog 15:47 已更新；明早 06:40 首跑后应一致，若仍不一致即 fail-visible 真问题） |
| G7 | fail（`\ZeroBound\NoticeBackfill` 今日 14:46 LastResult=**267014**（终止）；明早 06:45 PT8H 跑后应 rc=0，否则按清单 R3 处置） |
| G8 | **expected-risk**（A3 canary rc=1，accepted-risk，符合裁定 B） |
| G9 | fail（SentimentBT 尚未首跑：LastRun=1999 占位；明早 07:20 后判） |
| G10 | pass（KPI ok=9 / unknown=0 / K3=ok） |

> 结论：占位输出证明工具可跑通、分类正确；**FAIL 是"首夜前未到点"的真实状态，不是验收失败**。明早 09:00 复跑后应按 §五 期望全部 pass/expected-risk。

## 四、测试与门禁

| 项 | 结果 |
|---|---|
| `pytest tools/datawarehouse/tests/test_final_acceptance.py -q` | **14 passed**（0.36s） |
| 覆盖用例 | 全 pass→PASS；单 fail→FAIL；expected-risk 不算失败；unknown 阻断 PASS；G1 零行 fail + probe 缺失 unknown + 任务 rc 非 0 fail；G2 严格窗口过滤/失败/无记录 unknown；G6 通过 + 陈旧/版本不一致 fail；G8 rc=1 expected-risk / rc=0 pass / rc=2 fail / 缺失 unknown；JSON schema；Markdown 表格；`--json-only` 落盘不写 MD |
| 全 datawarehouse 测试目录 | `pytest tools/datawarehouse/tests -q` → **307 passed**（无回归） |
| 语法门 | `python tools/check_syntax.py --fail-on-warning` → **PASS**（4131 文件 / 0 错 / 0 警）；`python -W error -m py_compile` rc=0 |
| 运行时 | `C:\项目\py312\python.exe -X utf8 ... --json-only` 实测 rc=1（占位）且只写 JSON；`--help` 正常；GBK 控制台 emoji 已做 `_safe_print` 兜底（首版曾因 🔴 在 GBK 控制台 UnicodeEncodeError，已修复） |

## 五、明早 09:00 使用（终验收）

**前置**：06:00 XmapSync / 06:20 KgDailySync / **06:40 dw_snapshot** / 06:45 NoticeBackfill / 07:20 SentimentBT / 07:00 A3 canary 均已在计划任务中（本工具不触发）。

```powershell
cd C:\项目\老田的AI助手
& 'C:\项目\py312\python.exe' -X utf8 tools\datawarehouse\final_acceptance.py --date 20261103
# 输出：research/reports/final_acceptance_20261103.json + .md
# rc：0=PASS / 1=FAIL / 2=UNKNOWN
```

**期望（全部窗口成功时）**：`verdict=PASS`、counts `pass=9 / expected-risk=1 / fail=0 / unknown=0`。
**逐项读法**：
- rc=0 → 直接按 MD 表登记「终验收通过（含 G8 accepted-risk 签字）」；
- rc=1 → 看 JSON `gates[].reason` 定位：G1/G2 → 看门狗 `C:\Temp\tdx_sync_probe.log`/`tdx_sync_probe_task.log` 与四批任务；G4→XmapSync；G5→KgDailySync/catalog/registry sha；G6→dw_snapshot（06:40 首跑是否跑、日志/版本）；G7→NoticeBackfill PT8H；G9→SentimentBT；G10→`kpi_report.py`；
- rc=2 → 有 Gate 无证据（unknown）：按对应 `reason` 补证据后再判，**不得以 unknown 当 PASS**；
- G8 的 expected-risk 不阻断，但报告须逐项签字（清单 §4.3 R7）。

## 六、限制与注意事项

1. **G1/G2 数据级同源**：`rows_delta_total` 来自 TdxSyncProbe 严格窗口记录；若 probe 未装/未跑，G1/G2=unknown（fail-visible），不会用 rc=0 硬判过；
2. **G4–G9 用 `LastRunTime.date == 验收日`** 判"明早窗口是否跑过"；跨日长任务（NoticeBackfill PT8H）以 LastResult 为准（当前 267014=终止 → fail，明早应 0）；
3. **expected-risk 仅 G8** 接受；其它 Gate 出现 accepted-risk 语义（如 K10/DWG 白名单）不纳入 G1–G10 判定；
4. **G10 只读导入 kpi_report**（不写 KPI 文件）；K2/K3/K10 源会做只读 HTTP/任务查询，无写操作；K10 warn 不计失败（清单口径）；
5. **G6 catalog 版本**：要求 `snapshot.catalog.version == 当前 catalog.version`；若明早 06:40 后并行车道又重建 catalog（版本前进但 snapshot 未刷新）→ 工具会判 fail（fail-visible，需人工确认是否接受"snapshot 早于最后一次 catalog 重建"）；
6. 工具不修改任何上下文/基线文件；每次运行输出独立 JSON/MD（`--date` 决定文件名）。

## 七、回滚/边界

- **无状态变更**：不触发任务、不改数据/catalog/registry、不重启、不 commit；
- 回滚=删除本次占位输出（`research/reports/final_acceptance_20261102.json|md`，可选）；工具/测试/回执随任务提交；
- 明早若需重跑：同一命令可重复执行（幂等只读，覆盖同名输出）。

## 八、证据留档

- 占位输出：`research/reports/final_acceptance_20261102.json`（10,517 B）/ `.md`（3,215 B）；
- 测试日志：`C:\Temp\task74_tests*.log`、`C:\Temp\task74_dwsuite.log`（307 passed）、`C:\Temp\task74_syntax.log`（PASS）；
- 运行留档：`C:\Temp\task74_fa.json`（py312 `--json-only` 验证，rc=1 占位）、`C:\Temp\task74_py312.log`。
