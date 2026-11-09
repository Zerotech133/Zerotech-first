# 中性化 book_to_market：PIT 申万 L2 行业约束 60 日复评（同粒度对比）

- 任务：m06132「补齐申万 L2 历史成员，用同粒度行业分类再跑一次对比」
- 日期：2026-11-05
- 信号：`book_to_market`（PIT SW L2 行业 + 市值 OLS 残差，日截面 rank pct）
- 面板：484 日 × 5,662 只（20241106–20261104）
- 信号窗：2025-01-02 至 2026-09-29；442 个组合收益日、8 次 60 日调仓
- 基准配置：60 日调仓、头尾 20% 等权、多空各 0.5、buffer 0.3、turnover penalty 0.5、单边换手上限 0.3、单边成本 30bp
- 原始结果：`research/reports/portfolio_bm_pit_l2_20261105.json`

## 一、申万 L2 历史成员补齐

### 1.1 数据拉取

新增 `tools/data_sources/tushare_sw_member.py`（Tushare `index_member`，按行业指数逐个拉取，断点续传）：

```powershell
python tools/data_sources/tushare_sw_member.py fetch --level L2
python tools/data_sources/tushare_sw_member.py status --level L2
```

| 项 | 数量/说明 |
|---|---|
| `sw_classify_l1` 中 L2 指数 | 134 个 |
| API 返回非空 | 125 个，8,170 行 |
| API 返回 0 行 | 9 个（其中 6 个当前仍在用的 L2 指数） |
| 用 `index_member_all` 当前快照补齐 | 6 个：801019/801117/801207/801216/801961/801983，25 行 |
| 无成员可补（2014 版停用指数） | 3 个：801217/801768/801786 |
| 最终 `sw_l2_member` | **131 个 parquet、8,195 行、5,473 只、131 个行业** |
| 落盘路径 | `quant_backtest/data/tushare/sw_l2_member/`（数据目录，gitignore） |

> 口径说明：6 个当前 L2 指数 Tushare `index_member` 无历史返回，只能用 `index_member_all` 当前快照补；其 `in_date` 沿用快照，`out_date` 为空。3 个停用指数没有成员，不参与映射。

### 1.2 代码

- `tools/research/pit_industry.py`：
  - 新增通用 `load_sw_industry(level, member_dir=None, classify_dir=None)`；
  - 新增 `load_sw_l2_industry()` 和 `default_member_dir()`；
  - `PITIndustryMap.source` 增加 `level`，行业来源标记由 `pit_sw_l1` 修正为 `pit_sw_l2`（中性化与组合 meta 同步）。
- `tools/research/factor_neutralize.py`：`_industry_source_name()` 按 map 的 level 生成 `pit_sw_l1`/`pit_sw_l2`。
- `tools/research/factor_portfolio.py`：`simulate` 的 `industry_source` 同样按 level 生成。
- 测试：`tools/tests/test_tushare_sw_member.py`（6 项，FakeSession 不联网）+ `test_pit_industry.py`（8 项，含 L2 level 过滤）；PIT/中性化/组合 4 文件共 **34 passed**。

### 1.3 覆盖

- PIT L2 覆盖：2024-11-06 5,110 只/130 行业、2025-11-05 5,169 只、2026-11-04 5,216 只；相对 5,662 只面板，末端未覆盖约 446 只（7.9%），按 `UNKNOWN` 处理。
- 抽查：000001.SZ→股份制银行Ⅱ、000002.SZ→房地产开发、000004.SZ→软件开发。
- **粒度**：PIT L2 为 130–131 个行业，snapshot 为 `stock_basic.industry` 110 类，二者已接近同粒度；仍非同一套分类体系。

## 二、回测结果

### 2.1 PIT L2（30bp）

| 配置 | 年化收益 | 年化波动 | Sharpe | 最大回撤 | 年化单边换手 | 累计成本 | 行业最大净暴露 | 市值净暴露 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pit_l2_base（无行业/市值约束） | 0.88% | 5.65% | 0.156 | -8.13% | 1.23 | 0.65% | 1.148% | 16.75% |
| pit_l2_industry（+行业约束） | **1.27%** | 5.11% | **0.248** | -7.01% | 1.25 | 0.66% | 0.603% | 15.91% |
| pit_l2_both（+行业&市值约束） | **1.50%** | **4.69%** | **0.320** | **-5.38%** | 1.27 | 0.67% | 0.943% | 11.83% |

