# -*- coding: utf-8 -*-
"""Agent 编制工具（WO-LEGION-S33）——把「部门要加一个新 Agent」从纸面变成可执行。

为什么要有它：沙盘的虚席招募此前只能投一张工单到 bridge_inbox，**系统里没有任何工具
能真正把人加进编制**。org.json 的 agent_profiles 是组织真源，靠手改 JSON 入编，风险高
且无校验（重名 Agent 已经在历史上造出 4 对，账面混淆）。

设计红线：
  1. **默认 dry-run**：不传 --apply 只打印变更计划，一个字节都不写。
  2. **真写必须两个凭据**：工单号（--ticket）+ 放行人（--signed-by）。缺一即拒。
     签字人必须是老田——本工具无法验证身份，只能要求显式填写，并在产出里标注。
  3. **先备份后写**：org.json 备份到 registry/backups/org-<时间戳>.json，可回滚。
  4. **写后自检 + 失败自动回滚**：重新解析、核对总数、**深比较确认其他部门一字未动**；
     任一不符立即用备份还原并报错，绝不留半成品。
  5. **不覆盖既有 hr 档案**：hr 目录属编制本身，`_agent_id_index` 同时扫 org 与 hr，
     id 若已有履历即报「已被占用」拦下——不再单设一层判定（否则即死代码）。

用法：
    # 只看计划（默认，不写）
    python engine/org_enroll.py --dept dev --id DEV9 --name 蒋琬 --role 日志工程师 \
        --duty "负责全链路运行日志治理与异常归因，维护日志规范" \
        --binding ollama/qwen3.5:9b --ticket WO-ORG-210 --signed-by 老田

    # 确认无误后真写
    python engine/org_enroll.py ... 同上 ... --apply

    # 回滚
    python engine/org_enroll.py --rollback registry/backups/org-20261021-161500.json --signed-by 老田

    # 任命主官（只改部门 lead，不动名单与档案；跨部门兼任必须带 --cross-note）
    python engine/org_enroll.py --set-lead --dept proj_data --lead DATA1 \
        --ticket WO-LEGION-S41 --signed-by 老田 --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import time
from pathlib import Path

SCHEMA_VERSION = "1.0"
RULES_VERSION = "1.0"

BINDING_ALLOW = frozenset({
    "ollama/qwen3.5:9b",
    "ollama/qwen3.6:35b-a3b",
    "glm/glm-5.3",
    "glm/glm-5.3-flash",      # E1 调度/受理岗轻快档（INTEL0 履历即为该档）
    "glm/glm-4.6",
    "openai/gpt-6.0",
    "none",                  # 规则执行体：无 LLM 依赖（如 BEACON1 巡检哨）。
                             # 给这类席位硬填 9b 等于虚构能力，必须允许显式声明「无」。
})
GRADE_ALLOW = frozenset({"E1", "E2", "E3"})
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
_TICKET_RE = re.compile(r"^(WO-|K-|LEGION-|WOLEGION-)", re.I)
DUTY_MIN_LEN = 10


try:                                     # 包内导入
    from engine.org_profiles import profile_index
except ImportError:                      # 直接 `python engine/org_enroll.py` 运行
    from org_profiles import profile_index


class EnrollError(ValueError):
    """编制校验失败（含凭据缺失、重名、词表越界）——一律 fail-visible，不静默降级。"""


def _org_path(root: Path) -> Path:
    return root / "registry" / "org.json"


def _unique_backup_path(bdir: Path) -> Path:
    """同秒内多次写入不得互相覆盖备份。

    备份是回滚的唯一依据，被覆盖等于把早期状态永久丢失。时间戳只有秒级精度，
    连续补档多席必然撞在同一秒，故冲突时追加序号。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cand = bdir / ("org-%s.json" % stamp)
    i = 1
    while cand.exists():
        cand = bdir / ("org-%s-%d.json" % (stamp, i))
        i += 1
    return cand


def _hr_dir(root: Path) -> Path:
    return root / "hr"


def load_org(root: Path) -> dict:
    p = _org_path(root)
    if not p.exists():
        raise EnrollError("org.json not found: %s" % p)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise EnrollError("org.json unreadable: %s" % e)


def _find_dept(org: dict, dept: str) -> dict:
    for d in org.get("departments") or []:
        if d.get("dept") == dept:
            return d
    raise EnrollError("unknown dept: %s（可选 %s）"
                      % (dept, ", ".join(sorted(str(x.get("dept")) for x in org.get("departments") or []))))


def _cn_index(root: Path, org: dict) -> dict:
    """中文名 → 已占用者列表。org 里没有 cn_name 字段，故以 hr/ 名册为准。

    历史遗留的重名（司马懿/满宠/马良/陆逊 各 2 职）已造成账面混淆，新入编一律阻断。
    """
    idx: dict[str, list[str]] = {}
    hr = _hr_dir(root)
    if hr.exists():
        for d in sorted(hr.iterdir()):
            f = d / "resume.yaml"
            if not f.is_file():
                continue
            cn = ""
            try:
                for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.startswith("cn_name:"):
                        cn = ln.split(":", 1)[1].strip().strip("'\"")
                        break
                    if ln.startswith("name:") and not cn:
                        cn = ln.split(":", 1)[1].strip().strip("'\"")
            except OSError:
                continue
            if cn:
                idx.setdefault(cn, []).append(d.name)
    return idx


