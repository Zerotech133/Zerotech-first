# 20261014 vol targeting（波动率目标仓位缩放）改动留痕

任务：给回测底座 backtest.py 加"波动率目标仓位缩放"，**默认关闭、关闭时与现行为字节级等价**。

## 改动清单

| 文件 | 改动 | 备份 |
|---|---|---|
| `quant_backtest/backtest.py` | 1) 新增 `VOL_TARGET_DEFAULTS` / `load_vol_target_config()` / `_vol_scale()`；2) `run()` 新增 `vol_target=None` 参数：开启时信号域先乘 scale 再走同一 T+1 shift 路径（scale 只用截至信号日数据，无未来函数）；3) `_trade_list()` 单笔 pnl 按持仓期平均仓位折算（关闭时 pos∈{0,1} 均值恒为 1.0，pnl 精确不变）；4) `_metrics()` 交易计数改为"pos 从 0→非0"（关闭时与旧口径 `pos.diff()>0` 计数等价） | `backtest.py.bak_20261014_vt` |
| `quant_backtest/config.json` | `backtest` 节新增 `"vol_target": {"enabled": false, "target_vol": 0.15, "lookback": 20, "floor": 0.25, "cap": 1.0}`（默认关闭）；旧 config 无此键时 `load_vol_target_config()` 逐项回退默认（=关闭） | `config.json.bak_20261014_vt` |
| `quant_backtest/test_voltarget.py` | 新增，7 个用例（见下） | 新文件 |
| `quant_backtest/_voltarget_ab_20261014.py` | 对照实验一次性脚本 + 结果 `_voltarget_ab_result_20261014.json` | 新文件（不入产线） |

### 实现口径
- `scale = clip(target_vol / realized_vol, floor, cap)`；realized_vol = 信号日 t 前 lookback=20 交易日收益 `rolling(20).std()×sqrt(252)`（窗口只含 t 及之前 → 无未来函数；叠加引擎 shift(1) 后 t+1 生效）。
- 默认 target_vol=0.15（年化）、floor=0.25、cap=1.0（**只降仓不加杠杆**）；rv NaN（窗口不足）或 rv≤0 → scale=min(1.0, cap)。
- 成本仍按名义金额比例计（turnover×bps），缩放自然减小名义换手，比例不变。
- 成本分层不动：CN 12+3bps、T+1 shift(1) 防未来函数，均沿用 `load_trade_config()` 既有逻辑。

## 测试结果

```
test_voltarget.py：7 passed
  ① 关闭等价：4 组合成序列 × T+0/T+1，逐指标/净值/净收益/持仓/逐笔 trade_list
     与改动前备份（backtest.py.bak_20261014_vt，SourceFileLoader 动态加载）
     完全一致；config 键缺省/半缺键回退默认=关闭
  ② 高波动缩仓（scale<1）、低波动触 cap=1.0
  ③ 无未来函数：前 30 日相同、之后波动路径不同的合成数据，信号日<30 的
     scale/pos/net_ret 逐点一致；窗口吃进突变段后 scale 不同（机制灵敏）
  ④ floor=0.25 托底、cap 生效（cap=0.8 时 pos==0.8 不杠杆）
  ⑤ 缩放后单笔 pnl = 平均仓位×(出价/入价-1)，误差 <1e-12

既有回归（20261014 全量跑）：
  test_backtest_trade_layers.py test_runner_ledger_p1tail.py
  test_runner_scan_card.py test_nine_dim_backtest.py test_qpzm_backtest.py
  test_qd_backtest_1445.py test_voltarget.py
  → 40 passed, 1 failed
  唯一失败 test_qd_backtest_1445.py::test_hfq_return_across_exdiv：
  ModuleNotFoundError: No module named 'qd_backtest_1445'（模块文件在仓库
  中本就不存在，import 阶段即失败，属存量问题，与本次改动无关）
```

## 对照实验（enabled=false vs true，缩样口径如实标注）

口径：data/ashare 湖日线 parquet，**缩样 5 只 CN 大票**（非全池 5598 只）；
复权用 parquet 自带 pre_close 链乘构造后复权（adj_factor 湖只到 2023-07 覆盖
不足，链比率口径与 chanlun_scan._chain_ratio 一致，守卫 0.05~20）；
策略 `trend_follow`（runner 默认）；成本 CN=12+3bps T+1；区间=湖内全历史
（854 根 bar ≈ 2023-04~2026-10；000001.SH 仅 300 根）。
vol_target 默认参数（0.15/20/0.25/1.0）。PF=逐笔盈利合计/亏损合计绝对值。

| 标的 | bars | 总收益 off | 总收益 on | PF off | PF on | 最大回撤 off | 最大回撤 on | 交易数 off | 交易数 on |
|---|---|---|---|---|---|---|---|---|---|
| 600519.SH | 854 | -30.9% | -22.8% | 0.277 | 0.271 | -35.6% | -27.8% | 35 | 35 |
| 000001.SH | 300 | +0.3% | +0.3% | ∞（仅1笔无亏损笔） | ∞ | -1.2% | -1.2% | 1 | 1 |
| 600036.SH | 854 | -3.6% | +1.1% | 1.795 | 1.690 | -18.7% | -18.1% | 42 | 42 |
| 000858.SZ | 854 | -25.2% | -15.2% | 0.265 | 0.235 | -30.5% | -17.4% | 27 | 27 |
| 601318.SH | 854 | +18.7% | +16.1% | 1.454 | 1.167 | -20.6% | -11.8% | 34 | 34 |

观察：交易数不变（缩放不改信号）；4/5 样本最大回撤显著收窄（000858 -13.1pct、
601318 -8.8pct），亏损标的总收益普遍改善；盈利标的因降仓让渡部分收益（601318
-2.6pct），符合"只降仓不加杠杆"的预期权衡。

## 回退方法

```bash
cd E:/老田的AI助手/quant_backtest
cp backtest.py.bak_20261014_vt backtest.py
cp config.json.bak_20261014_vt config.json
# test_voltarget.py / _voltarget_ab_20261014.py / _voltarget_ab_result_20261014.json
# 为新增文件，回退后可留可删；不删不影响旧行为（无人 import）
```

或仅关开关：config.json backtest.vol_target.enabled 置 false（默认已是），
行为与改动前字节级等价（有测试保障）。

## 禁区确认

未注册计划任务、未推飞书、未改 portal 产线配置、未删除任何文件。
