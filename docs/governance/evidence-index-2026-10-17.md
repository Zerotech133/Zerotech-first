# GOV-MIN 证据索引（只读）

- **索引版本**：`evidence-index-2026-10-17.v1`
- **生成时间**：`2026-10-17T21:51:46.6054502+08:00`
- **维护 owner**：`software-engineer`（受 `team-lead` 授权）
- **适用工单**：`GOV-MIN-001`、`GOV-MIN-003`、`GOV-MIN-004`、`GOV-MIN-005`、`GOV-MIN-006`、`GOV-MIN-007`
- **运行模式**：只读扫描与新建索引；未修改、移动、重命名或删除任何既有文件，未触碰生产服务、账本、数据根、端口或计划任务。
- **事实源原则**：本索引只提供定位、元数据、事实范围、状态和复现命令；原始证据仍是事实源，不复制、改写或替代原文。
- **状态枚举**：`VERIFIED`（路径及内容/状态可按索引范围核验）、`MISSING`（目标证据不存在或不可定位）、`UNVERIFIED`（只能确认元数据，或事实范围/版本/复现关系仍不足）。未确认项不得视为通过。
- **敏感信息纪律**：不记录密码、Token、私钥、个人敏感数据或秘密内容；本索引未计算或写入任何新的 hash。仅引用事实源中已明确给出的 hash。

## 1. 最小正式证据记录

> 记录键为 `ticket_id + evidence_id`。同一证据的后续附件或版本应追加新的 `E02`、`E03` 等键，不覆盖历史记录；旧版本应标记 `superseded`。

