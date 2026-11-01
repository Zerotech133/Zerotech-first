# S2-09 etf_mins registry 重建 · 回执

> 2026-11-02 ｜ 执行席位：dwgov-impl（受 DATA1 委托）｜ 任务：task-16（W1/W2 并行开发·车道3）
> 数据根：`C:\项目\老田的AI助手\quant_backtest\data` ｜ 结果：**S2-09 DoD（drift diff≤1）达成：diff=0，snapshot rc=0，metadata PASS，display 保持 174**

## 一、结论

| 验收项 | 结果 |
|---|---|
| `catalog_snapshot.py --json` | **rc=0，code=0，issues=[]，drift_warn=[]**（etf_mins 登记 780 = 磁盘 780） |
| `metadata_consistency.py` | **result: PASS（rc=0，issues=[]）** |
| registry 与 catalog sha | 一致：`d50f510f8297dd59ffca13d673672546d7fb23251f15a5fb9b1ac5938fd91b24` |
| registry 计数 | physical 256 / **display 174（保持不变）** |
| 数据文件 | **未增删**：etf_mins 磁盘 780 个 parquet（前后一致） |
| 泄漏/越界 | 未 commit、未重启、未改代码、未删数据文件 |

**重要：任务书前提已过期**——本单启动时（07:03）catalog 已不是 files=760：
- `2026-11-02T06:22:48` 例行 `catalog_build exit=0` 已把 catalog 刷新为 **files=780**、updated=2026-11-02T06:22:47；
- `2026-11-02T07:00:41` 已有一次 `registry_rebuild ... sha=d50f510f8297dd59 exit=0` 完成 sha 对齐（先于本单 2 分钟，应为 Lead 车道预跑）。
因此本单实际工作是：**独立复验 + 按指令做一次幂等 registry 重建留证 + 清理一处工具重加的历史遗留行 + 产出本回执**。S2-09 要求在复验时已满足。

## 二、步骤1：etf_mins 只读核对（实测）

| 项 | 实测值 | 证据/命令 |
|---|---|---|
| 磁盘文件数 | **780** 个 `*.parquet`（87.5 MB） | `Path(.../tushare/etf_mins).glob('*.parquet')` |
| 最新文件 mtime | **2026-11-01T23:30:53+08:00**（`159920_SZ_202611.parquet` 等 3 个最新） | 文件 stat |
| 数据行数 | **3,677,660 行**（pyarrow dataset count_rows） | `py312 pyarrow.dataset` |
| trade_time 范围 | **2023-09-01 09:30:00 ~ 2026-10-30 15:00:00** | pyarrow 读 trade_time 列 |
| manifest | `tushare/_manifest.json` → `etf_mins: {done: true, codes: 20, months: 2}`（store 目录内无独立 manifest） | 只读 JSON |
| catalog 登记 | version 18 / updated **2026-11-02T06:22:47**；etf_mins: `files=780`、`rows=null`、`freshness=20261030`、`verification_status=verified`、`manifest_status=closed` | catalog.json |
| rows 口径判定 | **不补 rows**：catalog 现有 256 表中 35 表 rows=null；`by_code_month` 6 表中 5 张（etf_mins/idx_mins/report_rc/stk_mins/sw_mins）均为 null，属既有构建口径（catalog_build 只对部分布局统计 rows）；且 `freshness=20261030` 与实测 max trade_time 2026-10-30 一致 | catalog.json 统计 |

**files 前后值（S2-09 核心）：760（任务书/W1W2 回执基线）→ 780（2026-11-02T06:22:48 例行 catalog_build 刷新）；本次实测：登记 780 = 磁盘 780，diff=0。**

## 三、步骤2：`agent_platform/catalogs/data_catalog.yaml` 偏差说明（未改）

- 该文件为**数据册 v6 / 27 条资源**（资源层，kind/uri/layer/owner/steward/freshness/backup/verify_period/fail_mode/grants/verified 等字段），**不存在 `etf_mins` 条目**；其字段集合也**没有 files/rows/updated**（files/rows/updated 是 `quant_backtest/data/catalog.json` 的物理表字段）——即任务书"data_catalog.yaml 中 etf_mins 条目（files=实测值）"与实际文件结构不符。
- `etf_mins` 现有登记位置：① `catalog.json`（物理表，files=780 已正确）；② `agent_platform/catalogs/dataset_registry_v1.yaml`（数据集 `ts_etf_mins`，registered=true）。
- 故**未修改 data_catalog.yaml**，也未擅自新增资源条目：按计划 v3，**S2-01 明确以 dataset_registry_v1.yaml 并入 data_catalog v7（314 条）**，此时单独新增会与 v7 合并撞车。**建议：etf_mins 资源条目随 S2-01 v7 合并纳入**（本回执作为凭据）。
- `catalog.json` 的 files 字段**不在本任务写域**（writeScopes 仅 data_catalog.yaml + 回执），且已由例行 `catalog_build` 维护为 780，无需人工改写。

## 四、步骤3：registry 重建（按要求执行，py312）

```powershell
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/registry_rebuild.py --apply --json
```

