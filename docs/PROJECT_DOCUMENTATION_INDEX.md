# 老田的 AI 工作站：项目文档总索引

> STATUS: superseded by ../../DOCS_INDEX.md（现行文档索引，2026-10-25 起；本文档基线 2026-10-17，仅作历史索引保留）

> 文档基线：2026-10-17
> 维护原则：以代码、运行态和最新验收记录为事实源；历史计划/复盘只用于追溯，不得作为当前运行说明。

## 1. 项目定位

本项目是“零界 · 开源情报能力平台”的金融 AI 能力平台落地件：以本地数据、模型和自动化任务为基础，形成“采集 → 存储 → 分析 → 决策门禁 → 分发 → 门户展示”的闭环。当前核心场景是 A 股量化情报、组织化 Agent 编排、个人 AI 工作站和内容/OSINT 产线。

## 2. 当前事实源优先级

| 优先级 | 事实源 | 用途 |
|---|---|---|
| 1 | 运行中的服务、端口和实际 HTTP 响应 | 判断当前是否可用 |
| 2 | 代码与配置：`portal/nginx.conf`、`portal/portal_api.py`、`agent_platform/`、启动脚本 | 判断真实实现 |
| 3 | `docs/audit/` 最新审计与 `deliverables/page-audit/` 验收记录 | 判断已验证结论 |
| 4 | 本索引与架构复盘 | 提供导航和解释 |
| 5 | 历史 `docs/plans/`、旧日报、旧复盘 | 只做背景，不代表当前状态 |

冲突时按高优先级覆盖低优先级，并在审计文档中记录差异。

## 3. 目录地图

| 目录 | 当前职责 | 推荐入口 |
|---|---|---|
| `portal/` | nginx 门户、页面、8091 API 与页面路由 | `portal/nginx.conf`、`portal/portal_api.py` |
| `agent_platform/` | 统一任务账本、审批、证据、记忆、七/九域投影和 wiring | `agent_platform/docs/`、`agent_platform/engine/` |
| `quant_backtest/` | 行情数据、缠论、九维、回测、三线联动、数据仓 | `quant_backtest/数据中台架构设计.md`、`docs/ai_operator/` |
| `zerobound-kg/` | KG/数据大屏及数据同步相关组件 | `zerobound-kg/AGENTS.md`、`deliverables/zerobound-kg_数据库全景研究报告_20261017.md` |
| `Hermes/` | 本地超级助手 WebUI/Agent/MCP 链路 | Hermes 内部 README 与配置说明 |
| `news_collector/`、`jev-eval/` | 新闻、JEV 判断、推送与证据链 | `docs/ops/`、`jev-eval/docs/` |
| `gzh/`、`osint_video/` | 内容创作与视频号 OSINT 产线（**采集层：`C:\项目\res-downloader-py`**，规则嗅探+批量采集+知识提炼，2026-10-18 归属拍板） | 各目录 README 与最新审计 |
| `ops/` | 健康检查、运维脚本 | `ops/health_check.py` |
| `docs/` | 规范、计划、审计、运行手册、迁移材料 | 本文件及下文索引 |
| `deliverables/` | 对外交付、专项复盘和验收附件 | `deliverables/page-audit/` |

## 4. 文档分层

### A. 入口与使用

- 根目录 `README.md`：面向使用者的上手、门户入口、量化产线 SOP。
- `docs/runbooks/`：故障处置与人工操作步骤。
- 各模块 `README.md`：模块局部使用说明；若与运行态冲突，以代码和审计为准。

### B. 架构与设计

- `deliverables/项目全景复盘与定位_20261017.md`：最新项目全景定位与风险分级。
- `deliverables/老田的AI助手_全面复盘报告_20261016.md`：迁移前后全局审计背景。
- `deliverables/agent-watch-platform-architecture-20261016.md`：Agent Watch/管理平台架构。
- `deliverables/zerobound-kg_数据库全景研究报告_20261017.md`：KG/数据底座全景。
- `quant_backtest/数据中台架构设计.md`：量化数据中台设计。
- `docs/CURRENT_RUNTIME_MATRIX_2026-10-17.md`：当前端口、数据根、入口、启动与故障速查。
- `docs/PROJECT_GOVERNANCE_PLAN_2026-10-17.md`：并行开发期治理方案、量化执行核心阶段门与变更闸门。
- `docs/governance/`：共享资源登记、工单治理交付模板、周度治理评审模板和治理基线。
- `docs/plans/`：设计决策和实施计划，按日期追溯。

### C. 事实审计与验收

- `docs/audit/`：里程碑、代码审计、运行态审计和判例。
- `deliverables/page-audit/portal-audit.md`：页面与入口审计。
- `deliverables/page-audit/p1-navfix-log.md`：门户动线与统一导航验收。
- `deliverables/page-audit/org-cutover-validation.md`：组织架构新版收编验收。
- `deliverables/page-audit/cleanup-execution-log.md`：工作区清理执行记录。

