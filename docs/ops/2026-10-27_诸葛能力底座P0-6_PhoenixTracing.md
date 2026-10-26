# 诸葛亮能力底座 P0-6：Phoenix 本地 tracing（部分完成：instrumentation 已验证，Phoenix 运行态受阻）

- 工单：`WO-LEGION-ZHUGE-P0TRACE-20261027`
- 日期：2026-10-27
- 范围：P0 开工包第 6 项（观测；不常驻、不新增 L2）
- 状态：**instrumentation 完成并通过 OTel span 验证；Phoenix 服务器因依赖冲突未跑起来（如实记录）**

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `tools/p0_trace.py` | 懒加载 tracer：优先 Phoenix OTLP → 失败回退 OTel console；`traced_chat` 包住 8795 `/llm/chat`，span 记录 binding/carrier/relay/status/latency/ok/error；失败 no-op 不影响业务 |
| `tools/test_p0_trace.py` | 4 个离线单测（no-op、fake tracer attributes、HTTP 异常、默认未启用） |

## 2. 验证（2026-10-27 07:2x）

```text
python -m pytest tools/test_p0_trace.py -q  → 4 passed

OTEL_TRACE=1 python tools/p0_trace.py --selftest
→ tracer=True
→ span: {"name":"zhuge.llm.chat","trace_id":"0xd7bb...","attributes":{...}}（ConsoleSpanExporter 输出）
→ 调用 8795 ok=true / elapsed_ms=31460（当前 openclaw 载体较慢）
```
即：**span 生成与 8795 调用链 instrumentation 已验证**；未启用时自动 no-op。

## 3. Phoenix 运行态受阻（依赖冲突）

- 已安装 `arize-phoenix 19.18.0` / `arize-phoenix-otel 0.17.2`（pip 成功）；
- 但 `import phoenix` 在**本机现环境**报错：
  `ValueError: mutable default <class 'mappingproxy'> for field boolean_names is not allowed: use default_factory`
  （pydantic/dataclass 与 Phoenix 版本不兼容；尝试 pin `pydantic 2.12.5` 仍失败，且 open-webui 要求 2.12.5、semantica 要求 ≥2.13.4，环境本身存在版本冲突）；
- 已恢复 `pydantic 2.14.1`；**8795/8091 health 均 200**（服务未受影响）；
- Phoenix 安装同时升级了 starlette/cryptography/openai 等依赖，**open-webui 依赖告警**仍在；本轮**未再改动环境版本**，避免进一步破坏；建议 Phoenix 用**独立 venv**或待依赖统一后重装。

## 4. 结论与下一步

- **P0-6 观测的代码面已就绪**：统一走 OpenTelemetry span；Phoenix 兼容后仅需设
  `PHOENIX_TRACE=1` + `PHOENIX_ENDPOINT`，无需改代码；
- 未做：Phoenix 服务器启动/UI 验证、8795 服务端 instrumentation（当前是客户端 span）、常驻/计划任务；
- 建议：P1 在隔离 venv 装 Phoenix，重跑 `PHOENIX_TRACE=1 python tools/p0_trace.py --selftest` 并核对 UI trace；若环境不适合，可改用 OTLP collector / 其他 OTel 后端。

## 5. 边界
- 不常驻、不新增计划任务、不写远端（除非显式 PHOENIX_TRACE + endpoint 可用）；
- console exporter 仅用于 selftest，输出到终端，不写文件/不发送。
