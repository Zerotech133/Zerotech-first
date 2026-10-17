# 盯盘标准化能力规格 v1.0

## 1. 定位

本能力将“日线趋势 + 60 分钟回踩 + MACD 动能确认”固化为一个可配置、可回测、可审计的标准化盯盘能力。

它不是单一指标预警，也不是让 LLM 凭感觉判断买点，而是：

> **市场状态门控 → 个股趋势确认 → 回踩结构识别 → 小周期触发 → 风险预算 → 告警闭环**

能力归属：

- 项目：`zhice-os-v2` 盯盘机器人
- 技术域：Alerting & Ops / Signal Aftercare
- 中台形态：确定性方法插件 + 稀疏 LLM 仲裁 + Hermes/Cowbridge 学习闭环
- 默认动作：只生成候选信号，不直接下单

## 2. 设计原则

1. **大周期优先**：市场状态和日线趋势可以否决 60 分钟买点。
2. **信号与执行分离**：T 日收盘形成信号，最早 T+1 开盘执行模拟；严禁用信号收盘价成交。
3. **硬门槛与评分分离**：硬门槛负责排除不适用场景，评分负责排序，不用评分替代风险否决。
4. **确定性优先**：均线、MACD、量价、ATR、状态机全部可复现；LLM 只处理冲突和不完整信息。
5. **可解释**：每一票必须携带输入快照、方法票、原因、风险和版本号。
6. **先回测、再影子、后小规模验证**：没有样本外和成本后结果，不得宣称策略有效。

## 3. 标准能力契约

### 3.1 输入

每个标的、每个决策时点需要：

```text
market_snapshot:
  index_close, index_ma20, index_ma60
  index_ma60_slope, market_regime, breadth

symbol_daily:
  OHLCV（后复权，按时间升序）
  MA5, MA10, MA20, MA60
  MACD DIF, DEA, HIST
  ATR14, volume_ma5, volume_ma20

symbol_60m:
  OHLCV（按时间升序）
  EMA20, MACD DIF, DEA, HIST
  ATR14, volume_ma20

execution:
  signal_ts, next_tradeable_ts, price_limit_state
  t_plus_one, suspension, liquidity

optional:
  events, sentiment, memory_hints, position_value
```

### 3.2 输出

```json
{
  "capability_id": "pullback_resonance_cn",
  "capability_version": "1.0.0",
  "symbol": "600000.SH",
  "market": "CN",
  "action": "BUY_CANDIDATE",
  "confidence": 0.78,
  "score": 82,
  "state": "TRIGGER_CONFIRMED",
  "method_votes": [],
  "gates": {
    "market": "PASS",
    "trend": "PASS",
    "pullback": "PASS",
    "momentum": "PASS",
    "risk": "PASS"
  },
  "entry_plan": {
    "execution": "NEXT_BAR_OPEN",
    "first_tranche_ratio": 0.4,
    "confirm_tranche_ratio": 0.6
  },
  "risk_plan": {
    "stop_price": null,
    "risk_r": null,
    "max_position_ratio": 0.10
  },
  "reasons": [],
  "data_quality": {},
  "created_at": "..."
}
```

允许动作统一为：

```text
WAIT             数据不足、条件未完成或市场禁止
WATCH            接近成立，但尚未触发
BUY_CANDIDATE    硬门槛通过且达到触发分数
RISK_EXIT        已持仓且风险退出条件成立
HOLD             持仓继续观察
```

## 4. 状态机

```text
DATA_READY
   ↓
MARKET_ALLOWED ──否──→ WAIT_MARKET_RISK_OFF
   ↓
TREND_CONFIRMED ──否──→ WAIT_TREND
   ↓
PULLBACK_DETECTED ──否──→ WATCH_PULLBACK
   ↓
MOMENTUM_TRIGGERED ──否──→ WATCH_TRIGGER
   ↓
RISK_CHECKED ──否──→ BLOCKED_BY_RISK
   ↓
TRIGGER_CONFIRMED
   ↓
BUY_CANDIDATE → NEXT_BAR_OPEN → CONFIRM_OR_CANCEL
```

规则：

- 任何状态只能向前推进或回退到 `WAIT/WATCH`，不能跳过风险门。
- `BUY_CANDIDATE` 只表示“进入执行候选”，不等于无条件买入。
- 信号在收盘后生成，执行时点必须写入 `next_tradeable_ts`。
- 状态迁移必须保留审计记录，方便 Hermes 复盘。

## 5. 五个确定性方法插件

### 5.1 `market_regime_gate`

市场允许做多的最低条件：

```text
index_close > index_MA60
index_MA60[t] > index_MA60[t-5]
index_MACD_DIF > 0
```

风险状态任一成立即否决新增多头：

```text
指数连续2日收盘低于MA60
MA60斜率向下
指数 DIF < 0 且 DEA < 0
市场广度/情绪进入 risk_off
```

