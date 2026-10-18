# 15 · Palantir 思想引入：本体、行动与治理

> 说明：本文基于 `/Users/join/Desktop/zhice/zhice-os-v2/Palantir完整调研总报告.md` 原文校准。
> 原文核心结论：Palantir 本质是“面向复杂组织与高责任场景的决策操作系统公司”，最值得借鉴的是它把数据、对象、AI、动作、审计、反馈组织成闭环系统的方法论。

## 1. 为什么要引入 Palantir 思想

Palantir 的核心不是“某个具体功能”，而是一套方法论：

- 以 **本体（Ontology）** 连接数据、对象、关系和行动。
- 以 **数据管道（Pipeline）** 保证数据可靠、可溯源。
- 以 **行动（Action）** 驱动业务闭环，而不是让人直接操作数据库。
- 以 **AI/LLM 辅助** 理解数据、生成建议，但关键动作仍由人授权。
- 以 **权限与治理（Governance）** 保证每一步都可控、可审计。

这正好适合 zhice-OS V2 的定位：

```
多市场量化交易系统 = 数据本体 + 交易对象 + 业务行动 + AI 研究 + 严格审批
```

## 2. 核心思想映射

| Palantir 思想 | zhice-OS V2 对应 |
|---|---|
| Ontology 本体 | Instrument / Strategy / Order / Position / ReplayCase 等统一对象模型 |
| Object-centric 对象中心 | 前端以“对象”和“行动”为中心，而不是以“页面/表格”为中心 |
| Data Fusion 数据融合 | 多市场多源数据统一接入、清洗、去重、标准化 |
| Foundry Pipeline 数据管道 | 可追溯、可重跑、带质量门禁的数据流水线 |
| Action 行动 | 下单、审批、拒绝、晋升、回测、注册等统一业务行动 |
| AIP / LLM Agent | 研究助手、复盘解读、策略建议，但不下达未经授权的交易 |
| Governance 治理 | 权限、审计、审批、熔断、数据血缘 |
| Provenance 数据血缘 | 每个 K 线、因子、策略、成交都能回溯来源 |
| Slate / Workspace 工作台 | 交易台、研究台、资产台、系统台基于本体构建 |

## 3. 原文关键思想校准

根据 `Palantir完整调研总报告.md`，最值得引入 V2 的七条原则：

1. **统一对象模型（量化版 Ontology）**
   - 不能把数据、策略、账户、风控、AI、复盘割裂成孤岛。
   - 必须把 `Stock / MarketState / Strategy / Signal / OrderIntent / Position / RiskEvent / ReviewCase / LearningTask` 做成系统一等公民。

2. **AI 不停留在建议层，要进入受控执行层**
   - AI 要读取真实交易上下文、账户约束、风控规则、仓位状态。
   - AI 输出结构化建议，而不是空泛自然语言。
   - 每次 AI 参与都有审计和回放。
   - 高风险动作必须人工确认。

3. **闭环比单点能力重要**
   - 真正的护城河是“选股 → 盯盘 → 信号 → 交易 → 风控 → 复盘 → 学习”全链路闭环。
   - 复盘要能定位问题发生在哪一环，并把结果转成参数、规则、策略更新。

4. **审计、回放、人工确认是上实盘前的生死线**
   - 每个关键动作保存证据链：
     - 数据快照
     - 触发条件
     - 关联策略
     - AI 参与内容
     - 风控约束
     - 人工是否确认
     - 最终执行动作
     - 后续结果
     - 复盘结论
   - 早期实盘禁止无限制自动执行。

5. **分层 AI 执行机制**
   - 信息层
   - 建议层
   - 警报层
   - 执行建议层
   - 严格白名单下的自动执行层

6. **像 bootcamp 一样打透高价值闭环**
   - 先打透“盯盘 + 信号 + 风控联动”。
   - 再做“复盘 + 学习”。
   - 再做“多策略并行 + 多账户调度”。
   - 不是一开始做大全。

7. **产品定位是“量化决策操作系统”，不是功能堆叠**
   - 首页应是总控台，而不是菜单页。
   - 展示“发现 → 验证 → 风控 → 执行 → 复盘 → 学习进化”的完整能力。

## 4. 本体层设计（Ontology Layer）

V2 在领域模型之上增加“本体层”，让对象、关系、行动成为系统的公共语言。

### 3.1 核心对象

```python
class OntologyObject:
    object_id: str
    object_type: str
    properties: dict
    links: list[ObjectLink]
    permissions: dict
    provenance: Provenance
```

核心对象类型：

- `Instrument`
- `Strategy`
- `StrategyVersion`
- `Candidate`
- `TradePlan`
- `ApprovalRequest`
- `OrderIntent`
- `ExecutionRecord`
- `PositionLot`
- `ReplayCase`
- `LearningCase`
- `ResearchReport`
- `User`

### 3.2 对象关系

```
Strategy 1—N StrategyVersion
StrategyVersion 1—N Candidate
Candidate 1—N TradePlan
TradePlan 1—1 ApprovalRequest
ApprovalRequest 1—N ExecutionRecord
ExecutionRecord N—1 PositionLot
PositionLot 1—N ReplayCase
ReplayCase N—1 LearningCase
ResearchReport 1—N StrategySpec
```

### 3.3 对象属性示例