def _agent_id_index(org: dict, root: Path) -> dict:
    """agent_id → 占用位置（部门 or hr 档案目录）。"""
    idx: dict[str, str] = {}
    for d in org.get("departments") or []:
        for p in d.get("agent_profiles") or []:
            aid = str(p.get("agent_id") or "")
            if aid:
                idx[aid] = "org:%s" % d.get("dept")
        for aid in d.get("agents") or []:
            idx.setdefault(str(aid), "org.agents:%s" % d.get("dept"))
    hr = _hr_dir(root)
    if hr.exists():
        for d in hr.iterdir():
            if d.is_dir():
                idx.setdefault(d.name, "hr档案")
    return idx


def build_plan(root: Path, *, dept: str, agent_id: str, cn_name: str,
               role_profile: str, duty: str, binding: str, grade: str = "E2",
               ticket: str = "", signed_by: str = "",
               analysis_type: str = "", tools: list | None = None) -> dict:
    """只读校验并产出变更计划。**不写任何字节**，可安全用于界面预览。"""
    org = load_org(root)
    target = _find_dept(org, dept)

    aid = (agent_id or "").strip()
    if not _ID_RE.match(aid):
        raise EnrollError("agent_id 非法：%r（须大写字母开头、2~16 位大写字母或数字，如 DEV9）" % agent_id)

    id_idx = _agent_id_index(org, root)
    if aid in id_idx:
        raise EnrollError("agent_id 已被占用：%s（位置：%s）" % (aid, id_idx[aid]))

    cn = (cn_name or "").strip()
    if not cn:
        raise EnrollError("cn_name 不可为空（军团席位必须有中文名，账号无名的历史教训）")
    cn_idx = _cn_index(root, org)
    if cn in cn_idx:
        raise EnrollError("中文名重名：%s 已被 %s 使用。历史上已有 4 对重名造成账面混淆，"
                          "新入编一律阻断——请换一个不冲突的名字。"
                          % (cn, "、".join(cn_idx[cn])))

    role = (role_profile or "").strip()
    if not role:
        raise EnrollError("role_profile 不可为空（这是「这个 Agent 到底干嘛」的答案，不能留白）")

    duty_s = (duty or "").strip()
    if len(duty_s) < DUTY_MIN_LEN:
        raise EnrollError("duty 至少 %d 字：必须写清职责边界，否则新席位无法被指挥（当前 %d 字）"
                          % (DUTY_MIN_LEN, len(duty_s)))

    if binding not in BINDING_ALLOW:
        raise EnrollError("llm_binding 不在词表：%r（可选 %s）"
                          % (binding, ", ".join(sorted(BINDING_ALLOW))))
    if grade not in GRADE_ALLOW:
        raise EnrollError("grade 不在词表：%r（可选 %s）" % (grade, ", ".join(sorted(GRADE_ALLOW))))

    if not (ticket or "").strip():
        raise EnrollError("缺少工单号：无单不写码（--ticket，形如 WO-ORG-210）")
    if not _TICKET_RE.match(ticket.strip()):
        raise EnrollError("工单号格式不符：%r（须 WO- / K- / LEGION- 前缀）" % ticket)
    if not (signed_by or "").strip():
        raise EnrollError("缺少放行人：入编改组织真源，须老田显式签字（--signed-by）")

    profile = {
        "agent_id": aid,
        "role_profile": role,
        "analysis_type": (analysis_type or "").strip(),
        "charter_detail": duty_s,
        "llm_binding": binding,
        "binding_note": "由 org_enroll 按工单 %s 入编（放行人：%s）"
                        % (ticket.strip(), signed_by.strip()),
        "tools": list(tools or []),
        "tools_note": "入编初始为空，按工单另行授予",
        "memory_grants": [],
        "grade": grade,
        "grade_note": "入编初始档，待 KPI 观察后调整",
        "attributes": {},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "dept": dept,
        "dept_name": target.get("name"),
        "cn_name": cn,
        "agent": profile,
        "hr_dir": str(_hr_dir(root) / aid),
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "writes": ["registry/org.json（追加 agent_profiles + agents）",
                   "hr/%s/resume.yaml（新建骨架，不覆盖）" % aid],
        "note": "dry-run 计划：以下变更尚未写入任何字节",
    }