| evidence_id | ticket_id | evidence_type | title / summary | canonical_path（绝对路径） | source_system | owner | created_at / updated_at | version / revision | sha256 / hash | status | sensitivity | related_ticket / dependency | verifier / verified_at | verification_method | notes / gap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `GOV-MIN-001-E01` | `GOV-MIN-001` | `index` | 本批治理证据索引；记录覆盖范围、字段规范、版本和核验记录 | `C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md` | repository / governance docs | `software-engineer` | 2026-10-17T21:51:46+08:00 / 写后核验时间见第 3 节 | `evidence-index-2026-10-17.v1` | 未计算 | `VERIFIED`（目标文件已新建并完成只读回读） | governance | `GOV-MIN-003/004/005/006/007`；依赖 T01 | `software-engineer` / 2026-10-17T21:51:46+08:00 | 写入前 `Test-Path` 确认不存在；写后 `Get-Item` 与 `Get-Content -Raw` 只读核验 | 索引本身的最终字节数/mtime 以第 3 节为准，避免自引用漂移 |
| `GOV-MIN-003-E01` | `GOV-MIN-003` | `path_matrix` | E 盘路径逐文件迁移矩阵；应证明 canonical/deprecated 路径、环境/机器、权限与验证状态 | `C:\项目\老田的AI助手\docs\governance\path-migration-matrix-2026-10-17.md` | repository / governance docs | 待指定 | 未知（目标文件不存在） | 未知 | 未提供 | `MISSING` | governance | `GOV-FIX-002`；依赖路径审计 | `software-engineer` / 2026-10-17T21:51:46+08:00 | `Test-Path -LiteralPath` 与治理目录只读枚举 | 架构文件将其列为 T02 交付物；当前未发现目标文件，不能以 `GOV-FIX-002` 计划文字替代矩阵 |
| `GOV-MIN-004-E01` | `GOV-MIN-004` | `regression_result` | 门户回归用例/执行记录；包含导航、mixin、路由差异、端口严格检查及只读约束 | `C:\项目\老田的AI助手\docs\governance\portal-qa-2026-10-17.txt` | portal / governance docs | `portal-qa`（文档未给实名 owner） | 文档日期 2026-10-17 / 文件 mtime `2026-10-17T11:19:58.4032967Z` | 文档未声明 revision | 未提供 | `VERIFIED`（文件可定位且内容包含命令、退出码、结果和限制） | governance; portal metadata | `GOV-FIX-005`；依赖 portal/tools 只读检查 | `software-engineer` / 2026-10-17T21:51:46+08:00 | 读取原件；核对 `unify_nav --check`、`_mixin_integrity.py --check`、`route_baseline.py --check`、`check_ports.py --strict --json` 的记录 | 记录明确未修改 nginx、基线或生产服务；新增路由/新增函数提示仍需负责人审阅，不等同生产批准 |
| `GOV-MIN-005-E01` | `GOV-MIN-005` | `readonly_projection` | 账本只读投影定义及字段映射；应证明 source、snapshot/version、刷新状态、时区和只读边界 | `C:\项目\老田的AI助手\docs\governance\ledger-candidate-projection-2026-10-17.md` | repository / governance docs | 待指定 | 未知（目标文件不存在） | 未知 | 未提供 | `MISSING` | governance; ledger metadata | `GOV-FIX-004`；依赖两个账本只读快照 | `software-engineer` / 2026-10-17T21:51:46+08:00 | `Test-Path -LiteralPath` 与治理目录只读枚举 | 现有 `GOV-FIX-004-ledger-preservation-2026-10-17.md` 和 `GOV-FIX-004-recheck-2026-10-17.md` 是保全/复核记录，不替代指定的外部投影交付物；不得回写账本 |
| `GOV-MIN-006-E01` | `GOV-MIN-006` | `runbook` | 运行手册 Draft；应证明适用范围、RACI、健康检查、故障/回滚及未验证标识 | `C:\项目\老田的AI助手\docs\governance\runbook-drafts-2026-10-17.md` | repository / governance docs | 待指定 | 未知（目标文件不存在） | 未知 | 未提供 | `MISSING` | governance; runtime metadata | `GOV-FIX-006`、`GOV-FIX-003`；依赖已验证运行事实 | `software-engineer` / 2026-10-17T21:51:46+08:00 | `Test-Path -LiteralPath` 与治理目录只读枚举 | 已存在的端口/数据根只读记录可作为事实源，但不能将其推测扩展为 runbook 已交付；未知启动命令必须保持 `UNVERIFIED` |
| `GOV-MIN-007-E01` | `GOV-MIN-007` | `test_readiness` | Agent Platform 测试准备包；应证明环境/权限/依赖、smoke/权限/异常/幂等用例和退出准则 | `C:\项目\老田的AI助手\docs\governance\platform-test-environment-2026-10-17.md`（环境报告）及 `C:\项目\老田的AI助手\docs\governance\platform-contract-test-matrix-2026-10-17.md`（契约矩阵） | repository / governance docs | 待指定 | 未知（两个目标文件均不存在） | 未知 | 未提供 | `MISSING` | governance; platform metadata | `GOV-FIX-007`；依赖独立 `zhice-os-v2` 树环境门 | `software-engineer` / 2026-10-17T21:51:46+08:00 | 两个目标均以 `Test-Path -LiteralPath` 只读核验 | 架构设计明确环境门；在 Python/pytest/依赖/权限未确认前，不得宣称契约测试通过。两个文件若后续分别生成，应追加为独立 evidence_id，不覆盖本记录 |

### 覆盖率统计

- 正式纳入工单：6 项（`001/003/004/005/006/007`）。
- 正式记录数：6 条（每项至少一条）。
- 记录覆盖率：`6 / 6 = 100%`（覆盖不等于全部已核通过）。
- 当前 `VERIFIED`：2 条（`GOV-MIN-001-E01`、`GOV-MIN-004-E01`）。
- 当前 `MISSING`：4 条（`GOV-MIN-003-E01`、`GOV-MIN-005-E01`、`GOV-MIN-006-E01`、`GOV-MIN-007-E01`）。
- 当前 `UNVERIFIED`：0 条正式记录；已知未验证事实见各条 `notes / gap`。
- 由于正式记录少于 10 条，验收抽样规则为全量核验；本次已对 6 条全量定位/核验。

## 2. 事实源目录与补充定位记录

以下记录用于复现和解释正式证据状态；不替代上表正式工单记录。

