# 张松 PIT / 数据口径自查（供司马微 G7）· 2026-11-01

> 范围：量学因子 9 个 · 可计算战法 13 个 · 批量四关候选 17 项（可计算 13 / 不可计算 4）
> PIT 结论：因子 pass 9 / fail 0 / uncertain 0；战法 pass 13 / fail 0 / uncertain 0
> 信号口径：T 日收盘信号 → T+1 收盘入场 → T+21 收盘卖出
> **仅审计不改策略代码**；源文件 sha256 见 JSON scope。

## 一、PIT/前视审查（逐因子）

| 因子 | 函数 | 证据行 | 判定 | 说明 |
|---|---|---|---|---|
| vol_ratio5 | `_f_vol_ratio5` | tools/research/factor_lib.py:359-366 | **pass** | 量比基准为近似量（T-3..T-1 均值），未按流通股本/复权成交量归一 |
| double_vol | `_f_double_vol` | tools/research/factor_lib.py:367-376 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| shrink_vol | `_f_shrink_vol` | tools/research/factor_lib.py:377-386 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| up_vol_down | `_f_up_vol_down` | tools/research/factor_lib.py:387-396 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| yin_yang | `_f_yin_yang` | tools/research/factor_lib.py:397-409 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| near_high_vol | `_f_near_high_vol` | tools/research/factor_lib.py:410-421 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| golden_pillar | `_f_golden_pillar` | tools/research/factor_lib.py:422-441 | **pass** | 黄金柱使用未复权 close 与 T-3 基柱比较（除权日可能误判，建议改用 adj_close 复权序列） |
| zero_vol | `_f_zero_vol` | tools/research/factor_lib.py:442-452 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |
| aokou | `_f_aokou` | tools/research/factor_lib.py:453-471 | **pass** | 仅用 T 及以前数据（滚动/正向 shift） |

## 二、PIT/前视审查（逐战法）

| spec | 名称 | 函数 | 证据行 | 判定 | 简化/偏差 |
|---|---|---|---|---|---|
| LX03_GENERAL_PILLAR | 将军柱（卧底/搭梯战法） | `_f_lx03_general_pillar` | tools/research/book_batch_test.py:219-238 | **pass** | 实现简化：确认日信号（基柱 T-3 + 三日均收低于实顶、守住实底×0.99）；卧底平底/搭梯突破未实现 |
| LX04_MARSHAL_PILLAR_ZERO | 元帅柱·零号战法 | `_f_lx04_marshal_zero` | tools/research/book_batch_test.py:239-263 | **pass** | 实现简化：跳空阳柱+三日不破实顶/实底+确认日放量阳线（零号弹起）；回踩深度与先者优先未实现 |
| LX05_GOLDEN_LINE_TRILOGY | 金线战法三步曲（黄金线/黄金双线/黄金十字） | `_f_lx05_golden_line` | tools/research/book_batch_test.py:264-282 | **pass** | 实现简化：金线绑定 factor_lib 已确认黄金柱实顶；回踩±1%不破+收阳；黄金双线/十字未实现 |
| LX06_YIN_LINE_TACTICS | 大盘阴线战法 / 长阴短柱 | `_f_lx06_long_yin_short` | tools/research/book_batch_test.py:283-303 | **pass** | 实现简化：只实现个股长阴短柱（-3%/量比≤0.7）+有底+阳盖阴；大盘阴线择时未实现 |
| LX09_LIVE_SUFFOCATION | 现场直憋战法 | `_f_lx09_suffocation` | tools/research/book_batch_test.py:304-324 | **pass** | 实现简化：大阴框架→20日窄幅（≤20%）缩量箱体（5日均量≤100日均量×0.8）→放量突破箱体上沿；按买点后置（突破日）计 |
| LX10_3121_RULER | 3121 战法（量柱/价柱/量价标尺） | `_f_lx10_ruler3121` | tools/research/book_batch_test.py:325-344 | **pass** | 实现简化：缩量31（≤2/3）或缩为百低判假跌；次日阳盖阴且站上前柱 (high+low)/2 的21位 |
| LX11_SPY_TACTICS | 间谍战法（量学七剑） | `_f_lx11_spy_small_vol` | tools/research/book_batch_test.py:345-367 | **pass** | 实现简化：小倍阳（1.0~1.9倍、收阳）密集≥2根+最近小倍阳实底水平线回踩不破再收阳 |
| LX12_DOUBLE_YIN_EXIT | 双阴出货战法 | `_f_lx12_double_yin_exit` | tools/research/book_batch_test.py:368-386 | **pass** | 实现简化：出场/风控信号按反向事件（direction=-1）检验：双阴（含假阳）+跌破前收/左阳实底+下降趋势过滤 |
| LX14_RELAY_DOUBLE_YANG | 接力双阳（底部小双阳） | `_f_lx14_relay_double_yang` | tools/research/book_batch_test.py:387-413 | **pass** | 实现简化：小双阳间隔3~5日（右阳需距今日≥3日以满足“后3日不破”）+实底精准线±1%+突破右阳实顶 |
| LX17_STAND_ON_CONCAVE_PEAK | 站稳凹峰即将上攻 | `_f_lx17_concave_peak` | tools/research/book_batch_test.py:414-432 | **pass** | 实现简化：凹峰线用近20日最高开盘（大阴实顶代理）+≥3个±1%契合点+当日回踩不破收阳 |
| LX19_BULL_STOCK_VOLUME_SHAPE | 牛股量形（五要素/三阶段） | `_f_lx19_bull_shape_launch` | tools/research/book_batch_test.py:433-454 | **pass** | 实现简化：五要素中可日线化之①百日低量群+③启动柱倍阳过左峰，叠加60日横盘孕育期；②大起大落/⑤黄金十字画线未实现 |
| LX20_BULL_STOCK_THREE_ABSOLUTES | 牛股三绝（倍量不穿/高量不破/跳空不补） | `_f_lx20_three_absolutes` | tools/research/book_batch_test.py:455-544 | **pass** | 实现简化：只实现“倍量不穿+突破倍量柱实顶”作事件；高量不破/跳空不补与持有过滤未并联 |
| LX22_CONCAVE_NOTCH_GOLD | 凹口淘金 | `_f_aokou` | tools/research/factor_lib.py:453-471 | **pass** | 复用 factor_lib 共享因子实现（同 PIT 审查结论）；实现简化：直接复用 factor_lib 现有 aokou（凹口收口近似：20日高点回落≥8% |

