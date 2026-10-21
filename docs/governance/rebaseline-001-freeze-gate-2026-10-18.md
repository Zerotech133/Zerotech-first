# REBASELINE-001 写入来源冻结与审批准备单

> 文档性质：控制面审批准备材料；不是账本快照、投影、recheck 或正式基线。
> 创建时间：2026-10-18（中国标准时间）
> 当前状态：`BLOCKED`
> 关联决策：`rebaseline-001-decision-record-2026-10-18.md`
>
> 本单创建时不读取、不解析、不校验、不运行任何账本 writer；不修改 `.worktrees/tasks.json`、Agent Platform JSON/JSONL、路由正式基线或服务配置。

## 一、不可变决策边界

- 正式路由基线：维持 72 条。
- 新增路由：继续隔离，不生成 `CANDIDATE`，不纳入正式基线。
- 账本：继续保全，不投影、不生成 current recheck、不回写、不修复、不归档、不删除、不重排、不补字段。
- Legacy `.worktrees/tasks.json` 与 Agent Platform JSON/JSONL：两个独立事实源，不合并、不互相推断 owner。
- REBASELINE-001 状态保持：`DRIFT_DETECTED / HUMAN_REVIEW_REQUIRED / CONTINUOUS_LEDGER_DRIFT / BLOCKED`。

## 二、待人工签认的 RACI（不得用默认执行人替代）

| 事实源域 | ledger_owner | source_owner | approver | executor | observer | 状态 |
|---|---|---|---|---|---|---|
| Legacy `.worktrees/tasks.json` | 待指定 | 待指定 | 待指定 | 待指定 | 待指定 | `UNVERIFIED/BLOCKED` |
| Agent Platform JSON/JSONL | 待指定 | 待指定 | 待指定 | 待指定 | 待指定 | `UNVERIFIED/BLOCKED` |

说明：封存记录中的 owner=老田、executor=WorkBuddy、observer=老田，仅表示本次一次性只读封存的角色，不等于账本写入 owner 或 writer owner。

## 三、写入来源声明表（仅限静态证据）

每个候选 writer 必须由人工填写并附现有代码、配置或变更记录路径；不得以历史执行人、目录存在、HTTP 200 或单次观测替代来源证据。

| writer canonical absolute path | repo/component | 写入动作 | 目标事实源域 | 触发工单/变更号 | 预期字段/范围 | rollback | source_owner 签认 | ledger_owner 许可 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| 待登记 | 待登记 | 待登记 | Legacy / Agent Platform | 待登记 | 待登记 | 待登记 | 待签 | 待签 | `UNVERIFIED` |

### 3.1 静态代码索引候选（未执行、未读取账本）

以下条目仅来自既有源文件的静态符号/路径索引，用于后续人工确认“谁可能写、写入哪个事实源”。本节不证明运行态调用关系，不证明近期曾写入，也不授予任何执行权限。所有条目均必须由 **source_owner 与 ledger_owner 双签** 后方可从 `UNVERIFIED/PENDING` 进入下一状态；任一方未签认均保持 `BLOCKED`。