| ref_id | 用途 / 事实范围 | canonical_path（绝对路径） | 存在性 / 类型 | size / mtime（采集时） | status | 可复现只读命令 | 限制 / 关联 |
|---|---|---|---|---:|---|---|---|
| `SRC-001` | 系统设计、T02 文件清单、允许路径、状态和回滚规则 | `C:\项目\老田的AI助手\docs\system_design.md` | 存在 / file | 17,427 bytes / `2026-10-17T13:48:09.2336684Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\system_design.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 设计是范围依据，不是运行态事实；版本未单独声明 |
| `SRC-002` | GOV-MIN-001 工单目标、允许范围、验收和回滚 | `C:\项目\老田的AI助手\docs\governance\next-minimal-workorders-2026-10-17.md` | 存在 / file | 7,309 bytes / `2026-10-17T12:11:27.5053825Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\next-minimal-workorders-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 计划/工单卡片不能代替实际证据；关联 GOV-MIN-001 |
| `SRC-003` | 共享资源默认只读、窗口、scope 和回滚纪律 | `C:\项目\老田的AI助手\docs\governance\shared-resource-register.md` | 存在 / file | 2,486 bytes / `2026-10-17T10:13:50.5279258Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\shared-resource-register.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 登记表不是生产写权限授权 |
| `SRC-004` | 总控事实、门户/端口/账本缺口和禁止动作 | `C:\项目\老田的AI助手\docs\governance\governance-control-ledger-2026-10-17.md` | 存在 / file | 8,011 bytes / `2026-10-17T12:13:02.7275757Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\governance-control-ledger-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 运行态结论需以其记录时间和现场证据为界；关联 GOV-FIX-003/004/005/006 |
| `SRC-005` | 并行治理任务和“发现共享写面只登记不顺手修”规则 | `C:\项目\老田的AI助手\docs\governance\parallel-governance-board-2026-10-17.md` | 存在 / file | 1,300 bytes / `2026-10-17T10:13:48.5254405Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\parallel-governance-board-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 工作板状态不是证据通过结论 |
| `SRC-006` | 人工批准、禁止动作、回滚与 blocked 门槛 | `C:\项目\老田的AI助手\docs\governance\manual-approval-decision-register-2026-10-17.md` | 存在 / file | 9,818 bytes / `2026-10-17T11:42:45.7536755Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\manual-approval-decision-register-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 所有决策当前均为待填/blocked，不构成生产授权 |
| `SRC-007` | 数据根静态审查、catalog 事实、启动器判据及未验证项 | `C:\项目\老田的AI助手\docs\governance\GOV-FIX-003-data-root-readonly-2026-10-17.txt` | 存在 / file | 6,834 bytes / `2026-10-17T11:32:15.6515208Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\GOV-FIX-003-data-root-readonly-2026-10-17.txt' | Select-Object FullName,Length,LastWriteTimeUtc` | 原文明确未运行启动器；原文已给出日志/启动器 hash，本索引不重复计算 |
| `SRC-008` | 端口/portproxy/HTTP 只读核验 | `C:\项目\老田的AI助手\docs\governance\GOV-FIX-006-port-readonly-2026-10-17.txt` | 存在 / file | 2,927 bytes / `2026-10-17T11:30:23.9313627Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\GOV-FIX-006-port-readonly-2026-10-17.txt' | Select-Object FullName,Length,LastWriteTimeUtc` | 现场时点为 2026-10-17；不能推断后续状态 |
| `SRC-009` | 门户导航/mixin/路由/端口回归执行记录 | `C:\项目\老田的AI助手\docs\governance\portal-qa-2026-10-17.txt` | 存在 / file | 8,098 bytes / `2026-10-17T11:19:58.4032967Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\portal-qa-2026-10-17.txt' | Select-Object FullName,Length,LastWriteTimeUtc` | `route_baseline` 报告 22 条新增路由；不等于已批准纳入基线 |
| `SRC-010` | 账本保全和外部处置边界（非投影交付） | `C:\项目\老田的AI助手\docs\governance\GOV-FIX-004-ledger-preservation-2026-10-17.md` | 存在 / file | 5,917 bytes / `2026-10-17T10:30:57.4514579Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\GOV-FIX-004-ledger-preservation-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 不是 `GOV-MIN-005-E01` 指定投影；原始账本仍只读 |
| `SRC-011` | 账本保全复核记录 | `C:\项目\老田的AI助手\docs\governance\GOV-FIX-004-recheck-2026-10-17.md` | 存在 / file | 3,628 bytes / `2026-10-17T11:22:57.0632725Z` | `VERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\GOV-FIX-004-recheck-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc` | 是复核事实源，不替代外部投影 |
| `SRC-012` | T02 账本原始源（仅登记，不写入） | `C:\项目\老田的AI助手\.worktrees\tasks.json` | 存在 / file | 145,702 bytes / `2026-10-17T13:43:11.3563870Z` | `UNVERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\.worktrees\tasks.json' | Select-Object FullName,Length,LastWriteTimeUtc` | 本索引只核验元数据，未解析、未计算 hash；账本内容/统计需 GOV-MIN-005 专项投影 |
| `SRC-013` | T02 Agent Platform JSONL 原始源（仅登记，不写入） | `C:\项目\老田的AI助手\agent_platform\data\org\tasks\tasks_202610.jsonl` | 存在 / file | 16,466 bytes / `2026-10-17T02:14:51.8548414Z` | `UNVERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\agent_platform\data\org\tasks\tasks_202610.jsonl' | Select-Object FullName,Length,LastWriteTimeUtc` | 本索引只核验元数据，未解析、未计算 hash；不得把 raw/history/current 统计写成已核验 |
| `SRC-014` | T02 代码事实源（路径/账本相关调用方，仅登记） | `C:\项目\老田的AI助手\portal\portal_api.py` | 存在 / file | 210,407 bytes / `2026-10-17T11:39:37.5380247Z` | `UNVERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\portal\portal_api.py' | Select-Object FullName,Length,LastWriteTimeUtc` | 本索引未进行代码语义分类；需 GOV-MIN-003 专项审计 |
| `SRC-015` | T02 代码事实源（路径/账本相关调用方，仅登记） | `C:\项目\老田的AI助手\portal\api_portfolio.py` | 存在 / file | 125,517 bytes / `2026-10-12T17:14:42.0000000Z` | `UNVERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\portal\api_portfolio.py' | Select-Object FullName,Length,LastWriteTimeUtc` | 本索引未进行代码语义分类；需 GOV-MIN-003 专项审计 |
| `SRC-016` | T02 代码事实源（自动提交/账本调用方，仅登记） | `C:\项目\老田的AI助手\portal\agent_auto_commit.py` | 存在 / file | 10,561 bytes / `2026-10-12T08:39:40.0000000Z` | `UNVERIFIED` | `Get-Item -LiteralPath 'C:\项目\老田的AI助手\portal\agent_auto_commit.py' | Select-Object FullName,Length,LastWriteTimeUtc` | 本索引未进行代码语义分类；不能据文件存在性推断运行态行为 |
| `SRC-017` | 架构指定但尚未交付的 GOV-MIN-003 路径矩阵 | `C:\项目\老田的AI助手\docs\governance\path-migration-matrix-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\path-migration-matrix-2026-10-17.md'` | 关联 `GOV-MIN-003` |
| `SRC-018` | 架构指定但尚未交付的 GOV-MIN-005 外部投影 | `C:\项目\老田的AI助手\docs\governance\ledger-candidate-projection-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\ledger-candidate-projection-2026-10-17.md'` | 关联 `GOV-MIN-005` |
| `SRC-019` | 架构指定但尚未交付的 GOV-MIN-004 专项报告 | `C:\项目\老田的AI助手\docs\governance\portal-regression-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\portal-regression-2026-10-17.md'` | 关联 `GOV-MIN-004`；现有 `portal-qa` 为事实源 |
| `SRC-020` | 架构指定但尚未交付的 GOV-MIN-006 runbook 草稿 | `C:\项目\老田的AI助手\docs\governance\runbook-drafts-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\runbook-drafts-2026-10-17.md'` | 关联 `GOV-MIN-006` |
| `SRC-021` | 架构指定但尚未交付的 GOV-MIN-007 环境报告 | `C:\项目\老田的AI助手\docs\governance\platform-test-environment-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\platform-test-environment-2026-10-17.md'` | 关联 `GOV-MIN-007`；不得宣称环境可用 |
| `SRC-022` | 架构指定但尚未交付的 GOV-MIN-007 契约矩阵 | `C:\项目\老田的AI助手\docs\governance\platform-contract-test-matrix-2026-10-17.md` | 不存在 / missing | 无 | `MISSING` | `Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\platform-contract-test-matrix-2026-10-17.md'` | 关联 `GOV-MIN-007`；不得宣称契约测试通过 |

