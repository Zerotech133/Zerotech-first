# 并行治理总控台账

> 日期：2026-10-17
> 模式：短期扩张冻结 + 分层治理 + 低风险开发不停
> 当前权限：只读审计、测试、文档、离线实验；未授权共享生产写入、重启、迁移或账本清理。
> 下一步执行板：`docs/governance/next-minimal-workorders-2026-10-17.md`；人工批准清单：`docs/governance/manual-approval-decision-register-2026-10-17.md`。

## 一、总控结论

当前不执行停摆式治理，也不恢复跨域大功能扩张。治理窗口内保留单域、只读、测试、文档和 Mock/Shadow 实验；涉及统一入口、共享服务、数据根、任务账本、生产调度和交易执行的变更必须先立项、登记资源窗口、备份并获得人工批准。

运行态事实优先于文档声明：当前可访问门户为 `8080`；`8734` 是计划中的人类入口，但目前没有 portproxy，不能宣称已生效。

## 二、风险与行动台账

> 逐项签批入口：`docs/governance/manual-approval-decision-register-2026-10-17.md`。该清单仅记录待决策事项，不构成执行授权。

| 编号 | 领域 | 当前事实 | 风险 | 证据/来源 | 下一动作 | 人工批准 |
|---|---|---|---|---|---|---|
| GOV-13 | 路径与入口 | 8734 未配置 portproxy；8080 实际监听；`C:\aios` 指向当前 C 盘仓；portal paper/审批/历史/ops、自动提交和 jev 研究脚本仍有 E: 硬编码；`start_kg_sync.ps1` 优先 `C:\项目\KGDataRoot`，与唯一 live 根冲突 | P0 | `AGENTS.md:12-20`；`docs/PROJECT_DOCUMENTATION_INDEX.md:78-95`；`docs/CURRENT_RUNTIME_MATRIX_2026-10-17.md:7-37`；`portal/portal_api.py:2945-2950,3027-3109`；`portal/api_portfolio.py:1594,1645-1712`；`portal/agent_auto_commit.py:5,29-32`；`jev-eval/kg_time_audit*.py`；`quant_backtest/start_kg_sync.ps1:55-103`；运行态端口探测 | 分别建立入口修复、路径迁移、数据根优先级工单；先逐文件确认读写语义，禁止批量 E→C 替换 | 是，尤其是 portproxy、生产路径重构和数据根切换 |
| GOV-14 | 端口与服务 | 关键服务端口在线；最新只读端口证据显示 8080/8787/8788 HTTP 200、8734 无监听；7897 在本次探测未监听；历史严格门禁曾发现 7897 未登记，需以最新现场为准 | P0/P1 | `tools/check_ports.py`、`docs/governance/GOV-FIX-006-port-readonly-2026-10-17.txt` | 不自动修改白名单或注册表；若 7897 再出现，先确认归属；单独诊断 8788，不触碰 8787 生产实例 | 是，注册/白名单和验证实例操作 |
| GOV-15 | 任务账本 | 旧 `.worktrees/tasks.json` 216 条对象：done=167、aborted=45、review=3、doing=1；E: 历史路径 148 条且无活动任务指向旧 E:；master 13 条均为历史；两个异常 CLI 条目均 aborted；8 条缺字段。Agent Platform JSONL 24 行、可解析，过滤 1 event 后为 10 个当前任务（历史重复 13 行），按 task_id 最后快照统计当前状态 | P1 | `.worktrees/tasks.json`；`agent_platform/data/org/tasks/tasks_202610.jsonl`；`agent_platform/engine/tasks.py:49-66` | 只读备份、SHA-256、建立迁移映射；区分 raw/history=24 与 current=10；K-PUSH-010 stale 先人工确认；K-ORG-068/134 先确认孤儿/漏记，禁止直接删除 | 是，任何原地清理、状态修正或字段补齐 |
| GOV-16 | Agent Platform | 治理缺口集中在 Artifact 统一登记、portal mixin/approval/replay 测试、导航导出回归、heartbeat/日报统一投影、knowledge/skill 读侧 UI | P1 | `agent_platform/` 代码与测试审查 | 按单域拆成最小工单，先补测试与证据，不改变共享账本语义 | 跨域接口或账本写入需批准 |
| GOV-17 | 门户门禁 | `lineage.html` 已完成 defer 标签低风险修复；当前复核无路由丢失，mixin 守卫通过但仍有新增项提示；7897 未登记；`portal-qa-2026-10-17.txt` 已落盘并完成 2026-10-17 只读门禁复核 | P1 | `portal/lineage.html`；`tools/unify_nav.py`；`tools/_mixin_integrity.py`；门户复核回传；`portal-qa-2026-10-17.txt` 在仓库中未找到 | 补齐可复现门禁证据文件；核验新增路由批准状态；不得直接重建基线掩盖差异 | 生产页面/配置改动需批准 |
| GOV-18 | 运行文档 | Hermes、KG Dashboard、8093 registry 运行手册当前均未在仓库中找到，不能宣称已落盘或已验证；启动器返回 0 不等于 ready | P1 | `docs/governance/hermes-runbook-2026-10-17.md`、`docs/governance/kg-dashboard-runbook-2026-10-17.md`、`docs/governance/org-registry-runbook-2026-10-17.md` 均未找到 | 补齐运行手册并建立每次变更后的事实源复核；记录探测时间戳 | 启停与状态切换需批准 |
| GOV-19 | 统一入口 | 新版 `org.html` 已纳入 `/org`；旧 8091 SSR 路由运行态退出；`/api/org` 和 `/api/agent/knowledge` 由 8093 承接 | P1（已收口，需回归） | `portal/nginx.conf`、`portal/portal_api.py`、`portal/org.html` | 保持新版单一入口；补入口回归和数据契约测试 | 生产发布需批准 |