`pit_l2_both` 成本敏感性：0bp 1.89%（Sharpe 0.402）、10bp 1.76%（0.375）、30bp 1.50%（0.320）、50bp 1.24%（0.265）。

### 2.2 三版对比（60 日，30bp）

| 配置 | snapshot（110 类） | PIT L1（31 类） | PIT L2（131 类） |
|---|---:|---:|---:|
| base 年化 / Sharpe / MDD | 1.85% / 0.329 / -7.69% | 0.10% / 0.015 / -10.56% | 0.88% / 0.156 / -8.13% |
| +行业 年化 / Sharpe / MDD | 2.51% / 0.481 / -6.82% | 1.29% / 0.216 / -8.41% | 1.27% / 0.248 / -7.01% |
| +行业&市值 年化 / Sharpe / MDD | **3.15% / 0.708 / -4.46%** | 1.14% / 0.201 / -7.80% | **1.50% / 0.320 / -5.38%** |

### 2.3 核心发现

1. **L2 明显优于 L1，行业粒度是重要变量**：同样 PIT 口径，base 年化 0.10%→0.88%，both 1.14%→1.50%，both Sharpe 0.201→0.320，MDD -7.80%→-5.38%。L1 的「PIT 后收益崩塌」有一部分是行业分类太粗，而非全是前视偏差。
2. **但 L2 仍显著低于 snapshot**：both 年化 3.15%→1.50%（-1.65pp），Sharpe 0.708→0.320（-0.39），MDD -4.46%→-5.38%。L2 的 130–131 类与 snapshot 的 110 类粒度接近，因此这部分差距**更难用粒度差异解释**，更可能来自 `stock_basic.industry` 当前快照的前视/幸存偏差（用后来的行业归属回填历史）。
3. **行业约束的方向仍成立**：L2 内部 base 0.88%→industry 1.27%，Sharpe 0.156→0.248；再叠加市值约束到 1.50% / 0.320，行业净暴露和市值净暴露均下降。
4. **换手/成本稳定**：三配置年化换手 1.23–1.27、成本 0.65–0.67%，改善不是靠提高交易频率。
5. **结论**：此前的 3.15% / 0.708 不能作为可持续基线；**PIT L2 的 1.50% / 0.320 / -5.38% 是目前更可信的研究基线**，但仍是单段行情、未计融券与冲击成本，不接生产。

## 三、口径限制

- **分类体系非完全一致**：PIT 用申万 L2（131 类），snapshot 用 `stock_basic.industry`（110 类）；只能算近似同粒度，不能做逐行业一一对应。
- **6 个 L2 指数用当前快照补齐**：这些指数 Tushare `index_member` 无历史返回，成员区间用 `index_member_all` 当前快照；可能仍有轻微前视。
- **3 个 2014 版停用 L2 指数无成员数据**，未纳入映射；这些行业在样本期基本不再使用。
- **覆盖缺口**：末端约 446 只（7.9%）无 PIT L2 行业，按 `UNKNOWN` 处理。
- **样本短**：442 个收益日、8 次 60 日调仓；单段行情。
- **交易假设**：未计融券费用/券源、冲击成本、涨跌停排队失败、停复牌跳空、退市偏差；多空 gross=1，不能与单边等权基准直接比较。
- **未接生产**：研究结论，选股链路未变更，无需重启。

## 四、复现

```powershell
cd C:\项目\老田的AI助手
# 1) 拉取 L2 成分（首次/断点续传）
& C:\项目\py312\python.exe -X utf8 tools/data_sources/tushare_sw_member.py fetch --level L2
# 2) 回测
& C:\项目\py312\python.exe -X utf8 -u C:\Temp\run_bm_pit_l2.py
```

## 五、产物

- `tools/data_sources/tushare_sw_member.py`：申万 L2 历史成员拉取工具
- `tools/tests/test_tushare_sw_member.py`、`tools/tests/test_pit_industry.py`
- `tools/research/pit_industry.py`：支持 L1/L2 PIT 映射
- `tools/research/factor_neutralize.py`、`tools/research/factor_portfolio.py`：行业来源标记修正
- `research/reports/portfolio_bm_pit_l2_20261105.json`：完整回测结果
- 执行回执：`deliverables/2026-11-05_PIT_L2行业约束复跑_执行回执.md`

---
生成：2026-11-05；Lead（零界项目）