| writer canonical absolute path / symbol | repo/component | 静态观察到的写入动作 | 目标事实源域（待确认） | 触发工单/变更号 | 预期字段/范围 | rollback | source_owner 签认 | ledger_owner 许可 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\tools\devtask.py::_save`（请求中的 `save_tasks` 候选；静态索引未发现同名 `save_tasks` 定义） | legacy devtask CLI | 原子写回 `.worktrees/tasks.json`（临时文件后 replace） | Legacy `.worktrees/tasks.json`（待确认） | 待登记 | 待人工确认工单状态字段及保留/删除语义 | 待人工确认；不得在冻结窗口执行 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |
| `C:\项目\老田的AI助手\tools\legion_worker.py::save_tasks` | legion worker | 临时文件写入后 replace `.worktrees/tasks.json`；文档声明状态变化会写入 tasks.json | Legacy `.worktrees/tasks.json`（待确认） | 待登记 | 待人工确认 worker 状态、lease/failure 字段范围 | 待人工确认；不得在冻结窗口执行 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |
| `C:\项目\老田的AI助手\agent_platform\domains\devtask_wiring.py::save_tasks` | Agent Platform devtask wiring | 原子写回 `.worktrees/tasks.json`，并执行增量结构校验 | Legacy `.worktrees/tasks.json`（待确认） | 待登记 | 待人工确认变更工单及审批凭据范围 | 待人工确认；不得在冻结窗口执行 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |
| `C:\项目\老田的AI助手\agent_platform\engine\tasks.py::TaskStore.open` / `TaskStore.move` | Agent Platform TaskStore | 以 append-only 方式追加完整任务快照至 `agent_platform/data/org/tasks/tasks_<YYYYMM>.jsonl` | Agent Platform JSON/JSONL（待确认） | 待登记 | 待人工确认 task snapshot、status/history、操作者字段 | 待人工确认；append-only 不得删除或改写历史行 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |
| `C:\项目\老田的AI助手\agent_platform\adapters\task_adapter.py`（只读适配器） | Agent Platform task adapter | 静态索引显示读取 canonical JSONL 并生成归一化报告；未作为 writer 认定 | Agent Platform JSON/JSONL（只读候选，待确认） | 待登记 | 待确认是否存在任何间接写入调用；当前不得推断 | 删除外部报告；不触碰事实源 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |
| `C:\项目\老田的AI助手\agent_platform\tests\test_task_adapter.py`、`test_devtask_wiring.py`（测试/写入接口覆盖） | Agent Platform task adapter/devtask wiring tests | 静态索引显示测试覆盖适配器及写入接口；测试文件本身不是生产 writer | Legacy / Agent Platform（待确认） | 待登记 | 待确认测试 fixture、临时根与调用边界 | 删除测试产物；不得触碰 live 账本 | 待双签 | 待双签 | `UNVERIFIED/PENDING` |

**静态索引限制**：`tools/devtask.py` 的实际符号为 `_save`，不是 `save_tasks`；`agent_platform/adapters/task_adapter.py` 当前文档声明为只读，不能仅因测试或适配器引用而归因写入。上述差异必须由 source_owner 与 ledger_owner 分别确认后，才可建立 writer→ledger 映射。冻结窗口内禁止运行这些 writer、测试或任何可能触发写入的入口。

### 3.2 候选清单口径

本节原有的泛化候选已由上方 **3.1 静态代码索引候选** 取代；3.1 以实际静态符号为准（例如 `tools/devtask.py` 为 `_save`，并非 `save_tasks`）。不得将泛化名称视为已确认 writer，也不得在冻结窗口运行候选入口。

## 四、一次性只读冻结窗口登记字段

冻结窗口尚未授权，以下字段必须由人工补齐；填写本单不授予生产写入或账本读取权限。

| 字段 | 要求 | 当前值 |
|---|---|---|
| ticket | 独立审批工单号 | `REBASELINE-001-FREEZE`（待人工确认） |
| resource | 两个事实源及相关 writer 控制面 | 待登记 |
| owner / approver / executor / observer | 实名或 agent_id，逐项签认 | 待指定 |
| start / end | ISO 8601，含时区 | 待指定 |
| mode | 必须为 `read-only` | `read-only`（待批准） |
| allowed paths | 明确允许读取的控制面文件/元数据 | 待指定 |
| forbidden paths | 账本内容、writer 执行面、生产写面 | 固定禁止 |
| stop condition | 任一来源继续写入、字段缺失或证据冲突 | 固定中止 |
| rollback | 取消窗口、保留证据、回到 BLOCKED | 固定规则 |

## 五、冻结前后元数据证据（不解析账本）

仅在取得单独授权后填写。每个事实源分别记录绝对路径、size、mtime、SHA-256、采集时间和采集者；不得覆盖历史证据。

| 事实源域 | canonical path | phase | size | mtime | SHA-256 | collected_at | collector | result |
|---|---|---|---:|---|---|---|---|---|
| Legacy | 待授权后登记 | before | 待采集 | 待采集 | 待采集 | 待采集 | 待指定 | `PENDING` |
| Legacy | 待授权后登记 | after | 待采集 | 待采集 | 待采集 | 待采集 | 待指定 | `PENDING` |
| Agent Platform | 待授权后登记 | before | 待采集 | 待采集 | 待采集 | 待采集 | 待指定 | `PENDING` |
| Agent Platform | 待授权后登记 | after | 待采集 | 待采集 | 待采集 | 待指定 | `PENDING` |

判定规则：任一事实源 before/after 的 size、mtime 或 SHA-256 变化，冻结窗口失败，状态保持或恢复 `BLOCKED`；不得据此修复、回滚或归因账本。

## 六、路由口径冲突（独立于账本审批）

封存包的现场输出记录“基线 72 条 vs 当前 106 条”，列出 34 条差异；人工决策记录保留“23 条新增”的历史观察口径。两者当前均不得用于批准。

处理规则：

1. 不覆盖历史封存证据；
2. 不在本单中重新扫描或重算；
3. 来源冻结后，由授权人工逐条登记差异的来源、用途、owner、审批、回滚；
4. 由独立 QA 复核口径和证据时间切片；
5. 路由审批与账本审批分离。

## 七、控制面状态迁移

仅允许以下迁移：

```text
BLOCKED
  -> READY_FOR_APPROVAL   （冻结条件全部满足 + 路由口径冲突完成授权人工复核 + 独立 QA 通过）
  -> BLOCKED               （任一写入、漂移、证据缺失、责任未签认或口径冲突未解决）
```

`READY_FOR_APPROVAL` 仅表示审批包完成，不代表批准，不触发账本执行、生产写入、投影或基线切换。

## 八、当前验收结论

```text
owner/source/approver/observer：未齐
writer 声明：未齐
冻结窗口：未登记并未授权
冻结前后元数据：未采集
路由 23/34 口径冲突：未解决
独立 QA：门禁复核 PASS（不等于审批或放行）
最终状态：BLOCKED
```

下一步只能是：人工补齐 RACI、writer 声明和冻结窗口授权。未完成前，不读取或修改账本，不运行 writer/计划任务，不生成 current recheck、投影、candidate 或正式基线。
