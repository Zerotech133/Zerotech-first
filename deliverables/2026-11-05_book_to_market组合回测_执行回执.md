# 中性化 book_to_market 多空组合回测 执行回执

- 任务：m05683
- 日期：2026-11-05
- 状态：**完成（研究用途，不接生产）**
- 关联：B3 全市场评测 37f5d0424、Value 中性化 cee8cfcca

## 一、交付内容

| 文件 | 说明 |
|---|---|
| `tools/research/factor_portfolio.py` | 组合回测引擎：`PortfolioConfig`、`investable_masks`、`rebalance_indices`、`_target_weights`、`_drift_weights`、`simulate`、`summarize`；支持 ST/涨跌停/停牌/成交额/换手率过滤、单边换手上限、成本敏感性 |
| `tools/tests/test_factor_portfolio.py` | 5 项单测：调仓索引、可交易性过滤、换手上限、成本>0、首笔无未来函数 + 多空数量/首次换手 |
| `research/reports/portfolio_bm_neutral_20261105.json` | 原始结果：4 配置 + 成本敏感性 + 22 笔调仓明细 + 净值/日收益 |
| `deliverables/2026-11-05_中性化book_to_market多空组合回测.md` | 方法、结果、解释、限制、复现 |

## 二、回测配置

- 信号：中性化 `book_to_market`（行业+市值 OLS 残差，日截面 rank pct）
- 面板：484 日 × 5,662 只（2024-11-06 至 2026-11-04）
- 信号窗：2025-01-02 至 2026-09-29；442 个组合收益日；22 次调仓
- 组合：20 日调仓、头尾 20%、等权、多空各 0.5（gross=1）、T+1 收盘成交
- 成本：30bp 单边；敏感性 0/10/30/50bp
- 基准：同期股票池等权（年化 36.78%）

## 三、核心结果

| 配置 | 年化 | Sharpe | 最大回撤 | 平均单边换手 | 累计成本 |
|---|---:|---:|---:|---:|---:|
| base | 4.28% | 0.688 | -7.53% | 0.3205 | 2.12% |
| tradable | 0.60% | 0.091 | -10.73% | 0.3821 | 2.52% |
| constrained（上限 0.5） | 0.93% | 0.142 | -10.21% | 0.3649 | 2.41% |
| strong（上限 0.3） | 2.17% | 0.332 | -9.13% | 0.2978 | 1.97% |

成本敏感性（constrained）：0bp 2.33%（.355）、10bp 1.86%（.284）、30bp 0.93%（.142）、50bp 0.01%（.002）。

## 四、结论

1. 基础配置收益依赖不可交易样本（ST/涨跌停/高换手/低流动性），实盘不可用。
2. 可交易性过滤后多空年化降至 0.60%；换手上限 0.3 可提升至 2.17%/Sharpe 0.332，但仍远低于单边等权基准（36.78%，口径不可直接比较）。
3. 策略对成本极敏感，50bp 即抹平收益；20 日调仓对 Value 信号偏短、换手偏高。
4. **不接入生产选股**；建议后续做 60 日调仓、换手惩罚/buffer、行业市值组合约束、paper trading。

## 五、验证与门禁

- `pytest tools/tests/test_factor_portfolio.py -q`：**5 passed**
- 关联测试（neutralize/factor_lib/financial）：**44 passed / 37 passed**（前序）
- `tools/check_syntax.py --fail-on-warning`：**4512 文件 / 0 错误 / 0 告警 / PASS**
- 数据/结果：`research/reports/portfolio_bm_neutral_20261105.json`（约 22 笔调仓、4 配置、成本敏感性）

## 六、边界

- 未计融券费用/券源、冲击成本、涨跌停排队失败、停牌复牌跳空、退市偏差。
- 组合层面未加行业/市值暴露约束（仅信号中性化）。
- 样本仅 2025–2026 一段，20 日重叠窗口，独立样本有限。
- 研究结果不代表实盘收益；生产链路未改。

## 七、复现

```powershell
cd C:\项目\老田的AI助手
& C:\项目\py312\python.exe -X utf8 -u C:\Temp\run_bm_portfolio.py
```
