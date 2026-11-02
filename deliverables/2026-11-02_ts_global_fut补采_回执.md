# ts_global_fut 补采 + 资质复判 回执

> 任务：`task-38`（车道3，数据写 L1）｜执行：wa-quant｜口径 **2026-11-02 10:05–10:12 +08:00**
> 允许写域：采集脚本写 `quant_backtest/data/tdx`、`qualify_probe --apply` 写 `warehouse_registry.json`、本回执。**未改 catalog/代码、未 commit、未重启。**
> 凭据：无凭据明文。

## 0. 结论

| 项 | 结果 |
|---|---|
| 补采 | ✅ 4/4 品种成功（含历史上缺失的 **NYMEX钯金 GF_PA00Y** 新增落库）；最新交易日 **20261102**（补采前 20261015，lag 18 天 → **0 天**） |
| 数据增量 | 文件 3→**4**，总行数 12,299→**16,019**（+3,720；其中新增品种 3,448 行 + 既有 3 品种更新/回溯 272 行） |
| 资质复判 | ✅ dry-run `verdict=evidence_ok`（lag 0、manifest present via catalog、4 文件 16,019 行） |
| apply | ✅ 已写 registry：`ts_global_fut` **unknown → evidence_ok**（仅此 1 表变化，其余 255 表不变；validation 全绿） |
| 门禁 | registry↔catalog sha 匹配（`catalog_sha256` 校验通过）、kg 互斥日志检查通过并补记 |

**一句话**：ts_global_fut 从「数据停在 10-15 + manifest 缺失/回退」修复为「数据到 11-02 + 证据齐备」，资质由 unknown 升为 **evidence_ok**；回滚点齐全（游标/数据/registry 三份备份）。

---

## 1. 前置检查（步骤 1）

| 项 | 事实 | 证据 |
|---|---|---|
| kg 互斥日志 | 10:00:20 `registry_rebuild … sha=dc5de65b…`、10:00:33/34 他人 `qual_probe`；无进行中结构操作 | `C:\Temp\kg_sync_task.log` 尾 6 行 |
| 脚本参数 | `global_fut_sync.py`：`--only`（品种）/`--dryrun`/`--y0`/`--quick`；`--quick`=只拉最近 3 年 | `global_fut_sync.py:216-222` |
| `--dryrun` | 4 品种清单确认：COMEX铜 HG00Y / COMEX金 GC00Y / NYMEX原油 CL00Y / NYMEX钯金 PA00Y | `--dryrun` 输出（rc=0，无网络无写） |
| 远端连通 | 实测 `fetch('101.HG00Y', beg=20260101)` 返回 **216 行、最新 2026-11-02**；CL00Y 同 | 单次只读探针（lmt=5，不落盘） |
| ⚠️ **风险规避（偏离任务字面）** | 现有 parquet 为 **全历史**（HG 3,902 行/2011 起、GC 4,326 行/2010 起、CL 4,071 行/2011 起），而 `--quick` 以 `df.to_parquet()` **整文件覆盖**为近 3 年 → 会截断 2010–2022 历史。故**未用 `--quick`**，改「逐品种全量」（脚本生产用法，`fetch_history(y0=2010)` 逐年窗，保留全历史）；仍满足小批量/失败即停 | `global_fut_sync.py:244-263`、补采前 footer 行数（备份 `C:\Temp\global_fut_backfill_20261102_100540\`） |

**补采前备份（游标+数据+回滚点）**：`C:\Temp\global_fut_backfill_20261102_100540\`
- `ts_global_fut_manifest.json.before`（表级 cursor，1,088 B）
- `tdx_manifest.json.before`（主 manifest，28,753 B，含 `ts_global_fut={done:true,rows:12299,files:3}`）
- `GF_HG00Y.parquet` / `GF_GC00Y.parquet` / `GF_CL00Y.parquet`（补采前原件，209–239 KB）

---

## 2. 补采执行（步骤 2）

| 项 | 值 |
|---|---|
| 命令 | `python311 -X utf8 global_fut_sync.py --only <品种>` ×4（cwd=`quant_backtest`） |
| 日志 | `C:\项目\work\research2\global_fut_sync_20261102_100616.log`（8,658 B） |
| 起止 | 10:06:16 → 10:08:57（约 2 分 41 秒） |
| 结果 | 每品种 `完成 ok=1 fail=0`；**无 `[warn]` / 无限流 / 无退避失败**；`STOP=False`（未触发即停） |
| 限流自律 | 脚本内置 host 轮转（`1.push2his` 优先）+ 指数退避（2/4/8/16/32s，上限 40s）+ 每年窗/品种间 sleep 0.7–0.8s |

**补采后数据（footer 实测）**：

| 文件 | 行数（前→后） | 日期范围（后） | mtime |
|---|---|---|---|
| GF_HG00Y.parquet（COMEX铜） | 3,902 → **3,914** | 20110715 ~ **20261102** | 10-07 |
| GF_GC00Y.parquet（COMEX金） | 4,326 → **4,338** | 20100104 ~ **20261102** | 10-07 |
| GF_CL00Y.parquet（NYMEX原油） | 4,071 → **4,319** | 20100104(前 2011) ~ **20261102** | 10-08 |
| **GF_PA00Y.parquet（NYMEX钯金）** | **新增 3,448** | 20130624 ~ **20261102** | 10-08 |
| **合计** | 12,299 → **16,019** | — | — |

表级 manifest `data/tdx/ts_global_fut/_manifest.json`：4 品种 `ok=True/rows/max=20261102/fetched_at=2026-11-02 10:06~10:08`；4 个历史无效 secid（SI00Y/AHD/ZSD/CAD）保持 `ok=False`（东财无有效 secid，判例已记录，非本轮问题）。

---

## 3. 资质复判与 apply（步骤 3）

### 3.1 dry-run（写前）

```
python tools/datawarehouse/qualify_probe.py --tables ts_global_fut --dry-run --json
→ code=0  summary={tables:1, ok:1, partial:0, bad:0}
   verdict=evidence_ok  reasons=[]  lag_days=0（阈值 7）
   latest_business_date=20261102  latest_source=footer_statistics
   files_on_disk=4  rows_sampled=16019  manifest_state=present  manifest_source=catalog
