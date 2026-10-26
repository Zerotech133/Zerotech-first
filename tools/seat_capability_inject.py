"""seat_capability_inject.py — 三席位能力底座注入器（2026-10-23 00:20；2026-10-26 TFSEAT：田丰补全 15 能力 + 15 技能卡）。

把「AI 该有的记忆」按三层打进各席位底层：
  ① 技能表 skills/CAPABILITIES.json —— 本席位全部能力清单（能干什么/怎么调/边界）
  ② 技能卡 skills/<name>/SKILL.md —— 逐技能操作卡（缺失才建，不覆盖已有）
  ③ 工作记忆骨架 memories/WORKING_MEMORY.md —— 席位视角的长期工作记忆索引

设计纪律：
  - 诸葛亮(OpenClaw workspace) 已有 3 技能卡：只补缺不重写；
  - 田丰/赵云(Hermes profiles) 按职责面注入完整能力卡（2026-10-26 田丰 15 项）；
  - 能力清单对齐 SEAT_CARD 职责面 + 全仓既有工具真源（每项能力都指向真实工具/命令，不虚编）；
  - 幂等：CAPABILITIES.json 可重生成；SKILL.md 存在即跳过。

用法：
    python tools/seat_capability_inject.py --dry-run
    python tools/seat_capability_inject.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _repo_paths import repo_root  # noqa: E402

REPO = str(repo_root(HERE))

HOMES = {
    "tianfeng": os.path.expanduser("~/.hermes/profiles/tianfeng"),
    "zhaoyun": os.path.expanduser("~/.hermes/profiles/zhaoyun"),
    "zhuge": os.path.join(REPO, "legion_agents", "zhuge", "workspace"),
}

# ---------------- 能力全集（按席位职责面 × 全仓真实工具） ----------------
# 每项：name / what（能干什么）/ how（真实调用）/ boundary（边界）/ status
# status 枚举（2026-10-26 A2/C2 扩展）：ready | degraded_relay | degraded_rule |
# pending_wiring | paper；verified_at/verified_scope = 运行态证据时点与范围
# （静态卡 ≠ 运行态可用；审计通过 ≠ 能力可用）。
CAPABILITIES = {
    "tianfeng": [
        {"name": "devtask", "what": "工单全生命周期：建单/列表/合并/收口", "how": "python tools/devtask.py new|list|merge", "boundary": "五守卫全开；pathspec 精确提交", "status": "ready"},
        {"name": "ticket-gate", "what": "归口验收：复核互审单、经验卡签字链核对", "how": "读 deliverables 互审文书 + Z2 实验卡核对签字", "boundary": "复核不改写原始记录", "status": "ready"},
        {"name": "council-route", "what": "分发路由建议（定稿后派工组合）", "how": "python tools/council_route.py --tags …", "boundary": "只产建议不直接建单", "status": "ready"},
        {"name": "health-card", "what": "治理健康卡 21 项生成与解读", "how": "python tools/gen_health_card.py", "boundary": "读不到=红不是绿", "status": "ready"},
        {"name": "task-exit-audit", "what": "计划任务退出码账本+窗口对比", "how": "python agent_platform/tools/task_exit_audit.py --scan|--window", "boundary": "只读扫描不改任务定义", "status": "ready"},
        {"name": "experience-review", "what": "Z4 经验复核（赵云写田丰读）", "how": "读 ~/.hermes/profiles/zhaoyun/experience/*/EXPERIENCE.md 四字段条目", "boundary": "无签字不结案（Z2）", "status": "ready"},
        {"name": "z-series", "what": "Z1-Z5 进化引擎链（profile/实验卡/匿名横评/经验库/防穿越）", "how": "agent_platform/engine/{experiment_card,anonymous_review,experience_store,lookahead_guard}.py", "boundary": "真实经验库 0 条待签", "status": "ready"},
        {"name": "memory-card", "what": "席位三层记忆（L1/L2/L3）", "how": "SEAT_CARD.json（tools/seat_memory_sync.py 生成）", "boundary": "L3 只引用不复制", "status": "ready"},
        {"name": "council-hall", "what": "议会厅发言与议题推进", "how": "落盘 deliverables 文书 + tools/gen_council_data.py 刷新", "boundary": "发言=文书，页面只投影", "status": "ready"},
        {"name": "docs-index", "what": "文档现行性索引维护", "how": "python -B tools/gen_docs_index.py", "boundary": "索引同提交（S3）", "status": "ready"},
        {"name": "llm-glm", "what": "GLM 云端推理（归口复核智能层）", "how": "agent_platform/gateway llm_gateway（glm-5.3）", "boundary": "glm/nova 不传 model 用实例绑定", "status": "ready"},
        {"name": "mutual-review", "what": "互审单复核：评审对家席位产出（MUTUAL-REVIEW 工单）", "how": "读 deliverables 互审文书/worktree diff，工具 tools/mutual_review_advice.py 可辅助", "boundary": "执行者不结案；分歧上议会厅；复核不改写原始记录", "status": "ready"},
        {"name": "case-sign-chain", "what": "判例签字链核对：draft→待签 payload→本人签→公钥验签→入 Z4（只核对/验签）", "how": "python tools/gov_ai_case_bridge.py --emit / --apply（私钥老田持有，田丰只验签不签）", "boundary": "未签/篡改/未配公钥一律拒；田丰不代签", "status": "ready"},
        {"name": "governance-ai", "what": "治理 AI draft 复核：异常归纳/优先级建议/判例草稿", "how": "python agent_platform/tools/gov_ai.py --action all --json（只读 draft）", "boundary": "只读 draft，不写卡/经验库/工单", "status": "ready"},
        {"name": "worktree-hygiene", "what": "工单收口检查：worktree TTL/分支合并核对（S1）", "how": "git worktree list + git merge-base --is-ancestor + devtask 收口提示", "boundary": "只读检查；删除类进回收站或交赵云", "status": "ready"},
    ],
    "zhaoyun": [
        {"name": "task-exit-audit", "what": "治理扫描主工具：46 任务退出码+窗口三分类", "how": "python agent_platform/tools/task_exit_audit.py --scan --window 24", "boundary": "只读；Disabled/267011 不计败", "status": "ready"},
        {"name": "health-card", "what": "健康卡运行与红黄绿解读", "how": "python tools/gen_health_card.py", "boundary": "检查器先自证（判例）", "status": "ready"},
        {"name": "task-launch-probe", "what": "S-0 能力正测试（一次性任务 rc 断言）", "how": "ProbeP1 模式：建临时任务→/run→断言 rc∈{0,3}→删", "boundary": "探针用后即清", "status": "ready"},
        {"name": "backup-bundles", "what": "备份链：bundle+manifest+校验+轮转", "how": "powershell tools/backup_bundles.ps1 -DryRun|执行", "boundary": "异地已落地（2026-10-26 回执 7/7 sha256）", "status": "ready"},
        {"name": "worktree-hygiene", "what": "工作树 TTL/回收/收口（S1）", "how": "git worktree list + devtask 收口提示", "boundary": "删除类进回收站/先归档", "status": "ready"},
        {"name": "docs-archive", "what": "一话题一归档（S4）：根目录 md >24h 迁移", "how": "移 docs/audit/ 或 _archive/", "boundary": "回收站纪律", "status": "ready"},
        {"name": "experience-write", "what": "Z4 supervision 域经验写入（赵云写田丰读）", "how": "~/.hermes/profiles/zhaoyun/experience/supervision/ 四字段（cue/lesson/evidence/confidence）", "boundary": "登记即去重键；采纳需老田签", "status": "ready"},
        {"name": "mutual-review", "what": "互审单：评审对家产出（MUTUAL-REVIEW 工单）", "how": "读对家 deliverables/worktree diff → 评审批文书", "boundary": "执行者不结案；分歧上议会厅", "status": "ready"},
        {"name": "lookahead-guard", "what": "Z5 防穿越断言（数据时点校验）", "how": "python agent_platform/engine/lookahead_guard.py --selftest", "boundary": "规则精确优先+pass-through 反证", "status": "ready"},
        {"name": "council-hall", "what": "议会厅治理面发言", "how": "落盘评审/回执文书", "boundary": "只产建议与证据（V3.1）", "status": "ready"},
        {"name": "harness-runner", "what": "DeepSeek Harness 执行框架（cron/重试/隔离）", "how": "执行体=DSH 会话（DeepSeek Harness 桌面版）；定时/重试由 governance_clock 承担；Harness² 本体不在位，按需再立项", "boundary": "执行体=DSH 会话；不依赖 Harness² 本体", "status": "ready"},
        {"name": "llm-deepseek", "what": "DeepSeek 真模型推理（异常归纳/建议生成/判例抽取）", "how": "python agent_platform/engine/deepseek.py --selftest（默认 deepseek-flash；key 读 _vault/secrets/deepseek_api_key.txt 或 DEEPSEEK_API_KEY）", "boundary": "key 私有区不落仓；异族互镜的一极", "status": "ready"},
        {"name": "governance-ai", "what": "治理 AI：异常归纳/优先级建议/判例草稿（DeepSeek flash）", "how": "python agent_platform/tools/gov_ai.py --action all --json（draft 落 data/governance_ai；健康卡 governance.ai_advice 判活）", "boundary": "只产 draft，不写卡/经验库/工单；失败黄不阻塞治理链", "status": "ready"},
        {"name": "case-sign-chain", "what": "判例草稿签字链：AI draft→待签 payload→本人签→公钥验签→入 Z4", "how": "python tools/gov_ai_case_bridge.py --emit / --apply（私钥由老田持有，Agent 只验签）", "boundary": "未签/篡改/未配公钥一律拒；strict 模式 verified=True 才入库", "status": "ready"},
    ],
    "zhuge": [
        {"name": "adversarial-review", "what": "反向质询（已有技能卡）", "how": "skills/adversarial-review/SKILL.md；OpenClaw agent zhuge skills 已登记；一次性 infer 是否加载技能卡 ⏳未验证", "boundary": "只给建议与质询卡，不代签；通道级可用", "status": "ready", "verified_scope": "skill_card_and_agent_binding", "verified_at": "2026-10-26T23:33+08:00"},
        {"name": "evidence-audit", "what": "证据审计（已有技能卡）", "how": "skills/evidence-audit/SKILL.md；通道级可用，技能注入未验证", "boundary": "已核实/推断/待验证三分", "status": "ready", "verified_scope": "skill_card_and_agent_binding", "verified_at": "2026-10-26T23:33+08:00"},
        {"name": "pre-mortem", "what": "事前验尸（已有技能卡）", "how": "skills/pre-mortem/SKILL.md；L5 实测答复形态符合 pre-mortem（3 条失败风险）", "boundary": "失败路径枚举；通道级可用，技能注入未验证", "status": "ready", "verified_scope": "skill_card_and_agent_binding", "verified_at": "2026-10-26T23:33+08:00"},
        {"name": "zhuge-brief", "what": "诸葛亮建议引擎（规则版，七源归集）", "how": "portal/api_zhuge_brief.py + /api/zhuge/brief（8091/8734 实测 200，suggestions≈30/gaps=0）", "boundary": "页面标注'规则归集非模型推理'；不派单不审批", "status": "ready", "verified_scope": "runtime_http", "verified_at": "2026-10-26T23:32+08:00"},
        {"name": "os-suggest", "what": "OS 首页建议呈现（四视图，消费 /api/zhuge/brief）", "how": "portal/os_entry_v9_prod.html 消费；实测 8734 页面 200 且前端同语义适配", "boundary": "只读归集不造第二账；页面自述未挂定时推送", "status": "ready", "verified_scope": "runtime_page", "verified_at": "2026-10-26T23:39+08:00"},
        {"name": "council-advise", "what": "议会厅建议面表态（该不该做/优先级）", "how": "portal/council_ops.py 规则代行（CHAIR_POOL/建议面代行语句）→ tools/gen_council_data.py 刷新 portal/data/council.json；'看法文书落 deliverables' 无代码路径（人工/文档动作）", "boundary": "不派单不裁决；当前为规则代行（非模型表态）；council.json 的 zhuge online/verifiable 非通道判活", "status": "degraded_rule", "verified_scope": "runtime_rule", "verified_at": "2026-10-26T23:35+08:00"},
        {"name": "brief-trend", "what": "建议趋势与采纳率跟踪", "how": "设计：portal/harvest_adoption.py（access log）→ portal/data/zhuge_adoption.jsonl + portal/api_zhuge_adoption.py；实测 2026-10-26 23:4x：/api/zhuge/adoption=404（未注册）、账本 0B、无排程", "boundary": "接线判据=路由 200 + 账本非空 + 归集器在计划任务中（三者同时满足）；未接线不虚报 ready", "status": "pending_wiring", "verified_scope": "runtime_absent", "verified_at": "2026-10-26T23:40+08:00"},
        {"name": "llm-carrier", "what": "真模型推理通道（目标 GPT-6.0；当前经 OpenClaw agent-config 载体以 deepseek/deepseek-chat 中转真实作答）", "how": "OpenClaw agent zhuge（agents.entries.zhuge.model.primary，现 deepseek/deepseek-chat）→ L5 portal/comm_zhuge.py、8796 engine/openclaw_cli.py+legion_command/serve.py；切换 ops/zhuge_carrier_switch.ps1", "boundary": "GPT6 真身 itokengate/gpt-6-sol 待跨境网络；env key 非前置（密钥在 OpenClaw secret store）；中转必须标 openclaw_cloud+carrier+relay=true，不冒充 GPT6；真身恢复后按 agent-config 自动回 openclaw_gpt", "status": "degraded_relay", "mode": "openclaw_cloud", "carrier": "deepseek/deepseek-chat", "relay": True, "gpt6_ready": False, "verified_scope": "runtime_l5_and_8796", "verified_at": "2026-10-26T23:33+08:00", "evidence": "work/zhuge-role-audit-20261026/phase2/_probe_consult.py"},
        {"name": "carrier-switch", "what": "载体三级自动切换（gpt6→ms1max35→ms1max177→deepseek，取当前可达最高档）", "how": "ops/zhuge_carrier_switch.ps1 -DryRun|-Apply；Windows 计划任务「AI工作站诸葛载体自动切换」每 10 分钟 -Apply -Json；日志 C:\\Temp\\zhuge_carrier_switch.log", "boundary": "探测 2s TCP；Apply=备份+openclaw config patch+validate+真实推理自检，失败自动回滚；切换后标签按 agent-config 实读自动变、无需重启；计划任务增删属 L2；自 22:42 注册以来始终 no-change（真实切换路径未触发）", "status": "ready", "apply_path_verified": False, "verified_scope": "runtime_probe_and_task", "verified_at": "2026-10-26T23:12+08:00"},
    ],
}

# 技能卡（缺才建）：田丰/赵云各补最关键的操作卡
SKILL_CARDS = {
    "tianfeng": {
        "ticket-gate": """---
