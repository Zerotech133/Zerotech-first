# -*- coding: utf-8 -*-
"""组织架构图注册服务（K-ORG-053 · 架构即注册表）。

配 portal/org.html：老田在组织架构图上新增部门/AI → 本服务校验后直接落盘
注册表三源（team_*.json + org.json + prompts/*.md）→ 自动重烘 bundle +
重导出 claude_agents → IDE/组织图刷新即见。

端口 127.0.0.1:8093（仅本机）。只写注册表，**不做 git 提交**——
落盘成功后由老田/助理按产线纪律出 K 编号提交（人在回路）。

路由：
  GET  /            → portal/org.html
  GET  /api/org     → {bundle, org, audit_tail}
  POST /api/preview → 校验+生成落盘计划（不写盘）
  POST /api/register→ 校验+写盘+重烘+重导出+审计留痕

注册表纪律（SEC1 审计面对齐）：
  - agent_id 全局唯一、正则 ^[A-Z][A-Z0-9]{1,3}$
  - grants 最小化：write 默认只给 agent/<id>/*（quality 部附加部门 observation）
  - 每次落盘追加 agent_platform/data/org_registry_audit.jsonl
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "agent_platform", "registry")
PROMPTS = os.path.join(ROOT, "agent_platform", "prompts")
HTML = os.path.join(ROOT, "portal", "org.html")
AUDIT = os.path.join(ROOT, "agent_platform", "data", "org_registry_audit.jsonl")
KDOCS = os.path.join(ROOT, "knowledge_docs")          # 知识库文档源
MEMDIR = os.path.join(ROOT, "agent_platform", "data", "memory")  # 岗位专属记忆空间
PORT = 8093

GRADE = {  # 工作年限档（老田口径：年限即档位）
    "E1": ("10年档", "E1·10年档"),
    "E2": ("20年档", "E2·20年档"),
    "E3": ("30年档", "E3·30年档"),
}
MODEL = {  # 表单模型 → 注册表 llm_binding（保持 interpreter split("/") 兼容）+ claude 档
    "glm46": ("glm/glm-4.6", "glm-4.6"),
    "flash": ("glm/glm-4.5-flash", "glm-4.5-flash"),
    "ollama9b": ("ollama/qwen3.5:9b", None),   # claude 档不覆盖→部门默认
    "ollama4b": ("ollama/qwen3.5:4b", None),
}
TOOLS = {  # 能力档 → 注册表 tools + claude 工具面
    "dev": (["devtask", "events"], "Read,Edit,Grep,Glob,Bash"),
    "audit": (["devtask", "events"], "Read,Grep"),
    "light": (["devtask", "events"], "Read,Grep,Glob"),
}
LOCK = threading.Lock()
ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,3}$")
DEPT_RE = re.compile(r"^[a-z][a-z0-9_]{1,20}$")


def load_registry():
    """全量注册表 → (agents_dict, team_files, org)。"""
    org = json.load(open(os.path.join(REG, "org.json"), encoding="utf-8"))
    teams = {}
    for fn in os.listdir(REG):
        if fn.startswith("team_") and fn.endswith(".json"):
            teams[fn] = json.load(open(os.path.join(REG, fn), encoding="utf-8"))
    agents = {}
    for d in teams.values():
        for a in d.get("agents", []):
            agents[a["agent_id"]] = a
    if org.get("chief", {}).get("agent_id"):  # A0 只在 org.json 不在 team 文件
        agents[org["chief"]["agent_id"]] = org["chief"]
    return agents, teams, org


def _rebake_and_reexport():
    """重烘 bundle + 重导出 claude_agents（注册/改配后统一调用）。"""
    for script in ("tools/build_agent_ide_bundle.py",
                   "tools/export_claude_agents.py"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, script)],
                           capture_output=True, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError("%s 失败: %s" % (script,
                                 (r.stderr or b"").decode("utf-8", "ignore")[-400:]))


def _find_team_entry(agent_id):
    """定位工号所在 team 文件与条目（含 org.json chief 兜底）。返回 (path, entry, team)。"""
    for fn in sorted(os.listdir(REG)):
        if fn.startswith("team_") and fn.endswith(".json"):
            p = os.path.join(REG, fn)
            team = json.load(open(p, encoding="utf-8"))
            for e in team.get("agents", []):
                if e.get("agent_id") == agent_id:
                    return p, e, team
    org = json.load(open(os.path.join(REG, "org.json"), encoding="utf-8"))
    if org.get("chief", {}).get("agent_id") == agent_id:
        return None, None, None  # A0 属性走 org.json chief 段（现状编辑面不含 A0）
    return None, None, None


def agent_update(p):
    """K-ORG-054: 属性编辑 + 模型选配。{agent_id, experience?, specialties?, model?, tool_preset?}"""
    aid = (p.get("agent_id") or "").strip().upper()
    path, entry, team = _find_team_entry(aid)
    if entry is None:
        return "工号 %s 不在可编辑注册表（A0 走 org.json chief，暂不支持图上改）", None
    changed = []
    attrs = entry.setdefault("attributes", {"experience": "", "specialties": [], "knowledge": []})
    if "experience" in p:
        attrs["experience"] = (p.get("experience") or "").strip()
        changed.append("从业经验")
    if "specialties" in p:
        sp = p.get("specialties")
        if isinstance(sp, str):
            sp = [s.strip() for s in re.split("[,，、;；]", sp) if s.strip()]
        attrs["specialties"] = sp
        changed.append("擅长")
    if p.get("model"):
        if p["model"] not in MODEL:
            return "模型档未知", None
        binding, claude_model = MODEL[p["model"]]
        entry["llm_binding"] = binding
        if claude_model:
            entry["claude_model"] = claude_model
        else:
            entry.pop("claude_model", None)
        changed.append("模型→%s" % binding)
    if p.get("tool_preset"):
        if p["tool_preset"] not in TOOLS:
            return "能力档未知", None
        tools, claude_tools = TOOLS[p["tool_preset"]]
        entry["tools"] = tools
        entry["claude_tools"] = claude_tools
        changed.append("能力档")
    if not changed:
        return "没有要改的字段", None
    json.dump(team, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # org.json 内联卡同步（有 agent_profiles 的部门）
    org_path = os.path.join(REG, "org.json")
    org = json.load(open(org_path, encoding="utf-8"))
    for d in org.get("departments", []):
        for prof in d.get("agent_profiles", []) or []:
            if prof.get("agent_id") == aid:
                prof["llm_binding"] = entry["llm_binding"]
    json.dump(org, open(org_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    _rebake_and_reexport()
    rec = audit_log({"kind": "update", "agent_id": aid, "dept": entry.get("dept", ""),
                     "grade": entry.get("grade"), "model": p.get("model"),
                     "files": [path]}, changed)
    return None, {"updated": aid, "changed": changed, "audit": rec,
                  "headcount": len(load_registry()[0])}


def agent_learn(p):
    """K-ORG-054: 知识库学习——读知识文档→模型按本岗职责提炼→沉淀 agent/<id>/knowledge 专属空间。"""
    aid = (p.get("agent_id") or "").strip().upper()
    doc = (p.get("doc") or "").strip()
    _, entry, _ = _find_team_entry(aid)
    if entry is None:
        return "工号 %s 不在编", None
    if doc != os.path.basename(doc) or not os.path.isfile(os.path.join(KDOCS, doc)):
        return "知识文档须是 knowledge_docs/ 下的文件名", None
    raw = open(os.path.join(KDOCS, doc), encoding="utf-8", errors="replace").read()
    text = raw[:8000] + ("\n…(中段截)…\n" + raw[-4000:] if len(raw) > 12000 else "")
    charter = entry.get("charter_detail", "")
    attrs = entry.get("attributes", {})
    spec = "、".join(attrs.get("specialties", []))
    prompt = ("你是 %s（%s）。专长：%s。岗位职责：%s。\n"
              "请从下面的知识库文档《%s》中提炼**与本岗职责相关**的学习笔记：\n"
              "- 条目式，600~900 字；\n"
              "- 每条要点标注（能用在岗内什么事上）；\n"
              "- 末尾一行：「对本岗的价值：…」；\n"
              "- 只输出笔记本体。\n\n=== 文档内容（截断） ===\n%s"
              % (aid, entry.get("grade_note", ""), spec or "（待补）",
                 charter, doc, text))
    import agent_coder  # 懒加载（首次 import 才拉模型通道配置）
    note = agent_coder.chat_auto(aid, {"label": "learn:%s" % doc,
                                       "prompt": prompt}).strip()
    if not note:
        return "学习失败：模型空回（通道异常，稍后重试）", None
    os.makedirs(MEMDIR, exist_ok=True)
    fp = os.path.join(MEMDIR, "agent__%s__knowledge.jsonl" % aid)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "learned",
           "doc": doc, "digest": note[:4000],
           "chars": len(raw), "via": agent_coder.last_channel()}
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 注册表 attributes.knowledge 登记已学文档（去重）
    path, entry2, team = _find_team_entry(aid)
    klist = entry2.setdefault("attributes", {}).setdefault("knowledge", [])
    if doc not in klist:
        klist.append(doc)
        json.dump(team, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    _rebake_and_reexport()
    audit_log({"kind": "learn", "agent_id": aid, "dept": entry2.get("dept", ""),
               "grade": entry2.get("grade"), "model": None,
               "files": [doc]}, ["知识沉淀 %d 字" % len(note)])
    return None, {"agent_id": aid, "doc": doc, "digest_len": len(note),
                  "digest": note}


def validate(p):
    """表单 → 校验。返回 (err, normalized)。"""
    kind = p.get("kind")
    if kind not in ("agent", "dept"):
        return "kind 必须是 agent 或 dept", None
    agents, teams, org = load_registry()
    n = {"kind": kind}

    # 部门：已有 或 新建
    dept = (p.get("dept") or "").strip()
    known_depts = {d.get("dept") for d in org.get("departments", [])}
    known_depts |= {d.get("dept") for d in teams.values()}
    if kind == "dept":
        if not DEPT_RE.match(dept):
            return "部门标识需小写字母开头 2-21 位（如 research）", None
        if dept in known_depts:
            return "部门 %s 已存在" % dept, None
        if not (p.get("dept_name") or "").strip():
            return "新部门必须有显示名", None
        n.update(dept=dept, dept_name=p["dept_name"].strip(),
                 dept_charter=(p.get("dept_charter") or "").strip())
    else:
        if dept not in known_depts:
            # K-ORG-053: 图上一路建部门+进首岗（需给显示名）
            if not DEPT_RE.match(dept):
                return "部门标识需小写字母开头 2-21 位（如 research）", None
            if not (p.get("dept_name") or "").strip():
                return "新部门 %s 需提供部门显示名" % dept, None
            n["dept_name"] = p["dept_name"].strip()
            n["dept_charter"] = (p.get("dept_charter") or "").strip()
        n["dept"] = dept

    if kind == "agent":
        aid = (p.get("agent_id") or "").strip().upper()
        if not ID_RE.match(aid):
            return "工号需 2-4 位大写字母+数字（如 DEV9/SEC3/A0B）", None
        if aid in agents:
            return "工号 %s 已在编（%s）" % (aid, agents[aid].get("role_profile", "?")), None
        grade = p.get("grade")
        if grade not in GRADE:
            return "工作年限档必须是 E1/E2/E3", None
        model = p.get("model")
        if model not in MODEL:
            return "模型档未知", None
        preset = p.get("tool_preset")
        if preset not in TOOLS:
            return "能力档必须是 dev/audit/light", None
        charter = (p.get("charter") or "").strip()
        if len(charter) < 8:
            return "岗位职责至少写 8 个字——组织图即职责定义", None
        role_profile = (p.get("role_profile") or "").strip().lower() or aid.lower()
        if len(role_profile) < 3:  # 短工号（如 R1）自动补足可读前缀
            role_profile = "role_" + role_profile
        if not re.match(r"^[a-z0-9_]{3,24}$", role_profile):
            return "prompt 文件名需小写字母数字下划线", None
        if os.path.exists(os.path.join(PROMPTS, role_profile + ".md")):
            return "人设 prompts/%s.md 已存在，换一个名" % role_profile, None
        n.update(agent_id=aid, name=(p.get("name") or "").strip(),
                 grade=grade, model=model, tool_preset=preset,
                 charter=charter, persona=(p.get("persona") or "").strip(),
                 role_profile=role_profile)
    return None, n


def build_plan(n):
    """规范化数据 → 落盘计划（写哪些文件、写什么）。"""
    plan = []
    kind = n["kind"]
    dept = n["dept"]
    dept_src = os.path.join(REG, "team_%s.json" % dept)

    if kind == "dept":
        team = {"_note": "组织架构图注册（K-ORG-053）：显示名=%s" % n["dept_name"],
                "team": dept, "dept": dept, "description": n["dept_name"],
                "agents": []}
        plan.append({"path": "agent_platform/registry/team_%s.json" % dept,
                     "action": "create", "what": "新部门注册表", "content": team})
        plan.append({"path": "agent_platform/registry/org.json",
                     "action": "patch", "what": "departments 追加新部门"})
    else:
        if os.path.exists(dept_src):
            plan.append({"path": "agent_platform/registry/team_%s.json" % dept,
                         "action": "patch", "what": "agents 追加 %s" % n["agent_id"]})
        else:  # 部门在 org.json 有但 team 文件缺（纯图上建）→ 一并新建
            plan.append({"path": "agent_platform/registry/team_%s.json" % dept,
                         "action": "create", "what": "部门注册表+首岗", "content": {
                             "_note": "组织架构图注册（K-ORG-053）", "team": dept,
                             "dept": dept, "description": n.get("dept_name", dept),
                             "agents": []}})
        plan.append({"path": "agent_platform/registry/org.json",
                     "action": "patch", "what": "%s 部门 agents+agent_profiles 追加" % dept})
        plan.append({"path": "agent_platform/prompts/%s.md" % n["role_profile"],
                     "action": "create", "what": "%s 人设" % n["agent_id"]})
    plan.append({"path": "portal/agent_ide_bundle.json",
                 "action": "rebake", "what": "重烘 IDE bundle（自动）"})
    plan.append({"path": "agent_platform/registry/claude_agents.json",
                 "action": "reexport", "what": "重导出 29+ 岗子代理（自动）"})
    return plan


def agent_entry(n):
    """表单 → team_*.json 岗位条目（对齐现有字段形状）。"""
    binding, claude_model = MODEL[n["model"]]
    tools, claude_tools = TOOLS[n["tool_preset"]]
    grade_note, grade_label = GRADE[n["grade"]]
    aid = n["agent_id"]
    write = ["agent/%s/*" % aid]
    if n["dept"] == "quality":
        write.append("dept/quality/observation")
    e = {
        "agent_id": aid,
        "role_profile": n["role_profile"],
        "analysis_type": "org_chart_registered",
        "node_key": aid.lower() + "_core",
        "parent_node_keys": [],
        "level": 1,
        "is_parallel": True,
        "wait_all_complete": False,
        "llm_binding": binding,
        "tools": tools,
        "memory_grants": {
            "read": ["domain/*", "dept/%s/*" % n["dept"], "agent/%s/*" % aid],
            "write": write,
        },
        "grade": n["grade"],
        "grade_note": grade_note,
        "charter_detail": n["charter"],
        # K-ORG-053: 岗级覆盖（export_claude_agents 识别）
        "claude_model": claude_model,
        "claude_tools": claude_tools,
        "source": "K-ORG-053 组织架构图注册",
    }
    return e


def prompt_md(n):
    grade_label = GRADE[n["grade"]][1]
    _, claude_model = MODEL[n["model"]]
    persona = n["persona"] or "（待补：人设要点由老田后续口述充实）"
    preset_name = {"dev": "研发全能（可读写代码）", "audit": "审查只读",
                   "light": "轻量只读"}[n["tool_preset"]]
    return """# {aid} · {role}（org_chart）

