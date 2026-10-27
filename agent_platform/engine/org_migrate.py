# -*- coding: utf-8 -*-
"""档案迁入部门档案表（WO-LEGION-S43）——把散在 chief 块 / team_*.json 的档案落到标准位。

背景：组织里存在三种档案位置（详见 engine/org_profiles.py）：
  1. `departments[].agent_profiles`（标准位，org_enroll 写这里）
  2. `org.json` 顶层 `chief`（A0 田丰，历史结构，monthly_report/snapshot 直接读它）
  3. `registry/team_*.json`（A0A 董允、DENGZHI1 邓芝）

前两轮已让**读**的一方认全三处；本轮做**写**的一方：把 2/3 的档案迁进 1，
让「一个席位一份档案」成立。但 `chief` 块仍被 monthly_report / snapshot 直接读取，
**不能删**——迁入后原位置保留并打 `_superseded_by` 标记，读取方零改动。

硬约束（与其余编制工具一致）：
  - 默认 dry-run；真写需工单 + 放行人
  - 目标部门已有该 id 档案 → 拒绝（不覆盖、不合并，避免悄悄改职责）
  - 职责（charter_detail）必须来自原档案（charter_detail/duty）或 `--charter` 显式给出，
    **工具不生成职责**（编制红线：不造数据）
  - 备份 → 原子写 → 自检（目标档案字段一致、名单只增了该 id、其他部门一字未动）→ 失败回滚
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

RULES_VERSION = "1.0"
_TICKET_RE = re.compile(r"^(WO-|K-|LEGION-|WOLEGION-)", re.I)
DUTY_MIN_LEN = 10
BINDING_ALLOW = frozenset(
    {
        "ollama/qwen3.5:9b",
        "ollama/qwen3.6:35b-a3b",
        "glm/glm-5.3",
        "glm/glm-5.3-flash",
        "glm/glm-4.6",
        "openai/gpt-6.0",
        "none",
    }
)


class MigrateError(ValueError):
    """迁移校验失败——fail-visible。"""


def _org_path(root: Path) -> Path:
    return root / "registry" / "org.json"


def _backup(root: Path, tag: str) -> Path:
    bdir = root / "registry" / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    base = "migrate-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), tag)
    p = bdir / base
    n = 1
    while p.exists():
        p = bdir / ("%s-%d" % (base, n))
        n += 1
    return p


def _find_source(root: Path, aid: str, source: str) -> tuple:
    """返回 (档案 dict, 原位置描述, 原文件相对路径 or None)。"""
    if source == "chief":
        org = json.loads(_org_path(root).read_text(encoding="utf-8"))
        c = org.get("chief") or {}
        if str(c.get("agent_id") or "") != aid:
            raise MigrateError(
                "chief 块里的席位是 %r，不是 %r" % (c.get("agent_id"), aid)
            )
        return dict(c), "org.json 顶层 chief", None
    if source.startswith("team:"):
        rel = source.split(":", 1)[1]
        p = root / rel
        if not p.exists():
            raise MigrateError("团队文件不存在：%s" % rel)
        t = json.loads(p.read_text(encoding="utf-8"))
        for a in t.get("agents") or []:
            if str(a.get("agent_id")) == aid:
                return dict(a), rel, rel
        raise MigrateError("%s 里没有 %s" % (rel, aid))
    raise MigrateError("未知 --source：%r（可选 chief / team:<相对路径>）" % source)


def _to_profile(raw: dict, *, binding: str, charter: str, grade: str) -> dict:
    duty = (raw.get("charter_detail") or raw.get("duty") or "").strip()
    if not duty:
        duty = (charter or "").strip()
    if len(duty) < DUTY_MIN_LEN:
        raise MigrateError(
            "职责（charter_detail/duty）不足 %d 字且未用 --charter 显式给出："
            "编制红线不允许工具自造职责" % DUTY_MIN_LEN
        )
    prof = {
        "agent_id": str(raw.get("agent_id")),
        "role_profile": str(raw.get("role_profile") or ""),
        "analysis_type": str(raw.get("analysis_type") or ""),
        "charter_detail": duty,
        "llm_binding": (binding or str(raw.get("llm_binding") or "")),
        "binding_note": str(raw.get("binding_note") or ""),
        "tools": raw.get("tools") or [],
        "memory_grants": raw.get("memory_grants") or [],
        "grade": (grade or str(raw.get("grade") or "")),
        "attributes": raw.get("attributes") or {},
    }
    name = raw.get("name") or (raw.get("persona") or {}).get("name") or ""
    if name:
        prof["persona"] = {"name": str(name)}
    return prof


def build_migrate_plan(
    root: Path,
    *,
    agent_id: str,
    dept: str,
    source: str,
    binding: str = "",
    charter: str = "",
    grade: str = "",
    ticket: str = "",
    signed_by: str = "",
) -> dict:
    aid = (agent_id or "").strip()
    org_p = _org_path(root)
    try:
        org = json.loads(org_p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise MigrateError("org.json 不可解析：%s" % e)
    target = next(
        (d for d in org.get("departments") or [] if d.get("dept") == dept), None
    )
    if target is None:
        raise MigrateError("未知部门 %r" % dept)
    if any(str(p.get("agent_id")) == aid for p in (target.get("agent_profiles") or [])):
        raise MigrateError(
            "%s 在 %s 已有档案，迁移会覆盖既有职责——工具拒绝合并" % (aid, dept)
        )
    raw, where, rel = _find_source(root, aid, source)
    if binding and binding not in BINDING_ALLOW:
        raise MigrateError("llm_binding 不在词表：%r" % binding)
    prof = _to_profile(raw, binding=binding, charter=charter, grade=grade)
    if not prof["llm_binding"]:
        raise MigrateError(
            "%s 原档案未声明 llm_binding，请用 --binding 显式给出"
            "（不知道绑什么就填 none，不要留给默认档替它假定能力）" % aid
        )
    if not (ticket or "").strip() or not _TICKET_RE.match(ticket.strip()):
        raise MigrateError("工单号必填且须 WO-/K-/LEGION- 前缀（--ticket）")
    if not (signed_by or "").strip():
        raise MigrateError("缺少放行人（--signed-by）")

    add_to_agents = aid not in [str(a) for a in (target.get("agents") or [])]
    return {
        "rules_version": RULES_VERSION,
        "mode": "migrate_profile",
        "agent_id": aid,
        "dept": dept,
        "dept_name": target.get("name"),
        "from_source": where,
        "profile": prof,
        "add_to_agents": add_to_agents,
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "writes": [
            "registry/org.json（%s 档案 + 名单%s）"
            % (dept, "+1" if add_to_agents else "不变")
        ],
        "note": "dry-run 迁移计划：未写入任何字节",
    }


def apply_migrate_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise MigrateError("放行人与计划不一致")
    aid, dept = plan["agent_id"], plan["dept"]
    before = json.loads(_org_path(root).read_text(encoding="utf-8"))
    backup = _backup(root, aid)
    shutil.copy2(_org_path(root), backup)
    # 来源是 team 文件时也要备份：apply 会先给它打 _superseded_by 标记，
    # 只回滚 org.json 会留下「标记指向一个没发生的迁移」的脏状态（S43 实测踩到）。
    src_backup = None
    if plan["from_source"] != "org.json 顶层 chief":
        src_path = root / plan["from_source"]
        if src_path.exists():
            src_backup = _backup(root, aid + "-src")
            shutil.copy2(src_path, src_backup)

    org = copy.deepcopy(before)
    target = next(d for d in org["departments"] if d.get("dept") == dept)
    target["agent_profiles"] = (target.get("agent_profiles") or []) + [plan["profile"]]
    if plan.get("add_to_agents"):
        target["agents"] = (target.get("agents") or []) + [aid]
    # 原位置打标记（不删：chief 块仍被 monthly_report/snapshot 直接读）
    mark = {
        "_superseded_by": "departments.%s.agent_profiles" % dept,
        "_superseded_note": "S43 已迁入部门档案表（工单 %s / 放行人 %s）；"
        "此副本仅为兼容既有读取方，勿在此维护" % (plan["ticket"], plan["signed_by"]),
    }
    if plan["from_source"] == "org.json 顶层 chief":
        org["chief"] = dict(org.get("chief") or {}, **mark)
    else:
        rel = plan["from_source"]
        tp = root / rel
        t = json.loads(tp.read_text(encoding="utf-8"))
        for a in t.get("agents") or []:
            if str(a.get("agent_id")) == aid:
                a.update(mark)
        tmp_t = tp.with_suffix(".json.tmp")
        tmp_t.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_t.replace(tp)

    tmp = _org_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_org_path(root))
    try:
        _selfcheck(root, before, dept, aid, plan)
    except MigrateError:
        shutil.copy2(backup, _org_path(root))
        if src_backup is not None:
            shutil.copy2(src_backup, root / plan["from_source"])
        raise
    return {
        "rules_version": RULES_VERSION,
        "ok": True,
        "mode": "migrate_profile",
        "agent_id": aid,
        "dept": dept,
        "from_source": plan["from_source"],
        "backup": str(backup),
        "note": "档案已迁入部门档案表并自检通过；原位置保留并标记 _superseded_by",
    }


def _selfcheck(root: Path, before: dict, dept: str, aid: str, plan: dict) -> None:
    after = json.loads(_org_path(root).read_text(encoding="utf-8"))
    b = {d.get("dept"): d for d in before.get("departments") or []}
    a = {d.get("dept"): d for d in after.get("departments") or []}
    if list(b.keys()) != list(a.keys()):
        raise MigrateError("自检失败：部门清单变化")
    ad = a.get(dept)
    got = next(
        (p for p in (ad.get("agent_profiles") or []) if str(p.get("agent_id")) == aid),
        None,
    )
    if got is None:
        raise MigrateError("自检失败：%s 档案未落到 %s" % (aid, dept))
    if json.dumps(got, sort_keys=True, ensure_ascii=False) != json.dumps(
        plan["profile"], sort_keys=True, ensure_ascii=False
    ):
        raise MigrateError("自检失败：落盘档案与计划不一致")
    if plan.get("add_to_agents") and aid not in [
        str(x) for x in (ad.get("agents") or [])
    ]:
        raise MigrateError("自检失败：%s 未加入 %s 名单" % (aid, dept))
    # 其他部门一字未动
    for name in b:
        if name == dept:
            continue
        if json.dumps(b[name], sort_keys=True, ensure_ascii=False) != json.dumps(
            a.get(name), sort_keys=True, ensure_ascii=False
        ):
            raise MigrateError("自检失败：非目标部门 %r 被改动" % name)
    # 本部门其他档案与名单（除新增项）未动
    others_b = [
        p
        for p in (b[dept].get("agent_profiles") or [])
        if str(p.get("agent_id")) != aid
    ]
    others_a = [
        p for p in (ad.get("agent_profiles") or []) if str(p.get("agent_id")) != aid
    ]
    if json.dumps(others_b, sort_keys=True, ensure_ascii=False) != json.dumps(
        others_a, sort_keys=True, ensure_ascii=False
    ):
        raise MigrateError("自检失败：同部门其他档案被改动")
    agents_b = [str(x) for x in (b[dept].get("agents") or [])]
    agents_now = [str(x) for x in (ad.get("agents") or [])]
    # 只有「本次新增」才该多出该 id；原本就在名单里的（如邓芝本就在 comm 名单）
    # 不能反被过滤掉——旧实现无条件过滤，导致这类席位每次迁移都误报（S43 实测）。
    if plan.get("add_to_agents"):
        agents_now = [x for x in agents_now if x != aid]
    if agents_b != agents_now:
        raise MigrateError("自检失败：同部门名单被改动（除新增项）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="档案迁入部门档案表（默认 dry-run）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--id", dest="agent_id")
    ap.add_argument("--dept")
    ap.add_argument(
        "--source", required=False, help="chief 或 team:registry/team_x.json"
    )
    ap.add_argument("--binding", default="", help="覆盖绑定；原档案无绑定时必须给")
    ap.add_argument("--charter", default="", help="职责；原档案无 duty 时必须给")
    ap.add_argument("--grade", default="")
    ap.add_argument("--ticket", default="")
    ap.add_argument("--signed-by", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        if not (args.agent_id and args.dept and args.source):
            print("需要 --id --dept --source", file=sys.stderr)
            return 2
        plan = build_migrate_plan(
            root,
            agent_id=args.agent_id,
            dept=args.dept,
            source=args.source,
            binding=args.binding,
            charter=args.charter,
            grade=args.grade,
            ticket=args.ticket,
            signed_by=args.signed_by,
        )
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            print(
                "\n【dry-run】以上为迁移计划，未写入任何字节。确认无误加 --apply。",
                file=sys.stderr,
            )
            return 0
        print(
            json.dumps(
                apply_migrate_plan(root, plan, signed_by=args.signed_by),
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    except MigrateError as e:
        print("迁移校验失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
