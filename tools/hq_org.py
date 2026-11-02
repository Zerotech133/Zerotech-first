#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-01：零界组织/角色/权限只读模型 v1。

数据源（全部只读）：
- agent_platform/registry/org.json          编制树（chief/departments/agents）
- agent_platform/registry/team_*.json       team 投影（role_profile/llm_binding/tools）
- agent_platform/registry/model_tiers.json  模型分层
- agent_platform/registry/skill_registry.json 技能/工具总数
- agent_platform/hr/<id>/resume.yaml        HR 姓名/档案
- agent_platform/registry/profile_seats.json 岗位-席位（若存在）

输出：Markdown 或 JSON。只读，不写文件、不启停服务。
用法：python -X utf8 tools/hq_org.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "agent_platform" / "registry"
ORG = REG / "org.json"
HR = ROOT / "agent_platform" / "hr"
SKILLS = REG / "skill_registry.json"
MODEL_TIERS = REG / "model_tiers.json"
PROFILE_SEATS = REG / "profile_seats.json"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _hr_name(agent_id: str) -> str:
    p = HR / agent_id / "resume.yaml"
    if not p.is_file():
        return ""
    try:
        if yaml is not None:
            d = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace")) or {}
            return str(d.get("cn_name") or d.get("name") or "").strip()
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cn_name:"):
                return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _team_index() -> dict:
    idx = {}
    for p in sorted(REG.glob("team_*.json")):
        d = _load_json(p, {})
        for a in (d.get("agents") or []):
            if isinstance(a, dict) and a.get("agent_id"):
                idx[str(a["agent_id"])] = {
                    "team": d.get("team") or p.stem.replace("team_", ""),
                    "role_profile": a.get("role_profile"),
                    "llm_binding": a.get("llm_binding"),
                    "tools": a.get("tools") or [],
                    "level": a.get("level"),
                }
    return idx


def build_org() -> dict:
    org = _load_json(ORG, {})
    teams = _team_index()
    skill_doc = _load_json(SKILLS, {})
    model_doc = _load_json(MODEL_TIERS, {})
    profile_doc = _load_json(PROFILE_SEATS, {})
    dept_rows = []
    all_agents = []
    for d in (org.get("departments") or []):
        dept = str(d.get("dept") or "")
        dept_name = str(d.get("name") or dept)
        lead = str(d.get("lead") or "")
        agents = []
        for aid in (d.get("agents") or []):
            aid = str(aid)
            t = teams.get(aid, {})
            row = {
                "agent_id": aid,
                "name": _hr_name(aid) or aid,
                "dept": dept,
                "dept_name": dept_name,
                "is_lead": aid == lead,
                "hr_profile": (HR / aid / "resume.yaml").is_file(),
                "team": t.get("team") or "",
                "role_profile": t.get("role_profile") or "",
                "llm_binding": t.get("llm_binding") or "",
                "tools": t.get("tools") or [],
                "level": t.get("level"),
            }
            agents.append(row)
            all_agents.append(row)
        dept_rows.append({"dept": dept, "name": dept_name, "lead": lead, "agents": agents,
                         "n": len(agents), "leads_present": bool(lead)})
    chief = org.get("chief", {}) or {}
    hr_count = len([p for p in HR.iterdir() if p.is_dir()]) if HR.is_dir() else 0
    names = [a["name"] for a in all_agents]
    no_hr = [a["agent_id"] for a in all_agents if not a["hr_profile"]]
    no_team = [a["agent_id"] for a in all_agents if not a["team"]]
    return {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "chief": {"agent_id": chief.get("agent_id", "A0"), "name": chief.get("name", "田丰")},
        "summary": {
            "departments": len(dept_rows),
            "agents": len(all_agents),
            "unique_names": len(set(n for n in names if n)),
            "hr_profiles": hr_count,
            "team_projected": len(all_agents) - len(no_team),
            "leads": sum(1 for d in dept_rows if d["lead"]),
            "skills": len(skill_doc.get("skills") or []),
            "models": len((model_doc.get("tiers") or model_doc.get("models") or {})),
            "profile_seats": len(profile_doc.get("seats") or profile_doc) if isinstance(profile_doc, dict) else 0,
        },
        "departments": dept_rows,
        "gaps": {"no_hr_profile": no_hr, "no_team_projection": no_team},
    }


def render_md(o: dict) -> str:
    s = o["summary"]
    lines = [
        "# 零界 HQ · 组织/角色/权限 v1",
        "",
        f"- 生成：{o['generated_at']}｜参谋长：{o['chief']['name']}（{o['chief']['agent_id']}）",
        f"- 部门 **{s['departments']}**｜席位 **{s['agents']}**｜HR 档案 **{s['hr_profiles']}**｜team 投影 **{s['team_projected']}**｜技能注册 **{s['skills']}**",
        "",
        "## 组织树",
        "",
        f"- {o['chief']['name']}（{o['chief']['agent_id']}）",
    ]
    for d in o["departments"]:
        lead = d["lead"] or "（缺）"
        lines.append(f"  - {d['name']}（{d['dept']}）· lead={lead} · {d['n']} 席")
    lines += ["", "## 角色/权限矩阵（前 40 席）", "",
              "| 席位 | 姓名 | 部门 | team | role | model | tools | HR |",
              "|---|---|---|---|---|---|---:|---|"]
    for a in [x for d in o["departments"] for x in d["agents"]][:40]:
        lines.append("| {aid} | {name} | {dept} | {team} | {role} | {model} | {tools} | {hr} |".format(
            aid=a["agent_id"], name=a["name"], dept=a["dept"], team=a["team"] or "-",
            role=a["role_profile"] or "-", model=a["llm_binding"] or "-",
            tools=len(a["tools"]), hr="✓" if a["hr_profile"] else "—"))
    lines += ["", "## 缺口", "",
              f"- 无 HR 档案：{len(o['gaps']['no_hr_profile'])} 席（{', '.join(o['gaps']['no_hr_profile'][:15])}）",
              f"- 无 team 投影：{len(o['gaps']['no_team_projection'])} 席（{', '.join(o['gaps']['no_team_projection'][:15])}）",
              "", "> 只读模型；后续由 `/hq/org` API/页面消费，不改 org/team/HR 源文件。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-01 组织/角色/权限只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    o = build_org()
    print(json.dumps(o, ensure_ascii=False, indent=1) if args.json else render_md(o), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
