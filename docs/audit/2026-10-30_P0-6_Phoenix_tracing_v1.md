# P0-6 Phoenix tracing 收口（2026-10-30 v1）

- 工单：`WO-LEGION-P0-6-OTLP-20261030`
- 前置：Phoenix 6006/4317/4318 已监听；`p0_trace.py` 单测 4 passed
- 结论：**trace 已真实进入 Phoenix**；不新增依赖、不改常驻服务

## 1. 原阻塞

`phoenix.otel` 在 Python 3.11 缺失：

```text
ModuleNotFoundError: No module named 'phoenix.otel'
```

旧 `init_tracer` 因此回退到 console exporter：span 生成了，但 Phoenix 收不到，P0-6 只能算“部分”。

## 2. 修复

`tools/p0_trace.py` 增加标准 OTLP HTTP 回退：

```text
opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter
endpoint = http://127.0.0.1:6006/v1/traces
resource.service.name = zhuge-p0
PHOENIX_PROJECT 通过 x-phoenix-project-name 头传递
```

回退顺序：

1. `phoenix.otel.register`（若安装）；
2. **标准 OTLP HTTP**（本次使用）；
3. console exporter（仅最后兜底）。

无需安装新包：3.11 环境已有 `opentelemetry` 与 OTLP HTTP exporter。

## 3. 验收证据

自检命令：

```powershell
$env:PHOENIX_TRACE="1"
$env:PHOENIX_ENDPOINT="http://127.0.0.1:6006/v1/traces"
$env:PHOENIX_PROJECT="zhuge-p0"
python tools/p0_trace.py --selftest
```

结果：

```text
tracer= True
init_error= phoenix unavailable: ... | using otlp http fallback
{"ok": true, "elapsed_ms": 9183.9}
```

Phoenix 查询：

```http
GET http://127.0.0.1:6006/v1/projects/default/spans
```

返回包含：

```json
{
  "name": "zhuge.llm.chat",
  "context": {"trace_id": "c353fa713bb477e10159b9aa09b43c6a"},
  "attributes": {
    "llm.binding": "ollama/qwen3.5:9b",
    "llm.endpoint": "http://127.0.0.1:8795/llm/chat",
    "llm.latency_ms": 9183.9,
    "llm.status": "normal",
    "llm.ok": true
  }
}
```

## 4. 状态

P0-6 tracing 验收通过：

- span 名称、trace_id、延迟、状态、错误可用；
- 调用 8795 `/llm/chat` 的真实链路；
- 不依赖 `phoenix.otel`；
- 仍为**显式开启**（`PHOENIX_TRACE=1`），默认不常驻、不新增计划任务。

## 5. 未做 / 边界

- 尚未把 `PHOENIX_TRACE=1` 注入常驻 8795/8091（属配置变更；当前验证链路已通）；
- `x-phoenix-project-name` 未自动创建独立 `zhuge-p0` 项目，span 落在 default；需要独立项目时可另建；
- token 统计未加入该 span（P0-6 当前判据是 trace 可用，不要求 token）。

## 6. 回滚

- 删除 `init_tracer` 中的 OTLP HTTP 回退块；
- 或保持代码但 unset `PHOENIX_TRACE`，行为回到 no-op；
- 无数据/配置变更。