name: ticket-gate
description: 归口验收：读互审单/Z2实验卡/工单证据链，核对签字与判例适用；田丰复核专用。
---

# 归口验收

1. 读交付文书全文与引用证据（命令输出/commit/文件路径），独立重验关键主张，不采信自述。
2. 核对签字链：Z2 无签字不结案；approval_gate 凭据完整；互审分歧是否已收敛（议会厅议题灯）。
3. 判定三态：通过 / 驳回（列缺陷）/ 升级老田（列裁决点）。
4. 复核不改写原始记录；意见以新文书落盘（deliverables + git）。
5. 路由建议交 council_route.py 产，田丰只执行派单（devtask new）不扩大建议权。
""",
        "experience-review": """---
name: experience-review
description: 复核赵云写入的 Z4 supervision 经验条目（四字段）；去重与升级建议。
---

# 经验复核（赵云写田丰读）

1. 读 ~/.hermes/profiles/zhaoyun/experience/<域>/EXPERIENCE.md，逐条核对 cue/lesson/evidence/confidence。
2. evidence 必须可回溯（commit/文书/账本行号）；缺证据标记 confidence≤0.3 待补。
3. 去重：同 cue 已有条目 → 合并引用，不重复登记。
4. 升级建议：confidence≥0.8 且跨席位适用 → 建议进 SEAT_CARD 硬规则（报老田签）。
5. 采纳状态以老田 approval_gate 签字为准；未签字一律「未生效」。
""",
        "devtask": """---