def build_backfill_plan(root: Path, *, dept: str, agent_id: str, role_profile: str,
                        duty: str, binding: str, grade: str = "E2",
                        ticket: str = "", signed_by: str = "",
                        analysis_type: str = "", tools: list | None = None) -> dict:
    """补档计划：给**已列入部门 agents 名单、但没有 agent_profiles 档案**的席位补档案。

    为什么不走 build_plan：build_plan 是「新增席位」，会因 id 已在名单而拒绝。
    而 S34 审计出的 34 个「有名无档」席位需要的是「补档案不动名单」——
    名单里已经有人，缺的是职责/模型/档级/工具这些可指挥的信息。

    差异点：
      - 要求 agent_id **必须**在该部门 agents 名单里（否则应走 build_plan 新入编）；
      - 要求该部门**尚无**此 id 的 profile（已有档案请走改档流程，本工具不改既有档案）；
      - **不做中文名重名校验**：席位既已存在，名字是既成事实，重名问题另单治理。
    """
    org = load_org(root)
    target = _find_dept(org, dept)

    aid = (agent_id or "").strip()
    listed = [str(x) for x in (target.get("agents") or [])]
    prof_ids = [str(p.get("agent_id")) for p in (target.get("agent_profiles") or [])]
    if aid not in listed:
        raise EnrollError(
            "补档要求 %s 已在 %s 的 agents 名单中（当前名单：%s）。"
            "新席位请改用建计划（不加 --backfill）。" % (aid or "?", dept, ", ".join(listed)))
    if aid in prof_ids:
        raise EnrollError("%s 在 %s 已有档案，本工具不改既有档案（避免覆盖他人成果）" % (aid, dept))

    role = (role_profile or "").strip()
    if not role:
        raise EnrollError("role_profile 不可为空（这是「这个 Agent 到底干嘛」的答案，不能留白）")
    duty_s = (duty or "").strip()
    if len(duty_s) < DUTY_MIN_LEN:
        raise EnrollError("duty 至少 %d 字：必须写清职责边界，否则该席位无法被指挥（当前 %d 字）"
                          % (DUTY_MIN_LEN, len(duty_s)))
    if binding not in BINDING_ALLOW:
        raise EnrollError("llm_binding 不在词表：%r（可选 %s）"
                          % (binding, ", ".join(sorted(BINDING_ALLOW))))
    if grade not in GRADE_ALLOW:
        raise EnrollError("grade 不在词表：%r（可选 %s）" % (grade, ", ".join(sorted(GRADE_ALLOW))))
    if not (ticket or "").strip() or not _TICKET_RE.match(ticket.strip()):
        raise EnrollError("工单号必填且须 WO-/K-/LEGION- 前缀（--ticket）")
    if not (signed_by or "").strip():
        raise EnrollError("缺少放行人：补档改组织真源，须老田显式签字（--signed-by）")

    profile = {
        "agent_id": aid,
        "role_profile": role,
        "analysis_type": (analysis_type or "").strip(),
        "charter_detail": duty_s,
        "llm_binding": binding,
        "binding_note": "由 org_enroll 补档（工单 %s / 放行人 %s）"
                        % (ticket.strip(), signed_by.strip()),
        "tools": list(tools or []),
        "tools_note": "补档初始为空，按工单另行授予",
        "memory_grants": [],
        "grade": grade,
        "grade_note": "补档初始档，待 KPI 观察后调整",
        "attributes": {},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "mode": "backfill",
        "dept": dept,
        "dept_name": target.get("name"),
        "cn_name": _cn_of_id(root, aid),
        "agent": profile,
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "writes": ["registry/org.json（仅追加 agent_profiles，**不改 agents 名单**）"],
        "note": "dry-run 补档计划：未写入任何字节；hr 档案已存在，不新建",
    }


def _cn_of_id(root: Path, aid: str) -> str:
    """取席位中文名。

    两遍扫描：**先找 cn_name，找不到才退回 name**。
    （一遍扫描会把「name: 军情司上行受理席」当成中文名返回——履历里 name 是岗位名、
    cn_name 才是人名，顺序不固定，一遍扫必错。）
    """
    f = _hr_dir(root) / aid / "resume.yaml"
    if not f.exists():
        return ""
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    for ln in lines:
        if ln.startswith("cn_name:"):
            v = ln.split(":", 1)[1].strip().strip("'\"")
            if v:
                return v
    for ln in lines:
        if ln.startswith("name:"):
            v = ln.split(":", 1)[1].strip().strip("'\"")
            # 「烽燧·平台巡检哨」这种岗位式 name 不作人名，取 · 前那段
            return v.split("·")[0].strip()
    return ""


def _selfcheck_backfill(root: Path, before: dict, dept: str, aid: str) -> None:
    """补档自检：profile 到位、总数 +1、**名单未变**、其他部门一字未动。"""
    after = load_org(root)
    b = {d.get("dept"): d for d in before.get("departments") or []}
    a = {d.get("dept"): d for d in after.get("departments") or []}
    if b[dept].get("agents") != a[dept].get("agents"):
        raise EnrollError("自检失败：补档不得改动 %s 的 agents 名单" % dept)
    prof_ids = [p.get("agent_id") for p in (a[dept].get("agent_profiles") or [])]
    if aid not in prof_ids:
        raise EnrollError("自检失败：%s 的档案未落到 %s" % (aid, dept))
    n0 = sum(len(d.get("agent_profiles") or []) for d in before.get("departments") or [])
    n1 = sum(len(d.get("agent_profiles") or []) for d in after.get("departments") or [])
    if n1 != n0 + 1:
        raise EnrollError("自检失败：档案总数 %d → %d（期望 +1）" % (n0, n1))
    for k, d0 in b.items():
        if k == dept:
            continue
        if json.dumps(d0, sort_keys=True, ensure_ascii=False) != \
           json.dumps(a.get(k), sort_keys=True, ensure_ascii=False):
            raise EnrollError("自检失败：非目标部门 %r 被意外改动" % k)


def apply_backfill_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    """执行补档：备份 → 原子写（只追加 profile）→ 自检；失败自动回滚。"""
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise EnrollError("放行人与计划不一致：plan=%r apply=%r"
                          % (plan.get("signed_by"), signed_by))
    dept = plan["dept"]
    aid = plan["agent"]["agent_id"]

    before = load_org(root)
    target = _find_dept(before, dept)
    if aid not in [str(x) for x in (target.get("agents") or [])]:
        raise EnrollError("执行前复核失败：%s 不在 %s 名单——真源在计划后被改动" % (aid, dept))
    if aid in [str(p.get("agent_id")) for p in (target.get("agent_profiles") or [])]:
        raise EnrollError("执行前复核失败：%s 已有档案" % aid)

    bdir = root / "registry" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = _unique_backup_path(bdir)
    shutil.copy2(_org_path(root), backup)

    org = copy.deepcopy(before)
    t2 = _find_dept(org, dept)
    t2["agent_profiles"] = (t2.get("agent_profiles") or []) + [plan["agent"]]
    tmp = _org_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_org_path(root))

    try:
        _selfcheck_backfill(root, before, dept, aid)
    except EnrollError:
        shutil.copy2(backup, _org_path(root))
        raise

    return {"schema_version": SCHEMA_VERSION, "ok": True, "mode": "backfill",
            "dept": dept, "agent_id": aid, "backup": str(backup),
            "note": "补档已落 org.json 并自检通过（名单未变）；回滚用 --rollback %s" % backup}


