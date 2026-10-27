# ADR-005：Agent 可观测采用 OpenTelemetry GenAI 语义约定（分阶段）

- **状态**：Proposed（2026-10-27）
- **决定者**：DSH-Lead（待 ARCH1/平台 owner 复核）
- **关联**：`ARCHITECTURE.md`、`docs/testing.md`、M4 五环 trace（已有）

## 背景

当前 Agent 链路已有自建可观测：`trace_links.jsonl`、`perf_ledger.jsonl`、checkpoints.db、
服务日志与 `dev_events.jsonl`，但**字段口径是自建的**，跨工具（Phoenix/Langfuse/Datadog 等）不可直接消费。
OpenTelemetry 已发布 **GenAI semantic conventions**（`gen_ai.*` 属性、模型/用量/工具调用等），
主流可观测后端（Datadog 等）已原生支持；OpenAI Agents SDK 也内置 tracing 作为核心概念。

## 决定（分阶段）

1. **P1（低风险，先做）**：新增"字段映射表"文档（`docs/specs/`），把现有 trace/perf 字段映射到
   `gen_ai.*`（模型、操作名、token 用量、工具名、会话 id、错误类型），不改现有写入；
2. **P2**：关键链路（agent_platform 席位调用、MCP 工具调用）加 OTel span/属性，
   通过环境变量开关（默认关），失败不影响主链路；
3. **P3**：接一个可观测后端（Phoenix/Langfuse/Datadog 任一）做面板；先用离线 JSON 验证再上线；
4. 任何涉及服务重启/新端口/新依赖的动作走 L2 五要素。

## 后果

- 正面：Agent 链路可跨工具观测、可做 eval 对比；符合开源生态。
- 代价：P2 引入 OTel SDK 依赖与少量性能开销；需与现有 M4 trace 共存一段时间。
- 不做：不推倒现有 trace/perf 账本；不加常驻 collector（P3 再议）。
