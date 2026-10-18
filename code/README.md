# zhice-OS V2

> 智策操作系统第二版：独立目录、重新设计架构、保留现有系统全部业务能力。

## 定位

zhice-OS V2 不是对现有 `zhice/backend` 的小修小补，而是在不抛弃已有业务资产的前提下，重新设计的一套**模块化单体 + 领域内核 + 市场插件 + 事件驱动**的量化交易操作系统。

V1 已经验证了：

- 多市场闭环可以跑通
- 策略可以物化为确定性 `StrategySpec`
- 模拟交易、风控、复盘、学习可以形成闭环
- 外部研究引擎可以作为研究大脑

V2 的目标是：

- 把 V1 的“能跑”升级为“结构清晰、边界明确、易于扩展”
- 让 CN / US / HK / CRYPTO 成为同一套内核的四个市场实例
- 让研究、回测、策略注册、模拟交易、实盘交易、复盘学习成为统一流水线
- 让实时行情、多周期K线、WebSocket 推送成为一等能力

## 目录说明

```
zhice-os-v2/
  README.md
  docs/
    00-overview.md         总体愿景与设计原则
    01-architecture.md     目标架构
    02-domain-model.md     领域模型
    03-market-adaptation.md 多市场设计
    04-strategy-system.md  策略系统
    05-execution-broker.md 执行与券商抽象
    06-event-driven.md     事件驱动架构
    07-data-platform.md    数据平台
    08-realtime.md         实时行情
    09-frontend.md         前端设计
    10-roadmap.md          实施路线图
    11-current-state-analysis.md  V1 现状分析
    12-trade-approval-and-execution.md  统一交易推送/授权/执行
    13-replay-data-consumption.md       复盘数据消费
    14-seamless-research-experience.md  无感研究体验
    15-palantir-inspired-design.md       Palantir 思想引入：本体/行动/治理
    16-implementation-status.md          实施状态
    17-deployment.md                     本机部署
  adr/
    0001-independent-modular-monolith.md 独立系统与模块化单体架构决策
```

## 已确认设计决策

- 自动跟单开关按“市场 + 策略”分别配置，默认关闭。
- IM 授权渠道预留，后期版本实现。
- 模拟交易可自动执行；实盘交易默认必须人工授权，或人工开启实盘自动跟单。
- V2 第一阶段复盘数据先做规则化学习和参数优化，不训练小模型。
- UI、后端日志、文档、命名逐步去掉外部研究引擎相关字样。

## 当前状态

- 已完成 V2 独立目录、独立 Git 仓库初始化。
- 已完成 Palantir 思想融合设计文档。
- 已建立 Python 工程骨架。
- 已落地第一版领域层原型：
  - Ontology 对象与 Action
  - MarketContext / MarketProfile
  - StrategySpec / StrategyExecutor
  - BrokerPort / ExecutionMode
  - ApprovalRequest / ApprovalCenter
  - EventBus / DomainEvent
- 当前测试：37 passed
- 已支持券商实盘开关配置 API
- 已支持多账户 API
- 已支持告警中心 API
- 已支持策略晋升 / 降级 / 自动审查 API
- 已支持策略衰减监控 `/api/v1/reports/decay`
- 已支持签名 Token 登录与 `/auth/me` 当前用户接口
- 持久化支持 memory / sql 两种后端切换（`PERSISTENCE_BACKEND=memory|sql`）
- 前端行情页已接入 WebSocket 实时行情
- 前端已提供 K线页与多周期切换（1m/5m/15m/30m/60m/day/week）
- K线图支持 MA5 / MA10 / RSI 指标与十字光标
- 前端已提供登录页与 Token 保存
- 前端已提供 AI 研究助手页
- 前端已提供学习报告页
- 前端已提供系统状态页
- 前端已提供告警中心页
- 前端已提供组合总览页与账户页
- 已提供 FastAPI 应用入口与基础 REST API
- 已提供最小 React 前端骨架
- 已提供 Dockerfile / docker-compose

## 运行

> **默认账号**：admin / **首次初始化密码**：`123456`（仅开发环境）。
> `ADMIN_PASSWORD` 是 SQL 模式首次 bootstrap 管理员时使用的密码；管理员已存在后，应用重启不会覆盖其数据库密码。
> 生产首次部署前必须修改 `.env.local` 中的 `ADMIN_PASSWORD` 和 `SECRET_KEY`。

### 后端

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make test
make dev-api
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### Docker

完整部署包含四个服务：`postgres`、`migrate`（一次性跑完退出）、`api`（端口 8002）、`scheduler`（APScheduler 四市场全部交易任务）。

```bash
# 首次部署（或代码更新后）：重新构建并启动所有服务
docker compose up --build

# 后续启动（镜像已构建，无代码变更）
docker compose up -d

# 仅重跑迁移（如新增了 alembic revision）
docker compose run --rm migrate
```

API 运行在 `http://localhost:8002`，Swagger 文档见 `http://localhost:8002/docs`。

> **本地开发（非 Docker）**：`make dev-api` 同样使用端口 8002。
> 若要避开其他占用，可手动指定端口：
> `PYTHONPATH=src .venv/bin/uvicorn zhice_os_v2.api.main:app --reload --port 8102`
> 并同步设置 `VITE_PROXY_TARGET=http://localhost:8102`。

## 已完成功能（MVP）

- 多市场 Profile：CN / US / HK / CRYPTO
- 策略统一模型：StrategySpec / StrategyExecutor
- 候选生成：支持 momentum / ma_cross / rsi
- 交易计划 / 风控 / 授权 / 执行 / 持仓 / 复盘 / 学习 全链路 Pipeline
- 模拟券商 SimulatedBroker
- 事件总线 EventBus
- Palantir 式 Ontology / Action / Provenance
- FastAPI REST API
- K线重采样：1m / 5m / 15m / 30m / 60m / day / week
- 前端 React 基础页面：行情 / 策略 / 授权 / 持仓 / 复盘
- SQLAlchemy ORM 模型
- Docker / docker-compose / CI

## 后续待办

- 接入 PostgreSQL 持久化 Repository
- 接入真实数据源
- 实现 WebSocket/SSE 实时推送
- 实现真实券商 Broker Adapter
- 完善 IM 授权渠道
- 完善用户/RBAC
- 从原版系统复制历史数据与可复用资产

## 核心思想

1. **不推倒重来**：V1 的数据、功能、策略、API、前端资产全部保留，V2 是重新组织边界。
2. **模块化单体**：不盲目微服务，保持部署简单、事务一致。
3. **领域内核**：交易闭环的核心逻辑与市场无关，通过 `MarketContext` 注入市场差异。
4. **策略统一**：所有策略都走 `StrategySpec + StrategyExecutor` 统一通道。
5. **事件驱动**：复盘、学习、报告、通知、审计都通过领域事件解耦。
6. **端口适配器**：数据库、数据源、券商、通知、研究大脑都是可替换的端口。
