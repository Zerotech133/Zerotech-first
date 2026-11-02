# ts_global_fut 表级 manifest 补登 + 复判回执（task-34）

> 任务：`task-34`（S3-01 B 组）｜执行：wa-govern ｜执行窗口：**2026-11-02 09:51:58–09:53（Asia/Shanghai）**
> 边界：只执行 `manifest_repair --apply`（写 `tdx/_manifest.json`）；**未**执行 `qualify_probe --apply`（verdict 非 ok，按任务规则不写 registry）；未改 catalog/代码、未 commit/未重启。
> 前置：catalog **v19**（08:56:24，Lead 已 rebuild）已声明 `ts_global_fut.manifest=.../data/tdx/_manifest.json`、`manifest_status=missing`；本单把表级键补进该 manifest。

## 一、执行链（全部命令与结果）

| # | 步骤 | 命令/动作 | 结果 |
|---|---|---|---|
| 0 | AGENTS §3 互斥前置 | `Get-Content C:\Temp\kg_sync_task.log -Tail 12` | 末条 `registry_rebuild 08:56:25 exit=0`；无进行中的数据根操作；当前非 TdxSync（17:30）窗口 → 可安全写 |
| 1 | 只读预览 | `manifest_repair.py --json` | plan=3：**FIX `ts_global_fut`**（manifest=`C:\aios\...\data\tdx\_manifest.json`、store=`.../tdx/ts_global_fut`、old_status=missing、**files=3、rows=12,299**、footer_scanned=3/3）；SKIP `quarantine_prod_overwrite`(lifecycle_deprecated)、`xq_comment`(lifecycle_planned) → **仅 1 张可修，与任务目标一致** |
| 2 | 执行补登 | `manifest_repair.py --apply` | `rc=0`、`applied: {ok: True, changed_tables: ['ts_global_fut'], manifests: ['.../data/tdx/_manifest.json']}`；kg 日志新增 `manifest_repair date=2026-11-02T09:52:25 tables=1 files=1 exit=0` |
| 3 | 写后校验 | 读 manifest + 与备份逐键 diff | `keys 182→183`；**added=['ts_global_fut']、removed=[]、changed=[]**（其余 182 键零改动）；JSON 可解析 |
| 4 | 复判 | `qualify_probe.py --tables ts_global_fut --dry-run --json` | `manifest_state=present`、`manifest_source=catalog`、**reasons 仅剩 `stale_gt_7d`（lag_days=18）**、`verdict=evidence_partial`、summary `{ok:0, partial:1}` → **不满足"verdict=ok 才 apply"条件，未写 registry** |
| 5 | 旁证 | `manifest_check.py --json` | `code=0`；tdx manifest `keys 182→183 / covered 169→170`（baseline 166，未回退） |
| 6 | 旁证 | `metadata_consistency.py --json` | `code=0 / issues=[] / warnings=[]`（manifest 补登未引入一致性漂移） |

## 二、前后对照

| 项 | 补登前 | 补登后 |
|---|---|---|
| `data/tdx/_manifest.json` | 28,598B / 182 keys / sha256 `7FD5ABBF…` / mtime 11-01 19:29:49 | **28,753B / 183 keys** / sha256 `44FAEAD2…` / mtime **2026-11-02 09:52:25** |
| `ts_global_fut` 键 | 不存在 | **`{"done": true, "rows": 12299, "files": 3, "repaired_at": "2026-11-02T09:52:25", "repaired_by": "manifest_repair"}`** |
| 备份 | — | **`data/tdx/_manifest.json.bak-20261102-095225`**（28,598B，旧内容完整） |
| 其它 182 键 | — | **零改动**（逐键 diff：added=1 / removed=0 / changed=0） |
| 数据文件 | 3 parquet（10-15）+ store 内 symbol manifest（1,088B，sha256 `7CFDE1D9…`） | **完全未动**（同 mtime/同 sha256；symbol 级 7 条=3 ok/4 限流失败，rows_sum=**12,299**，与表级 rows 一致） |
| registry | `ts_global_fut.qualification_status=unknown`（mtime 08:56:25） | **未写**（mtime/size/状态均未变） |

## 三、复判明细（qualify_probe dry-run，catalog v19）