### 5.2 `trend_structure`

个股日线趋势条件：

```text
close > MA20
MA20 > MA60
MA20[t] > MA20[t-5]
MA60[t] >= MA60[t-5]
DIF > DEA
DIF > 0
```

说明：MA5 > MA10 > MA20 不设为绝对硬门槛。MA5/MA10 用于识别回踩节奏，MA20/MA60 用于定义中期趋势。

### 5.3 `ma_pullback`

有效回踩至少满足以下三项：

```text
最近5日最低价触及 MA5 ± 1.5%
或最近10日最低价触及 MA10 ± 1.5%
收盘未连续2日低于 MA20
回踩没有明显放量下跌
回踩后出现止跌K线
```

量价判定：

```text
volume / volume_ma20 <= 0.8     加分：抛压收缩
0.8 < ratio <= 1.2              中性：正常换手
ratio > 1.2 且收阴              否决：疑似出货
```

### 5.4 `macd_resonance`

日线只负责确认趋势动能：

```text
DIF > 0
DIF > DEA
HIST > 0 或 HIST 连续改善
```

60 分钟触发满足以下任一类：

```text
A类：DIF 上穿 DEA
     HIST 连续两根改善
     收盘重新站上 EMA20

B类：回踩低点未破
     收盘突破回踩结构高点
     volume / volume_ma20 >= 1.2
     收盘站上 EMA20
```

底背离只作为加分项，不作为唯一触发条件，避免主观识别和未来函数。

### 5.5 `risk_guard`

初始止损参考：

```text
结构低点 - 0.5 × ATR14
MA20 × 0.98
```

止损距离超过入场价 6% 时，直接放弃信号。

单笔风险采用风险预算：

```text
单笔最大亏损 = 账户权益 × risk_budget
数量 = 单笔最大亏损 / (入场价 - 止损价)
```

策略默认参数：

```text
risk_budget: 0.005~0.01
single_symbol_max: 0.10
industry_max: 0.25
risk_off_total_max: 0.30
risk_on_total_max: 0.60
```

## 6. 评分模型

硬门槛通过后进行 100 分评分：

| 维度 | 分值 |
|---|---:|
| 市场处于允许做多状态 | 20 |
| 个股站上 MA20、MA60 | 15 |
| MA20、MA60 斜率向上 | 15 |
| 日线 MACD 位于零轴上方并改善 | 15 |
| 日线完成有效回踩 | 10 |
| 回踩没有放量出逃 | 10 |
| 60 分钟 MACD 修复 | 10 |
| 60 分钟站回 EMA20 | 5 |

执行阈值：

```text
<65       WAIT
65-74     WATCH
75-84     BUY_CANDIDATE，首仓30%-40%
>=85      BUY_CANDIDATE，首仓最高50%
```

以下任一条件具有否决权：

```text
日线 MACD DIF < 0
MA60 斜率向下
回踩出现放量阴线
距离前方压力小于 1.5R
市场 regime_risk_off
数据质量不达标
```

## 7. 执行与退出标准

### 7.1 执行

- 第一笔：信号确认后，计划仓位 30%–50%。
- 第二笔：下一根 60 分钟 K 线未跌回 EMA20 下方，且未跌破触发 K 线低点，再补足剩余计划仓位。
- 不追高：若执行时价格距离 MA5 超过 3%，信号过期。
- A 股严格模拟 T+1、涨跌停、停牌和不可成交。

### 7.2 止盈

```text
达到 1.5R：减仓1/3，止损上移至成本附近
达到 2R：再减仓1/3
剩余仓位：跌破MA20、60分钟EMA20或出现趋势反转时退出
```

### 7.3 退出

```text
日线连续2日收盘低于MA20
日线 MACD DIF 下穿 DEA 且绿柱扩大
MA20 斜率转负
市场进入 risk_off
60分钟跌破EMA20且反弹不能收回
```

### 7.4 时间止损

买入后 8 个交易日没有达到 +1R，且价格持续低于 MA5，退出并记录为“时间失效”。

## 8. 与现有 Agent 中台的映射

建议新增一个标准 Playbook：

```yaml
id: pullback_resonance_cn
market: CN
version: 1.0.0
methods:
  - id: market_regime_gate
    weight: 1.0
  - id: trend_structure
    weight: 1.0
  - id: ma_pullback
    weight: 1.0
  - id: macd_resonance
    weight: 1.0
  - id: risk_guard
    weight: 1.0
arbitration:
  quorum_min_votes: 4
  tie_margin: 0.10
  regime_veto: true
execution:
  signal_to_execution: next_bar_open
  confirmation_bars: 1
  max_signal_age_bars: 1
risk:
  single_symbol_max: 0.10
  risk_budget: 0.0075
```

与现有代码接缝：

