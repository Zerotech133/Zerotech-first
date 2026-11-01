# 张松策略严苛调校 · 司马微 G1-G8 审计（v1）

- 生成：2026-11-01T16:48:33+08:00；审计对象：S01-S05 + book_backlog 24 项 + 张松 4 组研究
- 交付单：`deliverables/2026-11-01_张松交付司马微_策略严苛调校单_v1.md`
- 严格协议：G1 多重检验（BH-FDR q≤0.10）/ G2 walk-forward（≥3 fold+5 日 embargo）/G3 成本 30-50-80bp / G4 安慰剂≥1000 次置换 / G5 容量覆盖 / G6 分层稳健 / G7 PIT 审计 / G8 裁决
- **不改事实结论**（H24-H29 rejected、S05 closed），只重审「更严格证据下是否仍成立」

## 一、G1 多重检验（BH-FDR，q≤0.10）

- 假设池：**27** 个已测假设（H24-H29 9 + 批量四关 13 + 反向规避 4 + S05 1），有 t 值 27 个；q≤0.10 的假设 21 个。
- Bonferroni α=0.10/27=0.0037。

| 假设 | 家族 | t | p | q（BH） | 显著为正 |
|---|---|---|---|---|---|
| H24_double_vol | H24-H29 | -17.39 | 9.82e-68 | 1.33e-66 |  |
| H25_price_up_vol_down | H24-H29 | -11.89 | 1.33e-32 | 1.20e-31 |  |
| H26_yin_yang | H24-H29 | -8.91 | 5.10e-19 | 3.44e-18 |  |
| H27_near_high_vol | H24-H29 | -20.77 | 8.09e-96 | 2.18e-94 |  |
| H29_golden_pillar | H24-H29 | -6.19 | 6.02e-10 | 1.80e-09 |  |
| H29_double_vol_confirm | H24-H29 | -1.69 | 9.10e-02 | 1.07e-01 |  |
| H29_yin_yang_strict | H24-H29 | -4.04 | 5.35e-05 | 1.03e-04 |  |
| H29_shrink_pullback | H24-H29 | -7.68 | 1.59e-14 | 8.59e-14 |  |
| H28_avoid | H24-H29 | -0.84 | 4.01e-01 | 4.51e-01 |  |
| LX03_GENERAL_PILLAR | book_batch_13 | -5.89 | 3.77e-09 | 1.02e-08 |  |
| LX04_MARSHAL_PILLAR_ZERO | book_batch_13 | -0.73 | 4.65e-01 | 5.03e-01 |  |
| LX05_GOLDEN_LINE_TRILOGY | book_batch_13 | -1.90 | 5.72e-02 | 7.35e-02 |  |
| LX06_YIN_LINE_TACTICS | book_batch_13 | -3.92 | 8.89e-05 | 1.60e-04 |  |
| LX09_LIVE_SUFFOCATION | book_batch_13 | -3.70 | 2.15e-04 | 3.62e-04 |  |
| LX10_3121_RULER | book_batch_13 | -2.18 | 2.91e-02 | 4.14e-02 |  |
| LX11_SPY_TACTICS | book_batch_13 | -3.46 | 5.50e-04 | 8.74e-04 |  |
| LX12_DOUBLE_YIN_EXIT | book_batch_13 | +0.01 | 9.89e-01 | 9.89e-01 |  |
| LX14_RELAY_DOUBLE_YANG | book_batch_13 | -4.89 | 9.93e-07 | 2.23e-06 |  |
| LX17_STAND_ON_CONCAVE_PEAK | book_batch_13 | -4.43 | 9.60e-06 | 1.99e-05 |  |
| LX19_BULL_STOCK_VOLUME_SHAPE | book_batch_13 | -1.72 | 8.47e-02 | 1.04e-01 |  |
| LX20_BULL_STOCK_THREE_ABSOLUTES | book_batch_13 | -5.56 | 2.73e-08 | 6.70e-08 |  |
| LX22_CONCAVE_NOTCH_GOLD | book_batch_13 | -6.29 | 3.13e-10 | 1.21e-09 |  |
| LX20_BULL_STOCK_THREE_ABSOLUTES | reverse_avoid_4 | +7.35 | 1.98e-13 | 8.92e-13 | ✅ |
| LX22_CONCAVE_NOTCH_GOLD | reverse_avoid_4 | +2.03 | 4.24e-02 | 5.72e-02 | ✅ |
| LX06_YIN_LINE_TACTICS | reverse_avoid_4 | +6.20 | 5.65e-10 | 1.80e-09 | ✅ |
| LX12_DOUBLE_YIN_EXIT | reverse_avoid_4 | -3.27 | 1.08e-03 | 1.61e-03 |  |
| S05_LX20_AVOID_DEWEIGHT | strategies | -0.34 | 7.34e-01 | 7.62e-01 |  |

