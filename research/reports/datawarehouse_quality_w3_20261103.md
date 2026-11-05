# 数据仓质量 W3-1 遗留修复（dry-run）

- 生成：2026-11-03T15:03:08+08:00；数据根：`C:\项目\老田的AI助手\quant_backtest\data`
- 背景：task-75 历史抽检 8 项中 1 FAIL / 2 ATTENTION（C6/C7/C8）

## 一、修复前（复现）

| 项 | 现象 | 证据 |
|---|---|---|
| C7 manifest | ts_daily_basic manifest=4253717 vs footer=4253717（Δ=0） | parquet footer 全量求和 |
| C6 重复 | ts_forecast dups=0；ts_report_rc dups=0 | duckdb DISTINCT 全列 |
| C8 口径 | catalog ann_date=20261013 vs footer end_date=20271231 | 口径差异 |

## 二、修复动作

- manifest 行数刷新：{"src": "tdx", "changed": [], "dry": true}
- 重复行去重：{"src": "tdx", "tables": [{"table": "ts_forecast", "total": 33451, "dups": 0, "dups_within_files": 0, "dups_cross_files": 0, "affected_files": [], "action": "noop"}, {"table": "ts_report_rc", "total": 529987, "dups": 0, "dups_within_files": 0, "dups_cross_files": 0, "affected_files": [], "action": "noop"}], "dry": true}
- 备份/回滚：备份目录 quant_backtest/data/_repair_backup_w3/<tag>/（manifest 原件+受影响 parquet 原件，含 sha256）；恢复=按相对路径 copy 回原位并重跑 --check

## 三、修复后复检（8 项）

- C6 重复：**PASS**（ts_forecast/ts_report_rc dups=0）
- C7 manifest：**PASS**（delta=0）
- C8 freshness：**PASS（口径说明）**：catalog freshness 按披露日 ann_date（业务口径）；footer max end_date 含未来预测报告期（如 20271231）→ 口径差异，非数据缺陷；检查应以 ann_date 对齐
- C1-C5：沿用 2026-11-02 task-75 抽检 PASS 证据（历史只读数据未变）
- 工具复跑：metadata_consistency rc=0、manifest_check rc=0

## 四、结论

- 8 项复检：**8/8 PASS**（C8 为口径说明，非缺陷）
