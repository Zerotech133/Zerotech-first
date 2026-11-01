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
import re
import sys
import time
import pathlib
import logging

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 工单真源固定在主仓（worktree 里跑 bundle 时也能看到主任务板）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _repo_paths import repo_root
    MAIN_ROOT = str(repo_root(pathlib.Path(__file__).resolve().parent))
except Exception:
    MAIN_ROOT = ROOT

REG = os.path.join(ROOT, "agent_platform", "registry")
PROMPTS = os.path.join(ROOT, "agent_platform", "prompts")
OUT = os.path.join(ROOT, "portal", "agent_ide_bundle.json")


# ── 组织架构富化（2026-10-26 WO-IDE-ENRICH）─────────────────────────────
# 名字真源：agent_platform/hr/<席码>/resume.yaml 的 cn_name（三国人名，工具
#   tools/agent_census.py 同源口径）；缺档时用 org.json agent_profiles.cn_name 补。
# 能力真源：org.json departments[].agent_profiles 的 attributes.specialties /
#   charter_detail / grade / grade_note；hr resume 的 skills 作为补充。
def _load_hr_meta(root):
    """{agent_id: {cn_name, skills, experience}}（只读，找不到就空）。"""
    out = {}
    hr_dir = os.path.join(root, "agent_platform", "hr")
    if not os.path.isdir(hr_dir):
        return out
    for seat in os.listdir(hr_dir):
        seat_dir = os.path.join(hr_dir, seat)
        if not os.path.isdir(seat_dir):
            continue
        meta = {}
        for dirpath, _dirs, files in os.walk(seat_dir):
            for fn in files:
                if not fn.endswith((".yaml", ".yml")):
                    continue
                try:
                    text = open(os.path.join(dirpath, fn), encoding="utf-8",
                                errors="replace").read()
                except OSError:
                    continue
                m = re.search(r'cn_name"?\s*:\s*"?([^\s",]+)', text)
                if m and not meta.get("cn_name"):
                    meta["cn_name"] = m.group(1)
                mn = re.search(r"^name:\s*(.+)$", text, re.M)
                if mn and not meta.get("name"):
                    meta["name"] = mn.group(1).strip().strip("'\"")
                m2 = re.search(r"^skills\s*:\s*\[(.*?)\]\s*$", text, re.M | re.S)
                if m2 and not meta.get("skills"):
                    meta["skills"] = [s.strip().strip("'\"")
                                      for s in m2.group(1).split(",") if s.strip()]
                m3 = re.search(r"^\s*role:\s*(.+)$", text, re.M)
                if m3 and not meta.get("experience"):
                    meta["experience"] = m3.group(1).strip()
            if meta.get("cn_name"):
                break
        if meta:
            out[seat] = meta
    return out


def _load_org_profiles(org):
    """{agent_id: profile}；兼容 dict/list 两种 agent_profiles 形态。"""
    out = {}
    for d in org.get("departments") or []:
        ap = d.get("agent_profiles") or {}
        items = ap.values() if isinstance(ap, dict) else ap
        for prof in items or []:
            if isinstance(prof, dict) and prof.get("agent_id"):
                out[prof["agent_id"]] = prof
    return out


def _role_cn_from_prompt(prompt, fallback):
    """从 prompt 首行/首句提取中文岗位名：# ARCH1 · 架构师（…）/ 你是 DEV1，…的代码工程师。"""
    text = prompt or ""
    m = re.search(r"^#\s*[^\s·]+\s*·\s*([^（(\n]+)", text, re.M)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"你是\s*\S+?，[^，\n]*的([^，。\n（(]+)", text)
    if m2:
        return m2.group(1).strip()
    # "你是 TR1，交易部执行岗（v2 人设…）"——无「的」时取首个逗号后的岗位名
    m3 = re.search(r"你是\s*\S+?，([^，。\n（(]+)", text)
    if m3:
        return m3.group(1).strip()
    return fallback or ""


