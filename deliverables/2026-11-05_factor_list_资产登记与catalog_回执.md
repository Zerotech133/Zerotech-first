# Tushare factor_list 数据资产登记与 catalog 接入回执

- 日期：2026-11-05
- 任务来源：用户「把因子清单登记为数据资产，并加进 catalog」
- 执行：Data / 代码审计线（Lead 直做）
- 结论：**已完成**——catalog v40 新增 `factor_list`（257 表），registry 资产登记 physical 305/display 258，数据册 v8 新增 `tushare_factor_list`，metadata 一致性 PASS，相关测试 33 passed。

## 1. 数据资产

| 项 | 值 |
|---|---|
| 资产 ID | `factor_list`（数据册资源 ID `tushare_factor_list`） |
| 来源 | Tushare `factor_list`（真实 API `code=0`） |
| 内容 | 272 因子 / 9 类：Alpha101 101、Growth 15、Liquidity 35、Momentum 20、Quality 59、Reversal 3、Risk 25、Size 3、Value 11；asset_type 全 STK |
| 字段 | factor_name / asset_type / factor_type / factor_desc |
| 存储 | `quant_backtest/data/factor_library/tushare_factor_list/`（latest.json + latest.parquet + snapshots/ + manifest.json） |
| 快照 | `factor_list_20261105.json` 101,008 B；parquet 25,360 B；manifest 1,005 B |
| sha256 | `80892d701a0ff737c9817c2ca8877cc037beb325e8fbcbc3fb909f38315a7f6f` |
| fetched_at | 2026-11-05 06:59:32（token_sha8 `0fd89b25`） |
| 类型 | 静态元数据字典，非时序；`static_no_date=true`，`date_status=not_applicable`，`primary_key=["factor_name"]`，`freq="M"` |
| 采集工具 | `tools/data_sources/tushare_factor_list.py`（fetch/check/status/show；18 tests，前次已提交 `612508198`） |

## 2. catalog 登记（v39/256 → v40/257）

- 构建代码：`quant_backtest/build_catalog.py`
  - 新增 `FACTOR_LIBRARY_DIR` / `FACTOR_LIST_DIR` 常量与 `_scan_factor_library(root=None)` 扫描器；
  - 扫描 manifest + latest/snapshots parquet schema，生成静态条目：
    - `source=factor_library`、`layout=parquet_snapshot`、`date_col/code_col=null`、`date_status=not_applicable`、`static_no_date=true`
    - `rows=272`、`files=3`、`done=true`、`manifest_status=closed`、`verification_status=static_no_date`
    - `primary_key=["factor_name"]`、`freq="M"`、`by_type/by_asset/sha256/fetched_at/token_sha8`
  - `build()` 仅在 `data_root is None`（生产 data 根）时并入；显式 data_root 的测试/沙箱不受影响；
  - `catalog_changelog.jsonl` 追加 version 40 / `added: ["factor_list"]`。
- 构建结果：
  - `catalog_version=40`、`tables=257`、`updated=2026-11-05T07:09:42`
  - catalog sha256 `b0625a590865d86d2752b777f6d98b488882aec2bed8b976160a7617b937f374`
  - 条目 `factor_list`：rows=272、files=3、done=true、manifest closed、9 类计数齐全。
- 备份：`research/reports/backups/catalog_before_factor_list_20261105-070707.json`

## 3. registry 资产登记（physical 304 → 305）

- `registry_align.py --apply` 重建后：
  - `physical_count=305`、`display_count=258`；
  - `factor_list` entry：
    ```json
    {"asset_id":"factor_list","physical_tables":["factor_list"],"channel":"factor_library",
     "frequency":"M","primary_key":["factor_name"],"qualification_status":"unknown"}
    ```
  - registry `catalog_sha256` 与 catalog 一致。
- 备份：`research/reports/backups/warehouse_registry_before_factor_list_20261105-070707.json`

### registry_align BUG 修复（登记过程中暴露）