**结论**：校正后显著为正的假设只有 LX20_BULL_STOCK_THREE_ABSOLUTES, LX22_CONCAVE_NOTCH_GOLD, LX06_YIN_LINE_TACTICS。量学形态做多方向全部不显著（多数显著为负=反向线索）；反向规避方向显著，但属事后 sign-flip，需 OOS 独立确认。

## 二、逐策略 G1-G8 结果与裁决

| 策略/战法 | 裁决 | G1 | G2 | G3(50bp) | G4 | G5 | G6 | G7 | 关键证据 |
|---|---|---|---|---|---|---|---|---|---|
| 牛股三绝 · 反向规避/降配（S05 候选）<br>`LX20_BULL_STOCK_THREE_ABSOLUTES` | **NEEDS-MORE** | pass | pass | fail | fail | pass | pass | pass | G1/G2/G5/G6/G7 通过，但 G3 50bp 净超额已转负（-1.28%）、G4 安慰剂未做 → 不可直接交易，按严格协议 needs-more；先做降换手与安慰剂，再谈影子盘。 |
| 长阴短柱 · 反向规避（S05 候选）<br>`LX06_YIN_LINE_TACTICS` | **NEEDS-MORE** | pass | pass | pass | fail | pass | pass | pass | G1/G3(50bp)/G6/G7 通过，但覆盖 1.15% 过低、G4 未做 → needs-more（优先级低于 LX20）。 |
| 凹口淘金 · 反向规避<br>`LX22_CONCAVE_NOTCH_GOLD` | **CLOSE** | pass | pass | fail | fail | fail | pass | pass | G3 30bp 已 -6.57%、G5 覆盖 42.1%>30% → close（张松已判『被成本吃光』，复核一致）。 |
| 双阴出货 · 反向规避<br>`LX12_DOUBLE_YIN_EXIT` | **CLOSE** | fail | partial | fail | fail | fail | fail | pass | G1/G3/G5 均 fail（净 -24.6%@30bp、覆盖 71.9%）→ close。 |
| S05 LX20 规避/降配（已注册）<br>`S05_LX20_AVOID_DEWEIGHT` | **CLOSE** | fail | fail | fail | fail | pass | not-met | pass | 注册表已 closed_both_directions_fail；严格复核再次确认（G1/G2/G3 fail）。与 LX20 反向规避研究不矛盾：S05 是叠加在既有组合上的覆盖层，覆层后 OOS 为负；LX20 的独立规避口径（等权全市场前 5 日事件股剔除）才有正超额，两者口径不同。 |

## 三、book_backlog 24 项裁决

- 计数：**{'close': 20, 'needs-more': 4}**（close=关闭/停止投入；needs-more=待补验证）