| 项 | 值 |
|---|---|
| rc | **0** |
| physical | before 256 → retained 256 + added 0 → after 256 |
| display | before **174** → after **174**（原样保留，符合任务"display 应保持 174"） |
| catalog sha256 | before = after = `d50f510f…91b24`（match_before=true） |
| generated_at | before `2026-11-02T07:00:41.345340+08:00` → after `2026-11-02T07:07:02.075962+08:00` |
| 写盘校验 | json_valid / catalog_sha256_match / physical_count_match / display_count_match = **全 True** |
| 备份 | `quant_backtest/data/warehouse_registry.json.bak-20261102-070702`（542,000 B） |
| kg 流水 | `registry_rebuild date=2026-11-02T07:07:02+08:00 tables=256 sha=d50f510f8297dd59 exit=0` |
| 幂等性 | 与 apply 前备份逐字段对比：除 `generated_at` 外**内容等价（diff=[]）** |

**清理工具历史遗留行（重要）**：`registry_rebuild.py` 的 `LIMITATIONS_V2` 常量含旧口径文案「display_assets 沿用 2026-10-09 历史证据原样保留（92 条）…」；本次 `--apply` 把它重新追加进 registry.limitations（与当前 display=174 矛盾）。已在写盘后用原子写（CRLF，tmp+replace）**移除该条第 7 行**，恢复为 6 条 limitations；复核 diff=[]（除 generated_at 外与 pre-apply 完全一致）。
→ **遗留缺陷**：该硬编码常量未修（本单边界"不改代码"），**任何再次执行 registry_rebuild --apply 都会重加该过时行**，建议后续代码任务把 `LIMITATIONS_V2` 更新为当前口径或删除硬编码。

## 五、步骤4：复验门禁（重建+清理后执行）

| 命令 | rc | 结果 |
|---|---|---|
| `py312 catalog_snapshot.py --json` | **0** | code=0；issues=[]；drift_warn=[]；files_na=[]；registry：physical 256 / display 174 / catalog_sha256_match=true |
| `py312 metadata_consistency.py` | **0** | `catalog v18 / 256 表 / registry 256 物理` → **result: PASS**，issues=[] |
| registry 最终文件 | — | 542,000 B，sha256 `c13ed45053da9ba5cc7b171d2bb6fd1a976b316f2fe1e044e1d59f942521f9d7`；CRLF 保留；无 `.tmp*` 残留 |
| catalog.json | — | 未改：290,390 B，mtime 仍 2026-11-02T06:22:47 |

## 六、数据根互斥纪律（AGENTS §3）

- 操作前读 `C:\Temp\kg_sync_task.log` 尾部：`registry_rebuild date=2026-11-02T07:00:41+08:00 tables=256 sha=d50f510f8297dd59 exit=0`（已完成，无进行中标记）→ 可执行。
- 完成后日志尾部：`evt_build exit=0 ...` / `registry_rebuild ... 07:00:41 exit=0` / **`registry_rebuild date=2026-11-02T07:07:02+08:00 tables=256 sha=d50f510f8297dd59 exit=0`**（本单补记）。
- 未删任何数据文件；etf_mins 780 个分片前后不变；未改 catalog.json/代码；未 commit/未重启。

## 七、回滚

```powershell
# 回滚到本单 apply 前（registry 与现在仅 generated_at/遗留行差异）
Copy-Item "C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json.bak-20261102-070702" `
          "C:\项目\老田的AI助手\quant_backtest\data\warehouse_registry.json" -Force
# 如需回到 display 补登记前（92 条）：使用 .bak-20261102-display（418,516 B，display=92）
```
注：catalog.json / 数据文件本单未改，无需回滚。

## 八、未做项 / 风险

1. **`data_catalog.yaml` 的 etf_mins 条目未改（偏差已披露）**：该文件无此条目且无 files/rows 字段；建议随 S2-01（dataset_registry_v1 → data_catalog v7 / 314 条）纳入。若 Lead 要求单独补登资源条目，请另开一单明确 v6→v7 口径归属。
2. **工具常量缺陷**：`registry_rebuild.py` `LIMITATIONS_V2` 仍含「92 条」过时行，每次 rebuild 会重加；已在本回执标注，待代码任务修（或改用从现有 limitations 去重合并策略）。
3. **rows 未补**：etf_mins `rows=null` 符合 catalog_build 对 `by_code_month` 的既有口径（6 表中 5 张为 null），实测真实行数 3,677,660 已记于本回执；如后续 catalog_build 支持 rows 统计再补。
4. catalog.json 由每日 06:22 例行 catalog_build 自动刷新，本次 drift 即由其自愈；S2 后续建议把「catalog_snapshot rc 守卫」放进每日流程，避免人工发现滞后（约 3 小时窗口）。

## 九、复核命令

```powershell
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/catalog_snapshot.py --json
C:\项目\py312\python.exe -X utf8 tools/datawarehouse/metadata_consistency.py
python -X utf8 -c "import json,hashlib;from pathlib import Path;D=Path('quant_backtest/data');r=json.load(open(D/'warehouse_registry.json',encoding='utf-8'));print(r['display_count'],r['physical_count'],r['catalog_sha256']==hashlib.sha256((D/'catalog.json').read_bytes()).hexdigest())"
python -X utf8 -c "from pathlib import Path;p=Path('quant_backtest/data/tushare/etf_mins');print(len(list(p.glob('*.parquet'))))"
Get-Content C:\Temp\kg_sync_task.log -Tail 3
```
