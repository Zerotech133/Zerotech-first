# 验收判据：Phoenix OTel 接通（WO-OTEL-PB）
> STATUS: declared



| # | 判据 | 证据路径/命令 | 通过线 |
|---|---|---|---|
| 1 | OTLP 上报成功 | 设 ZHAOYUN_OTEL=1+endpoint 后 trace.record() | otel.status=200 |
| 2 | 默认零副作用 | 不设 env 时 trace.record() | otel.skipped=otel_off |
| 3 | 单元测试 | `pytest agent_platform/tests/test_trace_otel.py` | 3 passed |
| 4 | protobuf 上报 | Phoenix OTLP HTTP 4318 | POST /v1/traces 返回 200 |
| 5 | 可回滚 | `git revert 9ebdd2866` 或 env 关 | 恢复旧行为；无残留 |

## 回归项（修复后不误伤）

- 默认 off 时不得影响业务路径；record() 返回 skipped 而非异常。
