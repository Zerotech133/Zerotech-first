# 中性化 book_to_market：PIT 历史行业约束 60 日复评

- 任务：m05880「用 PIT 历史行业分类重跑行业约束，消除前视偏差」
- 日期：2026-11-05
- 信号：`book_to_market`（PIT 行业 + 市值 OLS 残差，日截面 rank pct）
- 面板：484 日 × 5,662 只（20241106–20261104）
- 信号窗：2025-01-02 至 2026-09-29；442 个组合收益日、8 次 60 日调仓
- 基准配置：60 日调仓、头尾 20% 等权、多空各 0.5、buffer 0.3、turnover penalty 0.5、单边换手上限 0.3、单边成本 30bp
- 原始结果：`research/reports/portfolio_bm_pit_20261105.json`

## 一、PIT 行业数据与实现

### 1.1 数据

| 数据 | 路径 | 说明 |
|---|---|---|
| SW L1 成员历史 | `quant_backtest/data/tushare/sw_l1_member/` | 31 分片、7,740 行、5,503 只；字段 `index_code,con_code,in_date,out_date,is_new` |
| SW L1 行业名 | `quant_backtest/data/tushare/sw_classify_l1/` | 42 分片；过滤 `level=L1` 后 31 个行业 |
| 申万分级成分 | `quant_backtest/data/tushare/index_member_all/` | 仅当前快照（5,922 行全 `is_new=Y`、`out_date=None`），**无 L2/L3 历史** |

Tushare API 复核：`index_member_all` 也只返回当前快照（5,922 行、`is_new=Y`、`out_date=None`）。因此本地可用的 PIT 历史行业分类只有 **SW L1（31 类）**。

### 1.2 实现

- 新增 `tools/research/pit_industry.py`：
  - `PITIndustryMap.at(date)`：取 `in_date <= date` 且（`out_date` 为空或 `out_date >= date`）的成员；同一股票多条记录按 `in_date` 降序、`is_new=Y` 优先、`index_code` 升序去重，得到该交易日唯一行业。
  - `load_sw_l1_industry()`：读两份 parquet，`index_code -> industry_name`，缺失成员填 `UNKNOWN`；返回 map 与 source 统计。
- `tools/research/factor_neutralize.py`：`neutralize_matrix(..., industry_map=None)` 逐日取 PIT 行业并做 `factor ~ 1 + log(total_mv) + industry_dummies` 残差回归，meta 记录 `industry_source="pit_sw_l1"`。
- `tools/research/factor_portfolio.py`：`simulate(..., industry_map=None)`，信号日行业用于约束权重分配，建仓日行业用于暴露统计；返回 `industry_source`。
- 测试：`tools/tests/test_pit_industry.py`（6）、`test_factor_neutralize.py`、`test_factor_portfolio.py` 共 **26 passed**；`check_syntax.py --fail-on-warning` **4,518 文件 PASS**。

### 1.3 覆盖

- 面板 484 日 × 5,662 只；PIT 行业覆盖：2024-11-06 为 5,106 只/31 行业，2025-11-05 为 5,164 只，2026-11-04 为 5,211 只。
- 未覆盖代码（约 450–556 只）按 `UNKNOWN` 处理；这是保守口径，不回溯使用未来行业。
- 抽查：000001.SZ→银行、600519.SH/000858.SZ→食品饮料、300750.SZ→电力设备、688981.SH→电子，映射正确。

## 二、回测结果

### 2.1 PIT 版本（30bp）

| 配置 | 年化收益 | 年化波动 | Sharpe | 最大回撤 | 年化单边换手 | 累计成本 | 行业最大净暴露 | 市值净暴露 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pit_base（无行业/市值约束） | 0.10% | 6.57% | 0.015 | -10.56% | 1.22 | 0.64% | 2.055% | 19.04% |
| pit_industry（+行业约束） | **1.29%** | 6.00% | **0.216** | -8.41% | 1.22 | 0.64% | 0.667% | 14.09% |
| pit_both（+行业&市值约束） | 1.14% | 5.66% | 0.201 | -7.80% | 1.24 | 0.65% | 0.908% | 11.50% |

PIT 版 `pit_both` 成本敏感性：0bp 1.52%（Sharpe 0.268）、10bp 1.39%（0.246）、30bp 1.14%（0.201）、50bp 0.89%（0.157）。

### 2.2 与 snapshot 版本对比