def build_rebind_plan(root: Path, *, dept: str, agent_id: str, binding: str = "",
                      ticket: str = "", signed_by: str = "", reason: str = "",
                      grade: str = "", duty: str = "", role_profile: str = "") -> dict:
    """改档计划：只改已有档案的 `llm_binding`（不动职责、不动名单）。

    为什么需要：S38 实测发现编制里写的 `glm/glm-5.3-flash` 实际调用返回 HTTP 400
    （模型名不存在）。**词表只保证拼写合法，不保证能力可用**。改档是常规运维动作，
    不能靠手改真源——手改无备份、无自检、无痕迹。
    """
    org = load_org(root)
    target = _find_dept(org, dept)
    aid = (agent_id or "").strip()
    profs = target.get("agent_profiles") or []
    cur = next((p for p in profs if str(p.get("agent_id")) == aid), None)
    if cur is None:
        raise EnrollError("%s 在 %s 尚无档案，改档无从谈起：请先用 --backfill 补档" % (aid or "?", dept))
    changes: dict[str, str] = {}
    if binding:
        if binding not in BINDING_ALLOW:
            raise EnrollError("llm_binding 不在词表：%r（可选 %s）"
                              % (binding, ", ".join(sorted(BINDING_ALLOW))))
        old_b = str(cur.get("llm_binding") or "")
        if old_b != binding:
            changes["llm_binding"] = binding
    if grade:
        if grade not in GRADE_ALLOW:
            raise EnrollError("grade 不在词表：%r（可选 %s）" % (grade, ", ".join(sorted(GRADE_ALLOW))))
        if str(cur.get("grade") or "") != grade:
            changes["grade"] = grade
    if duty:
        d = duty.strip()
        if len(d) < DUTY_MIN_LEN:
            raise EnrollError("duty 至少 %d 字（当前 %d 字）" % (DUTY_MIN_LEN, len(d)))
        if str(cur.get("charter_detail") or "") != d:
            changes["charter_detail"] = d
    if role_profile:
        r = role_profile.strip()
        if not r:
            raise EnrollError("role_profile 不可为空")
        if str(cur.get("role_profile") or "") != r:
            changes["role_profile"] = r
    if not changes:
        raise EnrollError("%s 的目标字段与当前值相同，无需改档" % aid)
    if not (ticket or "").strip() or not _TICKET_RE.match(ticket.strip()):
        raise EnrollError("工单号必填且须 WO-/K-/LEGION- 前缀（--ticket）")
    if not (signed_by or "").strip():
        raise EnrollError("缺少放行人：改档改组织真源，须老田显式签字（--signed-by）")
    old = str(cur.get("llm_binding") or "")
    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "mode": "rebind",
        "dept": dept,
        "dept_name": target.get("name"),
        "agent_id": aid,
        "cn_name": _cn_of_id(root, aid),
        "from_binding": old,
        "to_binding": changes.get("llm_binding", old),
        "changes": changes,
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "reason": (reason or "").strip(),
        "writes": ["registry/org.json（仅改 %s 的 llm_binding 与 binding_note）" % aid],
        "note": "dry-run 改档计划：未写入任何字节",
    }


def apply_rebind_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    """执行改档：备份 → 只改两字段 → 自检（其余一字未动）→ 失败回滚。"""
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise EnrollError("放行人与计划不一致：plan=%r apply=%r"
                          % (plan.get("signed_by"), signed_by))
    dept = plan["dept"]
    aid = plan["agent_id"]

    before = load_org(root)
    target = _find_dept(before, dept)
    cur = next((p for p in (target.get("agent_profiles") or [])
                if str(p.get("agent_id")) == aid), None)
    if cur is None:
        raise EnrollError("执行前复核失败：%s 无档案" % aid)
    if str(cur.get("llm_binding") or "") != plan["from_binding"]:
        raise EnrollError("执行前复核失败：%s 当前绑定已变（%r → %r），请重新生成计划"
                          % (aid, plan["from_binding"], cur.get("llm_binding")))

    bdir = root / "registry" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = _unique_backup_path(bdir)
    shutil.copy2(_org_path(root), backup)

    org = copy.deepcopy(before)
    t2 = _find_dept(org, dept)
    prof = next(p for p in t2["agent_profiles"] if str(p.get("agent_id")) == aid)
    changes = plan.get("changes") or {"llm_binding": plan["to_binding"]}
    for k, v in changes.items():
        prof[k] = v
    note = "改档 %s（工单 %s / 放行人 %s）" % (
        "、".join("%s→%s" % (k, str(v)[:40]) for k, v in changes.items()),
        plan["ticket"], plan["signed_by"])
    if plan.get("reason"):
        note += "：%s" % plan["reason"]
    prev_note = str(prof.get("binding_note") or "")
    prof["binding_note"] = (prev_note + "；" + note) if prev_note else note

    tmp = _org_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_org_path(root))

    try:
        _selfcheck_rebind(root, before, dept, aid, plan["to_binding"], plan)
    except EnrollError:
        shutil.copy2(backup, _org_path(root))
        raise

    return {"schema_version": SCHEMA_VERSION, "ok": True, "mode": "rebind",
            "dept": dept, "agent_id": aid,
            "from_binding": plan["from_binding"], "to_binding": plan["to_binding"],
            "backup": str(backup),
            "note": "改档已落 org.json 并自检通过；回滚用 --rollback %s" % backup}


