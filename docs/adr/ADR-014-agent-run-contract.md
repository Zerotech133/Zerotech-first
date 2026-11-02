# ADR-014：Agent 运行契约 v1（run / event / conversation）

- **状态**：Accepted（2026-10-27）；P2 部分落地：trace.run_scope/spans.run_id/llm.attempt（2026-11-02），P2 余项待 W3
- **关联**：O6；`agent_platform/observability/trace.py`、`tools/events.py`、`agent_platform/legion_command/serve.py`；ADR-005（可观测）
- **本 ADR 只定义契约，不改变任何运行时行为**。

## 背景

Agent 能力分散在多个入口（军团 8796、agent_platform engine、飞书 bot、开发事件层），
"一次运行"目前没有统一标识与状态：trace 只有 span、dev_events 只有开发事件、run 级
输入/输出/状态/成本/产物无法从一个地方读全。导致：跨入口排障要拼数据、无法统一统计
成功率/时延/成本、后续接外部消费者（看板/评测）成本高。

## 决定

1. 定义 **三个核心对象 + 一个版本字段**：
   - `Run`：一次任务/对话回合的执行单元（run_id、agent/seat、输入、状态、时间、trace_id、产物、错误、用量/成本）；
   - `Event`：run 内有序事件（run_id、seq、type、ts、payload、trace_id）；
   - `Conversation`：多轮对话容器（conversation_id、participants、messages、run_ids）；
   - `contract_version`：语义化版本（`major.minor.patch`），所有产出必须带。
2. **兼容策略**：
   - minor：只增字段/事件类型，向后兼容；消费者忽略未知字段/事件；
   - major：删改字段必须新版本并存 + 至少 90 天 deprecation（参考 HA deprecation 纪律）；
   - 每个对象带 `schema_version`，与 contract_version 同源。
3. **分层落地，不做大爆炸重构**：
   - P1（只读适配）：从现有 trace/dev_events 派生 run/event 视图，不改写入；
   - P2（双写）：在 1-2 个入口（如 legion_command、agent engine）按契约写 run/event；
   - P3（消费）：看板/评测/diagnostics 读取统一契约；
   - P4（收敛）：旧入口迁移，保留兼容期。
4. **唯一 run 标识**：`run_id` 由入口生成（ULID/uuid4），全链路透传；`trace_id` 可复用现有 trace 体系，一个 run 可含多个 span。
5. **敏感字段**：契约中禁止出现 token/key/密码明文；`inputs/outputs` 允许摘要或脱敏引用（PII/凭据不入）。
6. **存储**：JSONL（append-only）+ 必要索引（SQLite/现有 traces.db）；先保证可读可导出，不引新数据库。

## 后果

- 正面：跨入口可统一排障/统计/评测；对外 API 稳定；与 OTel/Phoenix 可映射；
- 代价：需要在入口加轻量埋点与 schema 校验；历史数据只能派生不能回填完整 run；
- 不做：不引入消息队列/分布式追踪后端；不改现有 trace/dev_events 语义（只做上层契约）。

## 待评审问题

1. `agent` 用席位名还是运行时 ID？（建议两者都带：`agent.name` + `agent.runtime_id`）
2. `Run.status` 是否需要 `cancelled`/`timeout` 两个终态？（建议要）
3. `conversation` 是否必须？部分一次性任务无多轮（建议可选，Run 为主）
4. P2 首批入口选哪两个？（建议 `legion_command/serve.py` + `agent_platform/engine`）
