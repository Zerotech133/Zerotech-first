# -*- coding: utf-8 -*-
"""军团指挥界面套件（WO-LEGION-S22）：快照聚合器。

只读聚合组织真源 → 前端一屏快照：org.json（16 营 48 员）+ hr/ 中文名册
+ tasks.json 在途 + schedules.json 排程。纪律：只读零写入；数据源缺失显式
标记 missing_source，不造数（business_adapter 同哲学）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

SCHEMA_VERSION = "1.1"

# 在途工单归属规则（版本化）：tasks.json 的 owner 多为 unknown-agent，
# 按 key 前缀 / 描述关键词把任务归到部门与席位，让沙盘能显示"谁在干什么"。
# 只做标签映射，不改 tasks.json 任何字段。
ACTIVITY_RULES_VERSION = "1.0"
ACTIVITY_RULES = (
    # (匹配函数名, 部门 id, 席位 id 或 "")
    ("desc", "盘前", "invest", "D1"),
    ("desc", "三线扫描", "invest", "D1"),
    ("desc", "复盘三问", "ops", "A0A"),
    ("desc", "军团周报", "ops", "A0A"),
    ("desc", "晨会", "ops", "A0A"),
    ("key_prefix", "legion-", "invest", "D1"),
    ("key_prefix", "K-INTEL", "intel", ""),
    ("key_prefix", "K-ORG", "ops", ""),
    ("key_contains", "hermes", "ops", ""),
    ("key_contains", "Hermes", "ops", ""),
)


def _map_task(key: str, desc: str) -> tuple:
    """返回 (dept_id, agent_id) 或 ("", "")。命中即停（顺序即优先级）。"""
    k = str(key)
    d = str(desc)
    for mode, pat, dept, agent in ACTIVITY_RULES:
        if mode == "desc" and pat in d:
            return dept, agent
        if mode == "key_prefix" and k.startswith(pat):
            return dept, agent
        if mode == "key_contains" and pat in k:
            return dept, agent
    return "", ""


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return "__error__"


def _cn_name(agent_id: str) -> str:
    f = _PLATFORM / "hr" / agent_id / "resume.yaml"
    if not f.exists():
        return ""
    for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("cn_name:"):
            return ln.split(":", 1)[1].strip()
    return ""


def build_snapshot(platform_dir: Path | None = None) -> dict:
    root = Path(platform_dir) if platform_dir else _PLATFORM
    org = _load_json(root / "registry" / "org.json")
    if not isinstance(org, dict):
        return {"schema_version": SCHEMA_VERSION, "ok": False,
                "error": "org.json missing or corrupt"}

    lead_cache: dict[str, str] = {}
    departments = []
    for d in org.get("departments", []):
        did = d.get("dept") or d.get("dept_id") or ""
        lead_id = d.get("lead") or ""
        if lead_id and lead_id not in lead_cache:
            lead_cache[lead_id] = _cn_name(lead_id) or lead_id
        agents = []
        for p in d.get("agent_profiles") or []:
            aid = p.get("agent_id") or ""
            agents.append({
                "id": aid,
                "cn": _cn_name(aid) or aid,
                "binding": p.get("llm_binding") or "",
                "role": p.get("role_profile") or "",
            })
        departments.append({
            "id": did,
            "name": d.get("name") or did,
            "cn_title": d.get("cn_title") or "",
            "lead": lead_cache.get(lead_id, "") if lead_id else "",
            "status": d.get("status") or "",
            "agents": agents,
        })

    tasks = _load_json(root.parent / ".worktrees" / "tasks.json")
    doing = []
    if isinstance(tasks, dict):
        for k, v in tasks.items():
            if isinstance(v, dict) and v.get("status") == "doing":
                dept, agent = _map_task(k, v.get("desc", ""))
                doing.append({"key": k,
                              "desc": str(v.get("desc", ""))[:80],
                              "owner": v.get("owner", ""),
                              "dept": dept,
                              "agent": agent})

    by_dept: dict = {}
    by_agent: dict = {}
    unmapped = []
    for t in doing:
        if t["dept"]:
            by_dept.setdefault(t["dept"], []).append(t["key"])
        else:
            unmapped.append(t["key"])
        if t["agent"]:
            by_agent.setdefault(t["agent"], []).append(t["key"])

    schedules = _load_json(root / "data" / "schedules.json")
    sched = []
    if isinstance(schedules, list):
        for r in schedules:
            if r.get("enabled"):
                sched.append({"id": r.get("id") or r.get("name"),
                              "spec": r.get("cron") or r.get("spec"),
                              "desc": str(r.get("desc", ""))[:60]})

    total_agents = sum(len(x["agents"]) for x in departments)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "departments": len(departments),
            "agents": total_agents,
            "doing": len(doing),
            "schedules": len(sched),
        },
        "chief": {
            "agent_id": (org.get("chief") or {}).get("agent_id", ""),
            "binding": (org.get("chief") or {}).get("llm_binding", ""),
        },
        "departments": departments,
        "activity": {"doing": doing,
                     "by_dept": by_dept,
                     "by_agent": by_agent,
                     "unmapped": unmapped,
                     "rules_version": ACTIVITY_RULES_VERSION},
        "schedules": sched,
    }