```python
class StrategyVersion:
    id: str
    strategy_id: str
    version_no: str
    market: str
    spec: StrategySpec
    status: str
    effective_from: datetime
    effective_to: datetime | None
    research_ref: str | None
```

## 5. 行动层设计（Action Layer）

Palantir 的关键是“用户不直接改数据，而是执行 Action”。

### 4.1 Action 统一模型

```python
class Action:
    action_id: str
    action_type: str
    target_object: ObjectRef
    parameters: dict
    status: ActionStatus  # DRAFT / PENDING_APPROVAL / EXECUTING / SUCCEEDED / FAILED / REJECTED
    requested_by: UserRef
    approved_by: UserRef | None
    created_at: datetime
    decided_at: datetime | None
    audit_trail: list[AuditEntry]
```

### 4.2 核心 Action

| Action | 触发者 | 是否需要审批 | 说明 |
|---|---|---|---|
| `create_research` | 用户 | 否 | 创建研究任务 |
| `submit_strategy` | 用户 | 否 | 提交策略注册 |
| `approve_strategy` | 用户 | 是 | 人工审批策略 |
| `generate_candidate` | 系统 | 否 | 生成候选池 |
| `create_trade_plan` | 系统 | 否 | 创建交易计划 |
| `request_trade_approval` | 系统 | 是 | 推送交易授权 |
| `approve_trade` | 用户/自动 | 是 | 通过交易授权 |
| `reject_trade` | 用户 | 是 | 拒绝交易授权 |
| `execute_simulated` | 系统 | 按配置 | 执行模拟交易 |
| `execute_live` | 系统 | 必须授权 | 执行实盘交易 |
| `close_position` | 系统 | 按配置 | 平仓 |
| `generate_replay` | 系统 | 否 | 生成复盘 |
| `run_parameter_optimization` | 用户 | 否 | 参数优化 |
| `promote_strategy` | 用户 | 是 | 策略晋升 |

### 4.3 Action 与交易授权

这是 V2 最重要的 Action：

```
TradePlan → Action(request_trade_approval)
         → ApprovalRequest
         → 用户执行 Action(approve_trade / reject_trade)
         → 生成 Action(execute_simulated) / Action(execute_live)
```

每个 Action 都有：

- 唯一 ID
- 操作人
- 状态
- 时间
- 审计记录
- 权限校验

## 6. 数据管道与血缘

### 5.1 管道化

Palantir Foundry 的 Pipeline 思想映射到数据接入：

```
Source Adapter
  → Normalize
  → Validate / Quality Gate
  → Upsert to Staging
  → Merge to Fact Table
  → Publish Data Event
  → Update Lineage
```

### 5.2 数据血缘

每个数据对象记录：

```python
class Provenance:
    source_system: str
    source_version: str
    transform_name: str
    transform_version: str
    input_refs: list[ObjectRef]
    created_at: datetime
    updated_at: datetime
```

例如：

- 某根日K来自哪个数据源。
- 某因子值由哪个版本的计算器产生。
- 某笔回测绩效由哪个回测运行产生。
- 某笔模拟成交由哪次授权产生。

## 7. AIP / AI 辅助

Palantir AIP 的核心是：

> LLM 基于本体和业务上下文，辅助用户完成工作，但关键行动仍由人控制。

对应到 zhice-OS V2：

- AI 研究助手可以阅读行情、策略、复盘、持仓数据。
- AI 可以生成策略提案、复盘解读、参数优化建议。
- AI 可以辅助填写审批摘要。
- AI 不能直接下单。
- 所有交易行动必须经过 `Action` + 授权中心。

## 8. 权限与治理

### 7.1 权限模型

每个对象和 Action 都有权限：

```python
class PermissionPolicy:
    object_type: str
    action_type: str
    allowed_roles: list[str]
    require_approval: bool
```

### 7.2 角色

- `ADMIN`
- `TRADER`
- `RESEARCHER`
- `RISK_MANAGER`
- `VIEWER`

### 7.3 治理能力

- 所有 Action 审计留痕。
- 所有交易授权可追溯。
- 所有策略变更可回滚。
- 所有数据变更可溯源。
- 所有 AI 建议记录 prompt / context / output。

## 9. 用户界面

前端不再只是“数据库浏览器”，而是：

- 对象详情页：策略、订单、持仓、复盘。
- 行动中心：待审批、待执行、异常。
- 工作台：交易、资产、研究、系统。
- AI 助手侧边栏：基于当前对象上下文提供建议。

## 10. 对 V2 的落地建议

1. 在领域模型之上建立 `Ontology` 层。
2. 把交易、审批、策略注册等统一建模为 `Action`。
3. 数据接入全部走 Pipeline + Lineage。
4. 所有 AI 能力都作为“建议者”，不直接执行交易。
5. 权限、审计、审批从第一天就进入核心设计。

## 11. 已校准项

已基于 `Palantir完整调研总报告.md` 校准：

- 产品定位：量化决策操作系统，而非功能堆叠。
- 统一对象模型：对象是系统一等公民。
- AI 进入受控执行层，而不是聊天建议。
- 审计、回放、人工确认是实盘生死线。
- 分层 AI 执行机制。
- 先打透高价值闭环，再扩展功能。
- 架构分层：Data / Object / Decision / Execution / Observation / Review / Learning。

后续如果报告更新，再持续同步到本文档。