name: devtask
description: 工单全生命周期：建单/列单/合并/收口；归口席的派单唯一入口。
---

# 工单（devtask）

1. 建单：`python tools/devtask.py new <TID> "<目标/验收>" --project quant`；一单一事。
2. 列单/查单：`python tools/devtask.py list`；doing 单每日收口检查（S1）。
3. 合并只走 `devtask.py merge <TID>`：五守卫全开、pathspec 精确提交；禁直推 master。
4. 收口：merge 后回收 worktree、记回执；未合并/aborted 单写清原因。
5. 建单前先查重（同目标已有单 → 引用不重开）；五要素类变更先等老田批准。
""",
        "council-route": """---
name: council-route
description: 分发路由建议：按议题标签产出派工组合建议（只建议不建单）。
---

# 分发路由建议

1. `python tools/council_route.py --tags ...` 产建议（归属/依赖/顺序）。
2. 只产建议不直接建单；定稿后由田丰用 devtask new 派工。
3. 建议注明依据（org.json 编制/现有负载/依赖链）。
4. 与议会厅议题灯保持一致；分歧升老田。
""",
        "health-card": """---
name: health-card
description: 治理健康卡生成与红黄绿解读；红项先归因再派单。
---

# 健康卡

1. `python tools/gen_health_card.py` 生成/读取最新卡。
2. 红/黄项先归因（证据+时间戳），禁止凭记忆报数；读不到=红不是绿。
3. 检查器先自证（判例）：数字存疑先用已知样本验证工具。
4. 红项按归口拆单（devtask）或升级老田；健康卡不代替真修复。
""",
        "task-exit-audit": """---
