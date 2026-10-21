# GOV-MIN-006 后续控制面：四服务无副作用健康验收设计

> **状态：DESIGN_ONLY / NOT_EXECUTED / BLOCKED_FOR_LIVE_VERIFICATION**（2026-10-18）。本文仅根据既有运行手册、历史 GOV-FIX-006 证据及相关静态配置提出未来可审议的验收契约；未执行 HTTP、端口扫描、进程/任务探测、服务启停，未读取冻结账本或访问生产 state/data。**本文不是探测授权、生产健康证明、启动/回滚指令或 READY 签认。**
>
> 基础证据：[四服务运行手册草稿](service-runbooks-draft-2026-10-18.md)、[历史 GOV-FIX-006 端口记录](GOV-FIX-006-port-readonly-2026-10-17.txt)、[历史门户端口清单](portal-qa-2026-10-17.txt)、[运行态矩阵](../CURRENT_RUNTIME_MATRIX_2026-10-17.md)、[人工批准决策清单](manual-approval-decision-register-2026-10-17.md)。静态候选代码仅用于判断请求风险：`tools/check_ports.py`、`portal/nginx.conf`、`tools/org_registry_server.py`、`Hermes/hermes-webui-next/api/routes.py`、`Hermes/hermes-webui-next/api/config.py`。静态配置可能与运行中的版本不同。历史 10 月 17 日切片不可延伸为 10 月 18 日或现在的状态。

## 1. 四级判据、证据规则与否决项

| 层级 | 拟验收判据（必须按顺序独立记录） | 证据不充分时的状态 |
|---|---|---|
| L1 身份 `IDENTITY_CONFIRMED` | 经授权的只读现场证据将**指定监听 PID → 可核对的进程映像/命令行 → 代码或部署版本 → owner/启动源**闭合；同时核对实际绑定地址、反代上游归属、端口是否被其他服务抢占。缺失命令行/多实例/配置矛盾不得猜测归属。 | `IDENTITY_UNVERIFIED`；尤其 8093 不准归因为 org registry。 |
| L2 监听 `OBSERVED_LISTENING` | 在记录时间窗内针对**指定地址与端口**留存本机监听/PID；工具只报告 `up=true` 时只能说“该端口有历史观察”，无法替代 L1。 | `UNVERIFIED`；`optional_offline=true` 仅为门禁策略，不是健康或故障豁免。 |
| L3 健康 `HEALTH_CHECK_PASSED` | L1/L2 成立后，owner 审过目标代码/处理器及代理链，确认该具体 GET 无持久化、无隐式任务、无审计落盘、无外部写侧；在**另行批准的窗口**取得状态码、受限响应的预期字段、时间戳、来源与上游身份。非 2xx/超时/跳转/响应不符均不得通过。 | `UNVERIFIED`；根页 200、计划任务注册或通用 `/health` 名称均不能自动算过。 |
| L4 业务 `BUSINESS_READY` | L1–L3 均完成，且 owner 定义并签认只读业务契约：关键依赖的数据来源/新鲜度/投影正确性/鉴权边界与生产验证隔离，各项有独立证据及独立复核。响应成功但数据为空或旧、错上游、读到了错误 state 均不可通过。 | `UNVERIFIED`；生产放行另需正式批准，验收分层不是变更批准。 |

- **顺序与不可推断**：L2 甚至 L3 先被观察，也不能倒推 L1；同一个 HTTP 200 不得升级为 L4。四级记录分开打时间戳、窗口及来源，不用笼统 `healthy` 抹平缺口。对外状态取最弱必需项，发生冲突即 `UNVERIFIED`；高风险操作准入另为 `BLOCKED`，不表示服务离线。
- **证据规范**：每条未来观察须含 `ticket`、审批引用、执行人与独立复核人、UTC ISO 8601 采样时间和时区、目标 bind IP/port/路径/方法、请求头的脱敏摘要、禁止重定向标记、超时/次数/字节上限、PID/映像/命令行可得性与代码版本、HTTP 状态/安全响应摘要、链路每一跳、读取范围、异常、证据文件路径及 SHA-256；不记录密钥、cookie、完整敏感 body。PID 与路由配置需**同一窗口**关联，不把跨日切片拼成一张现况图。
- **不可运行的候选**：本文件中的路径均是**待 owner 静态审核的探针候选**，不是可直接执行的命令。即使 GET，也可能产生日志、审计写入或触发计算；在证明无副作用前不允许 live 调用。禁止 `tools/check_ports.py --probe`、任意泛化 `GET /api/*`、POST/PUT/PATCH/DELETE、自动重试/轮询/跟随重定向或经代理扩大到未经审查的上游。优先在纯离线 mock/临时隔离环境验证响应结构和副作用，再申请精确的只读现场窗口。

