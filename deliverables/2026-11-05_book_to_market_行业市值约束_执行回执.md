# 中性化 book_to_market：60 日组合加行业/市值约束 执行回执

- 任务：m05804（承接 m05745）
- 日期：2026-11-05
- 状态：**完成（研究用途，不接生产）**

## 一、代码变更

`tools/research/factor_portfolio.py`：

- `PortfolioConfig` 新增 `industry_neutral: bool`、`size_neutral: bool`、`size_buckets: int=3`。
- 新增 `_target_weights_constrained()`：按行业 × 市值分组（T 日 log(total_mv) 截面分桶）分配多空权重；每组内取因子 top/bottom 20% 等权，组权重按该组有效池数量占比，多头/空头同组 gross 匹配；保留 buffer；组内不足 2 只跳过并归一化两腿。
- 新增 `_portfolio_exposures()`：记录每期行业最大净暴露与市值净暴露（权重×log 市值）。
- `simulate()`：计算 industry/log_mv，选择普通或约束目标权重；交易记录写入 `max_industry_net`、`size_net`；指标新增 `avg_max_industry_net`、`avg_abs_size_net`。
- `summarize()`：输出 `industry_neutral`、`size_neutral`、`size_buckets` 及暴露均值。

`tools/tests/test_factor_portfolio.py`：新增行业/市值约束权重中性、约束模拟暴露记录 2 项，共 **10 tests**。

## 二、回测配置

基础：60 日调仓、头尾 20% 等权、多空各 0.5、buffer 0.3、turnover penalty 0.5、单边换手上限 0.3、成本 30bp、ST/涨跌停/停牌/成交额/换手率过滤。

| 变体 | 行业约束 | 市值约束 |
|---|---|---|
| chosen | 否 | 否 |
| chosen_industry | 是 | 否 |
| chosen_size | 否 | 是 |
| chosen_both | 是 | 是（3 组） |

## 三、核心结果

| 配置 | 年化 | Sharpe | 最大回撤 | 年化换手 | 成本 | 行业净暴露 | 市值净暴露 |
|---|---:|---:|---:|---:|---:|---:|---:|
| chosen | 1.85% | 0.329 | -7.69% | 1.24 | 0.65% | 0.985% | 0.1488 |
| chosen_industry | 2.51% | 0.481 | -6.82% | 1.24 | 0.65% | 0.592% | 0.1457 |
| chosen_size | 1.77% | 0.317 | -7.41% | 1.24 | 0.65% | 0.972% | 0.1057 |
| **chosen_both** | **3.15%** | **0.708** | **-4.46%** | 1.26 | 0.67% | 0.631% | 0.0632 |

成本敏感性（chosen_both）：0bp 3.54%、10bp 3.41%、30bp 3.15%、50bp 2.89%（Sharpe 0.796/0.767/0.708/0.649）。

## 四、结论

1. 行业约束是主要改善来源：年化 +0.66pp、Sharpe +0.15、回撤 -0.87pp；市值约束单独几乎不改变收益。
2. 两者同时施加：年化 **3.15%**、Sharpe **0.708**、最大回撤 **-4.46%**、波动 4.45%；行业净暴露 0.631%、市值净暴露 0.063。
3. 换手/成本未上升（1.26x / 0.67%），改善来自行业/市值分布均衡而非更频繁交易。
4. 50bp 成本下仍 2.89%，成本稳健性明显优于 20 日版本。
5. 仍不接生产：需 PIT 行业分类（当前用 `stock_basic.industry` 当前快照，存在前视）、更长样本、融券/冲击成本、严格中性投影、paper trading。

## 五、验证与产物

- `pytest tools/tests/test_factor_portfolio.py -q`：**10 passed**
- `pytest tools/tests/test_factor_portfolio.py tools/tests/test_factor_neutralize.py -q`：**17 passed**
- `tools/check_syntax.py --fail-on-warning`：**4516 文件 / 0 错误 / 0 告警 / PASS**
- 原始结果：`research/reports/portfolio_bm_constraints_20261105.json`
- 报告：`deliverables/2026-11-05_book_to_market_行业市值约束_60日.md`
- 运行脚本：`C:\Temp\run_bm_portfolio_constraints.py`

## 六、边界

- 行业分类非 PIT；市值组内等权，未做连续暴露优化。
- 未计融券费用/券源、冲击成本、涨跌停排队失败、停复牌跳空、退市偏差。
- 基准为等权多头，与市场中性多空不可直接比较；样本 442 日、8 次调仓，独立样本有限。
- 研究结果不代表实盘收益；生产选股链路未改。

## 七、复现

```powershell
cd C:\项目\老田的AI助手
& C:\项目\py312\python.exe -X utf8 -u C:\Temp\run_bm_portfolio_constraints.py
```