name: task-exit-audit
description: 计划任务退出码账本扫描+窗口三分类；只读，不改任务定义。
---

# 退出码审计

1. `python agent_platform/tools/task_exit_audit.py --scan` 落账本。
2. `--window 24` 出 new/persisting/fixed；fixed≠验收（rc=0 不证明有产出）。
3. 语义：0 正常 / 2 degraded / 267014 强杀 / 267011 未跑不计败。
4. 只读；异常产文书交议会厅/工单，不改任务定义（五要素）。
""",
        "z-series": """---
name: z-series
description: Z1-Z5 进化引擎链：profile/实验卡/匿名横评/经验库/防穿越。
---

# Z 系列引擎

1. Z2 实验卡：无签字不结案（agent_platform/engine/experiment_card.py）。
2. Z3 匿名横评：匿名化后横评，防名气偏差。
3. Z4 经验库：四字段 cue/lesson/evidence/confidence；田丰为复核侧（只读+去重建议）。
4. Z5 防穿越：lookahead_guard 规则精确优先+pass-through 反证。
5. 引擎产物是草稿/证据，采纳以老田签字为准。
""",
        "memory-card": """---
name: memory-card
description: 席位三层记忆（L1 运行时 / L2 席位卡 / L3 组织引用）的读取与重生成。
---

# 三层记忆

1. L1 会话级：任务单/会话记录/临时笔记（随会话生死，各底座自管）。
2. L2 席位：SEAT_CARD.json（`python tools/seat_memory_sync.py --apply` 生成）。
3. L3 组织：只引用不复制（org.json/工单账本/健康卡/经验库等单源）。
4. 改单源即全员生效；不在席位内复制 L3 内容。
""",
        "council-hall": """---
name: council-hall
description: 议会厅发言与议题推进：发言=文书，页面只投影。
---

# 议会厅

1. 发言落盘 deliverables 文书（文件名含议题/日期），`tools/gen_council_data.py` 刷新投影。
2. 只产建议与证据，不做裁决；裁决权在老田。
3. 分歧挂待裁区，不静默；未闭环滚动升级。
4. 不在页面里造第二本账。
""",
        "docs-index": """---
name: docs-index
description: 文档现行性索引维护（S3：索引同提交）。
---

# 文档索引

1. `python -B tools/gen_docs_index.py` 生成/刷新索引。
2. 文档新增/迁移/废弃时索引同提交（S3）。
3. 根目录散落 md >24h 应归档（docs/audit 或 _archive）。
4. 索引只反映现行性，不代替正文。
""",
        "llm-glm": """---
name: llm-glm
description: GLM 云端推理（归口复核智能层）：glm-5.3，经统一 LLM 门面。
---

# GLM 推理

1. 走 `agent_platform/gateway/llm_gateway.py`，binding `glm/glm-5.3`；不直连、不建第二调用层。
2. glm/nova 不传 model（实例绑定模型）；异常归一为 ok=False 信封，不抛穿。
3. 云端不可用 → 标注 degraded，判读交人工；本地 9B 只做检索/草稿。
4. 含客户隐私/密钥/交易细节的内容不得直接送云端（脱敏规则先定）。
""",
        "mutual-review": """---