| spec_id | 战法 | 裁决 | 理由 |
|---|---|---|---|
| LX01_SEVEN_VOLUME_FORMS | 量柱七形态（高/低/平/倍/梯/缩/金） | **close** | 已测 rejected（H24 倍量柱/H25 价升量缩 → rejected（简化定义无效））；H24-H29 全部 rejected，G1 无正向显著。 |
| LX02_GOLDEN_PILLAR | 黄金柱（三日不破实顶+缩量） | **close** | 已测 rejected（H29 严格版 → rejected（PIT 修正后前视撤回））；H24-H29 全部 rejected，G1 无正向显著。 |
| LX03_GENERAL_PILLAR | 将军柱（卧底/搭梯战法） | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX04_MARSHAL_PILLAR_ZERO | 元帅柱·零号战法 | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX05_GOLDEN_LINE_TRILOGY | 金线战法三步曲（黄金线/黄金双线/黄金十字） | **needs-more** | 批量四关 grade=C（方向未定，OOS 不显著）；需补 walk-forward/置换/成本敏感性。 |
| LX06_YIN_LINE_TACTICS | 大盘阴线战法 / 长阴短柱 | **needs-more** | G1/G3(50bp)/G6/G7 通过，但覆盖 1.15% 过低、G4 未做 → needs-more（优先级低于 LX20）。 |
| LX07_ONE_SWORD_SEAL_THROAT | 一剑封喉战法 | **close** | 批量四关 grade=F；无正向显著，不值得继续投入。 |
| LX08_DOUBLE_VOL_EXPAND_CONTRACT | 倍量伸缩战法 | **close** | 已测 rejected（H29 倍量确认 → rejected）；H24-H29 全部 rejected，G1 无正向显著。 |
| LX09_LIVE_SUFFOCATION | 现场直憋战法 | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX10_3121_RULER | 3121 战法 | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX11_SPY_TACTICS | 间谍战法（量学七剑） | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX12_DOUBLE_YIN_EXIT | 双阴出货战法 | **close** | G1/G3/G5 均 fail（净 -24.6%@30bp、覆盖 71.9%）→ close。 |
| LX13_ARMY_AT_CITY_GATE | 兵临城下（试关/攻关/冲关/守关） | **close** | 已测 rejected（H27 near_high_vol → rejected）；H24-H29 全部 rejected，G1 无正向显著。 |
| LX14_RELAY_DOUBLE_YANG | 接力双阳 | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX15_PRICE_UP_VOL_DOWN | 价升量缩择机做多 | **close** | 已测 rejected（H25 → rejected）；H24-H29 全部 rejected，G1 无正向显著。 |
| LX16_FLAT_SLANT_CROSS | 平斜交叉不上必下 | **close** | 批量四关 grade=F；无正向显著，不值得继续投入。 |
| LX17_STAND_ON_CONCAVE_PEAK | 站稳凹峰即将上攻 | **needs-more** | 批量四关 grade=C（方向未定，OOS 不显著）；需补 walk-forward/置换/成本敏感性。 |
| LX18_EXTREME_YIN_NEXT_YANG | 极阴次阳择幅建仓 | **close** | 已测 rejected（H26 → rejected（严格版仍负））；H24-H29 全部 rejected，G1 无正向显著。 |
| LX19_BULL_STOCK_VOLUME_SHAPE | 牛股量形 | **close** | 批量四关 grade=D；无正向显著，不值得继续投入。 |
| LX20_BULL_STOCK_THREE_ABSOLUTES | 牛股三绝 | **needs-more** | G1/G2/G5/G6/G7 通过，但 G3 50bp 净超额已转负（-1.28%）、G4 安慰剂未做 → 不可直接交易，按严格协议 needs-more；先做降换手与安慰剂，再谈影子盘。 |
| LX21_TEN_MONTH_PREGNANCY | 十月怀胎一朝分娩 | **close** | 批量四关 grade=F；无正向显著，不值得继续投入。 |
| LX22_CONCAVE_NOTCH_GOLD | 凹口淘金 | **close** | G3 30bp 已 -6.57%、G5 覆盖 42.1%>30% → close（张松已判『被成本吃光』，复核一致）。 |
| LX23_FUSE_TACTICS | 量学导火索战法 | **close** | 批量四关 grade=F；无正向显著，不值得继续投入。 |
| LX24_LIMIT_UP_GENE | 涨停基因（单个/组合） | **close** | 概念级战法（不可程序化），无假设可测；作为特征映射框架使用，不进入策略池。 |

## 四、关键裁决说明

### LX20 牛股三绝（唯一接近可交易的候选 → needs-more，不是 keep）
- G1：毛超额 t=7.35（q≈0）；G2：年度 2023-2026 4/4 同向；G5：覆盖 24.35%≤30%、容量 AUM≈187 亿；G6：市值×行业层内 t=9.76。
- **G3 不通过**：30bp 净 +2.03% → **50bp 净 -1.278%、80bp 净 -6.236%**（线性重算；换手年化成本 4.96% 吃掉全部 alpha）。
- **G4 未达**：只有 300 次日频 bootstrap，不是「同覆盖率随机信号 1000 次置换」→ 无法排除覆盖率/小市值代理。
- 结论：**不做交易**；先降换手（周频/持有 N≥10 日）+ 50bp 复测 + 1000 次置换，仍正才进影子盘。