> {dept} 部（K-ORG-053 组织架构图注册）。{grade_label}。

## 职责（老田在组织图上定义）
{charter}

## 人设要点
{persona}

## 能力档
- 工具面：{preset_name}
- 模型：{binding}

## 纪律（写死，不可协商）
- 边界以注册表 memory_grants 为准，越界产出无效。
- 结论/产出留痕 `agent/{aid}/decision`，带证据。

## 上游/下游
- 上游：{dept} 部门负责人与 A0 参谋长（田小皮）
- 下游：产出门进部门 observation 与 A0 日报链
""".format(aid=n["agent_id"], role=n["role_profile"], dept=n["dept"],
           grade_label=grade_label, charter=n["charter"], persona=persona,
           preset_name=preset_name, binding=claude_model or n["model"])


def apply_plan(n):
    """真写盘。返回写过的文件列表。"""
    written = []
    dept = n["dept"]
    dept_src = os.path.join(REG, "team_%s.json" % dept)

    if n["kind"] == "dept":
        team = {"_note": "组织架构图注册（K-ORG-053）：显示名=%s" % n["dept_name"],
                "team": dept, "dept": dept, "description": n["dept_name"],
                "agents": []}
        json.dump(team, open(dept_src, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        written.append(dept_src)

    entry = agent_entry(n) if n["kind"] == "agent" else None
    if entry is not None:
        if os.path.exists(dept_src):
            team = json.load(open(dept_src, encoding="utf-8"))
        else:
            team = {"_note": "组织架构图注册（K-ORG-053）", "team": dept,
                    "dept": dept, "description": n.get("dept_name", dept),
                    "agents": []}
        team.setdefault("agents", []).append(entry)
        json.dump(team, open(dept_src, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        written.append(dept_src)

        md = prompt_md(n)
        with open(os.path.join(PROMPTS, n["role_profile"] + ".md"),
                  "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        written.append(os.path.join(PROMPTS, n["role_profile"] + ".md"))

    # org.json 补挂
    org_path = os.path.join(REG, "org.json")
    org = json.load(open(org_path, encoding="utf-8"))
    dep_obj = next((d for d in org["departments"] if d.get("dept") == dept), None)
    if dep_obj is None:
        dep_obj = {"dept": dept, "name": n.get("dept_name", dept), "lead": None,
                   "status": "active",
                   "charter": n.get("dept_charter", n.get("charter", "")),
                   "agents": [], "agent_profiles": [],
                   "source": "K-ORG-053 组织架构图注册"}
        org["departments"].append(dep_obj)
    if n["kind"] == "agent":
        dep_obj.setdefault("agents", [])
        if entry["agent_id"] not in dep_obj["agents"]:
            dep_obj["agents"].append(entry["agent_id"])
        if "agent_profiles" in dep_obj:
            dep_obj["agent_profiles"].append({
                "agent_id": entry["agent_id"],
                "role_profile": entry["role_profile"],
                "charter_detail": entry["charter_detail"],
                "llm_binding": entry["llm_binding"],
                "tools": entry["tools"],
                "memory_grants": entry["memory_grants"],
                "grade": entry["grade"],
                "grade_note": entry["grade_note"],
            })
    json.dump(org, open(org_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    written.append(org_path)

    # 重烘 + 重导出（失败则报错回滚提示：注册表已写，bundle 未更——可重跑）
    env = dict(os.environ)
    for script in ("tools/build_agent_ide_bundle.py",
                   "tools/export_claude_agents.py"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, script)],
                           capture_output=True, cwd=ROOT, env=env)
        if r.returncode != 0:
            raise RuntimeError("%s 失败: %s" % (script,
                                 (r.stderr or b"").decode("utf-8", "ignore")[-400:]))
    return written


def audit_log(n, written):
    os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "operator": "laotian@org-chart", "kind": n["kind"],
           "agent_id": n.get("agent_id"), "dept": n["dept"],
           "grade": n.get("grade"), "model": n.get("model"),
           "files": [os.path.relpath(w, ROOT).replace("\\", "/") for w in written]}
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ── K-ORG-057：项目线注册（工作区内二级维度；与部门注册同构） ──
PROJ_REG = os.path.join(REG, "projects.json")


def project_validate(p):
    """表单 → 校验。返回 (err, normalized)。"""
    dept = (p.get("dept") or "").strip()
    pid = (p.get("project_id") or "").strip().lower()
    name = (p.get("name") or "").strip()
    k_prefix = (p.get("k_prefix") or "").strip().upper()
    members = [m.strip().upper() for m in (p.get("members") or []) if m.strip()]
    charter = (p.get("charter") or "").strip()
    if not pid or not re.fullmatch(r"[a-z][a-z0-9_]{1,23}", pid):
        return "项目线 id 须为小写字母开头的 2~24 位 [a-z0-9_]（如 quant）", None
    if not name or len(name) < 2:
        return "项目线名称至少 2 个字", None
    if k_prefix and not re.fullmatch(r"K-[A-Z0-9]{2,8}", k_prefix):
        return "k 前缀形如 K-STOCK（K- 加 2~8 位大写字母数字）", None
    agents, _, org = load_registry()
    if dept not in [d.get("dept") for d in org.get("departments", [])]:
        return "部门 %s 不存在（先建部门）" % dept, None
    known = set(agents.keys())
    bad = [m for m in members if m not in known]
    if bad:
        return "成员工号未注册: %s" % ", ".join(bad), None
    try:
        existing = json.load(open(PROJ_REG, encoding="utf-8")).get("projects", [])
    except Exception:  # noqa: BLE001
        existing = []
    for pr in existing:
        if pr.get("project_id") == pid:
            return "项目线 %s 已存在（%s·%s）" % (
                pid, pr.get("name", "?"), "活跃" if pr.get("status", "active") == "active" else "已归档"), None
    n = {"kind": "project", "project_id": pid, "dept": dept, "name": name,
         "k_prefix": k_prefix, "members": members, "charter": charter}
    steps = [
        {"action": "write", "path": "agent_platform/registry/projects.json",
         "what": "登记项目线 %s（%s·%s·成员 %d 人）挂在部门 %s"
                 % (pid, name, k_prefix or "无独立前缀", len(members), dept)},
        {"action": "rebake", "path": "portal/agent_ide_bundle.json",
         "what": "重烘 bundle → IDE 该部门工作区顶栏自动出现项目线芯片"},
    ]
    return None, {"n": n, "plan": steps}


def project_apply(n):
    try:
        data = json.load(open(PROJ_REG, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {"projects": []}
    data.setdefault("projects", []).append({
        "project_id": n["project_id"], "dept": n["dept"], "name": n["name"],
        "k_prefix": n["k_prefix"], "members": n["members"],
        "charter": n["charter"], "status": "active",
        "created": time.strftime("%F"),
        "source": "org-chart 注册（operator=laotian）",
    })
    data["updated"] = time.strftime("%F")
    os.makedirs(os.path.dirname(PROJ_REG), exist_ok=True)
    with open(PROJ_REG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    _rebake_and_reexport()
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "operator": "laotian@org-chart", "kind": "project",
           "agent_id": None, "dept": n["dept"], "project_id": n["project_id"],
           "grade": None, "model": None,
           "files": ["agent_platform/registry/projects.json",
                     "portal/agent_ide_bundle.json"]}
    os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/org.html"):
            html = open(HTML, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/api/org":
            agents, teams, org = load_registry()
            bundle = json.load(open(os.path.join(ROOT, "portal",
                                                 "agent_ide_bundle.json"),
                                     encoding="utf-8"))
            tail = []
            if os.path.exists(AUDIT):
                lines = [l for l in open(AUDIT, encoding="utf-8") if l.strip()]
                tail = [json.loads(l) for l in lines[-20:]]
            kdocs = sorted(os.listdir(KDOCS)) if os.path.isdir(KDOCS) else []
            self._json(200, {"bundle": bundle, "org": org, "audit_tail": tail,
                             "knowledge_docs": kdocs})
        elif self.path.startswith("/api/agent/knowledge"):
            q = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&")
                     if "=" in p)
            aid = q.get("id", "")
            fp = os.path.join(MEMDIR, "agent__%s__knowledge.jsonl" % aid)
            rows = []
            if os.path.exists(fp):
                for l in open(fp, encoding="utf-8"):
                    if l.strip():
                        rows.append(json.loads(l))
            self._json(200, {"ok": True, "agent_id": aid, "items": rows[-30:]})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path not in ("/api/preview", "/api/register",
                             "/api/agent/update", "/api/agent/learn",
                             "/api/project/preview", "/api/project/register"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            p = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._json(400, {"ok": False, "error": "bad json: %s" % e})
            return
        with LOCK:
            if self.path == "/api/agent/update":
                err, result = agent_update(p)
                self._json(400 if err else 200,
                           {"ok": not err, **({"error": err} if err else result)})
                return
            if self.path == "/api/agent/learn":
                err, result = agent_learn(p)
                self._json(400 if err else 200,
                           {"ok": not err, **({"error": err} if err else result)})
                return
            if self.path in ("/api/project/preview", "/api/project/register"):
                err, pv = project_validate(p)   # K-ORG-057 项目线注册
                if err:
                    self._json(400, {"ok": False, "error": err})
                    return
                if self.path.endswith("/preview"):
                    self._json(200, {"ok": True, "mode": "preview", "plan": pv["plan"]})
                    return
                rec = project_apply(pv["n"])
                self._json(200, {"ok": True, "mode": "registered",
                                 "audit": rec, "plan": pv["plan"]})
                return
            err, n = validate(p)
            if err:
                self._json(400, {"ok": False, "error": err})
                return
            plan = build_plan(n)
            if self.path == "/api/preview":
                self._json(200, {"ok": True, "mode": "preview", "plan": plan})
                return
            try:
                written = apply_plan(n)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False,
                                 "error": "落盘失败（注册表可能已部分写入，勿重复提交）: %s" % e})
                return
            rec = audit_log(n, written)
            agents, _, _ = load_registry()
            # load_registry 含 chief（A0 只在 org.json）→ 字典长度即在编数
            self._json(200, {"ok": True, "mode": "registered",
                             "audit": rec, "headcount": len(agents),
                             "plan": plan})

    def log_message(self, *a):  # 静音默认访问日志
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("org registry server on http://127.0.0.1:%d  (K-ORG-053)" % PORT)
    srv.serve_forever()
