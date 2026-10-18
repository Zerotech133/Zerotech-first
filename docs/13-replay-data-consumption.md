# 13 · 复盘数据的消费

## 1. 问题

复盘数据产生后，谁来消费？

- 人工看？
- 机器学习？
- 微调策略参数？
- 是否需要专门训练小模型参与系统？

## 2. 结论先行

复盘数据应当被“多层消费”，但 **V2 第一阶段先做规则化学习和参数优化**，暂不训练小模型。

先把数据管道和基础分析做好，等样本量足够后再引入 ML。

## 3. 复盘数据消费层次

### 3.1 第一层：人类复盘

- 每日/每周复盘报告
- 单笔交易归因
- 策略绩效看板
- 错误案例集
- 行为建议

### 3.2 第二层：规则化学习

- 统计胜率、盈亏比、持仓周期、回撤分布
- 识别策略衰减
- 识别入场/出场规则失效
- 生成规则化改进建议
- 参考 Shadow Account 思路，从交易流水提取 if-then 规则

### 3.3 第三层：参数优化

- 基于历史复盘结果做参数敏感性分析
- 使用网格搜索 / 贝叶斯优化
- 但必须严格 out-of-sample / walk-forward 验证
- 参数优化结果不能直接自动上线，仍需人工审批

### 3.4 第四层：机器学习

适合的方向：

| 方向 | 说明 | 优先级 |
|---|---|---|
| 策略衰减检测 | 用时序模型识别绩效下滑 | 高 |
| 市场状态分类 | 区分趋势/震荡/高波动，作为策略开关 | 中 |
| 交易质量评分 | 对候选交易打“质量分”，辅助风控 | 中 |
| 异常行为检测 | 识别异常成交、滑点、数据问题 | 中 |
| 参数推荐 | 根据市场状态推荐参数 | 低 |
| 自动策略生成 | 用模型生成新策略 | 低，需强约束 |

### 3.5 第五层：大模型/LLM 辅助

- 生成复盘叙事
- 解释归因
- 把结构化数据转为人类可读报告
- 不作为自动交易决策核心

## 4. 复盘数据标准化

为了让上层消费，复盘数据应标准化为：

```python
class ReplayCase:
    id: str
    market: str
    strategy_version_id: int
    instrument: Instrument
    entry_time: datetime
    exit_time: datetime
    direction: Direction
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    holding_period: timedelta
    market_regime: str
    attribution: dict
    exit_reason: str
    slippage: Decimal
    quality_flags: list[str]
    review_status: str
    created_at: datetime
```

## 5. 是否需要小模型？

### 5.1 短期不需要

原因：

- 样本量不足，尤其是 US/HK/CRYPTO。
- 交易系统最重要的是稳定和可解释。
- 小模型容易过拟合，且会引入不可解释风险。
- 规则化学习 + 参数优化已经能覆盖大部分需求。

### 5.2 中期可以引入

当满足以下条件时再引入：

- 单策略样本 > 500 笔
- 有足够市场状态标签
- 有严格的回测框架
- 模型只做“辅助决策”，不直接下单

建议的小模型方向：

- 市场状态分类器
- 策略衰减预警
- 交易质量排序

### 5.3 大模型定位

大模型/LLM 定位为“研究助手”，不是“交易大脑”。

## 6. 数据流

```
模拟盘/实盘成交
      ↓
PositionClosed 事件
      ↓
ReplayService 生成 ReplayCase
      ↓
保存复盘事实表
      ↓
多路消费：
  - 人工复盘
  - 规则学习
  - 参数优化
  - 机器学习数据集
  - LLM 报告
```

## 7. 与研究引擎的关系

- 复盘数据可以导出为通用交易日志。
- 外部研究引擎的交易日志分析 / 影子策略提取能力，作为“规则学习”的一种实现。
- V2 应该把“学习能力”抽象为 `LearningProvider`，外部研究引擎只是其中一个 Provider。

```python
class LearningProvider(Protocol):
    def analyze_journal(self, journal_path: str) -> LearningInsight: ...
    def extract_rules(self, journal_path: str) -> list[RuleSpec]: ...
```

## 8. 落地建议

V2 第一阶段做：

1. 完整保存标准化 ReplayCase。
2. 提供绩效统计和衰减预警。
3. 提供导出功能：CSV / Parquet / 通用交易日志。
4. 提供人工复盘工作台。
5. 预留 ML 特征表。

V2 第一阶段不做：

1. 不训练小模型。
2. 不做自动策略生成。
3. 不做基于模型的黑盒交易决策。

第二阶段再做：

1. 市场状态分类模型。
2. 策略衰减模型。
3. 交易质量评分。
4. 参数自动建议。
