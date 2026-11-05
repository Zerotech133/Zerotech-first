# W2 因子每日只读快照（2026-11-04）

- **as_of**：`20261103`（最新可用交易日；来源 最新 daily 文件 d_20261103.parquet（CN tradable））
- **universe**：`CN tradable`，`universe_count=3859`
- 因子数：**8**；status ok=6，partial=2，unavailable=0
- 只读：`read_only=true`；不选股、不写 DB、不改生产配置、不进策略市场；生成 2026-11-04T07:22:11+08:00

## 1. 因子覆盖与数据源

| 因子 | status | coverage | sample_size | 数据源 | 缺失原因 |
|---|---|---|---|---|---|
| ma_convergence | partial | 0.9997 | 3858/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | 1 只股票在 ma_convergence 上缺值（停牌/新股/历史窗口不足/数据缺失） |
| ma_bull_alignment | partial | 0.9995 | 3857/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | 2 只股票在 ma_bull_alignment 上缺值（停牌/新股/历史窗口不足/数据缺失） |
| volume_breakout_strength | ok | 1.0 | 3859/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | — |
| macd_momentum | ok | 1.0 | 3859/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | — |
| platform_tightness | ok | 1.0 | 3859/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | — |
| post_limitup_strength | ok | 1.0 | 3859/3859 | data/tushare/daily（日线 close/vol/pct_chg/amount） | — |
| chip_concentration | ok | 1.0 | 3859/3859 | data/tushare_curated/cyq_perf.parquet（合并版成本分位）；data/tushare/cyq_perf/d_YYYYMMDD.parquet（逐日版）；data/tdx/ts_cyq_perf/part_*.parquet（TDX 备份） | — |
| moneyflow_strength | ok | 1.0 | 3859/3859 | data/tushare/moneyflow/d_YYYYMMDD.parquet（net_mf_amount 万元） | — |

## 2. 当日分布（仅统计 universe 内非空样本；不输出选股清单）

| 因子 | mean | p10 | p50 | p90 | min | max |
|---|---|---|---|---|---|---|
| ma_convergence | 0.857607 | 0.708061 | 0.886145 | 0.965822 | 0.247532 | 0.995987 |
| ma_bull_alignment | -0.042865 | -0.109006 | -0.040694 | 0.017152 | -0.284804 | 0.302031 |
| volume_breakout_strength | 0.002734 | 0.0 | 0.0 | 0.0 | 0.0 | 0.639401 |
| macd_momentum | -0.006065 | -0.021672 | -0.003944 | 0.007468 | -0.101765 | 0.046864 |
| platform_tightness | 0.771175 | 0.591192 | 0.799588 | 0.913868 | -0.020866 | 0.972934 |
| post_limitup_strength | -0.011068 | -0.023828 | 0.0 | 0.0 | -0.406494 | 0.128883 |
| chip_concentration | 0.656689 | 0.415235 | 0.704545 | 0.833333 | -1.684211 | 0.935484 |
| moneyflow_strength | -0.030244 | -0.155531 | -0.035212 | 0.106877 | -1.0 | 0.41313 |

## 3. 数据缺口与说明

- **ma_convergence** `partial`：1 只股票在 ma_convergence 上缺值（停牌/新股/历史窗口不足/数据缺失）
- **ma_bull_alignment** `partial`：2 只股票在 ma_bull_alignment 上缺值（停牌/新股/历史窗口不足/数据缺失）
- 快照只覆盖 CN tradable 股票池在 as_of 日；覆盖率为该因子非空样本 / universe_count。
- chip_concentration 使用 T 日 cyq_perf 成本分位；`cyq_chips` 无 ts_code（仅 3 列），无法做全市场横截面，未作为该因子数据源（如实记录为不可用数据源）。
- 因子定义复用 W2-1/2/3/4a/4b 脚本口径（见各因子 formula）；PIT 仅用 T 日及以前数据，不做未来引用/ffill；缺值即缺，不填充。
- 本报告不是选股结果、不是交易信号；后续使用须经因子研究/严格审计与策略化流程。

## 4. 复现

```powershell
$env:PYTHONPATH="<code>/src"; .\<venv>\python.exe scripts/w2_factor_daily.py
```

数据源：daily=C:\项目\老田的AI助手\quant_backtest\data\tushare\daily；moneyflow=data/tushare/moneyflow/d_20261103.parquet；chip latest=20261103。

生成时间：2026-11-04T07:22:11+08:00；JSON：`C:\Temp\w2_factor_daily.json`。