# 职能部/项目部分类（K-ORG-053 起按文件名约定判定：team_proj_* = 项目部只打标，
# 其余 team_*.json 一律职能部落卡——组织图新注册部门免改码自动入编）
# （旧 FUNC_TEAMS 白名单已废：新增部门曾因不在白名单被当项目部处理只打标不落卡）

DEPT_META = {  # 部门显示名（org.json name 键值在此冗余固化，避免页面再拉 org）
    "invest": ("📊 投研部", "func"),
    "dev": ("🏗 工程研发中心", "func"),
    "trading": ("📈 交易部", "func"),
    "data": ("🗄 数据智能部", "func"),
    "product": ("📋 产品管理部", "func"),
    "quality": ("✅ 质量工程部", "func"),
    "marketing": ("📣 市场部", "func"),
    "legal": ("⚖️ 合规法务部", "func"),
    "proj_backtest": ("🅰 回测开发部", "project"),
    "proj_data": ("🅱 数据治理部", "project"),
    "proj_next": ("🅲 下版本设计推理部", "project"),
    # WO-IDE-ZHAOYUN：org 有 profiles 但原先无 team 文件的部门（现在走兜底入编）
    "ops_runtime": ("📡 运行体部 · 驿传署", "func"),
    "chief_line": ("🧠 直属参谋长线 · 参军署", "func"),
    "risk": ("🛡 风控部 · 大都督府", "func"),
}

# devtask 工单库（K-ORG-024 批C先行：研发工单真实投影进 IDE）
TASKS = os.path.join(MAIN_ROOT, ".worktrees", "tasks.json")
# 记忆黑板（K-ORG-027a 批C3：agent/<id>/* 只读视图进 IDE）
MEMORY_DIR = os.path.join(ROOT, "agent_platform", "data", "memory")


def memory_digest(agent_id, max_recent=8):
    """岗卡记忆面板：各 namespace jsonl 行数 + 最近 N 条摘要（只读·K-STOCK-344 三期加深供全文查看）。
    K-ORG-054: +knowledge 种类——知识库学习沉淀（agent/<id>/knowledge 专属空间）。"""
    ns = {}
    for kind in ("decision", "observation", "feedback", "knowledge"):
        fp = os.path.join(MEMORY_DIR, "agent__%s__%s.jsonl" % (agent_id, kind))
        if not os.path.exists(fp):
            continue
        try:
            lines = [l for l in open(fp, encoding="utf-8", errors="replace")
                     if l.strip()]
        except Exception:  # noqa: BLE001
            continue
        if not lines:
            continue
        recent = []
        for l in lines[-max_recent:]:
            try:
                e = json.loads(l)
                recent.append({"ts": e.get("ts", ""), "event": e.get("event", ""),
                               "desc": (e.get("desc", "") or "")[:200]})
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/build_agent_ide_bundle.py", exc)
        ns[kind] = {"count": len(lines), "recent": recent}
    return ns or None


# 编码轨迹（K-ORG-036e F5：shadow/coder_traces 只读视图进 IDE）
TRACE_DIR = os.path.join(ROOT, "shadow", "coder_traces")


def coder_traces_digest(max_orders=5):
    """最近 N 个编码工单轨迹：K编号 + 动作数 + 各类计数 + 最后一条。
    K-STOCK-345 审计加固：k_id<4字符视为畸形轨迹不入展示面（证据文件保留在磁盘）；
    events 上限 200 条防超大 trace 撑爆 bundle。"""
    files = []
    if os.path.isdir(TRACE_DIR):
        for day in os.listdir(TRACE_DIR):
            d = os.path.join(TRACE_DIR, day)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if fn.endswith(".jsonl"):
                    files.append((os.path.getmtime(os.path.join(d, fn)),
                                  os.path.join(d, fn)))
    files.sort(reverse=True)
    out = []
    skipped = []
    for _, fp in files[:max_orders]:
        try:
            recs = [json.loads(l) for l in open(fp, encoding="utf-8",
                                                errors="replace") if l.strip()]
        except Exception:  # noqa: BLE001
            continue
        if not recs:
            continue
        last = recs[-1]
        kid = last.get("k_id", os.path.basename(fp)[:-6])
        if len(str(kid)) < 4:  # 畸形轨迹（如 T.jsonl·345 审计）：不进展示面
            skipped.append(str(kid))
            continue
        kinds = {}
        for r in recs:
            kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
        events = [{"ts": r.get("ts", ""), "kind": r.get("kind", "?"),
                   "detail": (r.get("detail", "") or "")[:160]} for r in recs]
        if len(events) > 200:  # 345 审计：防超大 trace 撑爆 bundle（保留最近 200）
            events = events[-200:]
        out.append({"k_id": kid,
                    "actions": len(recs),
                    "kinds": kinds,
                    "last_ts": last.get("ts", ""),
                    "last": (last.get("kind", "") + ": " + (last.get("detail", "") or ""))[:100],
                    "events": events})
    if skipped:
        print("traces skip(畸形k_id):", skipped)
    return out or None


