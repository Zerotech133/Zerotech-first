# W2-4b 资金流强度因子初筛

- 日期：2026-11-04T02:46:27+08:00
- 范围：W2 日线因子，资金流强度（净流入/成交额，5/20 日平滑）
- **结论：不通过**（仅因子初筛，不生成选股/不进策略市场）
- JSON 证据：`research/reports/W2_moneyflow_20261104.json`
- 脚本：`code/scripts/research_w2_moneyflow.py`
- 数据操作：只读 parquet；未写 DB、未改生产配置、未生成选股清单

## 一、数据探查（真实字段/日期/代码格式）

- 数据根：`C:\项目\老田的AI助手\quant_backtest\data`
- 选用：`tdx/ts_moneyflow`（按任务点名读取 data/tdx/ts_moneyflow（合并分区，字段/代码格式与 tushare/moneyflow 一致）；tushare/moneyflow 作为同日分区备源；若 tdx 目录缺失才回退 tushare）

| 来源 | 格式 | 文件数 | 行数/最新 | 日期范围 | 代码示例 | 字段 |
|---|---|---:|---|---|---|---|
| data/tdx/ts_moneyflow | part_*.parquet（合并分区） | 54 | 4066845 | 20230731~20261103 | 000001.SZ, 000002.SZ, 000004.SZ, 000005.SZ, 000006.SZ, 000007.SZ, 000008.SZ, 000009.SZ | ts_code, trade_date, buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount, buy_md_vol, buy_md_amount… |
| data/tushare/moneyflow | d_YYYYMMDD.parquet（每日一个文件） | 927 | 5578 | 20230103~20261103 | 000001.SZ, 000002.SZ, 000006.SZ, 000007.SZ, 000008.SZ, 000009.SZ, 000010.SZ, 000011.SZ | ts_code, trade_date, buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount, buy_md_vol, buy_md_amount… |
| data/tushare_staging/tail_fill/ts_moneyflow | ts_moneyflow_YYYYMMDD.parquet（最近尾部补数） | 10 | None | 20261019~20261030 |  |  |
- 完整字段（tdx/ts_moneyflow）：`ts_code, trade_date, buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount, buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount, buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount, buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount, net_mf_vol, net_mf_amount, _fetched_at`

- 单位校验（20261103）：daily.amount 千元；moneyflow buy_total/net_mf_amount 万元；`amount_wan / buy_total` 中位=1.0；amount/10（万元）与 moneyflow buy_total 同量纲约 1:1
- 代码格式：`ts_code` 形如 `000001.SZ`/`920xxx.BJ`；`trade_date` 为 `YYYYMMDD` 字符串。

## 二、因子定义与 PIT

```
资金流强度(日) = net_mf_amount / (daily.amount / 10)
  net_mf_amount 单位万元；daily.amount 单位千元；/10 后同为万元。
5日/20日 = 日度比值的 trailing 5/20 日简单均值（min_periods=3/10）。
先验方向：+1（净流入强度越高，未来20日CAR越高）。
PIT：T 日因子只用 T 及以前 moneyflow/amount；T 收盘信号，T+1 收盘入场，T+21 收盘卖出。
```

PIT 不变性检查（用 ≤T 数据重算 vs 全量因子 T 行）：

- 20251208: passed=True, max_abs_diff={'w2_mf_raw': 0.0, 'w2_mf_5d': 0.0, 'w2_mf_20d': 0.0}
- 20260716: passed=True, max_abs_diff={'w2_mf_raw': 0.0, 'w2_mf_5d': 0.0, 'w2_mf_20d': 0.0}

## 三、样本与有效窗口

- 面板：483 个交易日 × 5662 只；数据 20241106~20261103
- 有效信号窗口：20250102~20260928，422 个信号日；20 日持有，30bp 成本，CN tradable。
- moneyflow 实际：2511677 行，5630 个代码，20241106~20261103；net_mf_amount 缺失 223069 格（8.2%）。