### 为什么 S05（关闭）与 LX20 反向规避（needs-more）不矛盾
- S05 是**覆盖层**（对既有组合中触发股降配权重 w），叠加后 OOS 年化 -0.63%（t=-0.34）、1/4 年为正、50bp -2.04% → 关；
- LX20 反向规避是**独立等权全市场口径**（剔除触发股后与等权市场比），T+1 收盘执行 30bp 净 +2.03%，但 50bp 转负；
- 两者口径不同，必须独立裁决；S05 的失败说明「叠加到实盘组合」不可行，LX20 的勉强正超额只在低费率下成立。

### 双阴出货 LX12 / 凹口淘金 LX22
- 均在 G3（30bp 就为负：-24.6% / -6.57%）与 G5（覆盖 71.9% / 42.1%>30%）fail → close；与张松交付单已知问题 4/6 一致。

## 五、G7 数据审计（PIT/除权/停牌/ST/新股）

- **PIT**：H29 首版确认型形态用 T+1~T+3 确认信息但按形态日索引 → 前视 3 天；修正后 66/81→0/81 通过、黄金柱 OOS 由 +1.09%(t=7.27) 翻为 -0.34%(t=-2.62)。判例已制度化（确认日索引+次日入场）。
- **factor_lib 单测**：负 lag 仅允许出现在 forward_car；面板截断到 T 后重算因子值完全一致（无未来数据泄漏）。
- **除权**：adj_factor 复权价（后复权口径）；批量四关与反向规避均用复权收益。
- **停牌/涨跌停**：执行可交易性已量化（LX20 T+1 收盘：涨停不可买 1.47%、停牌 0.15%、一字跌停不可卖 0.02%）。
- **ST/新股**：股票池已剔除 ST/退市/上市<250 日/价格<2 元/成交额<5000 万。
- 结论：**pass-with-caveat**（无新前视/幸存者偏差级问题；简化实现的定义漂移已在 specs 标注）。

## 六、给张松的反馈

**有价值、继续**：
- 量学形态库作为**反向风险特征**有真价值：LX20 牛股三绝独立规避口径毛超额 t=7.35、层内 t=9.76（但 50bp 成本后转负，需降换手后才能交易）
- PIT 自纠判例（H29 前视假阳性）是全团队最有价值的制度产出：确认型形态必须按确认日索引
- 批量四关初筛 + 因子库统一接口（`fn(panel)->DataFrame` + PIT 单测）值得保留复用
- 『规避/风控』比『做多形态』更适合量学：事件长期负 α 可反向用作风险信号

**应停止投入**：
- 继续挖掘量学形态的**做多**alpha：H24-H29 + 13 个批量候选全部无正向显著，H29 81 组参数 0/81；
- C 级画线类战法（LX07/LX16/LX21/LX23）无法稳定程序化，停止投入；
- 未做多重检验/成本敏感性/安慰剂的『新假设快速上报』，建议一律先走四关再上报。

**下次交付必须补齐**：
- 任何策略先报：BH-FDR q、50bp 净超额、≥1000 次同覆盖率置换分位、容量 AUM
- walk-forward ≥3 folds + embargo；事件策略必须用确认日索引（PIT 判例）

## 七、可执行清单（进入影子盘候选 = 无）

- 本审计 **keep = 0 项**：没有任何策略同时通过 G1-G8。
- needs-more：LX20 反向规避、LX06 长阴短柱、S03、S04、book_backlog 中 2 项（LX05/LX17，grade C）。需要按各自 next_actions 补齐后重审；补齐前**不交易**。
- close：S01/S02/S05、LX22、LX12 + 其余 book_backlog 项（含全部已测 rejected 与 C 级不可计算战法）。

## 八、边界与局限

- 本审计基于**已有证据重算**：成本敏感性为 30bp 基础上的线性重算（未含冲击成本，偏乐观）；未重跑回测/LLM；G4 安慰剂在本次审计中未新做（证据缺失即判 not-met，从严）。
- G1 的 p 值由 t 近似（正态双侧）；日频序列存在自相关，BH 校正未做 block 修正 → q 偏乐观。
- 审计不构成投资建议；任何策略上线需影子盘+审批。
