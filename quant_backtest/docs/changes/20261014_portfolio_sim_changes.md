# 20261014 合成组合历史模拟（portfolio_sim）变更留痕

> sim-dev 只读产线任务：如果过去 2-3 年就按今日验证过的规则组合跑盘，
> 资金曲线什么样、哪个模块贡献正/负。产线零改动，无回退需求
> （删除下述新文件即完全清除）。

## 一、口径（全部来自今日已验证规则，零新口径）

| 项 | 口径 | 来源留痕/产物 |
|---|---|---|
| 环境总闸 | 信号日 breadth ≥ 0.45 才开新仓（45 下限，45~50 为 qpzm/cross/双刀三方互证观察带） | reports/qd_breadth_series.json（湖 raw close>MA20 全市场占比，与 cross 环境闸门同源）；reports/qd_gate_variants.json（θ∈40/45/50 网格） |
| a) 双刀 | qd_details_hfq 剔 rotation 后 regime==trend_up，持有 20 日。验证锚：combo hold20 trend_up PF 1.293（n=503） | reports/qd_details_hfq.json；reports/qd_backtest_1445_variants.json |
| b) cross | nine=down0.5 / k=3 / thr=P75 / sort=raw / TopN=10，T-1 as-of 四源票，持有 10 日。验证锚：带 40% 闸门档 FULL pf5=1.2653 | tools/research/cross_grid_backtest.py（移植复刻）；reports/cross_grid_result.json、cross_grid_envgate_check.json |
| c) qpzm | 现行口径 w_cur / th7550 / gate_on / jev=missing，当日 composite score≥75 按 score 降序 TopN=10，持有 5 日。验证锚：hold5 均值 +0.27%、超额 +0.16%（窗 20251001~20261013） | qpzm_backtest.py（import 复用）；reports/qpzm_backtest_report_20261014.json |
| 组合规则 | 等权（每仓固定 1/20 权益）、最多 20 仓、同票不重复加仓（跨模块）、T+1 开盘成交（一字板 unfill 剔除）、持有 N 日收盘卖出（停牌 ffill 结算）、15bps 单边成本（往返 30bps） | 任务书 20261014 |
| 优先级 | 仓位稀缺时 a 双刀 > b cross > c qpzm，模块内按分数降序 | 本模拟约定（留痕备案） |

## 二、数据源映射（全部只读）

