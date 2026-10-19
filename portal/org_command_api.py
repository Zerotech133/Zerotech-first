# -*- coding: utf-8 -*-
"""org_command_api.py — Agent 指挥台后端（K-ORG-190，2026-10-19 老田拍板四项落地）

背景：portal/org.html 指挥台前端早已建好（新增 AI/部门/项目表单、预览/落盘两段式、
属性/模型编辑区），但后端 API 从未实现——/api/org 等全部 404，页面"加载失败"断粮
（2026-10-19 实测 8080/8787 双 404）。本模块补齐数据面与写回链。

拍板口径（老田 2026-10-19）：
  ① 引用不内嵌：技能挂载 skills 只存 {id,kind,enabled} 引用，实体在 skill_registry.json；
  ② 模型档位表：E3=strong / E2=strong|fast / E1=fast|local（model_tiers.json，注册与改绑硬校验，
     K-ORG-136 判例：coding 端点只通 glm-5.3 / glm-5.3-flash；存量绑定不回刷）；
  ③ 加 Agent 审批流：直属线（agent/dept）直接生效+事后审计（org_audit.jsonl）；
     跨部门项目线（project）入列 workflow_candidates/pending_a0_confirm/ 待参谋长（A0）确认；
  ④ 指挥台落位：org.html 原地升级（/org 路由不变），本模块即其数据面。

写回安全链（判例平移）：备份（registry/binding_backups/<ts>_<tag>/）→ 写前校验
（agent_id/档位/技能引用/拓扑最小规则）→ 原子写 → 审计落盘。测试一律传 tmp 目录，
**任何函数不得默认绑定真实 registry 路径**（write_snapshot 判例）。

诚实边界：/api/agent/learn（在线学习·模型提炼）属 P2（K-ORG-185 学习环在途），本版显式
返回 ok:false，不做无 LLM 的假蒸馏；心跳灯仅对配置了 heartbeat.probe 的岗位计算。
"""
import json
import os
import re
import shutil
import time

__all__ = [
    "api_org", "api_preview", "api_register", "api_project_preview",
    "api_project_register", "api_agent_update", "api_agent_knowledge",
    "api_agent_learn", "STATION",
]

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(STATION, "agent_platform", "registry")
PORTAL_DIR = os.path.join(STATION, "portal")

AGENT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
DEPT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
PROJECT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,23}$")
GRADES = ("E1", "E2", "E3")


# ---------------- 基础 IO（全部显式传目录，不默认绑真路径） ----------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _append_jsonl(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _backup(station, relpaths, tag):
    """把将被改写的文件整份拷入 binding_backups/<ts>_<tag>/，返回备份目录。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(station, "agent_platform", "registry",
                        "binding_backups", "%s_%s" % (ts, tag))
    for rel in relpaths:
        src = os.path.join(station, rel)
        if os.path.exists(src):
            dst = os.path.join(bdir, os.path.basename(rel))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    return bdir


def _reg(station, name):
    return os.path.join(station, "agent_platform", "registry", name)


def _err(msg):
    return {"ok": False, "error": msg}


# ---------------- 注册表读取 ----------------

def _load_org(station):
    org = _read_json(_reg(station, "org.json"))
    if not org:
        raise ValueError("org.json 缺失或不可解析")
    return org


def _load_tiers(station):
    return _read_json(_reg(station, "model_tiers.json")) or {}


def _load_skills(station):
    return _read_json(_reg(station, "skill_registry.json")) or {}


def _load_faces(station):
    mkt = _read_json(os.path.join(station, "portal", "ai_market.json")) or {}
    face = {}
    for g in mkt.get("groups", []):
        for a in g.get("ais", []):
            face[a.get("id", "")] = a
    return face


def _all_agents_raw(org):
    """产出 (dept_key, agent_dict)。口径：dept['agents'] 内联 + teams 条目
    （字符串 → team_<k>.json；dict → 内联 team）。与 _org_page 读法同族。"""
    for dep in org.get("departments", []):
        dk = dep.get("dept", "")
        for ag in dep.get("agents", []) or []:
            if isinstance(ag, dict):
                yield dk, ag
        for t in dep.get("teams", []) or []:
            if isinstance(t, str):
                tj = _read_json(_reg(station, "team_%s.json" % t)) or {}
                for ag in tj.get("agents", []) or []:
                    if isinstance(ag, dict):
                        yield dk, ag
            elif isinstance(t, dict):
                for ag in t.get("agents", []) or []:
                    if isinstance(ag, dict):
                        yield dk, ag


def _existing_agent_ids(org, station):
    ids = set()
    ch = org.get("chief", {})
    if ch.get("agent_id"):
        ids.add(ch["agent_id"])
    for _, ag in _all_agents_raw(org):
        if ag.get("agent_id"):
            ids.add(ag["agent_id"])
    return ids


def _prompt_for(ag, station):
    """人设解析：prompts/<role_profile>.md 优先（interpreter 同口径），
    退 claude_agents.json#<agent_id>，再退 None。"""
    rp = ag.get("role_profile", "")
    if rp:
        p = os.path.join(station, "agent_platform", "prompts", rp + ".md")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass
    cj = _read_json(_reg(station, "claude_agents.json")) or {}
    seg = cj.get(ag.get("agent_id", "")) or {}
    return seg.get("prompt")


