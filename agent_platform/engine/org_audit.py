# -*- coding: utf-8 -*-
"""编制完整性审计（WO-LEGION-S34）—— 只读，回答「这个军团到底有多少人、谁在干什么」。

为什么要有它：2026-10-21 盘面核实发现 org.json 里 `agents`（名单）与 `agent_profiles`
（档案）严重脱节——**63 个席位只有 29 个有档案，34 个「有名无档」**；17 个主官里 7 个
没有档案，2 个空悬，1 个主官连本部门名单都不在。这意味着沙盘上很多席位**只知道名字，
不知道职责/模型/档级/工具**——「谁在干嘛」根本答不出来。

本模块只读，不写任何真源。它产出的是**缺口清单**，不是替人填档案：
职责（charter_detail）必须由田丰归口、老田裁定，工具不得造。

用法：
    python engine/org_audit.py            # 人读摘要
    python engine/org_audit.py --json     # 机器读（供 /api/org/audit）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0"
RULES_VERSION = "1.0"

# 严重度口径（写死在此，避免各处解释不一）
#   P1 无法指挥：主官空悬 / 主官无档 / 整部门零档案
#   P2 指挥易错：主官跨部门引用 / 中文名重复 / 部分席位缺档
#   P3 存量观察：编外人员、名单与档案数量不符但已有主官
SEV_P1, SEV_P2, SEV_P3 = "P1", "P2", "P3"


def _load_org(root: Path) -> dict:
    p = root / "registry" / "org.json"
    if not p.exists():
        raise FileNotFoundError("org.json not found: %s" % p)
    return json.loads(p.read_text(encoding="utf-8"))


def _hr_names(root: Path) -> dict:
    """hr/<ID>/resume.yaml → 中文名。org 里没有 cn_name，名册以 hr 为准。"""
    out: dict[str, str] = {}
    hr = root / "hr"
    if not hr.exists():
        return out
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
            out[d.name] = cn
    return out


def audit(root: Path) -> dict:
    """只读审计。返回结构化缺口清单（不写任何字节）。"""
    org = _load_org(root)
    names = _hr_names(root)
    depts = org.get("departments") or []

    findings: list[dict] = []
    rows: list[dict] = []
    listed_total = profiled_total = ghost_total = 0
    in_org: set[str] = set()

    for d in depts:
        dept = str(d.get("dept"))
        lead = d.get("lead")
        ids = [str(x) for x in (d.get("agents") or [])]
        profs = [str(p.get("agent_id")) for p in (d.get("agent_profiles") or [])]
        ghosts = [i for i in ids if i not in profs]
        extra = [i for i in profs if i not in ids]          # 有档案但名单里没有

        listed_total += len(ids)
        profiled_total += len(profs)
        ghost_total += len(ghosts)
        in_org.update(ids)
        in_org.update(profs)

        if not profs:
            findings.append({"code": "DEPT_NO_PROFILE", "severity": SEV_P1, "dept": dept,
                             "detail": "整部门零档案：%d 个席位只有名字，无法回答职责/模型/档级"
                                       % len(ids), "agents": ids})
        elif ghosts:
            findings.append({"code": "GHOST_SEAT", "severity": SEV_P2, "dept": dept,
                             "detail": "%d/%d 席位有名无档" % (len(ghosts), len(ids)),
                             "agents": ghosts})

        if lead is None:
            findings.append({"code": "LEAD_NULL", "severity": SEV_P1, "dept": dept,
                             "detail": "主官空悬（lead=None），该营无签字人"})
        elif str(lead) not in profs:
            in_list = str(lead) in ids
            findings.append({"code": "LEAD_NO_PROFILE", "severity": SEV_P1, "dept": dept,
                             "lead": str(lead),
                             "detail": "主官 %s 无档案%s" % (lead, "（仅在本部门名单中）" if in_list else "（且不在本部门名单）")})
            if not in_list:
                findings.append({"code": "LEAD_OUT_OF_DEPT", "severity": SEV_P2, "dept": dept,
                                 "lead": str(lead),
                                 "detail": "主官 %s 不在本部门名单，指挥链跨部门（须显式兼任说明）" % lead})

        if extra:
            findings.append({"code": "PROFILE_NOT_LISTED", "severity": SEV_P3, "dept": dept,
                             "detail": "有档案但未列入部门名单", "agents": extra})

        rows.append({"dept": dept, "name": d.get("name"), "lead": lead,
                     "listed": len(ids), "profiled": len(profs),
                     "ghost": len(ghosts), "ghost_list": ghosts,
                     "lead_ok": bool(lead) and str(lead) in profs})

    # 中文名重复（历史 4 对，账面混淆）
    by_cn: dict[str, list[str]] = {}
    for aid, cn in names.items():
        by_cn.setdefault(cn, []).append(aid)
    dups = {cn: ids for cn, ids in by_cn.items() if len(ids) > 1}
    for cn, ids in sorted(dups.items()):
        findings.append({"code": "DUPLICATE_CN_NAME", "severity": SEV_P2,
                         "name": cn, "holders": ids,
                         "detail": "中文名 %s 被 %d 个席位共用，账面易混" % (cn, len(ids))})

    # 未登记 vs 真编外：
    #   有 hr 档案、组织真源里没有席位——若此人正是某部门主官，属「主官未登记档案」，
    #   不能笼统称"编外"（把参谋长说成编外是错的）；其余才算真编外，待收编或注销。
    leads_all = {str(d.get("lead")) for d in depts if d.get("lead")}
    unregistered_leads = sorted(a for a in names if a not in in_org and a in leads_all)
    if unregistered_leads:
        findings.append({"code": "LEAD_UNREGISTERED", "severity": SEV_P2,
                         "agents": unregistered_leads,
                         "detail": "%d 名主官持有 hr 档案但组织真源未登记席位档案"
                                   "（跨部门任职须在 org.json 显式登记，否则指挥链断在真源）"
                                   % len(unregistered_leads)})
    orphans = sorted(a for a in names if a not in in_org and a not in leads_all)
    if orphans:
        findings.append({"code": "ORPHAN_STAFF", "severity": SEV_P3,
                         "agents": orphans,
                         "detail": "%d 员有 hr 档案但既未挂部门也非任何主官（收编或注销待裁定）"
                                   % len(orphans)})

    leads_total = len(depts)
    leads_ok = sum(1 for r in rows if r["lead_ok"])
    p1 = sum(1 for f in findings if f["severity"] == SEV_P1)
    p2 = sum(1 for f in findings if f["severity"] == SEV_P2)
    p3 = sum(1 for f in findings if f["severity"] == SEV_P3)

    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": RULES_VERSION,
        "ok": True,
        "summary": {
            "depts": len(depts),
            "seats_listed": listed_total,
            "seats_profiled": profiled_total,
            "seats_ghost": ghost_total,
            "ghost_ratio": round(ghost_total / listed_total, 3) if listed_total else 0.0,
            "leads_total": leads_total,
            "leads_ok": leads_ok,
            "leads_not_ok": leads_total - leads_ok,
            "duplicate_names": len(dups),
            "orphan_staff": len(orphans),
            "leads_unregistered": len(unregistered_leads),
            "findings_p1": p1, "findings_p2": p2, "findings_p3": p3,
        },
        "dept_rows": rows,
        "findings": findings,
        "note": "只读审计：缺口清单不是档案。charter_detail 须田丰归口、老田裁定，"
                "本工具不代填、不造数据；补齐走 engine/org_enroll.py（须签字）。",
    }


def _print_human(rep: dict) -> None:
    s = rep["summary"]
    print("编制完整性审计（rules %s）" % rep["rules_version"])
    print("=" * 66)
    print("部门 %d 个 · 席位名单 %d · 有档案 %d · 有名无档 %d（%.0f%%）"
          % (s["depts"], s["seats_listed"], s["seats_profiled"],
             s["seats_ghost"], s["ghost_ratio"] * 100))
    print("主官 %d 个 · 档案齐备 %d · 异常 %d" % (s["leads_total"], s["leads_ok"], s["leads_not_ok"]))
    print("重名 %d 对 · 编外 %d 员 · 主官未登记 %d · 缺口 P1=%d P2=%d P3=%d"
          % (s["duplicate_names"], s["orphan_staff"], s["leads_unregistered"],
             s["findings_p1"], s["findings_p2"], s["findings_p3"]))
    print()
    order = {SEV_P1: 0, SEV_P2: 1, SEV_P3: 2}
    for f in sorted(rep["findings"], key=lambda x: (order.get(x["severity"], 9), x["code"])):
        who = f.get("dept") or f.get("name") or ""
        extra = ""
        if f.get("agents"):
            extra = " → " + ", ".join(f["agents"][:8]) + ("…" if len(f["agents"]) > 8 else "")
        print("[%s] %-22s %-14s %s%s" % (f["severity"], f["code"], who, f["detail"], extra))
    print()
    print(rep["note"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数字军团编制完整性审计（只读）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--json", action="store_true", help="输出 JSON（供端点/机器消费）")
    ap.add_argument("--strict", action="store_true", help="存在 P1 时以非零码退出（CI 用）")
    args = ap.parse_args(argv)
    try:
        rep = audit(Path(args.root))
    except FileNotFoundError as e:
        print("审计失败：%s" % e, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        _print_human(rep)
    if args.strict and rep["summary"]["findings_p1"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