- 现象：`registry_align --apply` 在 registry 存在 catalog 已不存在的孤儿条目时必然回滚（06:42 知识治理日志 `registry_align exit=1`）。
- 根因：apply 后校验 `expected_physical = len(catalog2["tables"])`，但 `registry_rebuild` 按设计保守保留孤儿条目，`physical_count` 允许大于 catalog 表数；该期望值永远不等。
- 修复：`expected_physical = len(registry2.get("physical_tables") or [])`，自洽性继续由 `verify_written(physical_count_match)` 保证；孤儿保留行为不变。
- 回归测试：`tools/datawarehouse/tests/test_registry_align.py::test_apply_succeeds_with_retained_orphan_entries`（含孤儿 registry 应用成功、孤儿保留、metadata code=0 + count warning）。
- 影响面：修复后后续每日 registry 对齐不再因孤儿误回滚；孤儿条目仍不作物理删除。

## 4. 数据册登记（v7 → v8）

- 文件：`agent_platform/catalogs/data_catalog.yaml`
  - `version: 8`、`updated: 2026-11-05`、resources=28；
  - 新增 `tushare_factor_list`：`kind: parquet+json`、`uri: quant_backtest/data/factor_library/tushare_factor_list/`、`layer: 元数据层（静态字典，非时序）`、owner DATA1、steward 张松、grants `[invest, quality]`、按需 freshness/verify，fail_mode 覆盖 40203/40204 无权限。

## 5. 验证

| 验证 | 结果 |
|---|---|
| `metadata_consistency.py` | code=0、catalog_version=40、tables=257、physical_count=305、issues=[]；仅已知 warning `registry_vs_catalog_table_count_diff:305/257` |
| `registry_align.py --check` | exit 0、match_before=True、diffs=[] |
| catalog 条目自检 | factor_list rows=272/files=3/done=True/static_no_date/manifest closed |
| 定向测试 | `test_build_catalog_factor_library` + `test_registry_align` + `test_data_catalog` + `test_metadata_consistency` + `test_registry_rebuild_review` → **33 passed** |
| 语法门 | `tools/check_syntax.py --fail-on-warning` → **4487 文件 / 0 错误 / 0 告警 / PASS** |

## 6. 回滚

- 恢复数据文件（gitignored，不在版本库）：
  ```powershell
  Copy-Item research\reports\backups\catalog_before_factor_list_20261105-070707.json quant_backtest\data\catalog.json
  Copy-Item research\reports\backups\warehouse_registry_before_factor_list_20261105-070707.json quant_backtest\data\warehouse_registry.json
  ```
- 回退代码/数据册：`git revert <本次commit>`（build_catalog 扫描器、registry_align 修复、data_catalog 条目、回执）。
- 数据快照本身不删（`quant_backtest/data/factor_library/`），仅解除登记不影响文件。

## 7. 已知边界与后续

1. `catalog.json` / `warehouse_registry.json` 为 gitignore 数据文件，**生产以重建/复检为准**；新克隆环境需运行一次 `build_catalog` + `registry_align --apply`。
2. `qualification_status` 暂为 `unknown`（尚未做血缘/口径签核）；如需对外发布再补 qualification。
3. `factor_list` 为手动/按需刷新（非每日时序），Tushare 因子库变更或 token 权限变化时重跑 fetch；无权限时工具退出码 2。
4. `metadata_consistency` 的 305/257 计数差异是“registry 保留孤儿 + 多物理表资产”的已知口径，非错误；本轮未扩大差异。

## 8. 产物清单

- 代码：`quant_backtest/build_catalog.py`、`tools/datawarehouse/registry_align.py`
- 测试：`tools/tests/test_build_catalog_factor_library.py`、`tools/datawarehouse/tests/test_registry_align.py`
- 数据册：`agent_platform/catalogs/data_catalog.yaml`
- 数据：`quant_backtest/data/catalog.json`（v40）、`warehouse_registry.json`（305/258）、`catalog_changelog.jsonl`
- 备份：`research/reports/backups/catalog_before_factor_list_20261105-070707.json`、`warehouse_registry_before_factor_list_20261105-070707.json`
- 本回执