# ---------------- bundle（/api/org 数据面） ----------------

def _agent_public(ag, dept_key, faces, station):
    aid = ag.get("agent_id", "?")
    face = faces.get(aid, {}) or {}
    at = ag.get("attributes", {}) or {}
    mg = ag.get("memory_grants", {}) or {}
    mem = ag.get("memory", {}) or {}
    return {
        "name": face.get("name") or ag.get("name") or aid,
        "charter": ag.get("charter_detail") or ag.get("charter") or "",
        "grade": ag.get("grade", ""),
        "grade_note": ag.get("grade_note", ""),
        "dept": dept_key,
        "attributes": {
            "experience": at.get("experience", ""),
            "specialties": at.get("specialties", []) or [],
            "knowledge": at.get("knowledge", []) or [],
        },
        "memory": mem if mem.get("knowledge") else {},
        "llm": ag.get("llm_binding", ""),
        "tools": ag.get("tools", []) or [],
        "skills": ag.get("skills", []) or [],
        "projects": ag.get("projects", []) or [],
        "grants_read": mg.get("read", []) or [],
        "grants_write": mg.get("write", []) or [],
        "prompt": _prompt_for(ag, station),
        "heartbeat": ag.get("heartbeat"),
    }


def load_bundle(station=STATION):
    org = _load_org(station)
    faces = _load_faces(station)
    agents = {}
    for dk, ag in _all_agents_raw(org):
        aid = ag.get("agent_id")
        if aid:
            agents[aid] = _agent_public(ag, dk, faces, station)
    ch = org.get("chief", {})
    if ch.get("agent_id"):
        agents[ch["agent_id"]] = _agent_public(ch, "chief", faces, station)

    departments = []
    for dep in org.get("departments", []):
        ids = []
        for ag in dep.get("agents", []) or []:
            if isinstance(ag, dict) and ag.get("agent_id"):
                ids.append(ag["agent_id"])
        for t in dep.get("teams", []) or []:
            team_agents = []
            if isinstance(t, str):
                tj = _read_json(_reg(station, "team_%s.json" % t)) or {}
                team_agents = tj.get("agents", []) or []
            elif isinstance(t, dict):
                team_agents = t.get("agents", []) or []
            for ag in team_agents:
                if isinstance(ag, dict) and ag.get("agent_id"):
                    ids.append(ag["agent_id"])
        departments.append({
            "id": dep.get("dept", ""),
            "name": dep.get("name", dep.get("dept", "")),
            "type": "project" if dep.get("type") == "project" else "func",
            "charter": dep.get("charter", ""),
            "status": dep.get("status", ""),
            "agents": ids,
        })
    return {
        "agents": agents,
        "departments": departments,
        "stats": {"agents": len(agents), "departments": len(departments)},
    }


def api_org(station=STATION):
    try:
        org = _load_org(station)
        audit_path = _reg(station, "org_audit.jsonl")
        audit_tail = []
        if os.path.exists(audit_path):
            with open(audit_path, encoding="utf-8") as f:
                rows = [json.loads(x) for x in f if x.strip()]
            audit_tail = rows[-20:][::-1]
        return {"ok": True,
                "bundle": load_bundle(station),
                "org": {"chief": org.get("chief", {}),
                        "report_to": org.get("report_to", "human")},
                "audit_tail": audit_tail,
                "knowledge_docs": []}
    except Exception as e:  # fail-visible：恒 200 携错误体（判例）
        return dict(_err("org bundle: %r" % (e,)))


