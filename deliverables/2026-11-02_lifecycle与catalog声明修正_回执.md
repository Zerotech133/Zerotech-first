# lifecycle 与 catalog 声明修正回执（task-30）

> 任务：`task-30`（S3-01 B 组 C 类修复·仓库侧声明）｜执行：wa-govern ｜完成：**2026-11-02 08:58（Asia/Shanghai）**
> 边界：**只改 2 个配置文件 + 本回执**；未运行 `build_catalog` / `registry_rebuild` / `qualify_probe --apply`；未 commit、未重启、未改数据。
> 依据：`docs/audit/2026-11-02_manifest缺失4表诊断.md` §2.2（crypto_daily）/§2.3（ts_global_fut）/§2.4（us_legacy）。

## 一、改动清单（2 文件，均未提交，工作区 M）

### 1) `agent_platform/catalogs/data_lifecycle.yaml`（+19/−3，version 1→2，updated 2026-11-02）
| 条目 | lifecycle | 说明 |
|---|---|---|
| `us_legacy`（新增） | **deprecated** | us_daily 已 verified 收口（`data/tushare/us_daily` 24211 文件、freshness=20261030、manifest 键 done=true）；`data/us` 仅读兼容，待下游（chanlun US 扫描等）切换后停写。不删数据。 |
| `crypto_daily`（新增） | **deprecated**（语义=暂停/paused） | WO-QT-20261021-094：`crypto_sync` 默认停用、改走 zhice-db 供给层 `export_us_hk --market CRYPTO`；该任务接线未完成（`CryptoSync` 任务 Disabled），`data/crypto` 冻结于 2026-10-21（SLA #14 P0 线）。供给层接线完成后**删除本条**即恢复 active。 |

同时：头部注释补全合法值（`planned/deprecated/archived/active`，明确**无 paused/disabled 枚举**，"暂停但可能恢复"用 deprecated + reason 注明）；版本号/日期更新。**未改动既有 8 条目**（xq_* 7 条 planned、quarantine_prod_overwrite deprecated 原样）。

> 说明：任务要求"crypto_daily 标 paused/disabled"，但 `tools/datawarehouse/lifecycle.py::VALID_STATUSES` 只有 active/planned/deprecated/archived（未知值归一 active）；新增 paused 需改代码（超出"只改配置"边界）。故用 **deprecated + reason 显式注明暂停语义**，效果=registry 不计资格欠账/freshness 跳过；恢复时删条目即可。

### 2) `quant_backtest/build_catalog.py`（+7/−1，`TABLE_DEFS["ts_global_fut"]` L275-290）
- `"manifest": None` → **`"manifest": os.path.join(HERE, "data", "tdx", "_manifest.json")`**
- 补注释：该表属 tdx 仓（store=`data/tdx/ts_global_fut`，由 `WAREHOUSES` 的 xtcloud 循环扫描）；`_scan_dir` 以所属仓 `mpath` 为准（此值作非空断言/自文档）；rebuild 后 `manifest_status` 由 **not_applicable → missing**，方可由 `manifest_repair` 补表级键。
- 口径一致性：与 `_scan_dir` 的 `manifest` 语义一致（非 None 即启用仓 manifest），未引入新字段；`index_global`/`crypto_daily`/`us_legacy` 的 `None` **保持不动**（非本单范围）。

## 二、验证证据（全部只读，未触发生成/重建）

| 检查 | 命令 | 结果 |
|---|---|---|
| YAML 语法 | `yaml.safe_load(data_lifecycle.yaml)` | **OK**：version=2 / updated=2026-11-02 / **tables=10**；无 BOM（3045B） |
| lifecycle 读取器 | `python -c "import lifecycle; status_for/skipped"` | `us_legacy=deprecated(skip=True)`、`crypto_daily=deprecated(skip=True)`；`quarantine_prod_overwrite=deprecated`、`report_rc/ts_global_fut/daily=active`、`xq_comment=planned` —— 既有条目行为不变 |
| Python 语法 | `py_compile`（py312）+ py311 内存 compile | 均 **PASS**（生产链 KgDailySync 用 py311） |
| 相关单测 | `pytest quant_backtest/test_datamart.py test_disk_health_wo102.py test_datamart_integrity.py tools/datawarehouse/tests/test_lifecycle_archived.py` | **48 passed in 7.47s** |
| rebuild 后 catalog 效果预览 | 只读调用 `build_catalog._scan_dir(xtcloud, data/tdx, data/tdx/_manifest.json, "ts_global_fut", mf)`（不落盘） | `manifest=C:/项目/老田的AI助手/quant_backtest/data/tdx/_manifest.json`、**`manifest_status=missing`（原 not_applicable）**、files=3、done=True、freshness=20261015、readiness_basis=file_presence |
| 当前一致性（rebuild 前） | `python tools/datawarehouse/metadata_consistency.py --json` | code=1，**issues=2（预期内）**：`qualification_not_lifecycle:us_legacy:unknown!=deprecated`、`qualification_not_lifecycle:crypto_daily:unknown!=deprecated` —— 证明 lifecycle 已生效、**等 rebuild 把 registry 对齐后应清零** |

> 另：首版 YAML 因多行 plain scalar 正文含 `SLA #14`（空格+`#` 被当注释起始）解析失败，已改为 `>-` 折叠块标量并通过；期间已即时通知并行车道 dwgov-impl 解封（其 `manifest_repair` 可正常预览/apply）。

## 三、预期 rebuild 后效果