def _selfcheck_rebind(root: Path, before: dict, dept: str, aid: str, new_binding: str,
                      plan: dict | None = None) -> None:
    """改档自检：目标 profile 两字段已改、其字段与其他一切未动。"""
    after = load_org(root)
    b = {d.get("dept"): d for d in before.get("departments") or []}
    a = {d.get("dept"): d for d in after.get("departments") or []}
    if list(b.keys()) != list(a.keys()):
        raise EnrollError("自检失败：部门清单变化")
    pb = next(p for p in (b[dept].get("agent_profiles") or [])
              if str(p.get("agent_id")) == aid)
    pa = next((p for p in (a[dept].get("agent_profiles") or [])
               if str(p.get("agent_id")) == aid), None)
    if pa is None:
        raise EnrollError("自检失败：%s 档案丢失" % aid)
    if pa.get("llm_binding") != new_binding:
        raise EnrollError("自检失败：%s 绑定未改为 %r" % (aid, new_binding))
    allowed = set((plan_changes_keys := set((plan.get("changes") or {}).keys()))) | {"binding_note"}
    if pa.get("llm_binding") != plan["to_binding"] and "llm_binding" in allowed:
        raise EnrollError("自检失败：%s 绑定未改为 %r" % (aid, plan["to_binding"]))
    for k, v in pb.items():
        if k in allowed:
            continue
        if pa.get(k) != v:
            raise EnrollError("自检失败：%s 的 %s 被意外改动" % (aid, k))
    # 其他部门与其他席位必须一字未动
    for name in b:
        if name == dept:
            continue
        if json.dumps(b[name], sort_keys=True, ensure_ascii=False) != \
           json.dumps(a.get(name), sort_keys=True, ensure_ascii=False):
            raise EnrollError("自检失败：非目标部门 %r 被改动" % name)
    others_b = [p for p in (b[dept].get("agent_profiles") or [])
                if str(p.get("agent_id")) != aid]
    others_a = [p for p in (a[dept].get("agent_profiles") or [])
                if str(p.get("agent_id")) != aid]
    if json.dumps(others_b, sort_keys=True, ensure_ascii=False) != \
       json.dumps(others_a, sort_keys=True, ensure_ascii=False):
        raise EnrollError("自检失败：同部门其他席位被改动")


LEAD_NOTE_MIN_LEN = 10


def build_set_lead_plan(root: Path, *, dept: str, lead: str, ticket: str = "",
                        signed_by: str = "", cross_note: str = "",
                        lead_duty: str = "") -> dict:
    """主官任命计划：只改部门 `lead`（可连带 `lead_duty`），不动名单、不动档案。

    存在理由：org_audit 把「主官空悬 lead=None」「主官无档案」列为 P1（该营无签字人），
    但编制工具此前只有四条腿——新建 / 补档 / 改档 / 冒烟，**任命主官只能手改真源**：
    无备份、无自检、无痕迹。手改真源正是本项目反复踩过的坑，不能因为「只是改一个字符串」
    就例外。

    两条硬约束（都是拿 P1 的产生原因反推出来的）：
      1. **主官必须有档案**：任命一个无档案者，只是把「主官空悬」换成「主官无档」，
         该营照样没有可信签字人 —— 故硬拒，要求先 --backfill。
      2. **跨部门兼任必须显式说明**：lead 不在本部门 agents 名单时，要求 cross_note
         （≥10 字），并把兼任事实写进部门 `lead_appointed`。否则 audit 的
         LEAD_OUT_OF_DEPT 就只能报「指挥链断在真源」——事实是断了没登记，不是不该兼。
    """
    org = load_org(root)
    target = _find_dept(org, dept)
    lid = (lead or "").strip()
    if not lid:
        raise EnrollError("主官不可为空（--lead）")
    if not _ID_RE.match(lid):
        raise EnrollError("主官 id 格式非法：%r（须大写字母开头、2-16 位大写字母数字）" % lid)

    idx = _agent_id_index(org, root)
    where = idx.get(lid)
    if where is None:
        raise EnrollError("未知席位 %r：org 名单、档案与 hr 目录均无此 id" % lid)
    # 档案可以在三处：部门档案表 / 顶层 chief / team_*.json（S41 统一口径）。
    # 只扫部门表会把 A0A（档案在 registry/team_chief.json）误判成"无档案"而拒任命。
    profiled = set(profile_index(root))
    if lid not in profiled:
        raise EnrollError(
            "%s 尚无档案（当前仅见于 %s）。任命后该营会从「主官空悬」变成「主官无档」，"
            "签字人依旧不成立 —— 请先用 --backfill 补档再任命" % (lid, where))

    members = [str(a) for a in (target.get("agents") or [])]
    cross = lid not in members
    note_txt = (cross_note or "").strip()
    if cross:
        if len(note_txt) < LEAD_NOTE_MIN_LEN:
            raise EnrollError(
                "%s 不在 %s 的名单内（属跨部门兼任），须用 --cross-note 显式说明兼任范围"
                "（≥%d 字）：本条说明会写入部门 lead_appointed，"
                "否则指挥链在真源里是断的" % (lid, dept, LEAD_NOTE_MIN_LEN))

    old_lead = str(target.get("lead") or "")
    duty = (lead_duty or "").strip()
    if duty and len(duty) < DUTY_MIN_LEN:
        raise EnrollError("lead_duty 至少 %d 字（当前 %d 字）" % (DUTY_MIN_LEN, len(duty)))
    old_duty = str(target.get("lead_duty") or "")
    # 补登记兼任：主官没变、但真源里没有 lead_appointed 留痕（A0/A0A 就是这种历史状态——
    # lead 早就填了，兼任说明一个字没有，审计只能报「指挥链跨部门」）。
    # 这不改指挥关系，只补事实凭证，所以允许在「主官不变」时执行一次；已有留痕则幂等拒绝。
    ap_cur = target.get("lead_appointed") or {}
    already_noted = bool(ap_cur.get("cross_note")) and str(ap_cur.get("to") or "") == lid
    announce = bool(cross and note_txt and not already_noted)
    if old_lead == lid and (not duty or duty == old_duty) and not announce:
        raise EnrollError("%s 的主官已是 %s%s，无需任命"
                          % (dept, lid, "（兼任说明已登记）" if already_noted else ""))
    if not (ticket or "").strip() or not _TICKET_RE.match(ticket.strip()):
        raise EnrollError("工单号必填且须 WO-/K-/LEGION- 前缀（--ticket）")
    if not (signed_by or "").strip():
        raise EnrollError("缺少放行人：任命主官改组织真源，须老田显式签字（--signed-by）")

    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "mode": "set_lead",
        "action": "announce_cross" if (announce and old_lead == lid) else "appoint",
        "dept": dept,
        "dept_name": target.get("name"),
        "from_lead": old_lead,
        "to_lead": lid,
        "cn_name": _cn_of_id(root, lid),
        "cross_dept": cross,
        "cross_note": note_txt,
        "from_lead_duty": old_duty,
        "lead_duty": duty,
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "writes": ["registry/org.json（仅 %s 的 lead%s 与 lead_appointed）"
                   % (dept, "、lead_duty" if duty else "")],
        "note": "dry-run 任命计划：未写入任何字节",
    }