```
原始输出：`C:\Temp\global_fut_backfill_20261102_100540\qualify_dryrun_after.json`（UTF-8 副本 `_utf8.json`）

### 3.2 apply（写 registry，达门禁后执行）

```
python tools/datawarehouse/qualify_probe.py --tables ts_global_fut --apply --json
→ rc=0 code=0 applied=true
```
| 校验（脚本内置） | 结果 |
|---|---|
| registry.catalog_sha256 == 当前 catalog sha（`dc5de65b…`） | ✅ `catalog_sha256_matches_catalog=true` |
| 仅允许字段变更（qualification_status/evidence_checked_at/evidence_summary/unsupported_reason） | ✅ `only_allowed_fields=true` |
| 其他 255 表不变 / physical_count / display_count / 顶层字段不变 | ✅ 全 `true` |
| 写后重读校验 | ✅ `validation.ok=true` |
| kg 互斥日志 | ✅ 检查通过，补记 `qual_probe date=2026-11-02T10:09:26+08:00 tables=1 ok=1 partial=0 bad=0 exit=0` |
| registry 备份 | ✅ `quant_backtest/data/warehouse_registry.json.bak-20261102-100926`（584,898 B） |

**变更结果**：`ts_global_fut`：`qualification_status` **unknown → evidence_ok**；`evidence_checked_at=2026-11-02T10:09:26+08:00`；`evidence_summary=「store 在，4 文件（顶层 4），抽样 4 文件 16019 行；最新业务日 20261102（footer_statistics，lag 0 天）；manifest 在；判 evidence_ok：证据齐备」`；`unsupported_reason`=「证据齐备…待发布/覆盖核验」。**全 registry 仅此 1 表状态变化**（逐表 diff 验证）；registry sha `a19f41f5… → bd7ec1be…`。

---

## 4. 残留与后续（不在本单写域）

| # | 残留 | 说明/去向 |
|---|---|---|
| 1 | 主 manifest `data/tdx/_manifest.json` 的 `ts_global_fut` 仍是旧值（rows=12299/files=3） | 采集脚本只写表级 manifest；主 manifest 由 **DW-Daily / manifest_repair** 重算（下一轮自动）——本轮不手改（写域外） |
| 2 | catalog `freshness` 仍 `20261015`（未重跑 catalog_build） | 同上，下一轮 `build_catalog.py`（KgDailySync 06:20 等）会按 footer 更新为 20261102 |
| 3 | `global_fut_sync.py` **无计划任务调度** | 建议 L2 评估加入每日任务（或并入 TdxSync 拆批），否则 18 天停更会复发 |
| 4 | `--quick` 语义危险（整文件覆盖 → 截断全历史） | 建议后续给 `--quick` 加「追加合并」或改名为 `--smoke` 并打印覆盖警告（代码变更需另立任务） |
| 5 | 4 个历史无效 secid（COMEX银/LME铝/锌/铜） | 东财当前无有效 secid（判例 2026-10-15），保持 ok=False，非本轮问题 |

---

## 5. 回滚预案（如新数据被判定异常）

```powershell
# 1) 回滚数据（补采前原件）
Copy-Item C:\Temp\global_fut_backfill_20261102_100540\GF_*.parquet C:\项目\老田的AI助手\quant_backtest\data\tdx\ts_global_fut\ -Force
# 2) 回滚表级 cursor
Copy-Item C:\Temp\global_fut_backfill_20261102_100540\ts_global_fut_manifest.json.before C:\项目\老田的AI助手\quant_backtest\data\tdx\ts_global_fut\_manifest.json -Force
# 3) 回滚主 manifest（如需）
Copy-Item C:\Temp\global_fut_backfill_20261102_100540\tdx_manifest.json.before C:\项目\老田的AI助手\quant_backtest\data\tdx\_manifest.json -Force
# 4) 回滚 registry（apply 前自动备份）
Copy-Item C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json.bak-20261102-100926 C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json -Force
```
> 回滚属写操作，需 Lead/工单确认后执行（本单未触发）。

---

## 6. 证据路径

| 证据 | 路径 |
|---|---|
| 补采日志 | `C:\项目\work\research2\global_fut_sync_20261102_100616.log` |
| 补采前备份 | `C:\Temp\global_fut_backfill_20261102_100540\`（3 parquet + 2 manifest） |
| 复判 dry-run | `C:\Temp\global_fut_backfill_20261102_100540\qualify_dryrun_after.json` |
| apply 输出 | `C:\Temp\global_fut_backfill_20261102_100540\qualify_apply_20261102.json` |
| registry 写前备份 | `quant_backtest/data/warehouse_registry.json.bak-20261102-100926` |
| 数据目录 | `quant_backtest/data/tdx/ts_global_fut/`（4 parquet + `_manifest.json`） |
| 采集脚本（只读） | `quant_backtest/global_fut_sync.py` |
