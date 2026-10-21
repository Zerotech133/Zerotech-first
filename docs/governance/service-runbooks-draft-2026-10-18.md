# GOV-MIN-006：四服务运行手册草稿（8787 / 8788 / 8791 / 8093）

> 文档性质：**DRAFT / READ-ONLY**；2026-10-18 整理既有静态治理文件及已封存证据，不是当日现场巡检、上线批准、可执行启动手册或生产健康证明。没有执行端口探测、HTTP 请求、启停、配置修改、账本读取或写入。
>
> 有效性边界：下文的 `up`、监听及 HTTP 200 均是 **2026-10-17 的历史时间切片**，不代表 2026-10-18 或阅读时仍在线。启动命令、当前 PID、当前健康状态和未闭合的业务链路均为 `UNVERIFIED`。计划任务注册、脚本名称或 `optional_offline=true` 均不等于服务当日健康。

## 一、证据口径、状态与共同操作边界

### 1.1 原始证据与冲突处理

| 标识 | 已有静态文档/封存记录 | 证据时间与可用范围 |
|---|---|---|
| E1 | [`GOV-FIX-006-port-readonly-2026-10-17.txt`](GOV-FIX-006-port-readonly-2026-10-17.txt) | 本地时间（UTC+08:00）`2026-10-17T19:28:26.4356331` 至 `19:29:10.9597568`；仅覆盖 8080/8734/8787/8788 监听及根路径 HTTP、7897，不覆盖 8791/8093。 |
| E2 | [`portal-qa-2026-10-17.txt`](portal-qa-2026-10-17.txt) | `check_ports.py --strict --json` UTC `2026-10-17T11:18:15Z` 至 `11:18:35Z`；注册端口 `up` 观察，不证明具体 HTTP 健康、进程归属或业务可用。对应本地 UTC+08:00 `19:18:15` 至 `19:18:35`。该记录明确未执行 `--probe`。 |
| E3 | [`CURRENT_RUNTIME_MATRIX_2026-10-17.md`](../CURRENT_RUNTIME_MATRIX_2026-10-17.md) | 文档日期 2026-10-17；描述规划/配置入口、服务映射、state 根、任务名称；未给出本手册四服务逐端点响应的单独采样时间。 |
| E4 | [`governance-control-ledger-2026-10-17.md`](governance-control-ledger-2026-10-17.md)、[`manual-approval-decision-register-2026-10-17.md`](manual-approval-decision-register-2026-10-17.md) | 2026-10-17 治理与人工决策口径：8787 生产、8788 验证，禁止相互替代；批准记录待填、`blocked`。 |
| E5 | [`shared-resource-register.md`](shared-resource-register.md)、[`next-minimal-workorders-2026-10-17.md`](next-minimal-workorders-2026-10-17.md)、[`rebaseline-001-decision-record-2026-10-18.md`](rebaseline-001-decision-record-2026-10-18.md) | 分别为 2026-10-17 共享资源纪律、GOV-MIN-006 范围，以及 2026-10-18 的冻结决定；仅为控制面，不替代健康实测。 |

证据优先级：同一事项首先按 **具时间戳的实际观测** 限定其历史时点，其次核对代码/配置与审计记录，最后参照运行矩阵和计划。E2 将 8093 标为“数据仓管理预览(daily_ui_preview_server.py)”，而 E3/E4 将 8093 解释为 org registry / `/api/org` 数据面；**服务身份冲突，标记 `UNVERIFIED`，不能把端口 `up` 自动归因于 org registry**。E3 的 8734“唯一入口”与 E1 显示该端口无监听、无 portproxy 规则相冲突；仅按 E1 时点称 8080 为可访问入口，不宣称 8734 已生效。

### 1.2 状态定义（逐端口、逐时间切片使用）

| 状态 | 严格含义 |
|---|---|
| `OBSERVED_LISTENING` | 给定采集时间内确有监听或端口清单报告 `up=true`；不推断 HTTP、真实进程身份、业务功能。 |
| `HTTP_ROOT_200` | 给定采集时间对确切根路径 `/` 收到 HTTP 200；不推断 `/health`、下游、鉴权、数据写入安全、业务 ready。 |
| `IDENTITY_UNVERIFIED` | 端口有历史观察但其承载服务/入口归属缺少独立证据或现有证据冲突。 |
| `UNVERIFIED` | 字段没有对应时点及范围的直接证据；含阅读时是否在线、启动命令、专用健康接口、数据/state 隔离落实情况。不得写成 `healthy`。 |
| `BLOCKED` | 需人工签批的启停、变更、验证实例处置、运行基线调整被治理门禁阻断；不是服务离线的同义词。 |