name: mutual-review
description: 互审单复核：独立重验对家产出，产评审批文书；执行者不结案。
---

# 互审复核（MUTUAL-REVIEW）

1. 读对家交付（deliverables/worktree diff/账本），证据独立重验，不信自述。
2. 评审结构：事实核对（属实/不实带证据）→ 前提证伪 → 未覆盖风险 → 表述修正建议。
3. 自己错当场认；计数类结论存疑降级为「未知」。
4. 复核通过≠验收通过；分歧上议会厅待老田裁决。
""",
        "case-sign-chain": """---
name: case-sign-chain
description: 判例签字链核对：draft→待签 payload→本人签→公钥验签→入 Z4；田丰只验签不签。
---

# 判例签字链

1. `python tools/gov_ai_case_bridge.py --emit` 产待签 payload；本人（老田）持私钥签。
2. `--apply` 走公钥验签；未签/篡改/未配公钥一律拒；strict 模式 verified=True 才入库。
3. 田丰核对签字链完整性（签名/公钥/证据版本），不代签、不伪造。
4. 验收结论落文书；入库以 Z4 引擎记录为准。
""",
        "governance-ai": """---
name: governance-ai
description: 治理 AI draft 复核：异常归纳/优先级建议/判例草稿；只读 draft。
---

# 治理 AI 复核

1. `python agent_platform/tools/gov_ai.py --action all --json` 产出 draft（落 data/governance_ai）。
2. 只读 draft：核对证据、去重、优先级合理性；不写卡/经验库/工单。
3. 失败黄不阻塞治理链；草稿采纳走签字链。
4. 判读结论仍以 a0_agent/GLM-5.3 产线为准。
""",
        "worktree-hygiene": """---
name: worktree-hygiene
description: 工单收口检查：worktree TTL/分支合并状态/回收（S1）；只读检查。
---

# 工作树收口检查

1. `git worktree list` 查各单 worktree；merge 后应回收。
2. 分支合并核对：`git merge-base --is-ancestor task/<TID> master`（True 才可回收）。
3. TTL 超期/未合并 worktree 列清单，归因后交责任线；删除类进回收站。
4. 只读检查；不擅自删改他人工作树。
""",
    },
    "zhaoyun": {
        "task-exit-audit": """---
name: task-exit-audit
description: 计划任务退出码治理扫描：--scan 落账本+摘要，--window 出新增/持续/修复三分类。
---

# 退出码扫描

1. 每日双锚（08:46/17:55）：`python agent_platform/tools/task_exit_audit.py --scan`。
2. 窗口对比：`--window 24` 输出 new/persisting/fixed；fixed≠自动验收（LastTaskResult=0 不证明有产出）。
3. 语义表：0 正常 / 2 degraded / 267014 强杀 / 0xC000013A 外部强杀 / 267011 未跑（不计败）。
4. 判例·检测器自证：数字存疑时用已知样本验证工具再采信；结论记「未知」不记「正常」。
5. 只读扫描；发现异常产文书上议会厅，不改任务定义（五要素）。
""",
        "task-launch-probe": """---
name: task-launch-probe
description: S-0 能力正测试：建一次性 python 任务→/run→断言 rc∈{0,3}→删；防门禁被"停用做绿"。
---

# 能力正测试（S-0①-b）

1. 每 24h 一次：新建一次性计划任务（python.exe <临时脚本>，纯 ASCII 路径）。
2. Start-ScheduledTask → Get-ScheduledTaskInfo 断言 LastTaskResult ∈ {0, 3}。
3. 成功：删任务 + 健康卡记一行 green；失败（如 0x80070003）：门禁红 + 记返回码（根因挂账）。
4. 探针用后即清（不留残任务）；判定读数先自证（判例）。
""",
        "mutual-review": """---
name: mutual-review
description: 异族互审：评审对家席位的方案/代码/账本产出，产出评审文书（互审单）。
---

# 互审（MUTUAL-REVIEW）

1. 读对家交付（deliverables 文书 / worktree diff / 账本），证据独立重验，不信自述。
2. 评审结构：事实核对（属实/不属实带证据）→ 前提证伪（被今晚实测推翻的假设）→ 未覆盖风险 → 表述修正建议。
3. 自己错就当场认（计数类结论降级为「未知」只留质性判断——检测器自证判例）。
4. 执行者不结案：评审通过≠验收通过；分歧上议会厅待老田裁决。
5. 文书落 deliverables 或项目根（文件名含互审/回执/评审），议会厅自动投影。
""",
        "health-card": """---
name: health-card
description: 健康卡运行与红黄绿解读：跑 tools/gen_health_card.py 取最新卡；红项先归因，检查器先自证（判例）。
---

# 健康卡（治理面）

1. 运行/读取：`python tools/gen_health_card.py` 出最新健康卡；先核对卡的时间戳与覆盖范围，过期卡不采信。
2. 红黄绿解读：逐项给读数、证据与时间，红项先归因再交办；不凭记忆报数。
3. 检查器先自证（判例）：数字存疑时用已知样本复验检查器，确认可信后再采信其结论。
4. 无法复核/读不到的项目标注「未知」，不判绿；判读结论落文书。
5. 健康卡只反映状态，红项按归口拆单或升级老田，健康卡不代替真修复。
""",
        "backup-bundles": """---
