# 数据仓质量 W3-1 遗留修复（修复）

- 生成：2026-11-03T07:04:36+08:00；数据根：`C:\项目\老田的AI助手\quant_backtest\data`
- 背景：task-75 历史抽检 8 项中 1 FAIL / 2 ATTENTION（C6/C7/C8）

## 一、修复前（复现）

| 项 | 现象 | 证据 |
|---|---|---|
| C7 manifest | 历史 +5,559 已于 11-02 修复；本次 manifest=footer=4253717（Δ=0） | parquet footer 全量求和 |
| C6 重复 | ts_forecast dups=891；ts_report_rc dups=166 | duckdb DISTINCT 全列 |
| C8 口径 | catalog ann_date=20261013 vs footer end_date=20271231 | 口径差异 |

## 二、修复动作

- manifest 行数刷新：{"src": "tdx", "changed": [{"table": "ts_report_rc", "before_rows": 528027, "after_rows": 529987, "before_files": 14, "after_files": 14}], "dry": false, "backup": "C:\\项目\\老田的AI助手\\quant_backtest\\data\\_repair_backup_w3\\20261103-070426\\tdx___manifest.json"}
- 重复行去重：{"src": "tdx", "tables": [{"table": "ts_forecast", "total": 34342, "dups": 891, "dups_within_files": 0, "dups_cross_files": 891, "affected_files": [], "action": "deduped_global", "total_before": 34342, "total_after": 33451, "removed": 891, "affected": [{"file": "part_0001.parquet", "before": 891, "after": 0}], "backup": {"dir": "C:\\项目\\老田的AI助手\\quant_backtest\\data\\_repair_backup_w3\\20261103-070426", "files": [{"path": "tdx\\ts_forecast\\part_0001.parquet", "backup": "C:\\项目\\老田的AI助手\\quant_backtest\\data\\_repair_backup_w3\\20261103-070426\\tdx\\ts_forecast\\part_0001.parquet", "sha256": "ef46dbc9cbe02fea90fc51d2472db93c70536ebb6b23ce8deeb80b4f20600a64", "bytes": 357934}]}}, {"table": "ts_report_rc", "total": 530153, "dups": 166, "dups_within_files": 0, "dups_cross_files": 166, "affect
- 注：首次 apply 写回条件 bug 导致 4 个 report_rc 分片临时 0 行，已从备份恢复并修正条件；二次 apply 后全部行数校验通过（无净损失）
- 备份/回滚：备份目录 quant_backtest/data/_repair_backup_w3/<tag>/（manifest 原件+受影响 parquet 原件，含 sha256）；恢复=按相对路径 copy 回原位并重跑 --check

## 三、修复后复检（8 项）

- C6 重复：**PASS**（ts_forecast/ts_report_rc dups=0）
- C7 manifest：**PASS**（delta=0）
- C8 freshness：**PASS（口径说明）**：catalog freshness 按披露日 ann_date（业务口径）；footer max end_date 含未来预测报告期（如 20271231）→ 口径差异，非数据缺陷；检查应以 ann_date 对齐
- C1-C5：沿用 2026-11-02 task-75 抽检 PASS 证据（历史只读数据未变）
- 工具复跑：metadata_consistency rc=0、manifest_check rc=0

## 四、结论

- 8 项复检：**8/8 PASS**（C8 为口径说明，非缺陷）