def apply_set_lead_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    """执行任命：复核 → 备份 → 只改 lead/lead_duty + 留痕 → 自检 → 失败回滚。"""
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise EnrollError("放行人与计划不一致：plan=%r apply=%r"
                          % (plan.get("signed_by"), signed_by))
    dept = plan["dept"]
    before = load_org(root)
    target = _find_dept(before, dept)
    cur_lead = str(target.get("lead") or "")
    if cur_lead != plan["from_lead"]:
        raise EnrollError("执行前复核失败：%s 当前主官已变（%r → %r），请重新生成计划"
                          % (dept, plan["from_lead"], cur_lead))

    bdir = root / "registry" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = _unique_backup_path(bdir)
    shutil.copy2(_org_path(root), backup)

    org = copy.deepcopy(before)
    t2 = _find_dept(org, dept)
    t2["lead"] = plan["to_lead"]
    if plan.get("lead_duty"):
        t2["lead_duty"] = plan["lead_duty"]
    t2["lead_appointed"] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "from": plan["from_lead"] or None,
        "to": plan["to_lead"],
        "cross_dept": bool(plan.get("cross_dept")),
        "cross_note": plan.get("cross_note") or "",
        "ticket": plan["ticket"],
        "signed_by": plan["signed_by"],
    }

    tmp = _org_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_org_path(root))
    try:
        _selfcheck_set_lead(root, before, dept, plan)
    except EnrollError:
        shutil.copy2(backup, _org_path(root))
        raise

    return {"schema_version": SCHEMA_VERSION, "ok": True, "mode": "set_lead",
            "dept": dept, "from_lead": plan["from_lead"], "to_lead": plan["to_lead"],
            "cross_dept": bool(plan.get("cross_dept")), "backup": str(backup),
            "note": "任命已落 org.json 并自检通过（名单与档案未动）；回滚用 --rollback %s"
                    % backup}


def _selfcheck_set_lead(root: Path, before: dict, dept: str, plan: dict) -> None:
    """任命自检：lead 已改、留痕齐全，其余（名单/档案/他部门）一字未动。"""
    after = load_org(root)
    b = {d.get("dept"): d for d in before.get("departments") or []}
    a = {d.get("dept"): d for d in after.get("departments") or []}
    if list(b.keys()) != list(a.keys()):
        raise EnrollError("自检失败：部门清单变化")
    ad = a.get(dept)
    if ad is None:
        raise EnrollError("自检失败：目标部门丢失")
    if str(ad.get("lead") or "") != plan["to_lead"]:
        raise EnrollError("自检失败：主官未改为 %r" % plan["to_lead"])
    if plan.get("lead_duty") and str(ad.get("lead_duty") or "") != plan["lead_duty"]:
        raise EnrollError("自检失败：lead_duty 未改为计划值")
    ap = ad.get("lead_appointed") or {}
    if str(ap.get("to") or "") != plan["to_lead"]:
        raise EnrollError("自检失败：lead_appointed.to 与主官不一致")
    if str(ap.get("ticket") or "") != plan["ticket"]:
        raise EnrollError("自检失败：lead_appointed 未记工单")
    allowed = {"lead", "lead_duty", "lead_appointed"}

    def strip(d):
        return {k: v for k, v in (d or {}).items() if k not in allowed}

    if json.dumps(strip(b[dept]), sort_keys=True, ensure_ascii=False) != \
       json.dumps(strip(ad), sort_keys=True, ensure_ascii=False):
        raise EnrollError("自检失败：本部门除主官字段外被改动（名单或档案被动）")
    for name in b:
        if name == dept:
            continue
        if json.dumps(b[name], sort_keys=True, ensure_ascii=False) != \
           json.dumps(a.get(name), sort_keys=True, ensure_ascii=False):
            raise EnrollError("自检失败：非目标部门 %r 被改动" % name)