# ---------------- 校验（拍板①②硬闸） ----------------

def _tier_check(station, grade, model):
    tiers = _load_tiers(station)
    models = tiers.get("models", {})
    pol = (tiers.get("grade_policy", {}).get(grade, {}))
    if not model:
        return _err("model 必填（模型档位表 " + "；".join(
            "%s→%s" % (g, p.get("default", "?")) for g, p in
            tiers.get("grade_policy", {}).items()) + "）")
    if model not in models:
        return _err("模型 %r 不在档位表白名单" % model)
    if not models[model].get("enabled", True):
        return _err("模型 %r 未启用（%s）" % (model, models[model].get("note", "")))
    allow = pol.get("allow", [])
    if models[model].get("tier") not in allow:
        return _err("档位不符：%s 档 allow=%s，而 %s 属 %s 档（拍板②：E3=strong，E1=fast/local）"
                    % (grade, allow, model, models[model].get("tier")))
    return None


def _skill_ids(station, kind):
    return {s.get("id") for s in _load_skills(station).get("skills", [])
            if s.get("kind") == kind}


def _validate_agent_payload(station, p, org):
    aid = (p.get("agent_id") or "").strip()
    if not AGENT_ID_RE.match(aid):
        return _err("agent_id %r 不合法（大写字母开头 2-8 位大写字母/数字，如 DEV3）" % aid)
    if aid in _existing_agent_ids(org, station):
        return _err("agent_id %r 已在编（重复注册拒绝）" % aid)
    grade = (p.get("grade") or "").strip()
    if grade not in GRADES:
        return _err("grade 必须是 E1/E2/E3，得 %r" % grade)
    tier_err = _tier_check(station, grade, (p.get("model") or "").strip())
    if tier_err:
        return tier_err
    tools = [t.strip() for t in (p.get("tool_preset") or "").split(",")
             if t.strip()]
    tool_ids = _skill_ids(station, "tool")
    algo_ids = _skill_ids(station, "algo")
    for t in tools:
        if t not in tool_ids and t not in algo_ids:
            return _err("工具/技能 %r 不在 skill_registry（引用不内嵌：先在技能库登记）" % t)
    dept = (p.get("dept") or "").strip()
    if not dept:
        return _err("dept 必填")
    known = {d.get("dept") for d in org.get("departments", [])}
    if dept not in known:
        if not DEPT_KEY_RE.match(dept):
            return _err("新部门键 %r 不合法（小写字母开头 2-16 位）" % dept)
        if not (p.get("dept_name") or "").strip():
            return _err("新部门需同时给 dept_name")
    return None


# ---------------- 预览（两段式第一段：只算不写） ----------------

def _plan_rows_agent(station, p, org, new_dept):
    bdir = "agent_platform/registry"
    rows = [{"action": "backup", "path": bdir + "/binding_backups/<ts>_agent_" + p["agent_id"],
             "what": "改写前整份备份 org.json（+人设文件）"}]
    if new_dept:
        rows.append({"action": "update", "path": bdir + "/org.json",
                     "what": "新增部门 %s（%s）" % (p["dept"], p.get("dept_name", ""))})
    rows.append({"action": "update", "path": bdir + "/org.json",
                 "what": "部门 %s 的 agents 增编 %s（%s · %s · %s）"
                         % (p["dept"], p["agent_id"], p.get("grade"),
                            p.get("model"), p.get("name", ""))})
    if (p.get("persona") or "").strip():
        rows.append({"action": "create", "path": "agent_platform/prompts/%s.md" % p["agent_id"].lower(),
                     "what": "人设落盘（prompts 按 role_profile 解析，interpreter 同口径）"})
    rows.append({"action": "audit", "path": bdir + "/org_audit.jsonl",
                 "what": "事后审计一行（kind=agent，拍板③：直属线直接生效）"})
    return rows