snapshot 版本（`4ce8fc505`）使用 `stock_basic.industry` 当前快照（110 类）；PIT 版本使用 SW L1 历史成员（31 类）。

| 配置 | snapshot 年化 / Sharpe / MDD | PIT 年化 / Sharpe / MDD | 差异（PIT - snapshot） |
|---|---:|---:|---:|
| base（无行业/市值约束） | 1.85% / 0.329 / -7.69% | 0.10% / 0.015 / -10.56% | -1.75pp / -0.314 |
| +行业约束 | 2.51% / 0.481 / -6.82% | 1.29% / 0.216 / -8.41% | -1.22pp / -0.265 |
| +行业&市值约束 | **3.15% / 0.708 / -4.46%** | 1.14% / 0.201 / -7.80% | **-2.01pp / -0.507** |

### 2.3 核心发现

1. **PIT 版本内部，行业约束仍有改善**：pit_base 年化 0.10% → pit_industry 1.29%，Sharpe 0.015→0.216，MDD -10.56%→-8.41%；行业净暴露从 2.055% 降到 0.667%。方向与 snapshot 版本一致，说明「行业均衡能降低风险」不是纯前视造成的。
2. **但绝对收益/Sharpe 大幅下降**：PIT 版 `pit_both` 仅 1.14% / 0.201，远低于 snapshot 的 3.15% / 0.708；此前的高收益不能直接视为可持续。
3. **市值约束在 PIT 版仍只降暴露、不增收益**：pit_industry 1.29%→pit_both 1.14%，但市值净暴露 14.09%→11.50%。
4. **换手/成本几乎不变**：各配置年化换手 1.22–1.24、成本 0.64–0.65%，差异主要来自选股与风险暴露，而非交易频率。

## 三、口径限制（重要）

- **行业粒度不可比**：snapshot 是 `stock_basic.industry` 的 **110 类**细分行业；PIT 只有 SW L1 **31 类**。PIT 版的下降同时包含「消除前视/幸存偏差」与「行业分类变粗」两种效应，无法完全拆分。
- **没有 PIT L2/L3 历史**：Tushare 本地 `index_member_all` 与 API 均只返回当前快照；同粒度 apples-to-apples 需要 SW L2（134 类）或 L3（346 类）的历史成员，当前数据不可得。
- **覆盖缺口**：约 450–556 只（8%–10%）无 PIT 成员，按 `UNKNOWN` 处理；这些多为新上市或行业归属缺失股票。
- **样本短**：442 个收益日、8 次 60 日调仓；结论只代表单段行情。
- **交易假设**：未计融券费用/券源、冲击成本、涨跌停排队失败、停复牌跳空、退市偏差；多空 gross=1，不能与单边等权基准直接比较。
- **未接生产**：本报告为研究结论，选股链路未变更。

## 四、结论与建议

1. **PIT 行业约束技术上可行且方向正确**：消除 `stock_basic.industry` 当前快照的前视/幸存偏差后，行业约束仍能把 pit_base 的 Sharpe 从 0.015 提升到 0.216、回撤从 -10.56% 收窄到 -8.41%。
2. **此前的 3.15% / Sharpe 0.708 不能直接采信**：PIT 版只有 1.14% / 0.201；高收益至少部分来自行业分类粒度或快照信息。
3. **下一步做同粒度对比**：优先补齐 SW L2 历史成员（或能找到与 `stock_basic.industry` 同粒度的 PIT 分类），再判断前视偏差的真实幅度；在此之前不将 snapshot 结果作为策略结论。
4. **不建议接入生产**：仍缺 PIT 同粒度分类、更长样本、融券/冲击成本、严格中性投影与 paper trading。

## 五、复现

```powershell
cd C:\项目\老田的AI助手
& C:\项目\py312\python.exe -X utf8 -u C:\Temp\run_bm_pit.py
```

## 六、产物

- `tools/research/pit_industry.py`：PIT 行业映射
- `tools/research/factor_neutralize.py`：支持 PIT 行业逐日中性化
- `tools/research/factor_portfolio.py`：支持 PIT 行业约束与暴露统计
- `tools/tests/test_pit_industry.py`、`test_factor_neutralize.py`、`test_factor_portfolio.py`
- `research/reports/portfolio_bm_pit_20261105.json`：完整回测结果
- 本文档；执行回执见 `deliverables/2026-11-05_PIT行业约束复跑_执行回执.md`

---
生成：2026-11-05；Lead（零界项目）
