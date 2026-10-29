# 曹操下属能力迭代（每人一个迭代）

| 席位 | 角色 | 域 | 版本 | 本轮目标能力 | 学习材料 | 实践载体 | 导师 | 状态 |
|---|---|---|---|---|---|---|---|---|
| ARCH1 曹操 | 研发主官 · 架构师 | architecture | v1 | 研发计划拆解与验收标准 | 总纲 §5/§6 + WBS 模板 | 待定（下一任务） | A0 | planned |
| DEV1  | 后端工程师 · G1 组长 | backend | v1 | 质量门与 code review 判据 | devtask 质量门 + AGENTS 红线 | WO-P0-DEV1-MONDAY-20261027 | ARCH1 | planned |
| DEV2  | AI/模型工程师 | ai | v1 | 模型评估与降级策略 | model_tiers + fallback 链 | 待定（下一任务） | DEV1 | planned |
| DEV3  | 量化策略工程师 | quant | v1 | 策略数据契约与回测口径 | v1.1 白名单/字段契约 | 待定（下一任务） | DEV5 | planned |
| DEV4  | 回测/风控工程师 | quant | v1 | 风控判据与 Go-No-Go 复核 | backtest_registry + 判据冻结 | 待定（下一任务） | DEV5 | planned |
| DEV5  | 前端工程师 · G2 组长 | frontend | v1 | 前端设计 tokens 与组件规范 | tokens.css + unify_nav + 设计规范 | WO-P0-DEV5-EVALCASES-20261027 | ARCH1 | planned |
| DEV6  | 后端工程师 | backend | v1 | 数据管道与可观测 | dev_metrics_daily + 指标口径 | WO-P0-DEV6-TRACE-20261027 | DEV1 | planned |
| DEV7  | 后端工程师（标准化交付） | backend | v1 | 标准化交付（L1/L2 已启动） | seat_learning DEV7 计划 L1–L4 | WO-P0-DEV7-WIKI-20261027 | DEV1 | planned |
| DEV8  | 前端工程师 | frontend | v1 | 数据可视化与空错态 | 作战屏 v1/v1.1 页面 | WO-P0-DEV8-METRICS-20261027 | DEV5 | planned |
| DOC1  | 技术文档工程师 | docs | v1 | 文档/索引自动化 | gen_docs_index + 文档规范 | 待定（下一任务） | DEV5 | planned |
| OPS1  | 运维/SRE 工程师 | devops | v1 | 部署/重启演练与五要素 | start_portal_api.ps1 + 重启规范 | 待定（下一任务） | DEV1 | planned |

> 规则：v1 基线 → 学习/实践/评估 → 导师 pass + 证据 → version+1；AI 不代签、评估无证据不得标 passed。

## 迭代 1 启动（2026-10-30 · DSH-Lead）

- **状态**：kickoff_issued（本表各席 planned → 待确认；席位回执后改 in_progress）。
- **目标**：每席完成 1 项能力目标 + 1 个实践载体 + 证据链，11-10 前首批评估。
- **时间**：11-03 前各席确认目标/载体/导师；11-10 首批评估（D+14 门禁）。
- **回执格式（工单 note / 周报）**：席位 | 目标能力 | 实践载体 | 证据（commit/测试/文档） | 导师 | 结论（pass/redo）。
- **导师**：组长（DEV1/DEV5）或 A0；DSH 汇总进周报「能力迭代闭环」栏。
- **边界**：不改 org/权限/服务；实践载体必须真实任务，不为评估造样例。

