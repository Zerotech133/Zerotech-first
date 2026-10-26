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

SCHEMA_VERSION = "1.2"   # S34: agents 增 has_profile/lead_profiled/seats_*

# 在途工单归属规则（版本化）：tasks.json 的 owner 多为 unknown-agent，
# 按 key 前缀 / 描述关键词把任务归到部门与席位，让沙盘能显示"谁在干什么"。
# 只做标签映射，不改 tasks.json 任何字段。
ACTIVITY_RULES_VERSION = "1.2"
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
    # S29 补漏（2026-10-23）：K-QUANT 为量化专项前缀，此前无规则 → 在途单一律
    # 归入 unmapped，沙盘上表现为"有人干活但不知道是谁的活"。
    ("key_prefix", "K-QUANT", "invest", ""),
    # K-MDC = 情报侧多源数据归集（现存单产出 intel_lhb_shadow.py，龙虎榜/十大股东影子归集），
    # 语义落在情报域而非量化域——归因为据，不按“看起来像数据分析”就塞给 invest。
    ("key_prefix", "K-MDC", "intel", ""),
    # CM- = 交办给参谋长线席位的会议/交办单（现存 key 均指向 INTEL0 / 田丰），
    # 无更细席位证据时不硬派到人，只归部门，避免造出"某人负责"的假归属。
    ("key_prefix", "CM-", "chief_line", ""),
    # 2026-10-26 老田裁定：OS-* 属平台运行体自运维/修复线（OS-CLOSE/OS-FIX/OS-...）→ 运行体部；
    # WO-LEGION-* 属军团研发线 → 研发部。此前两类在途单一律 unmapped，沙盘"有人干活
    # 不知道是谁的活"，治理绊线 test_activity_mapping_* 长期红（2026-10-26 批次核验实录）。
    ("key_prefix", "OS-", "ops_runtime", ""),
    ("key_prefix", "WO-LEGION", "dev", ""),
    ("key_contains", "hermes", "ops", ""),
    ("key_contains", "Hermes", "ops", ""),
)


# 项目线→部门兜底（K-ORG-057 项目注册表 registry/projects.json 为准）：
# 前缀/关键词规则未命中时，若任务显式登记了 project，则用该线登记的 dept 归部门，
# 避免"每来一个新前缀就让治理绊线变红"的打地鼠。未登记 project（缺省 general）
# 仍返回 unmapped——绊线语义保留：没分线的活就是需要人管。
def _project_dept_map(root: Path) -> dict:
    proj = _load_json(root / "registry" / "projects.json")
    out = {}
    if isinstance(proj, dict):
        for pr in proj.get("projects") or []:
            if not isinstance(pr, dict):
                continue
            pid = str(pr.get("project_id") or "")
            dep = str(pr.get("dept") or "")
            if pid and dep:
                out[pid] = dep
    return out


def _map_task(key: str, desc: str, project: str = "",
              project_dept: dict | None = None) -> tuple:
    """返回 (dept_id, agent_id) 或 ("", "")。命中即停（顺序即优先级）。

    规则未命中时按任务登记的 project 查项目注册表 dept（有据可依，不猜前缀）。
    """
    k = str(key)
    d = str(desc)
    for mode, pat, dept, agent in ACTIVITY_RULES:
        if mode == "desc" and pat in d:
            return dept, agent
        if mode == "key_prefix" and k.startswith(pat):
            return dept, agent
        if mode == "key_contains" and pat in k:
            return dept, agent
    if project and isinstance(project_dept, dict):
        dep = project_dept.get(str(project))
        if dep:
            return dep, ""
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
    # 统一取档口径（S42）：档案可能在部门表 / 顶层 chief / team_*.json。
    # 指挥台若只认部门表，邓芝（DENGZHI1，档案在 team_comm.json）会被画成"无档"虚线——
    # 展示层显示的和真源里的事实相反，比不显示更糟。
    from engine.org_profiles import matrix_assignments, profile_index
    all_prof = profile_index(root)
    matrix_by_dept = matrix_assignments(org)
    departments = []
    for d in org.get("departments", []):
        did = d.get("dept") or d.get("dept_id") or ""
        lead_id = d.get("lead") or ""
        if lead_id and lead_id not in lead_cache:
            lead_cache[lead_id] = _cn_name(lead_id) or lead_id
        # 席位口径：agents 名单 ∪ agent_profiles 档案。
        # 只按 profiles 出人会把「有名无档」的席位整个藏起来（S34 前 intel 部显示 0 员，
        # 实际名单里 5 人）——缺口看不见比缺口存在更糟。故名单优先、档案补全字段，
        # 没有档案的标 has_profile=False，由前端显式画出差异。
        prof_by_id = {}
        for p in d.get("agent_profiles") or []:
            aid = str(p.get("agent_id") or "")
            if aid:
                prof_by_id[aid] = p
        for _aid, _m in (matrix_by_dept.get(did, {}) or {}).items():
            if _aid not in prof_by_id:
                prof_by_id[_aid] = {"agent_id": _aid,
                                    "role_profile": _m.get("role_profile", ""),
                                    "llm_binding": _m.get("llm_binding", ""),
                                    "_matrix": True}
        listed = [str(x) for x in (d.get("agents") or [])]
        ordered = listed + [a for a in prof_by_id if a not in listed]
        agents = []
        for aid in ordered:
            if not aid:
                continue
            p = prof_by_id.get(aid)
            ext = all_prof.get(aid) or {}
            agents.append({
                "id": aid,
                # 名字优先级：hr 名册 > 档案自带 name（team_comm.json 写着「邓芝」，
                # 但 DENGZHI1 没有 hr 目录；只认 hr 就会在指挥台上显示成 id）。
                "cn": (_cn_name(aid) or ext.get("cn_name") or aid),
                "binding": (p.get("llm_binding") if p else "") or ext.get("llm_binding", ""),
                "role": (p.get("role_profile") or "") if p else "",
                "has_profile": (p is not None) or bool(ext),
                # 档案不在部门表时如实标出来源，前端可据此提示「档案登记在别处」
                "profile_source": ("matrix" if (p is not None and p.get("_matrix"))
                                   else "dept" if p is not None
                                   else (ext.get("source_detail") or "")),
            })
        departments.append({
            "id": did,
            "name": d.get("name") or did,
            "cn_title": d.get("cn_title") or "",
            "lead": lead_cache.get(lead_id, "") if lead_id else "",
            "lead_id": lead_id,
            "lead_profiled": (lead_id in prof_by_id or lead_id in all_prof) if lead_id else False,
            "status": d.get("status") or "",
            "agents": agents,
            "seats_listed": len(listed),
            "seats_profiled": len(prof_by_id),
        })

    tasks = _load_json(root.parent / ".worktrees" / "tasks.json")
    project_dept = _project_dept_map(root)
    doing = []
    if isinstance(tasks, dict):
        for k, v in tasks.items():
            if isinstance(v, dict) and v.get("status") == "doing":
                dept, agent = _map_task(k, v.get("desc", ""),
                                        v.get("project") or "", project_dept)
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
