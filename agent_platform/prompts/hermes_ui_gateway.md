# HERMES_UI · Hermes WebUI 门面（hermes_ui_gateway）

> 运行体部（驿传署）· 补档说明：内容取自 org.json 注册表真源（charter_detail/attributes/grade/llm），未新增职责与权限。
> **运行时组件**：纯工程实现，无 LLM 依赖；只保证幂等、可追溯、失败不静默。

## 职责
- Hermes WebUI 门面：对外页面与接口门面、静态资源与路由
- 纯工程门面组件，无 LLM 依赖
- 只做本环节：不越权裁决、不自动执行、不代签。

## 专长（org attributes.specialties）
- 门面总管

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

## 工具（已授予）
- `hermes_ui_probe`（本席唯一工具）：对 Hermes WebUI 8787 做一次**只读探活**（GET /health、GET /），
  结果写 `agent_platform/data/hermes_ui/status.json` + `history.jsonl`，并写 `dev_events` 的
  `patrol_verdict` 事件（task=HERMES_UI）。**不写业务、不发消息、不重启服务。**
- 运行方式：`python agent_platform/tools/hermes_ui_probe.py --quiet`（计划任务）；人工可加 `--json`。
- 退出码：0=成功；1=探活失败；2=自身错误。
- 回滚：设置环境变量 `HERMES_UI_PROBE=off` 立即跳过；删除计划任务/本工具即完全回滚。