name: backup-bundles
description: 备份链 bundle+manifest+校验+轮转：tools/backup_bundles.ps1 先 -DryRun 再执行；sha256 回执核对。
---

# 备份链（bundle/manifest/校验/轮转）

1. 先演练：`powershell tools/backup_bundles.ps1 -DryRun`，看本次将产出的 bundle/manifest 清单与轮转动作。
2. 再执行：去掉 -DryRun 真跑；长跑输出落文件，不靠终端滚动。
3. 校验：按 manifest 逐项核 sha256；异地已落地以 2026-10-26 回执为准（7/7 sha256 通过）。
4. 轮转：按保留策略轮转/归档旧 bundle；删除类进回收站或先归档，不直删。
5. 任一项 sha256 不符 → 判备份链失败并升级，不写「已备份」。
""",
        "worktree-hygiene": """---
name: worktree-hygiene
description: 工作树 TTL/回收/收口（S1）：git worktree list 盘点 + devtask 收口提示；删除类进回收站/先归档。
---

# 工作树收口（S1）

1. `git worktree list` 盘点各单 worktree 与分支，标出 TTL 超期、已合并未回收、游离未登记三类。
2. 收口前核对分支是否已进 master、devtask 单状态与未提交改动，再决定回收。
3. 回收动作先确认目标绝对路径；删除类一律进回收站（SHFileOperationW）或先归档，禁 rm/del 直删。
4. 收口结果记回执（单号/路径/处置）；异常项交田丰或升级老田。
5. 只读盘点可随时跑；不擅自处置他人工作树。
""",
        "docs-archive": """---
name: docs-archive
description: 一话题一归档（S4）：根目录散落 md 超 24h 迁移 docs/audit/ 或 _archive/；回收站纪律。
---

# 文档归档（S4）

1. 扫根目录散落 .md，按 mtime 找出超 24h 未归档项，先按话题归类。
2. 迁移目标：审计/过程类 → `docs/audit/`；过期/历史类 → `_archive/`；不留在根目录。
3. 一话题一归档：同话题只留一份现行档，迁移时同步更新引用，避免断链。
4. 删除类走回收站纪律（SHFileOperationW），禁 rm/del 直删。
5. 归档可追溯：文件名含日期/话题，迁移动机记一句回执。
""",
        "experience-write": """---
name: experience-write
description: Z4 supervision 域经验写入（赵云写田丰读）：四字段 cue/lesson/evidence/confidence；采纳需老田签。
---

# 经验写入（Z4 · supervision 域）

1. 目标路径：`~/.hermes/profiles/zhaoyun/experience/supervision/`，只写本域，不越域。
2. 每条四字段齐全：cue（触发情境）/ lesson（教训）/ evidence（可回溯证据）/ confidence（0-1）。
3. evidence 必须能回溯（commit/文书/账本行号）；无证据不写高 confidence。
4. 登记即去重键：同 cue 先查已有条目，合并引用而不是重复登记。
5. 采纳状态以老田签字为准；未签字一律标「未生效」，不虚报已采纳。
""",
        "lookahead-guard": """---
name: lookahead-guard
description: Z5 防穿越断言（数据时点校验）：lookahead_guard.py --selftest；规则精确优先 + pass-through 反证。
---

# 防穿越断言（Z5）

1. 先自检：`python agent_platform/engine/lookahead_guard.py --selftest`，确认守卫自身可信。
2. 校验口径：回测/统计输入的数据时点必须 ≤ 决策时点，越界即判穿越。
3. 规则精确优先：能用精确规则判定的场景，不用宽松阈值兜底。
4. pass-through 反证：对「看起来能放行」的样本构造反证，确认放行不是漏检。
5. 判 fail 保留最小复现（输入时点/规则名）随结论落文书，交田丰或升级老田。
""",
        "council-hall": """---
name: council-hall
description: 议会厅治理面发言：评审/回执文书落盘即发言；只产建议与证据（V3.1），裁决权在老田。
---

# 议会厅（治理面发言）

1. 发言=文书：评审/回执/质询按文件名含议题与日期落 deliverables，页面只是投影。
2. 只产建议与证据（V3.1 边界）：列事实、读数、风险与建议，不宣布通过、不代裁决。
3. 分歧挂待裁区，写明裁决所需材料；不静默、不私下收敛。
4. 引用数据必须给来源路径/时间；不复制共享账本到席位记忆。
5. 裁决落定后按归口执行或派单，发言本身不构成授权。
""",
        "harness-runner": """---
name: harness-runner
description: DeepSeek Harness 执行框架：执行体=DSH 会话（桌面版），定时/重试由 governance_clock 承担；Harness² 本体不在位。
---

# Harness 执行框架

1. 执行体 = DSH 会话（DeepSeek Harness 桌面版）；不假设 Harness² 本体存在。
2. 定时/重试交给 governance_clock 承担，不在会话内自建第二套调度。
3. 隔离：每单独立 worktree/分支，长跑输出落文件，会话中断按回执续跑。
4. 需要 Harness² 本体的能力按需再立项，不把「不在位」当已具备。
5. 执行框架只负责跑与重试；通过/验收仍走评审门与收口流程。
""",
        "llm-deepseek": """---
name: llm-deepseek
description: DeepSeek 真模型推理（异常归纳/建议生成/判例抽取）：deepseek.py --selftest，默认 deepseek-flash；key 私有区不落仓。
---

