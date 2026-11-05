# 中性化 book_to_market：60 日调仓 + buffer + 换手惩罚 执行回执

- 任务：m05745（承接 m05683）
- 日期：2026-11-05
- 状态：**完成（研究用途，不接生产）**

## 一、代码变更

`tools/research/factor_portfolio.py`：

- `PortfolioConfig` 新增 `buffer_quantile: float | None`、`turnover_penalty: float`。
- `_target_weights(..., prior=...)`：支持 buffer——已有持仓只要仍在扩展分位内（多头 `rank >= 1-buffer`、空头 `rank <= buffer`）就保留；新开仓仍限最极端 `quantile`。
- `_apply_turnover_penalty()`：目标权重向当前持仓收缩 `prior + (1-penalty)*(target-prior)`，再按多空两腿归一化回 gross。
- `simulate()`：目标权重计算改为先求 prior、再调 `_target_weights(prior)`、再施加 penalty，最后执行单边换手上限；交易记录新增 `gross` 字段。
- `summarize()`：输出 `buffer_quantile`、`turnover_penalty`。

`tools/tests/test_factor_portfolio.py`：新增 buffer 保留/新开仓限制、penalty 降换手且 gross=1、60 日调仓索引/模拟 3 项，共 **8 tests**。

## 二、回测配置

| 配置 | 调仓 | 上限 | buffer | penalty |
|---|---:|---:|---:|---:|
| 20d_base | 20 | 0.5 | - | 0 |
| 20d_buffer_penalty | 20 | 0.5 | 0.3 | 0.5 |
| **60d_base** | 60 | 0.5 | - | 0 |
| 60d_buffer | 60 | 0.5 | 0.3 | 0 |
| 60d_buffer_penalty | 60 | 0.5 | 0.3 | 0.5 |
| 60d_buffer_penalty_strong | 60 | 0.3 | 0.3 | 0.5 |

其余：多头/空头 20% 等权、gross=1、T+1 收盘成交、单边 30bp、ST/涨跌停/停牌/成交额/换手率过滤。

## 三、核心结果

| 配置 | 年化 | Sharpe | 最大回撤 | 年化单边换手 | 累计成本 | 调仓次数 |
|---|---:|---:|---:|---:|---:|---:|
| 20d_base | 0.93% | 0.142 | -10.21% | 4.58 | 2.41% | 22 |
| 20d_buffer_penalty | 0.58% | 0.096 | -9.59% | 2.50 | 1.31% | 22 |
| **60d_base** | **2.58%** | **0.369** | -8.50% | 2.16 | 1.14% | 8 |
| 60d_buffer | 1.41% | 0.217 | -9.29% | 2.05 | 1.08% | 8 |
| 60d_buffer_penalty | 1.56% | 0.281 | -7.72% | 1.42 | 0.75% | 8 |
| 60d_buffer_penalty_strong | 1.85% | 0.329 | -7.69% | 1.24 | 0.65% | 8 |

成本敏感性（60d_buffer_penalty）：0bp 2.00%、10bp 1.85%、30bp 1.56%、50bp 1.27%。

## 四、结论

1. 60 日调仓显著优于 20 日：年化 0.93%→2.58%，年化换手 4.58→2.16，成本 2.41%→1.14%。
2. buffer 单独使用伤害收益（1.41%），保留信号衰减持仓的代价大于节省的换手。
3. buffer+penalty 改善风险与成本（波动 5.57%、回撤 -7.72%、换手 1.42x、成本 0.75%），但收益 1.56% 仍低于 60d_base；加上限 0.3 后年化 1.85%/Sharpe 0.329、换手 1.24x。
4. 若只选一个：60d_base 收益/Sharpe 最优；若重视换手/成本/回撤：60d_buffer_penalty_strong。
5. 成本 50bp 下 60d_buffer_penalty 仍有 1.27%，明显比 20 日版本稳健。
6. 不接生产；需更长样本、融券成本、组合层行业市值约束后再评估。

## 五、验证与产物

- `pytest tools/tests/test_factor_portfolio.py tools/tests/test_factor_neutralize.py -q`：**15 passed**
- `tools/check_syntax.py --fail-on-warning`：**4516 文件 / 0 错误 / 0 告警 / PASS**
- 原始结果：`research/reports/portfolio_bm_60d_20261105.json`（6 配置 + 成本敏感性 + trades/nav）
- 报告：`deliverables/2026-11-05_book_to_market组合_20日vs60日_换手惩罚buffer.md`
- 运行脚本：`C:\Temp\run_bm_portfolio_60d.py`

## 六、边界

- 未计融券费用/券源、冲击成本、涨跌停排队失败、停复牌跳空、退市偏差。
- 组合层未加行业/市值暴露约束；基准为等权多头，与市场中性多空不可直接比较。
- 样本 442 日、8 次 60 日调仓，独立样本有限；仅研究，不代表实盘。

## 七、复现

```powershell
cd C:\项目\老田的AI助手
& C:\项目\py312\python.exe -X utf8 -u C:\Temp\run_bm_portfolio_60d.py
```
