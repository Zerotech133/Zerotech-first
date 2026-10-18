# 14 · 无感研究体验

## 1. 需求

策略研究目前调用外部研究引擎。用户希望使用过程中“无感”，不感知外部系统的存在。

## 2. 设计原则

- 用户看到的是“系统研究能力”，不是“某个外部系统”。
- 内部可以继续使用外部研究能力，但对外统一品牌为“智策研究引擎”。
- UI、后端日志、文档、命名逐步去掉外部研究引擎相关字样。
- 如果外部研究引擎不可用，系统应优雅降级，不暴露底层技术细节。

## 3. 对外统一界面

用户侧词汇统一为：

| 用户看到 | 内部实际 |
|---|---|
| 策略研究 | ResearchService |
| AI 策略实验室 | ResearchGateway |
| 回测 | BacktestService |
| 策略生成 | StrategyProposalService |
| 策略导入 | StrategyRegistrar |
| 影子策略学习 | LearningProvider |

不在用户界面、后端日志、文档、命名中出现：

- 外部研究引擎原品牌名
- MCP
- venv
- LangChain / LangGraph
- run_dir
- contract.py

## 4. 内部架构

```
┌───────────────────────────────────────────┐
│              Frontend                     │
│  策略研究台 / 回测 / 策略提案 / 注册       │
└──────────────────┬────────────────────────┘
                   ▼
┌───────────────────────────────────────────┐
│            Research API                    │
│  /research/start                           │
│  /research/status                          │
│  /research/proposals                       │
│  /research/backtest                        │
└──────────────────┬────────────────────────┘
                   ▼
┌───────────────────────────────────────────┐
│          ResearchGateway                    │
│  - 统一输入/输出模型                        │
│  - 调度内部 Provider                       │
└──────────────────┬────────────────────────┘
                   ▼
┌──────────────────┬──────────────┬─────────────┐
│ ExternalResearch │ LocalBacktest│ ManualImport│
│ Provider (内部)  │ (可选)        │ (可选)      │
└──────────────────┴──────────────┴─────────────┘
```

## 5. 统一数据模型

所有 Provider 返回统一结构：

```python
class ResearchReport:
    id: str
    title: str
    market: str
    status: str
    created_at: datetime
    summary: str
    backtest_perf: BacktestPerf | None
    strategy_spec: StrategySpec | None
    source: str  # internal: external_research / local / manual，用户不可见
```

```python
class BacktestPerf:
    sharpe: float
    total_return: float
    annual_return: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    sample_start: date
    sample_end: date
```

## 6. 无感流程

```
用户：我要研究 BTC 均值回归
  ↓
前端：打开“AI 策略实验室”
  ↓
ResearchService 创建研究任务
  ↓
ResearchGateway 内部决定使用哪个 Provider
  ↓
用户看到：研究进行中、进度、结果
  ↓
结果以“策略提案”形式展示
  ↓
用户点击“申请注册”
  ↓
进入统一策略审批流程
```

## 7. 降级策略

- 外部研究引擎不可用：
  - 如果已有本地回测能力，则切本地。
  - 如果都不可用，显示“研究服务暂时不可用”，不暴露底层报错。
- 用户无需知道底层是哪个 Provider。

## 8. 迁移路径

V2 内部可以分两步：

1. 先保留现有外部研究适配模块，但对外 API、日志、文档、命名逐步替换。
2. 再抽象 `ResearchGateway`，把外部研究能力作为第一个 Provider。
3. 后端模块名从 `research_vibe` 逐步调整为 `research_gateway` / `research_provider` 等中性命名。

## 9. 对用户的价值

- 降低心智负担。
- 系统看起来是一个整体。
- 未来即使替换研究引擎，用户界面也不需要变。