def _default_attributes(a):
    """K-ORG-054: 未填属性岗按年限档推导经验字段（图上可再编辑覆盖）。"""
    g = a.get("grade") or ""
    years = {"E3": "30 年", "E2": "20 年", "E1": "10 年"}.get(g, "")
    return {"experience": ("%s从业经验（%s）" % (years, a.get("grade_note", ""))
                           if years else a.get("grade_note", "")),
            "specialties": [], "knowledge": []}


def disambiguate_names(agents, org):
    """同名不同 id 显式消歧（WO-SEAT-DEDUP / 2026-11-01 K-ORG-20261101-STRAT2FIX）。

    真实冲突不得静默吞掉：保留原名者只 1 席（优先 org.json 已注册席位，其次 agent_id
    排序），其余卡改「原名（ID）」限定别名，并把 raw 冲突/别名映射返回给 stats 留档。
    返回 ``(raw_collisions, name_aliases)``；就地改 ``agents`` 卡（cn_name/name/name_alias）。
    """
    by_name = {}
    for aid, card in agents.items():
        cn = (card or {}).get("cn_name") or ""
        if cn:
            by_name.setdefault(cn, []).append(aid)
    raw = {nm: sorted(ids, key=str) for nm, ids in by_name.items() if len(ids) > 1}
    org_registered = set()
    for dep in (org or {}).get("departments") or []:
        for aid in dep.get("formal_agents") or []:
            org_registered.add(str(aid))
        for item in dep.get("liaison_agents") or []:
            if isinstance(item, dict) and item.get("agent_id"):
                org_registered.add(str(item["agent_id"]))
        profiles = dep.get("agent_profiles") or {}
        for prof in (profiles.values() if isinstance(profiles, dict) else profiles):
            if isinstance(prof, dict) and prof.get("agent_id"):
                org_registered.add(str(prof["agent_id"]))
    aliases = {}
    for name, ids in sorted(raw.items()):
        ranked = sorted(ids, key=lambda aid: (0 if str(aid) in org_registered else 1,
                                             str(aid)))
        keep = ranked[0]
        for aid in ranked[1:]:
            card = agents.get(aid)
            if not isinstance(card, dict):
                continue
            alias = "%s（%s）" % (name, aid)
            card["cn_name_raw"] = name
            card["cn_name"] = alias
            card["name"] = alias
            card["name_alias"] = {"raw": name, "alias": alias, "kept": keep,
                                  "policy": "WO-SEAT-DEDUP/STRAT2FIX"}
            aliases[aid] = {"cn_name": name, "alias": alias, "kept": keep}
    return raw, aliases