# DeepSeek 推理

1. 先自检：`python agent_platform/engine/deepseek.py --selftest` 确认链路可用再上业务调用。
2. 默认模型 deepseek-flash；模型选择在调用处显式声明，不隐式依赖实例默认。
3. key 读取：`_vault/secrets/deepseek_api_key.txt` 或环境变量 `DEEPSEEK_API_KEY`；key 私有区不落仓、不进日志、不贴文书。
4. 用途边界：异常归纳/建议生成/判例抽取产草稿；结论仍要证据核对与签字，模型输出不直接当验收依据。
5. 异族互镜的一极：和其他族模型互镜对照，不单独作为唯一判据。
""",
        "governance-ai": """---
name: governance-ai
description: 治理 AI（DeepSeek flash）：异常归纳/优先级建议/判例草稿；draft 落 data/governance_ai，只产 draft 不写卡。
---

# 治理 AI

1. 调用：`python agent_platform/tools/gov_ai.py --action all --json`；draft 产物落 `data/governance_ai`。
2. 只读 draft：核对证据、去重、优先级合理性；不写卡/经验库/工单。
3. 判活看健康卡 `governance.ai_advice` 项；失败判黄，不阻塞治理链。
4. 草稿采纳走签字链（本人签 + 公钥验签）后才入 Z4。
5. 模型输出仅作候选；结论以证据核对为准，不虚报已采纳。
""",
        "case-sign-chain": """---
name: case-sign-chain
description: 判例草稿签字链：gov_ai_case_bridge --emit/--apply，私钥老田持有；未签/篡改/未配公钥一律拒，Agent 只验签。
---

# 判例签字链（治理面）

1. `python tools/gov_ai_case_bridge.py --emit` 产出待签 payload（判例草稿 + 证据版本）。
2. 本人（老田）持私钥签名；Agent 不代签、不接触私钥，只做验签与链路核对。
3. `--apply` 走公钥验签：未签 / 被篡改 / 未配公钥一律拒收，不降级放行。
4. strict 模式 verified=True 才入库（Z4）；非 strict 结果不得当已入库。
5. 验签失败保留原始 payload 与公钥信息供复盘，交田丰或升级老田。
""",
    },
    # 诸葛亮 OpenClaw workspace 已有 3 卡（adversarial-review/evidence-audit/pre-mortem），此处只补缺 5 张，不重写已有卡
    "zhuge": {
        "zhuge-brief": """---
name: zhuge-brief
description: 诸葛亮建议引擎（规则版，七源归集）：portal/api_zhuge_brief.py 出 /api/zhuge/brief（8091 已生效）；页面标注「规则归集非模型推理」。
---

# 建议引擎（规则版）

1. 建议数据来自 `portal/api_zhuge_brief.py` 的七源归集，出口 `/api/zhuge/brief`（8091 已生效，只读消费）。
2. 输出必须标注「规则归集非模型推理」：不把规则归集包装成模型结论或预测。
3. 七源归集逐源可追溯：每条建议给出来源/时间/证据，不造第二本账。
4. 只产建议不派单、不裁决；采纳与否由老田/归口决定。
5. 建议与实际采纳的偏差留痕，供 brief-trend 复盘。
""",
        "os-suggest": """---
name: os-suggest
description: OS 首页建议呈现（四视图）：portal/os_entry_v9_prod.html 消费建议引擎输出；只读归集不造第二账。
---

# OS 首页建议呈现（四视图）

1. 呈现入口：`portal/os_entry_v9_prod.html` 消费建议引擎归集结果，四视图只做投影。
2. 只读归集：页面不写业务账、不新增后端字段，数据单源在建议引擎与共享账本。
3. 四视图口径固定：同一份建议在不同视图里口径一致，不各算一套。
4. 页面标注「规则归集非模型推理」；不把呈现样式当成模型能力。
5. 呈现异常（缺数/错位）先核对数据源，再报归口；不改源数据硬凑页面。
""",
        "council-advise": """---
name: council-advise
description: 议会厅建议面表态：该不该做/优先级，落盘看法文书至 deliverables；不派单不裁决。
---

# 议会厅建议面表态

1. 表态范围：该不该做（做/不做/先补证据）与优先级（P0/P1/P2），仅限建议面。
2. 产出=看法文书，落 deliverables（文件名含议题/日期）；页面只投影。
3. 每项表态给依据（证据/来源/时间）与不确定性；无依据的直觉标「待验证」。
4. 不派单、不裁决：派单归田丰，裁决权在老田；分歧挂待裁区。
5. 表态与最终裁决的差异留痕，供趋势复盘。
""",
        "brief-trend": """---
name: brief-trend
description: 建议趋势与采纳率跟踪（status：pending_wiring，未接线）：数据源=OS 建议消费日志（待接线），采纳率进健康卡 R3。
---

# 建议趋势与采纳率（pending_wiring）

1. 当前状态：**pending_wiring** —— OS 建议消费日志尚未接线，本能力只有口径、无可用数据源。
2. 目标数据源：OS 建议消费日志（接线后只读归集）；接线前不得凭印象报采纳率。
3. 指标口径：建议数 / 被采纳数 / 采纳率 / 分类分布与趋势，分母与时间窗写清楚。
4. 采纳率出口：进健康卡 R3（接线后）；未接线时标注「未接线」，不虚报 ready。
5. 复盘结论只产建议；接线方案与优先级由老田拍板。
""",
        "llm-gpt6": """---