```json
{"table":"ts_global_fut","store":"quant_backtest/data/tdx/ts_global_fut","store_exists":true,
 "files_on_disk":3,"files_registered":3,
 "manifest_state":"present","manifest_catalog_status":"missing",
 "manifest_path":"quant_backtest/data/tdx/_manifest.json","manifest_source":"catalog",
 "catalog_freshness":"20261015","date_check":"footer","lag_days":18,
 "verdict":"evidence_partial","reasons":["stale_gt_7d"]}
```
- **manifest 侧已解决**：`manifest_missing` 与 `manifest_fallback_tushare` 两条 reason 均已消失（manifest_state 由 missing→present）。
- **仍 partial 的唯一原因**：`stale_gt_7d`，lag=18 天（数据最新 2026-10-15，catalog freshness=20261015）。属**数据断采**问题（`global_fut_sync.py` 无计划任务调度），不是 manifest 问题。
- 因此按任务规则**未执行** `qualify_probe --apply`，registry 保持 `unknown`。

## 四、未解决/后续（按优先级）

1. **补采（本单下一步外的 L1 数据窗口）**：先 dryrun 再实跑，补最近 3 年、仅 3 个 ok 品种：
   ```powershell
   cd C:\项目\老田的AI助手
   Get-Content C:\Temp\kg_sync_task.log -Tail 10                        # AGENTS §3 互斥前置
   python C:\项目\老田的AI助手\quant_backtest\global_fut_sync.py --quick --dryrun
   python C:\项目\老田的AI助手\quant_backtest\global_fut_sync.py --quick
   # 完成后补记 C:\Temp\kg_sync_task.log（含 START/END、品种/rows）
   ```
   注意：东财 push2his host 级限流（10-15 判例），可能仍有个别品种 `ok:false`；失败会写 store 内 symbol manifest（fail-visible）。
2. **补采成功后复判 + 写 registry**：
   ```powershell
   & 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\qualify_probe.py' --tables ts_global_fut --dry-run --json
   # 若 verdict=evidence_ok/ok 且 reasons 无 stale → 再执行：
   & 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\qualify_probe.py' --tables ts_global_fut --apply
   ```
3. **接线采集任务（L2，五要素待批）**：SLA #13 外盘期货线 P0（要求 T 日 24:00），建议新增每日/按周 `global_fut_sync.py --quick` 计划任务。
4. **catalog 状态翻新（可选，Lead 决定）**：下次 `build_catalog`（v20 或 KgDailySync 06:20 链）会读到 tdx manifest 新键 → `ts_global_fut.manifest_status` 由 missing→closed、`verification_status` 视数据日期判（当前仍会 stale）。本单未改 catalog。
5. 已知：store 内 symbol 级 manifest 4/7 品种 `ok:false`（SI00Y/AHD/ZSD/CAD，10-15 限流），表级 rows 12,299 仅覆盖 3 个 ok 品种——复判时需把"覆盖不完整"与"数据陈旧"区分记录。

## 五、风险与回滚

| 风险 | 说明 | 处置 |
|---|---|---|
| 与 TdxSync 17:30 并发写 tdx manifest | 本次 09:52 非窗口，无冲突；工具为原子写 | 下次同类操作避开 17:20–17:40 |
| 表级 rows 覆盖不完整 | rows=12,299 仅 3 个 ok 品种（4 品种限流失败无数据） | 已在 §四.5 显式记录；补采后由 symbol manifest 反映 |
| catalog 与 manifest 短暂不一致 | catalog 仍记 `manifest_status=missing`，manifest 已有键 | 下次 catalog rebuild 自动对齐；qualify 已按实际 manifest 判 present，无功能影响 |
| 回滚 | manifest 变更可逆 | 恢复：`Copy-Item quant_backtest\data\tdx\_manifest.json.bak-20261102-095225 quant_backtest\data\tdx\_manifest.json -Force`（回到 182 键）；registry 未写、数据未动，无需回滚；kg 日志留痕保留 |

## 六、证据索引

- 执行输出：`%TEMP%\d_repair34.json`（预览 plan）、`%TEMP%\d_repair34_apply.log`（apply）、`%TEMP%\d_qualify34.json`（复判）、`%TEMP%\d_mcheck34.json`、`%TEMP%\d_meta34.json`
- 变更文件：`quant_backtest/data/tdx/_manifest.json`（183 keys；备份 `.bak-20261102-095225`）
- 旁证：`C:\Temp\kg_sync_task.log` 末条 `manifest_repair date=2026-11-02T09:52:25 tables=1 files=1 exit=0`；`quant_backtest/data/tdx/ts_global_fut/_manifest.json`（symbol 级，未动）；`quant_backtest/data/warehouse_registry.json`（未动）
- 依据：`docs/audit/2026-11-02_manifest缺失4表诊断.md` §2.3；task-30 回执 §三（catalog v19 声明）；`deliverables/2026-10-20_数据线SLA清单_WO-QT-20261020-002.md` #13

*签名：wa-govern 2026-11-02 09:52｜manifest 已补登；registry 因 stale 未写，如实记录。*