## 2. 逐端口分层矩阵与候选探针

| 端口/静态角色 | L1 身份门槛 | L2 历史与未来门槛 | L3 健康安全候选（**未执行**） | L4 业务 READY 门槛（**未执行**） |
|---|---|---|---|---|
| **8787 Hermes 生产** | 运行矩阵称生产 WebUI、`portal/nginx.conf` 将 `/hermes/` 指向 8787；GOV-FIX-006 只见 `python.exe` PID 28032，映像路径/命令行空。未来须由 Hermes owner 补 PID→实际 server/版本、启动源及生产 state/data 解析路径；不能把静态路径视作现场值。 | GOV-FIX-006 本地 2026-10-17 19:28:26–19:29:10 +08:00 见 `127.0.0.1:8787` 监听、根页 HTTP 200；当前 `UNVERIFIED`。未来只读确证同一时刻端口与 PID。 | 静态 `Hermes/hermes-webui-next/api/routes.py` 存在 `GET /health`，且 `?deep=...` 会进入额外检查；**仅建议先在离线副本审计无 query 的 `/health` 调用链**（stream/run/accept 检查的副作用及依赖）。待 owner 证明无写侧、确定实际进程运行此版本并批准后，才可考虑单次直连回环 GET、禁重定向、限定 body；`status=ok`/HTTP 200 仅算 L3，不用 `deep=1`、`/api/health/agent` 或 `/api/system/health` 代替。根页 200 只是既有历史证据。 | 由 Hermes owner 签认认证/会话读取契约及依赖边界，明确生产 `/hermes/` 每一跳与实际生产 state 根，仅通过脱敏配置/只读元数据验证；不能登录、新建会话、发送模型请求来做 smoke。L4 尚无无写安全契约。 |
| **8788 Hermes 验证** | GOV-FIX-006 历史 PID 35756 的命令行指向 `C:\aios\Hermes\hermes-webui-next\server.py`，但非当前身份或可执行启动命令。运行矩阵声明 `Hermes/webui-state-next`、8787 声明 `Hermes/webui-state`；静态 `api/config.py` 的 `STATE_DIR` 可由 `HERMES_WEBUI_STATE_DIR`/`HERMES_HOME` 改写，默认路径未必等于矩阵，**实际 state 隔离尚未证实**。未来双实例在同一窗口核对各自有效环境、配置、规范化实际 state/data 路径、链接/挂载和写权限，生产/验证根不可同址、父子覆盖或经 junction 互通。 | 同一 GOV-FIX-006 历史窗口见 `127.0.0.1:8788` PID 35756 与根页 200；当前 `UNVERIFIED`。两端页面长度相同不代表共享数据或隔离已成立。 | 如果现场确认运行同一已审静态版本，可把**验证实例自己的** `GET /health`（无 deep）列候选，需先证明无写侧及不连生产 state；只可记录 8788 本身 L3，不能把 8788 的结果用来证明 8787。未审环境一律不可调用。 | 必须证明验证端不写生产 state/live 数据、独立凭证与访问日志路径、只读隔离业务契约；不得创建测试会话试写验证。`DEC-20261017-03` 保留/下线/Shadow 均仍 `BLOCKED`，不能因候选健康合格而自动处置。 |
| **8791 KG 数据看板** | `tools/check_ports.py` 注册为 `kg_data_dashboard.py`，运行矩阵声明 KG 看板及 `KgDataDashboard` 任务；这只是静态标识。未来需找到 owner 认可的实际入口代码/版本、PID、绑定地址和任务→脚本→解释器→产物链；未找到静态 handler 证据前不得假定存在 `/health`。 | 历史门户严格端口清单 UTC 2026-10-17 11:18:15–11:18:35Z 标 `up=true`，GOV-FIX-006 **没有检查此端口**；当前监听/PID、HTTP 均 `UNVERIFIED`。 | **尚无已审且证明无副作用的 8791 端点**。owner 应先静态定位具体 GET 路由与读取范围，在离线桩验证无写侧、无调度、无后台刷新；若没有，L3 保持 `UNVERIFIED`，不能试探 `/health` 或根页后宣称通过。 | 只读契约由 KG owner 指定固定数据集/字段、新鲜度窗口、时区、空数据语义及依赖（数据根、调度、图数据库）；通过 owner 认可的**脱敏离线快照/元数据**证明，不碰 `quant_backtest/data` live 根、不运行同步启动器、不创建 `KGDataRoot`。无契约前 L4 `UNVERIFIED`。 |
| **8093 身份冲突** | 历史 `tools/check_ports.py`/门户端口清单将 8093 标为**数据仓管理预览 `daily_ui_preview_server.py`**，运行矩阵与 `tools/org_registry_server.py`/共享资源登记则将其指向 **org registry**。端口号或路由声明不能消除冲突。先静态核对候选启动源/路由版本，再在批准窗口完成 PID→命令行→实际 handler/上游的独立归属；确认为预览服务时不得签认 registry。当前明确 `IDENTITY_UNVERIFIED`。 | 历史严格清单仅在 UTC 2026-10-17 11:18:15–11:18:35Z 标 `up=true`；GOV-FIX-006 **没有检查此端口**，没有该时点 PID/HTTP。当前 `UNVERIFIED`。 | `tools/org_registry_server.py` 的 `do_GET` **先调用 `_observe(...)`，再 `_enforce(...)`**；即便是 `GET /api/org`、`GET /api/agent/knowledge` 也可能产生审计写侧，且 `/api/org` 返回 `audit_tail`，知识端点读取个人知识。**两者不是已经证明无副作用的健康探针；目前不得 live GET**。当实际处理器确认为该 registry 版本时，`GET /` 虽返回页面，却同样先执行 `_observe`：该函数先 `makedirs`，再向 `TOOL_AUDIT` 追加审计（写失败仅 fail-open），**绝不可 live 调用作无副作用探针**；根页 200 也不能证明 8093 身份或数据面。未知预览服务代码亦不可猜 `/health`。先离线审计 `_observe` 的持久化行为、读取敏感性及实际处理器版本，再由 owner 提供专门无写/受限响应的健康契约；未补之前 L3 `UNVERIFIED`。 | 必须先 L1 解决预览与 registry 冲突，再经单独审批、静态代理路由核对及契约测试证明 `/org`→对应数据 API→实际 8093 上游、数据 schema/脱敏/权限均正确。**当前审阅的 `portal/nginx.conf` 静态配置中，`location = /org` 和统一 `location /api/` 均指向 8091，未见 8093 专用转发；这与运行矩阵称 `/api/org`、`/api/agent/knowledge` 特殊转 8093 冲突。**8091 应用是否另行转调、实际运行 nginx 配置及端到端链路均未核，不得推断 8093 数据面已连通，维持 `UNVERIFIED/BLOCKED`；`/org` 页面 200 不等于 API READY。禁止以 GET 观察写审计或访问冻结账本完成验证；若契约需要触碰这些资源，先改用纯离线 fixture 或另立审批，本设计不授权。 |

