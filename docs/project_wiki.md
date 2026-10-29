# 零界项目 Wiki（自动生成）

> 由 `tools/project_wiki.py` 生成；`--check` 校验是否与仓库现状一致。
> 仓库地图/ADR 索引/约定入口的单一自动快照；人工正文请写 docs/ 对应专题。

## 1. 仓库地图

| 顶层目录 | Python 模块数（codebase_intel scope） |
|---|---:|
| `agent_platform/` | 451 |
| `portal/` | 376 |
| `tools/` | 469 |

codebase_intel 统计：模块 **1296** / 符号 **14522**。

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
| `agent_platform/bridge_inbox/processed/20261018_sma20_rsi14/strategy_20261018_sma20_rsi14.py` | Codex 试点策略：SMA20 趋势过滤 + RSI14 超买超卖双向门（军团-M2）。 |
| `agent_platform/conf_calibration.py` | D1 自报置信度后验校准探针（K-STOCK-086，方案 085-P0-B）。 |
| `agent_platform/contracts/__init__.py` | 统一研究、决策与模拟执行契约。 |
| `agent_platform/contracts/objects.py` | M1 统一对象模型补齐：WorkItem / Artifact / Approval。 |
| `agent_platform/contracts/sim_market.py` | SIM_MARKET 只读数据契约。 |
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
| `agent_platform/engine/deepseek.py` | DeepSeek 云端推理客户端（工具级 llm-deepseek · WO-LEGION-LLMDEEPSEEK-20261026）。 |
| `agent_platform/engine/emp_numbering.py` | 军团员工编号契约（v1）——工号与编制号的唯一发号器 |
| `agent_platform/engine/evidence.py` | 统一证据仓（M2-P1）。 |
| `agent_platform/engine/evolve_pipeline.py` | 进化层端到端流水线（WO-LEGION-S27）。 |
| `agent_platform/engine/experience_store.py` | 经验入库（WO-LEGION-S26 · 进化层 Z4）。 |
| `agent_platform/engine/experiment_card.py` | 对照实验卡（WO-LEGION-S24 · 进化层 Z2）。 |
| `agent_platform/engine/idem_hook.py` | 双轨幂等 Hook（M0 P4）。 |
| `agent_platform/engine/idempotency.py` | 统一 IdempotencyRecord 账本（M0 P3）。 |
| `agent_platform/engine/interpreter.py` | 投研团队 DSL 解释器 v0（K-STOCK-004） |
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
| `agent_platform/intel_inbox/intel_inbox.py` | 满宠每日情报收件箱（方案B 文件收件箱上行·受理席）。 |
| `agent_platform/intel_inbox/test_intel_inbox.py` | 满宠每日情报收件箱受理席测试（全临时目录，零触碰真实 inbox/ledger）。 |
| `agent_platform/legion_command/__init__.py` | — |
| `agent_platform/legion_command/credential_catalog.py` | 统一凭据目录：JEV / 数据接口 / 飞书 等业务凭据的统一展示（只读，不复制明文）。 |
| `agent_platform/legion_command/hq_acceptance.py` | 验收会证据聚合：测试基线 + 红队最新 + 黄忠最新验收单（只读，缺件 fail-visible）。 |
| `agent_platform/legion_command/hq_agenda.py` | 会前议程 v1（P1）：收编 8798 议会厅的议题/留言/预约（只读）+ 留言追加。 |
| `agent_platform/legion_command/hq_meeting.py` | 指挥部会议 v2（P0）：五席晨会/作战会/验收会 纪要 + 订单派单。 |
| `agent_platform/legion_command/hq_stats.py` | 会议看板（P2）：闭环率/跑偏检测/回放（只读）。 |
| `agent_platform/legion_command/ide_fs.py` | IDE 调试通道只读文件工具：list_dir / read_file / search。 |
| `agent_platform/legion_command/intent.py` | 军团 OS · 席位意图卡契约（v1）——**现役实现（8796 /api/intent 唯一数据源）** |
| `agent_platform/legion_command/keypool.py` | 三席多 Key 池（田丰/赵云/诸葛亮）— 存储/测试/切换/自动切换。 |
| `agent_platform/legion_command/mcp_bridge.py` | Tushare MCP 工具桥（streamable-http JSON-RPC）。 |
| `agent_platform/legion_command/mission_dispatch.py` | Mission Dispatcher（阶段1）：只做 dry-run 派工计划，不建 worktree、不改代码。 |
| `agent_platform/legion_command/mission_evolver.py` | Mission 阶段3：结果采集 → 经验候选 → 策略建议。 |
| `agent_platform/legion_command/mission_planner.py` | Mission Planner（阶段1）：一份文档 → 任务 DAG → plan.json。 |
| `agent_platform/legion_command/mission_worker.py` | 阶段4-1 Worker：自动接单低风险任务，在 worktree 用 LLM 生成文件改动并提交。 |
| `agent_platform/legion_command/model_allocator.py` | 任务感知模型分配：任务/角色 → 本地 / 云端 / GPT-6。 |
| `agent_platform/legion_command/model_prefs.py` | 研发板块模型手动切换偏好：agent → model 别名。 |
| `agent_platform/legion_command/planner_rules.py` | Planner rules from signed experience + A/B comparison. |
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
| `agent_platform/legion_ide/exec_runner.py` | IDE 受控执行（R2）：命令白名单 + 超时/输出上限 + 环境净化。 |
| `agent_platform/legion_ide/rw_guard.py` | IDE 可写/可执行 · 作用域 / 工单锁 / 会话 / 审计（R0 骨架，D1/D3/D4=A）。 |
| `agent_platform/legion_ide/serve.py` | 军团 Agent IDE · 独立形态服务（legion_ide）· v2（R0+R1+R2）。 |
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
| `agent_platform/repair/__init__.py` | 关羽修复车道包。 |
| `agent_platform/repair/lane.py` | 关羽修复车道 v1：事故/失败 triage → 批次 → pytest 运行 → 留证。 |
| `agent_platform/replay.py` | 辩论回放 CLI（K-STOCK-007）：lineage 人工抽查工具，三判据 J3 的配套 |
| `agent_platform/research/__init__.py` | 赵云只读研究包。 |
| `agent_platform/research/web.py` | 只读 Web 取证：SSRF 防护 + 域名白名单 + 24h 缓存 + 每日预算 + 来源引用。 |
| `agent_platform/router.py` | 军团任务路由器 v0.1（K-ORG-119 批1） |
| `agent_platform/run_invest.py` | 投研部执行入口（K-STOCK-004） |
| `agent_platform/run_shadow.py` | 5 日影子运行账本（K-STOCK-004 蓝图 v2 判据的数据采集端） |
| `agent_platform/sandbox/__init__.py` | 赵云沙箱包。 |
| `agent_platform/sandbox/runner.py` | 本地受限沙箱：隔离 cwd / 净环境 / 超时 / 输出限额，执行技能 verify（只读验证）。 |
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
| `agent_platform/task.py` | 任务单 CLI（K-ORG-011）：open / ls / move |
| `agent_platform/templates/strategy_agent_template.py` | 第一版可插拔策略 Agent 模板。 |
| `agent_platform/tests/smoke_test.py` | 冒烟测试（K-STOCK-004）：MockLLM 全链路 + 断言，临时目录不污染正式 data |
| `agent_platform/tests/test_a0_agent_p1.py` | A0 权威 binding 与成对判读审计字段的纯本地回归测试。 |
| `agent_platform/tests/test_a0_evidence_judge.py` | a0_evidence_judge 单测（K-QUANT-20261026-A0EVJ）。 |
| `agent_platform/tests/test_a_phase_group_board.py` | A 阶段组级看板生成器测试（WO-A-STAGE-BOARD）。 |
| `agent_platform/tests/test_academy_ops.py` | 研究院运营三件套测试（WO-LEGION-ACAD1-P0）。 |
| `agent_platform/tests/test_academy_role_attrs.py` | 研究院角色属性一致性（2026-10-29 A，WO-ACAD-ROLE-ATTR-20261029）。 |
| `agent_platform/tests/test_academy_wiring.py` | M1 Academy 域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_agent_sandbox.py` | Agent 沙箱/benchmark 测试（WO-AGENT-SANDBOX-20261027）。 |
| `agent_platform/tests/test_anonymous_review.py` | WO-LEGION-S25 Z3 匿名横评测试。 |
| `agent_platform/tests/test_approvals_registry.py` | M1-阶段二 Approval 注册表只读数据面测试（离线 fixture + 真实仓冒烟）。 |
| `agent_platform/tests/test_approvals_store.py` | ApprovalStore 幂等契约测试（M0 契约补齐：同键异参拒绝）。 |
| `agent_platform/tests/test_arch1_ops.py` | ARCH1 架构/技术债周巡检测试（WO-LEGION-ARCH1-PATROL）。 |
| `agent_platform/tests/test_artifacts.py` | ArtifactStore 契约测试（M3-P1：统一产物登记仓）。 |
| `agent_platform/tests/test_attribution_gap_report.py` | tools/attribution_gap_report.py 测试（WO-DRILL3-ATTRGAP）。 |
| `agent_platform/tests/test_audit_20261028.py` | 2026-10-28 审计修复回归测试（WO-AUDIT-20261028-FIX）。 |
| `agent_platform/tests/test_authority_audit.py` | authority_audit 测试（WO-AUTHORITY-C3）。 |
| `agent_platform/tests/test_batch_strategy_drill.py` | 批量新增策略 Agent 的 SHADOW 零主链改动演练。 |
| `agent_platform/tests/test_business_adapter.py` | M0 P5 业务域投影适配器测试（离线 fixture）。 |
| `agent_platform/tests/test_capability_self_audit.py` | 能力自检测试（WO-ZHAOYUN-CAP2）。 |
| `agent_platform/tests/test_check_observation_point.py` | check_observation_point 周一观察点一键巡检测试。 |
| `agent_platform/tests/test_check_trigger_chain.py` | check_trigger_chain 周一触发链前置自检测试。 |
| `agent_platform/tests/test_closeout_round.py` | P0-2 日终收口轮测试（WO-LEGION-P0FIX-20261026）。 |
| `agent_platform/tests/test_codex_bridge.py` | 军团 Codex bridge 测试（M1 校验器 + M2 试点注册）。 |
| `agent_platform/tests/test_contracts.py` | 统一 contracts 基础契约测试。 |
| `agent_platform/tests/test_credential_trace.py` | adapters/credential_trace 交叉校验器单元测试（K-M1 审计补强）。 |
| `agent_platform/tests/test_credential_trace_audit.py` | _audit_credential_trace 交叉校验测试（tmp 独立仓，不碰真库）。 |
| `agent_platform/tests/test_d1_decision_strict.py` | D1 决策 strict JSON 契约测试（2026-10-29 · 鲁肃复盘整改）。 |
| `agent_platform/tests/test_d1_json_contract.py` | 决策节点 JSON 契约 + LLM 生成元信息留存（10-17 隔离预演驱动的收口）。 |
| `agent_platform/tests/test_data_lineage.py` | 数据血缘 MVP 测试（WO-DATA-LINEAGE-20261027）。 |
| `agent_platform/tests/test_data_pipeline_probe.py` | data_pipeline_probe 测试（WO-SEAT-REAL-B2B）。 |
| `agent_platform/tests/test_debate_roles.py` | 控辩审契约测试（V2 思路 2 并入 / K-ORG-166，2026-10-18 老田拍板）。 |
| `agent_platform/tests/test_decision_outcomes.py` | D1 决策 outcome 台账单测（不联网）。 |
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
| `agent_platform/tests/test_evidence_collect.py` | M2-P1 证据仓 + 六域收编测试。 |
| `agent_platform/tests/test_evolve_pipeline.py` | WO-LEGION-S27 进化层端到端流水线 + 指挥台进化视图测试。 |
| `agent_platform/tests/test_experience_store.py` | WO-LEGION-S26 Z4 经验入库测试。 |
| `agent_platform/tests/test_experiment_card.py` | WO-LEGION-S24 Z2 对照实验卡测试。 |
| `agent_platform/tests/test_gate3_verdict.py` | 门 3 独立复核工具（tools/gate3_verdict.py）的判据测试。 |
| `agent_platform/tests/test_gate_content_hash.py` | M3-P3 发布闸门 content_hash 绑定测试（批 A 发 B 拒绝）。 |
| `agent_platform/tests/test_gateway.py` | WO-LEGION-S21 军团门面测试：信封/LLM 门面/投递幂等/HTTP 端点。 |
| `agent_platform/tests/test_genai_otel.py` | D3：OTel GenAI 适配层单测（默认关 no-op + 字段映射 + span 包装）。 |
| `agent_platform/tests/test_gov_ai.py` | GOVAI-1 地基测试（WO-LEGION-GOVAI1-20261026）。 |
| `agent_platform/tests/test_gov_ai_case_bridge.py` | GOVAI-3 判例草稿签名链测试（WO-LEGION-GOVAI3-20261026）。 |
| `agent_platform/tests/test_gov_integ.py` | GOVINTEG（P1 消费接线 + P3 输入契约）测试。 |
| `agent_platform/tests/test_governance_clock.py` | 治理时钟测试（WO-LEGION-CLOCK-20261026）。 |
| `agent_platform/tests/test_gzh_artifact_wiring.py` | M3-P2 GZH 产线产物挂接测试（离线 fixture）。 |
| `agent_platform/tests/test_gzh_wiring.py` | M1 GZH 域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_healthcheck_all.py` | test_healthcheck_all：聚合逻辑测试（不真跑五个工具）。 |
| `agent_platform/tests/test_heartbeat.py` | Paper 引擎心跳适配器测试（Paperclip 心跳对接第一刀，B 路）。 |
| `agent_platform/tests/test_hermes_ui_probe.py` | HERMES_UI 探活工具测试（WO-HERMES-UI-REAL）。 |
| `agent_platform/tests/test_hq_acceptance.py` | 验收会证据 P1b 测试：gather/HTTP/纪要 acceptance 字段/前端标记。 |
| `agent_platform/tests/test_hq_agenda.py` | 会前议程 P1 测试：收编只读/留言追加/HTTP 路由/前端标记。 |
| `agent_platform/tests/test_hq_meeting.py` | 指挥部会议 v2 P0 测试：schema/校验/列表/派单/HTTP 路由/前端标记。 |
| `agent_platform/tests/test_hq_p2.py` | 会议 P2 测试：看板统计/跑偏/回放、赵云会议绑定覆盖、HTTP、前端标记。 |
| `agent_platform/tests/test_hq_reflux.py` | P1c 回流测试：会议事件/Phoenix trace/agenda 近事件/KINDS。 |
| `agent_platform/tests/test_ide_enrich.py` | Agent IDE 组织架构富化测试（WO-IDE-ENRICH-20261026）。 |
| `agent_platform/tests/test_ide_rw.py` | IDE 可写/可执行 R0+R1+R2 测试（D1-D4 全 A）。 |
| `agent_platform/tests/test_idem_acceptance.py` | M0 P4 自动化验收：双轨接入 + 死信 + 人工恢复 + 崩溃窗口。 |
| `agent_platform/tests/test_idempotency.py` | 统一 IdempotencyRecord 与只读审计器测试（M0 P3）。 |
| `agent_platform/tests/test_inbox_gateway_lifecycle.py` | OSLINK-P4 answered/delivered 生命周期测试（隔离树·待签）。 |
| `agent_platform/tests/test_inbox_offline.py` | WO-FS-157: all effects isolated to system-temp synthetic tickets. |
| `agent_platform/tests/test_intel_wiring.py` | intel_wiring（情报裁决域）测试。 |
| `agent_platform/tests/test_interpreter_run_scope.py` | O6 P2b：engine Interpreter.run 的 run_scope + attempt 记录单测。 |
| `agent_platform/tests/test_invest_wiring.py` | M1 Invest（股票投研）域接线测试（离线 fixture）。 |
| `agent_platform/tests/test_judge.py` | LLM-as-judge 测试（WO-EVAL-JUDGE）。 |
| `agent_platform/tests/test_keygen_script.py` | gen_principal_key.ps1 回归测试（WO-LEGION-KEYGENFIX-20261026）。 |
| `agent_platform/tests/test_knowledge_wiring.py` | M2-P2 统一记忆入口测试。 |
| `agent_platform/tests/test_legion_command.py` | WO-LEGION-S22 军团指挥界面套件测试：快照聚合 + HTTP 端点 + dispatch 走门面。 |
| `agent_platform/tests/test_legion_command_run_scope.py` | O6 P2c：legion_command 请求级 run_scope 单测。 |
| `agent_platform/tests/test_legion_contract.py` | LS-02 数据契约测试（M0 冻结前校验）：只读/字段/真源。 |
| `agent_platform/tests/test_legion_ide.py` | 军团 Agent IDE 独立形态测试（WO-LEGION-IDE-STANDALONE-20261026）。 |
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
| `agent_platform/tests/test_openclaw_cli.py` | OpenClaw CLI 桥单测（2026-10-26 WO-LEGION-ZHUGE-8796OC）：解析/配置/参数，零网络。 |
| `agent_platform/tests/test_org_audit.py` | WO-LEGION-S34 编制完整性审计测试。 |
| `agent_platform/tests/test_org_chart.py` | org_chart 测试（WO-ORG-CHART）。 |
| `agent_platform/tests/test_org_enroll.py` | WO-LEGION-S33 Agent 编制工具测试。 |
| `agent_platform/tests/test_org_matrix_migration.py` | org 矩阵迁移测试（WO-ORG-MATRIX P2）：双写校验、幂等、冲突拒绝。 |
| `agent_platform/tests/test_org_migrate.py` | WO-LEGION-S43 档案迁移测试。 |
| `agent_platform/tests/test_org_profiles.py` | WO-LEGION-S41 档案索引测试。 |
| `agent_platform/tests/test_org_rename.py` | WO-LEGION-S43 中文名迁移测试。 |
| `agent_platform/tests/test_osleg_consumer.py` | K-QUANT-20261026-OSLEG-CONSUMER：8795 消费者骨架离线测试。 |
| `agent_platform/tests/test_osleg_http_api.py` | OSLINK-W4 通道② 8795 HTTP 端点层 + 鉴权测试（离线 / 隔离 store / 不启 8795）。 |
| `agent_platform/tests/test_osleg_http_probe.py` | osleg_http_probe 纯函数测试（不启端口、不联网、不动真实 store）。 |
| `agent_platform/tests/test_osleg_p2b_gate.py` | K-QUANT-20261026-OSLEG-P2B：真实 store P2b 闸测试。 |
| `agent_platform/tests/test_paper_event_wiring.py` | M4-P2 Paper 事件入账测试。 |
| `agent_platform/tests/test_perf_ledger.py` | M4-P4 绩效账本测试：幂等/冲突拒绝/缺锚点跳过/链挂接/分组聚合/ |
| `agent_platform/tests/test_personal_wiring.py` | M1 个人协同域接线测试（schedules 受控写路径）。 |
| `agent_platform/tests/test_pitch_wiring.py` | M1 路演 PPT 域接线测试（decks 受控状态机）。 |
| `agent_platform/tests/test_policy_check.py` | 策略单源（policy.json）决策器测试（WO-POLICY-CODE-20261027）。 |
| `agent_platform/tests/test_portfolio_risk_wiring.py` | 组合预算与风险授权边界测试。 |
| `agent_platform/tests/test_pre_observation.py` | pre_observation（周一 17:25 前置一键）测试。 |
| `agent_platform/tests/test_quality_gate.py` | quality_gate 测试（WO-SEAT-REAL-B2D）。 |
| `agent_platform/tests/test_quant_double_knife.py` | 双刀移植自测（K-STOCK-018 补全）：信号模块八重过滤合成验证 + 入场确认模块 |
| `agent_platform/tests/test_readonly_surface_fixes.py` | 2026-10-17 复盘审查修复回归（只读面写副作用专项）。 |
| `agent_platform/tests/test_regime_roster.py` | M4-P3 Regime 阵容选择器测试。 |
| `agent_platform/tests/test_repair_lane.py` | 修复车道测试（WO-GUANYU-LANE）。 |
| `agent_platform/tests/test_replay_query.py` | M1 回放查询层测试（离线 fixture + 真实只读冒烟）。 |
| `agent_platform/tests/test_replay_root_ledger_isolation.py` | Offline discovery test for cross-ledger replay(root) path isolation. |
| `agent_platform/tests/test_research_fsm.py` | 研究流水线状态机测试（WO-RESEARCH-FSM-20261027）。 |
| `agent_platform/tests/test_research_seat_ops.py` | 研究值守双席测试（WO-LEGION-ACAD1-P2）。 |
| `agent_platform/tests/test_role_group_model.py` | 角色分工/分组适配模型测试（WO-ROLE-GROUP-MODEL）。 |
| `agent_platform/tests/test_roster_d1_contract.py` | 真仓 roster → D1 JSON 契约锚点测试（f59a0738 集成面收口）。 |
| `agent_platform/tests/test_run_shadow_guard.py` | 影子主链防拖死护栏测试（设计稿 §5.2 过渡缓解：单策略产出超时包裹）。 |
| `agent_platform/tests/test_run_shadow_log_redirect.py` | run_shadow 日志重定向护栏测试（K-OPS-20261019-SHADOWFIX）。 |
| `agent_platform/tests/test_run_strategy_shadow.py` | strategy_drill_cli CLI 契约测试（原名 run_strategy_shadow.py，拍板 02 更名去混淆）。 |
| `agent_platform/tests/test_sandbox.py` | 沙箱验证测试（WO-SANDBOX-VERIFY）。 |
| `agent_platform/tests/test_sandbox_quota.py` | 沙箱资源配额测试（WO-SANDBOX-QUOTA-20261027）。 |
| `agent_platform/tests/test_scan_secrets.py` | scan_secrets 只读扫描器测试。 |
| `agent_platform/tests/test_scan_secrets_timeout.py` | scan_secrets git_status_files 超时保护测试（审查 B4）。 |
| `agent_platform/tests/test_seat_batch1.py` | 批次1 测试：A0 取数审计事件 + 全席 prompt 回滚标记（WO-SEAT-REAL-BATCH1）。 |
| `agent_platform/tests/test_seat_eval.py` | 席位评测跑批器测试（WO-EVAL-SEATS-20261027）。 |
| `agent_platform/tests/test_seat_fallback.py` | 席位级 fallback 链验收（WO-LEGION-DEVEXEC2-20261027）。 |
| `agent_platform/tests/test_seat_heartbeat.py` | seat_heartbeat 测试（WO-SEAT-REAL-B2E）。全部 stub，不真实调用外部模型。 |
| `agent_platform/tests/test_seat_iteration.py` | seat_iteration 测试（11 席迭代配置）。 |
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
| `agent_platform/tests/test_sign_tools.py` | T3 签名工具三件套测试（WO-LEGION-SIGNTOOLS-20261026）。 |
| `agent_platform/tests/test_signature_guard.py` | P0-3 签名守卫测试（WO-LEGION-P0FIX-20261026）。 |
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
| `agent_platform/tests/test_tools_exitcode_contract.py` | 只读工具「退出码契约」测试（subprocess 级，端到端锁定 CLI 语义）。 |
| `agent_platform/tests/test_trace_cost.py` | trace_cost 单元测试（WO-TRACE-COST-20261027）。 |
| `agent_platform/tests/test_trace_link.py` | M4-P1 trace_link 映射层测试。 |
| `agent_platform/tests/test_trace_link_shadow_sync.py` | trace_link shadow-only 补链（replay_trace 锚点修复）契约。 |
| `agent_platform/tests/test_trace_otel.py` | OTel→Phoenix 导出单测（fake urlopen，不联网）。 |
| `agent_platform/tests/test_trace_run_id.py` | O6 P2a：trace.run_id 透传/迁移单测。 |
| `agent_platform/tests/test_trace_store.py` | Trace 存储测试（WO-TRACE-OTEL）。 |
| `agent_platform/tests/test_trace_usage.py` | LLM usage/成本留证测试（WO-TRACE-USAGE）。 |
| `agent_platform/tests/test_unified_projection.py` | M1 统一投影聚合器测试（离线 fixture）。 |
| `agent_platform/tests/test_usage_ledger.py` | M2-P4 统一使用账本测试。 |
| `agent_platform/tests/test_verify_strategy_ledger.py` | verify_strategy_ledger 三方对账工具测试（军团-M2 验收判据 1 闭环）。 |
| `agent_platform/tests/test_web_research.py` | 只读 web 取证测试（WO-WEB-RESEARCH）。 |
| `agent_platform/tests/test_weekly_report.py` | weekly_report.py（W36 周报生成器）最小测试集——W37 工位测试回填。 |
| `agent_platform/tests/test_weekly_report_html.py` | tools/weekly_report_html.py 测试（WO-DRILL2-WEEKLYHTML）。 |
| `agent_platform/tests/test_zhaoyun_cap2.py` | 赵云能力二期测试：审批闭环/干预建议/记忆策展/路由/自检/留证（WO-ZHAOYUN-CAP2）。 |
| `agent_platform/tests/test_zhaoyun_debate.py` | 辩论接线单测。 |
| `agent_platform/tests/test_zhaoyun_drill.py` | 赵云能力下钻模块测试（WO-ZHAOYUN-DRILL）。 |
| `agent_platform/tests/test_zhaoyun_quant.py` | 量化只读 接口/页面 单测（runner 注入，不联网）。 |
| `agent_platform/tests/test_zhaoyun_view.py` | 赵云 · 治理席前端页测试（WO-LEGION-ZHAOYUNPAGE-20261026 / ENH-20261026）。 |
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
| `agent_platform/tools/data_lineage.py` | 数据血缘 MVP（P0⑦ · WO-DATA-LINEAGE-20261027）。 |
| `agent_platform/tools/data_pipeline_probe.py` | 数据管道只读自检工具（B2-b 情报/采集 7 席）。 |
| `agent_platform/tools/diff_baseline.py` | diff_baseline：账本基线对比（观察点 FAIL 后取证第一步，只读，fail-visible）。 |
| `agent_platform/tools/gov_ai.py` | GOVAI-1 地基：DeepSeek 进治理动作（异常归纳 / 治理建议 / 判例草稿）。 |
| `agent_platform/tools/healthcheck_all.py` | healthcheck_all：一键聚合体检（周一现场一条命令跑全景，纯聚合零重复实现）。 |
| `agent_platform/tools/hermes_ui_probe.py` | HERMES_UI（糜竺）· 门面探活工具（真运行体成员的最小工具集）。 |
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
| `portal/_smoke_algo_market.py` | K-STOCK-087 冒烟：/algo 页渲染 + 卡密激活闭环 + 主页统计口径。 |
| `portal/agent_auto_commit.py` | agent_auto_commit.py — 各 Agent 代码定时强制入库（portal 统揽板块运维） |
| `portal/api_analysis.py` | 个股分析门户入口（/api/analysis/*）——独立文件，portal_api.py 只加早分派。 |
| `portal/api_approvals.py` | M1-阶段二 Approval 注册表只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_backtest.py` | P4 批 6：回测域 Mixin（backtest 页族/pulse/custom 批族）。 |
| `portal/api_bridge.py` | P4 批 2：桥接域 Mixin（bridge 决策/预览/动作族）。 |
| `portal/api_brief.py` | 复盘简报只读投影（驾驶舱·复盘舱数据面二期 R5）。 |
| `portal/api_capability.py` | M3-P5 能力看板只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_chanlun_pages.py` | P4 批 5：缠论页面 Mixin（chanlun 页/标注/PNG/池族）。 |
| `portal/api_common.py` | P4 批 0：portal 共享底座（helper+方案A+状态锁/常量+导航族+tp共享+_sched判色）。 |
| `portal/api_daily_data.py` | 数据日报聚合出口 mixin（K-ORG-183 M1，2026-10-18）。 |
| `portal/api_data.py` | P4 批 1：数据域 Mixin（data_health/warehouse/sync 族 + Handler 方法壳）。 |
| `portal/api_dataops.py` | 数据运营指挥台 /api/dataops/overview —— 采集动线 + 数据池 + 管道调度聚合视图。 |
| `portal/api_dev_legion.py` | 研发军团作战屏 v1 · 只读 API（LS-03，2026-10-28）。 |
| `portal/api_events.py` | R6 事件总线数据面（8091 侧 ingest/query，Gate 0 前置离线开发 M1 骨干）。 |
| `portal/api_evolution.py` | 进化账本只读投影（驾驶舱·复盘舱数据面二期）。 |
| `portal/api_execution.py` | R1 执行健康总线（联动层一期 T1，K-ORG-112 三层只读投影）。 |
| `portal/api_flow.py` | 动作流中心 API — flow.html 后端（P4 批7 mixin） |
| `portal/api_gate.py` | 周一观察点门判只读投影（驾驶舱·复盘舱数据面二期）。 |
| `portal/api_guojia.py` | 郭嘉·预判对账司出口 mixin（K-ORG-191/193，2026-10-18）。 |
| `portal/api_intel_bus.py` | 情报总线唯一出口 mixin（六域→总线→双路径，2026-10-19 联调首包）。 |
| `portal/api_intel_evidence.py` | news.db 的新闻—标的显式代码证据只读投影。 |
| `portal/api_intel_graph.py` | K-MDC-E2: /api/intel-graph/dossier — one-stock candidate-edges endpoint. |
| `portal/api_jev.py` | JEV 判定只读查询接口 —— `/api/jev/query`（2026-10-26 JEVAPI）。 |
| `portal/api_kg.py` | P4 批3：知识图谱域 Mixin（kg 页族 17 方法 + 卡片/标注 JS 与小函数）。 |
| `portal/api_legion_ops.py` | 军团作战大屏数据源（K-ORG-121 L3）：GET /api/legion/ops |
| `portal/api_legion_ops_v2.py` | 军团作战大屏数据源 v2（K-ORG-121 L3+ · 下钻/派工/复盘三端点） |
| `portal/api_lineage.py` | 管道血缘只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_misc.py` | P4 批7-MISC：杂域合并 Mixin（qpzm/org/aim/vd/subscribe/feishu/intel/audit/devops 页族 |
| `portal/api_model_admin.py` | 模型管理控制台 API（10-18 · 纯标准库 · 127.0.0.1:8098） |
| `portal/api_news_intel.py` | 新闻情报台聚合出口 mixin（K-ORG-208 判读输出台视图，2026-10-19）。 |
| `portal/api_notify.py` | 通知指挥四态队列只读聚合出口 mixin（K-ORG-201 W36，2026-10-19）。 |
| `portal/api_os_goal.py` | OS 目标/事项/成果接口（G0 骨架） |
| `portal/api_perf.py` | M4-P5 投研绩效看板只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_portfolio.py` | P4 批7-PF：持仓/投委会域 Mixin（debate/council 页族 + portfolio 函数族）。 |
| `portal/api_replay.py` | M1-③ 事件回放查询只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_screen.py` | P4 批7-SCR：数据/筛选域 Mixin（screen 页族 + build_screen/kline/chips 函数族）。 |
| `portal/api_tickets.py` | 工单只读投影（驾驶舱一期·复盘舱数据面）。 |
| `portal/api_unified.py` | M1 统一投影只读 mixin（挂 portal_api.Handler 继承链）。 |
| `portal/api_xq_intel.py` | 雪球情报出口 mixin（两线预案 M-RT3 消费端门户挂载，2026-10-18）。 |
| `portal/api_zhuge_adoption.py` | api_zhuge_adoption.py — 诸葛亮建议采纳率只追加路由（WO-OS-32 / C1，2026-10-23； |
| `portal/api_zhuge_brief.py` | WO-OS-04 诸葛亮建议引擎（规则版 v1）；WO-OS-20 扩第七源 pushflow。 |
| `portal/api_zhuge_route.py` | WO-OS-09 诸葛亮建议引擎只读路由（GET /api/zhuge/brief，2026-10-21）。 |
| `portal/approval_card.py` | K-ORG-201 W37：管理公告审批卡（老田飞书卡片确认 → 审批留痕）。 |
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
| `portal/inbound_gateway.py` | W43 入向受理器（飞书通知指挥四期 · 环 2 投喂环主施工）。 |
| `portal/intel_feishu_export.py` | K-MDC-E3 (阚泽·信驿司): feishu intel export — windowed, read-only, receipted. |
| `portal/issues_store.py` | issues_store.py — OS 问题账本（问题生命周期管理：发现→认领→解决→清除归档）。 |
| `portal/jev_card_action.py` | 盯盘预警卡片动作统一 handler（W3 清债：9104 老链迁移目标，2026-10-24）。 |
| `portal/job_spec_registry.py` | Persistent, non-executable descriptors for minute execution sources. |
| `portal/kg/__init__.py` | api_kg 拆分目录：kg_html / kg_service（第一批）。 |
| `portal/kg/kg_html.py` | kg 页族 HTML 片段（resonance/net/overview）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。 |
| `portal/kg/kg_service.py` | kg 数据/写入 helper（label/card save+diff）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。 |
| `portal/kg_stock_query.py` | 轻量股票血缘查询脚本（供 Bot subprocess 调用）。 |
| `portal/km_feishu_hub.py` | KM 知识库飞书联动枢纽（127.0.0.1:8099）。 |
| `portal/legacy_telemetry.py` | 旧模板调用遥测（P3 观察期，2026-10-25）。 |
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
| `portal/policy_geo_collector.py` | policy_geo_collector.py — 政策地缘情报域采集器（免费公开源起步）。 |
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
| `portal/redeliver.py` | W41 死信自动补达（2026-10-26）：退避 + 次数封顶 + 独立补达流水。 |
| `portal/register_feishu_watchdog_task.py` | 注册 FeishuBotWatchdog SYSTEM 计划任务（每 5 分钟自愈拉起飞书机器人）。 |
| `portal/renewal_reminder.py` | 订阅到期前提醒 · 纯函数能力（飞书中台 P3.7-lite · L-B 工位 2026-10-20）。 |
| `portal/renewal_state.py` | 订阅到期提醒 · 时区口径 + 已发去重状态（P3.8-lite · M-A 工位 2026-10-20）。 |
| `portal/risk_feedback_ledger.py` | 执行反馈账本 v0（盯盘→策略回流 · 协作协议 v0 账本解耦）。 |
| `portal/route_baseline.py` | portal_api.py 路由回归基线（K-STOCK-077 P-4 第一步）。 |
| `portal/run_bot_now.py` | — |
| `portal/score_guard.py` | 评分口径守卫（B1，2026-10-26）：5 个核心指标的强制校验/展示规范。 |
| `portal/score_registry.py` | 零界评分口径注册表（Score Registry，2026-10-26）。 |
| `portal/server_gate.py` | 今日选股公开访问壳：访问码 Cookie 门禁 + 路径白名单（源头版，进 git）。 |
| `portal/setup_tunnel_api.py` | Cloudflare API Token 模式：建命名隧道 → 绑 DNS → 起 cloudflared → 改链接基地址。 |
| `portal/share_tokens.py` | 分享令牌（K-PUSH-002 A1）——推送卡片深链的溯源 token：签发/校验/访问账。 |
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
| `portal/test_zhuge_morning.py` | — |
| `portal/test_zhuge_morning_review.py` | 晨报离线审核闸：所有测试只用 tmp_path，不访问生产账本或发送端。 |
| `portal/tests/test_api_backtest_minute.py` | Minute API integration tests with an import-time isolated JobSpec catalog. |
| `portal/tests/test_api_intel_evidence.py` | — |
| `portal/tests/test_api_intel_graph.py` | K-MDC-E2 tests: dossier endpoint — honest empty, candidate-only, traceable. |
| `portal/tests/test_api_kg_contract.py` | api_kg 契约测试骨架（2026-10-28 首建，拆分前先钉住行为）。 |
| `portal/tests/test_intel_dossier_skeleton.py` | K-INTEL-20261022-FE: offline skeleton contracts, without loading live sources. |
| `portal/tests/test_intel_feishu_export.py` | K-MDC-E3 tests: window gate, digest honesty, four-account receipt. 全离线不真发. |
| `portal/tests/test_intel_workbench_xq_link.py` | Offline regression for evidence-to-Xueqiu ticker links; no live source calls. |
| `portal/tests/test_job_spec_registry.py` | — |
| `portal/tests/test_job_spec_registry_expiry_recovery.py` | — |
| `portal/tests/test_minute_router_isolated.py` | Exercise the actual portal HTTP router with an isolated minute JobSpec root. |
| `portal/tests/test_minute_router_process_restart.py` | Exercise minute HTTP recovery across two separate portal OS processes. |
| `portal/tests/test_os_approval_voice_page.py` | 语音审批台页面结构测试（纯静态页，不改服务）。 |
| `portal/tests/test_os_asr.py` | VOICE3：本地 SenseVoice ASR 端点与页面接线测试。 |
| `portal/tests/test_os_asr_selfcheck.py` | ASR 自检守卫：路由存在且能返回自检结果。 |
| `portal/tests/test_os_bp.py` | G2a：一句话 → 商业计划（BP v1/v2）同交互测试。 |
| `portal/tests/test_os_controlled.py` | C 阶段最小真实受控闭环（CONTROLLED_ONLY）测试。 |
| `portal/tests/test_os_controlled_audit.py` | C0/C1 审计修复回归测试：撤销/停用/重复裁决/过期/能力匹配。 |
| `portal/tests/test_os_controlled_c1.py` | C1：L2 人工审批通道测试（不外发，仅审批+受控 outbox）。 |
| `portal/tests/test_os_controlled_ui.py` | C1 审批台：消费者列表 / 审批单列表 API 与页面存在性测试。 |
| `portal/tests/test_os_entry_links.py` | zero_proto → Quickstart 一句话入口结构测试。 |
| `portal/tests/test_os_goal_api.py` | G0 API 纯函数单测（不起 HTTP、不绑端口）。 |
| `portal/tests/test_os_goal_contract.py` | G0 合同判据单测（OS-CLOSE-G0）。 |
| `portal/tests/test_os_goal_store.py` | G0 账本单测：append-only / 哈希链 / 幂等 / 投影 / 证据包。 |
| `portal/tests/test_os_quick_auth.py` | QUICKFIX1：8734 反代下本机页面 POST 判定单测。 |
| `portal/tests/test_os_quickstart.py` | Quickstart 单测：一句话→一张卡→确认即执行（三句话交互）。 |
| `portal/tests/test_os_software_flow.py` | G1 软件面流程单测（同一账本：需求→卡→授权→工单/认领→隔离执行→测试→试用→验收/返工）。 |
| `portal/tests/test_os_software_preview.py` | G1b：只读预览路由（白名单 + 路径限定）与软件面工作台页面静态检查。 |
| `portal/tests/test_quant_alert_render_contract.py` | Offline assertions for the real quant portal alert loader (Node.js required). |
| `portal/tests/test_risk_feedback_ledger.py` | 执行反馈账本 v0 最小测试：合成数据 + 临时目录，不碰 portal/data。 |
| `portal/tests/test_strategy_pool_projection.py` | Isolated deny-by-default strategy-pool projection and HTTP router tests. |
| `portal/ticket_loop.py` | 派单闭环：催办 / 回复 / 状态摘要（2026-10-25，老田反馈"派单后只能刷新、没有催促"）。 |
| `portal/tools/export_xunyou_latest.py` | 导出荀攸盘后日报最新产物 → portal/data/xunyou_latest.json（G8 Agent 日报入口数据源） |
| `portal/tune_gate.py` | 调优闸门（K-PUSH-002 B2/B3）——客户反馈调优闭环的核心状态机。 |
| `portal/verify_watch_admin.py` | Independent verification for watch_admin_snapshot (M2/M3 read-only admin). |
| `portal/voice_ingest.py` | 飞书语音消息 ingest（P0：语音 → 文本） |
| `portal/wall_adapter.py` | F4 观点墙适配器：portal/data/todays_picks.json（+ 可选 pick_verdicts.json） |
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
| `portal/zhuge_morning.py` | WO-OS-18 诸葛亮晨报生成器（06 的分层落地：仅生成器）。 |
| `portal/zhuge_morning_review.py` | 诸葛亮晨报离线审核演练账。无发送器、无调度、无身份认证与真实送达能力。 |
| `tools/_korg056_dev1.py` | K-ORG-056 驱动脚本：研发部 DEV1 按方案条目产码（主控代跑产线接口）。 |
| `tools/_korg056_dev1_fix1.py` | K-ORG-056 迭代轮：冒烟缺陷反馈单回 DEV1（产线 F4 迭代回路）。 |
| `tools/_l2_guard.py` | L2 危险操作评审门（K-STOCK-416）——宿主机级危险操作合并拦截器。 |
| `tools/_mixin_integrity.py` | P4 拆分第四道门槛：Handler 方法集 AST 断言（K-STOCK-089）。 |
| `tools/_repo_paths.py` | _repo_paths.py — 统一「主工作树根」解析（linked worktree 回落主仓）。 |
| `tools/_scan_secrets_selftest.py` | scan_secrets 自验证：生成随机假密钥夹具 → 运行扫描器 → 程序化断言。 |
| `tools/a0_patrol.py` | 田丰总监察（K-ORG-121 L4 · A0 巡察） |
| `tools/a_phase_group_board.py` | A 阶段组级看板生成器（2026-10-27 WO-A-STAGE-BOARD）。 |
| `tools/academy_research.py` | 研究院课题台账与状态流转（K-ORG-126）。 |
| `tools/academy_worker_plan.py` | 研究院课题到 Legion Worker 工单的受控编排器。 |
| `tools/adr.py` | adr.py — 架构决策记录（ADR）登记工具（K-ORG-041，落点 docs/adr/） |
| `tools/agent_census.py` | agent_census.py — 智能体点名册：谁在干活、在干嘛、手里有没有活。 |
| `tools/agent_coder.py` | K-ORG-032: Agent 编码执行器——在编岗以人设真模型产出，直接写仓库工作区。 |
| `tools/agent_daily.py` | agent_daily.py — 每日工作项点名：把"他们每天干什么"变成可检查的事。 |
| `tools/ai_onboard.py` | AI 快速接入台：一条命令给新 AI 生成虚拟 Key + 配置片段 + 注册表。 |
| `tools/alerts_shadow.py` | 8001 策略预警影子服务（alerts_shadow，K-DASH-002 立项恢复实体）。 |
| `tools/approval_inbox.py` | approval-inbox：L2 审批队列（本地 JSON，append 决策留痕）。 |
| `tools/attribution_gap_report.py` | 归因缺口报表（WO-DRILL3-ATTRGAP）——只读统计开发事件层的归因缺失。 |
| `tools/audit_p1_observability.py` | P1 可观测性只读审计。 |
| `tools/audit_strategy_acceptance.py` | 策略分层验收的只读门禁审计。 |
| `tools/audit_task_targets.py` | 逐个验证计划任务的动作目标文件是否真的存在（只读）。 |
| `tools/audit_training_candidates.py` | 只读审计训练候选清单。 |
| `tools/authority_audit.py` | ARCH1 研发域最高权限审计与看板（2026-10-27 WO-AUTHORITY-C3）。 |
| `tools/browser_verify.py` | browser-verification：Playwright 真浏览器验收（goto/click/fill/expect/screenshot）。 |
| `tools/build_agent_ide_bundle.py` | Agent IDE 注册表聚合器（K-ORG-022）。 |
| `tools/build_performance_ledger.py` | 绩效账本聚合器（K-STOCK-094，K-STOCK-093 第一步的账本雏形）。 |
| `tools/c6_daily_kpi.py` | C6 指标日报生成器（只读、确定性、零 LLM）。 |
| `tools/chart_hypothesis.py` | 漏斗层1：图形推演引擎（K-ORG-120 图先行·事佐证） |
| `tools/check_deliverables_parity.py` | deliverables 双根一致性只读护栏（漂移门禁）。 |
| `tools/check_dirty.py` | 脏文件看板/协议守卫（P0 #2，2026-10-28）。 |
| `tools/check_format_args.py` | 格式串占位符 vs 参数数 AST 静态校验（W9 门禁，K-STOCK-393）。 |
| `tools/check_import_boundaries.py` | 导入边界联动检查：业务模块的新增 import 必须仍落在对应单测白名单内。 |
| `tools/check_name_uniqueness.py` | 名称唯一性 CI 规则（P3）——编制内一名一席、治理/虚拟席必须带席位限定。 |
| `tools/check_portal_split.py` | portal 大文件拆分守卫（2026-10-28，守则 v1）。 |
| `tools/check_ports.py` | 端口注册表守卫（K-STOCK-103）：治抢端口/私起服务。 |
| `tools/check_syntax.py` | 语法门：一方文件编译检查（SyntaxError 阻断；SyntaxWarning 可按门禁策略阻断）。 |
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
| `tools/cost_report.py` | — |
| `tools/council_route.py` | council_route.py — 议会厅工作流路由器（2026-10-22）。 |
| `tools/credential_catalog.py` | CLI：统一凭据目录（JEV/数据/飞书等，只读）。 |
| `tools/credential_store.py` | 平台业务凭据库（Windows DPAPI）：Tushare/数据接口等 Key 加密存储。 |
| `tools/daily_review.py` | 军团每日复盘引擎（K-ORG-122）：每个 Agent 每天回答三问 |
| `tools/daily_subscription_job.py` | 订阅日跑编排入口（飞书中台 P3.9-lite · N-B 工位 · WO-FS-122 · 2026-10-20）。 |
| `tools/datawarehouse/__init__.py` | DATA1 数据仓治理工具包（只读）。 |
| `tools/datawarehouse/ann_history_sync.py` | ann_history 增量回填（DATA1 治理 · L2-2A 批准范围）。 |
| `tools/datawarehouse/backup_receipt_check.py` | 备份回执核对（只读）：最新备份代、产物齐全、异地 receipt、断档小时数。 |
| `tools/datawarehouse/catalog_snapshot.py` | 目录快照：catalog（当前 v12）与来源资格 registry 一致性 + 物理漂移抽样（只读）。 |
| `tools/datawarehouse/common.py` | 数据仓治理工具公共层（只读）。 |
| `tools/datawarehouse/contract_export.py` | 数据契约生成器：catalog v12 六核心表 → ODCS v3 契约（datacontract-cli 可 lint）。 |
| `tools/datawarehouse/db_health.py` | SQLite 健康检查（A4 制度化）：给 DW 周检提供 quick_check / journal / 空间 / 索引卫生。 |
| `tools/datawarehouse/db_instance_probe.py` | 数据库实例只读探活：TCP 端口（不连库、不用凭据）。 |
| `tools/datawarehouse/freshness_report.py` | 新鲜度报告：六表 prod_max（footer，不凭 mtime）+ 4 库停摆（只读 sqlite）。 |
| `tools/datawarehouse/lifecycle.py` | 资产生命周期覆盖读取（catalog/registry/freshness 共用，fail-open）。 |
| `tools/datawarehouse/lineage_impact.py` | 血缘影响分析（只读、静态）：catalog 表 → store → alias → 仓内消费方。 |
| `tools/datawarehouse/manifest_check.py` | manifest 覆盖核对：4 个 `_manifest.json` 键 ∩ catalog 表名（只读）。 |
| `tools/datawarehouse/manifest_repair.py` | manifest 修复工具：为 catalog 中 manifest_status=missing/open 的表补齐源清单条目。 |
| `tools/datawarehouse/metadata_consistency.py` | 元数据一致性检查：catalog × registry × data_lifecycle × 物理 store。 |
| `tools/datawarehouse/qualify_probe.py` | 来源资格取证探针（批次 1：tushare 深层表 26 张）。 |
| `tools/datawarehouse/quality_test.py` | 确定性质量检查（只读）：表存在/空分片/分区重复/行数/主键抽样。 |
| `tools/datawarehouse/registry_rebuild.py` | warehouse_registry v2 重建：按 catalog v12 全量对齐物理表资格台账。 |
| `tools/datawarehouse/run_all.py` | 数据仓日检总入口：串起各只读工具 → 汇总 → 可选写观察记忆。 |
| `tools/datawarehouse/staging_health.py` | staging 生命周期周检：tushare_staging 体积/沉淀/可回收候选（只读）。 |
| `tools/datawarehouse/tests/test_ann_history_sync.py` | ann_history_sync 纯本地单测（不联网、不碰生产库）。 |
| `tools/datawarehouse/tests/test_contracts.py` | contract_export 纯单测：类型映射/契约构建/check 语义（不读真实数据根）。 |
| `tools/datawarehouse/tests/test_data_catalog.py` | 数据册 v2 校验：YAML 合法 / 条目数 / id 唯一 / 字段齐全 / 修正项落盘。 |
| `tools/datawarehouse/tests/test_db_health.py` | db_health 单测：用临时 SQLite 验证 ok/warn/fail 三态（不依赖真实业务库）。 |
| `tools/datawarehouse/tests/test_freshness_expectation.py` | freshness 期望模型（freq-aware）单测。 |
| `tools/datawarehouse/tests/test_freshness_parse.py` | freshness_report._parse_day 单测（2026-10-28 BUG 修复回归）。 |
| `tools/datawarehouse/tests/test_lifecycle.py` | lifecycle 覆盖单测。 |
| `tools/datawarehouse/tests/test_manifest_repair.py` | manifest_repair 纯函数单测：扫描/清单路径/合并写入（临时目录夹具）。 |
| `tools/datawarehouse/tests/test_metadata_consistency.py` | metadata_consistency 单测（临时 catalog/registry/lifecycle 夹具）。 |
| `tools/datawarehouse/tests/test_otel_tracing.py` | otel_tracing 纯单测：默认关闭 no-op / 装饰器透传 / fail-open。 |
| `tools/datawarehouse/tests/test_qualify_probe.py` | qualify_probe 单测：纯本地 fixture，不读真实数据仓/不联网/不写 registry。 |
| `tools/datawarehouse/tests/test_staging_health.py` | staging_health 周检单测（临时目录 + 注入体积，避免真造大文件）。 |
| `tools/datawarehouse/tests/test_staging_retention.py` | staging_health 保留窗口/陈旧锁单测（2026-10-28 增强）。 |
| `tools/datawarehouse/tests/test_tools.py` | datawarehouse 工具层单测（纯本地，不依赖网络/真实数据仓/凭据）。 |
| `tools/delivery_ledger.py` | 非研发部门交付台账（周度 CSV）：init / check / summary。 |
| `tools/dep_age_check.py` | 依赖版本年龄检查（O8 P3b，warn-only，只读）。 |
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
| `tools/e2e_pipeline.py` | 端到端全自动工单驱动器 v2（K-ORG-065 · 055 计划 11-08 里程碑彩排）。 |
| `tools/e2e_report.py` | — |
| `tools/e2e_run.py` | E2E 运行器（O5）：跑 tests/e2e 首批冒烟场景，产 artifact。 |
| `tools/escort_premonday_check.py` | 周一护航只读预检（escort-premonday-check）。 |
| `tools/eval_harness.py` | eval-harness：10 条金标任务 + 断言评分 + 回归报告。 |
| `tools/event_emit.py` | R6 产线侧事件上报配方（emit_event 一段函数，设计稿 §5 镜像件）。 |
| `tools/events.py` | 零界开发进度事件层（K-STOCK-113）——开发侧全量自动回流平台的数据地基。 |
| `tools/evolve_sign_payloads.py` | Z4 判例卡待签 payload 生成器（只生成 payload，不签名、不落库）。 |
| `tools/execution_watchdog.py` | R3 失败必响巡检（联动层一期 T3）：计划任务「AI工作站执行失败巡检」动作体。 |
| `tools/export_claude_agents.py` | K-ORG-036 F6: 27 在编岗人设 → Claude Code --agents JSON 导出（子代理化）。 |
| `tools/export_nav.py` | 前端入口统一治理（K-STOCK-100）· 第一件：导航单一源导出器。 |
| `tools/export_page_linkage.py` | export_page_linkage.py — 从导航注册表生成「全量前端页面联动矩阵」。 |
| `tools/extension_lint.py` | 扩展 manifest 校验器（O3）：只读校验 extensions/*/manifest.json。 |
| `tools/extension_manifest_audit.py` | O3-P2 对账：扩展 manifest ↔ 运行配置（servers.json / tool entry）只读差异。 |
| `tools/feishu_preflight_backup.py` | 飞书中台开闸/重启前备份工具（checklist 32 缺口补齐）。 |
| `tools/fut_backtest.py` | fut_backtest: 期货量化分析层 F3（K161-B）——核心池 9 策略回测对照。 |
| `tools/fut_scan.py` | fut_scan: 国际期货扫描快照 + 核心宏观池行情（tradingview-mcp F1+F2，K161-C） |
| `tools/fut_walkforward.py` | fut_walkforward: 核心宏观池 × 9 策略 walk-forward 样本外验证（tradingview-mcp F3 严格试测） |
| `tools/gaorta_models.py` | K-STOCK-137 S-a · gaorta 模型目录生成器 |
| `tools/gate3_verdict.py` | 门 3 独立复核工具（第二意见）——只读，不写任何文件。 |
| `tools/gen_council_data.py` | gen_council_data.py — 会议厅数据生成器（S-1b，2026-10-22）。 |
| `tools/gen_data_manifest.py` | gen_data_manifest.py — 数据根轻量清单（只读，不复制任何数据）。 |
| `tools/gen_docs_index.py` | DOCS_INDEX.md 的生成与校验工具。 |
| `tools/gen_entrypoints.py` | 脚本入口登记表生成器（2026-10-28）。 |
| `tools/gen_health_card.py` | gen_health_card.py — 治理健康卡（只读，零第三方依赖）。 |
| `tools/gen_task_fixes.py` | 为 7 个"动作起不来"的计划任务生成修正版 XML（只读线上定义，只写工作区）。 |
| `tools/gen_tianfeng_attr.py` | 生成田丰（A0）属性页：legion_command/static/tianfeng.html。 |
| `tools/glm_retry_probe.py` | glm_retry_probe.py — GLM-5.3 额度探测（两阶段，外发最小化；审计 B M-03/S-01 收口）。 |
| `tools/gov_ai_case_bridge.py` | GOVAI-3：AI 判例草稿 → Z4 待签 payload → 验签入库（人类签字门）。 |
| `tools/huangzhong_accept.py` | 黄忠 · 验收守门官 v0（观察模式，**不阻断 merge**）。 |
| `tools/huangzhong_daily.py` | 黄忠每日验收观察（观察模式）：跑验收单 → 记 daily.jsonl → 周报指标。 |
| `tools/huangzhong_grade.py` | 黄忠 W1 四件套自动批改（Rubric 100 分，≥80 通过）。 |
| `tools/idem_replay_verify.py` | M0 P4 事件回放验证（只读）。 |
| `tools/init_subscription_db.py` | 订阅账本离线初始化工具（飞书中台 P3.8-lite · M-C / WO-FS-113）。 |
| `tools/inspect_format_mismatch.py` | 对 check_format_args 的 MISMATCH 逐个打印完整源码段，人工定真假。 |
| `tools/jev_audit.py` | JEV 判读账本 + 后验回填 + 校准报告（K-DATA-20261026-JEVAUDIT，P1-lite）。 |
| `tools/jev_bench.py` | jev_bench.py — JEV 两段式推理 A/B 基准（K-ORG-20261026-JEV2STAGE）。 |
| `tools/jev_mcp.py` | JEV 只读 MCP Server（stdio）——把 JEV 判定查询/校准状态暴露给 Hermes agent。 |
| `tools/jev_night.py` | jev_night.py —— 夜间 JEV 独占跑批（22:00 暂停 M3 → 07:00 恢复）。 |
| `tools/jev_posterior_poc.py` | JEV 后验校准 POC v2（K-DATA-20261026-JEVCAL，JEV × yz-substrate P0）。 |
| `tools/jev_roles_daily.py` | jev_roles_daily.py — JEV 三席「日产出物」生成器（K-ORG-20261026-JEVROLE）。 |
| `tools/killsvc_guard.py` | Hermes8787-KillSvc Gate 0 前置逻辑层（R7，纯逻辑，零注册零执行零杀进程）。 |
| `tools/knowledge_distill.py` | 知识提炼器（LCS-2 知识环）：让 AI 越来越聪明的飞轮 |
| `tools/legion_debate.py` | 军团分析 AI · LCS-1 全链固化版（K-ORG-120/LCS-1 判例产物） |
| `tools/legion_heartbeat_bridge.py` | 军团心跳桥接器（K-ORG-190 拍板⑤ 立项产物）。 |
| `tools/legion_meeting.py` | 军团晨会引擎 + 知识提炼器（LCS-2 四制一环） |
| `tools/legion_meeting_room.py` | 议事厅（G2）：多轮会议消息 + 版本化白板的持久化。 |
| `tools/legion_pulse.py` | 军团心跳模块（K-ORG-121 L1 · observe 账本补账） |
| `tools/legion_readiness.py` | 曹操军团实战就绪审计（只读，2026-10-29 WO-LEGION-READY）。 |
| `tools/legion_scheduler.py` | 军团排程调度器（K-ORG-122）：事先调度的机器化 |
| `tools/legion_tri_party_meeting.py` | 三方会议阶段一：结构化会议、双观点、分歧与人工确认。 |
| `tools/legion_worker.py` | 数字军团 Agent Worker（K-ORG-123）。 |
| `tools/litellm_route.py` | LiteLLM 路由最小接入：Key 池 → Router（多 Key/多 provider/fallback/成本）。 |
| `tools/log_rotate.py` | 日志轮转工具 v0（只读演示默认，--apply 才动手；不删除，仅归档+截断）。 |
| `tools/mcp_ro_filesystem.py` | MCP 只读文件 server：list_dir / read_file / stat（根目录白名单，防路径穿越）。 |
| `tools/mcp_ro_git.py` | MCP 只读 git server：status / log / diff（固定参数，无 shell，仓库白名单）。 |
| `tools/mcp_ro_zhaoyun.py` | 赵云 MCP 只读工具 server（stdlib，复用 zhuge_mcp_server 的只读数据函数）。 |
| `tools/mcp_security_check.py` | MCP 白名单安全自查（只读）：对齐 MCP Security Best Practices + ADR/安全策略。 |
| `tools/memory_rag.py` | memory-rag 最小版：本地 hash-vector 索引 + 判例/文档召回（无外部 Key）。 |
| `tools/merge_gated.py` | 合并质量闸（P0 #1，2026-10-28）：merge 前强制跑测试。 |
| `tools/merge_parquet.py` | merge_parquet.py — 分片 parquet 的目录级合并（统一 schema + 流式写入）。 |
| `tools/merge_preflight.py` | 合并前预检（pre-merge-commit 钩子用，2026-10-28 · P13）。 |
| `tools/mission.py` | Mission MVP CLI：文档→plan；dry-run 派工；列表。 |
| `tools/mission_evolve.py` | Mission 阶段3 CLI：采集/汇总/进化建议/候选/签字入库。 |
| `tools/mission_worker.py` | Mission Worker CLI：自动接单低风险任务 → 在 worktree 写文件 → 语法检查 → commit。 |
| `tools/model_route.py` | — |
| `tools/morning_brief.py` | tools/morning_brief.py —— 晨会三件套生成器 v0（K-ORG-056） |
| `tools/mutual_review_advice.py` | P2 异族互镜第 1 轮：本地 Qwen（非 DeepSeek 模型族）逐条复核最近 5 条 AI 治理建议。 |
| `tools/night_batch.py` | night_batch.py — 夜间长跑批处理循环器（本地模型两条积压队列）。 |
| `tools/night_test_baseline.py` | 夜跑测试基线：分套件跑 pytest，保存基线 JSON；--check 对比上一次，新增失败即 rc=1。 |
| `tools/notify_devops.py` | 开发指挥台 · 飞书卡型（K-STOCK-114 M2）——离屏遥控三卡。 |
| `tools/ollama_rag_smoke.py` | 本地 Ollama/RAG 运行与 smoke 工具（K-STOCK-160）。 |
| `tools/org_chart.py` | 研发部组织架构图生成/校验（2026-10-27 WO-ORG-CHART）。 |
| `tools/org_registry_server.py` | 组织架构图注册服务（K-ORG-053 · 架构即注册表）。 |
| `tools/org_supervision.py` | 组织监督判读脚本（K-ORG-063 · 055 计划 L2 治理线配套）。 |
| `tools/os_goal_ref/os_gate_check.py` | OS 首版「单事项闭环」合同校验器 v1.0（WP0 参考实现，非生产代码） |
| `tools/os_goal_ref/os_goal_check.py` | 零界 v1.1「事业目标—事项—成果」三级关系校验器（WP-A1 参考实现） |
| `tools/os_goal_ref/os_samples.py` | 合成事件账本样本工厂 —— 给 os_gate_check 自测与演示用。 |
| `tools/os_goal_ref/tests/test_os_gate_check.py` | os_gate_check 合同校验器回归测试（WP0 自测，全内存、不写盘）。 |
| `tools/os_goal_ref/tests/test_os_goal_check.py` | v1.1「事业目标—事项—成果」三级关系校验器回归测试（全内存）。 |
| `tools/otel_tracing.py` | OTEL 追踪助手（fail-open，默认关闭）：给关键链路加轻量 trace，不改变业务行为。 |
| `tools/p0_eval_runner.py` | P0-4 确定性基线评测 runner（30 golden）。 |
| `tools/p0_shadow_guard.py` | p0_shadow_guard.py — 诸葛亮能力底座 P0-5：最小护栏（shadow 模式）。 |
| `tools/p0_trace.py` | p0_trace.py — 诸葛亮能力底座 P0-6：Phoenix 本地 tracing。 |
| `tools/p1_citation_eval.py` | P1-1 引用率评测：对 factual 用例跑 grounded_chat，检查 [S#] 引用。 |
| `tools/p1_rag_eval.py` | P1-2 M1 recall@3 评测：8 条 factual，检查相关证据是否进入 BM25 Top3。 |
| `tools/p1_rag_observe.py` | p1_rag_observe.py — P1-2 观察期汇总（只读 grounding_usage.jsonl）。 |
| `tools/p4_ast_gate.py` | p4_ast_gate.py —— P4 拆分的 AST 断言门（K-STOCK-089 第四道门落地工具） |
| `tools/p4_batch0_rehearsal.py` | P4 批 0 预演（K-STOCK-096）：在 git archive 副本里演练 api_common.py |
| `tools/paper_bridge.py` | paper_bridge：pick_verdicts → paper.db 薄桥（K-STOCK-095，10-16 实弹）。 |
| `tools/pg_robot_watchdog.py` | PG-Robot 看门狗（K-STOCK-302） |
| `tools/phoenix_start.py` | Phoenix 幂等自启：健康检查→(在跑则退出0/不在跑则拉起并等健康)。 |
| `tools/plan_staging_cleanup.py` | 生成 tushare_staging 清理清单（只读，不删任何东西）。 |
| `tools/planner_experience.py` | Planner 经验规则 CLI：status / build-rules / ab。 |
| `tools/pool_tradability_audit.py` | pool_tradability_audit: 候选池可成交性审计（mask-first 思想落地，arXiv 2507.07107 对照） |
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
| `tools/quality_scale_check.py` | O2 质量分级检查器：席位/工具/集成三层 × Bronze/Silver（只读）。 |
| `tools/quant_readonly.py` | P2 量化只读工具 v0：复用 tushare-http MCP（258 工具），只读、白名单、不落明文。 |
| `tools/rebuild_academy_source_snapshot.py` | 重建零界学院 E 盘只读候选来源快照。 |
| `tools/reconcile_subscription.py` | 订阅账本勾稽自检（飞书中台 P3.7-lite · L-D / WO-FS-104）。 |
| `tools/redteam_daily.py` | P2 红队定时化 v0：每日抽样 + 周报（不建计划任务，先提供可调度入口）。 |
| `tools/refresh_curated.py` | 重建「整理层」tushare_curated（raw → curated）。 |
| `tools/repo_map.py` | K-ORG-035/036 F2: 仓库地图（借 Aider RepoMap 范式，自研实现）。 |
| `tools/req_lines_check.py` | O8 P2：分线 lock 快照校验（只读）。 |
| `tools/research/ann_classify.py` | 公告分类 v2：columns_txt（官方类型）+ 标题关键词 双层规则。 |
| `tools/research/dragon_tiger.py` | H3 龙虎榜机构席位跟单：机构净买入 → 未来 5/10 日收益（行业×规模中性 + OOS + 成本）。 |
| `tools/research/event_study.py` | H1 公告事件研究：公告披露 → 次日/5 日市场调整超额（CAR）。 |
| `tools/research/event_study2.py` | H11（3 年公告 CAR，行业中性）+ H2（PEAD，持有 5/10/20 日）研究工具。 |
| `tools/research/factor_strat.py` | H7 财务因子分层：PIT 财务指标（价值/质量/成长）月度 IC 与五分位多空。 |
| `tools/research/flow_divergence.py` | H4 资金流背离：moneyflow 净流入 × 当日涨跌 → 未来 5/10 日行业×规模中性收益。 |
| `tools/research/h2v2_earnings_surprise.py` | H2 v2：真实盈余惊喜 → PEAD（公告后 5/10/20 日漂移）。 |
| `tools/research/hypotheses.py` | 假设台账 CLI：research/hypotheses.db 的初始化/播种/查询/更新。 |
| `tools/research/jev_attribution.py` | H6 JEV 决策归因：bias / event_type / playbook / regime × 收益与胜率。 |
| `tools/research/quality_reversal.py` | H14 质量反向：高 ROE 跑输是否在估值分层内仍存在（剥离质量-估值交互）。 |
| `tools/research/tests/test_ann_classify.py` | ann_classify 单测：columns_txt 优先、标题兜底、覆盖率口径。 |
| `tools/research/tests/test_event_study.py` | event_study 纯函数单测（合成行情，不读真实数据）。 |
| `tools/research/tests/test_event_study2.py` | event_study2 纯函数单测（行业中性映射 / stats / 分类调用）。 |
| `tools/research/tests/test_event_study_promote.py` | event_study v2（晋级三关）纯函数单测：市值分组中性 + 成本后净 CAR 口径。 |
| `tools/research/tests/test_hypotheses.py` | hypotheses 台账单测（临时 DB）。 |
| `tools/research/tests/test_jev_attribution.py` | jev_attribution 单测（临时 sqlite 夹具；aw_decision 走外部不测）。 |
| `tools/research/tests/test_review_fixes.py` | 代码审查补测：盈余方向解析 / 分位排名 / 边界。 |
| `tools/research/weekly_review.py` | 研究周复盘：台账状态汇总 + 本周新增报告 + 四关进展（供周五例会）。 |
| `tools/retro_brief.py` | R5 复盘回流生成器（联动层二期 R5）：计划任务「AgentRetrospectBrief」动作体。 |
| `tools/riskguard_postmortem.py` | RiskGuard 误杀后验复算分析器 v0.1（K-ORG-113 · S3 备料·只读）。 |
| `tools/role_group_model.py` | 研发军团角色分工与分组适配模型校验（2026-10-27 WO-ROLE-GROUP-MODEL）。 |
| `tools/rotate_logs.py` | rotate_logs.py — 日志轮转 + 回收站保留（默认 dry-run，T7 轨道） |
| `tools/rotate_quantdinger_keys_20261015.py` | quantdinger 密钥轮换（P0 收口 2026-10-15，用户拍板）。 |
| `tools/ruff_ratchet.py` | ruff F/E9 棘轮：脏账只降不升（默认基线在 tools/ruff_baseline.json）。 |
| `tools/run_contract_view.py` | O6 P1/P2d：从现有 traces.sqlite **只读派生** Run/Event 契约视图（支持任务级聚合）。 |
| `tools/safe_delete.py` | safe_delete.py — 按 AGENTS §7 的要求删除：默认进回收站，永久删除需显式开关。 |
| `tools/sandbox_verify.py` | sandbox-verification：临时 git worktree + 可选 venv + pytest 证据包。 |
| `tools/scan_secrets.py` | scan_secrets — 防泄漏只读扫描器（pre-commit 闸门） |
| `tools/scan_task_paths.py` | 扫全部已知计划任务的动作路径，找同一类"起不来"隐患（只读）。 |
| `tools/seat_capability_inject.py` | seat_capability_inject.py — 席位能力底座注入器（2026-10-23 00:20；2026-10-26 TFSEAT：田丰补全 15 能力 + 15 技能卡；2026-10-27 ACAD1 P0/P1：+acad1/strat1/back1）。 |
| `tools/seat_evidence_packet.py` | seat_evidence_packet.py — 田丰取数门面：确定性证据包生成器（零 LLM / 零网络 / 只读源）。 |
| `tools/seat_iteration.py` | 曹操下属能力迭代体系（2026-10-29 WO-SEAT-ITER）。 |
| `tools/seat_learning.py` | 单席位能力学习计划（2026-10-28 WO-LEARNING-DEV7）。 |
| `tools/seat_memory_sync.py` | seat_memory_sync.py — 三席位三层记忆装配器（一次性注入工具，2026-10-22）。 |
| `tools/seat_router.py` | CLI：席位路由/虚拟 Key 管理。 |
| `tools/seat_system_audit.py` | seat_system_audit.py — 席位系统完整性审计（只读，2026-10-26）。 |
| `tools/seat_write_guard.py` | seat_write_guard.py — 田丰 v3 L2 写路径白名单/执行确认（检测层）。 |
| `tools/sec2_review.py` | sec2_review.py — SEC2 方案级走形检查固化工具（K-ORG-054 · G3 闭环）。 |
| `tools/secret_scan_gitleaks.py` | gitleaks 包装器：调用本机 gitleaks 扫描指定目录（默认主仓），输出摘要。 |
| `tools/secrets/apply_rotation_fillback.py` | 批量应用「轮换回填表」到本地目标（A3 · R1/R2/R4/R7） |
| `tools/secrets/mcp_preflight.py` | MCP 配置预检/应用/回滚：解决「.mcp.json 只留占位符、重启前必须渲染」的运维缺口。 |
| `tools/secrets/render_mcp.py` | render_mcp.py —— 从 ``.mcp.json.template`` + 密文 token 渲染 ``.mcp.local.json``。 |
| `tools/secrets/rotate_item.py` | R2/R4/R7 凭据本地轮换引导（A3 · 2026-10-27） |
| `tools/secrets/rotate_token.py` | Tushare MCP token 轮换收口：安全输入 → 加密入库 → 渲染 → 审计。 |
| `tools/secrets/rotate_tushare_token.py` | R1 Tushare MCP token 轮换引导（A3 E1-E9 · 2026-10-27） |
| `tools/secrets/tests/test_rotation_helpers.py` | mcp_preflight / rotate_token 纯函数单测（不联网、不碰真实保险库）。 |
| `tools/secrets/tests/test_vault.py` | tools/secrets 单元测试：加密→解密 round-trip、MANIFEST 校验、fail-closed。 |
| `tools/secrets/vault.py` | secvault —— L1 凭据保险库（age 优先 / cryptography AES-GCM 兜底）。 |
| `tools/security_gate.py` | security-gate：密钥/AST 危险模式扫描 + 证据落盘（只读）。 |
| `tools/selftest_detectors.py` | selftest_detectors.py — 检测器自证（判例 04 的工具化）。 |
| `tools/shadow_eval.py` | shadow_eval.py — D3 影子评测：行为对齐人设（K-ORG-040）。 |
| `tools/sign_approval.py` | 离线签名器（人类专用；Agent 不得调用）。 |
| `tools/sop_link_check.py` | docs 本地链接检查（WO-DRILL5-SOPLINK）：防 SOP/ADR 文档断链。 |
| `tools/spec_flow.py` | spec-driven-delivery：规范→计划→任务→收敛 的产物链与检查。 |
| `tools/subscription_smoke.py` | G3 订阅全流程演练脚本（P3.10-lite · O-C 工位 · WO-FS-133）。 |
| `tools/sync_pipeline_to_strategies.py` | E5 卡墙↔收编流水线回写工具。 |
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
| `tools/test_d2_gates.py` | D2 门禁工具单测：ruff ratchet 解析/diff、夜跑基线解析/diff。 |
| `tools/test_deep_shell.py` | 深屏骨架推广契约：/picks.html 静态接入 + /strategies 生成器接入。 |
| `tools/test_delivery_ledger.py` | §6.2 交付台账工具单测。 |
| `tools/test_dep_age_check.py` | O8 P3b 依赖年龄检查单测（不联网）。 |
| `tools/test_dev_acceptance_audit.py` | O4 验收独立性审计单测。 |
| `tools/test_devtask_tasks_save.py` | devtask._save 并发安全单测（K-QUANT-20261026-DEVTASKSAVE）。 |
| `tools/test_diagnostics.py` | O7 diagnostics 单测（不依赖真实服务）。 |
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
| `tools/test_huangzhong_accept.py` | 黄忠验收工具单测（fake git/证据，不联网）。 |
| `tools/test_huangzhong_daily.py` | 黄忠 W1 单测：样例文档在位 + 每日观察/周报工具。 |
| `tools/test_huangzhong_grade.py` | 黄忠 W1 批改工具单测（临时提交目录，不联网）。 |
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
| `tools/test_night_batch_stall.py` | night_batch 无推进退避重试 + 2026-10-25 EXEC-R13 超时改造回归。 |
| `tools/test_night_dep_age.py` | O8 P3c 夜跑 dep_age 段单测（不联网）。 |
| `tools/test_ollama_lock_deadline.py` | OS-FIX-JEVLOCK-20261026：ollama_lock.tick 阻塞上限回归。 |
| `tools/test_ollama_rag_smoke.py` | — |
| `tools/test_os_entry_ui_state.py` | OS entry Week2 契约：tokens/状态模板/键盘入口。 |
| `tools/test_p0_shadow_guard.py` | p0_shadow_guard 单测（P0-5）。 |
| `tools/test_p0_trace.py` | p0_trace 单测（P0-6）：离线、无 Phoenix 依赖。 |
| `tools/test_p1_closed_loop.py` | 端到端闭环演练（合成数据、临时仓库；不碰生产账本）。 |
| `tools/test_p1_rag_observe.py` | p1_rag_observe 单测（离线，临时 JSONL）。 |
| `tools/test_page_shell.py` | W1 PageShell v1 契约。 |
| `tools/test_page_shell_w4.py` | W4 PageShell adopted/redirect 契约。 |
| `tools/test_paper_dual.py` | 模拟仓深色工作台与双版本 UI 规范的契约。 |
| `tools/test_paper_simple.py` | P0 /paper 首屏减法契约。 |
| `tools/test_phoenix_start.py` | Phoenix 自启幂等化单测（不联网、不真起进程）。 |
| `tools/test_picks_simple.py` | P2 /picks 默认精简契约。 |
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
| `tools/test_ui_keyboard_audit.py` | ui_keyboard_audit 单测。 |
| `tools/test_ui_palette.py` | Week3 命令面板/密度/键盘审计契约。 |
| `tools/test_ui_ux_audit.py` | ui_ux_audit 单测（离线）。 |
| `tools/test_ui_visual_baseline.py` | ui_visual_baseline 单测（不启动浏览器）。 |
| `tools/test_weekly_dep_triage.py` | O8 P3d 依赖 triage 周报段单测（不联网）。 |
| `tools/test_wip_check.py` | §6.2 WIP 检查单测。 |
| `tools/test_worker_eligibility.py` | — |
| `tools/test_worker_llm_parse.py` | — |
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
| `tools/test_zhuge_p1_567.py` | P1-6/P1-7 离线单测：规划分解 + 自我反思（不联网/不调模型）。 |
| `tools/test_zhuge_rag.py` | zhuge_rag BM25 v0 单测（离线，临时 docs）。 |
| `tools/test_zhuge_redact.py` | P1-8 修复单测：L5 输出内部路径脱敏。 |
| `tools/test_zhuge_redteam.py` | P1-8 红队判定器离线单测（fake responder，不调模型）。 |
| `tools/test_zhuge_suggestion_map.py` | 建议→决策映射 v1：决策记录 suggestion_ids + brief 精确 join（临时仓库）。 |
| `tools/test_zhuge_wargame.py` | P2 兵棋单测（确定性，不联网）。 |
| `tools/tests/test_academy_activation4.py` | B 激活 4 席：能力卡定义/触发/工具属性测试（2026-10-29）。 |
| `tools/tests/test_agent_census_parse.py` | agent_census 时间解析：ISO 带时区/微秒不再 silent-except。 |
| `tools/tests/test_agent_census_status.py` | 系统态势卡的纯函数测试（K-ORG-20261026-BOARD2）。 |
| `tools/tests/test_agent_daily_on_demand.py` | on_demand 证据类型单测（K-ORG-20261026-AGENTHEALTH2）。 |
| `tools/tests/test_agent_daily_port.py` | agent.daily 端口证据接线单测（K-ORG-20261026-AGENTHEALTH）。 |
| `tools/tests/test_auto_rebuild_ide_bundle.py` | IDE bundle 收尾提交并发修复单测（2026-10-29）。 |
| `tools/tests/test_batch_backlog_level.py` | batch_backlog 判级单测（K-ORG-20261026-BACKLOGDUTY）。 |
| `tools/tests/test_check_dirty.py` | check_dirty 纯逻辑单测（2026-10-28，P15）。 |
| `tools/tests/test_check_syntax.py` | check_syntax 单测（临时 repo 夹具，禁用缓存写入）。 |
| `tools/tests/test_event_emit.py` | R6 产线侧 emit 配方单测（tools/event_emit.py）。 |
| `tools/tests/test_hermes_push_health.py` | Hermes 推送健康判级单测（K-ORG-20261026-HERMESFIX）。 |
| `tools/tests/test_import_boundaries.py` | 导入边界联动检查单测（临时模块/测试夹具）。 |
| `tools/tests/test_jev_audit.py` | JEV 判读账本纯函数单测（K-DATA-20261026-JEVAUDIT）。 |
| `tools/tests/test_jev_backfill_loop.py` | JEV 回填主循环控制函数单测（K-ORG-20261026-JEVLOOPTEST）。 |
| `tools/tests/test_jev_calibration.py` | jev.calibration 健康卡检查单测（K-ORG-20261026-JEVCALHC）。 |
| `tools/tests/test_jev_prefilter.py` | jev_prefilter 规则单测（K-ORG-20261026-JEV2STAGE）。 |
| `tools/tests/test_jev_roles_daily.py` | jev_roles_daily 纯函数单测（K-ORG-20261026-JEVROLE）。 |
| `tools/tests/test_killsvc_guard.py` | Hermes8787-KillSvc Gate 0 前置逻辑层单测（R7）。 |
| `tools/tests/test_l2_guard_fp.py` | _l2_guard 误报修复回归（WO-GUANYU-L2FIX-20261027，2026-10-27）。 |
| `tools/tests/test_merge_gated.py` | merge_gated 纯逻辑单测（2026-10-28，P7）。 |
| `tools/tests/test_merge_preflight.py` | merge_preflight 纯逻辑单测（2026-10-28，P13；P3c 依赖提示）。 |
| `tools/tests/test_ops_tools.py` | 四个运维工具的 pytest 契约测试（只新增测试，不改被测代码）。 |
| `tools/tests/test_rate_freshness.py` | 利率新鲜度检查单测（K-DATA-20261026-SHIBOR）。 |
| `tools/tests/test_scan_secrets_fp.py` | scan_secrets 误报修复回归（WO-GUANYU-SCANFIX-20261027，2026-10-27）。 |
| `tools/tianfeng_dispatch.py` | 田丰派工回执台账（dispatch-live）。 |
| `tools/tool_matrix_check.py` | tool_matrix_check.py —— 岗×操作级二维工具白名单校验器（K-ORG-080/N9 · 087/T4） |
| `tools/trace_critic.py` | trace_critic.py — 轨迹 Critic（K-ORG-080/N8） |
| `tools/trace_log.py` | observability-trace：本地 JSONL trace（工具链/时延/成本/状态）。 |
| `tools/triples_outcome_backfill.py` | triples_outcome_backfill: triples.jsonl 盈亏标签回填器（UMP 边裁影子原型第一步，K194-C/K195） |
| `tools/ui_keyboard_audit.py` | ui_keyboard_audit.py — 静态键盘焦点顺序审计（无浏览器依赖）。 |
| `tools/ui_ux_audit.py` | ui_ux_audit.py — 静态 UI/UX 审计（设计令牌/可访问性/状态/动效/内联样式）。 |
| `tools/ui_visual_baseline.py` | ui_visual_baseline.py — 通过 Chrome DevTools Protocol 采集视觉基线。 |
| `tools/unify_nav.py` | 前端入口统一治理（K-STOCK-100）· 第二件：静态页注入器 + 同步守卫。 |
| `tools/us_scan.py` | us_scan: 美股全市场市值榜快照（tradingview-mcp P1a 扩池工具） |
| `tools/us_sentiment.py` | us_sentiment: tradingview-mcp P1b 美股情绪试点（K178，K147-C P1b）。 |
| `tools/validate_json.py` | JSON 写侧前置校验工具（M3-P5；tasks.json 损坏事故判例落地）。 |
| `tools/verify_backups.py` | 只读备份校验器 —— 校验 <workspace>/_vault 下 MANIFEST.txt 与实体文件是否一致。 |
| `tools/watchdog_probe_8093.py` | 8093 看门狗固定探针（K-ORG-114 · 哨薄壳化判例 K-STOCK-197~199）。 |
| `tools/weekly_dep_triage.py` | O8 P3d：依赖 triage 周报段（只读、warn-only、不阻断）。 |
| `tools/weekly_report_html.py` | 研发部周报 Markdown→HTML 渲染器（WO-DRILL2-WEEKLYHTML）。 |
| `tools/wip_check.py` | 交付型席位 WIP 检查（只读 tasks.json + org.json）。 |
| `tools/xsec_profile.py` | 三线交叉标准件 xsec_profile v1.0（总方案 B1 · K-ORG-119 路由注册工具） |
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
| `tools/zhuge_planner.py` | 诸葛 P1-6：规划与分解 v0（规则模板，确定性、零依赖、可测）。 |
| `tools/zhuge_rag.py` | zhuge_rag.py — P1-2 M1：诸葛亮 BM25 v0 检索（纯 stdlib，零依赖）。 |
| `tools/zhuge_redteam.py` | 诸葛 P1-8：红队/对抗演练 v0（注入/越狱/幻觉）。 |
| `tools/zhuge_reflector.py` | 诸葛 P1-7：自我反思/一致性 v0（确定性规则，零依赖）。 |
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
| [ADR-009-dev-legion-screen-contract.md](adr/ADR-009-dev-legion-screen-contract.md) | ADR-009 研发军团作战屏 v1 数据契约（只读） |
| [ADR-009-port-registry-entry.md](adr/ADR-009-port-registry-entry.md) | ADR-009：端口注册表为唯一真源，一切绑 127.0.0.1 |
| [ADR-010-credentials-vault.md](adr/ADR-010-credentials-vault.md) | ADR-010：凭据集中到 `_vault`，密文副本 + token 仅环境注入 |
| [ADR-011-data-roots.md](adr/ADR-011-data-roots.md) | ADR-011：唯一数据根 + 运行态根分离，`work` 根退役 |
| [ADR-012-l2-five-elements.md](adr/ADR-012-l2-five-elements.md) | ADR-012：L2 动作五要素审批，沉默≠同意 |
| [ADR-013-scope-and-docs-discipline.md](adr/ADR-013-scope-and-docs-discipline.md) | ADR-013：单机个人 OS 的范围边界与文档纪律 |
| [ADR-014-agent-run-contract.md](adr/ADR-014-agent-run-contract.md) | ADR-014：Agent 运行契约 v1（run / event / conversation） |

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
