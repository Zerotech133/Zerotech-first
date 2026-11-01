# 真实因子面板验证 v1（low_turnover / momentum20）

- 工单：`WO-VAL-FACTOR-20261101`（task-32）
- 生成时间：2026-11-01T14:25:07+08:00
- 数据：`C:\项目\老田的AI助手\quant_backtest\data\tushare`（daily 最后文件 d_20261030.parquet）
- 窗口：20230101 ~ 20260930，周频，forward_return = 20 日
- 信号日：192 个（20230106 ~ 20260930）
- 股票池：factor_lib.tradable（T 日价格≥2 元、成交额≥5000 万、上市≥250 天）
- 过滤：bak_basic T 日 name 含 ST/退；tradable + T/T+1/T+21 复权价完整；pct_chg/OHLC 推导（主板10%/ST5%/创业板·科创板20%/北交所30%）
- 口径：调用 master 已合入 `factor_diagnostics.py` + `factor_gate.py`（研究诊断，不接生产）

## 结果

| 因子 | 行数 | 周数 | 覆盖均值 | rankIC 均值 | ICIR | IC 正比例 | 多空差 | 多空 IR | 换手 | gate |
|---|---|---|---|---|---|---|---|---|---|---|
| low_turnover | 644322 | 190 | 3391.2 | 0.0759 | 0.331263 | 0.6789 | 0.0025 | 0.030002 | 0.2986 | **warn** |
| mom20 | 643573 | 190 | 3387.2 | -0.0718 | -0.439701 | 0.3263 | -0.0114 | -0.180232 | 0.4551 | **fail** |

## 分位收益（Q1=因子最低 … Q5=最高）

- **low_turnover**：Q1=0.003277, Q2=0.011412, Q3=0.012, Q4=0.007867, Q5=0.00577（单调 up=False down=False，qSpearman=0.1）
- **mom20**：Q1=0.011945, Q2=0.011173, Q3=0.009214, Q4=0.0075, Q5=0.0005（单调 up=False down=True，qSpearman=-1.0）

## gate 检查明细

### low_turnover → **warn**

- PASS `bad_rows` value=0.0 threshold=0.2
- PASS `coverage` value=3391.17 threshold=500.0
- PASS `min_days` value=190.0 threshold=100.0
- PASS `rank_ic_mean` value=0.075939 threshold=0.02
- PASS `icir` value=0.331263 threshold=0.2
- PASS `positive_rate` value=0.6789 threshold=0.55
- WARN `quantile_spearman` value=0.1 threshold=0.5
- WARN `long_short_ir` value=0.030002 threshold=0.2
- PASS `turnover` value=0.2986 threshold=0.8
- WARN `quantile_monotonic` value=None threshold=None
- warnings: quantile_spearman, long_short_ir, quantile_monotonic

### mom20 → **fail**

- PASS `bad_rows` value=0.0 threshold=0.2
- PASS `coverage` value=3387.23 threshold=500.0
- PASS `min_days` value=190.0 threshold=100.0
- FAIL `rank_ic_mean` value=-0.071829 threshold=0.02
- FAIL `icir` value=-0.439701 threshold=0.2
- WARN `positive_rate` value=0.3263 threshold=0.55
- WARN `quantile_spearman` value=-1.0 threshold=0.5
- WARN `long_short_ir` value=-0.180232 threshold=0.2
- PASS `turnover` value=0.4551 threshold=0.8
- warnings: positive_rate, quantile_spearman, long_short_ir

## 数据 / 样本

- **low_turnover**：rows=644322 weeks=190 symbols=5411；candidates=1090600，剔除 股票池/停牌缺价=415306/ST=7562/信号日涨跌停=13221/次日涨停=9161/缺数据=1028；limit_check={"available": true, "checked_rows": 1324, "actual_limit_up_rate": 0.0, "note": "limit_list_d 仅 2025-09 起有；主过滤用 pct_chg/OHLC 推导，此处给实际涨停占比作数据质量参考（非逐行一致性）"}
- **mom20**：rows=643573 weeks=190 symbols=5411；candidates=1090600，剔除 股票池/停牌缺价=415306/ST=7562/信号日涨跌停=13221/次日涨停=9161/缺数据=1777；limit_check={"available": true, "checked_rows": 1321, "actual_limit_up_rate": 0.0, "note": "limit_list_d 仅 2025-09 起有；主过滤用 pct_chg/OHLC 推导，此处给实际涨停占比作数据质量参考（非逐行一致性）"}

## 局限

- 复权收益用 local adj_factor（未做分红再投资单独校验）；数据 as-of 以本地仓为准。
- 涨跌停无法用 limit_list_d 覆盖全期（本地 2025-09 起）→ 主过滤用 pct_chg/OHLC 推导，存在边界误差；T+1 涨停按推导矩阵剔除。
- 周频信号 = 每周最后交易日；forward 尾部因 20 日持有期截断（last_signal 早于 end）。
- factor_diagnostics IC 为周频截面、forward 20 日窗口→相邻周重叠 4/5，ICIR/t 仅描述性，不做独立样本显著性推断（需 block bootstrap）。
- 单因子排序诊断，不含成本/滑点/容量/换手成本；组合级净收益见既有 factor_validate/lowturn_freq_validate 报告（本单只做诊断口径）。
- 阈值（报告已回显）为验证研究口径，非生产预注册阈值；pass 不构成上线结论。
- not 复权/ST/停牌口径与生产选股引擎不完全一致：本单不改生产，仅为研究验证。

## 复现

```powershell
python tools/research/factor_panel_validate.py --start 20230101 --end 20260930 --horizon 20 --factors low_turnover,mom20 --data-root "C:\项目\老田的AI助手\quant_backtest\data\tushare" --out research/reports/val_factor_20261101.json --md research/reports/val_factor_20261101.md
```
