# 架构总览（ARCHITECTURE.md）

> 零界 · 一个人的 AI 公司｜主站 `C:\项目\老田的AI助手`（ASCII 别名 `C:\aios`）
> 本文是**当前架构的权威简图**；细节以 `docs/adr/`、`docs/specs/`、`AGENTS.md` 为准。
> 首次编写：2026-10-27（参考开源项目架构文档实践；决定记录见 ADR-004）。

## 一、一句话

单机（Windows）个人 AI 平台：**门户 8734 一个人口** + 多个本地服务 + 数字军团（agent_platform）+ 投研/量化/飞书 bot 四条产线，凭据统一进 `_vault`，数据根唯一在 `quant_backtest/data`。

**产品定位（2026-11-04 A0 裁定 P5）**：零界 = 持续创业者的个人事业操作系统；量化/情报/学习是能力场景，不定义整个产品（产品定义唯一源见 `docs/plans/2026-10-24_PRODUCT_BASELINE_v1.md` v1.1）。

## 二、组件与端口（以 `tools/check_ports.py` REGISTRY 为唯一真源）

| 组件 | 路径 | 入口/端口 | 说明 |
|---|---|---|---|
| 门户（人类唯一入口） | `portal/` | **8734**（nginx 同源反代） | 页面/卡片/移动端，页面只写 8734 |
| 门户 API | `portal/portal_api.py` | **8091** | `/health`、`/api/analysis/*`、`/api/zhuge/*` 等 |
| 军团命令面 | `agent_platform/legion_command/serve.py` | **8796** | org/意图卡 contract；MCP 客户端（258 tools） |
| OSLINK 网关 | `agent_platform/gateway/serve.py` | **8795** | W4 隔离通道（P2b 自测票已过，P2c 暂缓） |
| Hermes | `Hermes/` | **8787** | 田丰旧代运行中；双代收口待批 |
| 数字军团引擎 | `agent_platform/` | 进程内/命令行 | 8 投研席 + 研发席；触发/心跳/审计/工具矩阵 |
| 飞书 bot | `portal/feishu_bot_ws.py` 等 | 出站 WS | 看护器 `run_bot_watchdog_loop.ps1`；运行态 `C:\项目\dsh_runtime` |
| 投研流水线 | `analysis/` | 命令行 | 数据根 `C:\项目\analysis_work`（2026-10-27 迁移） |
| 量化回测 | `quant_backtest/` | 命令行/夜间 | 数据根 `quant_backtest/data`（KG_DATA_ROOT） |
| 公共库 | `common/`、`pylibs/`、`tools/` | — | 工具/评测/质量门 |

## 三、数据与运行态边界

| 边界 | 位置 | 规则 |
|---|---|---|
| 唯一 live 数据根 | `quant_backtest/data` | 结构性操作前读 `C:\Temp\kg_sync_task.log`；L2 五要素 |
| 凭据 | `C:\项目\_vault\secrets`（明文，Phase 1）/`secrets.enc`（age 密文） | 不入 git、不进聊天、不写日志；见 `SECURITY.md` |
| 运行态（临时） | `C:\项目\dsh_runtime`、`C:\项目\analysis_work` | 可重建；不写回 `work` |
| 冷归档 | `C:\项目\_archive\` | 只搬不删；manifest 可逆 |
| 工作区会话 | `C:\项目\work`（逐步退役，A-2 搬移中） | 新代码不得依赖 |

## 四、信任边界（简化）

- **人类 → 8734/8091**：本机 127.0.0.1；对外经 nginx/隧道（白名单）；
- **bot → 飞书**：出站 WS，token 在 `_vault`；机器人只私聊白名单；
- **agent → 工具**：`agent_platform/tools/*` 只读优先；MCP server 分 read-only/风险级；`servers.json` 禁明文 token（`token_env`）；
- **L2 动作**：重启服务/改计划任务/动凭据区/备份链 → 五要素 + 老田批准后才执行（判例 K-STOCK-416）。

## 五、架构原则与关键决策

**原则**：[docs/architecture/principles.md](docs/architecture/principles.md)（P1 本地优先 … P9 文档即产品；评审引用编号）。

**ADR 索引**（`docs/adr/`，一决策一文件、编号连续、状态可 superseded）：
- ADR-001 repomap 自建；ADR-002 agents 导出单一源；ADR-003 dev 路由+工具矩阵；
- [ADR-004 文档体系（Diátaxis+ADR）](docs/adr/ADR-004-docs-taxonomy-diataxis.md)；
- [ADR-005 Agent 可观测（OTel GenAI 语义约定）](docs/adr/ADR-005-agent-observability-otel-genai.md)；
- ADR-006 worktree/devtask 隔离；ADR-007 工具矩阵单源；ADR-008 席位契约；
- ADR-009 端口注册/127.0.0.1；ADR-010 凭据 vault/token env；ADR-011 数据根与 work 退役；
- ADR-012 L2 五要素；ADR-013 单机范围与文档纪律；
- [ADR-014 Agent 运行契约 v1（run/event/conversation，Accepted；P2 部分落地）](docs/adr/ADR-014-agent-run-contract.md)；
- [ADR-015 平台内核契约与组合模型 v1（Proposed）](docs/adr/ADR-015-platform-kernel-composition-model.md)；
- [ADR-016 唯一宿主整合（Accepted；supersedes 2026-10-30 联邦方案）](docs/adr/ADR-016-single-host-integration.md)；
- [ADR-017 唯一开发框架与黄金路径（ZBF v1，Proposed）](docs/adr/ADR-017-framework-golden-path.md)；
- [ADR-020 研发军团作战屏 v1 数据契约（Accepted / M0 冻结；原与端口注册重号的 ADR-009 之一，2026-11-02 重编号）](docs/adr/ADR-020-dev-legion-screen-contract.md)。

> **平台唯一框架（ZBF v1，2026-11-02）**：[docs/architecture/PLATFORM_FRAMEWORK_v1.md](docs/architecture/PLATFORM_FRAMEWORK_v1.md)
> （整体复盘 + 社区对标 + 一套宿主/一层内核/一个组合模型/一条黄金路径/一道门禁 + Q1–Q4 质量分级）。
> 未来所有开发只允许三种形态：组合变更、实现替换、内核变更（ADR）；禁止第二入口/认证/DB/调度与临时脚本交付。

> **唯一宿主（ADR-016，2026-11-02 老田批准）**：人类唯一入口与平台宿主 = 零界（8734）；
> 智策不再是独立系统/独立入口/独立认证，收敛为量化域资产（内部 API + 页面包 + PG + 日管线 + 身份映射），
> L1 截止 2026-12-02 关停 5175/8102。组合清单见 `compositions/`，内核冻结清单见 `platform/kernel_v1.yaml`。

## 六、变更规则

1. 一单一事：`tools/devtask.py new <TID>` → worktree → 测试 → 评审门合并；
2. 架构级改动必须写 ADR（`docs/adr/`），接口/契约改动同步 `docs/specs/`；
3. 服务/计划任务/凭据区变更走 L2；重启后必须"新进程出生时间 > 文件 mtime"验证；
4. 文档与代码同一批提交（本文件随架构变化更新）。