> 5d 的 Q5-Q1 在全样本为正（+0.5691%/20日，t=2.71），但全样本 IC 为负（-0.001186, t=-0.22），且 OOS 从 -0.0067 恶化到 -0.0657；说明这是局部/长尾分组差异，不是方向稳定的正向因子，不能单凭 Q5-Q1 的单个指标通过。

## 四、IC 与 IS/OOS 三个切分

| 因子 | 全样本 IC | IC t | 2025-04-30 OOS IC/t | 2025-10-31 OOS IC/t | 2026-04-30 OOS IC/t |
|---|---:|---:|---|---|---|
| 资金流强度-原始 | 0.0014 | 0.25 | 0.0024 / 0.41 | -0.0017 / -0.21 | -0.0305 / -2.10 |
| 资金流强度-5日平滑 | -0.0012 | -0.22 | 0.0000 / 0.01 | -0.0067 / -0.77 | -0.0657 / -4.56 |
| 资金流强度-20日平滑 | -0.0126 | -2.17 | -0.0037 / -0.57 | -0.0066 / -0.69 | -0.0726 / -4.15 |

## 五、五分位（全样本，20 日 CAR 均值）

| 因子 | Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 | Q5-Q1 t | Q5-Q1 bootstrap p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 资金流强度-原始 | -2.15% | -1.80% | -1.66% | -1.66% | -1.79% | +0.36% | 1.86 | 0.029 |
| 资金流强度-5日平滑 | -2.23% | -1.79% | -1.72% | -1.65% | -1.66% | +0.57% | 2.71 | 0.063 |
| 资金流强度-20日平滑 | -2.11% | -1.88% | -1.77% | -1.53% | -1.76% | +0.34% | 1.33 | 0.216 |

## 六、是否显著与因子裁决

| 因子 | 裁决 | 全样本 IC t | OOS 正向数 | 全样本 Q5-Q1 t | 关键原因 |
|---|---|---:|---:|---:|---|
| 资金流强度-原始 | 不通过 | 0.25 | 1/3 | 1.86 | 全样本 IC=0.001366、t=0.254 未达正向显著; OOS IC 正向少于 2/3; OOS 无显著正 IC; 全样本 Q5-Q1=0.3557%、t=1.856 未达正向显著 |
| 资金流强度-5日平滑 | 不通过 | -0.22 | 1/3 | 2.71 | 全样本 IC=-0.001186、t=-0.22 未达正向显著; OOS IC 正向少于 2/3; OOS 无显著正 IC |
| 资金流强度-20日平滑 | 不通过 | -2.17 | 0/3 | 1.33 | 全样本 IC=-0.012553、t=-2.17 未达正向显著; OOS IC 正向少于 2/3; OOS 无显著正 IC; 全样本 Q5-Q1=0.3431%、t=1.328 未达正向显著 |

## 七、结论

**不通过**：全样本与 OOS 未同时满足正向显著；w2_mf_raw: IC=0.001366(t=0.254), Q5-Q1=0.3557%(t=1.856)；w2_mf_5d: IC=-0.001186(t=-0.22), Q5-Q1=0.5691%(t=2.711)；w2_mf_20d: IC=-0.012553(t=-2.17), Q5-Q1=0.3431%(t=1.328)；样本 422 个信号日/1769649 obs

判定口径（初筛，非交易策略）：
1. 全样本 IC 方向为 +，且 |t|≥2（或 bootstrap p≤0.10）；
2. 三个 OOS 切分中至少 2 个 IC 为正、至少 1 个显著为正；
3. 全样本 Q5-Q1 为正且 t≥2；
4. 数据量/覆盖率足够；否则标 `数据不足`。

**不通过不等于反向可用**：本任务不做反向包装、不做成本/容量/可交易性验证。

## 八、边界与下一步

- 只读：不写 DB、不改生产 config、不生成选股、不进入策略市场、不自动上线。
- 限制：net_mf_amount 是 Tushare moneyflow 的净流入口径；未拆分大单/中单/小单；20 日平滑与未来收益重叠，p 值仅作量级参考；未做行业/市值中性化。
- 下一步：若继续该方向，先确认资金流口径（大单净流入 vs 全部净流入）并做行业/市值中性化；在通过初筛前不得进入策略池。