## 三、17 项批量四关 · 数据口径书面说明

| spec | 可计算 | 判定 | 数据源 | 复权 | universe | 样本期 | IS/OOS |
|---|---|---|---|---|---|---|---|
| LX03_GENERAL_PILLAR | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX04_MARSHAL_PILLAR_ZERO | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX05_GOLDEN_LINE_TRILOGY | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX06_YIN_LINE_TACTICS | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX07_ONE_SWORD_SEAL_THROAT | 否 | uncertain | tushare | — | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX09_LIVE_SUFFOCATION | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX10_3121_RULER | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX11_SPY_TACTICS | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX12_DOUBLE_YIN_EXIT | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX14_RELAY_DOUBLE_YANG | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX16_FLAT_SLANT_CROSS | 否 | uncertain | tushare | — | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX17_STAND_ON_CONCAVE_PEAK | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX19_BULL_STOCK_VOLUME_SHAPE | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX20_BULL_STOCK_THREE_ABSOLUTES | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX21_TEN_MONTH_PREGNANCY | 否 | uncertain | tushare | — | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX22_CONCAVE_NOTCH_GOLD | 是 | pass | tushare | adj_close(收益)/原始价量(信号) | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |
| LX23_FUSE_TACTICS | 否 | uncertain | tushare | — | tradable | 20230103 .. 20260923（请求 20230101..20261030） | 单次切分：IS .. 20241231 / OOS 20241231..20260923；signal_days=904 |

### 数据口径要点（全体 17 项共用）

- 数据源：tushare 本地数据根：C:\项目\老田的AI助手\quant_backtest\data\tushare
- 复权：收益用 adj_close（复权买入持有）；信号阈值使用 close/pct_chg/vol，未声明前/后复权（除权日形态可能失真）
- Universe：tradable 快照；四关补充 current stock_basic（幸存者偏差风险）
- 样本期：2023-01-03 .. 2026-09-23；IS 至 2024-12-31，OOS 2025-01-01 起（单次切分）
- 成本/基准：单边 30bp；等权市场日再平衡复利（与 H24/H26/H29 同口径）
- 四关口径（four_gate_20261101）：2019-01-31..2026-09-30，非 ST/上市≥250 日/价≥2，单边 0.15%，等权十分位

