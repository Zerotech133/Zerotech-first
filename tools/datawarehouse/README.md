# 数据仓治理工具（DATA1）

> 归属：**DATA1 张松（数据智能部/兰台 · 数据治理部主官）**｜方案 A（L1）落地件
> 写工具仅 3 个（均需显式参数）：`registry_rebuild.py --apply`、`manifest_repair.py`（修复 `_manifest.json`）、`ann_history_sync.py`（补公告历史）；其余**全部只读**：不写数据仓、不连需凭据的服务、不触发任务；所有"运行态"结论来自文件/DB 快照。
> 首个写工具 `registry_rebuild.py` 只在**主仓数据根**重建 `warehouse_registry.json`，严守 AGENTS §3：先读 `C:\Temp\kg_sync_task.log` 尾部→备份→原子写→重读校验→补记日志。

## 工具清单

| 工具 | 作用 | 命令 | 退出码 |
|---|---|---|---|
| `catalog_snapshot.py` | catalog v18 与 registry 一致性（sha256）+ 抽样物理漂移（按 layout 计数；diff≤1 归 info、>1 归 warn；sqlite/pg 跳过文件数） | `python tools/datawarehouse/catalog_snapshot.py --json` | 0/1/2 |
| `registry_rebuild.py` | 按 catalog v12 全量重建 registry（旧条目原样保留+新表补登） | `python tools/datawarehouse/registry_rebuild.py --dry-run/--apply [--json]` | 0/1/2 |
| `freshness_report.py` | 六表 prod_max（footer，不凭 mtime）+ 4 库停摆 | `python tools/datawarehouse/freshness_report.py --json` | 0/1/2 |
| `manifest_check.py` | 4 个 `_manifest.json` 键 ∩ catalog 表名 | `python tools/datawarehouse/manifest_check.py --json` | 0/1/2 |
| `quality_test.py` | 空分片/分区重复/行数等确定性检查 | `python tools/datawarehouse/quality_test.py --json [--table daily]` | 0/1/2 |
| `lineage_impact.py` | 改表前影响清单（静态检索消费方） | `python tools/datawarehouse/lineage_impact.py --table daily --json` | 0/1 |
| `backup_receipt_check.py` | 最新备份代/产物/异地 receipt/断档小时 | `python tools/datawarehouse/backup_receipt_check.py --json` | 0/1/2 |
| `db_instance_probe.py` | 15 个端口只读探活（不连库） | `python tools/datawarehouse/db_instance_probe.py --json` | 0/1 |
| `run_all.py` | 日检总入口（weekly/monthly 子集） | `python tools/datawarehouse/run_all.py --json [--weekly|--monthly] [--emit-memory]` | 0/1/2 |
| `metadata_consistency.py` | catalog×registry×lifecycle×store 元数据一致性（合并门禁 P77） | `python tools/datawarehouse/metadata_consistency.py` | 0/1/2 |
| `staging_health.py` | staging 区健康/滞留检查 | `python tools/datawarehouse/staging_health.py --json` | 0/1/2 |
| `lifecycle.py` / `lifecycle_coverage.py` | 生命周期登记与覆盖率 | `python tools/datawarehouse/lifecycle_coverage.py --json` | 0/1/2 |
| `contract_export.py` | 数据契约导出（contracts） | `python tools/datawarehouse/contract_export.py --json` | 0/1/2 |
| `manifest_repair.py` | `_manifest.json` 修复（唯一写工具之一） | `python tools/datawarehouse/manifest_repair.py --dry-run` | 0/1/2 |
| `qualify_probe.py` | 数据质量探查（抽样/分布） | `python tools/datawarehouse/qualify_probe.py --help` | 0/1 |
| `ann_history_sync.py` | 公告历史补齐（写数据根） | `python tools/datawarehouse/ann_history_sync.py --help` | 0/1/2 |
| `db_health.py` | 4 库连接/停摆检查 | `python tools/datawarehouse/db_health.py --json` | 0/1/2 |

## 设计边界

- **只读**：sqlite 一律 `mode=ro`；parquet 只读 footer；不调用任何采集/发布/清理脚本。
- **唯一写口**：`registry_rebuild.py --apply`（重建 registry，数据根结构性操作按 AGENTS §3 互斥纪律：读 kg 日志尾→备份 `.bak-<时间戳>`→tmp+replace 原子写→重读校验→补记日志）。
- **不替代** `quant_backtest/data_health.py`：那边是**全量仓扫描**（60s 缓存、7 分组）；本目录是**主管日检的轻量断言**（秒级、可进计划任务）。
- **不凭 mtime 推业务日**：优先 parquet footer statistics；取不到时退化文件名并在输出标 `filename_degraded`。
- **降级显式**：pyarrow 缺失时标注 `degraded`，不伪造结论。
- **输出可存档**：`--emit-memory` 时写 DATA1 的 `dept/data/observation` 记忆（MemoryStore RBAC）；默认 dry-run 不落盘。

## 典型用法

```powershell
# 每日 06:30（计划任务 DW-Daily，属 L2）
python tools/datawarehouse/run_all.py --json --emit-memory

# 改表前查影响
python tools/datawarehouse/lineage_impact.py --table daily --json

# 备份断档排查
python tools/datawarehouse/backup_receipt_check.py
```

## 回滚 / 停用

全部为独立脚本，删除目录即停用；`run_all.py` 默认 dry-run，不产生副作用；无守护进程、无端口占用。

## OTLP 追踪（默认关，2026-10-27 起）

关键链路已接 fail-open 追踪助手 `tools/otel_tracing.py`（Phoenix 可观测底座，UI http://127.0.0.1:6006）：

```powershell
$env:OTEL_ENABLED = "1"                                # 默认空=完全 no-op
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:6006/v1/traces"
$env:OTEL_SERVICE_NAME = "zerobond-datawarehouse"
python tools/datawarehouse/run_all.py --json --dry-run
```

- 未启用时零依赖、零行为变化；OTEL 包缺失/导出失败也静默降级（绝不阻断主流程）。
- Phoenix OTLP HTTP 端点在 **6006/v1/traces**（4317=gRPC；4318 预留）。
