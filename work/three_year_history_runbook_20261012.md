# 三年历史数据回补运行记录（2026-10-12）

- 范围：2023-01-03 至 2026-10-09，共 910 个 SSE 开市日。
- 核心表：`ts_daily_basic`、`ts_margin_detail`。
- 分片：23 个批次，批大小 40 个交易日（最后批次按实际日期数）。
- 结果：1,820 个单元全部 `candidate_ready`；首批复用 40 个，其余通过真实 TuShare 请求落盘。
- 载荷：每个单元保存原始 JSON、Parquet、receipt、原始响应 SHA-256 与 Parquet SHA-256。
- 完整性：原始响应与 Parquet SHA 不匹配 0；重复主键 0；无效回执 0；总行数 8,484,922。
- 发布门槛：`production_write=false`、`global_activation=false`，尚未写入 `data/tushare`。
- 扩展探针：`ts_daily`、`ts_moneyflow`、`ts_index_daily`、`ts_adj_factor` 在 2023-01-03 均返回 `candidate_ready`，仅完成 1 日探针，未宣称三年完成。

常用续跑命令：

```powershell
python quant_backtest/run_three_year_history.py --start 20230103 --end 20261009 --table ts_daily_basic --table ts_margin_detail --stage-root quant_backtest/data/tushare_staging/history_3y
```

命令会复用已有合格单元；扩展表按相同入口逐表执行，完成后再做来源资格和发布回读对账。