name: llm-gpt6
description: GPT-6.0 真模型推理（建议升级，status：pending_key）：OpenClaw zhuge agent 绑定 openai/gpt-6.0，OPENAI_API_KEY 未注入。
---

# GPT-6.0 推理（pending_key）

1. 当前状态：**pending_key** —— 模型已绑 `openai/gpt-6.0`（OpenClaw zhuge agent），但 `OPENAI_API_KEY` 未注入，真调用不可用。
2. 注入 key 前只做 Demo 对照与方案评估，不把「已绑模型」当「已可用」。
3. 升级路径：Demo 先行 → 老田拍板 → 替换现行建议引擎；未拍板不替换。
4. key 由私有区注入，不落仓、不进日志、不贴文书。
5. 真模型对照以同题同证据跑 A/B，结论入复盘；pending 期间建议口径仍用规则版。
""",
    },
}

WORKING_MEMORY = {
    "tianfeng": """# 工作记忆索引 — 田丰（归口面）

## 常读真源
- 工单账本：.worktrees/tasks.json（doing 单每日收口检查）
- 健康卡：python tools/gen_health_card.py（红项先归因再派单）
- 经验入库：~/.hermes/profiles/zhaoyun/experience/*/EXPERIENCE.md（复核侧）
- 议会厅：tools/gen_council_data.py 刷新后看 /os_council

## 我的裁决口径（沉淀中）
- （首条待填：每次归口判定后在此追加一行——日期/单号/口径/依据）
""",
    "zhaoyun": """# 工作记忆索引 — 赵云（治理面）

## 常读真源
- 退出码账本：portal/data/task_exit_audit/（我每日双锚写入）
- 健康卡红项：task_launch_failures（S-0 门禁盯守）
- 判例库：本文件所在 experience/ 五域（我写田丰读）
- 调查存档：C:/项目/调查报告_任务启动python失败_2026-10-22.md（0x80070003 六假设全推翻）

## 我的扫描口径（沉淀中）
- （首条待填：每轮扫描发现在此追加——日期/现象/定性/证据）
""",
    "zhuge": """# 工作记忆索引 — 诸葛亮（建议面）

## 常读真源
- 建议引擎输出：/api/zhuge/brief（8091）与 OS 首页消费
- 议会厅议题：/os_council（建议面表态入口）
- 建议趋势：portal/data/（采纳率接线后补）

## 我的建议口径（沉淀中）
- （首条待填：每轮建议事后复盘在此追加——日期/建议/被采纳与否/修正）
""",
}


def inject(dry_run: bool) -> int:
    wrote, skipped = 0, 0
    for seat, home in HOMES.items():
        skills_dir = os.path.join(home, "skills")
        if not dry_run:
            os.makedirs(skills_dir, exist_ok=True)
        # ① 能力清单
        cap_path = os.path.join(skills_dir, "CAPABILITIES.json")
        items = CAPABILITIES[seat]

        def _count(status):
            return sum(1 for c in items if c.get("status") == status)

        payload = {
            "schema": "seat_capabilities/v1",
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "seat": seat,
            "total": len(items),
            "ready": _count("ready"),
            "degraded_relay": _count("degraded_relay"),
            "degraded_rule": _count("degraded_rule"),
            "pending_wiring": _count("pending_wiring"),
            "pending_key": _count("pending_key"),
            "paper": _count("paper"),
            "capabilities": items,
            "note": "能力全部指向真实工具/命令；status=ready|degraded_relay|degraded_rule|"
                    "pending_wiring|paper；verified_at/verified_scope=运行态证据时点与范围"
                    "（静态卡≠运行态可用；审计通过≠能力可用）。",
        }
        if dry_run:
            print(f"[dry] {cap_path}（{payload['total']} 项：ready {payload['ready']} / "
                  f"relay {payload['degraded_relay']} / rule {payload['degraded_rule']} / "
                  f"wiring {payload['pending_wiring']}）")
        else:
            tmp = cap_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cap_path)
            print(f"[ok] {cap_path}")
            wrote += 1
        # ② 技能卡（缺才建）
        for name, body in SKILL_CARDS.get(seat, {}).items():
            p = os.path.join(skills_dir, name, "SKILL.md")
            if os.path.isfile(p):
                skipped += 1
                continue
            if dry_run:
                print(f"[dry] 新技能卡 {p}")
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(body)
                print(f"[ok] 技能卡 {name}")
                wrote += 1
        # ③ 工作记忆骨架
        mem_dir = os.path.join(home, "memories")
        if not dry_run:
            os.makedirs(mem_dir, exist_ok=True)
        wm = os.path.join(mem_dir, "WORKING_MEMORY.md")
        if os.path.isfile(wm):
            skipped += 1
        elif dry_run:
            print(f"[dry] {wm}")
        else:
            with open(wm, "w", encoding="utf-8") as f:
                f.write(WORKING_MEMORY[seat])
            print(f"[ok] 工作记忆 {seat}")
            wrote += 1
    print(f"完成：写入 {wrote} / 跳过已有 {skipped}（技能卡与工作记忆不覆盖）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三席位能力底座注入器")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.apply:
        ap.print_help()
        return 2
    return inject(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