def probe_binding(binding: str, *, timeout_s: int = 40) -> dict:
    """连通性冒烟：验证一个绑定**真的能调通**，而不只是拼写合法。

    存在理由：S38 实测 `glm/glm-5.3-flash` 词表合法却返回 HTTP 400（模型名不存在）。
    词表只能保证拼写，能力要靠实调。编制工具应能在补档/改档后顺手验一次。

    `binding == "none"`（规则执行体）不算失败，返回 kind=rule_only。
    """
    out = {"binding": binding, "ok": False, "kind": "", "latency_ms": 0, "error": ""}
    if binding == "none":
        out.update(ok=True, kind="rule_only",
                   error="规则执行体，无 LLM 依赖（无需连通性）")
        return out
    t0 = time.time()
    try:
        plat = Path(__file__).resolve().parents[1]
        if str(plat) not in sys.path:
            sys.path.insert(0, str(plat))
        from gateway import llm_gateway
        env = llm_gateway.chat(binding=binding, system="连通性冒烟，只回一个词：OK",
                              user="OK")
        out["latency_ms"] = int((time.time() - t0) * 1000)
        if env.get("ok"):
            out.update(ok=True, kind="llm",
                       text=str((env.get("data") or {}).get("text", ""))[:40])
        else:
            err = env.get("error") or {}
            out["error"] = "%s: %s" % (err.get("code", "?"), str(err.get("message", ""))[:160])
    except Exception as e:                                  # noqa: BLE001
        out["latency_ms"] = int((time.time() - t0) * 1000)
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def _selfcheck(root: Path, before: dict, dept: str, aid: str) -> None:
    """写后自检：新员在目标部门、总数 +1、**其他部门一字未动**。不符即抛错触发回滚。"""
    after = load_org(root)
    deps_after = {d.get("dept"): d for d in after.get("departments") or []}
    deps_before = {d.get("dept"): d for d in before.get("departments") or []}

    ids_after = [p.get("agent_id") for p in (deps_after.get(dept, {}).get("agent_profiles") or [])]
    if aid not in ids_after:
        raise EnrollError("自检失败：%s 未出现在 %s 部门" % (aid, dept))
    n_before = sum(len(d.get("agent_profiles") or []) for d in before.get("departments") or [])
    n_after = sum(len(d.get("agent_profiles") or []) for d in after.get("departments") or [])
    if n_after != n_before + 1:
        raise EnrollError("自检失败：agent 总数 %d → %d（期望 +1）" % (n_before, n_after))
    for name, d0 in deps_before.items():
        if name == dept:
            continue
        if json.dumps(d0, sort_keys=True, ensure_ascii=False) != \
           json.dumps(deps_after.get(name), sort_keys=True, ensure_ascii=False):
            raise EnrollError("自检失败：非目标部门 %r 被意外改动" % name)
    if list(deps_before.keys()) != list(deps_after.keys()):
        raise EnrollError("自检失败：部门清单或顺序变化")


def apply_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    """执行入编。先备份，再原子写，写后自检；自检失败自动回滚。"""
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise EnrollError("放行人与计划不一致：plan=%r apply=%r（重新 build_plan 后勿改签字人）"
                          % (plan.get("signed_by"), signed_by))

    dept = plan["dept"]
    aid = plan["agent"]["agent_id"]

    # TOCTOU 防护：计划生成后真源可能被别处改动，执行前复核 id 唯一性
    before = load_org(root)
    id_idx = _agent_id_index(before, root)
    if aid in id_idx:
        raise EnrollError("执行前复核失败：%s 已被占用（%s）——真源在计划生成后被改动，"
                          "请重新生成计划" % (aid, id_idx[aid]))

    # 1) 备份
    bdir = root / "registry" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = _unique_backup_path(bdir)
    shutil.copy2(_org_path(root), backup)

    # 2) 改内存并原子写
    #    注：不另做「hr 档案已存在」前置判断——hr 目录本身就是编制的一部分，
    #    上面的 id 复核（_agent_id_index 扫 hr）已覆盖此情形，再加一层即死代码。
    hr_path = _hr_dir(root) / aid / "resume.yaml"
    org = copy.deepcopy(before)
    target = _find_dept(org, dept)
    # 注意：agent_profiles 可能是显式 null（非缺失），setdefault 对 None 不生效，
    # 直接 .append 会 AttributeError——comm 部真实踩到。
    target["agent_profiles"] = (target.get("agent_profiles") or []) + [plan["agent"]]
    if isinstance(target.get("agents"), list):
        target["agents"].append(aid)
    tmp = _org_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_org_path(root))

    # 3) 写后自检，失败即回滚
    try:
        _selfcheck(root, before, dept, aid)
    except EnrollError:
        shutil.copy2(backup, _org_path(root))
        raise

    # 4) hr 骨架（org 已确认落地后才建，失败不回滚 org——档案缺失不影响运行，
    #    但会在返回里如实报出，交人工补）
    hr_created = _write_hr_skeleton(hr_path, plan)

    return {"schema_version": SCHEMA_VERSION, "ok": True, "dept": dept,
            "agent_id": aid, "backup": str(backup),
            "hr_resume": str(hr_path) if hr_created else None,
            "hr_written": hr_created,
            "rolled_back": False,
            "note": "入编已落 org.json 并自检通过；回滚用 --rollback %s" % backup}


