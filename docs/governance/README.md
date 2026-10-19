# 治理控制面

> 当前模式：短期扩张冻结 + 分层治理 + 低风险开发不停
> 启用日期：2026-10-17
> 当前权限：仅限获授权范围的静态审计、隔离测试、文档与离线实验；共享生产写入、重启、迁移和账本清理均需单独批准。
>
> **2026-10-18 冻结令优先（P0）**：以 [REBASELINE-001 人工决策](rebaseline-001-decision-record-2026-10-18.md)、[冻结门禁](rebaseline-001-freeze-gate-2026-10-18.md)及[增量证据索引](evidence-index-increment-2026-10-18.md)为当前控制面口径。两个独立任务账本均禁止读取、解析、计算当前哈希、备份、双统计、投影和写入；本页下方第 3 条是历史复核设想，在 `BLOCKED` 期间**不可执行**。本页的 8080/8734 描述是 2026-10-17 历史观测，不代表当前运行态；“现场探测优先”仅说明证据权重，不授予主动探测、服务操作或生产访问权限。任何现场核验须另经 owner 与批准窗口授权。

## 入口

- [并行治理总控台账](governance-control-ledger-2026-10-17.md)：当前事实、风险、人工批准项和下一批最小工单。
- [人工批准决策清单](manual-approval-decision-register-2026-10-17.md)：8734、7897/8788、数据根、路径、账本和 Agent Platform 的逐项签批记录。
- [REBASELINE-001 决策记录](rebaseline-001-decision-record-2026-10-18.md)：维持 72 条正式路由基线；23 条历史口径与封存现场 34 条差异尚未对账；账本继续保全，状态仍为 BLOCKED。
- [REBASELINE-001 冻结门禁](rebaseline-001-freeze-gate-2026-10-18.md)：控制面 RACI、写入来源声明、只读冻结窗口和 READY_FOR_APPROVAL 门槛；当前仍 BLOCKED。
- [REBASELINE-001 门禁自检](rebaseline-001-freeze-gate-self-check-2026-10-18.txt)：静态文本自检与独立 QA PASS；不代表账本冻结或审批放行。
- [GOV-MIN-002 离线测试设计](kg-root-selector-offline-test-plan-2026-10-18.md)：选择器源码读取受阻，五场景仅设计、未执行；GOV-MIN-002 验收仍 BLOCKED。
- [GOV-MIN-003 路径迁移矩阵](path-migration-matrix-2026-10-18.md)：旧 E 盘路径静态归类，迁移实施与生产写入均未获批准。
- [GOV-MIN-004 门户路由审阅](portal-route-review-2026-10-18.md)：逐条记录封存 34 路由，历史 23/34 仍待对账，正式基线不变。
- [GOV-MIN-006 四服务运行手册草稿](service-runbooks-draft-2026-10-18.md)：8787/8788/8791/8093 的历史证据与未验证项；不证明当前在线或批准启停。
- [GOV-MIN 增量证据索引](evidence-index-increment-2026-10-18.md)：不覆盖 10/17 历史索引，区分 001–007 的交付、静态 QA、未执行与阻断状态。
- [Portal 两项待批安全变更](portal-safety-change-requests-2026-10-18.md)：模型 POST 防护与带写 GET 缓存契约分别立项；均为 PROPOSED/BLOCKED，未改生产代码。
- [下一批最小修复工单（历史模板）](next-minimal-workorders-2026-10-17.md)：原拟派单口径已被 REBASELINE-001 部分取代；尤其 GOV-MIN-005 当前 BLOCKED，不能依此执行。
- [共享资源登记表](shared-resource-register.md)：共享路径、端口、服务和变更窗口。
- [并行治理工作板](parallel-governance-board-2026-10-17.md)：GOV-13～GOV-18 工作线状态（若尚未落盘，以总控台账为准）。
- [工单治理模板](workorder-governance-template.md)：每项变更的测试、证据、文档和回滚要求。
- [周度治理评审模板](weekly-governance-review-template.md)：每周复核指标。

## 运行与证据

- `baseline-2026-10-17.txt`：治理启动基线；当前未在仓库中找到，待补充。
- `portproxy-2026-10-17.txt`：8734/8080 入口探测证据；当前未在仓库中找到，待补充。
- `portal-qa-2026-10-17.txt`：门户门禁回归证据；已落盘，记录 2026-10-17 只读门禁命令、退出码和风险。
- `hermes-runbook-2026-10-17.md`：8787 Hermes 运行手册；当前未在仓库中找到，待补充。
- `kg-dashboard-runbook-2026-10-17.md`：8791 图谱看板运行手册；当前未在仓库中找到，待补充。
- `org-registry-runbook-2026-10-17.md`：8093 组织注册表运行手册；当前未在仓库中找到，待补充。

## 当前硬闸门

1. 运行态事实优先于历史计划和文档声明：当前门户实际入口为 8080；8734 仍是未生效的计划入口。
2. 不在未登记窗口内写入 `quant_backtest/data`、Agent Platform 账本、Neo4j、nginx、计划任务、Hermes production state 或共享服务。
3. 不直接清理或改写历史任务账本；先备份、校验 SHA-256、建立历史/当前双统计和迁移映射。
4. 不批量把旧 E 盘路径替换为 C 盘路径；逐文件确认读写语义和回滚方案。
5. 任何工单必须交付：代码/配置、测试、运行证据、文档更新、回滚说明。

## 复核顺序

```text
运行服务/端口/HTTP
  -> 代码与配置
  -> 最新审计证据
  -> 当前文档
  -> 历史计划
```

当证据文件缺失或路径与实际不一致时，以现场探测结果为准，并将缺失证据列为治理待办；不得用“文件存在”替代“运行态已验证”。
