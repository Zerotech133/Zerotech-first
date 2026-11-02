# 零界项目 Wiki（自动生成）

> 由 `tools/project_wiki.py` 生成；`--check` 校验是否与仓库现状一致。
> 仓库地图/ADR 索引/约定入口的单一自动快照；人工正文请写 docs/ 对应专题。

## 1. 仓库地图

| 顶层目录 | Python 模块数（codebase_intel scope） |
|---|---:|
| `agent_platform/` | 628 |
| `portal/` | 481 |
| `tools/` | 1132 |

codebase_intel 统计：模块 **2241** / 符号 **26041**。

## 2. 核心模块（按路径）

| 模块 | 说明 |
|---|---|
| `agent_platform/__init__.py` | — |
| `agent_platform/a0_agent.py` | A0 总管 agent 实例化骨架 v0.1（K-ORG-006） |
| `agent_platform/a0_chief.py` | A0 总管日报聚合器（K-ORG-001 §4 日报通道落地，K-ORG-003） |
| `agent_platform/a0_evidence_judge.py` | a0_evidence_judge.py — A0 判读常驻流程（离线件，不挂 automation）。 |
| `agent_platform/adapters/__init__.py` | 只读事实源适配器。 |
| `agent_platform/adapters/approvals_registry.py` | M1-阶段二 Approval 注册表只读数据面（供门户 /api/platform/approvals 与 CLI）。 |
| `agent_platform/adapters/business_adapter.py` | M0 P5 业务域只读投影适配器（项目双源 / Academy / GZH）。 |
| `agent_platform/adapters/codex_bridge.py` | 军团 Codex bridge（M1 静态校验器 + M2 试点注册 + M3 子进程沙箱试跑）。 |
| `agent_platform/adapters/credential_trace.py` | 凭据轮次 vs 真源痕迹 交叉校验（只读纯函数，M1 审计层补强）。 |
| `agent_platform/adapters/event_adapter.py` | M0 事实源只读适配器。 |
| `agent_platform/adapters/event_replay.py` | M1-③ 事件回放统一查询层（只读数据面，供门户 /api/platform/replay 与 CLI）。 |
| `agent_platform/adapters/evidence_collect.py` | M2-P1 六域证据收编扫描器（真源只读，只写证据仓）。 |
| `agent_platform/adapters/heartbeat.py` | Paper 引擎心跳采集适配器（Paperclip 心跳对接第一刀，B 路）。 |
| `agent_platform/adapters/idempotency_audit.py` | M0 P3 幂等基线只读审计器。 |
| `agent_platform/adapters/lineage_freshness.py` | lineage_freshness：血缘新鲜度维度（P1-A，纯函数无 IO 核心）。 |
| `agent_platform/adapters/replay_query.py` | M1 统一事件回放查询层（全只读）。 |
| `agent_platform/adapters/sim_market_readonly.py` | SIM_MARKET 独立只读策略适配器。 |
| `agent_platform/adapters/strategy_replay.py` | strategy_replay：strategy 判决 × 影子账本 回放聚合视图（K-ORG-167 第三批）。 |
| `agent_platform/adapters/task_adapter.py` | TaskStore 与 legacy devtask 账本的只读双读适配器。 |
| `agent_platform/adapters/trace_link.py` | trace_link：投研链 trace 贯通映射层（M4-P1） |
| `agent_platform/adapters/unified_projection.py` | M1 统一投影只读聚合器（门户读侧唯一数据面）。 |
| `agent_platform/adapters/usage_ledger.py` | M2-P4 统一模型使用账本（全只读聚合器）。 |
| `agent_platform/analysis_digest.py` | 多角色分析摘要（2026-10-30 WO-INV-POOL-ROLES）。 |
| `agent_platform/bridge_inbox/processed/20261018_sma20_rsi14/strategy_20261018_sma20_rsi14.py` | Codex 试点策略：SMA20 趋势过滤 + RSI14 超买超卖双向门（军团-M2）。 |
| `agent_platform/command_hq/__init__.py` | — |
| `agent_platform/command_hq/consensus.py` | 指挥部 W-C3：讨论共识判定 + 飞书定版卡 dry-run + 决策回写（K-ORG-20261101-HQCONS）。 |
| `agent_platform/command_hq/execution_gate.py` | HQ L0/L1 执行门（W-C4，K-ORG-20261101-HQEXEC）。 |
| `agent_platform/command_hq/orchestrator.py` | HQ 指挥部编排器 MVP（W-C2，K-ORG-20261101-HQORCH）。 |
| `agent_platform/command_hq/role_contract.py` | 指挥部输出契约 RoleOpinion v1（2026-11-01 K-ORG-20261101-HQROLE / 专项计划 §3.2）。 |
| `agent_platform/command_hq/roles.py` | 指挥部五角色适配器：田丰/诸葛亮/赵云/黄忠/关羽（2026-11-01 K-ORG-20261101-HQROLE）。 |
| `agent_platform/conf_calibration.py` | D1 自报置信度后验校准探针（K-STOCK-086，方案 085-P0-B）。 |
| `agent_platform/contracts/__init__.py` | 统一研究、决策与模拟执行契约。 |
| `agent_platform/contracts/objects.py` | M1 统一对象模型补齐：WorkItem / Artifact / Approval。 |
| `agent_platform/contracts/sim_market.py` | SIM_MARKET 只读数据契约。 |
| `agent_platform/d1_calibration.py` | D1 置信度校准（P1，2026-11-01 WO-D1-CALIB-20261101）。 |
| `agent_platform/d1_daily.py` | D1（鲁肃）部门日报只读聚合（P1，2026-11-01 WO-D1-DAILY-20261101）。 |
| `agent_platform/d1_debate_quality.py` | D1 辩论质量评估（P1，2026-11-01 WO-D1-DEBATEQ-20261101）。 |
| `agent_platform/d1_decision.py` | D1 最终裁决核心（P0，2026-11-01 WO-D1-DECISION-20261101）。 |
| `agent_platform/d1_handoff.py` | D1 交棒回执（P1，2026-11-01 WO-D1-HANDOFF-20261101）。 |
| `agent_platform/d1_meeting.py` | D1 投研会运行时（P0，2026-11-01 WO-D1-MEETING-20261101）。 |
| `agent_platform/data/shadow/diff_ledger.py` | diff 影子账本基线快照与当前账本，输出当日新增行摘要 |
| `agent_platform/decision_outcomes.py` | D1 决策 outcome 台账（只读 sidecar，2026-10-29 · 鲁肃复盘整改 #2）。 |
| `agent_platform/domains/__init__.py` | M1 业务域写侧接线包。 |
| `agent_platform/domains/academy_wiring.py` | M1 业务域写侧接线 · Academy（研究院）。 |
| `agent_platform/domains/dev_wiring.py` | M1 软件开发域接线（域 #5）· TaskStore done 验收门。 |
| `agent_platform/domains/devtask_wiring.py` | M1 软件开发域接线 · devtask 工单线（与 dev_wiring.py 职责切分）。 |
| `agent_platform/domains/gzh_wiring.py` | M1 业务域写侧接线 · GZH（自媒体）首发。 |
| `agent_platform/domains/intel_wiring.py` | M2-P3 情报裁决域：血缘断链 → 情报决策清单（Agent 建议权 + 人工裁决权）。 |
| `agent_platform/domains/invest_wiring.py` | M1 业务域写侧接线 · Invest（股票投研）。 |
| `agent_platform/domains/knowledge_wiring.py` | M2-P2 统一记忆入口：候选知识评审流。 |
| `agent_platform/domains/personal_wiring.py` | M1 个人协同域接线 · schedules 受控写路径（域 #3）。 |
| `agent_platform/domains/pitch_wiring.py` | M1 路演 PPT 域接线（域 #4）· decks 受控状态机。 |
| `agent_platform/domains/portfolio_risk_wiring.py` | 策略结论到组合预算/风险授权请求的只读边界。 |
| `agent_platform/domains/skill_wiring.py` | M2-P3 统一 Skill 目录：跨部门复用动作的注册表。 |
| `agent_platform/engine/__init__.py` | agent_platform.engine：投研团队 DSL 引擎（K-STOCK-004） |
| `agent_platform/engine/anonymous_review.py` | 匿名横评（WO-LEGION-S25 · 进化层 Z3）。 |
| `agent_platform/engine/approvals.py` | M1 统一审批注册表（ApprovalStore）。 |
| `agent_platform/engine/artifacts.py` | 统一产物登记仓（M3-P1）。 |
| `agent_platform/engine/checkpoints.py` | checkpoint + trace 落 SQLite（K-STOCK-004，PoC 本地版） |
| `agent_platform/engine/decision_policy.py` | DecisionPolicy（2026-10-30 WO-D1-EVIDENCE，规则门外置）。 |
| `agent_platform/engine/deepseek.py` | DeepSeek 云端推理客户端（工具级 llm-deepseek · WO-LEGION-LLMDEEPSEEK-20261026）。 |
| `agent_platform/engine/emp_numbering.py` | 军团员工编号契约（v1）——工号与编制号的唯一发号器 |
| `agent_platform/engine/evidence.py` | 统一证据仓（M2-P1）。 |
| `agent_platform/engine/evidence_bundle.py` | EvidenceBundle（2026-10-30 WO-D1-EVIDENCE，执行"无证据不调 D1"）。 |
| `agent_platform/engine/evidence_raw.py` | EvidenceBundle raw-ref 采集（2026-10-31 WO-EVIDRAW，K-ORG-20261031-EVIDRAW）。 |
| `agent_platform/engine/evolve_pipeline.py` | 进化层端到端流水线（WO-LEGION-S27）。 |
| `agent_platform/engine/experience_store.py` | 经验入库（WO-LEGION-S26 · 进化层 Z4）。 |
| `agent_platform/engine/experiment_card.py` | 对照实验卡（WO-LEGION-S24 · 进化层 Z2）。 |
| `agent_platform/engine/idem_hook.py` | 双轨幂等 Hook（M0 P4）。 |
| `agent_platform/engine/idempotency.py` | 统一 IdempotencyRecord 账本（M0 P3）。 |
| `agent_platform/engine/interpreter.py` | 投研团队 DSL 解释器 v0（K-STOCK-004） |
| `agent_platform/engine/l4_artifact.py` | L4 established 的 artifact_ref 必需校验（2026-10-31 WO-L4-M4-HARDEN-20261031）。 |
| `agent_platform/engine/l4_provider.py` | L4 定性来源接口（2026-10-31 WO-D1-L4SOURCE）。 |
| `agent_platform/engine/llm.py` | LLM 绑定层：Ollama 客户端 + MODEL_PROFILES 自动路由 + NovaMax 特种 + 降级三态 |
| `agent_platform/engine/lookahead_guard.py` | 防穿越断言（WO-LEGION-S23 · 进化层 Z5）。 |
| `agent_platform/engine/memory.py` | 四级 namespace 记忆层（K-STOCK-003 §5.2 的本地 PoC 落地版） |
| `agent_platform/engine/openclaw_cli.py` | OpenClaw CLI 桥（军团侧复用，2026-10-26 WO-LEGION-ZHUGE-8796OC）。 |
| `agent_platform/engine/org_audit.py` | 编制完整性审计（WO-LEGION-S34）—— 只读，回答「这个军团到底有多少人、谁在干什么」。 |
| `agent_platform/engine/org_enroll.py` | Agent 编制工具（WO-LEGION-S33）——把「部门要加一个新 Agent」从纸面变成可执行。 |
| `agent_platform/engine/org_migrate.py` | 档案迁入部门档案表（WO-LEGION-S43）——把散在 chief 块 / team_*.json 的档案落到标准位。 |
| `agent_platform/engine/org_profiles.py` | 档案索引（WO-LEGION-S41）——「某席位到底有没有档案、绑的是哪个模型」的唯一口径。 |
| `agent_platform/engine/org_rename.py` | 中文名迁移（WO-LEGION-S43）——「重名 4 对」的消歧通道。 |
| `agent_platform/engine/perf_ledger.py` | 绩效账本（M4-P4）：eval_shadow J2 后验收益沉淀 + intel 阵容校准反馈。 |
| `agent_platform/engine/quant_double_knife.py` | Double-knife signal prototype for A-share daily selection. |
| `agent_platform/engine/quant_double_knife_entry.py` | Minute-level entry confirmation for double-knife daily candidates. |
| `agent_platform/engine/regime_roster.py` | Regime 阵容选择器（M4-P3）：StrategyAgent 的 Regime→profile 落账层。 |
| `agent_platform/engine/router.py` | SmartRouter 智能模型路由器（10-18 老田拍板：自动思考该用什么模型） |
| `agent_platform/engine/shadow_long_cycle.py` | SHADOW 长周期验证与证据聚合（只读入口）。 |
| `agent_platform/engine/shadow_strategies.py` | 军团 shadows 三策略移植（K-ORG-167 第二批）——首批 StrategyAgent 插件。 |
| `agent_platform/engine/shadow_strategy_runner.py` | 独立的 SHADOW 验证入口。 |
| `agent_platform/engine/signature_guard.py` | 进化链签名守卫（WO-LEGION-P0FIX-20261026 · P0-3，渐进式）。 |
| `agent_platform/engine/strategy_config.py` | 策略显式 JSON 配置清单与运行前安全校验。 |
| `agent_platform/engine/strategy_discovery.py` | 显式策略清单驱动的 StrategyAgent 发现与装配。 |
| `agent_platform/engine/strategy_evaluation.py` | 独立多策略结果仲裁与增量评价。 |
| `agent_platform/engine/strategy_promotion.py` | 独立策略生命周期晋级门。 |
| `agent_platform/engine/strategy_registry.py` | strategy 域骨架（第十一域，K-ORG-167）——军团方案仓库路线拍板落 AP。 |
| `agent_platform/engine/strategy_runtime.py` | 策略 Agent 最小运行时隔离门。 |
| `agent_platform/engine/strategy_scheduler.py` | 独立多策略并行调度器。 |
| `agent_platform/engine/tasks.py` | 任务单系统（K-ORG-010 P2-1）：跨部门协作的唯一通道——禁直读记忆，只走任务单 |
| `agent_platform/engine/team_exec.py` | 团队执行账与结果摘要（2026-10-30 WO-INV-TEAM-ENGINE2）。 |
| `agent_platform/engine/team_schema.py` | 菜鸟团队分析结构化输出移植（2026-10-30 WO-INV-CAINIAO-PORT）。 |
| `agent_platform/engine/tools.py` | 数据工具层 v1（K-STOCK-004）：腾讯免费行情接口，零 token 依赖 |
| `agent_platform/eval_shadow.py` | 影子运行评估（蓝图 v2 三判据）——数据攒够 5 交易日后运行 |
| `agent_platform/evals/incident_to_test.py` | 事故→回归草稿：从 dev_events 抽 guard_fail/blocked，生成待评审回归用例草稿。 |
| `agent_platform/evals/judge.py` | LLM-as-judge（带每日预算 + 确定性降级，不伪造分数）。 |
| `agent_platform/evals/run_evals.py` | 赵云评估飞轮：跑行为目录，产出 session/trace/span 三层指标。 |
| `agent_platform/examples/strategy_example_momentum.py` | 只读动量 StrategyAgent 示例。 |
| `agent_platform/gateway/__init__.py` | 军团门面包（WO-LEGION-S21）：对外稳定接口的唯一入口。 |
| `agent_platform/gateway/envelope.py` | 军团对外信封规约（WO-LEGION-S21）。 |
| `agent_platform/gateway/inbox_gateway.py` | 任务投递门面（WO-LEGION-S21）。 |
| `agent_platform/gateway/inbox_offline.py` | WO-FS-157: 离线派单状态契约。禁止接真 inbox、网络、消息或交易。 |
| `agent_platform/gateway/llm_gateway.py` | LLM 稳定门面（WO-LEGION-S21）。 |
| `agent_platform/gateway/osleg_consumer.py` | OSLINK-W4 8795 消费者 worker 骨架（K-QUANT-20261026-OSLEG-CONSUMER）。 |
| `agent_platform/gateway/osleg_http_api.py` | OSLINK-W4 通道② 8795 HTTP 端点层 + 鉴权（离线 / 隔离 store / 不启端口）。 |
| `agent_platform/gateway/serve.py` | 军团门面 HTTP 服务（WO-LEGION-S21）。 |
| `agent_platform/hermes_bridge.py` | D1 ↔ Hermes stock-data 只读 MCP 数据桥（2026-10-30 WO-D1-HERMES-BRIDGE）。 |
| `agent_platform/ide_release_info.py` | IDE release artifact 只读信息：/api/ide/version 的唯一数据源。 |
| `agent_platform/intel_ab_harness.py` | 情报 A/B 隔离运行器（WO-INTEL-ABHARNESS-20261031 / task-20）。 |
| `agent_platform/intel_context.py` | N1 情报上下文：reviewed JEV → news_event（2026-10-31 WO-INTEL-N1AB / task-13）。 |
| `agent_platform/intel_inbox/intel_inbox.py` | 满宠每日情报收件箱（方案B 文件收件箱上行·受理席）。 |
| `agent_platform/intel_inbox/test_intel_inbox.py` | 满宠每日情报收件箱受理席测试（全临时目录，零触碰真实 inbox/ledger）。 |
| `agent_platform/legion_command/__init__.py` | — |
| `agent_platform/legion_command/credential_catalog.py` | 统一凭据目录：JEV / 数据接口 / 飞书 等业务凭据的统一展示（只读，不复制明文）。 |
| `agent_platform/legion_command/evidence.py` | Phase C：evidence artifact（run manifest）。 |
| `agent_platform/legion_command/governance_kpi.py` | 治理 KPI 聚合（只读；供 8796 `GET /api/governance/kpi`）。 |
| `agent_platform/legion_command/hermes_auth.py` | Hermes WebUI 认证客户端（Phase B1）。 |
| `agent_platform/legion_command/hermes_health.py` | Hermes 8787 会话感知健康探针（2026-10-31 · 24h 审计项）。 |
| `agent_platform/legion_command/hermes_orchestrator.py` | Hermes orchestrator：A0/席位先规划，OH 只执行。 |
| `agent_platform/legion_command/hq_acceptance.py` | 验收会证据聚合：测试基线 + 红队最新 + 黄忠最新验收单 + 日更验收汇总（只读，缺件 fail-visible）。 |
| `agent_platform/legion_command/hq_agenda.py` | 会前议程 v1（P1）：收编 8798 议会厅的议题/留言/预约（只读）+ 留言追加。 |
| `agent_platform/legion_command/hq_meeting.py` | 指挥部会议 v2（P0）：五席晨会/作战会/验收会 纪要 + 订单派单。 |
| `agent_platform/legion_command/hq_stats.py` | 会议看板（P2）：闭环率/跑偏检测/回放（只读）。 |
| `agent_platform/legion_command/ide_context.py` | IDE 上下文引擎：repo map + 词法检索 + 预算压缩。 |
| `agent_platform/legion_command/ide_fs.py` | IDE 调试通道只读文件工具：list_dir / read_file / search。 |
| `agent_platform/legion_command/ide_worktree.py` | OH 工单闭环：worktree 自动准备 / diff / 确认提交。 |
| `agent_platform/legion_command/intent.py` | 军团 OS · 席位意图卡契约（v1）——**现役实现（8796 /api/intent 唯一数据源）** |
| `agent_platform/legion_command/keypool.py` | 三席多 Key 池（田丰/赵云/诸葛亮）— 存储/测试/切换/自动切换。 |
| `agent_platform/legion_command/mcp_bridge.py` | Tushare MCP 工具桥（streamable-http JSON-RPC）。 |
| `agent_platform/legion_command/mission_dispatch.py` | Mission Dispatcher（阶段1）：只做 dry-run 派工计划，不建 worktree、不改代码。 |
| `agent_platform/legion_command/mission_evolver.py` | Mission 阶段3：结果采集 → 经验候选 → 策略建议。 |
| `agent_platform/legion_command/mission_planner.py` | Mission Planner（阶段1）：一份文档 → 任务 DAG → plan.json。 |
| `agent_platform/legion_command/mission_worker.py` | 阶段4-1 Worker：自动接单低风险任务，在 worktree 用 LLM 生成文件改动并提交。 |
| `agent_platform/legion_command/model_allocator.py` | 任务感知模型分配：任务/角色 → 本地 / 云端 / GPT-6。 |
| `agent_platform/legion_command/model_prefs.py` | 研发板块模型手动切换偏好：agent → model 别名。 |
| `agent_platform/legion_command/oh_auth.py` | C1：8796 的 OH 写/执行入口准入（门户 session 或能力令牌）。 |
| `agent_platform/legion_command/openhands_engine.py` | 方案 A：OpenHands Agent Server 适配层（原生会话 API）。 |
| `agent_platform/legion_command/planner_rules.py` | Planner rules from signed experience + A/B comparison. |
| `agent_platform/legion_command/redact_outbound.py` | 出网脱敏工具（P0-2）：A0 desc / 诸葛 diff / 测试输出在发往云端模型前分类与脱敏。 |
| `agent_platform/legion_command/safe_http.py` | 安全 urlopen：仅允许 http/https scheme（legion_command 树副本）。 |
| `agent_platform/legion_command/seat_router.py` | seat_router：模型别名 + 席位路由表 + 虚拟 Key（网关统一接入层）。 |
| `agent_platform/legion_command/serve.py` | 军团指挥界面套件 HTTP 服务（WO-LEGION-S22）。 |
| `agent_platform/legion_command/snapshot.py` | 军团指挥界面套件（WO-LEGION-S22）：快照聚合器。 |
| `agent_platform/legion_command/tianfeng_view.py` | 田丰（A0）· 产品/研发席前端视图聚合（只读，供 8796 `/api/tianfeng`）。 |
| `agent_platform/legion_command/zhaoyun_cap2.py` | 赵云能力二期：审批闭环 / 能力自检 / 模型路由 / 任务干预 / 记忆策展 / 使用留证。 |
| `agent_platform/legion_command/zhaoyun_debate.py` | 辩论视图（zhaoyun 接口用）：子进程调 py312 tools/zhuge_debate.py（只读）。 |
| `agent_platform/legion_command/zhaoyun_drill.py` | 赵云 · 能力下钻与调整请求（治理席，只读为主 + 请求型动作）。 |
| `agent_platform/legion_command/zhaoyun_quant.py` | 量化只读视图（zhaoyun 页面/接口用）：调 py312 的 tools/quant_readonly.py（只读）。 |
| `agent_platform/legion_command/zhaoyun_view.py` | 赵云 · 治理席前端视图聚合（只读，供 8796 `/api/zhaoyun`）。 |
| `agent_platform/legion_command/zhaoyun_wargame.py` | 兵棋视图（zhaoyun 接口用）：子进程调 py312 tools/zhuge_wargame.py（只读）。 |
| `agent_platform/legion_ide/__init__.py` | 军团 Agent IDE 独立形态（legion_ide）。 |
| `agent_platform/legion_ide/cap_token.py` | 零界本地能力令牌（Phase A）：无状态 HMAC，scope + task + TTL。 |
| `agent_platform/legion_ide/exec_runner.py` | IDE 受控执行（R2）：命令白名单 + 超时/输出上限 + 环境净化。 |
| `agent_platform/legion_ide/exec_sandbox.py` | LDE exec 进程隔离层（Windows Job Object）· fail-closed。 |
| `agent_platform/legion_ide/patchset_journal.py` | LDE patchset 事务磁盘 journal / 崩溃恢复（P0 事务崩溃一致性，2026-10-31）。 |
| `agent_platform/legion_ide/rw_guard.py` | IDE 可写/可执行 · 作用域 / 工单锁 / 会话 / 审计（R0 骨架，D1/D3/D4=A）。 |
| `agent_platform/legion_ide/serve.py` | 军团 Agent IDE · 独立形态服务（legion_ide）· v2（R0+R1+R2）。 |
| `agent_platform/legion_ide/undo_store.py` | LDE undo 持久化：content-addressed 旧内容 + 记录文件。 |
| `agent_platform/legion_ide/writer.py` | IDE 受控编辑（R1）：读/写 worktree 内文件 + 变更摘要（不碰 git index）。 |
| `agent_platform/legion_mcp/__init__.py` | 赵云 MCP 包：零依赖 stdio JSON-RPC 客户端 + 只读工具注册表。 |
| `agent_platform/legion_mcp/client.py` | MCP stdio 客户端（零依赖）：初始化握手 / tools/list / tools/call / 超时。 |
| `agent_platform/legion_mcp/http_client.py` | MCP Streamable HTTP 客户端（零依赖）：POST JSON-RPC，兼容 JSON 与 SSE 响应。 |
| `agent_platform/legion_mcp/registry.py` | 赵云 MCP 工具注册表：服务器配置加载 / 探测 / 白名单调用（只读）。 |
| `agent_platform/legion_mcp/server.py` | MCP stdio JSON-RPC 服务端通用实现（零依赖，stdlib）。 |
| `agent_platform/memory/__init__.py` | 赵云 MCP 包记忆 v2 测试。 |
| `agent_platform/memory/store.py` | 赵云记忆 v2：四层记忆（working/episodic/semantic/procedural）+ 混合检索 + 检索评测。 |
| `agent_platform/memory/vector.py` | 记忆 v2 向量升级（可选）：fastembed 语义重排 + sqlite-vec 索引，失败自动降级。 |
| `agent_platform/model_apply/__init__.py` | 模型覆盖 L2 生效编排包。 |
| `agent_platform/model_apply/apply.py` | 模型覆盖生效编排（L2 门控）：只生成计划 + 落审批 marker，**不自动改真源/不自动重启**。 |
| `agent_platform/monday_checkpoint.py` | M5 刀 4：周一观察点护航巡检（2026-10-17 规划 81dda30）。 |
| `agent_platform/morning_check.py` | 影子期晨检一键脚本（K-STOCK-016）：每天早上一条命令核对隔夜自动化产出 |
| `agent_platform/notify/__init__.py` | 赵云通知包。 |
| `agent_platform/notify/outbox.py` | 通知 outbox + 飞书审批卡片生成（默认只落 outbox，不假装发送）。 |
| `agent_platform/observability/genai.py` | Agent 可观测：OpenTelemetry GenAI 语义约定适配层（默认关闭）。 |
| `agent_platform/observability/trace.py` | 赵云可观测：最小 trace/span 存储（stdlib sqlite3，字段兼容 OTel GenAI semconv）。 |
| `agent_platform/observe_point_review.py` | 周一观察点复查执行器（K-ORG 预约 2026-10-18 19:1x 条目的机械化执行件）。 |
| `agent_platform/observe_review_task.py` | 观察点复查计划任务 wrapper（ObservePointReviewOnce，OneTime 17:50）。 |
| `agent_platform/onboard_agent.py` | 数字军团入职工具 v0.1（K-ORG-117 一批交付） |
| `agent_platform/paper/__init__.py` | — |
| `agent_platform/paper/exchange_facade.py` | Paper Exchange facade（只读路由，不迁移账）—— WO-EXCHANGE-FACADE-20261031，Phase 1-C。 |
| `agent_platform/paper/exchange_gateway.py` | Paper Exchange Gateway（只读路由接入）—— WO-EXCHANGE-GATEWAY-20261031，Phase 2-C。 |
| `agent_platform/paper/paper_book.py` | 纸面组合账本（paper MVP，2026-10-30 WO-INV-PAPER-MVP）。 |
| `agent_platform/paper/risk_gate.py` | paper MVP 风控门（2026-10-30 WO-INV-PAPER-MVP）。 |
| `agent_platform/paper/tenant_model.py` | 模拟交易所多租户/账户契约纯模型（2026-10-31 WO-PAPER-TENANT-20261031，Phase 0-C）。 |
| `agent_platform/policy/__init__.py` | Agent 运行时策略包（policy）。 |
| `agent_platform/policy/agent_policy.py` | Agent 工具调用运行时策略引擎 v0（P0 治理，2026-11-01 · WO-AGENT-POLICY-V0-20261101）。 |
| `agent_platform/portfolio_composer.py` | 组合计划器（2026-10-30 WO-INV-POOL-COMBO）。 |
| `agent_platform/recommendation_pool.py` | 司马徽（ACAD1）推荐池 M1（2026-10-30 WO-INV-RECOMMEND-POOL）。 |
| `agent_platform/repair/__init__.py` | 关羽修复车道包。 |
| `agent_platform/repair/lane.py` | 关羽修复车道 v1：事故/失败 triage → 批次 → pytest 运行 → 留证。 |
| `agent_platform/replay.py` | 辩论回放 CLI（K-STOCK-007）：lineage 人工抽查工具，三判据 J3 的配套 |
| `agent_platform/research/__init__.py` | 赵云只读研究包。 |
| `agent_platform/research/web.py` | 只读 Web 取证：SSRF 防护 + 域名白名单 + 24h 缓存 + 每日预算 + 来源引用。 |
| `agent_platform/research_plan.py` | D1 投研工作台数据合同（2026-10-30 WO-D1-RESEARCH）。 |
| `agent_platform/router.py` | 军团任务路由器 v0.1（K-ORG-119 批1） |
| `agent_platform/run_invest.py` | 投研部执行入口（K-STOCK-004） |
| `agent_platform/run_shadow.py` | 5 日影子运行账本（K-STOCK-004 蓝图 v2 判据的数据采集端） |
| `agent_platform/safe_urlopen.py` | 安全 urlopen：仅允许 http/https scheme（防 file:// / 自定义 scheme）。 |
| `agent_platform/sandbox/__init__.py` | 赵云沙箱包。 |
| `agent_platform/sandbox/runner.py` | 本地受限沙箱：隔离 cwd / 净环境 / 超时 / 输出限额，执行技能 verify（只读验证）。 |
| `agent_platform/sector_regime.py` | 板块/行业 regime（2026-10-30 WO-METHODS-EXIT）。 |
| `agent_platform/skills/backup-bundles/scripts/verify.py` | verify: backup-bundles（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/case-sign-chain/scripts/verify.py` | verify: case-sign-chain（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/council-hall/scripts/verify.py` | verify: council-hall（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/docs-archive/scripts/verify.py` | verify: docs-archive（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/experience-write/scripts/verify.py` | verify: experience-write（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/governance-ai/scripts/verify.py` | verify: governance-ai（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/harness-runner/scripts/verify.py` | verify: harness-runner（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/health-card/scripts/verify.py` | verify: health-card（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/huangzhong/scripts/verify.py` | verify: 黄忠验收守门技能（只读；缺件 fail-visible）。 |
| `agent_platform/skills/llm-deepseek/scripts/verify.py` | verify: llm-deepseek（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/lookahead-guard/scripts/verify.py` | verify: lookahead-guard（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/mutual-review/scripts/verify.py` | verify: mutual-review（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/task-exit-audit/scripts/verify.py` | verify: task-exit-audit（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/task-launch-probe/scripts/verify.py` | verify: task-launch-probe（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/skills/worktree-hygiene/scripts/verify.py` | verify: worktree-hygiene（只读；由 build_skills.py 生成，勿手改） |
| `agent_platform/smoke_platform_endpoints.py` | ②③端点级真 HTTP 冒烟（临时端口 8092，不触碰 8091 生产进程）。 |
| `agent_platform/strategy_alpha_bridge.py` | ST1 策略旁路 → alpha/D1 上下文桥（2026-10-30 WO-INV-CHAIN）。 |
| `agent_platform/task.py` | 任务单 CLI（K-ORG-011）：open / ls / move |
| `agent_platform/technical_levels.py` | D1 研究化建仓水平（2026-10-31 WO-D1-PLAN-20261031）。 |
| `agent_platform/templates/strategy_agent_template.py` | 第一版可插拔策略 Agent 模板。 |
| `agent_platform/tests/conftest.py` | agent_platform 测试路径守卫（2026-10-30 P78）+ evidence 生产目录隔离（2026-10-31 M1）。 |
| `agent_platform/tests/smoke_test.py` | 冒烟测试（K-STOCK-004）：MockLLM 全链路 + 断言，临时目录不污染正式 data |
| `agent_platform/tests/test_a0_agent_p1.py` | A0 权威 binding 与成对判读审计字段的纯本地回归测试。 |
| `agent_platform/tests/test_a0_chief_evidence_fields.py` | A0 shadow 日报字段透传测试（2026-10-31 WO-D1-B123-20261031 / B3）。 |
| `agent_platform/tests/test_a0_evidence_judge.py` | a0_evidence_judge 单测（K-QUANT-20261026-A0EVJ）。 |
| `agent_platform/tests/test_a_phase_group_board.py` | A 阶段组级看板生成器测试（WO-A-STAGE-BOARD）。 |
| `agent_platform/tests/test_academy_ops.py` | 研究院运营三件套测试（WO-LEGION-ACAD1-P0）。 |
| `agent_platform/tests/test_academy_role_attrs.py` | 研究院角色属性一致性（2026-10-29 A，WO-ACAD-ROLE-ATTR-20261029）。 |
| `agent_platform/tests/test_academy_wiring.py` | M1 Academy 域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_agent_policy.py` | agent_policy 单测（P0 Agent 工具调用运行时策略引擎 v0 · WO-AGENT-POLICY-V0-20261101）。 |
| `agent_platform/tests/test_agent_sandbox.py` | Agent 沙箱/benchmark 测试（WO-AGENT-SANDBOX-20261027）。 |
| `agent_platform/tests/test_anonymous_review.py` | WO-LEGION-S25 Z3 匿名横评测试。 |
| `agent_platform/tests/test_approval_card.py` | S2 审批卡构建测试。 |
| `agent_platform/tests/test_approval_control.py` | approval_control 测试：批准/驳回/幂等/未授权/自审阻断 + M4 多方签字。 |
| `agent_platform/tests/test_approval_control_m4_enforce.py` | M4 强制开关单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。 |
| `agent_platform/tests/test_approval_rollup.py` | tools/approval_rollup.py 测试（2026-10-31 WO-M4-SIGNERMAP-20261031）。 |
| `agent_platform/tests/test_approval_signers_wiring.py` | M4 签字角色映射接线测试（2026-10-31 WO-M4-SIGNERMAP-20261031）。 |
| `agent_platform/tests/test_approval_ws_wiring.py` | D3 接线冒烟测试：ws 卡片 approval:* 分支 + 动作注册表登记。 |
| `agent_platform/tests/test_approvals_registry.py` | M1-阶段二 Approval 注册表只读数据面测试（离线 fixture + 真实仓冒烟）。 |
| `agent_platform/tests/test_approvals_store.py` | ApprovalStore 幂等契约测试（M0 契约补齐：同键异参拒绝）。 |
| `agent_platform/tests/test_arch1_ops.py` | ARCH1 架构/技术债周巡检测试（WO-LEGION-ARCH1-PATROL）。 |
| `agent_platform/tests/test_artifacts.py` | ArtifactStore 契约测试（M3-P1：统一产物登记仓）。 |
| `agent_platform/tests/test_attribution_gap_report.py` | tools/attribution_gap_report.py 测试（WO-DRILL3-ATTRGAP）。 |
| `agent_platform/tests/test_audit2_guards.py` | pending 不推导水平 + levels 缓存 TTL 测试（WO-AUDIT2-FIX-20261031）。 |
| `agent_platform/tests/test_audit_20261028.py` | 2026-10-28 审计修复回归测试（WO-AUDIT-20261028-FIX）。 |
| `agent_platform/tests/test_authority_audit.py` | authority_audit 测试（WO-AUTHORITY-C3）。 |
| `agent_platform/tests/test_auto_hooks_import.py` | 17:10 自动钩子导入修复测试（2026-10-30 WO-INV-AUTO-FIX）。 |
| `agent_platform/tests/test_automation_supervisor.py` | automation_supervisor dry-run 测试（不触网）。 |
| `agent_platform/tests/test_batch_strategy_drill.py` | 批量新增策略 Agent 的 SHADOW 零主链改动演练。 |
| `agent_platform/tests/test_bj_symbol.py` | 北交所符号映射测试（2026-10-30 WO-INV-BJ-POOL）。 |
| `agent_platform/tests/test_business_adapter.py` | M0 P5 业务域投影适配器测试（离线 fixture）。 |
| `agent_platform/tests/test_cap_token.py` | Phase A 能力令牌测试。 |
| `agent_platform/tests/test_capability_self_audit.py` | 能力自检测试（WO-ZHAOYUN-CAP2）。 |
| `agent_platform/tests/test_check_observation_point.py` | check_observation_point 周一观察点一键巡检测试。 |
| `agent_platform/tests/test_check_trigger_chain.py` | check_trigger_chain 周一触发链前置自检测试。 |
| `agent_platform/tests/test_closeout_round.py` | P0-2 日终收口轮测试（WO-LEGION-P0FIX-20261026）。 |
| `agent_platform/tests/test_codex_bridge.py` | 军团 Codex bridge 测试（M1 校验器 + M2 试点注册）。 |
| `agent_platform/tests/test_command_hq_orchestrator.py` | K-ORG-20261101-HQORCH（W-C2）：指挥部编排器 MVP 测试（全 fake，无真实模型/执行）。 |
| `agent_platform/tests/test_command_hq_role_contract.py` | RoleOpinion v1 契约测试（2026-11-01 K-ORG-20261101-HQROLE；专项计划 §3.2）。 |
| `agent_platform/tests/test_command_hq_roles.py` | 五角色适配器测试（2026-11-01 K-ORG-20261101-HQROLE；专项计划 §3.1/§4 W-C1）。 |
| `agent_platform/tests/test_contracts.py` | 统一 contracts 基础契约测试。 |
| `agent_platform/tests/test_credential_trace.py` | adapters/credential_trace 交叉校验器单元测试（K-M1 审计补强）。 |
| `agent_platform/tests/test_credential_trace_audit.py` | _audit_credential_trace 交叉校验测试（tmp 独立仓，不碰真库）。 |
| `agent_platform/tests/test_d1_calibration.py` | D1 校准测试：①扩席门（tools/d1_calibration.py）；②置信度分桶校准 |
| `agent_platform/tests/test_d1_daily.py` | D1 部门日报测试（WO-D1-DAILY-20261101）。 |
| `agent_platform/tests/test_d1_data_fallback.py` | D1 data fallback 测试（WO-AUDIT-FIX-20261031）。 |
| `agent_platform/tests/test_d1_debate_quality.py` | D1 辩论质量评估测试（2026-11-01 WO-D1-DEBATEQ-20261101）。 |
| `agent_platform/tests/test_d1_decision.py` | D1 final_decision 核心测试（WO-D1-DECISION-20261101）。离线、纯文件。 |
| `agent_platform/tests/test_d1_decision_strict.py` | D1 决策 strict JSON 契约测试（2026-10-29 · 鲁肃复盘整改）。 |
| `agent_platform/tests/test_d1_evidence_policy.py` | D1 证据/规则门测试（2026-10-30 WO-D1-EVIDENCE）。 |
| `agent_platform/tests/test_d1_handoff.py` | D1 交棒回执测试（WO-D1-HANDOFF-20261101）。离线文件。 |
| `agent_platform/tests/test_d1_intel_evidence.py` | D1 决策记录接 reviewed 情报证据（INTEL_TO_D1 A/B）单测（WO-INTEL-D1EVID / task-16）。 |
| `agent_platform/tests/test_d1_json_contract.py` | 决策节点 JSON 契约 + LLM 生成元信息留存（10-17 隔离预演驱动的收口）。 |
| `agent_platform/tests/test_d1_l4_provider.py` | L4 provider 契约测试（含 2026-10-31 WO-L4-M4-HARDEN 的 artifact_ref 门）。 |
| `agent_platform/tests/test_d1_meeting.py` | D1 投研会运行时测试（WO-D1-MEETING-20261101）。离线文件。 |
| `agent_platform/tests/test_d1_outcome_daily.py` | D1 outcome 回填 + 校准日常 测试（WO-D1-OUTCOME-DAILY-20261031，离线）。 |
| `agent_platform/tests/test_d1_rehearsal_precheck.py` | D1 rehearsal 17:10 日常预检 离线测试（WO-D1-REHEARSAL-PRECHECK-20261031）。 |
| `agent_platform/tests/test_d1_research_mcp.py` | D1 research MCP 工具测试（WO-D1-HERMES-BRIDGE-20261030）。 |
| `agent_platform/tests/test_data_lineage.py` | 数据血缘 MVP 测试（WO-DATA-LINEAGE-20261027）。 |
| `agent_platform/tests/test_data_pipeline_probe.py` | data_pipeline_probe 测试（WO-SEAT-REAL-B2B）。 |
| `agent_platform/tests/test_debate_roles.py` | 控辩审契约测试（V2 思路 2 并入 / K-ORG-166，2026-10-18 老田拍板）。 |
| `agent_platform/tests/test_decision_outcomes.py` | D1 决策 outcome 台账单测（不联网）。 |
| `agent_platform/tests/test_decision_outcomes_pit.py` | decision_outcomes PIT/raw refs 增量测试（K-ORG-20261101-D1RAWPIT）。 |
| `agent_platform/tests/test_deepseek.py` | DeepSeek 客户端离线测试（WO-LEGION-LLMDEEPSEEK-20261026）。 |
| `agent_platform/tests/test_demo_guard.py` | M5-P2 演示隔离安全阀测试（刀2 验收门）。 |
| `agent_platform/tests/test_dev_gate_warn_report.py` | tools/dev_gate_warn_report.py 测试（WO-DRILL4-GATEWARN · 2026-10-27）。 |
| `agent_platform/tests/test_dev_legion_ai_card.py` | LS-05 AI 席位卡测试。 |
| `agent_platform/tests/test_dev_legion_api.py` | LS-03 只读 API 契约测试（研发军团作战屏 v1）。 |
| `agent_platform/tests/test_dev_legion_metrics.py` | LS-04 聚合层测试：在制/指标/红线；与 API payload 一致性。 |
| `agent_platform/tests/test_dev_legion_quant_card.py` | LS-06 量化席位卡测试。 |
| `agent_platform/tests/test_dev_legion_real.py` | v1.1 真实量化聚合测试（LV-02/03/04）。 |
| `agent_platform/tests/test_dev_legion_risk_card.py` | LS-07 回测/风控卡测试。 |
| `agent_platform/tests/test_dev_legion_ui.py` | LS-08 前端框架测试：三件套/接口/区块/无自绘色值。 |
| `agent_platform/tests/test_dev_legion_v2_ui.py` | 曹操军团指挥台 v2 页面静态契约（三件套/只读/四区/短屏）。 |
| `agent_platform/tests/test_dev_metrics_daily.py` | dev_metrics_daily 测试（WO-P0-DEV8-METRICS）。 |
| `agent_platform/tests/test_dev_orchestrator.py` | dev_orchestrator MVP 测试（WO-HARNESS-O12）。 |
| `agent_platform/tests/test_dev_pitch_artifact_wiring.py` | M3-P2 dev/pitch 产线产物挂接测试（离线 fixture）。 |
| `agent_platform/tests/test_dev_route_audit.py` | tools/dev_route_audit.py 回归测试（工单 WO-DRILL1-ROUTEAUDIT）。 |
| `agent_platform/tests/test_dev_weekly_report.py` | tools/dev_weekly_report.py 测试（WO-LEGION-P0WRAP）。 |
| `agent_platform/tests/test_dev_wiring.py` | M1 软件开发域接线测试（TaskStore done 验收门）。 |
| `agent_platform/tests/test_devtask_approver.py` | 研发域最高权限 charter · devtask 审批署名测试（WO-AUTHORITY-B2）。 |
| `agent_platform/tests/test_devtask_contract.py` | O2 任务契约 2.0 测试（WO-HARNESS-O12）：devtask 可选字段落账与旧调用兼容。 |
| `agent_platform/tests/test_devtask_dev_gate.py` | WO-LEGION-DEVEXEC-20261027 devtask 研发试点门（dev_owner_gate）回归测试。 |
| `agent_platform/tests/test_devtask_owner_domain.py` | WO-TF-03 owner 落账校验回归测试（零生产写入）。 |
| `agent_platform/tests/test_devtask_sandbox.py` | devtask sandbox 开关测试（WO-SANDBOX-WIRE-20261027）。 |
| `agent_platform/tests/test_devtask_wiring.py` | M1 devtask 工单线接线测试（离线 fixture）。 |
| `agent_platform/tests/test_diff_baseline.py` | diff_baseline 账本基线对比测试。 |
| `agent_platform/tests/test_emp_numbering.py` | 员工编号契约（engine/emp_numbering.py）的守卫测试。 |
| `agent_platform/tests/test_eval_shadow.py` | eval_shadow J2 逐行后验修复测试（K-STOCK-397）。 |
| `agent_platform/tests/test_evals.py` | 评估飞轮测试（WO-EVAL-HARNESS）。 |
| `agent_platform/tests/test_event_adapter.py` | — |
| `agent_platform/tests/test_event_replay.py` | M1-③ 事件回放查询层测试（离线 fixture + 真实仓库只读冒烟）。 |
| `agent_platform/tests/test_evidence.py` | Phase C evidence artifact 测试（含 P0 证据链内嵌 prev_hash/record_hash）。 |
| `agent_platform/tests/test_evidence_bundle_raw_refs.py` | EvidenceBundle raw refs 测试（2026-10-31 WO-EVIDRAW）。 |
| `agent_platform/tests/test_evidence_collect.py` | M2-P1 证据仓 + 六域收编测试。 |
| `agent_platform/tests/test_evidence_raw_pit.py` | evidence_raw PIT（数据时点）测试（K-ORG-20261101-D1RAWPIT）。 |
| `agent_platform/tests/test_evolve_pipeline.py` | WO-LEGION-S27 进化层端到端流水线 + 指挥台进化视图测试。 |
| `agent_platform/tests/test_exchange_facade.py` | Paper Exchange facade 只读路由测试（WO-EXCHANGE-FACADE-20261031，Phase 1-C）。 |
| `agent_platform/tests/test_exchange_gateway.py` | Paper Exchange Gateway 只读路由接入测试（WO-EXCHANGE-GATEWAY-20261031，Phase 2-C）。 |
| `agent_platform/tests/test_exec_sandbox_failclosed.py` | K-ORG-20261031-SANDBOXFAILCLOSED：exec_sandbox Job Object 失败 fail-closed 单测。 |
| `agent_platform/tests/test_execution_gate.py` | HQ L0/L1 执行门测试（W-C4 / K-ORG-20261101-HQEXEC）。 |
| `agent_platform/tests/test_experience_store.py` | WO-LEGION-S26 Z4 经验入库测试。 |
| `agent_platform/tests/test_experiment_card.py` | WO-LEGION-S24 Z2 对照实验卡测试。 |
| `agent_platform/tests/test_gate3_verdict.py` | 门 3 独立复核工具（tools/gate3_verdict.py）的判据测试。 |
| `agent_platform/tests/test_gate_content_hash.py` | M3-P3 发布闸门 content_hash 绑定测试（批 A 发 B 拒绝）。 |
| `agent_platform/tests/test_gate_coverage_w5.py` | W5 fast gate 覆盖口径 + 关键链路漂移守卫（WO-LEGION-W5-GATE-20261031，2026-10-31）。 |
| `agent_platform/tests/test_gateway.py` | WO-LEGION-S21 军团门面测试：信封/LLM 门面/投递幂等/HTTP 端点。 |
| `agent_platform/tests/test_genai_otel.py` | D3：OTel GenAI 适配层单测（默认关 no-op + 字段映射 + span 包装）。 |
| `agent_platform/tests/test_gov_ai.py` | GOVAI-1 地基测试（WO-LEGION-GOVAI1-20261026）。 |
| `agent_platform/tests/test_gov_ai_case_bridge.py` | GOVAI-3 判例草稿签名链测试（WO-LEGION-GOVAI3-20261026）。 |
| `agent_platform/tests/test_gov_integ.py` | GOVINTEG（P1 消费接线 + P3 输入契约）测试。 |
| `agent_platform/tests/test_governance_clock.py` | 治理时钟测试（WO-LEGION-CLOCK-20261026）。 |
| `agent_platform/tests/test_governance_kpi.py` | 治理 KPI 聚合单测：board/health/gate/bandit/OSV/deadlines（注入目录，无网络）。 |
| `agent_platform/tests/test_gzh_artifact_wiring.py` | M3-P2 GZH 产线产物挂接测试（离线 fixture）。 |
| `agent_platform/tests/test_gzh_wiring.py` | M1 GZH 域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_h1b_data_adapter.py` | H1b 数据适配层测试（只读 CSV 合成夹具；不依赖 parquet/pandas）。 |
| `agent_platform/tests/test_healthcheck_all.py` | test_healthcheck_all：聚合逻辑测试（不真跑五个工具）。 |
| `agent_platform/tests/test_heartbeat.py` | Paper 引擎心跳适配器测试（Paperclip 心跳对接第一刀，B 路）。 |
| `agent_platform/tests/test_hermes_auth.py` | B1 Hermes 认证客户端测试（本地桩，不打真实 8787）。 |
| `agent_platform/tests/test_hermes_bridge.py` | D1 ↔ Hermes stock-data 桥测试（WO-D1-HERMES-BRIDGE-20261030）。 |
| `agent_platform/tests/test_hermes_health.py` | hermes_health 单测：登录门识别 / 会话优先 / 失败软降级（2026-10-31）。 |
| `agent_platform/tests/test_hermes_orchestrator.py` | Hermes orchestrator：A0 规划 → 是否执行 → OH 只执行。 |
| `agent_platform/tests/test_hermes_ui_probe.py` | HERMES_UI 探活工具测试（WO-HERMES-UI-REAL）。 |
| `agent_platform/tests/test_hq_acceptance.py` | 验收会证据 P1b 测试：gather/HTTP/纪要 acceptance 字段/前端标记 + 日更验收联动。 |
| `agent_platform/tests/test_hq_agenda.py` | 会前议程 P1 测试：收编只读/留言追加/HTTP 路由/前端标记。 |
| `agent_platform/tests/test_hq_consensus.py` | 指挥部 W-C3 测试（K-ORG-20261101-HQCONS）：共识判定/轮次去重/分歧聚类/ |
| `agent_platform/tests/test_hq_meeting.py` | 指挥部会议 v2 P0 测试：schema/校验/列表/派单/HTTP 路由/前端标记。 |
| `agent_platform/tests/test_hq_p2.py` | 会议 P2 测试：看板统计/跑偏/回放、赵云会议绑定覆盖、HTTP、前端标记。 |
| `agent_platform/tests/test_hq_reflux.py` | P1c 回流测试：会议事件/Phoenix trace/agenda 近事件/KINDS。 |
| `agent_platform/tests/test_huangzhong_hr.py` | HUANGZHONG HR 档案一致性测试（WO-HZ-HR-20261030）。 |
| `agent_platform/tests/test_huangzhong_mcp.py` | huangzhong_mcp 单测（只读工具面；不触碰真实 Hermes / Windows 任务）。 |
| `agent_platform/tests/test_ide_bundle_bootstrap.py` | fresh worktree 缺 gitignored IDE bundle 的引导/契约回归（2026-10-31 WO-QUAL-BUNDLE-20261031）。 |
| `agent_platform/tests/test_ide_context.py` | — |
| `agent_platform/tests/test_ide_enrich.py` | Agent IDE 组织架构富化测试（WO-IDE-ENRICH-20261026）。 |
| `agent_platform/tests/test_ide_extension_e2e.py` | P0-3/P0-4 浏览器 e2e（静态桩 + headless Edge，不触生产）。 |
| `agent_platform/tests/test_ide_release_artifact.py` | W1（2026-10-31）：E2E 只测 release artifact + live version/hash smoke（只读）。 |
| `agent_platform/tests/test_ide_release_info.py` | IDE release artifact 版本/哈希契约。 |
| `agent_platform/tests/test_ide_roles.py` | P0-2：LDE 写/执行角色门测试（本地 fallback + 角色拒绝 + 环境覆盖）。 |
| `agent_platform/tests/test_ide_rw.py` | IDE 可写/可执行 R0+R1+R2 测试（D1-D4 全 A）。 |
| `agent_platform/tests/test_ide_worktree.py` | — |
| `agent_platform/tests/test_idem_acceptance.py` | M0 P4 自动化验收：双轨接入 + 死信 + 人工恢复 + 崩溃窗口。 |
| `agent_platform/tests/test_idempotency.py` | 统一 IdempotencyRecord 与只读审计器测试（M0 P3）。 |
| `agent_platform/tests/test_identity_display_names.py` | F2 身份一致性：display_name 唯一、赵云治理/风控区分、视图与 registry 对齐。 |
| `agent_platform/tests/test_inbox_gateway_lifecycle.py` | OSLINK-P4 answered/delivered 生命周期测试（隔离树·待签）。 |
| `agent_platform/tests/test_inbox_offline.py` | WO-FS-157: all effects isolated to system-temp synthetic tickets. |
| `agent_platform/tests/test_intel_ab_harness.py` | 情报 A/B 隔离运行器单测（WO-INTEL-ABHARNESS-20261031 / task-20）。 |
| `agent_platform/tests/test_intel_context.py` | intel_context（reviewed JEV → N1 news_event）单测（WO-INTEL-N1AB / task-13）。 |
| `agent_platform/tests/test_intel_to_n1.py` | INTEL_TO_N1 A/B 接线测试（WO-INTEL-N1AB / task-13）：make_context 层。 |
| `agent_platform/tests/test_intel_wiring.py` | intel_wiring（情报裁决域）测试。 |
| `agent_platform/tests/test_interpreter_run_scope.py` | O6 P2b：engine Interpreter.run 的 run_scope + attempt 记录单测。 |
| `agent_platform/tests/test_invest_chain.py` | 量化交易部联调测试（2026-10-30 WO-INV-CHAIN）。 |
| `agent_platform/tests/test_invest_p0_align.py` | 量化交易部 P0 对齐测试（2026-10-29 WO-INV-P0-ALIGN）。 |
| `agent_platform/tests/test_invest_pool_combo.py` | 每日股票池→组合计划测试（2026-10-30 WO-INV-POOL-COMBO）。 |
| `agent_platform/tests/test_invest_staff_audit.py` | 鲁肃军团人员就位/模型/能力配置审计测试（2026-10-30 WO-INV-STAFF-AUDIT）。 |
| `agent_platform/tests/test_invest_staffing.py` | 鲁肃部门扩编与盯盘/交易分组断言（WO-INV-STAFF-20261030）。 |
| `agent_platform/tests/test_invest_wiring.py` | M1 Invest（股票投研）域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_judge.py` | LLM-as-judge 测试（WO-EVAL-JUDGE）。 |
| `agent_platform/tests/test_keygen_script.py` | gen_principal_key.ps1 回归测试（WO-LEGION-KEYGENFIX-20261026）。 |
| `agent_platform/tests/test_kline_local_fallback.py` | kline_daily 本地 parquet 回退测试（WO-KLINE-LOCAL-20261030）。 |
| `agent_platform/tests/test_knowledge_wiring.py` | M2-P2 统一记忆入口测试。 |
| `agent_platform/tests/test_lcs1_h1b_caliber.py` | H1b 口径修复测试（task-40）：E1 涨停容差（H1 对齐）+ A 股 302 白名单。 |
| `agent_platform/tests/test_lcs1_h1b_exposure_regression.py` | H1b §5.2 暴露回归辅方法 测试（WO-LCS1-H1B-EXPOSURE-20261031，离线合成数据）。 |
| `agent_platform/tests/test_lcs1_h1b_harness.py` | LCS-1 H1b 回测骨架 测试（WO-LCS1-H1B-HARNESS-20261031，离线合成数据）。 |
| `agent_platform/tests/test_lcs1_h1b_neutralize.py` | H1b R2 中性化端点 测试（WO-LCS1-H1B-NEUTRAL-20261031，离线合成数据）。 |
| `agent_platform/tests/test_legion_command.py` | WO-LEGION-S22 军团指挥界面套件测试：快照聚合 + HTTP 端点 + dispatch 走门面。 |
| `agent_platform/tests/test_legion_command_run_scope.py` | O6 P2c：legion_command 请求级 run_scope 单测。 |
| `agent_platform/tests/test_legion_contract.py` | LS-02 数据契约测试（M0 冻结前校验）：只读/字段/真源。 |
| `agent_platform/tests/test_legion_ide.py` | 军团 Agent IDE 独立形态测试（WO-LEGION-IDE-STANDALONE-20261026）。 |
| `agent_platform/tests/test_legion_ide_audit.py` | — |
| `agent_platform/tests/test_legion_ide_http.py` | LDE 施工台 HTTP 端点回归（K-QUANT-20261030-LDETEST）。 |
| `agent_platform/tests/test_legion_ide_session.py` | LDE 会话持久化/owner 绑定回归（K-QUANT-20261030-IDEDEEP）。 |
| `agent_platform/tests/test_legion_ide_undo_store.py` | LDE undo content-addressed 持久化测试（K-QUANT-20261030-IDEDEEP2）。 |
| `agent_platform/tests/test_legion_intent_contract.py` | 意图卡契约（legion_command/intent.py）的仓库内守卫。 |
| `agent_platform/tests/test_legion_litellm_route.py` | AUDIT-01 A-03 回归：legion_command/serve.py LiteLLM 子进程健壮性（全 mock）。 |
| `agent_platform/tests/test_legion_meeting_backfill.py` | W42 晨会纪要第④段回填器 legion_meeting_backfill.py 离线测试。 |
| `agent_platform/tests/test_legion_readiness.py` | legion_readiness 测试（合成最小 org/tasks）。 |
| `agent_platform/tests/test_lineage_freshness.py` | lineage_freshness（P1-A 血缘新鲜度维度）测试。 |
| `agent_platform/tests/test_list_rejects.py` | list_rejects 拒档只读浏览工具测试。 |
| `agent_platform/tests/test_llm_routing.py` | MODEL_PROFILES 自动路由验收（10-18 llm.py 改造；10-27 B05 单轨化） |
| `agent_platform/tests/test_lookahead_guard.py` | WO-LEGION-S23 Z5 防穿越断言测试。 |
| `agent_platform/tests/test_lusu_kanze_naming.py` | 鲁肃 / 阚泽 定名防回归守卫（2026-10-27 判例）。 |
| `agent_platform/tests/test_m3_p4_replay_audit.py` | M3-P4 审计回放扩展测试（tmp 独立仓，全封闭不碰真库）。 |
| `agent_platform/tests/test_m4_signoff_status.py` | M4 签收状态只读看板 离线测试（WO-M4-SIGNOFF-STATUS-20261031）。 |
| `agent_platform/tests/test_make_context_empty.py` | make_context 空K线 fail-visible 测试（2026-10-30 WO-INV-BJ-EMPTY）。 |
| `agent_platform/tests/test_mcp_client.py` | MCP 客户端/服务端/注册表测试（WO-MCP-CLIENT）。 |
| `agent_platform/tests/test_mcp_http_docx.py` | MCP HTTP + DOCX 表格测试（WO-MCP-HTTP-DOCX）。 |
| `agent_platform/tests/test_memory_gap.py` | 记忆 v2 补差测试（WO-MEMORY-V2-GAP-20261027）：namespace/append-only/有效期/episode 溯源。 |
| `agent_platform/tests/test_memory_v2.py` | 记忆 v2 测试（WO-MEMORY-V2）。 |
| `agent_platform/tests/test_memory_vector.py` | 记忆向量升级测试（WO-MEMORY-VECTOR）：用假 embedder，不下载模型。 |
| `agent_platform/tests/test_model_apply.py` | 模型覆盖 L2 编排测试（WO-MODEL-APPLY）。 |
| `agent_platform/tests/test_model_binding_check.py` | 模型档位执行点检查测试（WO-LEGION-DEVEXEC-20261027 · 报告 C4 · task-8）。 |
| `agent_platform/tests/test_model_binding_check_carrier.py` | T5 第1步：carrier-managed（openai/）席 E3 fallback 豁免验收。 |
| `agent_platform/tests/test_model_router.py` | 模型路由测试（WO-ZHAOYUN-CAP2）。 |
| `agent_platform/tests/test_monday_checkpoint_log.py` | M5 刀4 巡检自写日志回归测试（周一观察点证据链）。 |
| `agent_platform/tests/test_monday_checkpoint_ran_today.py` | monday_checkpoint._ran_today 单测（K-ORG 审计 P1-1 修复回归）。 |
| `agent_platform/tests/test_monday_checkpoint_strategy_observe.py` | B 路追加观察项测试：monday_checkpoint 的 strategy 域首跑可见性（2026-10-18）。 |
| `agent_platform/tests/test_monday_checkpoint_timing.py` | 巡检「首跑未完成」等待条件回归测试（10-17 隔离预演驱动）。 |
| `agent_platform/tests/test_monday_drill.py` | monday_drill（周一观察点只读 dry-run 演练器）测试。 |
| `agent_platform/tests/test_monthly_report.py` | monthly_report.py（W37 月报生成器）测试集——三段勾稽 / 缺账本 INCONCLUSIVE / |
| `agent_platform/tests/test_morning_review_backfill.py` | W42 晨会纪要回填器最小测试集（三态判定 / 出处锚存在性 / 缺数据不编造）。 |
| `agent_platform/tests/test_name_uniqueness.py` | 名称唯一性 CI 测试（WO-NAME-UNIQ-20261027）。 |
| `agent_platform/tests/test_network_policy.py` | 沙箱出口白名单策略测试（WO-SANDBOX-QUOTA-20261027）。 |
| `agent_platform/tests/test_observe_point_review.py` | observe_point_review.py 单测（K-ORG 预约 2026-10-18 19:1x 机械化执行件）。 |
| `agent_platform/tests/test_office_lark.py` | Office + 通知 outbox 测试（WO-OFFICE-LARK）。 |
| `agent_platform/tests/test_oh_auth.py` | C1 OH broker 准入测试。 |
| `agent_platform/tests/test_openclaw_cli.py` | OpenClaw CLI 桥单测（2026-10-26 WO-LEGION-ZHUGE-8796OC）：解析/配置/参数，零网络。 |
| `agent_platform/tests/test_org_audit.py` | WO-LEGION-S34 编制完整性审计测试。 |
| `agent_platform/tests/test_org_chart.py` | org_chart 测试（WO-ORG-CHART）。 |
| `agent_platform/tests/test_org_enroll.py` | WO-LEGION-S33 Agent 编制工具测试。 |
| `agent_platform/tests/test_org_landing_review.py` | 11-03 组织落地复核只读聚合器测试（2026-11-01 WO-ORG-LANDING-REVIEW-20261101）。 |
| `agent_platform/tests/test_org_matrix_migration.py` | org 矩阵迁移测试（WO-ORG-MATRIX P2）：双写校验、幂等、冲突拒绝。 |
| `agent_platform/tests/test_org_migrate.py` | WO-LEGION-S43 档案迁移测试。 |
| `agent_platform/tests/test_org_profiles.py` | WO-LEGION-S41 档案索引测试。 |
| `agent_platform/tests/test_org_rename.py` | WO-LEGION-S43 中文名迁移测试。 |
| `agent_platform/tests/test_osleg_consumer.py` | K-QUANT-20261026-OSLEG-CONSUMER：8795 消费者骨架离线测试。 |
| `agent_platform/tests/test_osleg_http_api.py` | OSLINK-W4 通道② 8795 HTTP 端点层 + 鉴权测试（离线 / 隔离 store / 不启 8795）。 |
| `agent_platform/tests/test_osleg_http_probe.py` | osleg_http_probe 纯函数测试（不启端口、不联网、不动真实 store）。 |
| `agent_platform/tests/test_osleg_p2b_gate.py` | K-QUANT-20261026-OSLEG-P2B：真实 store P2b 闸测试。 |
| `agent_platform/tests/test_paper_event_wiring.py` | M4-P2 Paper 事件入账测试。 |
| `agent_platform/tests/test_paper_mvp.py` | paper MVP 测试（2026-10-30 WO-INV-PAPER-MVP）。 |
| `agent_platform/tests/test_paper_tenant_model.py` | 模拟交易所多租户/账户契约纯模型测试（WO-PAPER-TENANT-20261031，Phase 0-C）。 |
| `agent_platform/tests/test_patchset_journal.py` | patchset_journal 单测（P0 事务崩溃一致性，WO-LEGION-PATCHSET-JOURNAL-20261031）。 |
| `agent_platform/tests/test_perf_ledger.py` | M4-P4 绩效账本测试：幂等/冲突拒绝/缺锚点跳过/链挂接/分组聚合/ |
| `agent_platform/tests/test_personal_wiring.py` | M1 个人协同域接线测试（schedules 受控写路径）。 |
| `agent_platform/tests/test_pitch_wiring.py` | M1 路演 PPT 域接线测试（decks 受控状态机）。 |
| `agent_platform/tests/test_plan_dispatch.py` | plan_dispatch 测试。 |
| `agent_platform/tests/test_policy_check.py` | 策略单源（policy.json）决策器测试（WO-POLICY-CODE-20261027）。 |
| `agent_platform/tests/test_pool_auto_reuse.py` | 同日池复用测试（WO-POOL-STABLE-20261030）。 |
| `agent_platform/tests/test_portfolio_risk_wiring.py` | 组合预算与风险授权边界测试。 |
| `agent_platform/tests/test_pre_observation.py` | pre_observation（周一 17:25 前置一键）测试。 |
| `agent_platform/tests/test_profile_seats.py` | 不占编 profile 席位档案验收（WO-HZ-ZY-PROFILE-20261030）。 |
| `agent_platform/tests/test_quality_gate.py` | quality_gate 测试（WO-SEAT-REAL-B2D）。 |
| `agent_platform/tests/test_quant_double_knife.py` | 双刀移植自测（K-STOCK-018 补全）：信号模块八重过滤合成验证 + 入场确认模块 |
| `agent_platform/tests/test_readonly_surface_fixes.py` | 2026-10-17 复盘审查修复回归（只读面写副作用专项）。 |
| `agent_platform/tests/test_recommendation_pool.py` | ACAD1 推荐池测试（2026-10-30 WO-INV-RECOMMEND-POOL）。 |
| `agent_platform/tests/test_redact_outbound.py` | P0-2 出网脱敏工具测试。 |
| `agent_platform/tests/test_regime_roster.py` | M4-P3 Regime 阵容选择器测试。 |
| `agent_platform/tests/test_release_live_smoke.py` | W1（2026-10-31）live-version smoke 骨架：信任链/发布单元只读验收。 |
| `agent_platform/tests/test_repair_lane.py` | 修复车道测试（WO-GUANYU-LANE）。 |
| `agent_platform/tests/test_replay_query.py` | M1 回放查询层测试（离线 fixture + 真实只读冒烟）。 |
| `agent_platform/tests/test_replay_root_ledger_isolation.py` | Offline discovery test for cross-ledger replay(root) path isolation. |
| `agent_platform/tests/test_research_fsm.py` | 研究流水线状态机测试（WO-RESEARCH-FSM-20261027）。 |
| `agent_platform/tests/test_research_plan.py` | D1 投研工作台数据合同测试（WO-D1-RESEARCH）。 |
| `agent_platform/tests/test_research_plan_levels.py` | research_plan levels override 测试（WO-D1-PLAN-20261031）。 |
| `agent_platform/tests/test_research_seat_ops.py` | 研究值守双席测试（WO-LEGION-ACAD1-P2）。 |
| `agent_platform/tests/test_role_group_model.py` | 角色分工/分组适配模型测试（WO-ROLE-GROUP-MODEL）。 |
| `agent_platform/tests/test_roster_d1_contract.py` | 真仓 roster → D1 JSON 契约锚点测试（f59a0738 集成面收口）。 |
| `agent_platform/tests/test_run_shadow_guard.py` | 影子主链防拖死护栏测试（设计稿 §5.2 过渡缓解：单策略产出超时包裹）。 |
| `agent_platform/tests/test_run_shadow_log_redirect.py` | run_shadow 日志重定向护栏测试（K-OPS-20261019-SHADOWFIX）。 |
| `agent_platform/tests/test_run_shadow_trace_link.py` | TRACELINK 回归：run_shadow 必须在成功落账后调用 _record_trace_link（原为死代码）。 |
| `agent_platform/tests/test_run_strategy_shadow.py` | strategy_drill_cli CLI 契约测试（原名 run_strategy_shadow.py，拍板 02 更名去混淆）。 |
| `agent_platform/tests/test_sandbox.py` | 沙箱验证测试（WO-SANDBOX-VERIFY）。 |
| `agent_platform/tests/test_sandbox_quota.py` | 沙箱资源配额测试（WO-SANDBOX-QUOTA-20261027）。 |
| `agent_platform/tests/test_scan_secrets.py` | scan_secrets 只读扫描器测试。 |
| `agent_platform/tests/test_scan_secrets_timeout.py` | scan_secrets git_status_files 超时保护测试（审查 B4）。 |
| `agent_platform/tests/test_seat_batch1.py` | 批次1 测试：A0 取数审计事件 + 全席 prompt 回滚标记（WO-SEAT-REAL-BATCH1）。 |
| `agent_platform/tests/test_seat_eval.py` | 席位评测跑批器测试（WO-EVAL-SEATS-20261027）。 |
| `agent_platform/tests/test_seat_eval_v2.py` | seat_eval schema v2 断言执行器 + 4 席 golden（2026-10-29）。 |
| `agent_platform/tests/test_seat_fallback.py` | 席位级 fallback 链验收（WO-LEGION-DEVEXEC2-20261027）。 |
| `agent_platform/tests/test_seat_heartbeat.py` | seat_heartbeat 测试（WO-SEAT-REAL-B2E）。全部 stub，不真实调用外部模型。 |
| `agent_platform/tests/test_seat_iteration.py` | seat_iteration 测试（11 席迭代配置）。 |
| `agent_platform/tests/test_seat_iteration_alignment.py` | 11 席能力迭代配置口径对齐守卫（task-19 / WO-SEAT-ITER-ALIGN-20261031）。 |
| `agent_platform/tests/test_seat_iteration_receipts.py` | 11 席迭代回执只读跟踪器测试（2026-10-31 WO-SEAT-ITER-RECEIPTS / task-28）。 |
| `agent_platform/tests/test_seat_learning.py` | seat_learning 测试（WO-LEARNING-DEV7）。 |
| `agent_platform/tests/test_seat_realness.py` | 席位真成员化审计器测试（WO-SEAT-REALSTD-20261026）。 |
| `agent_platform/tests/test_service_probe.py` | service_probe 多目标探活测试（WO-SEAT-REAL-B2A）。 |
| `agent_platform/tests/test_shadow_isolation.py` | run 路径隔离根与预演阀契约测试（M5 缺口收口）。 |
| `agent_platform/tests/test_shadow_log_open_retry.py` | 影子运行日志/账本追加打开：有界重试+退避测试（2026-10-19 判例修复）。 |
| `agent_platform/tests/test_shadow_long_cycle.py` | SHADOW 长周期只读聚合入口契约测试。 |
| `agent_platform/tests/test_shadow_propose_timeout.py` | 任务 #112：run_shadow 单策略 propose 超时包裹——SKIPPED_TIMEOUT 落账契约测试。 |
| `agent_platform/tests/test_shadow_schema.py` | M5 刀 1：影子账本 schema 收口测试。 |
| `agent_platform/tests/test_shadow_strategies.py` | 军团 shadows 三策略插件测试（trend/momentum/mean-reversion，K-ORG-167）。 |
| `agent_platform/tests/test_shadow_strategy_runner.py` | 独立 SHADOW 验证入口契约测试。 |
| `agent_platform/tests/test_shuijing_ui.py` | 司马徽研究台 /academy/shuijing 页面静态契约（只读/三件套/短屏）。 |
| `agent_platform/tests/test_sign_tools.py` | T3 签名工具三件套测试（WO-LEGION-SIGNTOOLS-20261026）。 |
| `agent_platform/tests/test_signature_guard.py` | P0-3 签名守卫测试（WO-LEGION-P0FIX-20261026）。 |
| `agent_platform/tests/test_sim_exchange_org.py` | TR1 调入 D1 / 模拟交易所主管的 org 断言（WO-SIM-EXCHANGE-ORG-20261030）。 |
| `agent_platform/tests/test_sim_exchange_registry.py` | 模拟交易所归属注册表测试（WO-SIM-EXCHANGE-20261030）。 |
| `agent_platform/tests/test_sim_market_contract.py` | SIM_MARKET DTO 契约测试。 |
| `agent_platform/tests/test_sim_market_readonly.py` | SIM_MARKET 独立只读策略适配器测试。 |
| `agent_platform/tests/test_skill_registry.py` | 技能注册表测试（WO-SKILLS-REGISTRY）。 |
| `agent_platform/tests/test_skill_wiring.py` | M2-P3 统一 Skill 目录测试。 |
| `agent_platform/tests/test_smart_router.py` | SmartRouter 单测（零网络）：关键词路由/等级兜底/注册绑定优先/云端熔断/自动适配 |
| `agent_platform/tests/test_sop_link_check.py` | tools/sop_link_check.py 测试（WO-DRILL5-SOPLINK）。 |
| `agent_platform/tests/test_start_tuned_ollama.py` | start_tuned_ollama.py 的判定逻辑测试（tuned 闸门不许恒真）。 |
| `agent_platform/tests/test_strategy_config.py` | 策略显式 JSON 配置清单测试。 |
| `agent_platform/tests/test_strategy_discovery.py` | 显式策略清单驱动发现测试。 |
| `agent_platform/tests/test_strategy_evaluation.py` | 独立策略结果仲裁与增量评价测试。 |
| `agent_platform/tests/test_strategy_example_plugin.py` | strategy_example_momentum 示例插件验收测试。 |
| `agent_platform/tests/test_strategy_extension_contract.py` | StrategyAgent 扩展插槽验收契约测试。 |
| `agent_platform/tests/test_strategy_ledger.py` | strategy 域账本与证据源测试（K-ORG-167 wiring，M6 深化刀）。 |
| `agent_platform/tests/test_strategy_manifest.py` | StrategyAgent 最小插件元数据契约测试。 |
| `agent_platform/tests/test_strategy_plugin_v1_acceptance.py` | 第一版可插拔策略架构独立验收测试。 |
| `agent_platform/tests/test_strategy_promotion.py` | 独立策略生命周期晋级门测试。 |
| `agent_platform/tests/test_strategy_registry.py` | strategy 第十一域骨架测试（K-ORG-167）。 |
| `agent_platform/tests/test_strategy_replay.py` | strategy_replay 只读聚合视图单元测试（K-ORG-167 第三批）。 |
| `agent_platform/tests/test_strategy_runtime.py` | 策略 Agent 最小运行时隔离门测试。 |
| `agent_platform/tests/test_strategy_scheduler.py` | 独立多策略并行调度器测试。 |
| `agent_platform/tests/test_t1_path_resolve.py` | T1 尾巴收口测试：模块级路径常量 `_resolve(root)` 化（M3 总验收遗留）。 |
| `agent_platform/tests/test_task_adapter.py` | — |
| `agent_platform/tests/test_task_exit_audit.py` | test_task_exit_audit.py — 告警闭环工具测试（决策书 D1）。 |
| `agent_platform/tests/test_tasks.py` | 任务单系统自测（K-ORG-011 P2-1）：状态机全转移+RBAC越权+强制注记 |
| `agent_platform/tests/test_tasks_schema_profile.py` | M6：tasks.json 接 validate_json（profile=tasks）——降级巡检增强。 |
| `agent_platform/tests/test_team_exec.py` | 团队执行账/结果摘要测试（2026-10-30 WO-INV-TEAM-ENGINE2）。 |
| `agent_platform/tests/test_team_schema.py` | 菜鸟结构化输出移植测试（2026-10-30 WO-INV-CAINIAO-PORT）。 |
| `agent_platform/tests/test_team_topology.py` | 团队拓扑防回退（2026-10-30 WO-INV-TOPOLOGY-GUARD）。 |
| `agent_platform/tests/test_technical_levels.py` | D1 研究化水平测试（WO-D1-PLAN-20261031）。 |
| `agent_platform/tests/test_tianfeng_view_hermes_probe.py` | tianfeng_view 8787 探针统一单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。 |
| `agent_platform/tests/test_tools_exitcode_contract.py` | 只读工具「退出码契约」测试（subprocess 级，端到端锁定 CLI 语义）。 |
| `agent_platform/tests/test_trace_cost.py` | trace_cost 单元测试（WO-TRACE-COST-20261027）。 |
| `agent_platform/tests/test_trace_link.py` | M4-P1 trace_link 映射层测试。 |
| `agent_platform/tests/test_trace_link_shadow_sync.py` | trace_link shadow-only 补链（replay_trace 锚点修复）契约。 |
| `agent_platform/tests/test_trace_otel.py` | OTel→Phoenix 导出单测（fake urlopen，不联网）。 |
| `agent_platform/tests/test_trace_run_id.py` | O6 P2a：trace.run_id 透传/迁移单测。 |
| `agent_platform/tests/test_trace_store.py` | Trace 存储测试（WO-TRACE-OTEL）。 |
| `agent_platform/tests/test_trace_usage.py` | LLM usage/成本留证测试（WO-TRACE-USAGE）。 |
| `agent_platform/tests/test_trading_bot_flow.py` | 盯盘触发→人工确认→TR1 模拟执行 端到端测试（WO-BOT-CONFIRM）。 |
| `agent_platform/tests/test_trading_bot_orchestration.py` | 交易机器人多角色组编排测试（WO-TRADING-BOT-20261030）。 |
| `agent_platform/tests/test_trading_methods.py` | 首批交易手法规则/回放测试（WO-METHODS）。 |
| `agent_platform/tests/test_unified_projection.py` | M1 统一投影聚合器测试（离线 fixture）。 |
| `agent_platform/tests/test_usage_ledger.py` | M2-P4 统一使用账本测试。 |
| `agent_platform/tests/test_v11_m0_preflight.py` | v1.1 M0 批前只读预检器测试（task-43）。 |
| `agent_platform/tests/test_verify_strategy_ledger.py` | verify_strategy_ledger 三方对账工具测试（军团-M2 验收判据 1 闭环）。 |
| `agent_platform/tests/test_war_room_data.py` | 作战台实时聚合测试（WO-WARROOM-LIVE-20261030）。 |
| `agent_platform/tests/test_watch_feed_consumer.py` | WA1 feed consumer 离线测试（WO-WATCH-FEED）。 |
| `agent_platform/tests/test_watch_trigger_adapter.py` | watch trigger adapter 离线测试（WO-WATCH-TRIGGER）。 |
| `agent_platform/tests/test_web_research.py` | 只读 web 取证测试（WO-WEB-RESEARCH）。 |
| `agent_platform/tests/test_weekly_report.py` | weekly_report.py（W36 周报生成器）最小测试集——W37 工位测试回填。 |
| `agent_platform/tests/test_weekly_report_html.py` | tools/weekly_report_html.py 测试（WO-DRILL2-WEEKLYHTML）。 |
| `agent_platform/tests/test_write_auth.py` | F1 写接口防护：同源校验 + 浏览器写 token（CSRF 回归）。 |
| `agent_platform/tests/test_zhaoyun_cap2.py` | 赵云能力二期测试：审批闭环/干预建议/记忆策展/路由/自检/留证（WO-ZHAOYUN-CAP2）。 |
| `agent_platform/tests/test_zhaoyun_debate.py` | 辩论接线单测。 |
| `agent_platform/tests/test_zhaoyun_drill.py` | 赵云能力下钻模块测试（WO-ZHAOYUN-DRILL）。 |
| `agent_platform/tests/test_zhaoyun_quant.py` | 量化只读 接口/页面 单测（runner 注入，不联网）。 |
| `agent_platform/tests/test_zhaoyun_view.py` | 赵云 · 治理席前端页测试（WO-LEGION-ZHAOYUNPAGE-20261026 / ENH-20261026）。 |
| `agent_platform/tests/test_zhaoyun_view_p2.py` | P2：视图 registry 化配置 + 环境覆盖 + 结构化失败日志。 |
| `agent_platform/tests/test_zhaoyun_wargame.py` | 兵棋接线单测：视图函数 + 页面/路由标记。 |
| `agent_platform/tools/academy_ops.py` | 研究院研究运营三件套（ACAD1 P0）：立项简报 / 评审清单 / 研究院周报 + 事件留痕。 |
| `agent_platform/tools/agent_sandbox.py` | Agent 沙箱 + 内部 benchmark（P0⑧ · WO-AGENT-SANDBOX-20261027）。 |
| `agent_platform/tools/arch1_ops.py` | ARCH1 架构/技术债周巡检（WO-LEGION-ARCH1-PATROL）。 |
| `agent_platform/tools/build_skills.py` | 生成赵云 Agent Skills（14 能力卡 → SKILL.md + scripts/verify.py）。 |
| `agent_platform/tools/capability_self_audit.py` | 能力自检（capability-self-audit）：核对 CAPABILITIES.json 与真实工具/新鲜度，漂移即报警。 |
| `agent_platform/tools/check_observation_point.py` | check_observation_point：周一观察点一键巡检（只读，fail-visible）。 |
| `agent_platform/tools/check_report_isolation.py` | check_report_isolation.py —— 报告防假绿三查（W38，只读，三态化 + 阳性对照）。 |
| `agent_platform/tools/check_trigger_chain.py` | check_trigger_chain：周一外部触发链前置自检（只读，fail-visible）。 |
| `agent_platform/tools/closeout_round.py` | 日终收口轮（WO-LEGION-P0FIX-20261026 首版 · WO-LEGION-CLOCK-20261026 加固）。 |
| `agent_platform/tools/council_review_client.py` | council_review_client.py — 议会厅席位复核客户端（甲案落地，2026-10-23）。 |
| `agent_platform/tools/d1_research_mcp.py` | D1 research MCP server (stdio) — Hermes 可调用的只读投研工具。 |
| `agent_platform/tools/data_lineage.py` | 数据血缘 MVP（P0⑦ · WO-DATA-LINEAGE-20261027）。 |
| `agent_platform/tools/data_pipeline_probe.py` | 数据管道只读自检工具（B2-b 情报/采集 7 席）。 |
| `agent_platform/tools/diff_baseline.py` | diff_baseline：账本基线对比（观察点 FAIL 后取证第一步，只读，fail-visible）。 |
| `agent_platform/tools/dw_catalog_mcp.py` | 数据目录/资质只读 MCP server（stdio）——MCP-B dw-catalog，服务 DATA4 杨仪。 |
| `agent_platform/tools/dw_etl_dryrun_mcp.py` | 数据仓 ETL dry-run MCP（只生成计划，不执行）——执行批准项 4 的「先 dry-run」阶段。 |
| `agent_platform/tools/dw_governance_mcp.py` | 数据仓治理 MCP server（stdio）——Hermes 只读取数面（WP-I3）。 |
| `agent_platform/tools/dw_quality_mcp.py` | 数据仓质量只读 MCP server（stdio）——MCP-A dw-quality，服务 DATA3 陈群。 |
| `agent_platform/tools/factor_tools_mcp.py` | 因子研究只读 MCP（factor-tools）——FACTOR1 蒋琬席位的只读取数面。 |
| `agent_platform/tools/gov_ai.py` | GOVAI-1 地基：DeepSeek 进治理动作（异常归纳 / 治理建议 / 判例草稿）。 |
| `agent_platform/tools/healthcheck_all.py` | healthcheck_all：一键聚合体检（周一现场一条命令跑全景，纯聚合零重复实现）。 |
| `agent_platform/tools/hermes_ui_probe.py` | HERMES_UI（糜竺）· 门面探活工具（真运行体成员的最小工具集）。 |
| `agent_platform/tools/huangzhong_mcp.py` | 黄忠验收 MCP server (stdio) — Hermes 可调用的**只读**验收/守门工具。 |
| `agent_platform/tools/kb_corpus_mcp.py` | 知识库语料只读 MCP server（stdio JSON-RPC）——服务 DATA5 谯周（MCP-D kb-corpus）。 |
| `agent_platform/tools/legion_meeting_backfill.py` | W42 晨会纪要第④段（决议与教训）回填器 —— 拍板执行单 #10 方案B：独立脚本，错峰 17:55（A0A 17:50 复盘链尾之后），本脚本自身不注册任何计划任务（注册须另行签准）。 |
| `agent_platform/tools/list_rejects.py` | list_rejects：codex_bridge 拒档只读浏览工具（list_rejects）。 |
| `agent_platform/tools/mcp_probe.py` | MCP 探测/调用 CLI：python agent_platform/tools/mcp_probe.py --json\|--selftest\|--call ... |
| `agent_platform/tools/migrate_org_matrix.py` | org 矩阵迁移（P2）：一席一档 + matrix_assignments，替代"项目部门各存一份完整 profile"。 |
| `agent_platform/tools/model_binding_check.py` | 模型档位执行点检查（model-binding-check）：org 席位绑定 vs model_tiers 档位策略。 |
| `agent_platform/tools/model_router.py` | 模型路由（model-routing）：按能力/席位给出有序模型链，并在 429/不可用时降级。 |
| `agent_platform/tools/monday_drill.py` | monday_drill：周一观察点现场 SOP 的只读 dry-run 演练器（不执行任何命令）。 |
| `agent_platform/tools/monthly_report.py` | monthly_report.py —— 军团月报生成器（W37，账本投影器，只读）。 |
| `agent_platform/tools/morning_review_backfill.py` | morning_review_backfill.py —— 晨会纪要第④段回填器（W42，账本投影器，零自动定稿）。 |
| `agent_platform/tools/network_policy_check.py` | 沙箱出口白名单策略校验/决策器（P0⑧ 增强 · WO-SANDBOX-QUOTA-20261027）。 |
| `agent_platform/tools/office_doc.py` | Office 文档读写（零依赖优先）： |
| `agent_platform/tools/osleg_http_probe.py` | OSLINK-W4 8795 军团网关隔离验收探针（K-QUANT-20261026-OSLEG-8795PREP）。 |
| `agent_platform/tools/policy_check.py` | 策略单源决策器（P0③ · WO-POLICY-CODE-20261027）。 |
| `agent_platform/tools/pre_observation.py` | pre_observation：周一 17:25 巡检前的「收编 → 判读 → 汇总」一键入口（T 工位）。 |
| `agent_platform/tools/quality_gate.py` | 质量门自检工具（B2-d 研发/质量席）。 |
| `agent_platform/tools/research_fsm.py` | 研究流水线显式状态机（P0⑥ · WO-RESEARCH-FSM-20261027）。 |
| `agent_platform/tools/research_seat_ops.py` | 研究院研究值守双席（STRAT1 / BACK1）—— 策略假设值守 + 冻结/回测值守。 |
| `agent_platform/tools/s0_cwd_variant_probe.py` | s0_cwd_variant_probe.py — 0x80070003 根因对照变体探针组。 |
| `agent_platform/tools/scan_secrets.py` | 防泄漏只读凭据扫描器（scan_secrets）。 |
| `agent_platform/tools/seat_eval.py` | 席位评测跑批器（P0① · WO-EVAL-SEATS-20261027）。 |
| `agent_platform/tools/seat_heartbeat.py` | 席位心跳（B2-e）：对指定 LLM 席每日做一次**真实最小模型调用**，补齐触发/审计维。 |
| `agent_platform/tools/seat_realness.py` | 席位「真成员化」审计器（五维：授权/工具/触发/审计/回滚）。 |
| `agent_platform/tools/service_probe.py` | 运行体/通信线 · 多目标探活工具（B2-a 真成员化）。 |
| `agent_platform/tools/skill_registry.py` | 技能注册表：加载/校验/执行 verify（只读）+ 运行态结果持久化。 |
| `agent_platform/tools/start_tuned_ollama.py` | Ollama 守护启动器（tuned 配置）—— 2026-10-18 建立 |
| `agent_platform/tools/strategy_drill_cli.py` | 开发者可执行的独立 SHADOW 策略验证 CLI。 |
| `agent_platform/tools/task_exit_audit.py` | task_exit_audit.py — 计划任务退出码告警闭环（决策书 D1，P0 新能力）。 |
| `agent_platform/tools/task_launch_probe.py` | task_launch_probe.py — S-0①-b 能力正测试探针（2026-10-23）。 |
| `agent_platform/tools/trace_cost.py` | Agent 运行 trace + 成本观测（P0② · WO-TRACE-COST-20261027）。 |
| `agent_platform/tools/trigger_precheck_wrapper.py` | trigger_precheck_wrapper：周一触发链 T-30min 预检计划任务动作体（AgentTriggerChainPrecheck）。 |
| `agent_platform/tools/verify_strategy_ledger.py` | verify_strategy_ledger：strategy_runs × 影子账本 × 证据收编 三方对账（只读）。 |
| `agent_platform/tools/weekly_report.py` | weekly_report.py —— 军团周报生成器（W36，账本投影器，只读）。 |
| `agent_platform/trading_bot_flow.py` | 交易机器人：盯盘触发 → 人工确认 → TR1 模拟执行（2026-10-30 WO-BOT-CONFIRM）。 |
| `agent_platform/trading_methods.py` | 首批交易手法规则引擎 + paper 历史回放（2026-10-30 WO-METHODS-20261030）。 |
| `agent_platform/watch_feed_consumer.py` | WA1 feed consumer：signal_pool + chanlun_pool → watch trigger adapter（2026-10-30）。 |
| `agent_platform/watch_trigger_adapter.py` | watch trigger adapter（2026-10-30 WO-WATCH-TRIGGER-20261030）。 |
| `agent_platform/watchlist.py` | 每日股票池解析（2026-10-30 WO-INV-POOL-COMBO）。 |
| `portal/_smoke_algo_market.py` | K-STOCK-087 冒烟：/algo 页渲染 + 卡密激活闭环 + 主页统计口径。 |
| `portal/agent_auto_commit.py` | agent_auto_commit.py — 各 Agent 代码定时强制入库（portal 统揽板块运维） |
| `portal/api_analysis.py` | 个股分析门户入口（/api/analysis/*）——独立文件，portal_api.py 只加早分派。 |
| `portal/api_approvals.py` | M1-阶段二 Approval 注册表只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_backtest.py` | P4 批 6：回测域 Mixin（backtest 页族/pulse/custom 批族）。 |
| `portal/api_backtest_bt2.py` | api_backtest 拆分 B1（2026-10-30 P68）：bt2 参数化回测 / 我的策略 / minute 产物读取。 |
| `portal/api_backtest_jobs.py` | api_backtest 拆分 B6（2026-10-30 P71）：回测作业/状态辅助函数（custom/cbt/pulse/ |
| `portal/api_backtest_json_api.py` | api_backtest 拆分 B5（2026-10-30 P71）：JSON API（sa_result/sa_start/algo_activate）。 |
| `portal/api_backtest_minute.py` | api_backtest 拆分 B1（2026-10-30 P68）：minute 框架（注册表/校验/重放/产物）。 |
| `portal/api_backtest_pages_a1.py` | api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_a1.py。 |
| `portal/api_backtest_pages_a2.py` | api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_a2.py。 |
| `portal/api_backtest_pages_b1.py` | api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_b1.py。 |
| `portal/api_backtest_pages_b2.py` | api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_b2.py。 |
| `portal/api_backtest_pages_html.py` | api_backtest 拆分 B2（2026-10-30 P69）：回测页 HTML builder（fut / sellpoint）。 |
| `portal/api_backtest_pages_stock.py` | api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_stock.py。 |
| `portal/api_bridge.py` | P4 批 2：桥接域 Mixin（bridge 决策/预览/动作族）。 |
| `portal/api_brief.py` | 复盘简报只读投影（驾驶舱·复盘舱数据面二期 R5）。 |
| `portal/api_capability.py` | M3-P5 能力看板只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_chanlun_pages.py` | P4 批 5：缠论页面 Mixin（chanlun 页/标注/PNG/池族）。 |
| `portal/api_chanlun_sl_tp.py` | K78/K78b 缠论止损止盈预测与自动叠加线（Handler Mixin 拆出）。 |
| `portal/api_commercial.py` | P2.1 门户只读入口：/api/commercial/*（套餐/租户/用量/审计/健康）。 |
| `portal/api_common.py` | P4 批 0：portal 共享底座（helper+方案A+状态锁/常量+导航族+tp共享+_sched判色）。 |
| `portal/api_common_alert.py` | api_common 四组拆分：alert 组（2026-10-31 WO-COMMON-SPLIT-20261031）。 |
| `portal/api_common_assets.py` | api_common 四组拆分：assets 组（2026-10-31 WO-COMMON-SPLIT-20261031）。 |
| `portal/api_common_sched.py` | api_common 四组拆分：sched 组（2026-10-31 WO-COMMON-SPLIT-20261031）。 |
| `portal/api_common_stock.py` | api_common 四组拆分：stock 组（2026-10-31 WO-COMMON-SPLIT-20261031）。 |
| `portal/api_customer.py` | P2.4 客户门户 API：/api/customer/*（登录/分析/报告下载/历史/配额）。 |
| `portal/api_d1_decision.py` | D1 最终裁决 POST API（WO-D1-DECISION-20261101，2026-11-01）。 |
| `portal/api_d1_handoff.py` | D1 交棒回执 API（WO-D1-HANDOFF-20261101）。 |
| `portal/api_d1_meeting.py` | D1 投研会 API（WO-D1-MEETING-20261101，2026-11-01）。 |
| `portal/api_daily_data.py` | 数据日报聚合出口 mixin（K-ORG-183 M1，2026-10-18）。 |
| `portal/api_data.py` | P4 批 1：数据域 Mixin（data_health/warehouse/sync 族 + Handler 方法壳）。 |
| `portal/api_dataops.py` | 数据运营指挥台 /api/dataops/overview —— 采集动线 + 数据池 + 管道调度聚合视图。 |
| `portal/api_dev_legion.py` | 研发军团作战屏 v1 · 只读 API（LS-03，2026-10-28）。 |
| `portal/api_dw_registry.py` | 数据仓统一取数 HTTP 入口（WP-I3，独立 handler 文件）。 |
| `portal/api_events.py` | R6 事件总线数据面（8091 侧 ingest/query，Gate 0 前置离线开发 M1 骨干）。 |
| `portal/api_evolution.py` | 进化账本只读投影（驾驶舱·复盘舱数据面二期）。 |
| `portal/api_execution.py` | R1 执行健康总线（联动层一期 T1，K-ORG-112 三层只读投影）。 |
| `portal/api_flow.py` | 动作流中心 API — flow.html 后端（P4 批7 mixin） |
| `portal/api_gate.py` | 周一观察点门判只读投影（驾驶舱·复盘舱数据面二期）。 |
| `portal/api_guojia.py` | 郭嘉·预判对账司出口 mixin（K-ORG-191/193，2026-10-18）。 |
| `portal/api_health_portal.py` | 门户健康/观测族 Mixin 与 helper（A5 B2，2026-10-31 关羽）。 |
| `portal/api_huangzhong.py` | 黄忠验收事件只读投影（WO-HZ-HERMES-PHASE2-20261031）。 |
| `portal/api_intel_bus.py` | 情报总线唯一出口 mixin（六域→总线→双路径，2026-10-19 联调首包）。 |
| `portal/api_intel_evidence.py` | news.db 的新闻—标的显式代码证据只读投影。 |
| `portal/api_intel_graph.py` | K-MDC-E2: /api/intel-graph/dossier — one-stock candidate-edges endpoint. |
| `portal/api_jev.py` | JEV 判定只读查询接口 —— `/api/jev/query`（2026-10-26 JEVAPI）。 |
| `portal/api_jev_review.py` | JEV 低置信复核工作台 API（2026-10-30 K-JEV-20261030-QUEUEUI）。 |
| `portal/api_kb_corpus.py` | 语料知识库只读接口（YouTube 量化语料，portal 独立 handler 文件）。 |
| `portal/api_kb_qa.py` | 知识库问答 HTTP handler（预接线，未注册到运行中的 8091）。 |
| `portal/api_kb_read.py` | 知识库只读 HTTP handler（预置，未接线）。 |
| `portal/api_kg.py` | P4 批3：知识图谱域 Mixin（kg 页族 17 方法 + 卡片/标注 JS 与小函数）。 |
| `portal/api_legion_ops.py` | 军团作战大屏数据源（K-ORG-121 L3）：GET /api/legion/ops |
| `portal/api_legion_ops_v2.py` | 军团作战大屏数据源 v2（K-ORG-121 L3+ · 下钻/派工/复盘三端点） |
| `portal/api_lineage.py` | 管道血缘只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_manager_portal.py` | 门户管理/雇佣族 Mixin（A5 B3，2026-10-31 关羽）。 |
| `portal/api_misc.py` | P4 批7-MISC：杂域合并 Mixin（qpzm/org/aim/vd/subscribe/feishu/intel/audit/devops 页族 |
| `portal/api_misc_home.py` | K-STOCK-167 驾驶舱数据面（自 home_server.py/8094 移植，方案 A 拍板）。 |
| `portal/api_model_admin.py` | 模型管理控制台 API（10-18 · 纯标准库 · 127.0.0.1:8098） |
| `portal/api_news_intel.py` | 新闻情报台聚合出口 mixin（K-ORG-208 判读输出台视图，2026-10-19）。 |
| `portal/api_notify.py` | 通知指挥四态队列只读聚合出口 mixin（K-ORG-201 W36，2026-10-19）。 |
| `portal/api_os_goal.py` | OS 目标/事项/成果接口（G0 骨架） |
| `portal/api_pages_portal.py` | 门户页面渲染族 Mixin（A5 B1/B1b，2026-10-31 关羽）。 |
| `portal/api_perf.py` | M4-P5 投研绩效看板只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_portfolio.py` | P4 批7-PF：持仓/投委会域 Mixin（debate/council 页族 + portfolio 函数族）。 |
| `portal/api_portfolio_csv.py` | P4 批7-PF 函数族拆出：portfolio 摘要/CSV/平仓原因分类/配置生效。 |
| `portal/api_replay.py` | M1-③ 事件回放查询只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_screen.py` | P4 批7-SCR：数据/筛选域 Mixin（screen 页族 + build_screen/kline/chips 函数族）。 |
| `portal/api_screen_kline.py` | 选股屏 K 线数据族（A5 拆分首批，2026-10-31 关羽 P129）。 |
| `portal/api_tickets.py` | 工单只读投影（驾驶舱一期·复盘舱数据面）。 |
| `portal/api_unified.py` | M1 统一投影只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_xq_intel.py` | 雪球情报出口 mixin（两线预案 M-RT3 消费端门户挂载，2026-10-18）。 |
| `portal/api_zhice_bff.py` | 零界只读 BFF：智策健康 + 菜鸟只读统计 + 契约入口。 |
| `portal/api_zhuge_adoption.py` | api_zhuge_adoption.py — 诸葛亮建议采纳率只追加路由（WO-OS-32 / C1，2026-10-23； |
| `portal/api_zhuge_brief.py` | WO-OS-04 诸葛亮建议引擎（规则版 v1）；WO-OS-20 扩第七源 pushflow。 |
| `portal/api_zhuge_review.py` | 诸葛亮 IDE 复核 HTTP 适配器（P0 + 运营化 2026-10-31，WO-LEGION-REVIEWOPS-20261031）。 |
| `portal/api_zhuge_route.py` | WO-OS-09 诸葛亮建议引擎只读路由（GET /api/zhuge/brief，2026-10-21）。 |
| `portal/approval_card.py` | K-ORG-201 W37：管理公告审批卡（老田飞书卡片确认 → 审批留痕）。 |
| `portal/approval_control.py` | S2 审批卡回调处理（D2-D3，2026-10-30 WO-SUPERVISOR）。 |
| `portal/auth_gateway.py` | Phase A auth-gateway（2026-10-30，单独模块，与只读 BFF 分离）。 |
| `portal/auth_oidc.py` | B1 OIDC 客户端（Pocket ID，默认关；不替换密码登录入口）。 |
| `portal/auth_oidc_runtime.py` | B2 OIDC runtime 配置包装层。 |
| `portal/authorization.py` | 授权名单主表读取/查询（P0-2，2026-10-26）。 |
| `portal/block_triage.py` | 通知队列 blocked 存量分诊只读分析器（FB-G 工位，2026-10-20）。 |
| `portal/build_authorization.py` | 授权名单主表构建（P0-2，2026-10-26）。 |
| `portal/build_daily_data_public.py` | 数据日报公网快照构建器（K-ORG-186 M2，2026-10-18）。 |
| `portal/build_guojia_public.py` | 郭嘉对账公网快照构建器（K-ORG-191，2026-10-18，同 build_daily_data_public 判例）。 |
| `portal/build_pick_details.py` | 对今日选股清单逐只抓取全维度事实包 → portal/data/pick_details/<code>.json |
| `portal/build_pick_verdicts.py` | 选股信号后验计算器：历史池子里的票，信号日后 1/3/5 交易日的真实反应。 |
| `portal/build_todays_picks.py` | 聚合全部选股策略今日强制扫描结果 → portal/data/todays_picks.json |
| `portal/cainiao_auth.py` | 菜鸟(yudao) OAuth2 Bearer token 校验模块 —— X1 登录整合预置（2026-10-08）。 |
| `portal/card_actions.py` | K-PUSH-004：交互动作统一注册表（按钮归档）。 |
| `portal/card_audit.py` | H-B · 卡片模板覆盖审计（P3-lite 波次 2026-10-20，纯离线只读）。 |
| `portal/card_sign.py` | 卡片回调安全：HMAC 签名 + request_id 幂等（W40 收口，2026-10-26）。 |
| `portal/card_templates.py` | FB-L · 卡片差异化模板注册表（8097 发送侧，P1 波次 2026-10-20）。 |
| `portal/comm_dispatch.py` | 阚泽部 · 派单通道（K5.1，WO-FS-148-7）。 |
| `portal/comm_flags.py` | 阚泽部 · 开关读取统一入口（WO-FS-152）。 |
| `portal/comm_intake.py` | 阚泽部 · 智能接话层（K2.1 + K3.1，WO-FS-148-4/5）。 |
| `portal/comm_interest.py` | 阚泽部 · customer_interest 聚合与商机信号（K4.1/K4.2，WO-FS-148-6）。 |
| `portal/comm_memory.py` | 阚泽部 · 会话记忆（K2.2，WO-FS-148-4）。 |
| `portal/comm_router.py` | 阚泽部 · 通信入口智能路由核心（K1.1/K1.2/K1.3，WO-FS-148-2/3/4）。 |
| `portal/comm_summarize.py` | 阚泽部 · 出向智能摘要（K5.2，WO-FS-148-8）。 |
| `portal/comm_zhuge.py` | 阚泽部 · 诸葛亮咨询位（K6，WO-FS-148-10）。 |
| `portal/conftest.py` | pytest 收集排除：脚本式自检文件（K-STOCK-211 任务 4，2026-10-17）。 |
| `portal/contacts_store.py` | contacts_store.py — 商业关系库（客户/投资人/VP/文档档案）。 |
| `portal/council_ops.py` | council_ops.py — 议会厅交互桥（v3，2026-10-23）。 |
| `portal/d1_data.py` | D1 投研工作台数据口（2026-10-30 WO-D1-RESEARCH）。 |
| `portal/dead_letter.py` | K-ORG-201 W38：送达死信账（dead_letters.jsonl）。 |
| `portal/dev_legion_aggregate.py` | 研发军团作战屏 · 聚合层（LS-04，2026-10-28）。 |
| `portal/dev_legion_ai_card.py` | 研发军团作战屏 · AI 席位卡数据（LS-05，2026-10-28）。 |
| `portal/dev_legion_quant_card.py` | 研发军团作战屏 · 量化席位卡数据（LS-06，2026-10-28）。 |
| `portal/dev_legion_quant_real.py` | 作战屏 v1.1 · 策略运行只读聚合（LV-02，2026-10-29）。 |
| `portal/dev_legion_risk_card.py` | 研发军团作战屏 · 回测/风控卡数据（LS-07，2026-10-28）。 |
| `portal/dev_legion_risk_real.py` | 作战屏 v1.1 · 影子/回测只读聚合（LV-03，2026-10-29）。 |
| `portal/feed_extract.py` | 投喂正文抽取（P2 投喂补强，2026-10-26）。 |
| `portal/feishu_acceptance.py` | 邓芝台 · W4 验收脚本（2026-10-24）。 |
| `portal/feishu_app_pool.py` | 角色 → 飞书应用凭据池（W2 前置，2026-10-24）。 |
| `portal/feishu_bot_base.py` | 零界机器人公共壳 v1（邓芝台 W1，2026-10-24）。 |
| `portal/feishu_bot_ws.py` | 飞书下钻机器人（L1）· WebSocket 长连接，无需公网 IP。 |
| `portal/feishu_card_v2.py` | 飞书卡片 2.0 通用多层折叠卡 builder（下钻形态 L0）。 |
| `portal/feishu_console.py` | 邓芝台 · 飞书中台管理台（W1 离线版，2026-10-24）。 |
| `portal/feishu_control.py` | 飞书控制中枢（K-STOCK-135）：卡片按钮 → 后端四域真实落盘。 |
| `portal/feishu_dsh_bot.py` | 零界·执行 bot（zb.owner.exec）—— 我的远程入口（W2 目标文件，2026-10-24）。 |
| `portal/feishu_dsh_relay.py` | 飞书私聊 → DSH headless → 回复（远程桥，2026-10-24 增强版）。 |
| `portal/feishu_notify_bot.py` | 零界·通知 bot 常驻壳入口（zb.ops.notify，2026-10-25 拉起）。 |
| `portal/feishu_push.py` | 飞书推送统一封装 v1（2026-10-25）。 |
| `portal/feishu_registry.py` | 飞书机器人注册表与路由表（邓芝台 · W1 底座，2026-10-24）。 |
| `portal/feishu_rollback_drill.py` | W3 切换回滚演练（dry-run，2026-10-24）。 |
| `portal/feishu_sender_switch.py` | W3 sender 统一切换器（dry-run 默认可跑，2026-10-24）。 |
| `portal/feishu_sop_runner.py` | 新增机器人 7 步 SOP 执行器（W4 演练工具，2026-10-24）。 |
| `portal/feishu_target_audit.py` | 飞书收件人可达性审计（W3 清债工具，2026-10-24）。 |
| `portal/feishu_target_recollect.py` | 新 app 域收件人重采工具（W3 sender 统一前置，2026-10-24）。 |
| `portal/feishu_w3_preflight.py` | W3 切换窗口前置检查（GO / PARTIAL-GO / NO-GO，2026-10-24）。 |
| `portal/gen_result_evidence.py` | WO-OS-27 首个真实 ResultEvidence 供数器（2026-10-21）。 |
| `portal/harvest_adoption.py` | harvest_adoption.py — 诸葛亮建议采纳率归集器（C1 · 服务器侧方案，2026-10-23）。 |
| `portal/hermes_report_job.py` | Hermes 军机驿日报/周报/月报 · 平台侧定时抓取与投递（H3 收尾，2026-10-26）。 |
| `portal/identity_registry.py` | IdentityContext registry resolver（P3 统一身份，只读）。 |
| `portal/inbound_gateway.py` | W43 入向受理器（飞书通知指挥四期 · 环 2 投喂环主施工）。 |
| `portal/intel_feishu_export.py` | K-MDC-E3 (阚泽·信驿司): feishu intel export — windowed, read-only, receipted. |
| `portal/issues_store.py` | issues_store.py — OS 问题账本（问题生命周期管理：发现→认领→解决→清除归档）。 |
| `portal/jev_card_action.py` | 盯盘预警卡片动作统一 handler（W3 清债：9104 老链迁移目标，2026-10-24）。 |
| `portal/job_spec_registry.py` | Persistent, non-executable descriptors for minute execution sources. |
| `portal/kg/__init__.py` | api_kg 拆分目录：kg_html / kg_service（第一批）。 |
| `portal/kg/kg_html.py` | kg 页族 HTML 片段（resonance/net/overview）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。 |
| `portal/kg/kg_js.py` | KG 页面内嵌 JS 常量（拆分收尾，2026-10-30 自 api_kg.py 迁出）。 |
| `portal/kg/kg_pages_canvas.py` | kg 页族 canvas/canvas_v2（api_kg 拆分第四批，纯搬运 self→h）。 |
| `portal/kg/kg_pages_events.py` | kg 页族 events/doc/battle（api_kg 拆分第三批，纯搬运 self→h）。 |
| `portal/kg/kg_pages_misc.py` | kg 页族 misc（api_kg 拆分第二批：ask/lexicon/flow/label/card/shadow/map/company/page）。 |
| `portal/kg/kg_service.py` | kg 数据/写入 helper（label/card save+diff）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。 |
| `portal/kg_stock_query.py` | 轻量股票血缘查询脚本（供 Bot subprocess 调用）。 |
| `portal/km_feishu_hub.py` | KM 知识库飞书联动枢纽（127.0.0.1:8099）。 |
| `portal/legacy_telemetry.py` | 旧模板调用遥测（P3 观察期，2026-10-25）。 |
| `portal/legion_write_guard.py` | 军团写接口的浏览器上下文守卫（2026-10-30 P0 安全加固）。 |
| `portal/m_analysis.py` | 分析总览 H5 payload（2026-10-26，统一分格第一批）。 |
| `portal/m_api.py` | 移动端页面数据接口 /api/m/*（P1，2026-10-25）。 |
| `portal/m_brief.py` | 信息采集·今日简报 H5 payload（2026-10-26）。 |
| `portal/m_contract.py` | 移动端页面契约加载与校验 v1（2026-10-25）。 |
| `portal/m_feed.py` | 投喂面 H5 payload（P1-3，2026-10-26）：/m/feed。 |
| `portal/m_links.py` | 卡片/消息里的移动端链接自动签发（2026-10-25，P1 后续批次）。 |
| `portal/m_public_gate.py` | 移动端公网受限门 m_public_gate.py（2026-10-25，P0 公网方案执行）。 |
| `portal/m_push.py` | 推送中台 H5 payload（2026-10-26，本地直读版）。 |
| `portal/m_score.py` | 5 核心评分口径快照（B1-2，2026-10-26）。 |
| `portal/m_strategy.py` | 策略工单 H5（W43 环 3 工程落地，2026-10-26）。 |
| `portal/m_summary.py` | 8091 只读汇总 API（供 Hermes 总控/H4 协同，2026-10-26）。 |
| `portal/m_test_cards.py` | 一键发送「逐页测试卡」脚本（2026-10-25）。 |
| `portal/m_tokens.py` | 移动端页面令牌（HMAC-SHA256）v1（2026-10-25）。 |
| `portal/market_performance.py` | 只读绩效账本投影（K-STOCK-132）。 |
| `portal/notification_decision.py` | K-PUSH-012: 人工/Hermes 通知决策账；执行需单独启用，不使用 schedule.json 触发。 |
| `portal/notify_contracts.py` | K-ORG-201 轨道A：通知指挥底座契约（单一事实源）。 |
| `portal/org_command_api.py` | org_command_api.py — Agent 指挥台后端（K-ORG-190，2026-10-19 老田拍板四项落地） |
| `portal/os_asr.py` | 本地离线高精度语音识别（SenseVoiceSmall / funasr）—— 加固版。 |
| `portal/os_bp.py` | G2a：商业计划（BP v1/v2）隔离生成器。 |
| `portal/os_controlled.py` | C 阶段最小真实受控闭环（CONTROLLED_ONLY）。 |
| `portal/os_goal_contract.py` | OS 目标—事项—成果合同 v1.1（G0 内核判据，纯函数、只读、可单测） |
| `portal/os_goal_store.py` | OS 事项账本 v1.1（G0：append-only 事件账本 + 哈希链 + 投影） |
| `portal/os_quickstart.py` | Quickstart：一句话 → 一张卡 → 确认即执行（首版主交互） |
| `portal/os_software_flow.py` | 软件面流程（G1）：需求→推荐卡→授权→工单/认领→隔离执行→测试→试用→验收→返工 v2 |
| `portal/paper_data.py` | paper MVP 只读数据口（2026-10-30 WO-INV-PAPER-UI）。 |
| `portal/policy_geo_collector.py` | policy_geo_collector.py — 政策地缘情报域采集器（免费公开源起步）。 |
| `portal/pool_data.py` | ACAD1（司马徽）推荐池只读数据口（2026-10-30 WO-INV-POOL-UI）。 |
| `portal/portal_api.py` | OS 控制台 API 桥（纯标准库）— 127.0.0.1:8091 |
| `portal/portal_db.py` | 综合平台数据底座：portal.db 三表（K-STOCK-094，K-STOCK-093 第一步）。 |
| `portal/portal_report.py` | 青萍之末分析师报告引擎（portal_report）—— K244。 |
| `portal/publish_picks.py` | publish_picks.py — X5 选股公网发布一键链（sync + 多文件线上比对 + 状态留痕） |
| `portal/publish_schedule_server.py` | publish_schedule_server.py — picks 定时发布管理页 + 本地调度器（127.0.0.1:8095） |
| `portal/publish_watchdog.py` | publish_watchdog.py — picks 公网发布看门狗（本地定时兜底） |
| `portal/push_605100_drill.py` | 605100 华丰股份 L0 正式折叠报告（下钻形态实机验证）。 |
| `portal/push_admin_server.py` | AI 工作站 · 推送中台（127.0.0.1:8097）——消息触达底座，商业化入口。 |
| `portal/push_daily_digest.py` | 军机驿·推送日报（P1-4，2026-10-26）：每日一条只读摘要卡。 |
| `portal/push_egress_audit.py` | 推送出口审计（P0-1，2026-10-26）。 |
| `portal/push_legacy_audit.py` | 旧模板调用审计（P3 观察期，2026-10-25）。 |
| `portal/push_matrix.py` | 市场推送矩阵解析器（K-ORG-197 轨道A，W32 提前实装 2026-10-19）。 |
| `portal/push_matrix_send.py` | 客户推送发送器（K-ORG-197 轨道C，工单 W34 提前实装）。 |
| `portal/push_picks_digest.py` | 今日选股 · 手机速览版直投（内容直投方案 A）。 |
| `portal/push_picks_public_link.py` | 今日选股公网链接（带访问码）→ 飞书（个人 + 家人群）。 |
| `portal/push_picks_upgrade_note.py` | picks.html 两轮改版升级通告 → 飞书（个人 + 机器人在的群）。 |
| `portal/push_unified_adapter.py` | P2 生产者统一分装适配器（2026-10-25）。 |
| `portal/qd_daily_snapshot.py` | qd_daily_snapshot.py — 全市场基础数据每日快照（K-STOCK-028） |
| `portal/qd_replay_verify.py` | 尾盘两段式历史回放验证（K-STOCK-025 验证轮） |
| `portal/qd_scanner.py` | 量子双刀全市场扫描器（研究预览版，K-STOCK-022） |
| `portal/qd_view.py` | qd_view.py — 量子双刀 · 选股中心 展示层（K-STOCK-027 分层重构） |
| `portal/quant_mid/__init__.py` | — |
| `portal/quant_mid/api_routes.py` | K-QUANT-002: quant_mid REST API 子路由 —— 挂载到 8097 push_admin_server. |
| `portal/quant_mid/guojia_adapter.py` | K-QUANT-004: 郭嘉·缠论信号适配层 — chan_monitor → trade_pool. |
| `portal/quant_mid/test_trade_pool.py` | K-QUANT-001: trade_pool 单元测试 (mock psycopg2). |
| `portal/quant_mid/test_trade_pool_feedback.py` | K-QUANT-004: trade_pool 双向反馈列 (feedback_*) 单元测试. |
| `portal/quant_mid/trade_pool.py` | K-QUANT-001: trade_pool 推池接口 + 状态机。 |
| `portal/rate_limit.py` | P2.5 生产化：轻量内存限流（滑窗），供门户/客户 API 使用。 |
| `portal/redeliver.py` | W41 死信自动补达（2026-10-26）：退避 + 次数封顶 + 独立补达流水。 |
| `portal/register_feishu_watchdog_task.py` | 注册 FeishuBotWatchdog SYSTEM 计划任务（每 5 分钟自愈拉起飞书机器人）。 |
| `portal/renewal_reminder.py` | 订阅到期前提醒 · 纯函数能力（飞书中台 P3.7-lite · L-B 工位 2026-10-20）。 |
| `portal/renewal_state.py` | 订阅到期提醒 · 时区口径 + 已发去重状态（P3.8-lite · M-A 工位 2026-10-20）。 |
| `portal/risk_feedback_ledger.py` | 执行反馈账本 v0（盯盘→策略回流 · 协作协议 v0 账本解耦）。 |
| `portal/route_baseline.py` | portal_api.py 路由回归基线（K-STOCK-077 P-4 第一步）。 |
| `portal/route_table.py` | portal_api GET 精确路由表（B4a-lite，2026-10-31 关羽）。 |
| `portal/run_bot_now.py` | — |
| `portal/run_build_pick_verdicts_safe.py` | build_pick_verdicts.py 安全运行器（P2-1）：固定 py311 + 超时 + 分片 + 进度日志 + 独立输出。 |
| `portal/safe_urlopen.py` | 安全 urlopen：仅允许 http/https scheme（portal 树副本）。 |
| `portal/score_guard.py` | 评分口径守卫（B1，2026-10-26）：5 个核心指标的强制校验/展示规范。 |
| `portal/score_registry.py` | 零界评分口径注册表（Score Registry，2026-10-26）。 |
| `portal/server_gate.py` | 今日选股公开访问壳：访问码 Cookie 门禁 + 路径白名单（源头版，进 git）。 |
| `portal/setup_tunnel_api.py` | Cloudflare API Token 模式：建命名隧道 → 绑 DNS → 起 cloudflared → 改链接基地址。 |
| `portal/share_tokens.py` | 分享令牌（K-PUSH-002 A1）——推送卡片深链的溯源 token：签发/校验/访问账。 |
| `portal/sim_confirm.py` | 门户：模拟交易人工确认（WO-BOT-CONFIRM）。 |
| `portal/start_feishu_bot.py` | FeishuBotWatchdog 拉起器：无 feishu_bot_ws 进程则 DETACHED 启动。 |
| `portal/stock_card.py` | stock_card.py — 全站统一股票详情卡（K-STOCK-051，046 §5.1 一致性规范落地） |
| `portal/strategy_market.py` | K-STOCK-122 策略市场数据层（纯只读，零第三方依赖）。 |
| `portal/strategy_pool_projection.py` | Fail-closed, read-only strategy-pool membership projection. |
| `portal/strategy_ring.py` | W43 策略工单环 MVP（2026-10-26）：选股方法 → 工单 → LLM 草案 → 回测留桩 |
| `portal/strategy_worker.py` | 策略工单后台 worker（W43 环 3）：解析→回测→前视→影子/拒绝。 |
| `portal/subscription_store.py` | 订阅账本最小集（飞书中台 P2 · FC-B 工位，契约先行）。 |
| `portal/subscriptions.py` | K-STOCK-122 M3 订阅与组合数据层（单用户版，纯标准库）。 |
| `portal/sync_ledger.py` | 交易台账快照生成（K-STOCK-353）：quant_backtest/positions.json → portal/data/ledger.json。 |
| `portal/sync_publish.py` | sync_publish.py — X5 快照刷新一键脚本 |
| `portal/tech_feed.py` | tech_feed.py — 科技前沿域采集（免费公开源）。 |
| `portal/test_alert_source_health.py` | Adversarial, isolated tests for the alerts display and source evidence. |
| `portal/test_alerts_level.py` | E8 预警分级：build_alerts 每条 alert 须含 level ∈ {market,eng,strategy}， |
| `portal/test_api_brief.py` | 复盘简报只读投影单测（仿 test_api_tickets.py / test_api_gate.py 判例）。 |
| `portal/test_api_common_env_allowlist.py` | B5（2026-10-21）：_load_cainiao_env 注入面白名单化测试。 |
| `portal/test_api_evolution.py` | 进化账本只读投影单测（仿 test_api_tickets.py 判例）。 |
| `portal/test_api_flow.py` | R4 rerun 扩容单测（仿 test_api_evolution.py 判例）。 |
| `portal/test_api_gate.py` | 周一门判只读投影单测（仿 test_api_tickets.py 判例）。 |
| `portal/test_api_guojia.py` | 郭嘉出口单测（K-ORG-191，仿 test_api_brief.py 判例，tmp 注入不读真实产物）。 |
| `portal/test_api_news_intel.py` | K-ORG-207/208：api_news_intel 判读输出台聚合单测。 |
| `portal/test_api_notify.py` | K-ORG-201 W36：api_notify 四态队列只读聚合单测。 |
| `portal/test_api_tickets.py` | 工单只读投影单测（驾驶舱一期，仿 test_execution_mixin.py 判例）。 |
| `portal/test_api_zhuge_adoption.py` | test_api_zhuge_adoption.py — C3 契约测试：POST /api/zhuge/adoption 路由接线。 |
| `portal/test_api_zhuge_brief.py` | — |
| `portal/test_api_zhuge_route.py` | — |
| `portal/test_approval_flow.py` | K-ORG-201 W37/W38：公告审批流（卡片确认）+ 送达证据/死信 单测。 |
| `portal/test_auto_commit_secret.py` | K-STOCK-185：agent_auto_commit 敏感内容守卫正则自测（纯函数级，不碰 git）。 |
| `portal/test_bot_ws_backoff.py` | P2.7 bot WS 内部重连退避单测（G-A 工位）。 |
| `portal/test_bundle_branding.py` | P2.6 FE-A：8787 大 bundle 品牌残余分拣抽查。 |
| `portal/test_cainiao_auth.py` | cainiao_auth.py 单元测试：不依赖外部网络（mock urllib / 本地 http.server）。 |
| `portal/test_capability_mixin.py` | portal mixin pytest 收编（M3-P5）：api_capability + api_unified 读侧。 |
| `portal/test_card_actions_dispatch.py` | WO-FS-141 · P3.11-lite：card_actions.dispatch 买点订阅/退订迁移单测。 |
| `portal/test_card_audit.py` | H-B · card_audit 审计脚本单测（P3-lite 波次 2026-10-20，离线只读）。 |
| `portal/test_card_templates.py` | FB-L · card_templates 单测（P1 波次 2026-10-20，离线纯函数）。 |
| `portal/test_comm_dispatch.py` | 阚泽部派单通道单测（K5.1，WO-FS-148-7）。 |
| `portal/test_comm_intake.py` | 阚泽部 会话记忆 + 智能接话层单测（K2.1/K2.2/K3.1）。 |
| `portal/test_comm_interest.py` | 阚泽部 customer_interest 聚合与商机信号单测（K4.1/K4.2）。 |
| `portal/test_comm_interest_api.py` | `/api/comm/interest` 商机信号只读端点测试（K4.2 展示端，WO-FS-148-9）。 |
| `portal/test_comm_router.py` | 阚泽部通信路由核心单测（K1.1/K1.2/K1.3，WO-FS-148-2/3/4）。 |
| `portal/test_comm_summarize.py` | 阚泽部出向智能摘要单测（K5.2）。 |
| `portal/test_comm_zhuge.py` | 阚泽部诸葛亮咨询位单测（K6，WO-FS-148-10）。 |
| `portal/test_console_contract.py` | K-ORG-182：/console 离线入口、API 归属与执行健康契约。 |
| `portal/test_daily_subscription_job.py` | N-B · 订阅日跑编排入口单测（飞书中台 P3.9-lite · WO-FS-122，离线）。 |
| `portal/test_dataops_v3.py` | test_dataops_v3.py — 门户「指挥操作系统 v3」统一验收脚本 |
| `portal/test_debt_cleanup.py` | P2.5 FD-C · 遗留债清尾 · 离线契约测试（2026-10-20）。 |
| `portal/test_dengzhi_branding.py` | FB-N · 邓芝 branding 测试（P1 波次，全部离线只读）。 |
| `portal/test_domain_routing.py` | P2 分域（FC-A）：domain 一级路由字段单测。 |
| `portal/test_events.py` | R6 事件总线 api_events 单测（仿 test_api_brief.py 判例）。 |
| `portal/test_execution_mixin.py` | R1 执行健康总线单测（联动层一期 T1，对齐 test_capability_mixin.py 判例）。 |
| `portal/test_feed_redteam.py` | 投喂补强 · 注入红队 20 条（W42 验收门禁，2026-10-26）。 |
| `portal/test_feishu_acceptance.py` | W4 验收脚本单测：检查聚合口径、Markdown 渲染、CLI 可执行、真实检查项可跑。 |
| `portal/test_feishu_app_pool.py` | feishu_app_pool 单测（W2 前置）：角色凭据确定化、sender 选择、目标域校验、状态报告。 |
| `portal/test_feishu_bot_base.py` | feishu_bot_base 单测（W1）：群策略纯函数、凭据加载、心跳、AT 清洗、注册表联动。 |
| `portal/test_feishu_console.py` | feishu_console（邓芝台 W1 离线版）单测：数据采集结构、检查项、HTML 渲染、JSON 输出。 |
| `portal/test_feishu_console_annotations.py` | OS-FIX-W4CLOSE：push_flow 测试归因清单（feishu_console）回归。 |
| `portal/test_feishu_dsh_relay_allowed.py` | feishu_dsh_relay.allowed 多域白名单单测（2026-10-25 换新 app open_id 修复）。 |
| `portal/test_feishu_event_guard.py` | — |
| `portal/test_feishu_p0_fix.py` | P0 出口修复单测（2026-10-19 派工单 FB-B / B5）。 |
| `portal/test_feishu_push.py` | 飞书推送统一封装单测（2026-10-25）。 |
| `portal/test_feishu_registry.py` | feishu_registry 单测（W1 底座）：合法/非法注册表、路由匹配、白名单、通道铁律。 |
| `portal/test_feishu_rollback_drill.py` | W4 回滚演练单测：材料核验结构、回滚清单完整、结论口径、CLI。 |
| `portal/test_feishu_sender_switch.py` | sender 统一切换器单测：目标域阻塞判定、步骤清单、状态盘点、CLI。 |
| `portal/test_feishu_sop_rehearsal.py` | 新增机器人 7 步 SOP 离线演练（W4 预演，不创建真实飞书 app）。 |
| `portal/test_feishu_sop_runner.py` | W4 SOP 执行器单测：dry-run 7 步全绿、无生产副作用、live 缺角色 FAIL、CLI。 |
| `portal/test_feishu_target_audit.py` | feishu_target_audit 单测：分类、注入 opener 的校验、汇总口径（离线，不触网）。 |
| `portal/test_feishu_target_recollect.py` | 收件人重采工具单测：纯匹配逻辑、补丁生成、离线 chats-file、CLI 无凭据降级。 |
| `portal/test_feishu_w3_preflight.py` | W3 前置检查单测：结论口径、凭据判定、渲染、CLI。 |
| `portal/test_gen_result_evidence.py` | WO-OS-27 契约测试：真实证据供数器（先红后绿）。 |
| `portal/test_harvest_adoption.py` | test_harvest_adoption.py — 采纳率归集器测试（C1 · 服务器侧方案）。 |
| `portal/test_health_page_async.py` | 抽取真实门户函数，隔离验证慢扫描、缓存及并发刷新，不加载凭据。 |
| `portal/test_inbound_feed_card.py` | J-B · inbound:feed 注册单测（P3.5-lite 波次 2026-10-20，离线纯函数）。 |
| `portal/test_inbound_gateway.py` | W43 入向受理器单测（施工单强制门禁锁定项）。 |
| `portal/test_inbound_subscription.py` | FC-C · 入向订阅闸 + 引导卡片单测（P2 波次 2026-10-20）。 |
| `portal/test_init_subscription_db.py` | 订阅账本离线初始化工具单测（飞书中台 P3.8-lite · M-C / WO-FS-113）。 |
| `portal/test_intel_bus.py` | 情报总线出口单测 + 量化路径影子对账（2026-10-19 联调首包）。 |
| `portal/test_jev_card_action.py` | jev_card_action 单测（W3 清债：9104 老链迁移目标）+ dispatch 接线。 |
| `portal/test_jev_query.py` | `/api/jev/query` 单测（K-ORG-20261026-JEVAPI）。 |
| `portal/test_jev_review_api.py` | JEV 低置信复核 API 回归：列表筛选/排序/裁决写回。 |
| `portal/test_km_card_kind.py` | P3.5-lite km_card 链路 kind 透传单测（2026-10-20 派工单 J-A）。 |
| `portal/test_legion_auth_p0.py` | P0 鉴权整改专项回归（2026-10-19 议事厅 G2 前置）。 |
| `portal/test_legion_g3_release_gate.py` | G3 发布前离线安全闸门：仅使用临时房间和内存 HTTP handler。 |
| `portal/test_legion_live_boot_p0.py` | P0 鉴权整改真实 HTTP 验证：临时环回端口启动 H2，不触碰现役 8096。 |
| `portal/test_legion_private_get_auth.py` | H2 私人 GET 路由读鉴权：内存 handler 执行真实 do_GET 与 _send。 |
| `portal/test_legion_resolution_projection.py` | G3 决议只读投影专项测试：隔离房间数据、临时端口运行真实 H2 路由。 |
| `portal/test_legion_room_api_live.py` | 议事厅（G2）实机 API 验证：临时端口 18096 拉起 H2，走真实 HTTP 全链路。 |
| `portal/test_legion_room_list_end_to_end.py` | 临时 HTTP H2 档案列表到 OS 页面脚本的离线跨层验收（非浏览器）。 |
| `portal/test_legion_room_list_health.py` | 议事厅列表健康元数据与读鉴权回归。 |
| `portal/test_legion_room_unit.py` | 议事厅（G2）模块单元测试：多轮消息、版本化白板、边界与防篡改。 |
| `portal/test_m_analysis.py` | 分析总览 payload 单测（2026-10-26 统一分格第一批）。 |
| `portal/test_m_api.py` | P1 移动端接口单测：令牌→scene 数据、picks 每票短链、埋点校验、HTTP 适配桩。 |
| `portal/test_m_contract.py` | 移动端契约与令牌单测（P0）：四契约校验、签名/验签、过期/作用域。 |
| `portal/test_m_links.py` | m_links 卡片链接自动签发单测（P1 后续批次）。 |
| `portal/test_m_public_gate.py` | 移动端公网受限门单测（白名单/限流）。 |
| `portal/test_market_performance.py` | /market 只读绩效账本的契约测试。 |
| `portal/test_model_endpoint_guard_offline.py` | Offline, AST-isolated security regression for the two model-backed POST routes. |
| `portal/test_my_subscription.py` | 客户订阅面离线契约测试（飞书中台 P2.5 · FD-A 工位）。 |
| `portal/test_my_subscription_security.py` | 客户面订阅端点安全加固回归（WO-FS-101 · P3.7-lite R1）。 |
| `portal/test_my_subscription_ux.py` | 客户订阅面「到期可视化」离线契约测试（飞书中台 P3.9-lite · N-C 工位 2026-10-20）。 |
| `portal/test_notification_decision.py` | — |
| `portal/test_notify_blocked_archive.py` | blocked 存量治理（方案 A：T+7 过期归档 + 簇折叠投影）单测。 |
| `portal/test_notify_contracts.py` | K-ORG-201 轨道A：notify_contracts 底座契约单测。 |
| `portal/test_ops_board.py` | FB-M 运维对账面 · 离线契约测试（2026-10-20 飞书中台 P1） |
| `portal/test_ops_probe.py` | P2.6 FE-C · 8097 观测债三项 · 离线契约测试（2026-10-20）。 |
| `portal/test_org_command_api.py` | org_command_api 单测（K-ORG-190 拍板落地验收） |
| `portal/test_org_e2e.py` | K-ORG-190 指挥台端到端预演（容器重启前的预验凭据） |
| `portal/test_org_guard_p1.py` | P1 判定源修复 + org 写回端点守卫补丁回归测试（2026-10-19 老田批准执行）。 |
| `portal/test_os_council.py` | test_os_council.py — 会议厅契约测试（S-1b）。 |
| `portal/test_os_entry_adoption_link.py` | os_entry 建议卡 linkFor：decision_id 透传契约（静态检查）。 |
| `portal/test_p0p1_batch.py` | P0/P1 批次单测：评分注册表 / 授权主表 / 投喂面 payload。 |
| `portal/test_p2_batch.py` | P2 单测：W41 死信补达 + W43 策略工单环 + 投喂补强（抽取/L3 标注）。 |
| `portal/test_perf_mixin.py` | M4-P5 投研绩效看板 pytest：api_perf mixin（perf board + 四环回放）。 |
| `portal/test_portal_compat_regressions.py` | 门户模块拆分后的兼容回归测试。 |
| `portal/test_portal_key_rotation.py` | — |
| `portal/test_publish_allowlist.py` | — |
| `portal/test_push_admin_server.py` | 推送调度保存与计划任务联动结果；所有计划任务调用均打桩。 |
| `portal/test_push_daily_digest.py` | OS-FIX-W4CLOSE：push_daily_digest 统一发送 + 静默窗回归。 |
| `portal/test_push_legacy_audit.py` | 旧模板调用审计单测（P3 观察期）。 |
| `portal/test_push_matrix.py` | 市场推送矩阵解析器单测（K-ORG-197 轨道A，纯离线零触网）。 |
| `portal/test_push_matrix_send.py` | 市场推送矩阵发送器单测（K-ORG-197 轨道C，纯离线零触网）。 |
| `portal/test_push_unified_adapter.py` | P2 适配器单测：disabled 原样 / shadow 只记录 / enabled 换新卡 / 异常回落。 |
| `portal/test_qd_amount_fix.py` | qd_scanner 尾盘复核量纲修复（W1-2 任务B 20261014）单测。 |
| `portal/test_qd_breadth_note.py` | W3-5b 任务B 单测：量子双刀市况宽度影子注记（离线合成数据，不触网、 |
| `portal/test_qd_multisource.py` | qd_scanner 多源降级（[20261014_p1_tail] 腾讯→新浪→东财）单测。 |
| `portal/test_reconcile_subscription.py` | 订阅账本勾稽自检单测（P3.7-lite · L-D / WO-FS-104）。 |
| `portal/test_renewal_reminder.py` | L-B · 订阅到期提醒单测（P3.7-lite 2026-10-20，离线纯函数）。 |
| `portal/test_renewal_state.py` | M-A · 已发去重状态 + 时区口径单测（P3.8-lite 2026-10-20，离线）。 |
| `portal/test_revoke_cmd.py` | bot 指令族「撤销 <open_id> <档位>」桩测（P3.13-lite ② 方案 a，2026-10-21）。 |
| `portal/test_revoke_subscription.py` | 撤销订阅方案 a 单测（2026-10-21 老田拍板，WO-FS-145 主会话实施）。 |
| `portal/test_sa_single_write.py` | 回归守卫：/sa 页面不得在 _html() 完整响应后再写 wfile（K-ORG-20261026-SAWRITE）。 |
| `portal/test_score_guard.py` | score_guard 测试（B1 评分口径强制，2026-10-26）。 |
| `portal/test_strategy_h5.py` | 策略工单 H5 单测（W43 环 3 工程落地）。 |
| `portal/test_sub_admin.py` | FD-B 商务运营面 · 离线契约测试（2026-10-20 飞书中台 P2.5） |
| `portal/test_sub_admin_cmds.py` | 飞书中台 P3-lite 订阅管理员指令族单测（H-A 工位）。 |
| `portal/test_sub_admin_list_security.py` | `/api/subscription/list` 鉴权加固离线安全测试（WO-FS-112 · P3.8-lite M-B）。 |
| `portal/test_sub_admin_p36.py` | 订阅管理员指令健壮性单测（飞书中台 P3.6-lite · K-B 工位）。 |
| `portal/test_sub_admin_paging.py` | `/api/subscription/list` 分页接线 + 订阅运营面翻页（WO-FS-131 · P3.10-lite O-A）。 |
| `portal/test_subscription_list.py` | 订阅批量列表端点离线契约测试（飞书中台 P2.6 · FE-B 工位）。 |
| `portal/test_subscription_paging.py` | 订阅者分页离线单测（飞书中台 P3.9-lite · N-A 工位 2026-10-20）。 |
| `portal/test_subscription_smoke.py` | O-C · G3 演练脚本单测（P3.10-lite · WO-FS-133，2026-10-20）。 |
| `portal/test_subscription_store.py` | 订阅账本最小集单测（飞书中台 P2 · FC-B 工位）。 |
| `portal/test_ticket_loop.py` | 派单巡视自动催办单测（2026-10-25）：去重 / 合并 / 历史工单收口。 |
| `portal/test_voice_ingest.py` | voice_ingest 回归测试：无网络、无凭证、无 lark SDK 也必须能跑完。 |
| `portal/test_w40.py` | W40 收口单测：卡片签名/幂等、操作人配对、超时升级、日报每日一次。 |
| `portal/test_wall_adapter.py` | F4 观点墙适配器测试：wall.json schema 键齐全、幂等、坏输入 fail-visible、 |
| `portal/test_warehouse_dashboard.py` | 验证真实渲染的数据口径及不可信内容转义，不访问生产数据。 |
| `portal/test_warehouse_update_api.py` | 每日接口仅使用临时任务库和注入服务，不整体导入门户。 |
| `portal/test_warehouse_update_routes.py` | AST隔离新路由，禁止整体导入门户的认证初始化与外发功能。 |
| `portal/test_warehouse_update_services.py` | 真实临时仓的门户服务适配，不加载门户或采集配置。 |
| `portal/test_watch_tower_linkage.py` | — |
| `portal/test_watch_tower_snapshot.py` | — |
| `portal/test_zhuge_guard.py` | 诸葛亮 L5 注入护栏契约。 |
| `portal/test_zhuge_morning.py` | — |
| `portal/test_zhuge_morning_review.py` | 晨报离线审核闸：所有测试只用 tmp_path，不访问生产账本或发送端。 |
| `portal/tests/test_api_backtest_contract.py` | api_backtest.py 契约测试（B0 冻结，2026-10-30 · 关羽）。 |
| `portal/tests/test_api_backtest_minute.py` | Minute API integration tests with an import-time isolated JobSpec catalog. |
| `portal/tests/test_api_commercial.py` | P2.5: 商业化接口网关 token 测试。 |
| `portal/tests/test_api_customer.py` | P2.4 客户门户 API 测试（token/校验/路径防护，不跑真实分析）。 |
| `portal/tests/test_api_d1_decision.py` | D1 decision API 契约测试（WO-D1-DECISION-20261101）。离线，不写生产账。 |
| `portal/tests/test_api_d1_handoff.py` | D1 交棒回执 API 测试（WO-D1-HANDOFF-20261101）。离线。 |
| `portal/tests/test_api_d1_meeting.py` | D1 投研会 API 契约测试（WO-D1-MEETING-20261101）。离线。 |
| `portal/tests/test_api_d1_research.py` | D1 投研工作台页面/路由测试（WO-D1-RESEARCH）。 |
| `portal/tests/test_api_hardening_p0b.py` | P0-B API 加固测试（K-ORG-20261031-APIHARDEN，审计 v2 H3/M3/M5）。 |
| `portal/tests/test_api_intel_evidence.py` | — |
| `portal/tests/test_api_intel_graph.py` | K-MDC-E2 tests: dossier endpoint — honest empty, candidate-only, traceable. |
| `portal/tests/test_api_kg_contract.py` | api_kg 契约测试骨架（2026-10-28 首建，拆分前先钉住行为）。 |
| `portal/tests/test_api_paper.py` | paper 页面测试（2026-10-30 WO-INV-PAPER-UI）。 |
| `portal/tests/test_api_pool_page.py` | ACAD1 推荐池页面测试（2026-10-30 WO-INV-POOL-UI）。 |
| `portal/tests/test_api_sim_confirm.py` | 门户人工确认入口测试（WO-BOT-CONFIRM）。 |
| `portal/tests/test_api_war_room.py` | 作战台页面/路由测试（WO-WARROOM-LIVE-20261030）。 |
| `portal/tests/test_api_zhuge_review.py` | api_zhuge_review HTTP 适配器测试（stub handler，不起服务）。 |
| `portal/tests/test_auth_contracts.py` | P3 后半段认证契约测试：SessionContext / RFC 8693 Token Exchange。 |
| `portal/tests/test_auth_gateway.py` | Phase A auth-gateway 单元测试（无网络、无真实凭据）。 |
| `portal/tests/test_auth_local_admin.py` | 本地管理员（vault PBKDF2 哈希）回归。 |
| `portal/tests/test_auth_oidc.py` | B1 OIDC 客户端测试：默认关、start/PKCE、callback/JWT/nonce、session/映射。 |
| `portal/tests/test_auth_oidc_runtime.py` | B2 runtime 包装层测试（不修改 auth_oidc.py）。 |
| `portal/tests/test_d1_levels_ui.py` | D1 水平来源/ATR/支撑阻力展示与 pending 防误导回归（WO-D1-LEVEL-UI-20261031）。 |
| `portal/tests/test_d1_research_ui.py` | D1 投研页正式裁决接入测试（WO-D1-UI-20261101，离线）。 |
| `portal/tests/test_dataops_v3.py` | S1-03 输出级健康字段单测（portal/api_dataops.py）。 |
| `portal/tests/test_drift_error_structured.py` | S1-04 F3/F4 单测：drift 错误结构化 + portal 侧 DSN 去重（全 mock，不连生产 PG）。 |
| `portal/tests/test_health_portal_mixin.py` | B2b 健康/观测拆分不变量：锁对象共享、缓存归属、方法经 MRO 可见（2026-10-31）。 |
| `portal/tests/test_huangzhong_events.py` | GET /api/huangzhong/events 只读投影单测（WO-HZ-HERMES-PHASE2-20261031）。 |
| `portal/tests/test_huangzhong_events_page.py` | A2 事件 UI 页（portal/static/huangzhong_events.html）存在性/只读性/增强元素守护测试。 |
| `portal/tests/test_identity_registry.py` | P3 统一身份映射测试（2026-10-30）。 |
| `portal/tests/test_intel_dossier_skeleton.py` | K-INTEL-20261022-FE: offline skeleton contracts, without loading live sources. |
| `portal/tests/test_intel_feishu_export.py` | K-MDC-E3 tests: window gate, digest honesty, four-account receipt. 全离线不真发. |
| `portal/tests/test_intel_privacy.py` | 情报私有化 nginx 静态回归（2026-10-31 拍板 1「全私有」，WO-INTEL-PRIVATE-20261031）。 |
| `portal/tests/test_intel_sort_order.py` | 情报排序/新鲜度回归（2026-10-31 排序拍板，WO-INTEL-PRIVATE-20261031）。 |
| `portal/tests/test_intel_workbench_xq_link.py` | Offline regression for evidence-to-Xueqiu ticker links; no live source calls. |
| `portal/tests/test_jev_review_frontend_identity.py` | JEV 复核前端身份回归（K-ORG-20261101-H4HARDEN，审计 v2 M3 收尾）。 |
| `portal/tests/test_job_spec_registry.py` | — |
| `portal/tests/test_job_spec_registry_expiry_recovery.py` | — |
| `portal/tests/test_legion_write_guard.py` | 军团写接口浏览器上下文守卫单测（2026-10-30 P0 安全加固）。 |
| `portal/tests/test_login_ui.py` | /login UI 与 /os-entry 登录态静态测试。 |
| `portal/tests/test_minute_router_isolated.py` | Exercise the actual portal HTTP router with an isolated minute JobSpec root. |
| `portal/tests/test_minute_router_process_restart.py` | Exercise minute HTTP recovery across two separate portal OS processes. |
| `portal/tests/test_nginx_api_auth_subset.py` | H4-B（K-ORG-20261101-H4HARDEN，审计 v2 H4）：/api/ 公开白名单 + 默认拒绝静态回归。 |
| `portal/tests/test_nginx_auth_request.py` | nginx auth_request 页面真保护配置回归（2026-10-30 L2）。 |
| `portal/tests/test_os_approval_voice_page.py` | 语音审批台页面结构测试（纯静态页，不改服务）。 |
| `portal/tests/test_os_asr.py` | VOICE3：本地 SenseVoice ASR 端点与页面接线测试。 |
| `portal/tests/test_os_asr_selfcheck.py` | ASR 自检守卫：路由存在且能返回自检结果。 |
| `portal/tests/test_os_auth_hardening.py` | H2（K-ORG-20261031-PORTALAUTH，审计 v2）：sign_real_grant 服务端一次性 nonce。 |
| `portal/tests/test_os_bp.py` | G2a：一句话 → 商业计划（BP v1/v2）同交互测试。 |
| `portal/tests/test_os_controlled.py` | C 阶段最小真实受控闭环（CONTROLLED_ONLY）测试。 |
| `portal/tests/test_os_controlled_audit.py` | C0/C1 审计修复回归测试：撤销/停用/重复裁决/过期/能力匹配。 |
| `portal/tests/test_os_controlled_c1.py` | C1：L2 人工审批通道测试（不外发，仅审批+受控 outbox）。 |
| `portal/tests/test_os_controlled_ui.py` | C1 审批台：消费者列表 / 审批单列表 API 与页面存在性测试。 |
| `portal/tests/test_os_entry_ia.py` | /os-entry v10 四视图信息架构契约（静态）。 |
| `portal/tests/test_os_entry_links.py` | zero_proto → Quickstart 一句话入口结构测试。 |
| `portal/tests/test_os_goal_api.py` | G0 API 纯函数单测（不起 HTTP、不绑端口）。 |
| `portal/tests/test_os_goal_contract.py` | G0 合同判据单测（OS-CLOSE-G0）。 |
| `portal/tests/test_os_goal_store.py` | G0 账本单测：append-only / 哈希链 / 幂等 / 投影 / 证据包。 |
| `portal/tests/test_os_page_tools.py` | OS 页面工具（搜索/折叠）契约。 |
| `portal/tests/test_os_quick_auth.py` | H1（K-ORG-20261031-PORTALAUTH，审计 v2）：/api/os/* 认证 + 预览防 XSS 回归。 |
| `portal/tests/test_os_quickstart.py` | Quickstart 单测：一句话→一张卡→确认即执行（三句话交互）。 |
| `portal/tests/test_os_software_flow.py` | G1 软件面流程单测（同一账本：需求→卡→授权→工单/认领→隔离执行→测试→试用→验收/返工）。 |
| `portal/tests/test_os_software_preview.py` | G1b：只读预览路由（白名单 + 路径限定）与软件面工作台页面静态检查。 |
| `portal/tests/test_page_guard.py` | 页面登录态软保护测试（Phase A，只读）。 |
| `portal/tests/test_persist.py` | P2.6: SQLite 滑窗限流（多实例共享），+ 持久化任务测试。 |
| `portal/tests/test_post_auth_early_branches.py` | P142 回归：do_POST 早分支变更端点必须先过 X-Portal-Token（审计 L-01/L-02）。 |
| `portal/tests/test_quant_alert_render_contract.py` | Offline assertions for the real quant portal alert loader (Node.js required). |
| `portal/tests/test_rate_limit.py` | P2.5: 限流测试。 |
| `portal/tests/test_risk_feedback_ledger.py` | 执行反馈账本 v0 最小测试：合成数据 + 临时目录，不碰 portal/data。 |
| `portal/tests/test_route_table_contract.py` | route_table 契约测试（WO-INTEL-PRIVATE-20261031 P0：'str' object is not callable 502 修复）。 |
| `portal/tests/test_run_build_pick_verdicts_safe.py` | P2-1: build_pick_verdicts 安全运行器与分片语义单测。 |
| `portal/tests/test_strategy_pool_projection.py` | Isolated deny-by-default strategy-pool projection and HTTP router tests. |
| `portal/tests/test_ui_adoption.py` | UI Kit 在两个高频面的接入契约。 |
| `portal/tests/test_ui_kit.py` | UI Kit v0.1 契约：组件/页面/交互原语存在。 |
| `portal/tests/test_unify_os_ui.py` | OS 全站注入工具测试：幂等、双标记、check/apply、空 body 兜底。 |
| `portal/tests/test_zhice_bff_contracts.py` | 三系统契约 + 只读 BFF 测试（2026-10-30）。 |
| `portal/tests/test_zhuge_ops.py` | Phase C：复核 rate limit / adoption 测试。 |
| `portal/tests/test_zhuge_review.py` | 诸葛亮 IDE 复核 P0 单测：默认关/校验/解析/异步/缓存。 |
| `portal/ticket_loop.py` | 派单闭环：催办 / 回复 / 状态摘要（2026-10-25，老田反馈"派单后只能刷新、没有催促"）。 |
| `portal/tools/export_xunyou_latest.py` | 导出荀攸盘后日报最新产物 → portal/data/xunyou_latest.json（G8 Agent 日报入口数据源） |
| `portal/tune_gate.py` | 调优闸门（K-PUSH-002 B2/B3）——客户反馈调优闭环的核心状态机。 |
| `portal/verify_watch_admin.py` | Independent verification for watch_admin_snapshot (M2/M3 read-only admin). |
| `portal/voice_ingest.py` | 飞书语音消息 ingest（P0：语音 → 文本） |
| `portal/wall_adapter.py` | F4 观点墙适配器：portal/data/todays_picks.json（+ 可选 pick_verdicts.json） |
| `portal/war_room_data.py` | 量化作战台实时数据聚合（2026-10-30 WO-WARROOM-LIVE）。 |
| `portal/warehouse_dashboard.py` | 数据仓五视图：服务端只消费快照，每日操作经浏览器明确预览与确认。 |
| `portal/warehouse_update_api.py` | 每日数据仓接口；仅接受服务端冻结计划，不整体导入门户或启动采集。 |
| `portal/warehouse_update_services.py` | 真实本地仓服务适配；无凭据导入、远端请求或旧任务历史倒推。 |
| `portal/watch_admin_route_ackdash.py` | Watch-admin read-only view: 单A — 实时 ACK / 多 Agent 投票 / 用量看板. |
| `portal/watch_admin_route_alert.py` | 告警契约离线看板 —— 只读路由（P2 特性 F，盯盘只读管理页 M2/M3）。 |
| `portal/watch_admin_route_backtest.py` | 盯盘只读管理页 —— 回测分面横向对比（只读路由）。 |
| `portal/watch_admin_route_chanlun.py` | Standalone read-only route: 缠论信号池全量下钻 (Chanlun signal-pool drill-down). |
| `portal/watch_admin_route_combos.py` | 组合器静态配置下钻（quad 展开 + 红线标注）只读路由。 |
| `portal/watch_admin_route_contract.py` | 盯盘只读管理页 —— 席位/模型契约一致性看板（只读路由，P2 特性 E）。 |
| `portal/watch_admin_route_shadow.py` | Read-only "影子账本逐票决策下钻" (shadow ledger per-ticket decision drill-down) |
| `portal/watch_admin_server.py` | Read-only admin HTTP server for the Watch multi-Agent management & model |
| `portal/watch_admin_snapshot.py` | Read-only Watch Admin snapshot aggregation (M2 real read-only + M3 A/B shadow). |
| `portal/watch_tower_linkage.py` | Read-only historical market linkage for Watch Tower replay. |
| `portal/watch_tower_snapshot.py` | Read-only Watch Tower snapshot aggregation. |
| `portal/weather_store.py` | weather_store.py — 环境日程域·天气源（wttr.in 免费无 key）。 |
| `portal/zhuge_guard.py` | 诸葛亮 L5 注入护栏：高置信度规则命中才阻断；其余放行并留痕。 |
| `portal/zhuge_morning.py` | WO-OS-18 诸葛亮晨报生成器（06 的分层落地：仅生成器）。 |
| `portal/zhuge_morning_review.py` | 诸葛亮晨报离线审核演练账。无发送器、无调度、无身份认证与真实送达能力。 |
| `portal/zhuge_review.py` | 诸葛亮 IDE 复核：只读、异步、可缓存（P0）+ 运营化（WO-LEGION-REVIEWOPS-20261031）。 |
| `tools/_korg056_dev1.py` | K-ORG-056 驱动脚本：研发部 DEV1 按方案条目产码（主控代跑产线接口）。 |
| `tools/_korg056_dev1_fix1.py` | K-ORG-056 迭代轮：冒烟缺陷反馈单回 DEV1（产线 F4 迭代回路）。 |
| `tools/_l2_guard.py` | L2 危险操作评审门（K-STOCK-416）——宿主机级危险操作合并拦截器。 |
| `tools/_mixin_integrity.py` | P4 拆分第四道门槛：Handler 方法集 AST 断言（K-STOCK-089）。 |
| `tools/_repo_paths.py` | _repo_paths.py — 统一「主工作树根」解析（linked worktree 回落主仓）。 |
| `tools/_scan_secrets_selftest.py` | scan_secrets 自验证：生成随机假密钥夹具 → 运行扫描器 → 程序化断言。 |
| `tools/a0_patrol.py` | 田丰总监察（K-ORG-121 L4 · A0 巡察） |
| `tools/a_phase_group_board.py` | A 阶段组级看板生成器（2026-10-27 WO-A-STAGE-BOARD）。 |
| `tools/acad1_candidate_preflight.py` | acad1_candidate_preflight.py — ACAD1 候选生成门预检包装（2026-11-01）。 |
| `tools/acad1_pool_adapter.py` | ACAD1 池只读适配器：复核卡 × 本仓数据仓（CN/HK/US；CRYPTO unsupported）。 |
| `tools/acad1_v2_export.py` | acad1_v2_export.py — 从张松仓（zhice v2）只读导出 ACAD1 30 卡桥接 JSON。 |
| `tools/acad_d4_freeze_check.py` | acad_d4_freeze_check.py — ACAD1/DATA1 D4 冻结对账（只读，2026-10-31）。 |
| `tools/acad_d4_tiebreak.py` | acad_d4_tiebreak.py — D4 冲突键 tie-break 策略模拟（只读，2026-10-31）。 |
| `tools/academy_ledger_check.py` | ACAD1 研究账本一致性校验（2026-10-30，关羽 P99）。 |
| `tools/academy_research.py` | 研究院课题台账与状态流转（K-ORG-126）。 |
| `tools/academy_worker_plan.py` | 研究院课题到 Legion Worker 工单的受控编排器。 |
| `tools/adr.py` | adr.py — 架构决策记录（ADR）登记工具（K-ORG-041，落点 docs/adr/） |
| `tools/agent_census.py` | agent_census.py — 智能体点名册：谁在干活、在干嘛、手里有没有活。 |
| `tools/agent_coder.py` | K-ORG-032: Agent 编码执行器——在编岗以人设真模型产出，直接写仓库工作区。 |
| `tools/agent_daily.py` | agent_daily.py — 每日工作项点名：把"他们每天干什么"变成可检查的事。 |
| `tools/agent_obs_export.py` | 把零界既有审计事件导出为 OTel GenAI 形态 JSONL（最小可观测闭环，H0-1）。 |
| `tools/ai_onboard.py` | AI 快速接入台：一条命令给新 AI 生成虚拟 Key + 配置片段 + 注册表。 |
| `tools/alerts_shadow.py` | 8001 策略预警影子服务（alerts_shadow，K-DASH-002 立项恢复实体）。 |
| `tools/annotate_plan_status.py` | 给 docs/plans 计划补一行权威 STATUS（有则跳过）。 |
| `tools/approval_card.py` | S2 审批卡构建（D2-D3，2026-10-30 WO-SUPERVISOR）。 |
| `tools/approval_inbox.py` | approval-inbox：L2 审批队列（本地 JSON，append 决策留痕）。 |
| `tools/approval_rollup.py` | M4 签字汇总（只读 CLI）——2026-10-31 WO-M4-SIGNERMAP-20261031。 |
| `tools/attribution_gap_report.py` | 归因缺口报表（WO-DRILL3-ATTRGAP）——只读统计开发事件层的归因缺失。 |
| `tools/attribution_repair.py` | 归因缺口修复映射（2026-10-29 · 事件层归属率整改）。 |
| `tools/audit_p1_observability.py` | P1 可观测性只读审计。 |
| `tools/audit_strategy_acceptance.py` | 策略分层验收的只读门禁审计。 |
| `tools/audit_task_targets.py` | 逐个验证计划任务的动作目标文件是否真的存在（只读）。 |
| `tools/audit_training_candidates.py` | 只读审计训练候选清单。 |
| `tools/auth_gateway_smoke.py` | Phase A auth-gateway 端到端 smoke（只读自检；不打印密码/token/sid）。 |
| `tools/authority_audit.py` | ARCH1 研发域最高权限审计与看板（2026-10-27 WO-AUTHORITY-C3）。 |
| `tools/automation_supervisor.py` | 自动化监督器 dry-run（D2-D3，2026-10-30 WO-SUPERVISOR）。 |
| `tools/bff_readonly.py` | 零界只读 BFF CLI：不启服务、不写库，直接输出只读视图 JSON。 |
| `tools/browser_verify.py` | browser-verification：Playwright 真浏览器验收（goto/click/fill/expect/screenshot）。 |
| `tools/build_agent_ide_bundle.py` | Agent IDE 注册表聚合器（K-ORG-022）。 |
| `tools/build_performance_ledger.py` | 绩效账本聚合器（K-STOCK-094，K-STOCK-093 第一步的账本雏形）。 |
| `tools/bundle_bootstrap.py` | B5：clean clone/worktree 缺 IDE bundle 时的幂等 bootstrap（K-ORG-20261031-BUNDLEB5）。 |
| `tools/bundle_phase2_dryrun.py` | K-ORG-20261031-BUNDLEP2：bundle Phase2（去 tracked）离线 dry-run 规划器。 |
| `tools/c6_daily_kpi.py` | C6 指标日报生成器（只读、确定性、零 LLM）。 |
| `tools/candidate_evidence_guard.py` | candidate_evidence_guard.py — W3 候选证据包只读校验器（2026-10-31）。 |
| `tools/candidate_generation_guard.py` | candidate_generation_guard.py — 候选池生成层只读门（2026-11-01）。 |
| `tools/cap.py` | 能力令牌 CLI（Phase A）：init / issue / verify。 |
| `tools/change_gate.py` | 评估闸门 v0：prompt/模型变更的可比较、可回滚基线。 |
| `tools/chart_hypothesis.py` | 漏斗层1：图形推演引擎（K-ORG-120 图先行·事佐证） |
| `tools/check_auth_surfaces.py` | 身份入口守卫：只允许 config/auth_surfaces.json 中的 5 个凭证面。 |
| `tools/check_deliverables_parity.py` | deliverables 双根一致性只读护栏（漂移门禁）。 |
| `tools/check_dirty.py` | 脏文件看板/协议守卫（P0 #2，2026-10-28）。 |
| `tools/check_format_args.py` | 格式串占位符 vs 参数数 AST 静态校验（W9 门禁，K-STOCK-393）。 |
| `tools/check_import_boundaries.py` | 导入边界联动检查：业务模块的新增 import 必须仍落在对应单测白名单内。 |
| `tools/check_name_uniqueness.py` | 名称唯一性 CI 规则（P3）——编制内一名一席、治理/虚拟席必须带席位限定。 |
| `tools/check_portal_split.py` | portal 大文件拆分守卫（2026-10-28，守则 v1）。 |
| `tools/check_ports.py` | 端口注册表守卫（K-STOCK-103）：治抢端口/私起服务。 |
| `tools/check_syntax.py` | 语法门：一方文件编译检查（SyntaxError 阻断；SyntaxWarning 可按门禁策略阻断）。 |
| `tools/check_task_artifact.py` | Phase D1：按任务校验 evidence artifact（测试结果 + 证据）。 |
| `tools/chief_patrol.py` | chief_patrol — 田丰监督回路巡检器（K-ORG-026 批E1）。 |
| `tools/cleanup_pytest_tmp.py` | pytest tmp 产物 TTL 清理（K275 根治：C 盘 62G pytest 泄漏） |
| `tools/cn_fut_backtest.py` | cn_fut_backtest: 国内期货主力连续 × 放行策略 walk-forward 回测（F3b，K161-B/K163-C） |
| `tools/cn_fut_vol_backtest.py` | cn_fut_vol_backtest: 国内期货 量能/持仓量维度策略回测（K228，用户「量能的方法」点题）。 |
| `tools/cn_gold_seasonal_tsmom.py` | cn_gold_seasonal_tsmom: 沪金季节性 scout + TSMOM 全史 WF（K229）。 |
| `tools/codebase_intel.py` | codebase-intelligence：repo map / 符号检索 / 引用与影响面（只读，带缓存增量）。 |
| `tools/comm_chain_verify.py` | 开闸后服务侧全链验证（打桩不发飞书，可反复跑）。 |
| `tools/comm_e2e_smoke.py` | 阚泽部全链端到端冒烟（K1-K6，WO-FS-148-2~10）。 |
| `tools/comm_gate_check.py` | 飞书智能层开闸后自动核对（开闸操作单步骤 4 的自动化版）。 |
| `tools/comm_router_smoke.py` | 阚泽部意图路由 smoke + 混淆矩阵（K1.1 验收工具，WO-FS-148-2）。 |
| `tools/composition.py` | 组合清单校验器 / 组合运行时（P1，v0.2）。 |
| `tools/cost_report.py` | — |
| `tools/council_route.py` | council_route.py — 议会厅工作流路由器（2026-10-22）。 |
| `tools/credential_catalog.py` | CLI：统一凭据目录（JEV/数据/飞书等，只读）。 |
| `tools/credential_store.py` | 平台业务凭据库（Windows DPAPI）：Tushare/数据接口等 Key 加密存储。 |
| `tools/d1_calibration.py` | D1 校准门（2026-10-30）：无 Brier/ECE 不谈加人。 |
| `tools/d1_outcome_daily.py` | D1 outcome 回填 + 校准日常任务（幂等、离线可测、**不自动交易**）。 |
| `tools/d1_raw_pit_audit.py` | D1 raw/decision_id/PIT 只读审计（2026-11-01 K-ORG-20261101-D1RAWPIT）。 |
| `tools/d1_rehearsal_precheck.py` | D1 rehearsal 日常预检（17:10，计划 E3）：隔离根跑单票 + 五字段核对 + 校准记录。 |
| `tools/daily_review.py` | 军团每日复盘引擎（K-ORG-122）：每个 Agent 每天回答三问 |
| `tools/daily_subscription_job.py` | 订阅日跑编排入口（飞书中台 P3.9-lite · N-B 工位 · WO-FS-122 · 2026-10-20）。 |
| `tools/daily_three_ledger.py` | 日终三账快照（任务 / 计划 / 证据，只读一条命令）。 |
| `tools/data_lag_audit.py` | 数据对齐审计 + 真补齐（源侧 vs 本地逐日对比）。 |
| `tools/data_sync_manual.py` | 手动同步更新数据（一键 L1 动作）：Tushare 增量 → 六表守护 → 复权 → 港股 → 公告 → 驾驶舱快照。 |
| `tools/datawarehouse/__init__.py` | DATA1 数据仓治理工具包（只读）。 |
| `tools/datawarehouse/ann_history_sync.py` | ann_history 增量回填（DATA1 治理 · L2-2A 批准范围）。 |
| `tools/datawarehouse/backup_receipt_check.py` | 备份回执核对（只读）：最新备份代、产物齐全、异地 receipt、断档小时数。 |
| `tools/datawarehouse/catalog_snapshot.py` | 目录快照：catalog（当前 v12）与来源资格 registry 一致性 + 物理漂移抽样（只读）。 |
| `tools/datawarehouse/common.py` | 数据仓治理工具公共层（只读）。 |
| `tools/datawarehouse/contract_export.py` | 数据契约生成器：catalog v12 六核心表 → ODCS v3 契约（datacontract-cli 可 lint）。 |
| `tools/datawarehouse/dataset_registry.py` | 数据集级统一登记/取数（WP-I1/I3 骨架）。 |
| `tools/datawarehouse/dataset_registry_cli.py` | 统一取数入口 CLI（WP-I3 骨架）：dataset_id 一码查/取。 |
| `tools/datawarehouse/db_health.py` | SQLite 健康检查（A4 制度化）：给 DW 周检提供 quick_check / journal / 空间 / 索引卫生。 |
| `tools/datawarehouse/db_instance_probe.py` | 数据库实例只读探活：TCP 端口（不连库、不用凭据）。 |
| `tools/datawarehouse/etl_dryrun_daily.py` | 清洗 dry-run 批次台账（DATA-05 / DATA2 刘巴 06:40 证据）。 |
| `tools/datawarehouse/freshness_report.py` | 新鲜度报告：六表 prod_max（footer，不凭 mtime）+ 4 库停摆（只读 sqlite）。 |
| `tools/datawarehouse/lifecycle.py` | 资产生命周期覆盖读取（catalog/registry/freshness 共用，fail-open）。 |
| `tools/datawarehouse/lifecycle_coverage.py` | 数据生命周期覆盖率审计（P2-2，2026-10-30）：catalog lifecycle 字段只读盘点。 |
| `tools/datawarehouse/lineage_impact.py` | 血缘影响分析（只读、静态）：catalog 表 → store → alias → 仓内消费方。 |
| `tools/datawarehouse/manifest_check.py` | manifest 覆盖核对：4 个 `_manifest.json` 键 ∩ catalog 表名（只读）。 |
| `tools/datawarehouse/manifest_repair.py` | manifest 修复工具：为 catalog 中 manifest_status=missing/open 的表补齐源清单条目。 |
| `tools/datawarehouse/metadata_consistency.py` | 元数据一致性检查：catalog × registry × data_lifecycle × 物理 store。 |
| `tools/datawarehouse/qualify_probe.py` | 来源资格取证探针（批次 1：tushare 深层表 26 张）。 |
| `tools/datawarehouse/quality_daily.py` | 数据仓质量日报（DATA-06 / DATA3 陈群 07:00 巡检证据）。 |
| `tools/datawarehouse/quality_test.py` | 确定性质量检查（只读）：表存在/空分片/分区重复/行数/主键抽样。 |
| `tools/datawarehouse/registry_rebuild.py` | warehouse_registry v2 重建：按 catalog v12 全量对齐物理表资格台账。 |
| `tools/datawarehouse/run_all.py` | 数据仓日检总入口：串起各只读工具 → 汇总 → 可选写观察记忆。 |
| `tools/datawarehouse/staging_health.py` | staging 生命周期检查增强（2026-10-28）：保留窗口 + 清单未知 + 陈旧锁。 |
| `tools/datawarehouse/tests/test_ann_history_sync.py` | ann_history_sync 纯本地单测（不联网、不碰生产库）。 |
| `tools/datawarehouse/tests/test_contracts.py` | contract_export 纯单测：类型映射/契约构建/check 语义（不读真实数据根）。 |
| `tools/datawarehouse/tests/test_daily_ops.py` | DATA-05/06 日常证据生成器契约测试（写 tmp 不污染主仓）。 |
| `tools/datawarehouse/tests/test_data_catalog.py` | 数据册校验（v2 契约起，当前 v6）：YAML 合法 / 条目数 / id 唯一 / 字段齐全 / 修正项落盘。 |
| `tools/datawarehouse/tests/test_dataset_registry.py` | dataset_registry 契约测试：id 唯一/list/search/get/sample/emit。 |
| `tools/datawarehouse/tests/test_db_health.py` | db_health 单测：用临时 SQLite 验证 ok/warn/fail 三态（不依赖真实业务库）。 |
| `tools/datawarehouse/tests/test_dw_catalog_mcp.py` | dw-catalog MCP 测试：stdio JSON-RPC 真调 + 真实值对账 + 只读性。 |
| `tools/datawarehouse/tests/test_dw_etl_dryrun.py` | dw-etl-dryrun MCP 测试：只返回计划、不写文件。 |
| `tools/datawarehouse/tests/test_dw_quality_mcp.py` | MCP-A dw-quality 测试：stdio JSON-RPC 协议 + 只读工具调用 + 错误路径。 |
| `tools/datawarehouse/tests/test_dw_snapshot_daily.py` | S1-06 dw_snapshot 每日脚本测试：刷新成功路径 + 一致性校验失败路径 + 日志回执。 |
| `tools/datawarehouse/tests/test_dw_wp_i3.py` | WP-I3 测试：HTTP handler（直接调用）+ MCP stdio（子进程）。 |
| `tools/datawarehouse/tests/test_factor_tools_mcp.py` | factor-tools 只读 MCP 测试：stdio JSON-RPC 协议 + 真实产物 + 失败路径（fail-visible）。 |
| `tools/datawarehouse/tests/test_freshness_expectation.py` | freshness 期望模型（freq-aware）单测。 |
| `tools/datawarehouse/tests/test_freshness_parse.py` | freshness_report._parse_day 单测（2026-10-28 BUG 修复回归）。 |
| `tools/datawarehouse/tests/test_kb_corpus_mcp.py` | MCP-D kb-corpus 测试：stdio JSON-RPC（子进程）+ 只读/白名单/错误路径。 |
| `tools/datawarehouse/tests/test_lifecycle.py` | lifecycle 覆盖单测。 |
| `tools/datawarehouse/tests/test_lifecycle_archived.py` | lifecycle 四态（archived）单元测试（2026-10-30 P72）。 |
| `tools/datawarehouse/tests/test_lifecycle_coverage.py` | lifecycle_coverage.py 回归测试（DWH-101，2026-11-02；仅测试，不改实现）。 |
| `tools/datawarehouse/tests/test_manifest_repair.py` | manifest_repair 纯函数单测：扫描/清单路径/合并写入（临时目录夹具）。 |
| `tools/datawarehouse/tests/test_metadata_consistency.py` | metadata_consistency 单测（临时 catalog/registry/lifecycle 夹具）。 |
| `tools/datawarehouse/tests/test_otel_tracing.py` | otel_tracing 纯单测：默认关闭 no-op / 装饰器透传 / fail-open。 |
| `tools/datawarehouse/tests/test_qualify_probe.py` | qualify_probe 单测：纯本地 fixture，不读真实数据仓/不联网/不写 registry。 |
| `tools/datawarehouse/tests/test_staging_health.py` | staging_health 周检单测（临时目录 + 注入体积，避免真造大文件）。 |
| `tools/datawarehouse/tests/test_staging_retention.py` | staging_health 保留窗口/陈旧锁单测（2026-10-28 增强）。 |
| `tools/datawarehouse/tests/test_tools.py` | datawarehouse 工具层单测（纯本地，不依赖网络/真实数据仓/凭据）。 |
| `tools/delivery_ledger.py` | 非研发部门交付台账（周度 CSV）：init / check / summary。 |
| `tools/dep_age_check.py` | 依赖版本年龄检查（O8 P3b，warn-only，只读）。 |
| `tools/dep_audit.py` | 依赖锁校验 + 安全审计周检（P1-3，2026-10-30）。 |
| `tools/dev_acceptance_audit.py` | 实现/验收分离审计（只读）：dev 单是否有独立验收人与验收命令。 |
| `tools/dev_gate_warn_report.py` | 研发试点门（[dev-gate]）告警汇总（WO-DRILL4-GATEWARN · 2026-10-27）。 |
| `tools/dev_legion_smoke.py` | 研发军团作战屏 v1 · 冒烟（LS-10，2026-10-28）。 |
| `tools/dev_metrics_daily.py` | C6 每日指标（2026-10-27 WO-P0-DEV8-METRICS）。 |
| `tools/dev_orchestrator.py` | 研发编排状态机 MVP（O1 · 2026-10-27 WO-HARNESS-O12）。 |
| `tools/dev_route_audit.py` | 研发派工路由审计（只读工具，WO-DRILL1-ROUTEAUDIT）。 |
| `tools/dev_weekly_report.py` | 研发部周报生成器（C5/C6 交付物 · 2026-10-27 WO-LEGION-P0WRAP）。 |
| `tools/devtask.py` | devtask — 多 Agent 协作开发调度器（K-STOCK-104 方案丙）。 |
| `tools/devtask_audit.py` | — |
| `tools/diagnostics.py` | O7 Diagnostics：一键生成脱敏诊断包（zip）。 |
| `tools/disk_ttl_cleanup.py` | disk_ttl_cleanup.py — 运行产物磁盘 TTL 清理（默认 dry-run） |
| `tools/download_dflash2_hybrid.py` | DFlash2 模型多连接下载器（ModelScope 匿名限速 ~1MB/s，多连接可叠加提速） |
| `tools/durable_workflow.py` | durable-workflow 最小版：持久步骤状态机（断点续跑/超时/重试）。 |
| `tools/dw_dashboard.py` | 数据仓驾驶舱快照生成器 v2：KPI 下钻 + 来源详情 + 今日动态 + 挖掘时间线 + 治理路线。 |
| `tools/dw_snapshot_daily.py` | dw_snapshot 每日刷新 + 一致性校验（《数据仓研发计划 v3》S1-06）。 |
| `tools/e2e_pipeline.py` | 端到端全自动工单驱动器 v2（K-ORG-065 · 055 计划 11-08 里程碑彩排）。 |
| `tools/e2e_report.py` | — |
| `tools/e2e_run.py` | E2E 运行器（O5）：跑 tests/e2e 首批冒烟场景，产 artifact。 |
| `tools/entry_archive_scan.py` | 入口归档扫描 v2（P1-5，2026-10-30）：引用链证据（代码/配置，排除文档）。 |
| `tools/escort_premonday_check.py` | 周一护航只读预检（escort-premonday-check）。 |
| `tools/eval_harness.py` | eval-harness：10 条金标任务 + 断言评分 + 回归报告。 |
| `tools/event_emit.py` | R6 产线侧事件上报配方（emit_event 一段函数，设计稿 §5 镜像件）。 |
| `tools/events.py` | 零界开发进度事件层（K-STOCK-113）——开发侧全量自动回流平台的数据地基。 |
| `tools/evidence_chain_audit.py` | P0 evidence 链式审计器（evidence_chain_audit，只读 / 默认不落盘）。 |
| `tools/evolve_sign_payloads.py` | Z4 判例卡待签 payload 生成器（只生成 payload，不签名、不落库）。 |
| `tools/execution_watchdog.py` | R3 失败必响巡检（联动层一期 T3）：计划任务「AI工作站执行失败巡检」动作体。 |
| `tools/export_claude_agents.py` | K-ORG-036 F6: 27 在编岗人设 → Claude Code --agents JSON 导出（子代理化）。 |
| `tools/export_nav.py` | 前端入口统一治理（K-STOCK-100）· 第一件：导航单一源导出器。 |
| `tools/export_page_linkage.py` | export_page_linkage.py — 从导航注册表生成「全量前端页面联动矩阵」。 |
| `tools/extension_lint.py` | 扩展 manifest 校验器（O3）：只读校验 extensions/*/manifest.json。 |
| `tools/extension_manifest_audit.py` | O3-P2 对账：扩展 manifest ↔ 运行配置（servers.json / tool entry）只读差异。 |
| `tools/feishu_preflight_backup.py` | 飞书中台开闸/重启前备份工具（checklist 32 缺口补齐）。 |
| `tools/file_lock.py` | 通用跨进程文件锁（默认安全：同刻只允许一个持有者）。 |
| `tools/fin_report_sync.py` | 财报数据补齐（按报告期）：拉 Tushare *_vip → 合并进按股票 parquet（增量、去重、可重拉）。 |
| `tools/flaky_probe.py` | flaky 复验探针（2026-10-29，关羽）。 |
| `tools/full_universe_baseline.py` | 全量选股 Phase A 只读基线（WO-STRAT-FULL-BASE-20261102）。 |
| `tools/fut_backtest.py` | fut_backtest: 期货量化分析层 F3（K161-B）——核心池 9 策略回测对照。 |
| `tools/fut_scan.py` | fut_scan: 国际期货扫描快照 + 核心宏观池行情（tradingview-mcp F1+F2，K161-C） |
| `tools/fut_walkforward.py` | fut_walkforward: 核心宏观池 × 9 策略 walk-forward 样本外验证（tradingview-mcp F3 严格试测） |
| `tools/fx_fill.py` | 汇率数据补齐（换源）：东方财富日线 → data/tushare/fx_daily/。 |
| `tools/gaorta_models.py` | K-STOCK-137 S-a · gaorta 模型目录生成器 |
| `tools/gate3_verdict.py` | 门 3 独立复核工具（第二意见）——只读，不写任何文件。 |
| `tools/gen_bff_score_distribution.py` | 生成菜鸟评分快照历史分布（只读）。 |
| `tools/gen_council_data.py` | gen_council_data.py — 会议厅数据生成器（S-1b，2026-10-22）。 |
| `tools/gen_data_manifest.py` | gen_data_manifest.py — 数据根轻量清单（只读，不复制任何数据）。 |
| `tools/gen_docs_index.py` | DOCS_INDEX.md 的生成与校验工具。 |
| `tools/gen_entrypoints.py` | 脚本入口登记表生成器（2026-10-28）。 |
| `tools/gen_health_card.py` | gen_health_card.py — 治理健康卡（只读，零第三方依赖）。 |
| `tools/gen_task_fixes.py` | 为 7 个"动作起不来"的计划任务生成修正版 XML（只读线上定义，只写工作区）。 |
| `tools/gen_tianfeng_attr.py` | 生成田丰（A0）属性页：legion_command/static/tianfeng.html。 |
| `tools/git_worktree_guard.py` | 主仓 core.bare 自愈守卫（2026-10-31 事故 · 关羽 P110）。 |
| `tools/glm_retry_probe.py` | glm_retry_probe.py — GLM-5.3 额度探测（两阶段，外发最小化；审计 B M-03/S-01 收口）。 |
| `tools/gov_ai_case_bridge.py` | GOVAI-3：AI 判例草稿 → Z4 待签 payload → 验签入库（人类签字门）。 |
| `tools/h1b_data_adapter.py` | H1b 数据适配层：tdx/tushare（parquet）或 CSV 合成夹具 → panel/events JSON。 |
| `tools/hermes_acad1_build.py` | Hermes ACAD1（司马徽 / Zhice 研究台）扩展构建流水线 · P2-3 |
| `tools/hk_connect_sync.py` | H17 数据补齐：港股通（南向）资金流 + 港股通持股明细。 |
| `tools/hk_daily_backfill.py` | 回补 hk_daily 2021-01-01 ~ 2023-09-15（tushare hk_daily 按交易日）。 |
| `tools/hk_r1_daily.py` | HK R1 两周观察 · 每日跑批（默认 dry-run；--append 才执行并追加台账）。 |
| `tools/huangzhong_1104_finalize.py` | 黄忠 11-04 终版回执生成器 v1（只读汇总 + 写终版回执模板）。 |
| `tools/huangzhong_1104_preflight.py` | 黄忠 11-04 复核 Preflight 一键检查（只读编排）。 |
| `tools/huangzhong_1104_receipt.py` | 黄忠 11-04 观察期复核回执自动生成器 v1（只读编排，草案非最终回执）。 |
| `tools/huangzhong_1104_run.py` | huangzhong_1104_run.py — 11-04 复核一键执行器（只读编排，v1）。 |
| `tools/huangzhong_accept.py` | 黄忠 · 验收守门官 v1（观察模式，**不阻断 merge**）。 |
| `tools/huangzhong_blind_audit.py` | 黄忠盲审抽样包生成器（P3-C，2026-11-01，只读生成，不发送/不通知）。 |
| `tools/huangzhong_blocking_sim.py` | 黄忠阻断分级模拟（P3-D · WO-HZ-BLOCKSIM-20261101，只读）。 |
| `tools/huangzhong_brief.py` | 黄忠每日简报 v1（只读汇总；**不自动推送**）。 |
| `tools/huangzhong_cert_lifecycle.py` | 黄忠认证生命周期 dry-run v1（只读编排；**不创建任务、不发消息、不改配置**）。 |
| `tools/huangzhong_corpus.py` | 黄忠验收语料库与一致性基准 v1（只读；不写 work/）。 |
| `tools/huangzhong_coverage_trend.py` | 黄忠覆盖率趋势与 11-04 投影（只读，纯 stdlib）。 |
| `tools/huangzhong_daily.py` | 黄忠每日验收观察（观察模式）：跑验收单 → 记 daily.jsonl → 周报指标。 |
| `tools/huangzhong_defect_classify.py` | 黄忠缺陷分类与周报（taxonomy v1，只读）：REJECT/OBSERVE 原因归类 + 周聚合。 |
| `tools/huangzhong_drill.py` | 黄忠受控演练 helper（复演/复证；**默认 dry-run**，--apply 才写盘）。 |
| `tools/huangzhong_evidence_lint.py` | 黄忠验收单证据 schema lint（P8，2026-10-31，**只读**）。 |
| `tools/huangzhong_evidence_refresh.py` | 黄忠验收证据每日刷新（L2 计划任务 HuangzhongEvidenceRefresh 的执行体）。 |
| `tools/huangzhong_go_no_go.py` | 黄忠 2026-11-04 Go/No-Go 决策工具（只读）。 |
| `tools/huangzhong_grade.py` | 黄忠 W1 四件套自动批改（Rubric 100 分，≥80 通过）。 |
| `tools/huangzhong_health.py` | 黄忠工具链自监控心跳（只读；默认不写盘）。 |
| `tools/huangzhong_health_alert.py` | 黄忠健康告警联动（只读评估，**默认 dry-run**；--apply 才写 flag/state）。 |
| `tools/huangzhong_impact.py` | 黄忠变更影响面验证工具（P0-2 / P1-D，2026-10-31，**只读**）。 |
| `tools/huangzhong_incidents.py` | 黄忠事故台账 CLI（append-only）：add / list / verify / stats。 |
| `tools/huangzhong_ops.py` | 黄忠运维 CLI（P2，2026-10-31）：B 飞书推送开关管理 + 事件统计/轮转。 |
| `tools/huangzhong_peer_verify.py` | 黄忠跨 Agent 互验报告（只读）：黄忠验收 vs 赵云抽查 vs 关羽修复批次。 |
| `tools/huangzhong_perf_signal.py` | 黄忠性能/SLO 信号（只读）：关键端口/页面延迟抽样，纳入验收证据面。 |
| `tools/huangzhong_policy.py` | 黄忠策略即代码（P1-A · WO-HZ-POLICY-AS-CODE-20261031，只读）。 |
| `tools/huangzhong_review.py` | 黄忠认证 v2 六条一键复核（只读）。 |
| `tools/huangzhong_risk_coverage.py` | 黄忠风险加权覆盖率 2.0（只读，纯 stdlib）。 |
| `tools/huangzhong_security_signal.py` | 黄忠安全扫描信号（P2-E · WO-HZ-SEC-20261031，只读）。 |
| `tools/idem_replay_verify.py` | M0 P4 事件回放验证（只读）。 |
| `tools/identity_context.py` | P3 IdentityContext CLI（只读）：列出/解析零界·智策·菜鸟 actor 映射。 |
| `tools/index_governance_assets.py` | 全量计划 + 仓外交付物索引（2026-10-31 治理收口；K-ORG-20261031-PLANREG 修生成物分类）。 |
| `tools/init_subscription_db.py` | 订阅账本离线初始化工具（飞书中台 P3.8-lite · M-C / WO-FS-113）。 |
| `tools/inject_os_tools.py` | 给 /os-board、量化/情报域页注入通用页面工具（搜索/折叠）。 |
| `tools/inspect_format_mismatch.py` | 对 check_format_args 的 MISMATCH 逐个打印完整源码段，人工定真假。 |
| `tools/intel_ai_first_pass.py` | N1 AI 初筛写入器（WO-INTEL-AI1ST-20261031）。 |
| `tools/intel_batch_preflight.py` | 人工复核批次入库前质量闸（WO-INTEL-COMPANIES-AUDIT / P0）。 |
| `tools/intel_human_batch_apply.py` | 人工复核批次校验/回填器（WO-INTEL-HUMANAPPLY-20261031）。 |
| `tools/intel_pre_review.py` | 情报机器预审 200/日（只读建议，WO-INTEL-MACHINE-20261031）。 |
| `tools/intel_review_archive.py` | 情报复核队列归档：pending 预算分层 → unreviewed_legacy（WO-INTEL-REVIEW-20261031）。 |
| `tools/intel_review_batch.py` | 情报 50 条人工复核批次生成器（只读；WO-INTEL-REVIEWBATCH-20261031）。 |
| `tools/intel_source_health.py` | 情报来源健康检查（只读，WO-INTEL-HEALTH-20261031 / I1）。 |
| `tools/intel_sources.py` | 情报来源登记表只读查询 + 已有落盘样本采样（Phase 2，WO-INTEL-SOURCES-20261031）。 |
| `tools/invest_staff_audit.py` | 鲁肃军团（量化交易部）人员就位/模型/能力配置审计（2026-10-30）。 |
| `tools/jev_audit.py` | JEV 判读账本 + 后验回填 + 校准报告（K-DATA-20261026-JEVAUDIT，P1-lite）。 |
| `tools/jev_bench.py` | jev_bench.py — JEV 两段式推理 A/B 基准（K-ORG-20261026-JEV2STAGE）。 |
| `tools/jev_companies_audit.py` | JEV companies 质量门（WO-INTEL-COMPANIES-20261031）。 |
| `tools/jev_eval_harness.py` | JEV-Eval 评测门（2026-10-30 JEVEVAL，离线只读，CI 可用）。 |
| `tools/jev_mcp.py` | JEV 只读 MCP Server（stdio）——把 JEV 判定查询/校准状态暴露给 Hermes agent。 |
| `tools/jev_night.py` | jev_night.py —— 夜间 JEV 独占跑批（22:00 暂停 M3 → 07:00 恢复）。 |
| `tools/jev_night_preflight.py` | JEV 夜跑前置检查（2026-10-30 PREFLIGHT，只读；--warm 可选预热 9B）。 |
| `tools/jev_night_summary.py` | 夜跑轮次摘要（2026-10-30 NIGHTSUM，只读日志）。 |
| `tools/jev_pending.py` | Print current JEV pending count (single integer) for wrappers/scripts (2026-10-29). |
| `tools/jev_posterior_poc.py` | JEV 后验校准 POC v2（K-DATA-20261026-JEVCAL，JEV × yz-substrate P0）。 |
| `tools/jev_prefilter_eval.py` | prefilter 灰度评估（2026-10-30 PREFILTEREVAL，只读 news.db）。 |
| `tools/jev_prefilter_revert.py` | 回退预筛写入的 JEV 行（2026-10-30 BATCH4，K-ORG-20261030-BATCH4）。 |
| `tools/jev_review_loop.py` | 低置信 review 闭环（2026-10-30 JEVREVIEWLOOP，只读 news.db / 本地 JSONL）。 |
| `tools/jev_risk_alert.py` | JEV 风险告警桥（2026-10-30 JEVALERT，只读 news.db + 本地去重状态）。 |
| `tools/jev_roles_daily.py` | jev_roles_daily.py — JEV 三席「日产出物」生成器（K-ORG-20261026-JEVROLE）。 |
| `tools/kb_corpus/batch_ingest.py` | 批量入库驱动：逐文件子进程隔离（防原生崩溃拖垮整批），可续跑。 |
| `tools/kb_corpus/build_full_index.py` | 扩索引 · 将 P4-lite 从 pilot100 扩到 core_quant 过滤后的全量视频。 |
| `tools/kb_corpus/build_rag_index.py` | 接入 AI/111111 RAG：从语料生成 markdown 文档 → KnowledgeBase 建索引 → 验证问答。 |
| `tools/kb_corpus/card_from_units.py` | 知识单元草稿 → 学习卡候选（S5 自动化，task-130）。 |
| `tools/kb_corpus/card_merge.py` | 学习卡合并工具（默认 dry-run；--apply 需人工审核勾选后使用）。 |
| `tools/kb_corpus/card_precheck.py` | 学习卡机器预审建议（60 候选 → pass/revise/reject + 理由 + 置信），**仅辅助、不 apply**。 |
| `tools/kb_corpus/card_precheck_v3.py` | 第二轮 40 候选机器预审（wave27 CU）：复用 card_precheck 规则，补齐审核页第二轮预填。 |
| `tools/kb_corpus/card_proposal.py` | 建议学习卡审核稿生成器（wave10 缺口建议 → pending_review 学习卡）。 |
| `tools/kb_corpus/card_proposal_v2.py` | 零卡主题概念卡候选（wave10 AB 的 14 个 0 卡主题 → pending_review 概念卡）。 |
| `tools/kb_corpus/card_proposal_v3.py` | 学习卡第二轮扩卡（wave25 CO）：70 张卡覆盖缺口 → 30-40 候选卡。 |
| `tools/kb_corpus/card_proposal_v4.py` | 学习卡第三轮扩卡候选（task-126）：124 卡 21 主题深度分析 → <3 卡主题缺口 → 20-30 候选。 |
| `tools/kb_corpus/card_quality_audit.py` | 105 张学习卡质量复核（只读审计，**不修改学习卡**）。 |
| `tools/kb_corpus/card_review.py` | 学习卡审核工作流（60 候选 → 统一清单 → decisions → 合并预览/落盘）。 |
| `tools/kb_corpus/card_revision.py` | 学习卡 revise 24 张修订建议稿（**仅建议，不改 checklist/学习卡**）。 |
| `tools/kb_corpus/card_revision_apply.py` | 学习卡修订落地（wave24 CL）：把 wave23 CJ 的 merge_fix 14 / drop 10 执行到学习卡。 |
| `tools/kb_corpus/card_revision_round2.py` | 第二轮 45 张 revise 修订建议稿（**仅建议，不执行、不改学习卡/清单**）。 |
| `tools/kb_corpus/citation_retry_scale.py` | citation_retry 扩样 A/B（30 条，决定是否把 citation_retry 切为默认）。 |
| `tools/kb_corpus/concept_dedup.py` | 同 concept 重复知识单元审计（只审计不改数据）。 |
| `tools/kb_corpus/daily_briefing.py` | 每日研报简报生成器：从研报知识库取最新内容，生成简报 md + JSON（供飞书/门户推送）。 |
| `tools/kb_corpus/daily_kb.py` | 知识库每日自动化编排器（daily_kb）：按序刷新学习卡/研报面板/入库状态（可选问答检索冒烟）。 |
| `tools/kb_corpus/distill_remaining.py` | 剩余研报知识单元蒸馏（跳过已蒸馏），后台批跑；失败记录不中断。 |
| `tools/kb_corpus/eval_audit.py` | 检索评测两阶段偏差审计（wave11 支线 AF）：wave9（41 查询）vs wave10（89 查询）。 |
| `tools/kb_corpus/eval_registry.py` | 检索评测集资产化管理（wave14 支线 AP）：把冻结评测集导出为带版本 + sha256 的标准资产。 |
| `tools/kb_corpus/eval_report_header.py` | 评测报告头工具：统一从「冻结评测集资产」生成报告头部（set_id/content_hash/annotation_policy）。 |
| `tools/kb_corpus/eval_search_v2_ext.py` | 检索 v2 评测扩展（wave9 支线 X）：40 条人工标注查询，v1 vs v2 统计性对比。 |
| `tools/kb_corpus/eval_search_v2_ext2.py` | 检索 v2 评测第二阶段（wave10 支线 AA）：**89 条查询 + 分级标注 + NDCG@5 + bootstrap CI**。 |
| `tools/kb_corpus/eval_set_v3.py` | 评测集 v3（wave19 支线 BG）：补齐 merged-concept 查询 + 合并复评。 |
| `tools/kb_corpus/eval_v3_baseline.py` | 评测基线 v3 全量回归（wave21 支线 CF）：当前默认 search_v2（soft + synonym 0.6）vs v1。 |
| `tools/kb_corpus/export.py` | P1 · 语料规范化导出：video_channel_map / videos / comments。 |
| `tools/kb_corpus/golden_spotcheck.py` | 知识单元 golden 抽检：schema / 页码 / 章节 / 证据接地率。 |
| `tools/kb_corpus/gov_status.py` | 语料库治理状态 → 门户静态 JSON（零重启方案，2026-10-30）。 |
| `tools/kb_corpus/guard_threshold_ab.py` | guard 阈值 0.6 vs 0.7 扩样 A/B：同一批 multi 答案离线重算两种阈值的 guard 结果。 |
| `tools/kb_corpus/guard_threshold_study.py` | guard 阈值敏感性研究：inference_ratio 阈值 × 最小被引单元数 的通过率/质量权衡。 |
| `tools/kb_corpus/kb_daily_push.py` | 知识库每日推送准备（飞书交互卡片 payload，dry-run，不发送、不联网）。 |
| `tools/kb_corpus/kb_ingest_status.py` | PDF 知识入库状态 → 门户静态 JSON（零重启方案）。 |
| `tools/kb_corpus/kb_qa.py` | 研报知识库 RAG 问答（检索融合 + 本地 LLM 生成 + 引用/置信说明）。 |
| `tools/kb_corpus/kb_qa_bank.py` | 问答候选问题库：从知识单元批量生成 20–40 条候选问题（默认不调 LLM）。 |
| `tools/kb_corpus/kb_qa_bank_v2.py` | 问答问题模板 v2：7 类模板生成 40–60 条候选问题 + retrieval-only 自检 + 可答率验证。 |
| `tools/kb_corpus/kb_qa_bank_v3.py` | 问答问题库 v3：claim 覆盖度过滤 + 可答率扩验（12 条）+ 与 v2 对比。 |
| `tools/kb_corpus/kb_qa_eval.py` | 问答答案质量评估（wave16 支线 AU）：引用校验 + 支撑度分级 + 幻觉启发式（不调 LLM）。 |
| `tools/kb_corpus/kb_qa_eval_v2.py` | 问答答案质量评估 v2（wave17 支线 AX）：schema v2 + 幻觉规则调优（不调 LLM）。 |
| `tools/kb_corpus/kb_qa_expand.py` | 问答样本扩量：32 问题库 → 10–15 条新样本（逐条 kb_qa.ask，串行+降级），合并原 5 条成 15–20 条。 |
| `tools/kb_corpus/kb_qa_guard.py` | multi 答案质量守卫（P0）：引用可解析硬门 + 推断比例限制 + 被引单元集中度。 |
| `tools/kb_corpus/kb_qa_multi.py` | 多单元聚合回答 MVP：检索 top-k 单元 → 聚类 → 多单元聚合 prompt（允许综合推断）→ 与 v3 单单元对比。 |
| `tools/kb_corpus/kb_qa_multi_ab.py` | 多单元聚合扩样 A/B（single vs multi）：决定 multi 是否设为默认问答模式。 |
| `tools/kb_corpus/kb_qa_multi_ab50.py` | multi 扩样 50 条复跑：v3 全 50 题 single vs multi，复用已有 single/multi 基线。 |
| `tools/kb_corpus/kb_qa_multi_eval.py` | multi 答案质量评估（wave18 支线 BB）：single vs multi 的引用/支撑/幻觉对比（不调 LLM）。 |
| `tools/kb_corpus/kb_qa_prompt_v2.py` | multi 答案质量提升：prompt v2 A/B（独立实验，不改变现有 kb_qa/kb_qa_multi 行为）。 |
| `tools/kb_corpus/kb_qa_prompt_v3.py` | prompt v3 调优 A/B：先「事实摘录」后「推断（≤2 条且引用线索）」+ few-shot 示例。 |
| `tools/kb_corpus/kb_qa_prompt_v3_35b.py` | 35b 模型 prompt v3 A/B：验证「模型能力是否 v3 失败瓶颈」。 |
| `tools/kb_corpus/kb_qa_prompt_v4.py` | prompt v4 + guard 分段解析（segment）A/B：治「事实行未标【事实】」根因。 |
| `tools/kb_corpus/kb_qa_prompt_v4_scale.py` | prompt v4 扩样验证（30-40 条 A/B）：决定是否把 v4+segment 切为默认。 |
| `tools/kb_corpus/kb_qa_rank.py` | 问答检索 @1 排序优化：对 kb_qa 检索 top-k 做后处理重排（不改 kb_qa.py、不调 LLM）。 |
| `tools/kb_corpus/kb_qa_samples_refresh.py` | 门户问答样例刷新到 v4 默认（task-112）。 |
| `tools/kb_corpus/kb_qa_schema_v2.py` | 问答样本 schema v2（wave17 支线 AX）：字段定义、从 v1 样本推断迁移、备份与校验。 |
| `tools/kb_corpus/kb_qa_selfcheck.py` | 问答问题库 retrieval-only 自检：命中率@1/@5、无命中与缺口归因、按主题覆盖统计。 |
| `tools/kb_corpus/kb_qa_v4_default_recheck.py` | v4 默认复评（灰度稳定性）：用当前 CLI 默认（prompt v4 + guard segment）实跑 20 条复评。 |
| `tools/kb_corpus/kb_read_api.py` | 知识库只读接口 · 参考实现（CLI/JSON，供 Agent 消费）。 |
| `tools/kb_corpus/kb_reports_gov.py` | 研报知识库治理状态 → 门户静态 JSON（零重启）。 |
| `tools/kb_corpus/kb_reports_search.py` | 研报知识单元检索（Agent 工具，只读）：知识单元 + RAG 记录双通道。 |
| `tools/kb_corpus/kb_units_snapshot.py` | 知识单元全量快照：书籍/研报/策略经典 三源 → portal/static/kb_units_all.json（页面检索用）。 |
| `tools/kb_corpus/learning_analysis.py` | 学习卡质量与主题覆盖分析（缺口清单）生成器。 |
| `tools/kb_corpus/learning_card_push.py` | 学习卡每日推送（默认 dry-run，--send 走既有飞书通道）。 |
| `tools/kb_corpus/learning_cards.py` | 知识单元 → 人类学习卡（Q/A + 出处页码）。 |
| `tools/kb_corpus/learning_dashboard_build.py` | 学习项目看板数据聚合器：registry + learning_* 报告 + 知识库统计 → 静态看板 JSON。 |
| `tools/kb_corpus/learning_digest.py` | 学习卡/简报/策略结论 → 门户每日学习摘要（learning digest）生成器。 |
| `tools/kb_corpus/learning_intake.py` | 学习项目入库工具（原文件/Word/PDF/文本）——只写学习命名空间，绝不触碰数据仓。 |
| `tools/kb_corpus/learning_intake_watch.py` | 学习项目入库监听（SOP S0/S1 自动化脚手架）。 |
| `tools/kb_corpus/learning_ops.py` | 学习流水线运维：pipeline（S2-S5 草稿）/ governance（审计）/ standardize（规范）。 |
| `tools/kb_corpus/lineage_write.py` | 语料库血缘写入 quant-pg lineage（job_run + lineage_edge），幂等。 |
| `tools/kb_corpus/llm_rerank_eval.py` | Phase 4 增强验证：LLM rerank（无模型下载）对 Recall@5 的增益（抽样 12 条）。 |
| `tools/kb_corpus/merge_full_index.py` | 合并扩索引 shards → packed_full（门户/验证可用）。 |
| `tools/kb_corpus/merge_sim.py` | concept 合并影响模拟（只在内存/临时副本合并，绝不写真实数据）。 |
| `tools/kb_corpus/merge_sim_89.py` | 合并模拟扩展：冻结 89 查询评测集（graded）上的 concept 合并影响（内存，不改真实数据）。 |
| `tools/kb_corpus/morning_brief_push.py` | 零界知识早报：学习卡(5张) + H18 预告修订观察(前8条) 合并为一条早报（默认 dry-run）。 |
| `tools/kb_corpus/onnx_rerank_eval.py` | bge-reranker-base（ONNX）cross-encoder 重排评测（golden 50）。 |
| `tools/kb_corpus/p2_clean.py` | P2 · 评论清洗：去重（精确+近重复）、PII 脱敏、噪声过滤。 |
| `tools/kb_corpus/p3_distill.py` | P3b · 字幕 → 知识单元蒸馏（本地 qwen3.5，think=false）。 |
| `tools/kb_corpus/p3_fetch_subs.py` | P3a · 试点视频字幕抓取（yt-dlp，网络可用时执行）。 |
| `tools/kb_corpus/p3_probe.py` | Phase 3 网络探测：YouTube 可达则自动跑试点转写+蒸馏。 |
| `tools/kb_corpus/p3_select.py` | P3a · 试点选题（内容相关性过滤版）。 |
| `tools/kb_corpus/pack_index.py` | 把 rag_index_corpus.json 打包成门户 handler 可直接加载的二进制（无 numpy 依赖）。 |
| `tools/kb_corpus/pdf_ingest.py` | PDF 知识入库 · 标准能力 CLI（2026-10-31，工单 WO-PDF-INGEST-20261031）。 |
| `tools/kb_corpus/pdf_layout.py` | MinerU-lite · 布局感知 PDF 解析（PyMuPDF，无需 torch/新依赖）。 |
| `tools/kb_corpus/pdf_ocr.py` | OCR 扫描版 PDF（C:/项目/PDF）：PyMuPDF 渲染 + RapidOCR(ONNX)。 |
| `tools/kb_corpus/phase0_manifest.py` | Phase 0 · corpus_manifest.json + 交付报告对账（P0 交付物补齐）。 |
| `tools/kb_corpus/phase1_dq.py` | Phase 1 · 数据质量检查（DQ）+ Phase 2 · MinHash 近似去重抽样。 |
| `tools/kb_corpus/phase5_faithfulness_v2.py` | 修正 faithfulness 判分（三档：1/0.5/0），重测 15 条 golden，与 phase5_ragas_lite 合并。 |
| `tools/kb_corpus/phase5_full_eval.py` | Phase 5 · Golden 扩到 50 + 生成式引用正确率抽检。 |
| `tools/kb_corpus/phase5_ragas_lite.py` | Phase 5 · RAGAS 风格评测（免安装：本地 qwen3.5 作 judge）。 |
| `tools/kb_corpus/pipeline_check.py` | 知识全链路检查器：按标准 yaml 校验「入库产物」是否达标，并输出下一步 SOP 清单。 |
| `tools/kb_corpus/qa_acceptance.py` | 问答链路端到端验收（CLI / HTTP 预接线 / 门户）：三段验收 + 上线结论。 |
| `tools/kb_corpus/qa_channel_capacity.py` | 通道容量/限流语义复核（task-122）：mock 慢 LLM，真实 LLM 0 次。 |
| `tools/kb_corpus/qa_channel_limit.py` | single/multi 通道限流复核（task-119）：6/分窗口 vs 真实 LLM 延迟。 |
| `tools/kb_corpus/qa_http_live_acceptance.py` | 8091 问答 HTTP 端到端上线验收（真实 HTTP，不再进程内 mock）。 |
| `tools/kb_corpus/qa_http_loadtest.py` | 8091 问答 HTTP 限流/并发/长稳压测（retrieval 为主，控制 LLM 成本）。 |
| `tools/kb_corpus/qa_http_recheck.py` | 8091 问答验收复跑（限流 300/分 + 检索缓存优化后，2026-11-02）。 |
| `tools/kb_corpus/qa_live_quality_review.py` | 8091 线上 multi 质量抽检（真实 HTTP，非离线）：3 条 + 人工可审清单。 |
| `tools/kb_corpus/qa_quality_daily.py` | 问答质量每日抽检 + 漂移告警（真实 HTTP multi 1-2 条/日，低成本）。 |
| `tools/kb_corpus/rag_eval.py` | Phase 4 · 混合检索（向量 + BM25 融合 + 可选 LLM rerank）+ Phase 5 · Golden 评测。 |
| `tools/kb_corpus/rag_lite.py` | P4-lite · 语料本地检索问答（标题+描述+评论，bge-m3 + qwen3.5）。 |
| `tools/kb_corpus/review_sheet.py` | 知识单元人工复核表生成器：随机抽样 N 条，附原页文本摘录，供人工对照。 |
| `tools/kb_corpus/safe_json.py` | 静态产物 JSON 安全写入工具（中风险批次 · wave28 CX）。 |
| `tools/kb_corpus/search_v2.py` | 研报/知识单元检索 v2：多字段加权 + 同义词扩展 + 短语/AND/OR + 分组 + 小评测。 |
| `tools/kb_corpus/search_v2b.py` | 检索 v2b：软 AND + 同义词低权重实验（修复 wave10 的 AND 类回退）。 |
| `tools/kb_corpus/static_writer_audit.py` | 静态产物写入脚本审计（wave26 支线 CR）：防「并发会话覆盖门户静态 JSON」事故再发。 |
| `tools/kb_corpus/to_parquet.py` | P1b · JSONL → Parquet（videos / comments / map）。 |
| `tools/kb_corpus/validate_full.py` | 全量 packed 索引检索验证（bge-m3 + 余弦 top5 + 可选 qwen 生成）。 |
| `tools/kb_corpus/verify.py` | P0 · YouTube 量化语料交付包完整性核验。 |
| `tools/kb_corpus/weekly_eval_regression.py` | 每周评测回归（task-125）：v3 冻结基线漂移监控（不调 LLM）。 |
| `tools/kb_corpus/yt_commons_match.py` | 可行性验证：YouTube-Commons（PleIAs，HF 镜像）与本语料 video_id 交叉匹配。 |
| `tools/kb_corpus/yt_commons_scan.py` | YouTube-Commons 多 shard 命中率抽样（判断 Phase3 解阻可行性）。 |
| `tools/kernel_guard.py` | 内核守卫（P1 MVP）：内核契约文件改动必须带 ADR 号。 |
| `tools/killsvc_guard.py` | Hermes8787-KillSvc Gate 0 前置逻辑层（R7，纯逻辑，零注册零执行零杀进程）。 |
| `tools/knowledge_distill.py` | 知识提炼器（LCS-2 知识环）：让 AI 越来越聪明的飞轮 |
| `tools/l1_acceptance.py` | L1 单栈验收（ADR-016）v1。 |
| `tools/lcs1_h1b_harness.py` | LCS-1 H1b 回测骨架（预注册 v1，2026-11-01；**不跑真实回测**）。 |
| `tools/legacy_scan.py` | 历史代码引用链扫描（P0-2，2026-10-30）：为 legacy_registry 提供证据。 |
| `tools/legion_debate.py` | 军团分析 AI · LCS-1 全链固化版（K-ORG-120/LCS-1 判例产物） |
| `tools/legion_heartbeat_bridge.py` | 军团心跳桥接器（K-ORG-190 拍板⑤ 立项产物）。 |
| `tools/legion_meeting.py` | 军团晨会引擎 + 知识提炼器（LCS-2 四制一环） |
| `tools/legion_meeting_room.py` | 议事厅（G2）：多轮会议消息 + 版本化白板的持久化。 |
| `tools/legion_probe.py` | 军团 API 只读探针（直连 8096；不经 nginx /legion-api）。 |
| `tools/legion_pulse.py` | 军团心跳模块（K-ORG-121 L1 · observe 账本补账） |
| `tools/legion_readiness.py` | 曹操军团实战就绪审计（只读，2026-10-29 WO-LEGION-READY）。 |
| `tools/legion_scheduler.py` | 军团排程调度器（K-ORG-122）：事先调度的机器化 |
| `tools/legion_tri_party_meeting.py` | 三方会议阶段一：结构化会议、双观点、分歧与人工确认。 |
| `tools/legion_worker.py` | 数字军团 Agent Worker（K-ORG-123）。 |
| `tools/litellm_route.py` | LiteLLM 路由最小接入：Key 池 → Router（多 Key/多 provider/fallback/成本）。 |
| `tools/log_rotate.py` | 日志轮转工具 v0（只读演示默认，--apply 才动手；不删除，仅归档+截断）。 |
| `tools/m1_evidence_pack.py` | M1 信任链证据包聚合器（M1 evidence pack，只读一条命令）。 |
| `tools/m4_signoff_status.py` | M4 签收状态只读看板（工具，2026-10-31 WO-M4-SIGNOFF-STATUS-20261031）。 |
| `tools/mcp_ro_filesystem.py` | MCP 只读文件 server：list_dir / read_file / stat（根目录白名单，防路径穿越）。 |
| `tools/mcp_ro_git.py` | MCP 只读 git server：status / log / diff（固定参数，无 shell，仓库白名单）。 |
| `tools/mcp_ro_zhaoyun.py` | 赵云 MCP 只读工具 server（stdlib，复用 zhuge_mcp_server 的只读数据函数）。 |
| `tools/mcp_security_check.py` | MCP 白名单安全自查（只读）：对齐 MCP Security Best Practices + ADR/安全策略。 |
| `tools/memory_rag.py` | memory-rag 最小版：本地 hash-vector 索引 + 判例/文档召回（无外部 Key）。 |
| `tools/merge_gated.py` | 合并入口（兼容层）——质量闸已内联 `devtask.py merge`（P0#1 收尾，2026-10-29 P37）。 |
| `tools/merge_lock.py` | 跨进程 merge 串行锁（默认安全：同刻只允许一个 merge_gated/devtask merge）。 |
| `tools/merge_parquet.py` | merge_parquet.py — 分片 parquet 的目录级合并（统一 schema + 流式写入）。 |
| `tools/merge_preflight.py` | 合并前预检（pre-merge-commit 钩子用，2026-10-28 · P13）。 |
| `tools/merge_train.py` | merge train 门（W1/UF-09，ADR-015/ADGS v1.1 §9.8）——独立工具，纯 stdlib、无副作用。 |
| `tools/metadata_daily.py` | 元数据日报（P0-1，2026-10-30）：一致性/新鲜度/备份覆盖 → 每日一条事件。 |
| `tools/method_replay.py` | 交易手法 paper 历史回放（WO-METHODS）。只读 K 线，不写 paper 账本。 |
| `tools/mission.py` | Mission MVP CLI：文档→plan；dry-run 派工；列表。 |
| `tools/mission_evolve.py` | Mission 阶段3 CLI：采集/汇总/进化建议/候选/签字入库。 |
| `tools/mission_worker.py` | Mission Worker CLI：自动接单低风险任务 → 在 worktree 写文件 → 语法检查 → commit。 |
| `tools/model_route.py` | — |
| `tools/morning_brief.py` | tools/morning_brief.py —— 晨会三件套生成器 v0（K-ORG-056） |
| `tools/mutual_review_advice.py` | P2 异族互镜第 1 轮：本地 Qwen（非 DeepSeek 模型族）逐条复核最近 5 条 AI 治理建议。 |
| `tools/night_batch.py` | night_batch.py — 夜间长跑批处理循环器（本地模型两条积压队列）。 |
| `tools/night_test_baseline.py` | 夜跑测试基线：分套件跑 pytest，保存基线 JSON；--check 对比上一次，新增失败即 rc=1。 |
| `tools/notify_devops.py` | 开发指挥台 · 飞书卡型（K-STOCK-114 M2）——离屏遥控三卡。 |
| `tools/ocr_gate_eval.py` | OCR 门噪声评估采样器（P2-3，2026-10-30）。 |
| `tools/ollama_rag_smoke.py` | 本地 Ollama/RAG 运行与 smoke 工具（K-STOCK-160）。 |
| `tools/org_chart.py` | 研发部组织架构图生成/校验（2026-10-27 WO-ORG-CHART）。 |
| `tools/org_landing_review.py` | 11-03 组织落地硬复核 · 只读聚合器（2026-11-01 WO-ORG-LANDING-REVIEW-20261101 / task-42）。 |
| `tools/org_model_consistency.py` | org 模型一致性 gate（K-ORG-20261031-ORMMODEL / W4 决策 D2）。 |
| `tools/org_registry_server.py` | 组织架构图注册服务（K-ORG-053 · 架构即注册表）。 |
| `tools/org_supervision.py` | 组织监督判读脚本（K-ORG-063 · 055 计划 L2 治理线配套）。 |
| `tools/os_goal_ref/os_gate_check.py` | OS 首版「单事项闭环」合同校验器 v1.0（WP0 参考实现，非生产代码） |
| `tools/os_goal_ref/os_goal_check.py` | 零界 v1.1「事业目标—事项—成果」三级关系校验器（WP-A1 参考实现） |
| `tools/os_goal_ref/os_samples.py` | 合成事件账本样本工厂 —— 给 os_gate_check 自测与演示用。 |
| `tools/os_goal_ref/tests/test_os_gate_check.py` | os_gate_check 合同校验器回归测试（WP0 自测，全内存、不写盘）。 |
| `tools/os_goal_ref/tests/test_os_goal_check.py` | v1.1「事业目标—事项—成果」三级关系校验器回归测试（全内存）。 |
| `tools/otel_tracing.py` | OTEL 追踪助手（fail-open，默认关闭）：给关键链路加轻量 trace，不改变业务行为。 |
| `tools/outcome_contract_check.py` | W3 outcome 契约只读检查器（outcome_contract_check）。 |
| `tools/p0_eval_runner.py` | P0-4 确定性基线评测 runner（30 golden）。 |
| `tools/p0_shadow_guard.py` | p0_shadow_guard.py — 诸葛亮能力底座 P0-5：最小护栏（shadow 模式）。 |
| `tools/p0_trace.py` | p0_trace.py — 诸葛亮能力底座 P0-6：Phoenix 本地 tracing。 |
| `tools/p1_citation_eval.py` | P1-1 引用率评测：对 factual 用例跑 grounded_chat，检查 [S#] 引用。 |
| `tools/p1_rag_eval.py` | P1-2 M1 recall@3 评测：8 条 factual，检查相关证据是否进入 BM25 Top3。 |
| `tools/p1_rag_observe.py` | p1_rag_observe.py — P1-2 观察期汇总（只读 grounding_usage.jsonl）。 |
| `tools/p2_weekly.py` | P2 周报包装器（只读）。 |
| `tools/p4_ast_gate.py` | p4_ast_gate.py —— P4 拆分的 AST 断言门（K-STOCK-089 第四道门落地工具） |
| `tools/p4_batch0_rehearsal.py` | P4 批 0 预演（K-STOCK-096）：在 git archive 副本里演练 api_common.py |
| `tools/page_shell_gen.py` | PageShell v2 生成器：统一壳资产注入 + 新页模板。 |
| `tools/paper_bridge.py` | paper_bridge：pick_verdicts → paper.db 薄桥（K-STOCK-095，10-16 实弹）。 |
| `tools/paper_daily.py` | 量化部 paper 日报（2026-10-30 WO-INV-PAPER-MVP）。 |
| `tools/pg_robot_watchdog.py` | PG-Robot 看门狗（K-STOCK-302） |
| `tools/phoenix_start.py` | Phoenix 幂等自启：健康检查→(在跑则退出0/不在跑则拉起并等健康)。 |
| `tools/pip_osv_audit.py` | 依赖漏洞审计兜底：PYPI 冻结清单 → OSV batch API。 |
| `tools/plan_dispatch.py` | 研发计划拆解 → 工单/派工（半自动，2026-10-29 WO-PLAN-DISPATCH）。 |
| `tools/plan_register.py` | 计划台账生成器（2026-10-30，关羽 P90）。 |
| `tools/plan_staging_cleanup.py` | 生成 tushare_staging 清理清单（只读，不删任何东西）。 |
| `tools/plan_status_lint.py` | plan_status_lint.py — docs/plans 计划 STATUS 覆盖 lint（只读，2026-11-01）。 |
| `tools/planner_experience.py` | Planner 经验规则 CLI：status / build-rules / ab。 |
| `tools/pool_review_daily.py` | 司马徽（ACAD1）推荐池每日复盘（2026-10-30 WO-INV-RECOMMEND-POOL）。 |
| `tools/pool_tradability_audit.py` | pool_tradability_audit: 候选池可成交性审计（mask-first 思想落地，arXiv 2507.07107 对照） |
| `tools/portal_route_inventory.py` | portal_api 路由清单/一致性基线（A5 B0，2026-10-31 关羽）。 |
| `tools/portal_route_table_draft.py` | portal_api do_GET/do_POST 路由表草案生成器（A5 B4/B5，2026-10-31 关羽）。 |
| `tools/portal_smoke.py` | 门户 8091 全路由只读冒烟自检（P4 基座拆分跨域回归固化）。 |
| `tools/post_commit_event.py` | 跨 Windows/Git shell 的 post-commit 事件回流适配器。 |
| `tools/power_watch.py` | 电源/挂起心跳守护（独立脚本，零第三方依赖，Python 3.9+）。 |
| `tools/probe_llm_capability.py` | probe_llm_capability.py — 本地推理能力探针（健康卡第 22 项候选）。 |
| `tools/probe_task_start.py` | probe_task_start.py — 任务启动链路探针（80 行级，只读判据 + 一次性任务）。 |
| `tools/product_metrics.py` | product-metrics 最小版：P2 产品数据闭环（本地聚合 + 反馈）。 |
| `tools/project_wiki.py` | project_wiki：生成仓库 Wiki（仓库地图 / 核心模块 / ADR 索引 / 约定入口）。 |
| `tools/prompt_version_registry.py` | D1 (K-ORG-030, ARCH1): prompt 版本化登记器 |
| `tools/prune_worktrees.py` | prune_worktrees.py — worktree 自动回收（默认 dry-run）。 |
| `tools/push_eval_reminder.py` | K-STOCK-304 C 项评估日提醒（一次性，2026-10-20 09:00 由计划任务拉起）。 |
| `tools/push_liveness.py` | 推送活性巡检（K-STOCK-304 B 件） |
| `tools/qa1_review.py` | qa1_review.py — QA1 质检复核固化工具（K-ORG-038a）。 |
| `tools/qa_run.py` | qa_run.py —— QA 岗实跑验证工具（K-ORG-090 / T3，QA-RUN 五步闭环）。 |
| `tools/qs1_accept_upstream.py` | WA1 → QS1 上行受理/ACK 链（2026-10-30 WO-INV-CHAIN）。 |
| `tools/qs1_runtime_probe.py` | QS1（司马懿）运行体只读探针（2026-10-29 WO-INV-P0-ALIGN）。 |
| `tools/quality_scale_check.py` | O2 质量分级检查器：席位/工具/集成三层 × Bronze/Silver（只读）。 |
| `tools/quant_pool_daily.py` | 每日股票池 → 组合计划（2026-10-30 WO-INV-POOL-COMBO）。 |
| `tools/quant_readonly.py` | P2 量化只读工具 v0：复用 tushare-http MCP（258 工具），只读、白名单、不落明文。 |
| `tools/r1_daily_check.py` | R1 恢复只读检查器（计划任务 ``HK_R1_Daily`` 18:30 节点）。 |
| `tools/rebuild_academy_source_snapshot.py` | 重建零界学院 E 盘只读候选来源快照。 |
| `tools/recent_data_fill.py` | 研究相关数据「补齐」：最近缺口按日/按公告日拉取并落盘。 |
| `tools/reconcile_subscription.py` | 订阅账本勾稽自检（飞书中台 P3.7-lite · L-D / WO-FS-104）。 |
| `tools/redteam_daily.py` | P2 红队定时化 v0：每日抽样 + 周报（不建计划任务，先提供可调度入口）。 |
| `tools/refresh_curated.py` | 重建「整理层」tushare_curated（raw → curated）。 |
| `tools/refresh_hermes_capabilities.py` | 刷新 Hermes「赵云」能力卡（seat_capabilities/v1）——保守刷新器。 |
| `tools/release_artifact_guard.py` | artifact 完整性 guard + 只读锁工具（K-ORG-20261031-ARTLOCK）。 |
| `tools/release_legion_ide.py` | 军团 IDE 扩展发布工具（P0-0 方案 A）。 |
| `tools/release_provenance.py` | release artifact provenance 生成/校验器（P0 发布供应链，SLSA 启发，零外部依赖）。 |
| `tools/release_rollback_drill.py` | W1 Legion IDE 发布回滚演练（release rollback drill，隔离、可重复、一条命令）。 |
| `tools/release_sbom.py` | release artifact SBOM 生成/校验器（CycloneDX 1.5 JSON，离线、零外部依赖）。 |
| `tools/repo_map.py` | K-ORG-035/036 F2: 仓库地图（借 Aider RepoMap 范式，自研实现）。 |
| `tools/req_lines_check.py` | O8 P2：分线 lock 快照校验（只读）。 |
| `tools/research/ah_premium.py` | H15 · A/H 溢价收敛研究（汇率换算 + 港股日线 + A 股日线）。 |
| `tools/research/ann_classify.py` | 公告分类 v2：columns_txt（官方类型）+ 标题关键词 双层规则。 |
| `tools/research/book_batch_test.py` | tools/research/book_batch_test.py · 量学战法批量四关初筛（2026-11-01，支线 D）。 |
| `tools/research/bt_harness.py` | 统一回测口径 bt_harness v1（2026-11-01 · 支线 C）。 |
| `tools/research/daily_screening.py` | 每日筛选流水线（可手动运行；不注册定时任务，dry-run 安全）。 |
| `tools/research/daily_tasks_health.py` | 每日任务健康监控 + 失败飞书告警（task-108）。 |
| `tools/research/event_study.py` | H1 公告事件研究：公告披露 → 次日/5 日市场调整超额（CAR）。 |
| `tools/research/event_study2.py` | H11（3 年公告 CAR，行业中性）+ H2（PEAD，持有 5/10/20 日）研究工具。 |
| `tools/research/evidence_baseline.py` | 证据基准重建：低波 + 反转（20/120日）+ 低换手，行业中性化，月频 topN，四关检验。 |
| `tools/research/factor_daily.py` | factor_daily.py — 因子挖掘师（FACTOR1/蒋琬）每日因子挖掘与策略优化轮。 |
| `tools/research/factor_diagnostics.py` | 脱机因子诊断工具 v1（2026-11-01 WO-STRAT-FDIAG-20261101 / task-24）。 |
| `tools/research/factor_gate.py` | 因子诊断准入 preflight（只读）v1（2026-11-01 WO-STRAT-FGATE-20261101 / task-27）。 |
| `tools/research/factor_lib.py` | 因子库 v1.0 · 因子注册表 + PIT 特征 + 20 日 CAR 按日横截面 IC 评测。 |
| `tools/research/factor_panel_validate.py` | 真实因子面板验证运行器 v1（2026-11-01 WO-VAL-FACTOR-20261101 / task-32）。 |
| `tools/research/factor_strat.py` | H7 财务因子分层：PIT 财务指标（价值/质量/成长）月度 IC 与五分位多空。 |
| `tools/research/factor_validate.py` | 支线 E：正向因子独立验证（中性化 + walk-forward + 成本容量）。 |
| `tools/research/flow_divergence.py` | H4 资金流背离：moneyflow 净流入 × 当日涨跌 → 未来 5/10 日行业×规模中性收益。 |
| `tools/research/four_gate_eval.py` | 四关验证：前视/成本/容量/regime（对 mining v2 候选）。 |
| `tools/research/fund_evidence.py` | 基本面阈值证据：净利yoy/营收yoy/ROE 是否有预测力、阈值分档是否单调。 |
| `tools/research/h14_shadow.py` | H14 影子盘（A 方案）：质量组合 + 质量陷阱组权重上限 5%。 |
| `tools/research/h14_shadow_status.py` | H14 影子盘状态 → 门户静态 JSON（零重启）。 |
| `tools/research/h18_watch_push.py` | S03/H18 事件观察推送（默认 dry-run，--send 才发飞书）。 |
| `tools/research/hypotheses.py` | 假设台账 CLI：research/hypotheses.db 的初始化/播种/查询/更新。 |
| `tools/research/hypothesis_event_study.py` | 只读事件研究 harness v1（2026-11-01 WO-STRAT-EVENTSTUDY-20261101 / task-29）。 |
| `tools/research/industry_rs_factor.py` | tools/research/industry_rs_factor.py · 行业相对强度（RS）横截面因子严格检验。 |
| `tools/research/jev_attribution.py` | H6 JEV 决策归因：bias / event_type / playbook / regime × 收益与胜率。 |
| `tools/research/legacy/ah_monitor.py` | A/H 溢价每日监控信号（H15 研究的可落地形态）。 |
| `tools/research/legacy/ah_premium_shortable.py` | H15b · A/H 溢价收敛「可交易性子样本」：只保留 A 股为融资融券标的的配对。 |
| `tools/research/legacy/dragon_tiger.py` | H3 龙虎榜机构席位跟单：机构净买入 → 未来 5/10 日收益（行业×规模中性 + OOS + 成本）。 |
| `tools/research/legacy/h10_quality_sensitivity.py` | H10 数据质量扰动框架：6 类扰动 → 质量因子（ROE）月度 IC / 多空的变化。 |
| `tools/research/legacy/h13_multifactor.py` | H13 多因子复核：资金流动量 + 短期反转 + 市值 + 价值（+质量负向规避），月度调仓。 |
| `tools/research/legacy/h16_ah_strat.py` | H16 · A/H 溢价收敛的横截面结构（行业/市值/流动性分层）。 |
| `tools/research/legacy/h17_hsgt_leadlag.py` | H17 · 南向资金/港股通持股对 A/H 溢价的领先性检验。 |
| `tools/research/legacy/h18_forecast_revision.py` | H18 · 业绩预告修订动量（同公司同报告期的二次+公告）。 |
| `tools/research/legacy/h18_revision_alert.py` | H18 事件风险提醒：业绩预告修订 → 恶化修订清单（短期规避提醒）。 |
| `tools/research/legacy/h19_announcement_sentiment.py` | H19 · 公告标题情绪（关键词极性）→ 短期收益（anns_d 2019-2026）。 |
| `tools/research/legacy/h20_hkhold_ah.py` | H20 · 港股通持仓变化 × A/H 价差。 |
| `tools/research/legacy/h20_hkhold_ah_v2.py` | H20-v2 · 港股通持仓变化 × A/H 价差（hk_daily 回补 2021 后扩展样本）。 |
| `tools/research/legacy/h21_negative_ann_moneyflow.py` | H21 · 负面公告 × 主力净流出 复合规避信号。 |
| `tools/research/legacy/h22_ah_regime_timing.py` | H22 · A/H 高离散 regime 择时。 |
| `tools/research/legacy/h23_southbound_ah_momentum.py` | H23 · 南向增持 → A 相对 H 走强（20 日）· 重叠修正检验。 |
| `tools/research/legacy/h24_liangxue_features.py` | H24-H26 · 量学知识单元 → 可计算特征 → 事件研究。 |
| `tools/research/legacy/h29_liangxue_refined.py` | H29 · 严格版量学特征回归 + H28 规避信号容量测试。 |
| `tools/research/legacy/h29_param_capacity.py` | H29 深化：黄金柱/倍量确认 参数敏感性 + 容量/滑点评估。 |
| `tools/research/legacy/h2v2_earnings_surprise.py` | H2 v2：真实盈余惊喜 → PEAD（公告后 5/10/20 日漂移）。 |
| `tools/research/legacy/h30_quality_redflags.py` | H30-H32 · 企业尽调知识库「造假红旗」→ 基本面因子 → 年报公告事件研究。 |
| `tools/research/legacy/h33_quarterly_redflags.py` | H33 · H30 存贷双高 · 季度公告扩展重测（提高统计功效）。 |
| `tools/research/legacy/h34_report_coverage.py` | H34-H36 · 研报覆盖/关注度因子（research_report 元数据，无需解析 PDF）。 |
| `tools/research/legacy/h34_report_rc_factors.py` | H34-H36 v2 · 研报评级/盈利预测/覆盖因子（report_rc 历史数据）。 |
| `tools/research/legacy/h36_org_breadth_deep.py` | H36 深挖 · 机构广度拥挤反转（精炼版）。 |
| `tools/research/legacy/h5_sentiment_pilot.py` | H5 · 舆情情绪领先价格（pilot，数据可行性首测）。 |
| `tools/research/legacy/h8_industry_rotation.py` | H8 行业资金轮动（PIT 行业版）：行业净流入周度 z-score → 未来 20 日行业超额收益。 |
| `tools/research/legacy/h9_tradability.py` | H9 可交易性检验：外盘隔夜信号 → A 股「开盘缺口 vs 日内收益」分解。 |
| `tools/research/legacy/quality_reversal.py` | H14 质量反向：高 ROE 跑输是否在估值分层内仍存在（剥离质量-估值交互）。 |
| `tools/research/legacy/s10_long_sample.py` | S10 长样本验证（2015-2026）：低波+反转（+换手 2023+）行业中性 top20。 |
| `tools/research/legacy/s10_rebalance.py` | S10 证据基准影子：低波+反转+低换手（行业中性）top20 等权，月频。 |
| `tools/research/legacy/shadow_monitor.py` | 影子盘监控（P3）：归因（行业/分数分桶/个股）+ 衰减熔断 + 门户 JSON。 |
| `tools/research/legacy/strategy_inventory.py` | 策略/方法论全景盘点（可复现）：注册表 + 台账 + paper + 脚本/文档计数 → JSON。 |
| `tools/research/legacy/walkforward_eval.py` | walk-forward 稳健性框架：逐年滚动重估（解决「OOS-only」线索的 regime 问题）。 |
| `tools/research/legacy/weekly_review.py` | 研究周复盘：台账状态汇总 + 本周新增报告 + 四关进展（供周五例会）。 |
| `tools/research/liangxue_rs_overlay.py` | 量学选股 × 板块相对强度（RS）叠加层（v2 修正单位/因子映射）。 |
| `tools/research/lowturn_attribution.py` | 支线 J：low_turnover 逐年 / regime 归因（2023-24 负超额根因）。 |
| `tools/research/lowturn_freq_validate.py` | 支线 G：低频/成本优化验证（low_turnover 中性化组合）。 |
| `tools/research/lowturn_residual.py` | 支线 M：low_turnover 双重中性化残差检验（剥离「大市值+低波」风格后是否还有独立 alpha）。 |
| `tools/research/lx20_avoid_rule.py` | tools/research/lx20_avoid_rule.py · LX20 牛股三绝「规避/降配规则」可执行性深化（2026-11-01，wave4 支线 K）。 |
| `tools/research/neutral_effect.py` | 行业中性化效应量化：raw z vs 行业内 z，对 5 个候选因子的月频 IC/多空影响。 |
| `tools/research/panel_daily.py` | 研报面板日常化运行器：刷新门户静态 JSON + 校验 + 状态台账 + 变更摘要。 |
| `tools/research/panel_export.py` | 研报面板门户导出器：v3 面板 JSON → portal 静态 JSON（供 selection.html 展示）。 |
| `tools/research/pit_utils.py` | PIT 工具：上市天数/上市资格（按信号日计算，避免次新股前视）。 |
| `tools/research/portal_health.py` | 全站页面巡检 + 静态 JSON 契约校验 + 系统总览刷新（task-114）。 |
| `tools/research/portfolio_build.py` | 组合构建（P2）：选股清单 → 组合权重（波动率目标 + 单票/行业上限 + 成本估算）。 |
| `tools/research/report_panel.py` | 多智能体研报评审面板 MVP（TradingAgents 思路）。 |
| `tools/research/report_panel_v2.py` | 研报评审面板 v2：页码校验 + 多模型交叉 + 候选降维（TradingAgents 思路强化版）。 |
| `tools/research/report_panel_v3.py` | 研报评审面板 v3：修复截断/超时 + 精确页码校验 + 人工复核清单。 |
| `tools/research/reverse_avoid_test.py` | tools/research/reverse_avoid_test.py · 量学反向信号的「规避组合」深测（2026-11-01，支线 H）。 |
| `tools/research/rs_overlay_strict.py` | RS（强于板块）叠加层严苛复验 —— 司马微（task-88）。 |
| `tools/research/s01_neutral_test.py` | S01 中性化检验：剥离市值/行业暴露后，H14 质量陷阱过滤是否有 alpha。 |
| `tools/research/s05_overlay_test.py` | tools/research/s05_overlay_test.py · S05 规避规则 × S01 基线叠加测试（2026-11-01，wave5 支线 N）。 |
| `tools/research/s05_reverse_hypothesis.py` | tools/research/s05_reverse_hypothesis.py · S05 反向（超配）假设验证（2026-11-01，wave6 支线 P）。 |
| `tools/research/s06_reversal_combo.py` | S06 反转组合影子候选：mom_20 + mom_120 双反转组合（top20 等权，月频调仓）。 |
| `tools/research/s07_s08_shadows.py` | S07/S08 单因子小仓位影子：mom_20（S07）/ mom_120（S08）多头 top50 等权。 |
| `tools/research/s08_rebalance.py` | S08 长期反转影子：月度调仓 + 逐月跟踪（幂等）。 |
| `tools/research/s08_research.py` | S08（mom_120 长反转多头）改进研究：频率×持仓数×权重×中性化×行业上限。 |
| `tools/research/s08_v2_verify.py` | S08-v2 验证：对「M/K20/score」与基线「M/K50/eq」做年度/regime/容量/四关对照。 |
| `tools/research/s11_mom120_robust.py` | S11 mom_120 稳健影子网格：long-only 可交易性检验（PIT 修正版）。 |
| `tools/research/s11_rebalance.py` | S11 影子：mom_120 反转 + 下行 regime 过滤（60日市场收益<-2%），K20 分数加权，月频。 |
| `tools/research/s11_regime_sens.py` | S11 regime 阈值敏感性：市场趋势阈值/回看窗口是否刀锋。 |
| `tools/research/screening_20261101.py` | 用已研究的选股策略对最新历史数据做一次全市场筛选（支持量学 × 板块 RS 叠加）。 |
| `tools/research/selection_daily.py` | 每日选股任务：按策略库注册表运行所有可执行策略，输出统一 JSON（页面/接口/Agent 共用）。 |
| `tools/research/selection_engine.py` | 选股能力 MVP v1.1 · Strategy → Selection（策略→候选清单）。 |
| `tools/research/sepa_backtest.py` | SEPA 完整体系回测（Minervini v2，修正枢轴/突破流程）。 |
| `tools/research/sepa_scan.py` | SEPA 全市场扫描（A股）：TT 8 条 + PIT 基本面成长 + VCP 设置/突破状态。 |
| `tools/research/sepa_scan_daily.py` | SEPA 每日扫描包装：跑严格+宽口径扫描 → 合并静态 JSON/台账/摘要。 |
| `tools/research/shadow_dashboard.py` | 影子盘统一看板：汇总 S08/S11/H14 状态 → portal/static/shadow_dashboard.json。 |
| `tools/research/shadow_ledger.py` | 选股影子盘（P2）：模拟 T+1 收盘调仓，记录 NAV/回撤/换手，输出门户 JSON。 |
| `tools/research/smallcap_reversal_feasibility.py` | 「小市值反转」可行性研究（司马微 · task-92；不写策略、不调参）。 |
| `tools/research/stat_utils.py` | 统计修正工具（判例 2026-10-30：H22 前视 / H23 重叠）。 |
| `tools/research/strategy_miner.py` | 策略自挖一轮 v2：窗口扩到 2019+（价格），修复 report_rc 特征稀疏；月频 IC + 多空分层。 |
| `tools/research/strategy_specs.py` | 量学战法规格库（research/strategy_specs/*.yaml）加载 / 校验 CLI。 |
| `tools/research/survivorship_check.py` | 幸存者偏差评估：当前股票池 vs 含退市股票的全历史代码池（2019-2026）。 |
| `tools/research/survivorship_pit.py` | PIT 股票池重建（含退市股 + 历史 ST 名称）并重算 mom_120。 |
| `tools/research/tests/test_ann_classify.py` | ann_classify 单测：columns_txt 优先、标题兜底、覆盖率口径。 |
| `tools/research/tests/test_event_study.py` | event_study 纯函数单测（合成行情，不读真实数据）。 |
| `tools/research/tests/test_event_study2.py` | event_study2 纯函数单测（行业中性映射 / stats / 分类调用）。 |
| `tools/research/tests/test_event_study_promote.py` | event_study v2（晋级三关）纯函数单测：市值分组中性 + 成本后净 CAR 口径。 |
| `tools/research/tests/test_hypotheses.py` | hypotheses 台账单测（临时 DB）。 |
| `tools/research/tests/test_jev_attribution.py` | jev_attribution 单测（临时 sqlite 夹具；aw_decision 走外部不测）。 |
| `tools/research/tests/test_review_fixes.py` | 代码审查补测：盈余方向解析 / 分位排名 / 边界。 |
| `tools/research/trend_template.py` | Minervini Trend Template 8 条筛选器（A 股骨架，仅筛选/研究，不入策略库）。 |
| `tools/research/trend_template_export.py` | 对外能力导出：最新 Trend Template 筛选报告 → portal/static/trend_template_latest.json |
| `tools/research/trend_template_v11.py` | Minervini Trend Template v1.1：股票池过滤 × 退出规则 × RS 口径 组合回测。 |
| `tools/research/tt_ablation.py` | TT 基准条件消融：8 条逐条去掉，看哪条是「承重墙」。 |
| `tools/retro_brief.py` | R5 复盘回流生成器（联动层二期 R5）：计划任务「AgentRetrospectBrief」动作体。 |
| `tools/riskguard_postmortem.py` | RiskGuard 误杀后验复算分析器 v0.1（K-ORG-113 · S3 备料·只读）。 |
| `tools/role_group_model.py` | 研发军团角色分工与分组适配模型校验（2026-10-27 WO-ROLE-GROUP-MODEL）。 |
| `tools/rotate_logs.py` | rotate_logs.py — 日志轮转 + 回收站保留（默认 dry-run，T7 轨道） |
| `tools/rotate_quantdinger_keys_20261015.py` | quantdinger 密钥轮换（P0 收口 2026-10-15，用户拍板）。 |
| `tools/ruff_ratchet.py` | ruff F/E9 棘轮：脏账只降不升（默认基线在 tools/ruff_baseline.json）。 |
| `tools/run_contract_view.py` | O6 P1/P2d：从现有 traces.sqlite **只读派生** Run/Event 契约视图（支持任务级聚合）。 |
| `tools/s2_sign_pilot.py` | D4–D5 试点：M4/v1.1 S2 签字卡（构建 + 可选发送，回调走 approval_control）。 |
| `tools/safe_delete.py` | safe_delete.py — 按 AGENTS §7 的要求删除：默认进回收站，永久删除需显式开关。 |
| `tools/safe_urlopen.py` | 安全 urlopen：仅允许 http/https scheme（防 file:// / 自定义 scheme）。 |
| `tools/sandbox_verify.py` | sandbox-verification：临时 git worktree + 可选 venv + pytest 证据包。 |
| `tools/scan_secrets.py` | scan_secrets — 防泄漏只读扫描器（pre-commit 闸门） |
| `tools/scan_task_paths.py` | 扫全部已知计划任务的动作路径，找同一类"起不来"隐患（只读）。 |
| `tools/seat_capability_inject.py` | seat_capability_inject.py — 席位能力底座注入器（2026-10-23 00:20；2026-10-26 TFSEAT：田丰补全 15 能力 + 15 技能卡；2026-10-27 ACAD1 P0/P1：+acad1/strat1/back1）。 |
| `tools/seat_evidence_packet.py` | seat_evidence_packet.py — 田丰取数门面：确定性证据包生成器（零 LLM / 零网络 / 只读源）。 |
| `tools/seat_iteration.py` | 曹操下属能力迭代体系（2026-10-29 WO-SEAT-ITER）。 |
| `tools/seat_iteration_receipts.py` | 11 席能力迭代「确认回执」只读跟踪器（2026-10-31 WO-SEAT-ITER-RECEIPTS / task-28）。 |
| `tools/seat_learning.py` | 单席位能力学习计划（2026-10-28 WO-LEARNING-DEV7）。 |
| `tools/seat_memory_sync.py` | seat_memory_sync.py — 三席位三层记忆装配器（一次性注入工具，2026-10-22）。 |
| `tools/seat_router.py` | CLI：席位路由/虚拟 Key 管理。 |
| `tools/seat_system_audit.py` | seat_system_audit.py — 席位系统完整性审计（只读，2026-10-26）。 |
| `tools/seat_write_guard.py` | seat_write_guard.py — 田丰 v3 L2 写路径白名单/执行确认（检测层）。 |
| `tools/sec2_review.py` | sec2_review.py — SEC2 方案级走形检查固化工具（K-ORG-054 · G3 闭环）。 |
| `tools/secret_scan_gitleaks.py` | gitleaks 包装器：调用本机 gitleaks 扫描指定目录（默认主仓），输出摘要。 |
| `tools/secrets/apply_rotation_fillback.py` | 批量应用「轮换回填表」到本地目标（A3 · R1/R2/R4/R7） |
| `tools/secrets/mcp_preflight.py` | MCP 配置预检/应用/回滚：解决「.mcp.json 只留占位符、重启前必须渲染」的运维缺口。 |
| `tools/secrets/post_rotation_check.py` | X5 Tushare token 轮换 · 轮换后一键验收（P0-3，2026-10-30）。 |
| `tools/secrets/render_mcp.py` | render_mcp.py —— 从 ``.mcp.json.template`` + 密文 token 渲染 ``.mcp.local.json``。 |
| `tools/secrets/rotate_item.py` | R2/R4/R7 凭据本地轮换引导（A3 · 2026-10-27） |
| `tools/secrets/rotate_token.py` | Tushare MCP token 轮换收口：安全输入 → 加密入库 → 渲染 → 审计。 |
| `tools/secrets/rotate_tushare_token.py` | R1 Tushare MCP token 轮换引导（A3 E1-E9 · 2026-10-27） |
| `tools/secrets/tests/test_rotation_helpers.py` | mcp_preflight / rotate_token 纯函数单测（不联网、不碰真实保险库）。 |
| `tools/secrets/tests/test_vault.py` | tools/secrets 单元测试：加密→解密 round-trip、MANIFEST 校验、fail-closed。 |
| `tools/secrets/vault.py` | secvault —— L1 凭据保险库（age 优先 / cryptography AES-GCM 兜底）。 |
| `tools/security_gate.py` | security-gate：密钥/AST 危险模式扫描 + 证据落盘（只读）。 |
| `tools/selftest_detectors.py` | selftest_detectors.py — 检测器自证（判例 04 的工具化）。 |
| `tools/service_restart.py` | 提权服务重启监督（默认只检查；--apply 才触发重启）。 |
| `tools/shadow_eval.py` | shadow_eval.py — D3 影子评测：行为对齐人设（K-ORG-040）。 |
| `tools/shadow_node_probe.py` | run_shadow 单节点最小链路 smoke（只测模型/绑定，不写账本）。 |
| `tools/sign_approval.py` | 离线签名器（人类专用；Agent 不得调用）。 |
| `tools/sop_link_check.py` | docs 本地链接检查（WO-DRILL5-SOPLINK）：防 SOP/ADR 文档断链。 |
| `tools/spec_flow.py` | spec-driven-delivery：规范→计划→任务→收敛 的产物链与检查。 |
| `tools/sql_guard.py` | SQL 标识符 allowlist（防 B608：只允许显式白名单的 schema/表/列名）。 |
| `tools/strategy_health_weekly.py` | Phase 6-B：Strategy Health 周报生成 CLI（只读 PG → 原子写 latest.json）。 |
| `tools/subscription_smoke.py` | G3 订阅全流程演练脚本（P3.10-lite · O-C 工位 · WO-FS-133）。 |
| `tools/sync_pipeline_to_strategies.py` | E5 卡墙↔收编流水线回写工具。 |
| `tools/t1_ab.py` | T1/T2 输出预算/契约 A/B：同一 prompt 对比 num_predict 与输出顺序。 |
| `tools/t1t2_capture.py` | T1/T2 A/B trace 采集与受控归档（2026-11-01 WO-T1T2-CAPTURE-20261031）。 |
| `tools/t1t2_metrics.py` | T1/T2 A/B 指标只读聚合（2026-11-01 WO-T1T2-METRICS-20261031）。 |
| `tools/task_board_audit.py` | 任务板对账（只读）：找出「已合并未关单/无分支/提交未合并/无 worktree」等状态漂移。 |
| `tools/task_board_stale.py` | 任务板 stale 清理（默认 dry-run；--apply 需 --yes 且仅限安全类）。 |
| `tools/task_ledger_reconcile.py` | task_ledger_reconcile.py — 任务账本对账工具（W2，2026-10-31，工单 K-ORG-20261031-LEDGERRECON）。 |
| `tools/test_acad_d4_freeze_check.py` | test_acad_d4_freeze_check.py — D4 冻结对账工具测试。 |
| `tools/test_acad_d4_tiebreak.py` | test_acad_d4_tiebreak.py — D4 tie-break 策略模拟测试。 |
| `tools/test_academy_research.py` | 研究院课题与 Worker 阶段绑定测试。 |
| `tools/test_academy_summary_health.py` | summary 独立只读路径及健康状态。 |
| `tools/test_ai_onboard.py` | — |
| `tools/test_approval_inbox.py` | — |
| `tools/test_audit_p1_observability.py` | — |
| `tools/test_audit_strategy_acceptance.py` | — |
| `tools/test_audit_training_candidates.py` | — |
| `tools/test_auditfix_regressions.py` | 审计 A 修复回归测试（K-QUANT-20261026-AUDITFIX）。 |
| `tools/test_baseline.py` | 测试失败基线工具 v0：跑核心套件、记录基线、对比上次（不修改测试）。 |
| `tools/test_browser_verify.py` | — |
| `tools/test_change_gate.py` | change_gate 单测：快照/回归/回滚。 |
| `tools/test_check_ports_json.py` | check_ports.py 单测：JSON 契约 + 2026-10-26 strict 语义拆分回归。 |
| `tools/test_codebase_intel.py` | — |
| `tools/test_comm_zhuge_debate.py` | L5 辩论字段单测（fake 模块）。 |
| `tools/test_comm_zhuge_grounding.py` | L5 comm_zhuge grounding 单测（P1-1）。 |
| `tools/test_comm_zhuge_quant.py` | L5 量化资料注入单测（fake grounding/subprocess，不联网）。 |
| `tools/test_comm_zhuge_wargame.py` | L5 兵棋字段单测（fake 模块，不联网）。 |
| `tools/test_council_route.py` | test_council_route.py — 议会厅路由器测试。 |
| `tools/test_credential_catalog.py` | — |
| `tools/test_credential_catalog_local.py` | — |
| `tools/test_credential_store.py` | — |
| `tools/test_credential_store_harden.py` | 凭据库加固测试（WO-CRED-HARDEN-20261028）。 |
| `tools/test_curated_keep_prev.py` | EXEC-R17 单测：refresh_curated.py 的 --keep-prev / --from-tmp / 缩水护栏。 |
| `tools/test_d1_raw_pit_audit.py` | D1 raw/decision_id/PIT 只读审计 CLI 测试（K-ORG-20261101-D1RAWPIT）。 |
| `tools/test_d2_gates.py` | D2 门禁工具单测：ruff ratchet 解析/diff、夜跑基线解析/diff。 |
| `tools/test_deep_shell.py` | 深屏骨架推广契约：/picks.html 静态接入 + /strategies 生成器接入。 |
| `tools/test_delivery_ledger.py` | §6.2 交付台账工具单测。 |
| `tools/test_dep_age_check.py` | O8 P3b 依赖年龄检查单测（不联网）。 |
| `tools/test_dev_acceptance_audit.py` | O4 验收独立性审计单测。 |
| `tools/test_devtask_tasks_save.py` | devtask._save 并发安全单测（K-QUANT-20261026-DEVTASKSAVE）。 |
| `tools/test_diagnostics.py` | O7 diagnostics 单测（不依赖真实服务）。 |
| `tools/test_diagnostics_hermes_probe.py` | diagnostics 8787 探针统一单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。 |
| `tools/test_dual_ui.py` | 双版本 UI 契约：浅色驾驶舱 6 导航/亮对话 + 深色深屏互链。 |
| `tools/test_durable_workflow.py` | — |
| `tools/test_e2e_run.py` | O5 E2E 运行器单测（不跑真实 e2e）。 |
| `tools/test_eval_harness.py` | — |
| `tools/test_execution_watchdog.py` | R3 失败必响巡检单测（联动层一期 T3）。 |
| `tools/test_export_nav.py` | nav.js 生成物回归：不再内联 style/hex，class + nav.css。 |
| `tools/test_export_page_linkage.py` | 联动矩阵生成器测试。 |
| `tools/test_extension_lint.py` | O3 扩展 manifest 校验器单测。 |
| `tools/test_extension_manifest_audit.py` | O3-P2 对账工具单测。 |
| `tools/test_glm_retry_probe.py` | glm_retry_probe 两阶段单测（K-QUANT-20261026-GLMPROBE2）。 |
| `tools/test_grounding_guard.py` | — |
| `tools/test_huangzhong_1104_finalize.py` | 黄忠 11-04 终版回执生成器 单测（tmp 夹具；不碰真实 deliverables/work/）。 |
| `tools/test_huangzhong_1104_preflight.py` | 黄忠 11-04 复核 preflight 工具单测（tmp 夹具 + fake _run，不依赖真实数据）。 |
| `tools/test_huangzhong_1104_receipt.py` | 黄忠 11-04 回执自动生成器单测（tmp 夹具 + fake _run/_log）。 |
| `tools/test_huangzhong_1104_run.py` | huangzhong_1104_run.py 单测（fake _run，不真实执行子工具、不写 work/）。 |
| `tools/test_huangzhong_accept.py` | 黄忠验收工具单测（fake git/证据，不联网）。 |
| `tools/test_huangzhong_blind_audit.py` | 黄忠盲审抽样包生成器单测（tmp 夹具 + fake _run，不碰真实 work/、不落 deliverables）。 |
| `tools/test_huangzhong_blocking_sim.py` | 黄忠阻断分级模拟（P3-D · WO-HZ-BLOCKSIM-20261101）单测。 |
| `tools/test_huangzhong_brief.py` | 黄忠每日简报工具单测（tmp 夹具；不碰真实 work/ 与 deliverables）。 |
| `tools/test_huangzhong_cert_lifecycle.py` | 黄忠认证生命周期 dry-run 工具单测（tmp 夹具；不碰真实 work/ 与 deliverables）。 |
| `tools/test_huangzhong_corpus.py` | 黄忠验收语料库与一致性基准 单测（tmp 夹具；不碰真实 work/ 与 deliverables）。 |
| `tools/test_huangzhong_coverage_trend.py` | WO-HZ-COVTREND-20261031 单测：黄忠覆盖率趋势与 11-04 投影工具（tmp 夹具，不碰真实数据）。 |
| `tools/test_huangzhong_daily.py` | 黄忠 W1 单测：样例文档在位 + 每日观察/周报工具 + 日更验收事件联动。 |
| `tools/test_huangzhong_defect_classify.py` | 黄忠缺陷分类与周报单测（tmp 夹具，只读数据源，零网络）。 |
| `tools/test_huangzhong_drill.py` | huangzhong_drill.py 单测（tmp 夹具，不写真实 work/、不碰生产证据/历史）。 |
| `tools/test_huangzhong_evidence_lint.py` | 黄忠验收单证据 schema lint 单测（tmp 夹具，绝不触碰真实 work/ 或 deliverables）。 |
| `tools/test_huangzhong_evidence_refresh.py` | huangzhong_evidence_refresh 单测（不跑真实 pytest/红队）。 |
| `tools/test_huangzhong_go_no_go.py` | 黄忠 11-04 Go/No-Go 决策工具单测（tmp 夹具，不读真实 work/、不写真实 deliverables）。 |
| `tools/test_huangzhong_grade.py` | 黄忠 W1 批改工具单测（临时提交目录，不联网）。 |
| `tools/test_huangzhong_health.py` | huangzhong_health.py 单测（tmp 夹具 + fake _run；不读真实 work/、不写盘）。 |
| `tools/test_huangzhong_health_alert.py` | huangzhong_health_alert.py 单测（tmp 夹具 + fake _run；不跑真实 health、不写生产 alerts）。 |
| `tools/test_huangzhong_impact.py` | 黄忠变更影响面验证工具单测（tmp 夹具 + fake _run，绝不跑真实 git/测试/写 work/）。 |
| `tools/test_huangzhong_incidents.py` | 黄忠事故台账工具单测（tmp 夹具，零网络，不触碰真实 work/ 台账）。 |
| `tools/test_huangzhong_ops.py` | 黄忠运维 CLI 单测（tmp 夹具，绝不触碰真实 work/ 或 dev_events）。 |
| `tools/test_huangzhong_peer_verify.py` | huangzhong_peer_verify.py 单测（tmp 夹具；不读真实 work/、不写真实 deliverables）。 |
| `tools/test_huangzhong_perf_signal.py` | huangzhong_perf_signal.py 单测（tmp 夹具 + fake _http；**禁用真实网络**）。 |
| `tools/test_huangzhong_policy.py` | 黄忠策略即代码（P1-A · WO-HZ-POLICY-AS-CODE-20261031）单测。 |
| `tools/test_huangzhong_review.py` | 黄忠认证 v2 六条复核工具单测（tmp 夹具，不依赖真实 work/）。 |
| `tools/test_huangzhong_risk_coverage.py` | WO-HZ-RISKCOV-20261031 单测：黄忠风险加权覆盖率（fake _run + tmp 夹具，不碰真实数据）。 |
| `tools/test_huangzhong_security_signal.py` | 黄忠安全扫描信号（P2-E · WO-HZ-SEC-20261031）单测。 |
| `tools/test_ide_fs.py` | — |
| `tools/test_infra_2wk.py` | 工程基建 2 周项单测：日志轮转 + 测试基线工具。 |
| `tools/test_jev_night_window.py` | jev_night 时间窗回归（K-ORG-20261025-JEVNIGHT）。 |
| `tools/test_keypool_status.py` | — |
| `tools/test_legion_tri_party_material_version.py` | 会议材料指纹和并发确认的离线反例。 |
| `tools/test_legion_tri_party_meeting.py` | 三方会议阶段一离线测试：不触网、不启动服务、不创建工单。 |
| `tools/test_legion_worker.py` | — |
| `tools/test_litellm_route.py` | — |
| `tools/test_mcp_security_check.py` | D4：MCP 安全自查工具单测。 |
| `tools/test_memory_rag.py` | — |
| `tools/test_mission_evolver.py` | — |
| `tools/test_mission_evolver_quality.py` | — |
| `tools/test_mission_mvp.py` | — |
| `tools/test_mission_planner_exact.py` | — |
| `tools/test_mission_worker.py` | — |
| `tools/test_mission_worker_check_edge.py` | — |
| `tools/test_mission_worker_cmd_guard.py` | — |
| `tools/test_model_allocator.py` | — |
| `tools/test_model_dialog_catalog.py` | — |
| `tools/test_nav_skip_order.py` | skip link 必须在统一顶栏之前（真实 Tab 顺序回归）。 |
| `tools/test_nav_syntax.py` | nav.js 生成物语法烟测（Node 可用时执行；避免浏览器整栏静默失效）。 |
| `tools/test_navroot_dedup.py` | WO-NAVROOT-DEDUP-20261031 回归测试：导航门分支感知 + devtask GATES 单一定义。 |
| `tools/test_night_batch_stall.py` | night_batch 无推进退避重试 + 2026-10-25 EXEC-R13 超时改造回归。 |
| `tools/test_night_dep_age.py` | O8 P3c 夜跑 dep_age 段单测（不联网）。 |
| `tools/test_ollama_lock_deadline.py` | OS-FIX-JEVLOCK-20261026：ollama_lock.tick 阻塞上限回归。 |
| `tools/test_ollama_rag_smoke.py` | — |
| `tools/test_os_entry_ui_state.py` | OS entry Week2 契约：tokens/状态模板/键盘入口。 |
| `tools/test_p0_eval_runner.py` | P0 eval judge 规则回归：拒绝答案不应被 citation_required 误判。 |
| `tools/test_p0_shadow_guard.py` | p0_shadow_guard 单测（P0-5）。 |
| `tools/test_p0_trace.py` | p0_trace 单测（P0-6）：离线、无 Phoenix 依赖。 |
| `tools/test_p1_closed_loop.py` | 端到端闭环演练（合成数据、临时仓库；不碰生产账本）。 |
| `tools/test_p1_rag_observe.py` | p1_rag_observe 单测（离线，临时 JSONL）。 |
| `tools/test_page_shell.py` | W1 PageShell v1 契约。 |
| `tools/test_page_shell_gen.py` | PageShell v2 生成器契约。 |
| `tools/test_page_shell_w4.py` | W4 PageShell adopted/redirect 契约。 |
| `tools/test_paper_dual.py` | 模拟仓深色工作台与双版本 UI 规范的契约。 |
| `tools/test_paper_simple.py` | P0 /paper 首屏减法契约。 |
| `tools/test_phoenix_start.py` | Phoenix 自启幂等化单测（不联网、不真起进程）。 |
| `tools/test_picks_simple.py` | P2 /picks 默认精简契约。 |
| `tools/test_plan_status_lint.py` | test_plan_status_lint.py — 计划 STATUS lint 测试（tmp 假计划目录，不写真实 docs/plans）。 |
| `tools/test_planner_map2.py` | — |
| `tools/test_planner_paths_v3.py` | — |
| `tools/test_planner_paths_v4.py` | — |
| `tools/test_planner_rules.py` | — |
| `tools/test_post_commit_event.py` | post-commit 事件适配器测试。 |
| `tools/test_product_metrics.py` | — |
| `tools/test_project_wiki.py` | project_wiki 生成器契约。 |
| `tools/test_quality_scale_check.py` | O2 质量分级检查器单测。 |
| `tools/test_quant_redteam_ops.py` | P2 单测：量化只读（fake MCP）+ 红队定时（fake responder/临时账本）。 |
| `tools/test_r2fix_guards.py` | K-QUANT-20261026-R2FIX：畸形上游输入守卫回归测试。 |
| `tools/test_rebuild_academy_source_snapshot.py` | — |
| `tools/test_redteam_rotation.py` | 诸葛亮红队每日轮转：计划任务命令不变（--limit 2），代码每天换下一组。 |
| `tools/test_redteam_weekly_empty.py` | 红队周报空窗口回归：无运行记录时不得把列表传给 %d。 |
| `tools/test_req_lines_check.py` | O8 P2 分线 lock 校验器单测。 |
| `tools/test_retro_brief.py` | R5 复盘回流生成器单测。 |
| `tools/test_run_contract_view.py` | O6 P1 只读派生单测（不依赖真实 trace DB）。 |
| `tools/test_run_contract_view_p2d.py` | O6 P2d：run_contract_view 任务级聚合单测。 |
| `tools/test_sandbox_verify.py` | — |
| `tools/test_seat_capability_inject.py` | 三席位能力/技能卡结构测试。 |
| `tools/test_seat_evidence_packet.py` | seat_evidence_packet 单测（K-QUANT-20261026-TFEVID）。 |
| `tools/test_seat_memory_sync.py` | test_seat_memory_sync.py — 三层记忆装配器测试。 |
| `tools/test_seat_router.py` | — |
| `tools/test_seat_router_ops.py` | — |
| `tools/test_seat_system_audit.py` | test_seat_system_audit.py — 席位完整性审计工具测试。 |
| `tools/test_seat_write_guard.py` | seat_write_guard 检测层单测（TFGUARD）。 |
| `tools/test_security_gate.py` | — |
| `tools/test_security_gate_iter.py` | — |
| `tools/test_security_gate_vendor.py` | — |
| `tools/test_spec_flow.py` | — |
| `tools/test_strategies_simple.py` | P1 /strategies 分组折叠契约。 |
| `tools/test_sync_pipeline_to_strategies.py` | sync_pipeline_to_strategies 单测：全 tmp_path 替身，禁碰真实 portal/strategies.json。 |
| `tools/test_t2_manual.py` | — |
| `tools/test_t2_manual2.py` | — |
| `tools/test_tianfeng_dispatch.py` | tianfeng_dispatch 派工回执台账单测（TFEXEC）。 |
| `tools/test_trace_chain.py` | shadow -> paper pending/fill -> performance 的离线追踪链验证。 |
| `tools/test_trace_log.py` | — |
| `tools/test_trace_ops.py` | trace_ops 单测：汇总/告警，不打 Phoenix。 |
| `tools/test_ui_keyboard_audit.py` | ui_keyboard_audit 单测。 |
| `tools/test_ui_palette.py` | Week3 命令面板/密度/键盘审计契约。 |
| `tools/test_ui_ux_audit.py` | ui_ux_audit 单测（离线）。 |
| `tools/test_ui_visual_baseline.py` | ui_visual_baseline 单测（不启动浏览器）。 |
| `tools/test_ui_visual_gate.py` | 组件视觉基线 warn-only 门契约。 |
| `tools/test_weekly_dep_triage.py` | O8 P3d 依赖 triage 周报段单测（不联网）。 |
| `tools/test_wip_check.py` | §6.2 WIP 检查单测。 |
| `tools/test_worker_eligibility.py` | — |
| `tools/test_worker_llm_parse.py` | — |
| `tools/test_zhuge_brief_digest.py` | 两期摘要器契约：P4 折叠、去内部 source、晨/晚报切换、长度上限。 |
| `tools/test_zhuge_brief_push.py` | zhuge_brief_push dry-run 契约：默认不发送、只校验快照。 |
| `tools/test_zhuge_brief_snapshot.py` | zhuge_brief_snapshot 单测（K-QUANT-20261026-ZHUGESNAP）。 |
| `tools/test_zhuge_c1_decisions.py` | zhuge_c1_decisions 单测（C1 ↔ 决策记录，离线）。 |
| `tools/test_zhuge_c3_decisions.py` | zhuge_c3_decisions 单测（C3 采纳 ↔ 决策记录，离线）。 |
| `tools/test_zhuge_carrier_switch.py` | zhuge_carrier_switch.ps1 干跑用例（不联网、不改配置）。 |
| `tools/test_zhuge_challenge.py` | zhuge_challenge 单测（WO-LEGION-ZHUGE-CHALLENGE-20261027 / C1）。 |
| `tools/test_zhuge_challenge_model.py` | zhuge_challenge_model 单测（P2 真模型质询，2026-10-27）。 |
| `tools/test_zhuge_debate.py` | P2 三席辩论单测（确定性，不联网）。 |
| `tools/test_zhuge_decision_record.py` | zhuge_decision_record v0 单测（离线，临时仓库）。 |
| `tools/test_zhuge_evidence.py` | zhuge_evidence 单测（P0-2/P0-3）。 |
| `tools/test_zhuge_grounding.py` | zhuge_grounding 单测（P1-1）。离线：临时 docs + mock post。 |
| `tools/test_zhuge_mcp_server.py` | zhuge_mcp_server 单测（P0-1）。 |
| `tools/test_zhuge_memory.py` | zhuge_memory v0 单测（离线，临时仓库）。 |
| `tools/test_zhuge_p1.py` | P1 接线：RAG auto / decisions→memory seed / MCP 只读工具存在。 |
| `tools/test_zhuge_p1_567.py` | P1-6/P1-7 离线单测：规划分解 + 自我反思（不联网/不调模型）。 |
| `tools/test_zhuge_p1x.py` | P1X：RAG 自动重建 + 引用评分契约。 |
| `tools/test_zhuge_rag.py` | zhuge_rag BM25 v0 单测（离线，临时 docs）。 |
| `tools/test_zhuge_redact.py` | P1-8 修复单测：L5 输出内部路径脱敏。 |
| `tools/test_zhuge_redteam.py` | P1-8 红队判定器离线单测（fake responder，不调模型）。 |
| `tools/test_zhuge_suggestion_map.py` | 建议→决策映射 v1：决策记录 suggestion_ids + brief 精确 join（临时仓库）。 |
| `tools/test_zhuge_tool_router.py` | L5 只读工具编排：URL 取证 / MCP 意图 / 无意图不调用。 |
| `tools/test_zhuge_wargame.py` | P2 兵棋单测（确定性，不联网）。 |
| `tools/tests/test_a0_patrol_decision.py` | a0_patrol 决策记忆最小修复测试（P1-5 W4，2026-10-31）。 |
| `tools/tests/test_acad1_candidate_preflight.py` | ACAD1 候选生成门预检测试（WO-ACAD1-PREFLIGHT-20261031）。 |
| `tools/tests/test_acad1_pool_adapter.py` | acad1_pool_adapter：ACAD1 池只读适配器离线单测（临时 parquet 夹具，不依赖生产数据仓）。 |
| `tools/tests/test_acad1_v2_export.py` | acad1_v2_export 测试（WO-ACAD1-BRIDGE-EXPORT-20261101）。 |
| `tools/tests/test_academy_activation4.py` | B 激活 4 席：能力卡定义/触发/工具属性测试（2026-10-29）。 |
| `tools/tests/test_academy_ledger_check.py` | academy_ledger_check 单测（2026-10-30 P99）。 |
| `tools/tests/test_agent_census_parse.py` | agent_census 时间解析：ISO 带时区/微秒不再 silent-except。 |
| `tools/tests/test_agent_census_status.py` | 系统态势卡的纯函数测试（K-ORG-20261026-BOARD2）。 |
| `tools/tests/test_agent_daily_on_demand.py` | on_demand 证据类型单测（K-ORG-20261026-AGENTHEALTH2）。 |
| `tools/tests/test_agent_daily_port.py` | agent.daily 端口证据接线单测（K-ORG-20261026-AGENTHEALTH）。 |
| `tools/tests/test_agent_obs_export.py` | tests for tools/agent_obs_export.py（OTel GenAI 形态导出）。 |
| `tools/tests/test_api_jev_incremental.py` | 增量事件流（since/cursor）测试：纯函数 + 临时 news.db 集成。 |
| `tools/tests/test_api_kb_qa.py` | api_kb_qa（知识库问答 HTTP handler 预接线）契约测试 —— 不调真实 LLM、不启动/重启服务。 |
| `tools/tests/test_attribution_repair.py` | 归因缺口修复映射单测（2026-10-29）。 |
| `tools/tests/test_auto_rebuild_ide_bundle.py` | IDE bundle 自动重建单测（2026-10-29 并发修复 → 2026-10-31 Phase2 去 tracked）。 |
| `tools/tests/test_batch1_audit.py` | BATCH1 回归：posterior 导出契约、T+1 信号日口径、覆盖率/门控字段。 |
| `tools/tests/test_batch_backlog_level.py` | batch_backlog 判级单测（K-ORG-20261026-BACKLOGDUTY）。 |
| `tools/tests/test_book_batch_test.py` | tools/tests/test_book_batch_test.py · 批量四关初筛（tools/research/book_batch_test.py）单元测试。 |
| `tools/tests/test_bt_harness.py` | 统一回测口径 bt_harness 契约测试（合成行情，不依赖真实数据仓）。 |
| `tools/tests/test_bundle_bootstrap.py` | K-ORG-20261031-BUNDLEB5：bundle_bootstrap 幂等/失败可见/无写入测试。 |
| `tools/tests/test_bundle_phase2_dryrun.py` | K-ORG-20261031-BUNDLEP2：bundle Phase2 dry-run 规划器测试（只读/零写入）。 |
| `tools/tests/test_bundle_phase2b.py` | K-ORG-20261031-BUNDLEP2B：bundle Phase2-B（原地去 tracked）落地状态单测。 |
| `tools/tests/test_c6_attribution.py` | C6 归因修复映射 + devtask 署名强制单测（2026-10-29）。 |
| `tools/tests/test_c6_paper.py` | C6 paper 指标测试（2026-10-30 WO-INV-PAPER-OBS）。 |
| `tools/tests/test_candidate_evidence_guard.py` | candidate_evidence_guard：W3 候选证据包只读校验器测试（全离线 mock）。 |
| `tools/tests/test_candidate_generation_guard.py` | 候选池生成层门测试（WO-CAND-GEN-GUARD-20261031 / WO-GUARD-PROVENANCE-20261101）。 |
| `tools/tests/test_card_apply_round2.py` | 100 卡 apply+merge 执行验证测试（task-99）。 |
| `tools/tests/test_card_concept_fix.py` | 2 张 CP3 卡概念对齐修复测试（task-121）。 |
| `tools/tests/test_card_decisions_round2.py` | 100 卡建议 decisions 二轮 dry-run 测试（task-97）。 |
| `tools/tests/test_card_decisions_suggested.py` | 建议 decisions + dry-run 预览（wave19 BF）回归测试。 |
| `tools/tests/test_card_dedupe.py` | 「涨停密码」重复卡合并执行测试（task-105）。 |
| `tools/tests/test_card_from_units.py` | S5 草稿→学习卡候选测试（task-130）。 |
| `tools/tests/test_card_merge.py` | 学习卡合并工具（card_merge）回归测试。 |
| `tools/tests/test_card_precheck.py` | 学习卡机器预审（card_precheck）回归测试。 |
| `tools/tests/test_card_precheck_v3.py` | 第二轮 40 候选机器预审（card_precheck_v3）回归测试。 |
| `tools/tests/test_card_proposal.py` | 建议学习卡审核稿（card_proposal）回归测试。 |
| `tools/tests/test_card_proposal_v2.py` | 零卡主题概念卡候选（card_proposal_v2）回归测试。 |
| `tools/tests/test_card_proposal_v3.py` | 学习卡第二轮扩卡（card_proposal_v3）回归测试。 |
| `tools/tests/test_card_proposal_v4.py` | 学习卡第三轮扩卡候选测试（task-126）。 |
| `tools/tests/test_card_quality_audit.py` | 124 张学习卡质量复核测试（task-118，含新增 20 张 CP3）。 |
| `tools/tests/test_card_review.py` | 学习卡审核工作流（card_review）回归测试。 |
| `tools/tests/test_card_review_page.py` | 学习卡审核页（wave21 CD）回归测试。 |
| `tools/tests/test_card_revision.py` | 学习卡 revise 修订建议（card_revision）回归测试。 |
| `tools/tests/test_card_revision_apply.py` | 学习卡修订落地（card_revision_apply）回归测试。 |
| `tools/tests/test_card_revision_round2.py` | 第二轮 45 张 revise 修订建议稿测试（task-111）。 |
| `tools/tests/test_card_round2rev_apply.py` | 学习卡第二轮 revise 落地执行测试（task-115）。 |
| `tools/tests/test_card_round3_apply.py` | 第三轮 24 候选落地测试（task-127）。 |
| `tools/tests/test_channel_capacity.py` | tools/tests/test_channel_capacity.py — 通道容量/限流语义复核（task-122）。 |
| `tools/tests/test_channel_limit.py` | tools/tests/test_channel_limit.py — single/multi 通道限流复核（task-119）。 |
| `tools/tests/test_check_dirty.py` | check_dirty 纯逻辑单测（2026-10-28，P15）。 |
| `tools/tests/test_check_syntax.py` | check_syntax 单测（临时 repo 夹具，禁用缓存写入）。 |
| `tools/tests/test_check_task_artifact.py` | Phase D1：evidence artifact 校验门测试。 |
| `tools/tests/test_citation_retry.py` | v4 引用自检重试（citation_retry）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_citation_retry_scale.py` | citation_retry 扩样 A/B 契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_concept_dedup.py` | 同 concept 重复单元审计（concept_dedup）回归测试。 |
| `tools/tests/test_curated_update_tables.py` | IDXDAILY 回归：curated 日更必须覆盖 index_daily（JEV 基准）。 |
| `tools/tests/test_daily_kb.py` | 知识库每日自动化编排器（daily_kb）回归测试。 |
| `tools/tests/test_daily_screening.py` | tools/tests/test_daily_screening.py — 每日筛选流水线（dry-run，不注册定时任务）。 |
| `tools/tests/test_daily_screening_report.py` | 每日筛选正式输出：日报摘要 + 门户静态 JSON（task-101）。 |
| `tools/tests/test_daily_tasks_health.py` | tools/tests/test_daily_tasks_health.py — 每日任务健康监控 + 告警（task-108）。 |
| `tools/tests/test_daily_three_ledger.py` | daily_three_ledger：日终三账快照测试（全离线 mock，不碰生产/不跑三工具）。 |
| `tools/tests/test_devtask_branch_gate.py` | 质量闸作用域单测（2026-10-30 · 分支级闸）。 |
| `tools/tests/test_devtask_composition_gate.py` | P1.2 测试：组合门 + kernel guard 接入 devtask.merge / merge_preflight（ADR-015）。 |
| `tools/tests/test_devtask_gates.py` | devtask 评审门注册测试（P74：CIGATES 三道门恢复）。 |
| `tools/tests/test_devtask_git_health.py` | P140：devtask 启动级 core.bare 自愈守卫。 |
| `tools/tests/test_devtask_merge_lock.py` | devtask 合并锁单测（2026-10-29 · 防并发 HEAD 竞争）。 |
| `tools/tests/test_devtask_merge_retry.py` | devtask merge HEAD 竞态重试单测（fake git，不跑真实合并）。 |
| `tools/tests/test_devtask_worktree_mech.py` | WO-WORKTREE-MECH-20261031 回归测试：merge 自动回收 / TTL 红灯 / 并发上限。 |
| `tools/tests/test_devtask_wtcheck.py` | WO-DEVTASK-WTCHECK-20261031 回归测试：W2-3 worktree 校验（audit-worktrees / list --strict）。 |
| `tools/tests/test_eval_assets.py` | tools/tests/test_eval_assets.py — 评测资产 hash 守卫（wave15 支线 AS，轻量质量门）。 |
| `tools/tests/test_eval_audit.py` | tools/tests/test_eval_audit.py — 两阶段偏差审计（wave11 支线 AF）单元测试。 |
| `tools/tests/test_eval_registry.py` | tools/tests/test_eval_registry.py — 评测集资产化（wave14 支线 AP）单元测试。 |
| `tools/tests/test_eval_report_header.py` | tools/tests/test_eval_report_header.py — 评测报告头工具（wave15 支线 AS）。 |
| `tools/tests/test_eval_v3_baseline.py` | tools/tests/test_eval_v3_baseline.py — 评测基线 v3 全量回归（wave21 支线 CF）。 |
| `tools/tests/test_eval_v3_merge.py` | tools/tests/test_eval_v3_merge.py — 评测集 v3 + 合并复评（wave19 支线 BG）。 |
| `tools/tests/test_event_emit.py` | R6 产线侧 emit 配方单测（tools/event_emit.py）。 |
| `tools/tests/test_evidence_chain_audit.py` | evidence_chain_audit：P0 evidence 链式审计器离线单测（不依赖生产 evidence）。 |
| `tools/tests/test_factor_diagnostics.py` | 脱机因子诊断工具契约测试（WO-STRAT-FDIAG-20261101 / task-24）。 |
| `tools/tests/test_factor_gate.py` | 因子诊断准入 preflight 契约测试（WO-STRAT-FGATE-20261101 / task-27）。 |
| `tools/tests/test_factor_lib.py` | tools/tests/test_factor_lib.py · 因子库（tools/research/factor_lib.py）单元测试。 |
| `tools/tests/test_factor_panel_validate.py` | 真实因子面板验证运行器契约测试（WO-VAL-FACTOR-20261101 / task-32）。 |
| `tools/tests/test_factor_validate.py` | tools/tests/test_factor_validate.py — 支线 E 因子独立验证（tools/research/factor_validate.py）单元测试。 |
| `tools/tests/test_file_lock.py` | file_lock 单测（tmp 锁路径）。 |
| `tools/tests/test_full_universe_baseline.py` | 全量选股 Phase A 基线工具测试（WO-STRAT-FULL-BASE-20261102）。离线纯函数。 |
| `tools/tests/test_git_worktree_guard.py` | git_worktree_guard 单测（2026-10-31 P110）。 |
| `tools/tests/test_guard_marker.py` | guard 标记解析（marker_mode）契约测试 —— 纯离线、不调 LLM。 |
| `tools/tests/test_guard_review.py` | guard 拦截样本人工抽查清单（wave20 CC）回归测试。 |
| `tools/tests/test_guard_threshold.py` | guard_threshold_study（guard 阈值敏感性）契约测试 —— 纯离线、不调 LLM。 |
| `tools/tests/test_guard_threshold_ab.py` | guard_threshold_ab（0.6 vs 0.7 扩样 A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_hermes_push_health.py` | Hermes 推送健康判级单测（K-ORG-20261026-HERMESFIX）。 |
| `tools/tests/test_hk_r1_daily.py` | HK R1 每日跑批脚本单测（解析/干跑/台账表格插入，不执行真实跑批）。 |
| `tools/tests/test_huangzhong_tools_suite_gate.py` | fast 门纳管：黄忠 13 个 tools 工具测试聚合执行（WO-HZ-GATE-20261031）。 |
| `tools/tests/test_hypothesis_event_study.py` | 只读事件研究 harness 契约测试（WO-STRAT-EVENTSTUDY-20261101 / task-29）。 |
| `tools/tests/test_import_boundaries.py` | 导入边界联动检查单测（临时模块/测试夹具）。 |
| `tools/tests/test_index_governance_assets.py` | index_governance_assets 生成物分类单测（K-ORG-20261031-PLANREG，2026-10-31）。 |
| `tools/tests/test_industry_rs_factor.py` | tools/tests/test_industry_rs_factor.py · 行业相对强度因子（RS）单元测试。 |
| `tools/tests/test_intel_ai_first_pass.py` | intel_ai_first_pass 回归：映射/迁移/备份/事务/不覆盖非 pending（临时 DB）。 |
| `tools/tests/test_intel_batch_preflight.py` | 批次入库前质量闸离线测试（不写库/不联网）。 |
| `tools/tests/test_intel_human_batch_apply.py` | 人工复核 CSV 校验/回填器离线测试（WO-INTEL-HUMANAPPLY）。 |
| `tools/tests/test_intel_pre_review.py` | intel_pre_review 机器预审回归：打分/排序稳定、上限、只读、fail-visible。 |
| `tools/tests/test_intel_review_archive.py` | intel_review_archive 分层归档回归：规则/只读 dry-run/备份/幂等（临时 DB，不碰生产）。 |
| `tools/tests/test_intel_review_batch.py` | intel_review_batch 人工复核批次回归：选批/降级/只读/产物字段。 |
| `tools/tests/test_intel_source_health.py` | tools/intel_source_health.py + ops/run_intel_pre_review.ps1 测试 |
| `tools/tests/test_intel_sources.py` | tools/intel_sources.py 测试（Phase 2，WO-INTEL-SOURCES-20261031）。 |
| `tools/tests/test_jev_audit.py` | JEV 判读账本纯函数单测（K-DATA-20261026-JEVAUDIT）。 |
| `tools/tests/test_jev_backfill_loop.py` | JEV 回填主循环控制函数单测（K-ORG-20261026-JEVLOOPTEST）。 |
| `tools/tests/test_jev_calibration.py` | jev.calibration 健康卡检查单测（K-ORG-20261026-JEVCALHC）。 |
| `tools/tests/test_jev_companies_audit.py` | companies 质量门离线测试（只读，tmp DB/master）。 |
| `tools/tests/test_jev_eval_harness.py` | JEV-Eval 纯函数单测（JEVEVAL）。 |
| `tools/tests/test_jev_m3skip.py` | M3SKIP：M3 活跃时 JEV 小时轮预检让位。 |
| `tools/tests/test_jev_mcp.py` | JEV MCP 单测（BATCH3）：JSON-RPC 参数校验(-32602)、计数措辞、低信号消费。 |
| `tools/tests/test_jev_night_preflight.py` | PREFLIGHT 纯函数单测。 |
| `tools/tests/test_jev_night_summary.py` | NIGHTSUM 纯函数单测。 |
| `tools/tests/test_jev_night_window.py` | BATCH2 回归：夜跑跨午夜窗口、暂停旗 owner。 |
| `tools/tests/test_jev_night_wrapper.py` | Static guard for jev-eval/start_jev_night.ps1 (K-ORG-20261029-JEVGATEFIX). |
| `tools/tests/test_jev_prefilter.py` | jev_prefilter 规则单测（K-ORG-20261026-JEV2STAGE）。 |
| `tools/tests/test_jev_prefilter_eval.py` | prefilter 评估纯函数单测（PREFILTEREVAL）。 |
| `tools/tests/test_jev_prefilter_revert.py` | jev_prefilter_revert 单测（BATCH4）：只回退 src=prefilter 行。 |
| `tools/tests/test_jev_reason_merge.py` | BATCH4 回归：reason 并入本地判定调用（不再每条多调一次模型）。 |
| `tools/tests/test_jev_review_loop.py` | JEV review 闭环纯函数单测。 |
| `tools/tests/test_jev_risk_alert.py` | JEV 风险告警桥纯函数单测（JEVALERT）。 |
| `tools/tests/test_jev_roles_daily.py` | jev_roles_daily 纯函数单测（K-ORG-20261026-JEVROLE）。 |
| `tools/tests/test_jev_v2_audit.py` | 契约 v2 账本列测试：horizon/prob 迁移与写入。 |
| `tools/tests/test_kb_daily_push.py` | 知识库每日推送准备（kb_daily_push）回归测试。 |
| `tools/tests/test_kb_daily_push_send.py` | kb_daily_push.send_payload：真实发送接线（mock 不联网）。 |
| `tools/tests/test_kb_qa.py` | kb_qa（研报知识库 RAG 问答）契约测试 —— 不调真实 LLM（检索可用真实知识库只读检索）。 |
| `tools/tests/test_kb_qa_bank.py` | kb_qa_bank（问答候选问题库生成器）契约测试 —— 默认不调 LLM。 |
| `tools/tests/test_kb_qa_bank_v2.py` | kb_qa_bank_v2（问题模板 v2）契约测试 —— mock 不调真模型。 |
| `tools/tests/test_kb_qa_bank_v3.py` | kb_qa_bank_v3（claim 覆盖度过滤 + 可答率扩验）契约测试 —— mock 不调真模型。 |
| `tools/tests/test_kb_qa_eval.py` | tools/tests/test_kb_qa_eval.py — 问答答案质量评估（wave16 支线 AU）单元测试。 |
| `tools/tests/test_kb_qa_eval_v2.py` | tools/tests/test_kb_qa_eval_v2.py — schema v2 + 幻觉规则调优（wave17 支线 AX）。 |
| `tools/tests/test_kb_qa_expand.py` | kb_qa_expand（问答样本扩量）契约测试 —— mock 不调真模型、不写真实门户文件。 |
| `tools/tests/test_kb_qa_guard.py` | kb_qa_guard（multi 质量守卫）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_multi.py` | kb_qa_multi（多单元聚合回答 MVP）契约测试 —— mock 不调真模型。 |
| `tools/tests/test_kb_qa_multi_ab.py` | kb_qa_multi_ab（多单元聚合 A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_multi_ab50.py` | kb_qa_multi_ab50（50 条 A/B 复跑）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_multi_eval.py` | tools/tests/test_kb_qa_multi_eval.py — multi 答案质量评估（wave18 支线 BB）。 |
| `tools/tests/test_kb_qa_page.py` | kb_reports.html 问答示例（静态）区块契约测试 —— 只读页面/静态 JSON，不改数据、不调服务。 |
| `tools/tests/test_kb_qa_prompt_v2.py` | kb_qa_prompt_v2（prompt v2 A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_prompt_v3.py` | kb_qa_prompt_v3（先事实后推断 + few-shot A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_prompt_v3_35b.py` | kb_qa_prompt_v3_35b（35b 模型 prompt v3 A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_prompt_v4.py` | kb_qa_prompt_v4（prompt v4 + guard 分段解析）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_prompt_v4_scale.py` | kb_qa_prompt_v4_scale（prompt v4 扩样 A/B）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_qa_rank.py` | kb_qa_rank（检索 @1 重排评估）契约测试 —— 不调 LLM、不改 kb_qa.py。 |
| `tools/tests/test_kb_qa_samples_v4.py` | tools/tests/test_kb_qa_samples_v4.py — 门户问答样例刷新到 v4 默认（task-112）。 |
| `tools/tests/test_kb_qa_selfcheck.py` | kb_qa_selfcheck（问题库 retrieval-only 自检）契约测试 —— 不调 LLM。 |
| `tools/tests/test_kb_qa_v4_default_recheck.py` | kb_qa_v4_default_recheck（v4 默认灰度复评）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_kb_read_api.py` | 知识库只读接口契约测试（不联网、不写盘）。 |
| `tools/tests/test_kb_search_frontend.py` | tools/tests/test_kb_search_frontend.py — 研报检索 v2 前端灰度（portal/kb_reports.html）。 |
| `tools/tests/test_killsvc_guard.py` | Hermes8787-KillSvc Gate 0 前置逻辑层单测（R7）。 |
| `tools/tests/test_l2_guard_fp.py` | _l2_guard 误报修复回归（WO-GUANYU-L2FIX-20261027，2026-10-27）。 |
| `tools/tests/test_learning_analysis.py` | 学习卡质量与主题覆盖分析（learning_analysis）回归测试。 |
| `tools/tests/test_learning_cards_preserve.py` | learning_cards.merge_cards：重跑生成器不得覆盖人工合并/修订卡。 |
| `tools/tests/test_learning_dashboard.py` | 学习项目看板（聚合数据 + 门户页 + 导航注册）契约测试。 |
| `tools/tests/test_learning_digest.py` | 学习卡/简报门户化（learning_digest + kb_reports.html 区块）回归测试。 |
| `tools/tests/test_learning_intake.py` | learning_intake：docx 抽取/幂等/安全白名单/不碰数据仓。 |
| `tools/tests/test_learning_intake_watch.py` | learning_intake_watch：扫描/比对/dry-run 不写。 |
| `tools/tests/test_learning_ops.py` | learning_ops：pipeline 草稿/治理问题/标准化/安全白名单。 |
| `tools/tests/test_legacy_scan.py` | legacy_scan 引用链扫描测试（P0-2）：0 引用 → L3，仅文档 → L2，代码引用 → referenced。 |
| `tools/tests/test_log_rotation_wiring.py` | JEVROTATE 回归：JevDriftDaily wrapper 必须接线 rotate_logs（每日轮转）。 |
| `tools/tests/test_lowturn_attribution.py` | tools/tests/test_lowturn_attribution.py — 支线 J low_turnover 逐年/regime 归因单元测试。 |
| `tools/tests/test_lowturn_freq.py` | tools/tests/test_lowturn_freq.py — 支线 G 低频/成本优化验证单元测试。 |
| `tools/tests/test_lowturn_residual.py` | tools/tests/test_lowturn_residual.py — 支线 M 双重中性化残差检验（tools/research/lowturn_residual.py）。 |
| `tools/tests/test_lx20_avoid_rule.py` | tools/tests/test_lx20_avoid_rule.py · LX20 规避规则深化（tools/research/lx20_avoid_rule.py）单元测试。 |
| `tools/tests/test_m1_evidence_pack.py` | m1_evidence_pack：M1 信任链证据包聚合器测试（全离线 mock，不碰生产/不跑三工具）。 |
| `tools/tests/test_manual_assets_safe.py` | 人工资产写入加固测试（wave29 CY）：learning_cards / kb_units_snapshot。 |
| `tools/tests/test_medium_writers_safe.py` | tools/tests/test_medium_writers_safe.py — 中风险生成类脚本补原子写（wave29 支线 DA）。 |
| `tools/tests/test_merge_dirty_guard.py` | P49：devtask merge 脏文件快速失败守卫测试。 |
| `tools/tests/test_merge_gate_trust.py` | H5/A9 合并门可信化单测（K-ORG-20261031-MERGEGATE，2026-10-31）。 |
| `tools/tests/test_merge_gated.py` | merge_gated 纯逻辑单测（2026-10-28，P7）。 |
| `tools/tests/test_merge_lock.py` | merge 串行锁单测（tmp 锁路径，不碰真实 C:\Temp）。 |
| `tools/tests/test_merge_lock_gated.py` | merge_gated 兼容预检器单测（P37 后真实闸门在 devtask merge；此处只测预检语义）。 |
| `tools/tests/test_merge_preflight.py` | merge_preflight 纯逻辑单测（2026-10-28，P13；P3c 依赖提示）。 |
| `tools/tests/test_merge_sim.py` | concept 合并影响模拟（merge_sim）回归测试。 |
| `tools/tests/test_merge_sim_89.py` | 89 查询合并影响模拟（merge_sim_89）回归测试。 |
| `tools/tests/test_merge_train.py` | W1/UF-09 merge train 门测试（tools/merge_train.py）。 |
| `tools/tests/test_merge_train_gate.py` | W1/UF-09 merge train 接入测试：devtask merge 门 + merge_preflight 钩子（task-19）。 |
| `tools/tests/test_metadata_daily.py` | 元数据日报（P0-1）纯函数测试：summarize / render_line 异常判定与摘要格式。 |
| `tools/tests/test_mixin_root.py` | 分支感知 Mixin 门单测（2026-10-30 P103）。 |
| `tools/tests/test_new_writers_safe.py` | tools/tests/test_new_writers_safe.py — 新增 3 个写盘脚本加固（wave32 / task-93）。 |
| `tools/tests/test_ollama_lock_owner.py` | BATCH2 回归：ollama_lock 所有权校验。 |
| `tools/tests/test_ops_tools.py` | 四个运维工具的 pytest 契约测试（只新增测试，不改被测代码）。 |
| `tools/tests/test_org_model_consistency.py` | org_model_consistency（K-ORG-20261031-ORMMODEL）单测。 |
| `tools/tests/test_outcome_contract_check.py` | outcome_contract_check：W3 outcome 契约只读检查器离线单测（不依赖生产账本）。 |
| `tools/tests/test_panel_daily.py` | panel_daily（研报面板日常化运行器）契约测试 —— 不调 LLM、不写真实门户目录。 |
| `tools/tests/test_panel_export.py` | panel_export（研报面板 → 门户静态 JSON + selection.html 区块）契约测试 —— 不调 LLM、不写门户目录。 |
| `tools/tests/test_pdf_ingest_contract.py` | PDF 知识入库能力 · 契约测试（不跑 OCR，只校验接口/标准/注册）。 |
| `tools/tests/test_pip_osv_audit.py` | pip_osv_audit 单测：冻结清单解析 + OSV 结果聚合（mock urlopen）。 |
| `tools/tests/test_pit_utils.py` | PIT 工具契约：上市天数按信号日计算、次新股排除、BJ 排除。 |
| `tools/tests/test_plan_register.py` | plan_register 台账生成器单测（2026-10-30 P90）。 |
| `tools/tests/test_platform_composition.py` | P1 组合层工具测试：composition.py（validate/plan/diff/retire）+ kernel_guard.py。 |
| `tools/tests/test_portal_health.py` | tools/tests/test_portal_health.py — 全站巡检 + 静态契约 + 系统总览（task-114）。 |
| `tools/tests/test_portal_route_inventory.py` | portal_route_inventory 单测（A5 B0，2026-10-31）。 |
| `tools/tests/test_portal_route_table_draft.py` | 路由表草案生成器单测 + 与 B0 路由基线一致性测试（A5 B4/B5，2026-10-31）。 |
| `tools/tests/test_prefilter_on.py` | PREFILTERON 回归：JEV 子进程 env 默认 safe、父值可覆盖；wrapper 静态检查。 |
| `tools/tests/test_qa_acceptance.py` | qa_acceptance（问答链路端到端验收）契约测试 —— mock 不调真 LLM。 |
| `tools/tests/test_qa_http_acceptance_live.py` | 8091 问答 HTTP 端到端验收：mock 契约测试（不真调 8091/LLM）。 |
| `tools/tests/test_qa_http_loadtest.py` | 8091 问答 HTTP 限流/压测：mock 契约测试（不真打 8091、不调 LLM）。 |
| `tools/tests/test_qa_http_recheck.py` | 8091 验收复跑（限流/性能优化后）：mock 契约测试（不真打 8091/LLM）。 |
| `tools/tests/test_qa_live_quality.py` | 8091 线上 multi 质量抽检契约测试 —— mock 不真调 8091/LLM。 |
| `tools/tests/test_qa_quality_daily.py` | 问答质量每日抽检（qa_quality_daily）契约测试 —— mock 不真调 8091/LLM。 |
| `tools/tests/test_r1_daily_check.py` | r1_daily_check：R1 恢复只读检查器测试（全离线 mock，不启动 R1/不改计划任务）。 |
| `tools/tests/test_rate_freshness.py` | 利率新鲜度检查单测（K-DATA-20261026-SHIBOR）。 |
| `tools/tests/test_release_artifact_guard.py` | K-ORG-20261031-ARTLOCK：release artifact guard 单测（tmp 假 artifact，不碰真实 _vault）。 |
| `tools/tests/test_release_bundle_provenance.py` | K-ORG-20261101-BUNDLEPROV（审计 M9）：bundle 纳入 release provenance / guard 辅助段。 |
| `tools/tests/test_release_legion_ide.py` | release_legion_ide：从 HEAD 构建/校验/指针回滚（不依赖生产）。 |
| `tools/tests/test_release_provenance.py` | release_provenance：release artifact provenance 生成/校验器测试（临时 release root，不碰生产）。 |
| `tools/tests/test_release_rollback_drill.py` | release_rollback_drill：W1 发布回滚演练工具测试（隔离根，不碰生产）。 |
| `tools/tests/test_release_sbom.py` | release_sbom：release artifact SBOM 生成/校验器测试（临时 release root，不碰生产、不联网）。 |
| `tools/tests/test_report_panel.py` | report_panel（多智能体研报评审面板）契约测试 —— 不真调 LLM。 |
| `tools/tests/test_report_panel_v2.py` | report_panel_v2（页码校验 + 双模型交叉 + 候选降维）契约测试 —— 不真调 LLM。 |
| `tools/tests/test_report_panel_v3.py` | report_panel_v3（截断/超时修复 + 精确页码 + 人工复核清单）契约测试 —— 不真调 LLM。 |
| `tools/tests/test_retrieval_perf.py` | 检索性能优化契约测试（离线，不调 8091/LLM）。 |
| `tools/tests/test_reverse_avoid.py` | tools/tests/test_reverse_avoid.py · 规避组合深测（tools/research/reverse_avoid_test.py）单元测试。 |
| `tools/tests/test_risk_bridge_wiring.py` | JEVALERTB 回归：小时轮 wrapper 必须接线风险扫描且仅写 outbox（不外发）。 |
| `tools/tests/test_route_table_equivalence.py` | B4a-lite 路由表等价性测试（2026-10-31；WO-INTEL-BASELINE-20261031 校准）。 |
| `tools/tests/test_rs_overlay_strict.py` | tools/tests/test_rs_overlay_strict.py — RS 叠加严苛复验（task-88）单元测试。 |
| `tools/tests/test_runbook.py` | 运行手册 v1 + STATUS 收口契约测试（只读文档，不调服务/LLM）。 |
| `tools/tests/test_s05_overlay.py` | tools/tests/test_s05_overlay.py · S05×S01 叠加测试（tools/research/s05_overlay_test.py）单元测试。 |
| `tools/tests/test_s05_reverse.py` | tools/tests/test_s05_reverse.py · S05 反向（超配）假设验证（tools/research/s05_reverse_hypothesis.py）单元测试。 |
| `tools/tests/test_safe_json.py` | safe_json 安全写入工具测试（wave28 CX · 中风险批次）。 |
| `tools/tests/test_safe_urlopen.py` | safe_urlopen 单测：scheme allowlist + 透传。 |
| `tools/tests/test_scan_secrets_fp.py` | scan_secrets 误报修复回归（WO-GUANYU-SCANFIX-20261027，2026-10-27）。 |
| `tools/tests/test_scan_secrets_plan_register.py` | K-ORG-20261031-PLANREG：计划台账生成物路径豁免（HIGH_ENTROPY 误报）。 |
| `tools/tests/test_screening_page.py` | tools/tests/test_screening_page.py — 每日筛选页（portal/screening.html，task-106）。 |
| `tools/tests/test_screening_rs.py` | tools/tests/test_screening_rs.py — 量学筛选 × 板块 RS 叠加（RS 产品化）。 |
| `tools/tests/test_search_v2.py` | tools/tests/test_search_v2.py — 研报检索 v2（tools/kb_corpus/search_v2.py）单元测试。 |
| `tools/tests/test_search_v2_eval_ext.py` | tools/tests/test_search_v2_eval_ext.py — 检索 v2 评测扩展（wave9 支线 X）单元测试。 |
| `tools/tests/test_search_v2_eval_ext2.py` | tools/tests/test_search_v2_eval_ext2.py — 检索 v2 评测第二阶段（wave10 支线 AA）。 |
| `tools/tests/test_search_v2b.py` | tools/tests/test_search_v2b.py — 检索 v2b（软 AND + 同义词低权重，wave12 支线 AH）。 |
| `tools/tests/test_selection_contract.py` | 策略库/选股契约测试（轻量，不跑引擎）。 |
| `tools/tests/test_service_restart.py` | service_restart 单测（fake health/starter/sleep；不碰真实服务）。 |
| `tools/tests/test_service_restart_health.py` | service_restart 假成功回归测试。 |
| `tools/tests/test_shadow_contract.py` | 组合/影子盘契约测试（轻量）。 |
| `tools/tests/test_shadow_node_probe.py` | shadow_node_probe 单节点 smoke 的离线契约测试（不触网/不写账本）。 |
| `tools/tests/test_simahui_strict_audit.py` | tools/tests/test_simahui_strict_audit.py — 司马微 G1-G8 严苛调校审计（产物级测试）。 |
| `tools/tests/test_smallcap_feasibility.py` | tools/tests/test_smallcap_feasibility.py — 「小市值反转」可行性（task-92）单元测试。 |
| `tools/tests/test_sql_guard.py` | sql_guard 单测：标识符正则 + allowlist。 |
| `tools/tests/test_static_writer_audit.py` | tools/tests/test_static_writer_audit.py — 静态产物写入脚本审计（wave26 支线 CR）。 |
| `tools/tests/test_static_writers_safe.py` | tools/tests/test_static_writers_safe.py — 高风险静态写入脚本安全化（wave27 支线 CT）。 |
| `tools/tests/test_strategy_health_weekly.py` | strategy_health_weekly CLI 单测（WO-HEALTH-WEEKLY-GEN-20261031）。 |
| `tools/tests/test_strategy_specs.py` | 量学战法规格库（research/strategy_specs/）与 CLI 的回归测试。 |
| `tools/tests/test_t1t2_capture.py` | tests for tools/t1t2_capture.py（T1/T2 A/B trace 受控采集，不造数）。 |
| `tools/tests/test_t1t2_metrics.py` | tests for tools/t1t2_metrics.py（T1/T2 A/B 指标只读聚合，不造数）。 |
| `tools/tests/test_task_board_audit.py` | task_board_audit 单测：分类纯函数 + 不可读 tasks 兜底。 |
| `tools/tests/test_task_board_stale.py` | task_board_stale：分类与安全 apply（只处理 branch_missing 且 owner 白名单）。 |
| `tools/tests/test_task_ledger_reconcile.py` | task_ledger_reconcile 单测（K-ORG-20261031-LEDGERRECON，2026-10-31）。 |
| `tools/tests/test_test_gate_a5strict.py` | K-ORG-20261031-A5STRICT 守卫：test_gate.ps1 A5 预算门阶段2 **strict**（2026-10-31）。 |
| `tools/tests/test_test_gate_artifact_guard.py` | K-ORG-20261031-ARTGUARDWIRE：test_gate artifact guard 接入的契约测试（静态 + Parser）。 |
| `tools/tests/test_test_gate_b1.py` | W5 P1-6 落地守卫：test_gate.ps1 B1+B2 关键集 + A5 预算门（2026-10-31）。 |
| `tools/tests/test_test_gate_org_gate.py` | K-ORG-20261031-ORGGATEWIRE 守卫：test_gate.ps1 的 org 模型一致性 gate（warn-only）。 |
| `tools/tests/test_test_gate_wtcheck_gate.py` | WO-GATE-WTCHECK-20261031：test_gate.ps1 的 wtcheck 门（W2-3）契约测试。 |
| `tools/tests/test_trend_template_export.py` | tools/tests/test_trend_template_export.py — 最后一个中风险脚本写入加固（wave31 收尾）。 |
| `tools/tests/test_trust_chain_kpi.py` | trust_chain_kpi：M1 信任链 KPI 只读工具的离线单测（不依赖生产服务）。 |
| `tools/tests/test_v4_fallback_review.py` | v4 fallback 3 条人工可审清单：产物契约测试（只读，不调 LLM）。 |
| `tools/tests/test_w1_acceptance.py` | tests for tools/w1_acceptance.py（W1 验收一条命令）。 |
| `tools/tests/test_weekly_eval.py` | tools/tests/test_weekly_eval.py — 每周评测回归（task-125）。 |
| `tools/tests/test_weekly_kpi_board.py` | tools/weekly_kpi_board.py 单测（WO-LEGION-WEEKLY-KPI-20261031）。 |
| `tools/tests/test_worktree_invariant_check.py` | worktree_invariant_check 单测（W2 工作树/abort 不变量只读检查器）。 |
| `tools/tests/test_zhangsong_pit_audit.py` | 张松 PIT/数据口径自查报告测试（task-87）。 |
| `tools/tests/test_zhaoyun_daily.py` | zhaoyun_daily 单测：交付物扫描/索引/截止状态/报告渲染。 |
| `tools/tianfeng_dispatch.py` | 田丰派工回执台账（dispatch-live）。 |
| `tools/tool_matrix_check.py` | tool_matrix_check.py —— 岗×操作级二维工具白名单校验器（K-ORG-080/N9 · 087/T4） |
| `tools/trace_critic.py` | trace_critic.py — 轨迹 Critic（K-ORG-080/N8） |
| `tools/trace_log.py` | observability-trace：本地 JSONL trace（工具链/时延/成本/状态）。 |
| `tools/trace_ops.py` | trace 运营化 v0：读取 Phoenix span，做延迟/错误/调用量汇总与告警。 |
| `tools/trading_bot_flow.py` | 交易机器人人工确认 CLI（WO-BOT-CONFIRM）。 |
| `tools/triples_outcome_backfill.py` | triples_outcome_backfill: triples.jsonl 盈亏标签回填器（UMP 边裁影子原型第一步，K194-C/K195） |
| `tools/trust_chain_kpi.py` | M1 信任链 KPI / 证据只读检查（fail-soft；不写账本、不重启服务、不代签）。 |
| `tools/ui_keyboard_audit.py` | ui_keyboard_audit.py — 静态键盘焦点顺序审计（无浏览器依赖）。 |
| `tools/ui_ux_audit.py` | ui_ux_audit.py — 静态 UI/UX 审计（设计令牌/可访问性/状态/动效/内联样式）。 |
| `tools/ui_visual_baseline.py` | ui_visual_baseline.py — 通过 Chrome DevTools Protocol 采集视觉基线。 |
| `tools/ui_visual_gate.py` | 组件视觉基线 warn-only 门。 |
| `tools/unify_nav.py` | 前端入口统一治理（K-STOCK-100）· 第二件：静态页注入器 + 同步守卫。 |
| `tools/unify_os_ui.py` | 把 OS 统一主题/下钻层注入全站 HTML（幂等，可回滚）。 |
| `tools/us_scan.py` | us_scan: 美股全市场市值榜快照（tradingview-mcp P1a 扩池工具） |
| `tools/us_sentiment.py` | us_sentiment: tradingview-mcp P1b 美股情绪试点（K178，K147-C P1b）。 |
| `tools/v11_m0_preflight.py` | v1.1 M0 批前只读预检器（不执行 M0、不写 contract/配置/代码）。 |
| `tools/validate_json.py` | JSON 写侧前置校验工具（M3-P5；tasks.json 损坏事故判例落地）。 |
| `tools/verify_backups.py` | 只读备份校验器 —— 校验 <workspace>/_vault 下 MANIFEST.txt 与实体文件是否一致。 |
| `tools/w1_acceptance.py` | W1 验收一条命令（机器证，而非"文档称完成"）。 |
| `tools/watch_feed_to_confirm.py` | WA1 feed → 人工确认（WO-WATCH-FEED）。只读池文件，只建 pending。 |
| `tools/watchdog_probe_8093.py` | 8093 看门狗固定探针（K-ORG-114 · 哨薄壳化判例 K-STOCK-197~199）。 |
| `tools/weekly_dep_triage.py` | O8 P3d：依赖 triage 周报段（只读、warn-only、不阻断）。 |
| `tools/weekly_kpi_board.py` | 计划 v2 §5「周日 KPI 看板」只读聚合器（weekly_kpi_board）。 |
| `tools/weekly_report_html.py` | 研发部周报 Markdown→HTML 渲染器（WO-DRILL2-WEEKLYHTML）。 |
| `tools/wip_check.py` | 交付型席位 WIP 检查（只读 tasks.json + org.json）。 |
| `tools/worktree_invariant_check.py` | W2 工作树 / abort 不变量只读检查器（2026-10-31 · WO-LEGION-W2-INVARIANT-20261031）。 |
| `tools/xsec_profile.py` | 三线交叉标准件 xsec_profile v1.0（总方案 B1 · K-ORG-119 路由注册工具） |
| `tools/zhaoyun_daily.py` | 赵云治理日报 + deliverables 索引（只读采集，报告落盘）。 |
| `tools/zhice_migration_preflight.py` | 智策 DB 迁移链只读 preflight（ACL-104 第一阶段 / BUG-001 止血工具）。 |
| `tools/zhuge_brief_digest.py` | 诸葛亮晨报/晚报两期摘要器（纯格式化，不取数、不发送）。 |
| `tools/zhuge_brief_push.py` | zhuge_brief_push：诸葛亮晨报/晚报推送到个人飞书。 |
| `tools/zhuge_brief_snapshot.py` | zhuge_brief_snapshot.py — 诸葛亮双锚取数：本地只读快照 /api/zhuge/brief。 |
| `tools/zhuge_c1_decisions.py` | zhuge_c1_decisions.py — P1-4 与 C1 质询通道打通。 |
| `tools/zhuge_c3_decisions.py` | zhuge_c3_decisions.py — C3 采纳信号 ↔ P1-4 决策记录。 |
| `tools/zhuge_challenge.py` | zhuge_challenge.py — 诸葛亮质询通道离线 MVP（WO-LEGION-ZHUGE-CHALLENGE-20261027 / C1）。 |
| `tools/zhuge_challenge_model.py` | zhuge_challenge_model.py — P2 真模型质询生成（可选、默认不启用）。 |
| `tools/zhuge_debate.py` | 诸葛 P2 多智能体辩论 v0：三席结构化分歧（只读、不执行）。 |
| `tools/zhuge_decision_record.py` | zhuge_decision_record.py — P1-4 结构化决策记录 v0（追加式 JSONL）。 |
| `tools/zhuge_evidence.py` | zhuge_evidence.py — 诸葛亮能力底座 P0-2/P0-3：联网取证 + 证据 schema。 |
| `tools/zhuge_grounding.py` | zhuge_grounding.py — P1-1：把本地资料（MCP docs 检索）接入诸葛亮回答，强制 [S#] 引用。 |
| `tools/zhuge_mcp_server.py` | zhuge_mcp_server.py — 诸葛亮能力底座 P0-1：MCP 只读工具 server（stdio）。 |
| `tools/zhuge_memory.py` | zhuge_memory.py — P1-3 记忆 v0（Mem0 前置；纯 stdlib，JSONL 追加式）。 |
| `tools/zhuge_memory_seed.py` | zhuge_memory_seed：把 P1-4 决策账本中的最终决策写入长期记忆。 |
| `tools/zhuge_planner.py` | 诸葛 P1-6：规划与分解 v0（规则模板，确定性、零依赖、可测）。 |
| `tools/zhuge_rag.py` | zhuge_rag.py — P1-2 M1：诸葛亮 BM25 v0 检索（纯 stdlib，零依赖）。 |
| `tools/zhuge_redteam.py` | 诸葛 P1-8：红队/对抗演练 v0（注入/越狱/幻觉）。 |
| `tools/zhuge_reflector.py` | 诸葛 P1-7：自我反思/一致性 v0（确定性规则，零依赖）。 |
| `tools/zhuge_tool_router.py` | zhuge_tool_router — L5 只读工具自动编排。 |
| `tools/zhuge_wargame.py` | 诸葛 P2 兵棋 v0：三情景推演 + 敏感性 + 触发条件（只读、不执行）。 |

## 3. ADR 索引

| ADR | 标题 |
|---|---|
| [ADR-001-repomap-selfbuilt.md](adr/ADR-001-repomap-selfbuilt.md) | ADR-001 · 仓库地图自研(Aider RepoMap范式吸收) |
| [ADR-002-agents-export-single-source.md](adr/ADR-002-agents-export-single-source.md) | ADR-002 · 27岗子代理导出以bundle为单一事实源 |
| [ADR-003-dev-routing-tool-matrix.md](adr/ADR-003-dev-routing-tool-matrix.md) | ADR-003 · 研发单路由与 ARCH1 工具矩阵授权（A0 L1 批） |
| [ADR-004-docs-taxonomy-diataxis.md](adr/ADR-004-docs-taxonomy-diataxis.md) | ADR-004：文档体系采用 Diátaxis 分类 + ADR 记录架构决策 |
| [ADR-005-agent-observability-otel-genai.md](adr/ADR-005-agent-observability-otel-genai.md) | ADR-005：Agent 可观测采用 OpenTelemetry GenAI 语义约定（分阶段） |
| [ADR-006-worktree-devtask-isolation.md](adr/ADR-006-worktree-devtask-isolation.md) | ADR-006：一单一事 + git worktree 隔离 |
| [ADR-007-tool-matrix-single-source.md](adr/ADR-007-tool-matrix-single-source.md) | ADR-007：工具矩阵（tool_matrix）作为工具授权单一源 |
| [ADR-008-seat-contract.md](adr/ADR-008-seat-contract.md) | ADR-008：席位契约（SOUL / SEAT_CARD / CAPABILITIES / 记忆） |
| [ADR-009-port-registry-entry.md](adr/ADR-009-port-registry-entry.md) | ADR-009：端口注册表为唯一真源，一切绑 127.0.0.1 |
| [ADR-010-credentials-vault.md](adr/ADR-010-credentials-vault.md) | ADR-010：凭据集中到 `_vault`，密文副本 + token 仅环境注入 |
| [ADR-011-data-roots.md](adr/ADR-011-data-roots.md) | ADR-011：唯一数据根 + 运行态根分离，`work` 根退役 |
| [ADR-012-l2-five-elements.md](adr/ADR-012-l2-five-elements.md) | ADR-012：L2 动作五要素审批，沉默≠同意 |
| [ADR-013-scope-and-docs-discipline.md](adr/ADR-013-scope-and-docs-discipline.md) | ADR-013：单机个人 OS 的范围边界与文档纪律 |
| [ADR-014-agent-run-contract.md](adr/ADR-014-agent-run-contract.md) | ADR-014：Agent 运行契约 v1（run / event / conversation） |
| [ADR-015-platform-kernel-composition-model.md](adr/ADR-015-platform-kernel-composition-model.md) | ADR-015：平台内核契约与组合模型 v1 |
| [ADR-016-single-host-integration.md](adr/ADR-016-single-host-integration.md) | ADR-016：唯一宿主整合（零界承接零界 + 智策） |
| [ADR-017-framework-golden-path.md](adr/ADR-017-framework-golden-path.md) | ADR-017：唯一开发框架与黄金路径（ZBF v1） |
| [ADR-020-dev-legion-screen-contract.md](adr/ADR-020-dev-legion-screen-contract.md) | ADR-020 研发军团作战屏 v1 数据契约（只读） |

## 4. 约定与入口

- 开发宪法：[AGENTS.md](../AGENTS.md)
- 文档总索引：[docs/README.md](README.md)
- 交付物：[deliverables/](../deliverables/)
- 代码库智能：`python -X utf8 tools/codebase_intel.py map|search|refs|impact`

### AGENTS.md 章节

- 0. 平台身份
- 1. 端口铁律（不许互抢）
- 2. 前端准入三件套（新页面缺一不收）
- 3. 隔离与并发（多 Agent 同时开发）
- 4. 任务单纪律
- 5. 评审门（merge 自动执行，全绿才放行）
- 6. 受训与记忆（怎么让 Agent 越用越准）
- 7. 红线（历史踩过、代价高的）
- 8. 工程命名与别名注册表（2026-10-17 立章）
- 9. 敏捷开发与治理标准（ADGS v1.1 试行，2026-11-02 立章）
