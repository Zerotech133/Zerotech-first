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
    "glm/glm-4.6",
    "openai/gpt-6.0",
})
GRADE_ALLOW = frozenset({"E1", "E2", "E3"})
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
_TICKET_RE = re.compile(r"^(WO-|K-|LEGION-|WOLEGION-)", re.I)
DUTY_MIN_LEN = 10


class EnrollError(ValueError):
    """编制校验失败（含凭据缺失、重名、词表越界）——一律 fail-visible，不静默降级。"""


def _org_path(root: Path) -> Path:
    return root / "registry" / "org.json"


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
    backup = bdir / ("org-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(_org_path(root), backup)

    # 2) 改内存并原子写
    #    注：不另做「hr 档案已存在」前置判断——hr 目录本身就是编制的一部分，
    #    上面的 id 复核（_agent_id_index 扫 hr）已覆盖此情形，再加一层即死代码。
    hr_path = _hr_dir(root) / aid / "resume.yaml"
    org = copy.deepcopy(before)
    target = _find_dept(org, dept)
    target.setdefault("agent_profiles", []).append(plan["agent"])
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
    ap.add_argument("--grade", default="E2")
    ap.add_argument("--analysis-type", default="")
    ap.add_argument("--ticket", default="")
    ap.add_argument("--signed-by", default="")
    ap.add_argument("--apply", action="store_true", help="真写入（默认只打印计划）")
    ap.add_argument("--rollback", help="用指定备份还原 org.json")
    args = ap.parse_args(argv)

    root = Path(args.root)
    try:
        if args.rollback:
            print(json.dumps(rollback(root, args.rollback, signed_by=args.signed_by),
                             ensure_ascii=False, indent=1))
            return 0
        missing = [k for k in ("dept", "agent_id", "cn_name", "role", "duty") if not getattr(args, k)]
        if missing:
            print("缺少必填参数：%s" % ", ".join("--" + m for m in missing), file=sys.stderr)
            return 2
        plan = build_plan(root, dept=args.dept, agent_id=args.agent_id, cn_name=args.cn_name,
                          role_profile=args.role, duty=args.duty, binding=args.binding,
                          grade=args.grade, ticket=args.ticket, signed_by=args.signed_by,
                          analysis_type=args.analysis_type)
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            print("\n【dry-run】以上为计划，未写入任何字节。确认无误加 --apply 执行。",
                  file=sys.stderr)
            return 0
        res = apply_plan(root, plan, signed_by=args.signed_by)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    except EnrollError as e:
        print("编制校验失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
