# 机构风格 Playbook 卡 v0.1

> 定位：把《机构风格交易体系_选股止盈止损》转译为 agent_platform 第一个真实打法包（Playbook）。
> 本文解决源文档审计（2026-10-17 01:20）发现的定义缺口 ①②③，并给出注册规格。
> 市场：CN（依赖 A 股情绪数据，CRYPTO 分片不装载本包）｜来源：国金证券视频号 20 条研究

---

## 一、缺口裁定（审计 ①②③ 的拍板结果）

### ① 漏斗语义：L1 = 硬门槛（gate），权重只做圈内排序

**裁定**：L1 时代主线是**分类门槛**不是加分项——无 L1 的票最高只能进观察池，永远进不了核心池；评分权重（竞争力 40% + 景气 30% + 催化 20% + 叙事 10%）**只在通过 L1 门槛的候选内做排序**。

| 语义 | 作用层 | 行为 |
|---|---|---|
| L1 = gate | 仓位分类（下表） | 无故事 → 封顶观察仓 |
| 权重 = rank | 池内排序 | 决定核心池内部买谁先、仓位厚薄 |

两套语义各归其位，矛盾消除。回测口径同步：核心池样本必须 L1=true。

### ② 仓位映射完整决策表（16 组合全覆盖）

| L1 叙事 | L2 景气 | L3 质地 | L4 催化 | 仓位档 | 风控约束 |
|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ | **核心仓** | 可重仓（四层全中） |
| ✅ | ✅ | ✅ | ❌ | 进攻仓 | 等买点（L4 出现即升核心） |
| ✅ | ✅ | ❌ | ✅ | 进攻仓 | 质地缺口 → 上限半核心仓 |
| ✅ | ❌ | ✅ | ✅ | 游击仓 | 快进快出（景气未证实） |
| ✅ | ✅ | ❌ | ❌ | 观察仓 | 等质地与催化 |
| ✅ | ❌ | ✅ | ❌ | 观察仓 | 等景气 |
| ✅ | ❌ | ❌ | ✅ | 游击仓 | 纯题材，纪律止损从严 |
| ✅ | ❌ | ❌ | ❌ | 不入池 | — |
| ❌ | 任意 | 任意 | 任意 | **封顶观察仓**（gate） | L1 缺席永不进核心/进攻 |
| ❌ | ✅ | ✅ | ✅ | 观察仓* | "有业绩无故事"单独标注，季度复核 L1 |

*观察仓*（带星）= 潜在风格切换候选（市场叙事轮动时最先升级）。

### ③ 持仓类型判定：建仓时由入场信号源盖章，终身不改

**裁定**：`entry_style` 在建仓瞬间由**触发信号类型**写入，之后止盈式跟随 entry_style 执行，不做二次判判（避免持仓中改口）：

| 入场触发信号 | entry_style | 绑定止盈式 |
|---|---|---|
| L4 事件落地（公告/中标/涨价） | 事件仓 | ③ 事件窗口止盈 |
| 趋势突破/结构买点（缠论三买等） | 趋势仓 | ① 破位移动止盈 |
| 情绪周期启动位入场 | 情绪仓 | ② 情绪周期止盈 |
| 区间下沿/箱体低吸 | 波段仓 | ④ 分批止盈 |

边界规则：一笔仓只有一个 entry_style；混合触发时按**最先触发的信号**归类并留痕（votes_json 记录触发链）。

---

## 二、Playbook 注册规格（agent_platform/playbooks/org_style_cn.yaml）

```yaml
playbook_id: org_style_cn
name: 机构风格·事件驱动
market: CN
snapshot_hz: 60            # CN 盘中 1 分钟档（快照 10s 时代价过高，事件驱动不需 5s）
bar_freq: 15m              # 主决策周期 15m；趋势确认看 1d
methods:                   # 方法插件（weight 初始值 = 源文档优先级直译）
  - {id: event_window,  weight: 1.5, role: 催化倒计时（window_days 到期 → SELL 倾向）}
  - {id: trend_break,   weight: 1.2, role: 20 日线/趋势线破位（收盘确认 → SELL）}
  - {id: chan_structure,weight: 1.0, role: 缠论结构确认（背驰/三卖 → SELL）}
  - {id: sentiment_regime, weight: 0.8, role: 情绪周期高位（涨停家数回落/炸板率抬升 → 减仓票）}
arbitration:
  quorum_min_votes: 2      # 事件仓允许双票即决（快）
  tie_margin: 0.18         # 稍宽于默认（事件时效优先于精确）
  regime_veto: true        # 情绪冰点期（sentiment risk_off）SELL 权重 ×1.5
graduation:
  tier: CN_DEFAULT         # n≥30；sentiment_regime 数据冷启动期 n≥6 降档
  attribution: per_method  # 按 (method, playbook) 独立毕业
entry_style_binding: true  # 建仓盖章 entry_style，止盈式跟随
risk:
  per_trade_risk_pct: 2.0  # 单笔风险敞口 ≤ 总资金 2%（非仓位市值）
  no_averaging_down: true  # 禁摊平（逆向过滤器）
```

**与源文档的参数直译对照**：window_days（order_tender 30d / price_hike 45d / theme 10d）→ event_window 方法参数；"破 20 日线减 50%" → trend_break 触发线；情绪 5 特征（补全审计缺口：涨停家数、炸板率、连板高度、**成交额分位、赚钱效应**——后两个从源文档复盘语料补齐）→ sentiment_regime 输入。

---

## 三、商业化挂载点

- 本 playbook = E3 专业档首发 SKU（事件驱动型客户）；
- 与模型档位正交：org_style_cn × {E1 规则-only / E2 本地 7b / E3 云端仲裁} 三价；
- entry_style 留痕 = 客户可复盘的"机构式决策审计"卖点；
- 下一个 playbook 候选：北斗股术 C1 缠论+量能包（打法总结文档已备好素材）。

## 四、待办

- [ ] t06-dev 落地 Playbook 层（PlaybookSpec/映射/分组/毕业键扩展）
- [ ] CN 市场情绪数据源接入评估（涨停家数/炸板率——问财或东财口径）
- [ ] trend_break / event_window / sentiment_regime 三个新方法插件实现（chan_structure 复用现有）
- [ ] 回测验证：org_style_cn 对 2026-09~10 A 股窗口的事件仓 T+5 前向收益