def api_preview(body, station=STATION):
    try:
        p = body or {}
        org = _load_org(station)
        verr = _validate_agent_payload(station, p, org)
        if verr:
            return verr
        new_dept = p["dept"] not in {d.get("dept") for d in org.get("departments", [])}
        return {"ok": True, "plan": _plan_rows_agent(station, p, org, new_dept)}
    except Exception as e:
        return _err("preview: %r" % (e,))


# ---------------- 落盘（两段式第二段） ----------------

def api_register(body, station=STATION):
    try:
        p = body or {}
        org_path = _reg(station, "org.json")
        org = _load_org(station)
        verr = _validate_agent_payload(station, p, org)
        if verr:
            return verr
        aid = p["agent_id"]
        new_dept = p["dept"] not in {d.get("dept") for d in org.get("departments", [])}

        written = [os.path.relpath(org_path, station)]
        bdir = _backup(station, ["agent_platform/registry/org.json"],
                       "agent_" + aid)

        dep = None
        for d in org.get("departments", []):
            if d.get("dept") == p["dept"]:
                dep = d
                break
        if new_dept:
            dep = {"dept": p["dept"], "name": (p.get("dept_name") or "").strip(),
                   "status": "active", "charter": (p.get("dept_charter") or "").strip(),
                   "agents": [], "teams": []}
            org.setdefault("departments", []).append(dep)
        ag = {"agent_id": aid,
              "role_profile": aid.lower(),
              "name": (p.get("name") or "").strip() or aid,
              "grade": p["grade"],
              "level": 2,
              "node_key": aid.lower(),
              "parent_node_keys": [],
              "llm_binding": p["model"],
              "tools": [t.strip() for t in (p.get("tool_preset") or "").split(",") if t.strip()],
              "charter_detail": (p.get("charter") or "").strip(),
              "attributes": {"experience": "", "specialties": [], "knowledge": []},
              "status": "active"}
        dep.setdefault("agents", []).append(ag)
        _write_json_atomic(org_path, org)

        persona = (p.get("persona") or "").strip()
        if persona:
            pp = os.path.join(station, "agent_platform", "prompts", aid.lower() + ".md")
            os.makedirs(os.path.dirname(pp), exist_ok=True)
            with open(pp, "w", encoding="utf-8", newline="\n") as f:
                f.write(persona)
            written.append(os.path.relpath(pp, station))

        row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "agent",
               "agent_id": aid, "dept": p["dept"], "grade": p["grade"],
               "files": written, "backup": os.path.relpath(bdir, station),
               "caller": (p.get("caller") or "").strip()}
        _append_jsonl(_reg(station, "org_audit.jsonl"), row)
        return {"ok": True, "headcount": len(load_bundle(station)["agents"]),
                "audit": row}
    except Exception as e:
        return _err("register: %r" % (e,))


def api_project_preview(body, station=STATION):
    try:
        p = body or {}
        org = _load_org(station)
        verr = _validate_project_payload(station, p, org)
        if verr:
            return verr
        bdir = "agent_platform/registry"
        return {"ok": True, "plan": [
            {"action": "create",
             "path": bdir + "/workflow_candidates/pending_a0_confirm/" + p["project_id"] + ".json",
             "what": "项目线立案暂存（拍板③：跨部门项目走参谋长 A0 确认后生效）"},
            {"action": "audit", "path": bdir + "/org_audit.jsonl",
             "what": "审计一行（kind=project_pending）"}]}
    except Exception as e:
        return _err("project preview: %r" % (e,))


def _validate_project_payload(station, p, org):
    pid = (p.get("project_id") or "").strip()
    if not PROJECT_ID_RE.match(pid):
        return _err("project_id %r 不合法（2-24 位字母数字-_）" % pid)
    dept = (p.get("dept") or "").strip()
    if dept not in {d.get("dept") for d in org.get("departments", [])}:
        return _err("项目线必须挂现有部门，%r 不存在" % dept)
    ids = _existing_agent_ids(org, station)
    for m in p.get("members", []) or []:
        if m not in ids:
            return _err("成员 %r 不在编（跨部门抽人先核编制）" % m)
    return None


