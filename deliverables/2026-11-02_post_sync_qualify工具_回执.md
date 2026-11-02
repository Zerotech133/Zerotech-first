# post_sync_qualify 工具回执（首夜后资质复判自动化 · task-73）

> STATUS: done（只写工具+测试+本回执；**未运行 --apply、未写 registry/catalog/数据、未 commit、未重启**）

> 2026-11-02 15:55 ｜ 工单：task-73 ｜ 用途：今晚 TdxSync 首夜四批后，用一条命令替代手工「读 unknown → 逐表 dry-run → 挑 ok 写回」。

## 一、工具做什么

`tools/datawarehouse/post_sync_qualify.py`：

1. 读 registry 中 `qualification_status=unknown` 的表（或 `--tables` 指定）；
2. 分批（**≤20 表/批**）调用 `qualify_probe.py --dry-run --json` 复判，汇总 verdict 与 reasons；
3. 仅当 `--apply` 时，对 **verdict=evidence_ok** 的表分批调用 `qualify_probe.py --apply --json` 写回——完全复用其**备份 + 双阶段 validation + kg 日志校验**，本工具不直接写 registry；
4. 输出 JSON 摘要（before/after 分布、升 ok 清单、仍 partial/bad 归因）+ 人类摘要。

> 设计取舍：不复刻 qualify_probe 写回逻辑（避免两套备份/校验），只按「筛选+分批+汇总」编排；写回仍由 `qualify_probe --apply` 单一入口执行。

## 二、参数 / 退出码 / 产物

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dry-run` | ✅（默认） | 只读复判；与 `--apply` 互斥 |
| `--apply` | 关 | 对 dry-run 中 verdict=ok 的表分批写回 registry |
| `--tables a,b,c` | 空 | 指定表集合（默认 registry 全部 unknown） |
| `--batch-size N` | 20 | 每批表数；**上限 20**（>20 → rc=2 拒绝） |
| `--timeout S` | 600 | 单批 qualify 调用超时 |
| `--json` | 关 | stdout 输出完整 JSON 摘要 |
| `--out PATH` | 空 | 可选：JSON 摘要另存路径（建议 `C:\Temp\...`） |
| `--log PATH` | `C:\Temp\post_sync_qualify.log` | JSONL 运行日志（start/每个 batch/apply/done） |
| `--registry PATH` | 主仓数据根 registry | 调试/测试用 |

**退出码**：0 = 复判完成（含仍 partial/bad 或写回成功）；2 = 复判失败（qualify_probe rc/JSON 异常、catalog/registry 失配）或写回失败（applied=false / validation 非全 true）；无 unknown → 0。

**JSON 摘要关键字段**：`tool / mode / registry{before.distribution, unknown_count, batch_size} / batches[{batch,tables,rc,ok,partial,bad,error}] / dry_run_summary{ok[],partial[],bad[],verdicts,by_reason} / apply{requested,ok_tables,applied_tables,skipped_tables,failed,error,batches[{applied,validation_ok,backup}]} / after{distribution,unknown_count,upgraded_count,upgraded_tables} / exit_code / elapsed_s`。

## 三、安全边界（只读/写回可审计）

- **只写**：`C:\Temp\post_sync_qualify.log`（JSONL）+ `--out` 指定摘要；不改数据/catalog/registry；
- **写回仅两条路**：`qualify_probe --apply` 内部执行；apply 批次只包含 dry-run 判为 `evidence_ok` 的表（partial/bad 永不进入 apply 批次）；
- **写回失败 fail-visible**：任一批 `registry_write.applied != true` 或 `validation.ok != true` → 记 `failed`、停止后续批次、`exit_code=2`（日志含 `apply FAILED`）；
- **kg 互斥**：写回前 `qualify_probe` 自身检查 `C:\Temp\kg_sync_task.log` 尾部；被阻断时本工具返回 rc=2 并在 `apply.error` 体现（不绕过）；
- **复判失败 fail-visible**：qualify_probe 非 0/1 rc、非 JSON、catalog_sha 失配 → 该批记 error、整体 rc=2；
- **无 unknown**：直接 rc=0（不调用 probe），摘要注明 `nothing_to_do`。

## 四、测试与门禁

```
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/datawarehouse/tests/test_post_sync_qualify.py
→ 8 passed（≥5 要求；mock registry/qualify，全部 tmp）
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/datawarehouse/tests
→ 293 passed（rc=0）
C:\项目\py312\python.exe -X utf8 tools/check_syntax.py --fail-on-warning --files "tools/datawarehouse/post_sync_qualify.py,tools/datawarehouse/tests/test_post_sync_qualify.py"
→ PASS（错误 0 / 告警 0）
```

测试用例（8）：

| # | 用例 | 断言 |
|---:|---|---|
| 1 | `test_dry_run_mixed_verdicts` | 混合 verdict 汇总（ok/partial/bad）、reason 归因、apply 未调用、日志有 start/done |
| 2 | `test_apply_writes_only_ok_tables` | apply 调用仅含 ok 表；applied/validation_ok/backup；after unknown 减少、upgraded 正确 |
| 3 | `test_partial_bad_not_written` | 仅 partial/bad 时**不调用 --apply**、applied 空、after 不变 |
| 4 | `test_apply_validation_failure_rc_nonzero` | validation.ok=false → rc=2、applied 空、日志 apply FAILED |
| 5 | `test_probe_error_rc_nonzero` | qualify_probe rc=2 / catalog_sha 失配 → 批次 error、整体 rc=2 |
| 6 | `test_no_unknown_rc_zero` | 无 unknown → rc=0、不调用 probe、摘要总数为 0 |
| 7 | `test_batch_limits` | 45 表 → dry-run/apply 均 [20,20,5]，每批 ≤20 |
| 8 | `test_cli_json_out_and_size_guard` | CLI `--json --out` 写摘要且字段齐全；人类摘要；`--batch-size 21` → rc=2 |

## 五、真实 dry-run 验证（只读，开发期实跑）

```powershell
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/post_sync_qualify.py --dry-run --json --out C:\Temp\t73_dryrun.json --log C:\Temp\t73_dryrun.log
```

结果（当前 registry，as-of 15:52）：

| 指标 | 值 |
|---|---|
| mode / exit | dry_run / 0（rc=0；0.3s） |
| before unknown | 15 |
| batch | 1 批（batch_size=20） |
| verdicts | {"ok": 0, "partial": 14, "bad": 1, "unknown": 15} |
| 归因 by_reason | {"stale_gt_7d": 14, "store_missing": 1} |
| apply | 未请求（dry-run） |
| registry | 未被改动（本工具只读；写回只在 --apply 经 qualify_probe） |
| 日志 | `C:\Temp\t73_dryrun.log` 3 行（start/batch/done） |

> 说明：当前 15 张 unknown 均仍不可升（14 张真实 stale、1 张 store_missing），dry-run 判 **0 张 ok** → 今晚 apply 时若仍如此，工具会跳过写回并 rc=0（正确行为，不硬写）。

## 六、今晚 19:25 使用命令（首夜 TdxSync 四批 + 看门狗 19:20 之后）

```powershell
cd C:\项目\老田的AI助手
$py = "C:\项目\py312\python.exe"

