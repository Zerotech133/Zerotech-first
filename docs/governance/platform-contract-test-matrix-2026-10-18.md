# GOV-MIN-007｜owner/role、heartbeat、Artifact、replay 契约测试矩阵（设计）

> 2026-10-18；**所有用例 `NOT_EXECUTED`；`GOV-MIN-007=BLOCKED`**。矩阵只定义未来隔离测试的输入、预期与边界；不证明老田仓契约存在于独立 zhice 树，不证明任何测试通过。环境核验见[独立树环境报告](platform-test-environment-2026-10-18.md)。

## 不可跨越的测试边界

- 范围锚点：[原工单 GOV-MIN-007](next-minimal-workorders-2026-10-17.md#gov-min-007agent-platform-测试准备)；[REBASELINE-001 决策](rebaseline-001-decision-record-2026-10-18.md)和[冻结门禁](rebaseline-001-freeze-gate-2026-10-18.md)优先。不得读/解析 `.worktrees/tasks.json`、任何 Agent Platform JSONL；不得运行 writer、测试套件、生产服务或探端口；本报告未执行测试、未触碰数据库和外部系统。
- `ZH` = `C:\项目\zhice-os-v2-windows-20260920\code`，当前仅静态证实路径；`LT` = `C:\项目\老田的AI助手\agent_platform`。这不是等价映射。`ZH` 的 `api/dependencies_ownership.py`、`api/routers/my_trading.py`、`agent_platform/runtime/watch_agent.py`、`api/routers/replay.py` 等与 `LT` 的统一契约 `contracts/objects.py`、`adapters/heartbeat.py`、`engine/artifacts.py`、`adapters/replay_query.py` 属不同代码域。跨域统一心跳、Artifact、回放需求在 `ZH` 中仍 `SOURCE_UNVERIFIED`；在 owner 确认前只作为“待裁定的目标契约”，不得凭 `LT` 测试声称 `ZH` 通过。
- 每项拟议测试运行前必须：获 Agent Platform 负责人及项目负责人明确批准适用代码树/契约版本和测试窗口；独立唯一 **OS temp root**，与 LT/ZH 仓根及所有 live 账本、报告目录、PG、portal 状态隔离；合成数据、内存 SQLite、mock 时钟/认证/外部通知/文件源；禁网、禁真 PG、禁端口监听；核对所有测试 import 与 fixture 不能创建持久对象。允许测试写入仅发生在 temp root，结束后删除，记录其文件清单；异常时先停止并保留隔离临时根供复核，不尝试修复 live 数据。`tests/conftest.py` 默认 memory 守卫不替代逐项审查；不运行全套 pytest/默认 CLI。
- **回放硬禁**：LT `adapters/replay_query.py:60-64,110-115,142-152` 的 `_IDEM_DB`/`_APPROVALS_DB` 为模块级真实路径，`_load_idem_rows()`、`_load_approvals()` 均**不接受 root**；`replay(root=temp)` 只能将部分源（如 events/artifacts）定向，**绝不能**视作所有源隔离。任何导入或调用回放前，必须有经批准的实现级源注入/重定向和调用前路径防护，逐一证明 idempotency/approval/events/artifacts 全部只指向 temp root；达不到即保持 `NOT_EXECUTED / BLOCKED`。
- 任何会触及真实账本/approval/replay 语义修改、portal 发布、数据库迁移的尝试，必须另立工单、人工双签并取得独立窗口；当前批准表 [DEC-20261017-07](manual-approval-decision-register-2026-10-17.md)仍为 `blocked`。`REBASELINE-001` 继续 `BLOCKED`。

## 契约案例

所有行 `NOT_EXECUTED`；`权限`栏指未来运行所需**共同授权条件**（上节全部条件）外的额外裁定；不代表当前已授权。

| ID / 领域 | 静态目标来源 | 将来的隔离输入 / 操作 | 期望契约断言 | 权限、差距与结果 |
|---|---|---|---|---|
| O01 owner 范围 | ZH `api/dependencies_ownership.py:33-80`、`api/routers/my_trading.py:99-178` | 两名合成用户、各自实例 + 无归属 legacy，对 plans/positions/replays/approvals 做本人、他人、不存在读（mock JWT、in-memory repo） | owner 从 JWT 而非请求参数推得；非 owner/不存在/legacy 单资源 404 等价，列表仅本人的实例；不泄露他人存在性。 | 禁生产 token/DB；先审路由 import 与会话依赖。`NOT_EXECUTED`。 |
| O02 role 权限 | ZH `tests/test_user_role_orm.py:14-59`、`tests/test_rbac_viewer.py`、`api/dependencies.py` | 合成 ADMIN/TRADER/VIEWER/USER，匿名/无效 token，鉴权读与禁止写访问 | 允许角色矩阵由 owner 逐接口批准；不符合权限的角色不能越权，拒绝不产生持久写；不能从 ORM CHECK 推断 API 授权结果。 | 角色表与跨域 admin 行为待 owner 认可；memory SQLite/fake token，`NOT_EXECUTED`。 |
| O03 owner 持久映射 | ZH `tests/test_execution_ownership.py:72-151`、`domain/replay/replay.py:18-41` | 合成 `user_strategy_id`/`account_id` 对交易/回放对象 round-trip | 两个不同含义字段各自 round-trip 保真且关联正确（不要求两个 ID 相等）；legacy None 不被误认 owner；不得由 role 反推 owner。 | mock repository + 内存 SQLite；不跑原有套件，`NOT_EXECUTED`。 |
| H01 心跳样本校验 | LT `adapters/heartbeat.py:28-73,99-125`；ZH `scheduler.py` 的 heartbeat 与 LT 不是同一契约 | **仅待 owner 归属裁定**的合成有效/缺 source/非 bool alive/非法 checked_at 样本；mock clock | 样本先校验后开写；非法输入无临时根目录/账本副作用；有效行字段、版本与分钟粒度可追溯。 | LT writer 只能在获批另单时向唯一 temp root 写；本工单禁止运行。ZH 对应边界 `SOURCE_UNVERIFIED`，`NOT_EXECUTED`。 |
| H02 心跳幂等、降级 | LT `adapters/heartbeat.py:75-125,140-186` | NullProbe/显式 FileProbe 模拟；同 source+minute 重放、下一分钟新样本、坏状态文件 | 未接线明确 `alive=False / not_configured`，同键只有一条、下一分钟追加；坏输入 fail-visible；不得伪称真实服务健康。 | fake 文件只在 temp root；禁止真探针/端口；ZH 对应 `SOURCE_UNVERIFIED`，`NOT_EXECUTED`。 |
| H03 ZH 调度心跳边界 | ZH `scheduler.py` 中 `_write_heartbeat`；`agent_platform/runtime/watch_agent.py:63-79`；`metrics.py:107-125` | mock scheduler/file/metrics，不启动调度器及 HTTP server，注入合成 context | 周期/心跳时间与来源可分辨；故障不伪报 alive；禁止写缺省 `/tmp/v2_scheduler_heartbeat` 和报告目录。 | 先审调度模块 import 副作用、默认目标目录；`NOT_EXECUTED`。 |
| A01 Artifact 指纹/来源 | LT `contracts/objects.py:71-104`、`engine/artifacts.py:78-144`；ZH 同一登记仓未定位 | 合成预计算 hash、source_system/ref、produced_by、workitem_id、missing、path，临时 SQLite | `effective_hash` 优先 content_hash、否则 fact_hash；只记录指针而非复制文件内容；同 hash 全局幂等返回原 ID，已有登记字段读回保真。仓本身不读取 path 文件也不计算其 sha256；文件字节与登记 hash 一致性须另立调用者契约或设计。 | 仅待两树契约归属裁定后在临时 DB；ZH 实现 `SOURCE_UNVERIFIED`，`NOT_EXECUTED`。 |
| A02 supersede / append-only | LT `engine/artifacts.py:145-202` | 新旧不同 hash 的版本链，同 hash 重放，不存在的旧 ID | revision 新增且指回旧 ID，历史不删除；不存在的旧 ID fail-visible；同 hash 不重复新行。 | 禁 live artifacts.db；仅临时 SQLite + owner 授权，`NOT_EXECUTED`。 |
| A03 只读查询不建库 | LT `engine/artifacts.py:81-103,170-202` | 临时根下不存在的 DB 和已有临时 DB，用 `read_only=True` 逻辑作静态审查后拟议隔离测试 | 缺库明确失败但不创建库/WAL；已有库只读查询无状态变更。 | 严禁默认可写构造器指向真实库；`NOT_EXECUTED`。 |
| R01 owner/replay 查询 | ZH `api/dependencies_ownership.py:63-68`、`api/routers/my_trading.py:68-80,113-116,152-164` | 合成归属 replay、非 owner 与 legacy；mock JWT、内存 repo | owner 可见、他人与 legacy 隐藏；重复查询不改变状态。 | 原 `api/routers/replay.py:53-92` 是不同 legacy 全局路由，不可假定其同样 owner-scoped；`NOT_EXECUTED`。 |
| R02 带 `/` 的复盘 ID | ZH `api/routers/replay.py:70-92` | 人工构造带斜杠 replay_id；只用离线 ASGI transport/memory repository | `/replay/detail?replay_id=...` 精确查找；未找到 404；不得由路径吞掉字面量路由；鉴权与非 owner 边界按接口独立标注。 | 不能打真实 8102/API 或探端口；`NOT_EXECUTED`。 |
| R03 跨账本回放完整性 | LT `adapters/replay_query.py:7-35,60-64,88-105,110-160` | 合成 events/idempotency/approvals/artifacts、缺失源；仅在四类来源均通过经批准的实现级注入定向 temp root 并经调用前防护后拟议调用 | 按 trace/idem/target 串时间线，保留原生 status、来源、缺失标志；不把 review 映射 done，不跨账本猜 owner，不读真实 JSONL。 | **单传 `replay(root=temp)` 不隔离 `_IDEM_DB`/`_APPROVALS_DB`**；不可用此方式运行。先重构/注入全部源并单独获批，否则本项保持设计稿；ZH 对应 `SOURCE_UNVERIFIED`，`NOT_EXECUTED`。 |
| R04 approval/Artifact 回放审计 | LT `contracts/objects.py:107-131`、`adapters/replay_query.py:23-35,155-171`；ZH `agent_platform/learning/case_writer.py:49-95` 为另一条确认学习链 | 合成 target_id、审批人/时间、重复事件与 supersede；全桩对象；审批时 content_hash 仅作未接线差距记录 | 回放只断言现有可输出字段（审批 ID、幂等键、target_id、状态转移、批准人/理由摘要、decided_at 时间戳、trace_id）及时间线排序；不能从读取结果推断事件去重已实现。`replay_query` 当前 SELECT **不读取 content_hash**，不能声称回放验证批准时点哈希；若要验证此关联，需另行设计/审批实现及隔离注入。审批不代替执行，确认学习链不等同于统一跨账本 replay。 | 批准时点 hash 回放契约 `SOURCE_UNVERIFIED`；不调用 CaseWriter 实际写入；若需验证需只对临时 SQLite/fake PersistencePort 开单；`NOT_EXECUTED`。 |

## 现有测试的证据级别与放行

ZH `tests/test_execution_ownership.py`、`test_user_role_orm.py`、`test_agent_platform_learning.py` 有相关既存测试源码；`tests/conftest.py:10-12,47-73` 有 memory/PG 防护，但**源码存在 ≠ 本批已执行/通过**，也不能拿 LT 的 `tests/test_heartbeat.py`、`tests/test_artifacts.py`、`tests/test_replay_query.py` 结果冒充 ZH 契约。`pytest-cov` 当前已安装查询显示缺失（见环境报告）；测试框架可用不意味着完整依赖和运行隔离合格。

执行顺序仅供**授权后**参考：先签署来源/角色/安全边界与批准表 → 独立 temp root 与全链依赖注入、列出写路径和网络黑洞 → 静态审核具体测试和 fixture → 单项运行、记录**实际**命令/退出码/输出与 temp root 写入差异 → 独立 QA 复核与清理。任何失败都保持 `BLOCKED`，不得把报告中的“预期断言”改写为真实结果。当前矩阵自检结论：`IS_PASS: YES`（设计覆盖四类、测试状态真实），执行验收：`IS_PASS: NO / NOT_EXECUTED / BLOCKED`。
