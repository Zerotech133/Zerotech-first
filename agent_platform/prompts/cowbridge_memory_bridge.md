# COWBRIDGE · cowbridge 记忆桥（cowbridge_memory_bridge）

> 运行体部（驿传署）· 补档说明：内容取自 org.json 注册表真源（charter_detail/attributes/grade/llm），未新增职责与权限。
> **运行时组件**：纯工程实现，无 LLM 依赖；只保证幂等、可追溯、失败不静默。

## 职责
- cowbridge 记忆桥：跨层记忆读写与格式转换、幂等落库
- 纯工程桥接组件，无 LLM 依赖
- 只做本环节：不越权裁决、不自动执行、不代签。

## 专长（org attributes.specialties）
- 跨层记忆读写
- 格式转换
- 幂等落库
- 回放一致性

## 纪律（铁律）
- 只报实况/只做本环节，不臆造数据；缺数据就明说「需查证」并指出该查哪个源。
- 无 danger 权限（`danger=deny`）：不写业务、不发消息、不改配置。
- 建议/草案 ≠ 采纳；合并、执行、对外动作按平台审批流程（需人签的人签）。
- 输出带来源与时间戳；与注册表冲突时以 org.json 为准并上报。

## 上下游
- 上游：部门主官/上游席
- 下游/写入：本席记忆域（只写本席）

## 职级
`E2` · 补档初始档，待 KPI 观察后调整

## 模型
none（运行时规则执行体，非 LLM 判别席；不参与判读与决策）

## 回滚 / 停用
- 本席无工具、无持久化写入/授权：不产生需回滚的系统变更；
- 停用=平台侧将本席移出调用链（其建议草案作废重出），无进程/任务需清理；
- 若后续授予工具：必须同时提供 kill switch 与卸载路径，方可上线。

## 工具（已授予）
- `service_probe`（只读多目标探活）：按 `agent_platform/config/service_probe_targets.json` 中本席目标探测（HTTP /health 或数据目录新鲜度），写 `agent_platform/data/service_probe/<席>/` 与 `dev_events` 的 `patrol_verdict`（task=agent=席码）。**不写业务、不发消息、不重启服务。**
- 运行方式：`python agent_platform/tools/service_probe.py --targets all --quiet`（计划任务）。
- 回滚：`SERVICE_PROBE=off` kill switch；卸载计划任务 `SeatServiceProbe` 即停。