## 3. 写后核验与操作记录

### 3.1 写后文件元数据

- **目标路径**：`C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md`
- **写入前检查**：`Test-Path -LiteralPath 'C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md'` 返回 `False`；未覆盖既有文件。
- **写入后检查**：`Get-Item -LiteralPath 'C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md' | Select-Object FullName,Length,LastWriteTimeUtc`；文件存在且为普通文件。
- **写后读取检查**：`Get-Content -LiteralPath 'C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md' -Raw` 成功读取；已核对索引标题、版本、正式记录、覆盖率、缺口和回滚章节均存在。
- **采集/核验时间**：`2026-10-17T21:51:46.6054502+08:00`（本次只读核验记录）。
- **源文件变更纪律**：本次未对任何源文件执行写入、移动、重命名或删除；未计算新的 hash，未触碰 live 数据根、账本、门户、nginx、端口、计划任务或服务状态。

### 3.2 可复现的只读核验命令

```powershell
$index = 'C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md'
Get-Item -LiteralPath $index | Select-Object FullName, Length, LastWriteTimeUtc, Attributes
Get-Content -LiteralPath $index -Raw | Select-String -Pattern 'evidence-index-2026-10-17.v1','覆盖率统计','回滚方式'
```

对任一源文件仅做元数据核验：