- `methods/base.py`：沿用 `WatchContext`、`MethodVote`。
- `methods/__init__.py`：注册五个标准方法。
- `playbooks/spec.py`：加载 `pullback_resonance_cn.yaml`。
- `arbitration/regime_arbiter.py`：保留风险状态否决和分歧仲裁。
- `runtime/watch_daemon.py`：按 Playbook 分组，注入日线/60分钟上下文。
- `backtest/engine.py`：采用 T 日信号、T+1 执行，输出方法票和状态迁移。
- `learning/graduation.py`：按 `method@playbook` 分开学习，不能让不同打法共享毕业权重。
- `learning/card_bridge.py`：卡片展示评分、门控结果、触发原因、止损位置和信号有效期。

LLM 不参与普通信号计算，只在以下情况介入：

```text
方法票冲突
市场 regime 不明确
事件与技术方向冲突
风险收益比接近边界
数据缺口影响结论
```

## 9. 数据质量门

信号输出前必须检查：

```text
日线有效K线 >= 80根
60分钟有效K线 >= 120根
时间戳单调递增且无重复
OHLCV 无明显负值/零值异常
指标预热完成
复权口径一致
最近数据不超过允许延迟
停牌、涨跌停状态已标注
```

任一关键数据缺失时：

```text
不补默认值
不调用 LLM 猜测
输出 WAIT + DATA_GAP
记录缺口原因
```

## 10. 回测验收

正式结论必须同时报告：

```text
交易笔数
胜率
平均盈利/平均亏损
Profit Factor
最大回撤
年化收益和波动
Sharpe / Sortino / Calmar
交易成本后收益
市场状态分段表现
参数敏感性
样本内外差异
```

最低验收门槛：

```text
样本外交易笔数 >= 200
Profit Factor > 1.2
平均盈亏比 >= 1.5
最大回撤 <= 15%
样本外表现 >= 样本内表现的60%
参数上下扰动20%后结论不崩溃
```

验证流程：

1. 样本内开发，不得反复使用样本外调参。
2. 样本外验证，占比至少 20%。
3. Walk-forward 滚动验证，训练/测试之间设置 embargo。
4. 蒙特卡洛重排交易顺序，观察回撤尾部。
5. 影子运行 1–3 个月。
6. 小资金验证，观察滑点、成交率、延迟和信号衰减。

A 股回测必须模拟：

- 后复权数据；
- 佣金、印花税、过户费；
- 滑点和流动性冲击；
- T+1；
- 涨跌停不可成交；
- 停牌；
- 退市和 ST 历史样本；
- 数据缺口。

## 11. 标准化告警卡片

每张卡片至少展示：

```text
标的 / 市场 / 信号时间
能力名称 / 版本
市场状态：允许 / 禁止
趋势门：通过 / 未通过
回踩门：通过 / 未通过
MACD门：通过 / 未通过
量价门：通过 / 未通过
总分及评分明细
方法票及原因
建议执行时点：下一根K线/下一交易日
结构止损与最大风险
信号过期时间
确认 / 忽略按钮
```

人工点击后写入：

```text
decision_id
ack_action
ack_user
ack_ts
entry_style
reason_code
```

T+5、T+10、T+20 回填前向收益，进入 `LearningCase` 和 Hermes/Cowbridge 记忆。

## 12. 能力版本与商业化

能力包分为三层：

| 版本 | 能力 | 适用 |
|---|---|---|
| Basic | 日线趋势 + MA20 回踩 + 风险门 | 低频观察 |
| Pro | Basic + 60分钟 MACD + 量价确认 | 标准波段盯盘 |
| Institutional | Pro + 市场状态 + 事件/情绪 + 独立毕业权重 | 机构风格客户 |

模型档位与能力包解耦：

- 确定性方法层：所有档位必备；
- 本地模型仲裁：Pro 以上启用；
- Hermes 长期记忆与案例学习：Institutional 启用；
- 飞书交互、审计和自愈：平台基础能力，不应作为信号正确性的替代品。

## 13. 最终定盘

标准化能力的唯一主流程定为：

```text
市场允许做多
→ 个股 MA20/MA60 多头趋势
→ 日线有效回踩且无放量出逃
→ 日线 MACD 位于零轴上方并改善
→ 60分钟 MACD 修复或结构突破
→ 风险收益比合格
→ 下一根K线确认
→ 分批执行
→ 结构/ATR/时间止损
→ 人工确认回写
→ T+5 前向收益复盘
```

三条硬铁律：

1. **MA60 方向向下，不做多。**
2. **日线 MACD 位于零轴下方，不做标准回踩买入。**
3. **回踩放量下跌，不把它解释为洗盘。**

这套能力的正确定位是“趋势回踩入场过滤器”，不是预测系统，也不是收益承诺。它首先要证明的是：在成本、滑点、T+1 和数据缺口都被正确模拟后，是否仍具备稳定的正期望。