def main(argv=None):
    # 2026-10-31 WO-QUAL-BUNDLE-20261031：支持 --out（缺省 OUT 不变），供 fresh
    # worktree 引导/测试生成到指定路径，不覆写仓内正式 bundle。
    argv = sys.argv[1:] if argv is None else list(argv)
    out_path = OUT
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("用法：python tools/build_agent_ide_bundle.py [--out <bundle.json>]",
                  file=sys.stderr)
            return 2
        out_path = argv[i + 1]
    agents = {}          # agent_id -> 卡片（职能部主数据）
    dept_agents = {}     # dept -> [agent_id]（含项目部编制）
    projects_of = {}     # agent_id -> [项目 dept]

    # 参谋长田丰（A0）先行入编——全组织汇报顶点（K-ORG-023）
    org = json.load(open(os.path.join(REG, "org.json"), encoding="utf-8"))
    chief = org.get("chief", {})
    agents["A0"] = {
        "id": "A0",
        "dept": "chief",
        "name": chief.get("name", "田丰"),
        "role_profile": chief.get("role_profile", "chief_of_staff"),
        "grade": "E3",
        "grade_note": "30年档：产品+研发全在性/决策仲裁/多 AI 编排",
        # 在编数尾段回填（K-ORG-053：组织图动态注册后岗数随实际，不再硬编码）
        "charter": "产品经理+研发（全在性）：需求定义→架构/接口契约/代码参考→拆解派发→"
                   "AI 自动执行→验收；无人能定案时做最优决策并指挥 AI 执行；L0/L1 自主、L2 报老田。",
        "llm": chief.get("llm_binding", "ollama/qwen3.5:9b"),
        "tools": ["events", "devtask", "delegation"],
        "grants_read": ["domain/*", "dept/*", "agent/*", "deliverables/**", "work/**"],
        "grants_write": ["chief/decision", "chief/observation", "deliverables/**", "work/**"],
        "prompt_file": "agent_platform/prompts/a0_chief.md",
        "prompt": open(os.path.join(PROMPTS, "a0_chief.md"),
                       encoding="utf-8").read() if os.path.exists(
                       os.path.join(PROMPTS, "a0_chief.md")) else "",
        "projects": [],
        "team_desc": "产品经理+研发（全在性）· 最优决策/多 AI 编排/验收",
        "attributes": chief.get("attributes") or {
            "experience": "组织枢纽：汇报聚合、任务仲裁、记忆治理（K-ORG-015 起 live）",
            "specialties": ["日报判读", "任务单仲裁", "风险优先输出", "记忆治理"],
            "knowledge": []},
    }
    dept_agents["chief"] = ["A0"]

    for fn in sorted(glob.glob(os.path.join(REG, "team_*.json"))):
        base = os.path.basename(fn)
        d = json.load(open(fn, encoding="utf-8"))
        dept = d.get("dept", "")
        ids = [a.get("agent_id") for a in d.get("agents", [])]
        # K-ORG-052: 合并语义——chief 的 A0 先行入编，team_chief.json 并入不覆盖
        dept_agents[dept] = sorted(set(dept_agents.get(dept, [])) | set(ids))
        # K-ORG-053: 项目部命名约定 team_proj_*；其余一律职能部落卡——
        # 组织图新注册的部门 team 文件免登记自动入编（去 FUNC_TEAMS 白名单依赖）
        if not base.startswith("team_proj_"):
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
                    # K-ORG-054: 属性透传（从业经验/擅长/知识库）；缺省按年限档自动推导
                    "attributes": a.get("attributes") or _default_attributes(a),
                    # 2026-10-27 WO-ROLE-GROUP-MODEL：角色分工（工程域/中文岗位名）
                    "discipline": a.get("discipline", ""),
                    "role_title": a.get("role_title", ""),
                    # 2026-10-27 WO-ORG-CHART：组织架构显式字段
                    "group": a.get("group", ""),
                    "org_role": a.get("org_role", ""),
                    "report_to": a.get("report_to", ""),
                }
        else:
            for aid in ids:
                projects_of.setdefault(aid, []).append(dept)

    # ── P1 兜底（WO-IDE-ZHAOYUN）：org.json 有 agent_profiles 但未落卡的席位
    # 自动入编（无 team 文件的整部门 + 已有部门里漏掉的 profile 席）；
    # 已有席位不重复、不覆盖。
    for d in org.get("departments") or []:
        dep = d.get("dept")
        ap = d.get("agent_profiles") or {}
        items = ap.values() if isinstance(ap, dict) else ap
        new_ids = []
        for prof in items or []:
            if not isinstance(prof, dict):
                continue
            aid = prof.get("agent_id")
            if not aid or aid in agents:
                continue
            grants = prof.get("memory_grants") or {}
            agents[aid] = {
                "id": aid,
                "dept": dep,
                "role_profile": prof.get("role_profile", ""),
                "grade": prof.get("grade", ""),
                "grade_note": prof.get("grade_note", ""),
                "charter": prof.get("charter_detail", ""),
                "llm": prof.get("llm_binding", ""),
                "tools": prof.get("tools", []),
                "grants_read": grants.get("read", []),
                "grants_write": grants.get("write", []),
                "prompt_file": "",
                "prompt": "",
                "projects": [],
                "team_desc": d.get("description") or d.get("charter", ""),
                "attributes": prof.get("attributes") or {},
                "entry_source": "org_profiles",  # 与 team 文件落卡区分（排查用）
                "discipline": prof.get("discipline", ""),
                "role_title": prof.get("role_title", ""),
                "group": prof.get("group", ""),
                "org_role": prof.get("org_role", ""),
                "report_to": prof.get("report_to", ""),
            }
            new_ids.append(aid)
        if new_ids:
            dept_agents[dep] = list(dict.fromkeys(
                dept_agents.get(dep, []) + new_ids))

    # 项目标签回填 + prompt 全文（chief 田丰已在入编时装好 a0_chief.md）
    miss_prompt = []
    for aid, card in agents.items():
        card["projects"] = projects_of.get(aid, [])
        card["memory"] = memory_digest(aid)  # K-ORG-027a 记忆面板（只读摘要）
        if card["prompt"]:
            continue
        pf = os.path.join(PROMPTS, card["role_profile"] + ".md")
        if os.path.exists(pf):
            card["prompt_file"] = "agent_platform/prompts/%s.md" % card["role_profile"]
            card["prompt"] = open(pf, encoding="utf-8").read()
        else:
            miss_prompt.append(aid)

    # ── 组织架构富化（WO-IDE-ENRICH）：名字 + 能力 + 部门称谓 ──────────────
    hr_meta = _load_hr_meta(ROOT)
    profiles = _load_org_profiles(org)
    dept_name_map = {d.get("dept"): d.get("name", "")
                     for d in org.get("departments", [])}
    dept_title_map = {d.get("dept"): d.get("cn_title", "")
                      for d in org.get("departments", [])}
    for aid, card in agents.items():
        hr = hr_meta.get(aid) or {}
        prof = profiles.get(aid) or {}
        card["matrix_assignments"] = list(prof.get("matrix_assignments") or [])
        p_attrs = prof.get("attributes") if isinstance(prof.get("attributes"), dict) else {}
        t_attrs = card.get("attributes") if isinstance(card.get("attributes"), dict) else {}
        caps = (p_attrs.get("specialties") or t_attrs.get("specialties")
                or card.get("capabilities") or hr.get("skills") or [])
        # org 补档席的 specialties 可能只是 role slug（如 risk_conservative_sentinel）：
        # 视为占位，优先用 HR 技能
        if (len(caps) == 1 and caps[0] == card.get("role_profile")
                and hr.get("skills")):
            caps = hr["skills"]
        card["cn_name"] = (hr.get("cn_name") or prof.get("cn_name")
                           or card.get("cn_name") or "")
        if card["cn_name"]:
            card["name"] = card["cn_name"]
        card["role_cn"] = (_role_cn_from_prompt(card.get("prompt", ""),
                                                card.get("role_profile", ""))
                           or card.get("role_cn", ""))
        # 无 prompt 且 role_cn 还是英文 slug 时，回退 HR 档案里的中文岗位名
        if (not re.search(r"[\u4e00-\u9fff]", card["role_cn"] or "")
                and re.search(r"[\u4e00-\u9fff]", hr.get("name") or "")):
            card["role_cn"] = hr["name"]
        # 2026-10-27 WO-ROLE-GROUP-MODEL：真源 role_title/discipline 优先于岗位 slug
        if not card.get("discipline"):
            card["discipline"] = prof.get("discipline", "")
        if not card.get("role_title"):
            card["role_title"] = prof.get("role_title", "")
        if card.get("role_title"):
            card["role_cn"] = card["role_title"]
        # 组织架构字段（A0A 等非 team_dev 席也补齐）
        for _k in ("group", "org_role", "report_to"):
            if not card.get(_k):
                card[_k] = prof.get(_k, "")
        if aid == "A0":
            card["role_cn"] = "产品经理+研发（全在性）"
        card["capabilities"] = list(caps)
        card["skills"] = list(hr.get("skills") or [])
        card["experience"] = (p_attrs.get("experience") or t_attrs.get("experience")
                              or hr.get("experience") or "")
        if prof.get("charter_detail"):
            card["charter"] = prof["charter_detail"]
        if prof.get("grade"):
            card["grade"] = prof["grade"]
        if prof.get("grade_note"):
            card["grade_note"] = prof["grade_note"]
        card["dept_name"] = card.get("dept_name") or dept_name_map.get(card.get("dept"), "")
        card["dept_cn_title"] = (card.get("dept_cn_title")
                                 or dept_title_map.get(card.get("dept"), ""))

    # 部门视图（IDE 左树顺序：参谋长在顶，职能部居中，项目部在后）
    # K-ORG-053：组织图动态注册的新部门免改码——org.json departments 取显示名/type，
    # order 之外的新部门按 org.json 顺序追加在尾部
    org_dept_meta = {d.get("dept"): d for d in org.get("departments", [])}
    order = ["chief", "invest", "dev", "trading", "data", "product", "quality",
             "marketing", "legal", "proj_backtest", "proj_data", "proj_next"]
    order += [dep for dep in org_dept_meta if dep not in order
              and dep in dept_agents]
    order += [dep for dep in dept_agents if dep not in order]  # 纯 team 文件新部门兜底
    departments = []
    for dep in order:
        if dep not in dept_agents:
            continue
        od = org_dept_meta.get(dep, {})
        if dep == "chief":
            name, typ = "🧠 参谋长 · 田丰", "chief"
        elif dep in DEPT_META:
            name, typ = DEPT_META[dep]
        else:  # 动态注册部门：显示名与类型全部取 org.json
            name, typ = od.get("name", dep), od.get("type", "func")
        lead_id = od.get("lead") or ""
        lead_name = (agents.get(lead_id) or {}).get("cn_name") or ""
        departments.append({
            "id": dep, "name": name, "type": typ,
            "charter": od.get("charter", ""),
            "cn_title": od.get("cn_title", ""),  # 部门中文称谓（谋略府/将作监…）
            "lead_id": lead_id,
            "lead_name": lead_name,
            "agents": [a for a in dept_agents[dep] if a in agents],
            "all_ids": dept_agents[dep],  # 项目部可能引用未在职能表落卡者（现状无）
        })

    # ── P2（WO-IDE-ZHAOYUN）：治理席虚拟卡（profile 角色，不占编）────────────
    # 与 R2（org 编制·保守风控哨）是两个 id：页面/报表按 id 区分，勿按名字聚合。
    zy_caps = []
    try:
        cap_file = os.path.join(os.path.expanduser("~"), ".hermes", "profiles",
                                "zhaoyun", "skills", "CAPABILITIES.json")
        cap_data = json.load(open(cap_file, encoding="utf-8-sig"))
        zy_caps = [str(c.get("name")) for c in (cap_data.get("capabilities") or [])
                   if isinstance(c, dict) and c.get("name")]
    except (OSError, ValueError):
        zy_caps = []
    if not zy_caps:
        zy_caps = ["扫描与收口", "健康卡判读", "AI 归纳建议", "判例沉淀", "经验库维护"]
    agents["ZHAOYUN"] = {
        "id": "ZHAOYUN",
        "dept": "governance",
        "cn_name": "赵云·治理席（不占编）",
        "name": "赵云·治理席（不占编）",
        "role_cn": "治理席（进化官）",
        "role_profile": "governance_seat",
        "grade": "",
        "grade_note": "profile 角色，不占 org.json 编制（D5-C）",
        "charter": "治理面：扫描/收口/健康卡/AI 归纳建议/判例沉淀；只产建议与证据，"
                   "不裁决、不自动执行、不持私钥。",
        "llm": "deepseek/deepseek-v4-pro（DSH 会话载体）",
        "tools": [],
        "grants_read": [],
        "grants_write": [],
        "prompt_file": "",
        "prompt": "",
        "projects": [],
        "team_desc": "治理席（不占编）· 只产建议与证据",
        "attributes": {"specialties": zy_caps},
        "capabilities": zy_caps,
        "entry_source": "virtual_governance",
        "seat_note": "profile 角色，不占编（D5-C）；与 R2 风控席（org 编制·保守风控哨）"
                     "是不同 id，报表按 id 区分。",
        "home_page": "http://127.0.0.1:8796/zhaoyun",
    }
    governance_dept = {
        "id": "governance",
        "name": "🧭 治理席（不占编）",
        "type": "func",
        "charter": "赵云治理席（ZHAOYUN）：扫描/收口/健康卡/AI 归纳建议/判例沉淀；"
                   "只产建议与证据，不占编、不裁决、不自动执行。",
        "cn_title": "治理台",
        "lead_id": "ZHAOYUN",
        "lead_name": "赵云",
        "agents": ["ZHAOYUN"],
        "all_ids": ["ZHAOYUN"],
    }
    chief_idx = next((i for i, d in enumerate(departments) if d["id"] == "chief"), 0)
    departments.insert(chief_idx + 1, governance_dept)  # 左树顶部可见，且与编制区分

    # 参谋长 charter 回填实际**在编**数（排除不占编的治理席虚拟卡）
    org_agent_count = sum(1 for c in agents.values()
                          if c.get("entry_source") != "virtual_governance")
    if "{N}" in agents["A0"]["charter"]:
        agents["A0"]["charter"] = agents["A0"]["charter"].replace(
            "{N}", str(org_agent_count))

    # 工单投影（K-ORG-024 批C先行）：devtask tasks.json 活跃单挂到岗卡
    # owner 精确匹配 agent_id（如 DEV1/PM1）；Codex 系自由身 worker 归全局池
    workorders = []
    if os.path.exists(TASKS):
        raw = json.load(open(TASKS, encoding="utf-8"))
        for tid, t in raw.items():
            if t.get("status") not in ("doing", "review"):
                continue
            wo = {
                "id": tid,
                "desc": t.get("desc", ""),
                "status": t.get("status"),
                "owner": t.get("owner", ""),
                "branch": t.get("branch", ""),
                "created": t.get("created", ""),
                "project": t.get("project", "general"),  # K-ORG-057 项目线
            }
            workorders.append(wo)
            owner = t.get("owner", "")
            if owner in agents:
                agents[owner].setdefault("tasks", []).append(
                    {"id": tid, "desc": t.get("desc", ""), "status": t.get("status")})
    wo_stats = {
        "doing": sum(1 for w in workorders if w["status"] == "doing"),
        "review": sum(1 for w in workorders if w["status"] == "review"),
    }

    # 平台代码内嵌（IDE 左栏代码预览，纯静态零后端）：agent_platform 顶层 .py + registry/org.json
    code_files = []
    cf_paths = sorted(glob.glob(os.path.join(ROOT, "agent_platform", "*.py")))
    cf_paths.append(os.path.join(ROOT, "agent_platform", "registry", "org.json"))
    CF_CAP = 60000  # 单文件上限（现状最大约 10KB，不会触发；防膨胀兜底）
    for fp in cf_paths:
        try:
            txt = open(fp, encoding="utf-8", errors="replace").read()
        except Exception:  # noqa: BLE001
            continue
        truncated = len(txt) > CF_CAP
        code_files.append({
            "path": os.path.relpath(fp, ROOT).replace("\\", "/"),
            "size": len(txt),
            "lines": txt.count("\n") + 1,
            "truncated": truncated,
            "content": txt[:CF_CAP] + ("\n… [已截断，完整内容看仓库文件]" if truncated else ""),
        })

    # A0 日报+点评烘入（342 设计稿④底部面板数据源，随 bundle 重建刷新）
    SHADOW = os.path.join(ROOT, "agent_platform", "data", "shadow")
    def _latest_md(prefix):
        fs = sorted(glob.glob(os.path.join(SHADOW, prefix + "*.md")))
        return fs[-1] if fs else None
    a0_daily = a0_review = None
    fp = _latest_md("daily_report_")
    if fp:
        a0_daily = {"date": os.path.basename(fp)[13:23],
                    "content": open(fp, encoding="utf-8", errors="replace").read()[:8000]}
    fp = _latest_md("a0_review_")
    if fp:
        a0_review = {"date": os.path.basename(fp)[10:20],
                     "content": open(fp, encoding="utf-8", errors="replace").read()[:4000]}

    # K-ORG-057：项目线注册表透传（项目线=工作区内二级维度，不另开工作区）
    proj_path = os.path.join(REG, "projects.json")
    projects = []
    if os.path.exists(proj_path):
        for pr in json.load(open(proj_path, encoding="utf-8")).get("projects", []):
            projects.append({
                "project_id": pr.get("project_id", ""),
                "dept": pr.get("dept", ""),
                "name": pr.get("name", pr.get("project_id", "")),
                "k_prefix": pr.get("k_prefix", ""),
                "members": pr.get("members", []),
                "charter": pr.get("charter", ""),
                "status": pr.get("status", "active"),
            })

    # 名称唯一性守卫（WO-SEAT-DEDUP；2026-11-01 K-ORG-20261101-STRAT2FIX）：
    # 同名不同 id 显式消歧；raw 冲突留档 stats.name_collisions_raw，别名留档
    # stats.name_aliases（不静默吞掉真实冲突，见 disambiguate_names）。
    _raw_collisions, name_aliases = disambiguate_names(agents, org)
    name_collisions = {}  # 显式消歧后仍同名 = 契约违规；此处置空并靠 raw/aliases 留证

    bundle = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "generator": "tools/build_agent_ide_bundle.py (K-ORG-022/024)",
        "stats": {
            "agents": org_agent_count,
            "departments": len(departments),
            "governance_seats": 1,
            "total_cards": len(agents),
            "prompt_ok": org_agent_count - len(miss_prompt),
            "prompt_miss": miss_prompt,
            "name_collisions": name_collisions,
            "name_collisions_raw": _raw_collisions,
            "name_aliases": name_aliases,
            "workorders_doing": wo_stats["doing"],
            "workorders_review": wo_stats["review"],
        },
        "workorders": workorders,
        "coder_traces": coder_traces_digest(),  # K-ORG-036e F5 轨迹只读视图
        "code_files": code_files,  # IDE 左栏代码预览（agent_platform 源码内嵌）
        "a0_daily": a0_daily,      # 最近一期 A0 日报（只读快照）
        "a0_review": a0_review,    # 最近一期 A0 点评（只读快照）
        "departments": departments,
        "command_hq": org.get("command_hq") or {},
        "projects": projects,  # K-ORG-057 项目线（IDE 工作区内 chips）
        "agents": agents,
    }
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=1)
    print("bundle ->", out_path)
    print("agents:", len(agents), "| prompt_ok:", bundle["stats"]["prompt_ok"],
          "| miss:", miss_prompt or "none")
    print("workorders: doing=%d review=%d" % (wo_stats["doing"], wo_stats["review"]))
    if _raw_collisions:
        print("WARN 同名冲突已显式消歧:",
              json.dumps(_raw_collisions, ensure_ascii=False), "->",
              json.dumps(name_aliases, ensure_ascii=False))
    print("code_files: %d 个 · 合计 %.1f KB" % (
        len(code_files), sum(f["size"] for f in code_files) / 1024))


if __name__ == "__main__":
    sys.exit(main())