```powershell
Get-Item -LiteralPath '<absolute-source-path>' | Select-Object FullName, Length, LastWriteTimeUtc, Attributes
```

对缺失证据仅做存在性核验：

```powershell
Test-Path -LiteralPath '<absolute-missing-path>'
```

## 4. MISSING / UNVERIFIED 清单

### MISSING

1. `GOV-MIN-003-E01`：`C:\项目\老田的AI助手\docs\governance\path-migration-matrix-2026-10-17.md`
2. `GOV-MIN-005-E01`：`C:\项目\老田的AI助手\docs\governance\ledger-candidate-projection-2026-10-17.md`
3. `GOV-MIN-006-E01`：`C:\项目\老田的AI助手\docs\governance\runbook-drafts-2026-10-17.md`
4. `GOV-MIN-007-E01`：`C:\项目\老田的AI助手\docs\governance\platform-test-environment-2026-10-17.md` 与 `C:\项目\老田的AI助手\docs\governance\platform-contract-test-matrix-2026-10-17.md`
5. 补充缺失：`C:\项目\老田的AI助手\docs\governance\portal-regression-2026-10-17.md`（架构列出的 GOV-MIN-004 专项报告；不影响 E01 使用已存在的 `portal-qa` 事实源）。

### UNVERIFIED

1. `SRC-012`：`.worktrees\tasks.json` 仅确认存在和元数据，未做账本解析或 hash 复核。
2. `SRC-013`：`tasks_202610.jsonl` 仅确认存在和元数据，未做 JSONL 解析或 hash 复核。
3. `SRC-014`：`portal_api.py` 仅确认存在和元数据，未做调用方/读写语义分类。
4. `SRC-015`：`api_portfolio.py` 仅确认存在和元数据，未做调用方/读写语义分类。
5. `SRC-016`：`agent_auto_commit.py` 仅确认存在和元数据，未推断运行态行为。
6. `GOV-FIX-003` 原文列出的启动器运行、逐表一致性、并发写入和计划任务行为，仍保持原文的未验证标识。
7. `GOV-FIX-006` 原文的端口结论仅适用于其记录的 2026-10-17 现场时点，不延伸到后续运行态。
8. Agent Platform 独立树 Python/pytest/依赖/权限和契约测试状态未确认；不得宣称 GOV-MIN-007 通过。

## 5. 回滚方式

- 本交付仅新增本索引文件；回滚时删除 `C:\项目\老田的AI助手\docs\governance\evidence-index-2026-10-17.md` 即可。
- 回滚不得删除、修改、移动或重命名任何既有证据、账本、数据根、门户或配置文件。
- 若删除前需留存审计痕迹，应先由负责人按新工单登记外部存档位置；本工单不执行删除。
- 发现未登记写入、越权路径、生产进程受影响或源文件异常时，应立即停止后续动作，保留现场证据并将状态升级为 `BLOCKED`；不得通过覆盖索引掩盖异常。
