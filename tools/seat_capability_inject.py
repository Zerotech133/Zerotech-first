"""seat_capability_inject.py — 三席位能力底座注入器（2026-10-23 00:20）。

把「AI 该有的记忆」按三层打进各席位底层：
  ① 技能表 skills/CAPABILITIES.json —— 本席位全部能力清单（能干什么/怎么调/边界）
  ② 技能卡 skills/<name>/SKILL.md —— 逐技能操作卡（缺失才建，不覆盖已有）
  ③ 工作记忆骨架 memories/WORKING_MEMORY.md —— 席位视角的长期工作记忆索引

设计纪律：
  - 诸葛亮(OpenClaw workspace) 已有 3 技能卡：只补缺不重写；
  - 田丰/赵云(Hermes profiles) skills 全空：全量注入；
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))

HOMES = {
    "tianfeng": os.path.expanduser("~/.hermes/profiles/tianfeng"),
    "zhaoyun": os.path.expanduser("~/.hermes/profiles/zhaoyun"),
    "zhuge": os.path.join(REPO, "legion_agents", "zhuge", "workspace"),
}

# ---------------- 能力全集（按席位职责面 × 全仓真实工具） ----------------
# 每项：name / what（能干什么）/ how（真实调用）/ boundary（边界）/ status（ready|pending_key|pending_wiring）
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
        {"name": "harness-runner", "what": "DeepSeek Harness 执行框架（cron/重试/隔离）", "how": "本体当前不在位（C:/lab 与仓内均无）；执行载体 DSH/ Harness² 待另单定性", "boundary": "执行体=会话/DSH 承担", "status": "pending_key"},
        {"name": "llm-deepseek", "what": "DeepSeek 真模型推理（异常归纳/建议生成/判例抽取）", "how": "python agent_platform/engine/deepseek.py --selftest（默认 deepseek-flash；key 读 _vault/secrets/deepseek_api_key.txt 或 DEEPSEEK_API_KEY）", "boundary": "key 私有区不落仓；异族互镜的一极", "status": "ready"},
        {"name": "governance-ai", "what": "治理 AI：异常归纳/优先级建议/判例草稿（DeepSeek flash）", "how": "python agent_platform/tools/gov_ai.py --action all --json（draft 落 data/governance_ai；健康卡 governance.ai_advice 判活）", "boundary": "只产 draft，不写卡/经验库/工单；失败黄不阻塞治理链", "status": "ready"},
        {"name": "case-sign-chain", "what": "判例草稿签字链：AI draft→待签 payload→本人签→公钥验签→入 Z4", "how": "python tools/gov_ai_case_bridge.py --emit / --apply（私钥由老田持有，Agent 只验签）", "boundary": "未签/篡改/未配公钥一律拒；strict 模式 verified=True 才入库", "status": "ready"},
    ],
    "zhuge": [
        {"name": "adversarial-review", "what": "反向质询（已有技能卡）", "how": "skills/adversarial-review", "boundary": "只给建议与质询卡，不代签", "status": "ready"},
        {"name": "evidence-audit", "what": "证据审计（已有技能卡）", "how": "skills/evidence-audit", "boundary": "已核实/推断/待验证三分", "status": "ready"},
        {"name": "pre-mortem", "what": "事前验尸（已有技能卡）", "how": "skills/pre-mortem", "boundary": "失败路径枚举", "status": "ready"},
        {"name": "zhuge-brief", "what": "诸葛亮建议引擎（规则版，七源归集）", "how": "portal/api_zhuge_brief.py + /api/zhuge/brief（8091 已生效）", "boundary": "页面标注'规则归集非模型推理'", "status": "ready"},
        {"name": "os-suggest", "what": "OS 首页建议呈现（四视图）", "how": "portal/os_entry_v9_prod.html 消费", "boundary": "只读归集不造第二账", "status": "ready"},
        {"name": "council-advise", "what": "议会厅建议面表态（该不该做/优先级）", "how": "落盘看法文书至 deliverables", "boundary": "不派单不裁决", "status": "ready"},
        {"name": "brief-trend", "what": "建议趋势与采纳率跟踪", "how": "OS 建议消费日志（待接线）", "boundary": "采纳率进健康卡（R3）", "status": "pending_wiring"},
        {"name": "llm-gpt6", "what": "GPT-6.0 真模型推理（建议升级）", "how": "OpenClaw zhuge agent（模型已绑 openai/gpt-6.0）", "boundary": "OPENAI_API_KEY 未注入——注入后真模型对照（Demo先行→拍板→替换）", "status": "pending_key"},
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
    },
    "zhuge": {},  # 已有 3 卡，不重复注入
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
        os.makedirs(skills_dir, exist_ok=True)
        # ① 能力清单
        cap_path = os.path.join(skills_dir, "CAPABILITIES.json")
        payload = {
            "schema": "seat_capabilities/v1",
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "seat": seat,
            "total": len(CAPABILITIES[seat]),
            "ready": sum(1 for c in CAPABILITIES[seat] if c["status"] == "ready"),
            "pending_key": sum(1 for c in CAPABILITIES[seat] if c["status"] == "pending_key"),
            "pending_wiring": sum(1 for c in CAPABILITIES[seat] if c["status"] == "pending_wiring"),
            "capabilities": CAPABILITIES[seat],
            "note": "能力全部指向真实工具/命令；pending_key=等模型凭据；pending_wiring=等数据接线。",
        }
        if dry_run:
            print(f"[dry] {cap_path}（{payload['total']} 项：ready {payload['ready']} / key {payload['pending_key']} / wiring {payload['pending_wiring']}）")
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