def _write_hr_skeleton(path: Path, plan: dict) -> bool:
    a = plan["agent"]
    body = """agent_id: {aid}
name: {cn}
cn_name: {cn}
kind: staff
identity:
  origin: org_enroll 入编（工单 {tk} / 放行人 {sg} / {ts}）
  model: {bind}
  born: {day}
education:
  - program: 入编骨架（由 org_enroll 生成，待人审补充）
    source: registry/org.json
    graduated: {ym}
experience:
  - org: 数字军团 · {dept}
    role: {role}
    span: {ym}~
    kpi: 入编初始，尚无考核记录
skills: []
self_statement: （入编骨架——待本人与田丰补充）
""".format(aid=a["agent_id"], cn=plan.get("cn_name", ""), tk=plan["ticket"],
           sg=plan["signed_by"], ts=time.strftime("%Y-%m-%d %H:%M"),
           bind=a["llm_binding"], day=time.strftime("%Y-%m-%d"),
           ym=time.strftime("%Y-%m"), dept=plan["dept"], role=a["role_profile"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return True
    except OSError:
        return False


def rollback(root: Path, backup: str, *, signed_by: str) -> dict:
    if not (signed_by or "").strip():
        raise EnrollError("回滚同样须放行人（--signed-by）")
    b = Path(backup)
    if not b.is_absolute():
        b = root / backup
    if not b.exists():
        raise EnrollError("备份不存在：%s" % b)
    try:
        json.loads(b.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise EnrollError("备份不可解析，拒绝覆盖真源：%s" % e)
    shutil.copy2(b, _org_path(root))
    return {"ok": True, "restored_from": str(b), "signed_by": signed_by.strip(),
            "note": "已用备份还原 org.json；hr 档案不会自动删除（避免误删履历）"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数字军团 Agent 编制工具（默认 dry-run）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--dept")
    ap.add_argument("--id", dest="agent_id")
    ap.add_argument("--name", dest="cn_name")
    ap.add_argument("--role")
    ap.add_argument("--duty")
    ap.add_argument("--binding", default="ollama/qwen3.5:9b")
    ap.add_argument("--grade", default="", help="档级 E1/E2/E3（新建/补档默认 E2；改档不传则不改）")
    ap.add_argument("--analysis-type", default="")
    ap.add_argument("--ticket", default="")
    ap.add_argument("--signed-by", default="")
    ap.add_argument("--apply", action="store_true", help="真写入（默认只打印计划）")
    ap.add_argument("--rebind", action="store_true",
                    help="改档模式：只改已有档案的 llm_binding（不动职责与名单）")
    ap.add_argument("--probe", metavar="BINDING",
                    help="连通性冒烟：实测该 binding 能否调通（只读，不写任何真源）")
    ap.add_argument("--reason", default="", help="改档原因，写入 binding_note")
    ap.add_argument("--backfill", action="store_true",
                    help="补档模式：给已在名单但无档案的席位补档案（不动 agents 名单）")
    ap.add_argument("--set-lead", action="store_true",
                    help="主官任命模式：只改部门 lead（可带 lead_duty），不动名单与档案")
    ap.add_argument("--lead", default="", help="任命模式：新主官 agent_id（须已有档案）")
    ap.add_argument("--lead-duty", default="", help="任命模式：主官职责（≥10 字；不传则不动）")
    ap.add_argument("--cross-note", default="",
                    help="任命模式：跨部门兼任说明（≥10 字，写入部门 lead_appointed）")
    ap.add_argument("--rollback", help="用指定备份还原 org.json")
    args = ap.parse_args(argv)

    root = Path(args.root)
    try:
        if args.probe:
            res = probe_binding(args.probe)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0 if res["ok"] else 1
        if args.rollback:
            print(json.dumps(rollback(root, args.rollback, signed_by=args.signed_by),
                             ensure_ascii=False, indent=1))
            return 0
        if args.set_lead:
            need = ("dept", "lead")
        elif args.rebind:
            need = ("dept", "agent_id")
        elif args.backfill:
            need = ("dept", "agent_id", "role", "duty")
        else:
            need = ("dept", "agent_id", "cn_name", "role", "duty")
        missing = [k for k in need if not getattr(args, k)]
        if missing:
            print("缺少必填参数：%s" % ", ".join("--" + m for m in missing), file=sys.stderr)
            return 2
        if args.set_lead:
            plan = build_set_lead_plan(root, dept=args.dept, lead=args.lead,
                                       ticket=args.ticket, signed_by=args.signed_by,
                                       cross_note=args.cross_note,
                                       lead_duty=args.lead_duty)
            if not args.apply:
                print(json.dumps(plan, ensure_ascii=False, indent=1))
                print("\n【dry-run】以上为任命计划，未写入任何字节。确认无误加 --apply 执行。",
                      file=sys.stderr)
                return 0
            res = apply_set_lead_plan(root, plan, signed_by=args.signed_by)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0
        if args.rebind:
            plan = build_rebind_plan(root, dept=args.dept, agent_id=args.agent_id,
                                     binding=args.binding or "", ticket=args.ticket,
                                     signed_by=args.signed_by, reason=args.reason,
                                     grade=args.grade or "", duty=args.duty or "",
                                     role_profile=args.role or "")
            if not args.apply:
                print(json.dumps(plan, ensure_ascii=False, indent=1))
                print("\n【dry-run】以上为改档计划，未写入任何字节。确认无误加 --apply 执行。",
                      file=sys.stderr)
                return 0
            res = apply_rebind_plan(root, plan, signed_by=args.signed_by)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0
        if args.backfill:
            plan = build_backfill_plan(root, dept=args.dept, agent_id=args.agent_id,
                                       role_profile=args.role, duty=args.duty,
                                       binding=args.binding, grade=args.grade or "E2",
                                       ticket=args.ticket, signed_by=args.signed_by,
                                       analysis_type=args.analysis_type)
        else:
            plan = build_plan(root, dept=args.dept, agent_id=args.agent_id, cn_name=args.cn_name,
                              role_profile=args.role, duty=args.duty, binding=args.binding,
                              grade=args.grade, ticket=args.ticket, signed_by=args.signed_by,
                              analysis_type=args.analysis_type)
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            print("\n【dry-run】以上为计划，未写入任何字节。确认无误加 --apply 执行。",
                  file=sys.stderr)
            return 0
        res = (apply_backfill_plan if args.backfill else apply_plan)(
            root, plan, signed_by=args.signed_by)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    except EnrollError as e:
        print("编制校验失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