1. **catalog v18→v19（或按 override）**：`ts_global_fut.manifest` 由 null 变为 `.../data/tdx/_manifest.json`、`manifest_status` not_applicable→**missing**、`verification_status` 仍 pending（等 manifest_repair 补键后可转）；
2. **registry（rebuild --apply）**：`us_legacy`、`crypto_daily` 的 `qualification_status` 由 `unknown` → **deprecated**，`unsupported_reason` 取 lifecycle reason，**不再计入 unknown 资格欠账**（`registry_rebuild.py` L176-187 生命周期覆盖逻辑）；`ts_global_fut` 仍是 unknown（等取证）；
3. **metadata_consistency**：上述 2 条 `qualification_not_lifecycle` issues 应清零 → PASS；
4. **freshness/资格巡检**：两 deprecated 表按设计跳过新鲜度告警（`lifecycle.skipped=True`）；
5. **注意**：本单**不**修 `qualify_probe` 的 manifest 误判（crypto_daily/us_legacy 仍会因 not_applicable 回退 tushare 报 manifest_missing）——需批2 代码单（`resolve_manifest_path` 增 not_applicable 分支）+ 单测；`ts_global_fut` rebuild 后可进入 `manifest_repair` plan（补表级键）。

## 四、待 Lead 执行的 rebuild 命令（本单未执行，按序串行）

```powershell
cd C:\项目\老田的AI助手
# 0) 前置（AGENTS §3）：确认无并行数据根操作
Get-Content C:\Temp\kg_sync_task.log -Tail 10

# 1) 重建 catalog（建议显式版本号；不设 override 时因表集合未变 version 仍=18，只更新 updated）
$env:CATALOG_VERSION_OVERRIDE = "19"
& 'C:\Program Files\Python311\python.exe' -X utf8 'C:\aios\quant_backtest\build_catalog.py'
Remove-Item Env:CATALOG_VERSION_OVERRIDE
# 预期：catalog.json version=19；ts_global_fut.manifest 指向 tdx/_manifest.json、manifest_status=missing
#（也可等次日 06:20 KgDailySync 链自动重建，但本单要求串行受控，建议手动）

# 2) 重建 registry（dry-run 看 diff → apply；工具自动备份 .bak-<ts>+原子写+补记 kg 日志）
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\registry_rebuild.py' --dry-run --json
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\registry_rebuild.py' --apply

# 3) 一致性门禁（预期 issues=[] / code=0）
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\metadata_consistency.py'
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\metadata_consistency.py' --json

# 4) 验收读点（只读）
& 'C:\项目\py312\python.exe' -X utf8 -c "import json,pathlib;c=json.loads(pathlib.Path(r'quant_backtest/data/catalog.json').read_text(encoding='utf-8'));e=c['tables']['ts_global_fut'];print(c['version'],e['manifest'],e['manifest_status'])"
& 'C:\项目\py312\python.exe' -X utf8 -c "import json,pathlib;w=json.loads(pathlib.Path(r'quant_backtest/data/warehouse_registry.json').read_text(encoding='utf-8'));m={a['asset_id']:a.get('qualification_status') for a in w.get('physical_tables') or []};print(m.get('us_legacy'),m.get('crypto_daily'),m.get('ts_global_fut'))"
# 预期：19 .../tdx/_manifest.json missing ｜ deprecated deprecated unknown

# 5)（后续独立单）manifest_repair 补 ts_global_fut 表级键 → qualify_probe 复判
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\manifest_repair.py' --json
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\manifest_repair.py' --apply
& 'C:\项目\py312\python.exe' -X utf8 'tools\datawarehouse\qualify_probe.py' --tables ts_global_fut --dry-run --json
```

**执行后必做**：在 `C:\Temp\kg_sync_task.log` 补记（catalog_build / registry_rebuild 的 START/END，AGENTS §3）；若覆盖重启相关无——本单不重启任何服务。

## 五、风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| lifecycle 标 deprecated | registry 不计两表 unknown（预期收益）；freshness 跳过两表告警 | 数据与文件未动；crypto 恢复接线后**删除 crypto_daily 条目**即恢复 active；us 若需重启旧线同理 |
| catalog 声明 ts_global_fut manifest | rebuild 后 manifest_status=missing（仍待 manifest_repair 补键），qualify 仍 partial 直到补登 | 已给出第 5 步命令；不补也不影响数据读取 |
| build_catalog.py 代码改动 | 依赖 rebuild 才生效；若 rebuild 失败旧 catalog 不变（原子写） | 工具原子写 + `.bak`；回滚=git checkout |
| YAML 长中文 reason | 已踩一次（` #` 截断），后续编辑需用 `>-` | 已在回执备注；`yaml.safe_load` + lifecycle 读取器双验证 |
| **未 commit** | 工作区改动可能被他人覆盖/带走 | 回滚/入库由 Lead 在评审门处理；本单无数据/任务/服务副作用 |

**回滚（零数据风险）**：`git checkout -- agent_platform/catalogs/data_lifecycle.yaml quant_backtest/build_catalog.py` + 删除本回执；registry/catalog 未重建则无需回滚。

## 六、证据索引

- 改动：`agent_platform/catalogs/data_lifecycle.yaml`（+19/−3，10 条目）、`quant_backtest/build_catalog.py`（+7/−1，L275-290）
- 依据：`docs/audit/2026-11-02_manifest缺失4表诊断.md` §2.2/§2.3/§2.4；`tools/datawarehouse/lifecycle.py`（VALID_STATUSES/SKIP_STATUSES）；`tools/datawarehouse/registry_rebuild.py` L176-187（生命周期覆盖）
- 验证输出：YAML OK（version=2/tables=10）、lifecycle reader 5 项状态、pytest 48 passed、`_scan_dir` 预览（manifest_status=missing）、metadata_consistency issues=2（预期待 rebuild 清零）

*签名：wa-govern 2026-11-02 08:58｜只改配置，未跑 rebuild，rebuild 由 Lead 按 §四串行执行。*