| 源 | 文件 | 用途 |
|---|---|---|
| 双刀逐信号明细 | reports/qd_details_hfq.json（4291 条，20231009~20261013） | 直接过滤 regime==trend_up（492 条，尾部 10 条缺 r20 跳过） |
| cross 行情矩阵 | %TEMP%/cross_grid_cache_npz.npz（853 日×5036 只，20230403~20261013，今日 09:37 网格运行缓存命中，零重扫湖） | 复刻四源票+逐日 TopN 明细化 |
| cross 缠论事件 | data/sig_events/sig_events_market_v3_20231010_20261013.json | 四源之一 |
| cross 9dim 锚 | data/nine_dim_replay.jsonl（锚 20240902~20261008） | 四源之一；**决定 cross 信号窗起点 20240902** |
| qpzm 信号+价格引擎 | data/ashare/*.parquet（qpzm_backtest.load_wide，5597 只，K254 后复权） | 现行口径信号重建 + 统一价格引擎 |
| 宽度总闸序列 | reports/qd_breadth_series.json（893 个有效日，20230103~20261013） | 信号日闸门判定 |
| 信号缓存 | data/portfolio_sim_signals_20261014.json | 三源明细落盘，重跑秒级（整跑 60~126s，符合分钟级约束） |

三源均有逐信号明细，**无 excluded 源**（qpzm 原回测产物只有聚合数，本模拟经
import qpzm_backtest 零口径重建出逐日 TopN 明细）。

### 移植自校验（与验证锚比对）

- cross：同口径重放 40% 闸门档 **n=1486 / pf5=1.2653 / pf10=1.2691，
  与 cross_grid_envgate_check FULL 三项零偏差** ✅
- qpzm：env breadth@20251009 = 0.450，与 report env_daily[0] 一致 ✅
- qd：trend_up 492（验证锚 hold20 口径 n=503，差 10 条为尾部前向窗不足）✅

## 三、结果（reports/portfolio_sim_20261014.json + .md）

窗口 20231009~20261013；基准：全市场等权 B&H +34.91%（MDD -30.67%）、
等权日频参照 +48.31%（MDD -31.14%）。

| 组合 | 总收益 | 年化 | 最大回撤 | 夏普 | PF |
|---|---|---|---|---|---|
| a 双刀 | +36.90% | 11.47% | -8.99% | 1.012 | 1.849 |
| b cross | +41.20% | 12.67% | -20.83% | 0.671 | 1.320 |
| c qpzm | -1.05% | -0.36% | -25.69% | 0.125 | 1.035 |
| a+b | **+58.46%** | 17.25% | -21.75% | 0.851 | 1.441 |
| a+b+c | +26.24% | 8.39% | -25.07% | 0.458 | 1.151 |

分模块归因（a+b+c 组合内，简单加总口径）：qd +19.1pp（PF 1.73）、
cross +29.7pp（PF 1.43）、**qpzm -15.5pp（PF 0.88，负贡献）**。

总闸归因：闸关 357 日（占 49%），期间等权基准 -72.97% —— 空仓避损是组合
能跑赢 B&H 的第一功臣。分模块看，总闸拦截笔均收益：qd -0.21%、qpzm -0.31%
（避损为正），**cross +0.52%（拦截的信号均值为正 → 总闸对 cross 有错过成本）**。

一句话结论：按今日验证规则组合跑盘，a+b（双刀+cross）三年 +58.5%，显著跑赢
全市场等权买入持有（+34.9%）且回撤更浅；qpzm 为负贡献（其 2023-2024 为
验证窗外推期），总闸空仓期避损是超额的第一来源。

## 四、局限

1. **明细覆盖窗不一致**：qd 20231009 起；cross 受 nine_dim 周锚限制
   20240902~20260918（此前 cross 模块天然空转）；qpzm 信号可全窗生成，但
   验证窗仅 20251001 起，**2025-10 前的 qpzm 结果属口径外推**（其 2024 年
   独立跑盘 -6.8%、MDD -24.7% 需按外推样本看待）。
2. **统一口径 vs 各源验证口径**：模拟统一 K254 后复权 + 30bps 往返成本；
   验证回测多为 15bps 往返、cross 网格用未复权价 → 单模块模拟数与验证锚
   存在口径差（方向为更保守），cross 信号生成口径已零偏差校验。
3. **事件驱动简化**：入场按昨日收盘权益定仓（1/20 固定），现金不足即跳过
   （a+b+c 跳过 1747 笔——20 仓上限是主要约束）；未模拟部分成交/滑点/
   冲击成本；期末未平仓按最后可得价估值。
4. **归因为简单加总口径**（Σ笔收益×1/20），未做复利分摊；组合层面优先级
   a>b>c 为本模拟约定，非验证产物。
5. 基准宇宙为 load_wide 全 parquet（含 4 只指数文件混入，占比 0.07%，
   影响可忽略）；宽度序列与 cross 闸门为 T 日收盘近似 14:45 时点
   （qd_gate_variants 已注记偏差方向）。
6. 历史模拟 ≠ 前推保证：信号参数均经今日同窗验证，存在过拟合风险
   （尤其 qpzm 阈值 75/50 与 cross 网格最优档）；本模拟仅为规则组合的
   历史形态呈现。

## 五、产物清单

- portfolio_sim_20261014.py（新文件，仓库根目录，只读产线）
- reports/portfolio_sim_20261014.json / .md（资金曲线周度序列在 json 内）
- data/portfolio_sim_signals_20261014.json（三源信号缓存，重跑加速）
- C:/Temp/portfolio_sim.log（运行日志）

回退：删除上述文件即可，产线零改动。