# 0) 数据根互斥前置：确认首夜同步/看门狗已收尾（无进行中结构操作）
Get-Content C:\Temp\kg_sync_task.log -Tail 8

# 1) 19:25 只读复判（先看「升 ok 清单 / 归因 / 批数」，不写任何东西）
& $py -X utf8 tools\datawarehouse\post_sync_qualify.py --dry-run --json `
     --out C:\Temp\post_sync_qualify_20261102_1925.json

# 2) 确认 dry-run 的 ok 清单符合预期后，写回（仅 ok；备份/validation/kg 日志由 qualify_probe 负责）
& $py -X utf8 tools\datawarehouse\post_sync_qualify.py --apply --json `
     --out C:\Temp\post_sync_qualify_20261102_1925_apply.json

# 3) 写回后门禁复验（应 PASS；写回只动 registry 四字段）
& $py -X utf8 tools\datawarehouse\metadata_consistency.py --json
& $py -X utf8 tools\datawarehouse\catalog_snapshot.py --json
```

**判读**：
- rc=0 且 `apply.applied_tables` 有值 → 升 ok 成功，`after.unknown_count` 已下降；以 JSON 摘要入台账；
- rc=0 且 `apply.ok_tables=[]` → 本轮无表可升（真实 stale/缺数据未修复），**不做任何写回**，属正常；
- rc=2 → 看 `apply.error` / `batches[].error`：qualify 失败（catalog/registry 失配、数据源不可读）或验证失败；不被阻断前不重试写回；
- 若 kg log 守卫阻断（apply.error 含 kg），先按 `C:\Temp\kg_sync_task.log` 尾部补齐同步任务 exit=0 记录再重跑；
- 19:25 若首夜同步尚未完成→只跑第 1 步 dry-run，等待完成后再跑 apply。

## 七、边界与回滚

- 本轮**未运行 --apply**：未写 registry/catalog/数据，未 commit、未重启；开发期唯一真实执行是只读 dry-run（registry mtime 未变、0 张写回）；
- 回滚：删除 `tools/datawarehouse/post_sync_qualify.py`、`tools/datawarehouse/tests/test_post_sync_qualify.py`、本回执；无数据/服务状态影响；
- 若今晚 apply 后需回滚：用 qualify_probe 自动生成的 `warehouse_registry.json.bak-YYYYMMDD-HHMMSS` 覆盖后复验双门禁（本工具不产生其他状态）。

## 八、证据

- 新增文件：`tools/datawarehouse/post_sync_qualify.py`（~420 行）、`tools/datawarehouse/tests/test_post_sync_qualify.py`（8 用例）、本回执；
- 测试：pytest 8 passed / 整套 293 passed（rc=0）；syntax PASS；
- dry-run 留档：`C:\Temp\t73_dryrun.json`（JSON 摘要）、`C:\Temp\t73_dryrun.log`（3 行 JSONL）、`C:\Temp\t73_dryrun_stdout.log`；
- 未改：`quant_backtest/data/**`、catalog/registry、任何服务/计划任务。