def api_project_register(body, station=STATION):
    try:
        p = body or {}
        org = _load_org(station)
        verr = _validate_project_payload(station, p, org)
        if verr:
            return verr
        pend_dir = os.path.join(_reg(station, "workflow_candidates"),
                                "pending_a0_confirm")
        target = os.path.join(pend_dir, (p.get("project_id") or "P") + ".json")
        rel_target = os.path.relpath(target, station)
        _backup(station, ["agent_platform/registry/org.json"],
                "project_" + (p.get("project_id") or "P"))
        _write_json_atomic(target, {
            "status": "pending_a0_confirm",
            "payload": p, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "project_pending",
               "project_id": p.get("project_id"), "dept": p.get("dept"),
               "files": [rel_target], "caller": (p.get("caller") or "").strip()}
        _append_jsonl(_reg(station, "org_audit.jsonl"), row)
        return {"ok": True, "pending_a0_confirm": True, "path": rel_target,
                "note": "项目线已入列待参谋长（A0）确认，确认后生效（拍板③）"}
    except Exception as e:
        return _err("project register: %r" % (e,))


# ---------------- 属性/模型编辑（详情页编辑区） ----------------

def api_agent_update(body, station=STATION):
    try:
        p = body or {}
        aid = (p.get("agent_id") or "").strip()
        org_path = _reg(station, "org.json")
        org = _load_org(station)
        if aid == "A0":
            return _err("A0 走 org.json chief 段，图上编辑不含 A0（页面既有口径）")
        target = None
        for _, ag in _all_agents_raw(org):
            if ag.get("agent_id") == aid:
                target = ag
                break
        if not target:
            return _err("agent %r 不在编" % aid)

        changed = []
        exp = p.get("experience")
        if exp is not None:
            target.setdefault("attributes", {})["experience"] = str(exp).strip()
            changed.append("从业经验")
        spec = p.get("specialties")
        if spec is not None:
            target.setdefault("attributes", {})["specialties"] = [
                s.strip() for s in re.split(r"[,，]", str(spec)) if s.strip()]
            changed.append("擅长")
        model = (p.get("model") or "").strip()
        if model and model != target.get("llm_binding"):
            grade = target.get("grade")
            if grade in GRADES:
                tier_err = _tier_check(station, grade, model)
                if tier_err:
                    return tier_err
            target["llm_binding"] = model
            changed.append("模型")
        tool_preset = p.get("tool_preset")
        if tool_preset is not None:
            tools = [t.strip() for t in str(tool_preset).split(",") if t.strip()]
            tool_ids = _skill_ids(station, "tool") | _skill_ids(station, "algo")
            for t in tools:
                if t not in tool_ids:
                    return _err("工具/技能 %r 不在 skill_registry（引用不内嵌）" % t)
            target["tools"] = tools
            changed.append("工具")

        written = ["agent_platform/registry/org.json"]
        bdir = _backup(station, written, "update_" + aid)
        _write_json_atomic(org_path, org)
        row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "agent_update",
               "agent_id": aid, "changed": changed, "files": written,
               "backup": os.path.relpath(bdir, station),
               "caller": (p.get("caller") or "").strip()}
        _append_jsonl(_reg(station, "org_audit.jsonl"), row)
        return {"ok": True, "changed": changed}
    except Exception as e:
        return _err("agent update: %r" % (e,))


# ---------------- 知识沉淀（学习环） ----------------

def _knowledge_path(station, aid):
    return os.path.join(_reg(station, "knowledge"), aid + ".jsonl")


def api_agent_knowledge(query, station=STATION):
    try:
        aid = (query.get("id") or [""])[0].strip()
        path = _knowledge_path(station, aid)
        items = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                items = [json.loads(x) for x in f if x.strip()]
        return {"ok": True, "items": items}
    except Exception as e:
        return _err("knowledge: %r" % (e,))


def api_agent_learn(body, station=STATION):
    """拍板边界（诚实降级）：在线学习=模型提炼，属 P2（K-ORG-185 学习环在途）。
    本版显式拒绝，不做无 LLM 的假蒸馏（不假绿判例）。"""
    aid = ((body or {}).get("agent_id") or "").strip()
    return {"ok": False,
            "error": "学习环属 P2（K-ORG-185 缠论学习环在途）：在线学习（模型提炼沉淀）"
                     "本版未开放，请先走知识库流程；接口契约已就绪，P2 接 LLM 后即启用"}
