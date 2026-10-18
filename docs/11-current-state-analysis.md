# 11 · 现状分析（V1 → V2 的依据）

## 1. 当前系统构成

```
/Users/join/Desktop/zhice/
  zhice/backend/      多市场交易闭环、FastAPI、调度器、前端
  vibe-trading/       研究/回测 Agent
  docs/               融合设计、实施计划、评审记录
```

## 2. 已具备的能力

- CN 市场完整闭环
- US/HK/CRYPTO 多市场框架
- MarketProfile 市场差异抽象
- 独立数据库与调度器
- StrategySpec 策略物化
- vibe MCP 研究融合
- 模拟交易、风控、复盘、学习
- 前端 4 工作区导航
- 实时快照行情、分时图、多周期K线（最近已加）

## 3. 主要问题

### 3.1 领域层空壳

`domain/` 下没有实际业务逻辑，规则散落在 `modules` 和 `workflows`。

### 3.2 上帝模块

- `intra_day_workflow.py` 职责过多
- `workbench.py` 路由/编排过大
- `daily_increment.py` 混合多种数据职责

### 3.3 CN 与多市场不均衡

- 学习闭环仅 CN
- 晋升检查仅 CN
- 晨报/晚报仅 CN
- 内置策略多为 A股专属
- 基本面数据仅 CN 完善

### 3.4 执行层未抽象

- 只有 `simulated_broker`
- 没有统一 BrokerPort
- 未来接实盘需要重构

### 3.5 实时性依赖轮询

- 行情页 8~15 秒轮询
- 没有 WebSocket/SSE
- 对实时行情体验不友好

### 3.6 前端测试与导航漂移

- 已有 2 个测试失败
- 页面与 feature 边界不清晰

### 3.7 数据访问散落

- 大量直接 `get_db_session(market)`
- 缺少 Repository 封装
- 领域逻辑与 SQL 耦合

## 4. V2 要解决的根因

| 问题根因 | V2 对策 |
|---|---|
| 领域层空壳 | 建立 Domain Core，业务规则下沉 |
| 上帝模块 | Pipeline + UseCase 拆分 |
| 多市场不均衡 | MarketContext + 统一生命周期 |
| 执行不可替换 | BrokerPort |
| 轮询体验 | WebSocket/SSE |
| 数据访问散落 | Repository |
| 策略不统一 | StrategySpec + Executor 注册表 |
| 副作用侵入主流程 | 领域事件 |

## 5. 结论

V1 证明了业务可行性和闭环价值，V2 重点解决工程结构、多市场一致性和可扩展性。