本手册只描述**证据型判断**，不以“脚本退出码 0”、计划任务注册、端口 `up` 或根页 200 定义 `READY`。要认定业务 `READY`，需服务 owner 另定只读、无副作用的专用探针和下游验收标准，并记录 URL、请求方式、HTTP 状态、响应摘要、采集时区/时间、进程和 state/data 路径；在批准与实证前均标 `UNVERIFIED`。

### 1.3 所有服务的禁止动作与升级路径

1. **本次禁止**运行任何 live 端口/HTTP 探测、服务启动/停止/重启、修改端口映射/白名单/nginx/计划任务、访问或改写账本及生产 state/data；不得借 runbook 草稿隐含授予这些权限。
2. 即使未来有单独核验工单，也不得以 8788 故障为由重启/停用 8787，不得用 `Stop-Process -Name python` 一类宽范围操作；不把验证 state 写入生产 state，不让验证实例写 live 数据。
3. 任何共享资源写入/迁移/重启：先**停止当前动作并保全原有证据**，提交单项工单与人工批准，指定 owner / approver / executor / observer、资源窗口、允许路径、前置快照、最小范围实施、独立复核及回滚；`shared-resource-register.md` 的窗口登记**不是授权**。未经批准状态维持 `BLOCKED`。
4. 升级对象：8787/8788 → Hermes 负责人及项目负责人（8788 处置见 `DEC-20261017-03`）；8791 → KG/数据运行负责人及项目负责人；8093 → org registry/门户数据面负责人及项目负责人，且先澄清端口归属冲突。若涉及 nginx/端口/计划任务，追加管理员或运行负责人；涉及账本冻结状态，交现有 REBASELINE-001 人工复核门禁，**不读取账本自行归因**。上述具体实名值均 `UNVERIFIED`，不可代签。
5. 变更后由获授权的独立执行人保存**新时间戳**的只读监听、进程、针对性 HTTP、隔离路径、日志及回滚证据，不覆盖 E1/E2 或旧索引；缺失/冲突就维持 `UNVERIFIED/BLOCKED` 并升级，不猜测启动命令或在线状态。

## 二、8787：Hermes 生产 WebUI

| 字段 | 草稿记录 |
|---|---|
| 角色/端口 | E3：Hermes WebUI，`127.0.0.1:8787`；E4 明确为生产实例。E3 记载门户 `/hermes/` 反代 8787；反代配置/链路当日逐跳实测 `UNVERIFIED`。 |
| 封存入口及健康证据 | E1 在本地 `19:28:26.4356331`～`19:29:10.9597568+08:00` 记录 `127.0.0.1:8787` 监听 PID 28032，`GET http://127.0.0.1:8787/` HTTP 200，约 227,827 字符；E2 于 UTC `11:18:15`～`11:18:35Z` 标 `up=true, optional_offline=true`。HTTP 具体采样秒未独立给出，不补造秒级时间。 |
| 进程与数据 | E1 仅确认 PID 28032 为 `python.exe`，可读进程路径/命令行为空；不能据此确认实际脚本或启动命令。E3 声明 Hermes 生产 state 位于 `Hermes/webui-state`，但当前进程实际 state 根及读写隔离 `UNVERIFIED`。 |
| 状态 | 历史 `OBSERVED_LISTENING + HTTP_ROOT_200`；业务 `READY`、专用 health URL、当前在线/PID、启动与守护链均 `UNVERIFIED`。E3 提及脚本名称仅作待核静态线索，不作为本手册可执行命令。 |
| 故障处理界限 | 记录症状、证据时间和影响范围，升级 Hermes 负责人；不得启动旧副本覆盖生产 state、裸杀 Python、通过 8788 的状态反推 8787。生产实例启停须走审批窗口。 |

## 三、8788：Hermes 验证实例

| 字段 | 草稿记录 |
|---|---|
| 角色/端口 | E3/E4：Hermes next 验证/并行端口，`127.0.0.1:8788`；**不是生产入口**。不把 8788 页面或测试结论当作 8787 的放行证据。 |
| 封存入口及健康证据 | E1 同一 `2026-10-17T19:28:26.4356331`～`19:29:10.9597568+08:00` 窗口记录 `127.0.0.1:8788` 监听 PID 35756；`GET http://127.0.0.1:8788/` HTTP 200，约 227,827 字符；E2 UTC `11:18:15`～`11:18:35Z` 标 `up=true, optional_offline=true`。与 8787 响应长度相同**不证明**数据/state 共用或功能等同。 |
| 进程与数据 | E1 记录 PID 35756 为 Python，命令行指向 `C:\aios\Hermes\hermes-webui-next\server.py`；这是**历史进程参数，不是已验证启动命令**。E3 声明验证 state `Hermes/webui-state-next`；进程实际环境变量、state/data 根、访问日志与生产根是否物理隔离 `UNVERIFIED`。 |
| 状态 | 历史 `OBSERVED_LISTENING + HTTP_ROOT_200`；当前在线、业务 `READY`、专用 health URL、独立数据/state 配置 `UNVERIFIED`；验证实例处置 `DEC-20261017-03 = BLOCKED`。 |
| 故障处理界限 | 先保留 8788 自身历史证据，报 Hermes 负责人；不得重启 8787 替代处理，不得把验证请求写生产 state/live 数据。保留、下线或转 Shadow 均需该决策项人工批准、备份及回滚。 |