> 端口清单的 `up` 是历史工具输出，不包含探针响应。特别是历史 GOV-FIX-006 只覆盖 **8787、8788**（及范围外 8080/8734/7897），**不覆盖 8791/8093**。Hermes 静态代码中 `/health` 的存在也不能证明当日对应进程加载了该代码，更不能证明该 GET 已完成副作用审查。

## 3. 无副作用探针的准入设计

1. **离线先行**：owner 对目标服务的精确 GET handler、认证/重定向/中间件、调用图、日志/审计和缓存/后台线程做静态逐段审查；用 temp/mock 隔离文件、依赖和网络，证明请求前后限定目录/外部写调用均不变。若读敏感数据或写审计，**不是无副作用探针**，应改造专用只读健康端点并单独审批，而非静默豁免。
2. **请求白名单**：写明 `method=GET`、准确 `127.0.0.1:port/path`（仅实际 bind 与 owner 签认后）、禁止 query/caller 参数、禁止 cookie/token/测试用户输入（若鉴权必须带凭证，另审密钥处理）、`redirects=0`、`retries=0`、最大请求数 1、短超时、响应长度上限及结果脱敏；代理路径需单独列链路和上游，不能将直连与代理混写为同一证据。禁止 `HEAD` 未核 handler 语义便替代 GET。
3. **优先顺序**：静态 handler + 离线无写测试 → L1 身份签认 → L2 同窗口监听证据 → 已批准的 L3 小范围只读 GET → 与探针不同的 L4 只读业务契约。若其中一步需要访问冻结账本/生产 state 或具写侧，立即停在上一步、标 `UNVERIFIED/BLOCKED`，转人工决策，不因缺失证据扩大现场探测。
4. **失败与暂停**：身份矛盾、绑定不符、响应重定向到未知上游、403/404/5xx、数据泄露/异常大响应、审计或 state 写入疑虑、超时以及窗口过期时停止后续步骤并保全时间戳证据。**不得自动修复、切换端口、启动服务或使用宽范围 Python 停进程。**