### D. 迁移与恢复

- `docs/migration/README.md`：迁移材料索引。
- `docs/migration/README_换机恢复指南.md`：换机恢复操作指南。
- 同目录其余文件按“证据/执行记录/清单”保留，不得直接当作当前状态。

### E. 领域专项

- `quant_backtest/docs/ai_operator/`：AI 操作员各阶段计划、评审和验收。
- `agent_platform/docs/`：Agent Platform M0/M1/M2 验收和规则。
- `jev-eval/docs/`：JEV 专项研究。
- `osint_video/README.md` 与 `deliverables/osint-project-audit-review-2026-10-16.md`：视频号 OSINT 现状与审计。

## 5. 当前已确认架构

### 5.1 门户与页面

- 人类统一入口：`http://127.0.0.1:8734`（portproxy → nginx）；8080 仅为容器/局域网反代链路。
- 动态 API：`127.0.0.1:8091`，`portal/portal_api.py`，由计划任务守护。
- 组织注册表数据面：8093；8080 `/api/org`、`/api/agent/knowledge` 反代至 8093。
- 数据大屏：8791。
- Hermes：8787。
- `/org` 为新版静态 `org.html`；旧 8091 SSR 路由已退出运行态。
- `/approvals` 已由 nginx 精确反代到 8091。
- 导航唯一源：`api_common.py:DOMAIN_NAV_GROUPS` → `tools/export_nav.py` → `portal/static/nav.js`。

### 5.2 数据与模型

- 统一 live 数据根：`C:/项目/老田的AI助手/quant_backtest/data`。
- 顶层 `KGDataRoot` 已退役，不得作为新的数据写入根。
- KG、SQLite/PG/MySQL、Neo4j、DuckDB 等组件的具体状态以最新运行态审计为准。
- Ollama 为本地模型主通道，云端通道作为降级；不得在文档中把“已设计”写成“已上线”。

### 5.3 Agent Platform

M0 已收口；M1 已完成 GZH、Academy、Invest、Devtask、Pitch、Personal 等写侧/投影/回放/审批接线；M2 已完成证据仓、记忆门、Skill 目录、使用账本和 fact-check 实战。后续文档重点转向 M3 规划与业务域持续验收。

## 6. 运行入口速查

| 需要做什么 | 首先看 |
|---|---|
| 门户打不开 | `README.md` 故障速查；`portal/start_portal_api.ps1`；`C:/Temp/portal_api_*.log` |
| 修改门户页面/导航 | `portal/nginx.conf`、`portal/api_common.py`、`tools/export_nav.py` |
| 组织架构异常 | `portal/org.html`、8093 注册表服务、`deliverables/page-audit/org-cutover-validation.md` |
| 任务/审批/证据异常 | `agent_platform/docs/` 最新验收；`agent_platform/engine/` 与 adapters |
| 数据根或同步异常 | `quant_backtest/AGENTS.md`、`zerobound-kg/AGENTS.md`、数据中台设计 |
| 迁移/恢复 | `docs/migration/README_换机恢复指南.md` |
| 页面废弃/清理 | `deliverables/page-audit/portal-audit.md`，先只读盘点再确认删除 |

## 7. 文档状态标签

- `[CURRENT]` 当前实现和运行态已核验。
- `[AUDITED]` 有明确审计/回归证据，但可能不是实时状态。
- `[PLAN]` 设计或计划，尚未证明已实现。
- `[HISTORICAL]` 历史背景，仅供追溯。
- `[BLOCKED]` 有明确阻塞项。
- `[SUPERSEDED]` 被新文档或新实现替代。

现有历史文档暂不批量改名；以本索引和各目录 README 作为分类入口。

## 8. 维护规则

1. 新建文档必须写日期、范围、状态标签、事实源和结论。
2. 计划与验收分开：计划不得覆盖验收，验收不得省略未完成项。
3. 同一主题只保留一个“当前入口”；新增版本必须在旧文档顶部写 superseded 指向。
4. 运行端口、数据根、启动命令变更后，必须同步本索引与对应 runbook。
5. 不删除历史证据；废弃文档移动到 `docs/archive/` 前须确认没有脚本/链接依赖。
6. 每次重大变更在 `C:/项目/.workbuddy/memory/YYYY-MM-DD.md` 留一条长期有效摘要。

## 9. 当前文档缺口

- Hermes 与 8791 数据大屏缺少统一的“当前运行架构 + 故障处置”单页。
- 8093 org 注册表服务的启动/守护方式需要单独 runbook。
- 统一端口注册表、计划任务清单和数据服务清单尚未形成一份当前快照。
- 现有大量 `docs/plans/` 和 `quant_backtest/docs/ai_operator/` 文件仍按时间堆叠，需二次归档，但应在依赖扫描后执行。
- 根 README 仍保留部分历史路径/旧口径，后续应仅做增量修正，不进行大段无证据改写。