## 四、8791：KG 数据看板

| 字段 | 草稿记录 |
|---|---|
| 角色/端口 | E2 登记“图谱数据看板(kg_data_dashboard.py)”端口 8791，`optional_offline=true`；E3 描述为 KG 数据大屏、本机服务，记载计划任务名称 `KgDataDashboard` 及 Hermes MCP/门户相关读取。实际绑定地址、路由 URL、当前脚本命令、计划任务 LastResult 均 `UNVERIFIED`。 |
| 封存入口及健康证据 | E2 的严格 JSON 端口清单于 UTC `2026-10-17T11:18:15Z`～`11:18:35Z` 显示 `8791 up=true`。E1 **未检查 8791**；没有封存的 8791 HTTP 请求/返回码、专用健康端点、进程 PID、业务数据验收。 |
| 状态 | 历史 `OBSERVED_LISTENING`（仅 E2 工具输出）；HTTP、业务 `READY`、当前在线及调度成功均 `UNVERIFIED`。`optional_offline=true` 只是端口检查策略，不是故障豁免或当前关停证明。 |
| 数据隔离/故障处理 | KG live 根涉及 `quant_backtest/data`，E5 登记默认只读。看板出现空数据时先由 KG/数据 owner 区分数据源、读权限和调度状态；不得从端口 `up` 推断数据新鲜度，不得运行同步启动器、修改数据根、重建 `KGDataRoot`、启停任务或写 live 根。升级时附既有证据及待核字段；任何现场验证需单独授权。 |

## 五、8093：org registry / 门户组织数据面（身份待核）

| 字段 | 草稿记录 |
|---|---|
| 角色/端口 | E3/E4 称 8093 为 org registry，本机服务，门户 `/org` 数据经 `/api/org`、`/api/agent/knowledge` 指向 8093；E5 将 `org registry 8093` 登记为默认只读、API 数据面变更需契约测试。**但 E2 把同一端口命名为 `daily_ui_preview_server.py` 数据仓管理预览**；当前实际服务、进程与路由上游归属为 `IDENTITY_UNVERIFIED`。 |
| 封存入口及健康证据 | E2 UTC `2026-10-17T11:18:15Z`～`11:18:35Z` 报 `8093 up=true, optional_offline=true`；只是 8093 端口观察。E1 **未检查 8093**；`/api/org` 和 `/api/agent/knowledge` 的 HTTP 状态/响应、`/org` 页面至后端逐跳链路、专用 health URL、PID 均 `UNVERIFIED`。E3 的映射是文档声明，不是该时段健康证明。 |
| 状态 | 历史 `OBSERVED_LISTENING + IDENTITY_UNVERIFIED`；当前在线、registry 业务 `READY`、启动命令、守护任务名称/LastResult 均 `UNVERIFIED`。不得把端口 `up` 标为 org registry 健康。 |
| 生产/验证隔离及故障处理 | `/org` 页面返回 200 也不等于数据面可用；保留新版入口，不改回旧 8091 SSR。由 org registry/门户负责人先用静态证据人工核对 8093 服务身份、路由配置及契约，再在**单独获授权窗口**设计只读端到端 smoke；数据面变更必须契约测试及人工批准。生产与验证实例/数据根区分记录 `UNVERIFIED`，不可把预览服务视作已隔离 registry。 |

## 六、待补证据与草稿回滚

- **四服务共同缺口**：当前在线、最新进程/PID、真正启动/守护命令、服务 owner 实名、可靠无副作用健康端点、业务 `READY` 标准、失败告警和已演练回滚；均 `UNVERIFIED`。
- **8787/8788**：生产/验证 state 实际解析路径及写入隔离、配置和访问日志的独立证据；8788 处置审批仍 `BLOCKED`。
- **8791**：进程归属、HTTP 路由/响应、计划任务 LastResult 与产物时间、下游数据只读契约。
- **8093**：E2 与 E3/E4 的服务身份冲突、`/api/org` 与 `/api/agent/knowledge` 实际上游和无副作用响应、数据面契约。
- **回滚范围**：本工单只新增本草稿；若撤回，仅在单独受控文档工单中删除**本文件**，不得删除/覆盖任何原始证据；无生产服务或配置回滚动作（因本次未变更运行态）。