## 4. 授权窗口、隔离和回滚字段模板（待人工填写）

下列字段是**将来提交单项工单时必须补全的申请模板**，所有空值目前均 `UNVERIFIED`，共享资源登记不是审批。至少把“只读采样”、“新增健康接口/路由”、“8788 处置”、“8093 身份/数据面变更”拆为独立申请，不能打包借健康验收取得写权限。

| 分类 | 工单必填字段与判据 |
|---|---|
| 范围与责任 | `ticket`、服务/port/bind、单项 `objective` 与 `non_goals`、owner/approver/executor/observer **实名**、相应决策号（8788 对应 `DEC-20261017-03`）、风险级别、`rollout_mode=read-only/offline`；须准确标记 8093 身份冲突。不得以文档作者冒充运行 owner。 |
| 资源和时间 | UTC 起止窗口及时区、窗口失效规则、`shared_resources`（Hermes 生产/验证 state、KG live 根、8093 registry/预览、nginx/计划任务等）、`allowed_paths`、`forbidden_paths`、精确端点/HTTP 方法/请求次数/超时/字节上限、并行互斥；若需读生产 state 或账本，当前设计**不允许**，另行单项审批。 |
| 变更前保护 | 历史证据链接和时间切片、当前来源版本/配置哈希、同窗口身份与监听证据、适当的前置快照或由 owner 托管的恢复点；不得为了验收擅自生成/访问冻结账本快照或读取生产 state 内容。8787/8788 须由获授权人员证明解析后 state/data 根不交叠（含 junction/挂载/外部依赖）。 |
| 验证与停止 | 离线无副作用测试记录、明确批准的安全 GET 候选及预期响应、身份→监听→健康→业务的分级证据路径、错误/歧义即时停止条件、独立复核签字；四级全过也仅形成验收材料，不自动授予发布/启停。 |
| 回滚与结果 | `rollback_owner`、回滚触发阈值、备份位置/版本/哈希、精确配置/路由/state 恢复步骤及权限、最长恢复时间和独立验证；只读采样**没有运行态变更，回滚为停止采样并撤回本次新增报告**，不对服务做动作。任何未来获批的代码/路由/8788 处置需单独演练前进与回退，先备份再最小化恢复；回滚结果和时间戳追加到新证据，不覆盖历史原件。 |

## 5. 当前结论与本文件自身回滚

- 8787/8788：仅有 2026-10-17 历史监听与根页 200；**当前身份完整性、当前健康、业务 READY 与两实例 state/data 实际隔离都未证实**。8787 生产、8788 验证不可互证、不可互相处置。
- 8791：仅有历史端口清单 `up=true`，HTTP 与业务均无证据。8093：仅有历史 `up=true`，**预览 vs org registry 身份冲突必须 `IDENTITY_UNVERIFIED`**，其 GET handler 可能写审计，不具无副作用探针资格。
- 本次完成的是文档设计 **NOT_EXECUTED**；没有形成任何 live 新证据、启停/路由/state 变更或人工批准。若撤回设计，仅在另行受控文档工单中撤回**本新增文件**，不改/删历史 GOV-FIX-006、runbook、原始证据或运行态。
