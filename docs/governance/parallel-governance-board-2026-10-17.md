# 并行治理工作板

> 启动时间：2026-10-17 17:21
> 模式：短期扩张冻结；只读审计/文档/测试并行，禁止共享生产写入。

| 任务 | Owner | 类型 | 状态 | 共享资源限制 | 交付物 |
|---|---|---|---|---|---|
| GOV-13 路径与入口口径 | path-auditor | 只读审计 | running | 不改代码 | 待回传证据与清单 |
| GOV-14 端口与服务注册 | port-auditor | 只读审计 | running | 不重启 | 待回传端口快照 |
| GOV-15 任务账本污染 | ledger-auditor | 只读审计 | running | 不改账本 | 待回传校验报告 |
| GOV-16 Agent Platform 缺口 | platform-auditor | 只读审计 | running | 不改共享面 | 待回传最小工单 |
| GOV-17 门户门禁回归 | portal-qa | 测试/证据 | running | 不改 nginx/生产服务 | 待回传测试证据 |
| GOV-18 运行文档入口 | runtime-docs | 文档 | running | 仅写 docs/governance | 三份 runbook |

## 统一收口规则

- 每条线必须给：事实、证据路径、风险等级、建议动作、是否需要人工批准。
- 发现共享写面问题只登记，不顺手修。
- 任何生产变更必须另开工单、登记共享资源窗口并通过阶段门。
- 主控收口后更新 `shared-resource-register.md` 和周度评审模板。
