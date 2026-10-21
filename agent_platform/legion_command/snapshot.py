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

SCHEMA_VERSION = "1.0"


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
                doing.append({"key": k,
                              "desc": str(v.get("desc", ""))[:80],
                              "owner": v.get("owner", "")})

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
        "activity": {"doing": doing},
        "schedules": sched,
    }
