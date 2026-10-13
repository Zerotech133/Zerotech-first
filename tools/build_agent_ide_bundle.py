# -*- coding: utf-8 -*-
"""Agent IDE 注册表聚合器（K-ORG-022）。

把 agent_platform/registry/org.json + team_*.json + prompts/*.md 烘成
portal/agent_ide_bundle.json（nginx 静态直出，ide.html 数据源）。

- 职能 team 文件 = 岗位主数据源（grade/charter/llm/tools/grants）
- 项目 team 文件（team_proj_*）= 只取"所在项目"标签（矩阵制：一人多项目）
- prompts/<role_profile>.md 全文烘入（当前 32 编制岗 100% 覆盖）

用法：python tools/build_agent_ide_bundle.py
改了注册表或 prompt 后重跑本脚本即可，IDE 页面刷新即见（零重启）。
"""
import json
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "agent_platform", "registry")
PROMPTS = os.path.join(ROOT, "agent_platform", "prompts")
OUT = os.path.join(ROOT, "portal", "agent_ide_bundle.json")

# 职能部 team 文件（主数据源）；项目部文件单独归类
FUNC_TEAMS = ["team_invest.json", "team_dev.json", "team_trading.json",
              "team_data.json", "team_product.json", "team_quality.json"]

DEPT_META = {  # 部门显示名（org.json name 键值在此冗余固化，避免页面再拉 org）
    "invest": ("📊 投研部", "func"),
    "dev": ("🏗 工程研发中心", "func"),
    "trading": ("📈 交易部", "func"),
    "data": ("🗄 数据智能部", "func"),
    "product": ("📋 产品管理部", "func"),
    "quality": ("✅ 质量工程部", "func"),
    "proj_backtest": ("🅰 回测开发部", "project"),
    "proj_data": ("🅱 数据治理部", "project"),
    "proj_next": ("🅲 下版本设计推理部", "project"),
}


def main():
    agents = {}          # agent_id -> 卡片（职能部主数据）
    dept_agents = {}     # dept -> [agent_id]（含项目部编制）
    projects_of = {}     # agent_id -> [项目 dept]

    # 参谋长田小皮（A0）先行入编——全组织汇报顶点（K-ORG-023）
    org = json.load(open(os.path.join(REG, "org.json"), encoding="utf-8"))
    chief = org.get("chief", {})
    agents["A0"] = {
        "id": "A0",
        "dept": "chief",
        "name": chief.get("name", "田小皮"),
        "role_profile": chief.get("role_profile", "chief_of_staff"),
        "grade": "E3",
        "grade_note": "参谋长·组织枢纽（汇报聚合/任务仲裁/记忆治理）",
        "charter": "全组织 24 在编岗向田小皮汇报：判读各部门日报、风险优先输出判读、"
                   "任务单仲裁、记忆治理；影子期纪律：不给买卖操作建议。",
        "llm": chief.get("llm_binding", "ollama/qwen3.5:9b"),
        "tools": ["events", "devtask"],
        "grants_read": ["domain/*", "dept/*", "agent/*"],
        "grants_write": ["chief/decision", "chief/observation"],
        "prompt_file": "agent_platform/prompts/a0_chief.md",
        "prompt": open(os.path.join(PROMPTS, "a0_chief.md"),
                       encoding="utf-8").read() if os.path.exists(
                       os.path.join(PROMPTS, "a0_chief.md")) else "",
        "projects": [],
        "team_desc": "参谋长（组织枢纽）· 每日 17:35 值班判读",
    }
    dept_agents["chief"] = ["A0"]

    for fn in sorted(glob.glob(os.path.join(REG, "team_*.json"))):
        base = os.path.basename(fn)
        d = json.load(open(fn, encoding="utf-8"))
        dept = d.get("dept", "")
        ids = [a.get("agent_id") for a in d.get("agents", [])]
        dept_agents[dept] = ids
        if base in FUNC_TEAMS:
            for a in d.get("agents", []):
                aid = a.get("agent_id")
                agents[aid] = {
                    "id": aid,
                    "dept": dept,
                    "role_profile": a.get("role_profile", ""),
                    "grade": a.get("grade", ""),
                    "grade_note": a.get("grade_note", ""),
                    "charter": a.get("charter_detail", ""),
                    "llm": a.get("llm_binding", ""),
                    "tools": a.get("tools", []),
                    "grants_read": a.get("memory_grants", {}).get("read", []),
                    "grants_write": a.get("memory_grants", {}).get("write", []),
                    "prompt_file": "",
                    "prompt": "",
                    "projects": [],
                    "team_desc": d.get("description", ""),
                }
        else:
            for aid in ids:
                projects_of.setdefault(aid, []).append(dept)

    # 项目标签回填 + prompt 全文（chief 田小皮已在入编时装好 a0_chief.md）
    miss_prompt = []
    for aid, card in agents.items():
        card["projects"] = projects_of.get(aid, [])
        if card["prompt"]:
            continue
        pf = os.path.join(PROMPTS, card["role_profile"] + ".md")
        if os.path.exists(pf):
            card["prompt_file"] = "agent_platform/prompts/%s.md" % card["role_profile"]
            card["prompt"] = open(pf, encoding="utf-8").read()
        else:
            miss_prompt.append(aid)

    # 部门视图（IDE 左树顺序：参谋长在顶，职能部居中，项目部在后）
    order = ["chief", "invest", "dev", "trading", "data", "product", "quality",
             "proj_backtest", "proj_data", "proj_next"]
    departments = []
    for dep in order:
        if dep not in dept_agents:
            continue
        name, typ = DEPT_META.get(dep, ("🧠 参谋长 · 田小皮", "chief" if dep == "chief" else "func"))
        departments.append({
            "id": dep, "name": name, "type": typ,
            "agents": [a for a in dept_agents[dep] if a in agents],
            "all_ids": dept_agents[dep],  # 项目部可能引用未在职能表落卡者（现状无）
        })

    bundle = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "generator": "tools/build_agent_ide_bundle.py (K-ORG-022)",
        "stats": {
            "agents": len(agents),
            "departments": len(departments),
            "prompt_ok": len(agents) - len(miss_prompt),
            "prompt_miss": miss_prompt,
        },
        "departments": departments,
        "agents": agents,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=1)
    print("bundle ->", OUT)
    print("agents:", len(agents), "| prompt_ok:", bundle["stats"]["prompt_ok"],
          "| miss:", miss_prompt or "none")


if __name__ == "__main__":
    sys.exit(main())
