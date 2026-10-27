# 架构原则（docs/architecture/principles.md）

> 本页回答"为什么代码/流程是这样"，评审时引用这里的原则而不是个人偏好。
> 配套：`ARCHITECTURE.md`（大图）、`docs/adr/`（决策记录）、`AGENTS.md`（开发宪法）。
> 参考 Home Assistant 的 Principles → Big picture → Decisions 三层结构（2026-10-27 引入）。

## P1 本地优先，数据归用户

- 能在本机跑的不依赖云；数据默认不出机器（外部依赖 GLM/Tushare 等按需且凭据受控）。
- 构建时：优先本地文件/本地服务；外部调用必须可失败、可回退。

## P2 唯一数据根，运行态与代码分离

- live 数据根只有 `quant_backtest/data`；分析产物在 `C:\项目\analysis_work`，飞书运行态在 `C:\项目\dsh_runtime`。
- 新代码**禁止**写回 `C:\项目\work`（该根正在退役）；结构性操作按 L2 走。

## P3 一单一事，隔离开发

- 无任务单不写码；一单 = 一个 worktree + 一条分支；禁止 master 直改。
- 合并必须过评审门（端口/Mixin/导航/凭据/L2）。

## P4 默认只读，最小权限

- 工具/集成/MCP 默认 read-only；写权限必须显式登记、最小 `allow_tools`、可审计。
- Agent 不持有超出其职责的工具（tool_matrix 单源）。

## P5 失败可见，不静默

- 异常必须留痕（日志/事件/状态），禁止裸 `except: pass`；缺数据如实报缺失，不编造。
- 面向用户的失败要回告原因与下一步。

## P6 L2 克制，五要素，一次一议

- 重启/停服/计划任务/凭据区/宿主机级操作 = L2：先出五要素（命令全文/影响面/不可逆点/替代方案/回滚），老田批准才执行；沉默≠同意。

## P7 可回滚，搬移不删除

- 文件搬移用 Move + manifest（sha256），可逆向；删除走回收站。
- 服务/配置变更必须写回滚步骤；数据根操作前先查互斥日志。

## P8 事件归因与可观测

- 开发动作进 `dev_events`（DEVTOOL_AGENT/IDE/MODEL）；Agent 链路有 trace/perf 账本；新增可观测按 `gen_ai.*` 映射（ADR-005）。
- 没有归因的动作视为未发生（无法审计 = 不可靠）。

## P9 文档即产品

- 架构变更写 ADR；行为变更写回执 + CHANGELOG；文档与代码同一批提交；`DOCS_INDEX --check` 必须 rc=0。
- 文档分类见 `docs/README.md`（Diátaxis）。

## 用法

- Code review / 判读时：引用编号（如 "违反 P4"）。
- 原则冲突时：P6（安全/审批）> P3/P4（隔离/权限）> P1/P2（本地/数据）> 其他；并在 ADR 里记录取舍。
- 修订原则：写 ADR 说明背景与影响，更新本页，`docs/README` 索引同步。