## 四、交易制度处理核对

| 检查项 | 状态 | 证据 |
|---|---|---|
| limit_up | partial | 358:source="伏击涨停·量柱七形态")、366:source="伏击涨停·量柱七形态 / H24 倍量柱")、376:source="伏击涨停·量柱七形态") |
| suspension | partial | 1237:"**未做容量/换手/涨跌停与停牌可交易性评估**；等权市场为日频再平衡，与个股买入持有存在再平衡差" |
| st | partial | 17:- 财务数据用 ann_date（公告日）≤ T 的最新一期 ROE（见 load_latest_fina 同口径）；、19:- 股票池过滤（tradable）只用 T 日可知的价格/成交额 + 上市日期（静态），不含当前 ST 名称等后验信息。、134:sorted_rank = np.broadcast_to(np.arange(1, m + 1, dtype=np.f |
| new_stock | partial | 80:MIN_LIST_DAYS = 250           # 可交易股票池：上市天数下限、911:return pd.DataFrame(columns=["ts_code", "name", "industry", 、912:parts = [pq.read_table(f, columns=["ts_code", "name", "indus |
| tradable_mask | partial | 19:- 股票池过滤（tradable）只用 T 日可知的价格/成交额 + 上市日期（静态），不含当前 ST 名称等后验信息。、238:eligible（可选 bool DataFrame）：可交易股票池掩码；None 表示全市场。、241:def __init__(self, fields: dict[str, pd.DataFrame], eligible |

## 五、已知偏差清单

- 量学形态因子使用 close/pct_chg/vol 原始价量，除权日可能造成形态阈值误判（建议全量改 adj_close 复权序列并重算）
- universe: 等权市场基准日再平衡 vs 个股买入持有存在再平衡差（bb.meta.baseline 已披露，非前视但影响 CAR 解释）
- IS/OOS 单次固定切分（2023-2024 IS / 2025-2026 OOS），未做 walk-forward/embargo（G2 未覆盖）
- 成本假设单一：单边 30bp（四关报告为单边 0.15%），未做 50/80bp 敏感性（G3 未覆盖）
- 四关 universe 使用 current stock_basic → 幸存者偏差风险（G7 已知）
- 4 个 C 级战法（LX07/16/21/23）无程序化实现 → PIT 无法验证，判 uncertain
- 评估器 forward_car 使用 shift(-1)/shift(-1-h) 取未来收益，属标签构造（评测口径），非因子输入；因子/信号实现未引用未来值
- 停牌/涨停不可买/ST/新股：tradable 掩码存在但未在批量回测中显式逐日剔除涨停不可买（G5/G7 待补）

## 六、G7 结论与建议

- PIT/前视：9 个量学因子全部仅用 T 及以前数据（滚动/正向 shift），未发现负向 shift/center/bfill/未来标签引用；评估器 forward_car 的未来收益仅作标签。
- 13 个可计算战法实现均通过 T 日收盘信号 → T+1 收盘入场 → T+21 收盘卖出的口径，未发现未来函数；4 个 C 级战法无实现，PIT 无法验证（uncertain）。
- 数据口径：批量四关数据源 tushare、收益复权（adj_close）、universe=tradable（四关补充 current stock_basic）、样本期 2023-01-03..2026-09-23（IS 至 2024-12-31），为单次 IS/OOS 切分；存在幸存者偏差与复权口径未显式声明两处口径风险。
- 交易制度：tradable/eligible 掩码存在，但涨停不可买/停牌/ST/新股未在批量回测逐日显式剔除（G5/G7 待补）；本轮 PIT 审查不涉及重跑。
- G7 建议：①量学因子改用全复权序列重算；②universe 改为 point-in-time 快照并逐日剔除涨停/停牌/ST/新股；③IS/OOS 升级 walk-forward（G2）；④成本 30/50/80bp 敏感性（G3）。

> 本报告为代码级静态审查与口径说明，不构成投资建议；仅审计不改策略代码（源文件 sha256 记录于 scope）。
