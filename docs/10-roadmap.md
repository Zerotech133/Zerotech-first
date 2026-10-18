# 10 · 实施路线图

## 总原则

- 每一阶段都保持系统可用。
- 不推倒重来。
- 先立边界，再迁逻辑，最后优化体验。

## Phase 0：架构基线

目标：建立 V2 的工程骨架。

- 创建 Python 包结构
- 建立 Domain Core
- 定义 Ports & Adapters
- 引入 MarketContext
- 建立事件总线骨架
- 建立 CI/测试基线

交付物：

- 空但可运行的模块化单体
- 目录规范
- 领域层测试框架
- 事件总线雏形

## Phase 1：市场行情与实时页面

目标：先让“看行情”体验达到 V2 标准。

- 统一 MarketDataProvider
- 实现 REST K线接口
- 实现 WebSocket/SSE 推送
- 前端行情列表/详情/分时/多周期K线
- 多市场行情数据补全

## Phase 2：交易闭环重建

目标：把 V1 的候选/计划/风控/执行/持仓迁移到新架构。

- StrategySpec 统一策略
- CandidatePipeline
- TradePlanService
- RiskService
- BrokerPort + SimulatedBroker
- PositionService
- 事件发布：开仓/平仓/风控

## Phase 3：复盘学习报告

目标：补齐完整生命周期。

- ReplayService
- LearningService
- ReportService
- PromotionService
- 全部通过事件订阅接入
- CN 行为与 V1 对齐

## Phase 4：多市场补齐

目标：US/HK/CRYPTO 达到 CN 同等级闭环。

- learning/promotion/report 多市场化
- US 财务数据接入
- HK 数据源补齐
- CRYPTO 完整闭环
- 跨市场统一视图

## Phase 5：实时与体验

目标：产品化体验。

- WebSocket 全量接入
- 前端 feature 化
- 自选股
- 移动端适配（可选）

## Phase 6：实盘与多用户

目标：面向真实使用。

- 真实券商 adapter
- 订单状态机/对账
- 用户/RBAC
- 操作审计
- 灰度开关

## 验收标准

- 同一套核心代码驱动四市场
- 新增市场不需要复制业务代码
- 新增策略不需要改主流程
- 模拟/实盘只差 BrokerAdapter
- 复盘/学习/报告不侵入主流程
- 实时行情页面具备分时、多周期K线