## 三、允许与禁止

### 允许继续

- 单域且不触碰共享生产面的代码开发；
- 只读审计、自动化测试、文档整理；
- Mock、Shadow、离线数据实验；
- 不写 live 数据根的验证脚本；
- 为单个工单补测试、运行证据、文档和回滚说明。

### 暂停或禁止

- 新增跨域大功能；
- 未登记的 nginx、端口、计划任务、Neo4j、Ollama、Hermes state 或 live 数据写入；
- 未备份的账本迁移、删除、批量路径替换；
- 直接重启生产服务或补建 8734 portproxy；
- 将测试端口 8788 的问题通过重启 8787 生产实例处理。

## 四、下一批最小工单

1. **GOV-FIX-001：8734 入口决策**：确认是否仍需要 8734；若需要，补 portproxy 前提交管理员批准、登记窗口、保存前后探测证据。
2. **GOV-FIX-002：路径迁移清单**：逐文件审查 E 盘硬编码，仅迁移确定为当前生产读写路径的条目；每项带回滚。
3. **GOV-FIX-003：数据根优先级**：确认 `quant_backtest/data` 为唯一 live 根，禁止 `KGDataRoot` 重新成为新写入根；先做离线选择器测试。
4. **GOV-FIX-004：账本保全与映射**：复制并校验旧账本，建立历史/当前双统计；不直接修改原文件。
5. **GOV-FIX-005：门户门禁**：补 `lineage.html` 导航、建立真实 mixin 基线、增加 `export_nav.py` 回归测试。
6. **GOV-FIX-006：7897/8788**：确认第三方进程归属并决定登记或白名单；隔离诊断 8788 验证实例。
7. **GOV-FIX-007：Agent Platform 最小治理**：优先补 Artifact 登记和 approval/replay 测试，保持 append-only 与 fail-open 约束。

## 五、工单准入标准

每个工单必须同时提交：

```text
代码/配置 + 测试 + 运行证据 + 文档更新 + 回滚说明
```

共享资源工单还必须在变更前登记：owner、窗口、scope、mode、rollback；完成后补 result 和证据路径。缺少并发证明、授权边界、持仓一致性、回滚证据或运行证据时，交易执行和持仓相关变更保持 `blocked`。

## 六、证据文件核查

截至 2026-10-17，对治理目录及仓库内同名路径进行只读核查：`baseline-2026-10-17.txt`、`portproxy-2026-10-17.txt`、`portal-qa-2026-10-17.txt`、`hermes-runbook-2026-10-17.md`、`kg-dashboard-runbook-2026-10-17.md`、`org-registry-runbook-2026-10-17.md` 均未找到。上述文件均保持“待补充/未验证”状态，不得据此声称证据已生成或运行已验证。

## 七、复核节奏

- 每日：检查共享资源是否有未登记写入、入口和端口是否与文档一致。
- 每周：复核 owner 覆盖率、越界修改、测试覆盖、回滚说明、运行态/文档差异、旧路径和重复启动器。
- 每个里程碑：以实际服务、端口、HTTP 响应和代码配置重新确认事实源，不以历史计划或单一测试结果替代运行态证据。
