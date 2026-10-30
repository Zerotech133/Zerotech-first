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

try:  # 包内导入（serve/测试经 engine.* 进来）
    from engine.org_profiles import SRC_DEPT, matrix_assignments, profile_index
except ImportError:  # 直接 `python engine/org_audit.py` 运行
    from org_profiles import SRC_DEPT, matrix_assignments, profile_index

SCHEMA_VERSION = "1.0"
RULES_VERSION = "1.2"

# 严重度口径（写死在此，避免各处解释不一）
#   P1 无法指挥：主官空悬 / 主官真无档案（部门表、chief、team_* 都没有）/ 整部门零档案
#   P2 指挥易错：主官跨部门引用（未登记兼任说明）/ 主官档案登记在部门表之外
#                （取档方可能静默回落默认档）/ 中文名重复 / 部分席位缺档
#                / 有档案但未声明 llm_binding（回落默认档＝替它假定有 LLM 能力）
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


def _extra_profiles(root: Path, org: dict) -> dict:
    """部门档案表之外**确实存在**的档案（chief 块 / team_*.json），带来源与绑定。

    为什么要有它：A0（田丰，参谋长）与 A0A（参谋长助理）的档案根本不在
    `departments[].agent_profiles` 里——A0 在 org.json 顶层 `chief` 块，A0A 在
    `registry/team_chief.json`。旧版只扫部门档案表，于是把这两位报成
    「主官无档案（P1）」，读数等于说「军团没有参谋长」——**空态要先核查源里有没有
    该字段，不能把「我没找到」当成「不存在」**。

    实现委托 engine/org_profiles（与 legion_command.resolve_binding 同一口径），
    避免审计说「有档案」而会议层取不到、或反过来，两处各说各话。
    """
    idx = profile_index(root)
    return {aid: v for aid, v in idx.items() if v.get("source") != SRC_DEPT}


def audit(root: Path) -> dict:
    """只读审计。返回结构化缺口清单（不写任何字节）。"""
    org = _load_org(root)
    names = _hr_names(root)
    depts = org.get("departments") or []

    findings: list[dict] = []
    rows: list[dict] = []
    listed_total = profiled_total = ghost_total = elsewhere_total = 0
    matrix_total = 0
    in_org: set[str] = set()
    all_idx = profile_index(root)  # 三处档案全量（dept / chief / team_*）
    matrix_by_dept = matrix_assignments(
        org
    )  # P2：项目行营角色（主档 matrix_assignments）
    extra_prof = {
        aid: v for aid, v in all_idx.items() if v.get("source") != SRC_DEPT
    }  # 部门表之外的
    leads_elsewhere: set[str] = set()
    # 同一席位可能同时挂在「常设部门」与「项目行营」两条名单上（DEV3 在 dev 与
    # proj_backtest）。条目数会把一个人算两次——两个数都要报，否则「68 席」是虚的。
    seat_depts: dict[str, list] = {}

    for d in depts:
        dept = str(d.get("dept"))
        lead = d.get("lead")
        ids = [str(x) for x in (d.get("agents") or [])]
        profs = [str(p.get("agent_id")) for p in (d.get("agent_profiles") or [])]
        matrix_ids = set(matrix_by_dept.get(dept, {}).keys())
        # 「有名无档」必须是**三处都查不到**才成立。DENGZHI1（邓芝）的档案在
        # registry/team_comm.json（Hermes 8787 通讯中枢），只扫部门表就把它报成幽灵席位，
        # 与 A0A 是同一个根因（S41 只修了主官判定，漏了席位统计——S42 补齐）。
        ghosts = [
            i
            for i in ids
            if i not in profs and i not in matrix_ids and i not in extra_prof
        ]
        elsewhere_seats = [
            i for i in ids if i not in profs and i not in matrix_ids and i in extra_prof
        ]
        extra = [i for i in profs if i not in ids]  # 有档案但名单里没有

        listed_total += len(ids)
        profiled_total += len(profs)
        ghost_total += len(ghosts)
        elsewhere_total += len(elsewhere_seats)
        matrix_total += len(matrix_ids)
        in_org.update(ids)
        in_org.update(profs)
        for i in ids:
            seat_depts.setdefault(i, []).append(dept)

        # 2026-10-30 P79：空部门（无名单席位）不算零档案部门——
        # 原条件漏了 ids，会把 agents=[] 的空壳部门误报 DEPT_NO_PROFILE
        # （trading 部门迁移后为空即触发 test_org_audit 假红）。
        if ids and not profs and not matrix_ids:
            findings.append(
                {
                    "code": "DEPT_NO_PROFILE",
                    "severity": SEV_P1,
                    "dept": dept,
                    "detail": "整部门零档案：%d 个席位只有名字，无法回答职责/模型/档级"
                    % len(ids),
                    "agents": ids,
                }
            )
        elif ghosts:
            findings.append(
                {
                    "code": "GHOST_SEAT",
                    "severity": SEV_P2,
                    "dept": dept,
                    "detail": "%d/%d 席位有名无档" % (len(ghosts), len(ids)),
                    "agents": ghosts,
                }
            )
        if elsewhere_seats:
            srcs = sorted(
                {str(extra_prof[i].get("source_detail") or "") for i in elsewhere_seats}
            )
            findings.append(
                {
                    "code": "SEAT_PROFILE_ELSEWHERE",
                    "severity": SEV_P3,
                    "dept": dept,
                    "detail": "%d 席档案登记在部门表之外（%s）：职责可答，"
                    "但按部门表取档的旧调用方会当成无档"
                    % (len(elsewhere_seats), "、".join(s for s in srcs if s)),
                    "agents": elsewhere_seats,
                }
            )

        if lead is None:
            findings.append(
                {
                    "code": "LEAD_NULL",
                    "severity": SEV_P1,
                    "dept": dept,
                    "detail": "主官空悬（lead=None），该营无签字人",
                }
            )
        else:
            lid = str(lead)
            in_list, in_prof = lid in ids, lid in profs
            # 查「组织里任何一处」有没有档案：本部门表之外还包括其他部门的部门表
            # （跨部门兼任的主官，档案常落在自己主属部门的表里，如 A0A 迁入 chief_line）。
            elsewhere = all_idx.get(lid)
            if not in_prof:
                if elsewhere:
                    # 档案存在但不在部门档案表：职责/模型/档级答得出，但**按部门取档案的
                    # 调用方（如会议层 resolve_binding）取不到 → 静默回落默认档**。
                    src_kind = elsewhere.get("source")
                    # 档案在**别的部门档案表**里（跨部门兼任的常态：主官档案在自己主属部门）：
                    # 统一取档口径能查到，不存在"回落默认档"，只是位置不在本部门——
                    # 这是结构事实，P3 提示即可，不该按 P2 当成取档故障。
                    other_dept = src_kind == SRC_DEPT
                    findings.append(
                        {
                            "code": "LEAD_PROFILE_ELSEWHERE",
                            "severity": SEV_P3 if other_dept else SEV_P2,
                            "dept": dept,
                            "lead": lid,
                            "detail": (
                                "主官 %s 档案在其主属部门 %s（跨部门兼任，统一取档可查，绑定 %s）"
                                % (
                                    lid,
                                    (elsewhere.get("source_detail") or "").replace(
                                        "dept:", ""
                                    ),
                                    elsewhere.get("llm_binding") or "未声明",
                                )
                            )
                            if other_dept
                            else (
                                "主官 %s 有档案但登记在 %s（不在本部门档案表，绑定 %s）："
                                "按部门档案取档的旧调用方取不到，会静默回落默认档"
                                % (
                                    lid,
                                    elsewhere.get("source_detail") or src_kind,
                                    elsewhere.get("llm_binding") or "未声明",
                                )
                            ),
                        }
                    )
                    leads_elsewhere.add(lid)
                else:
                    findings.append(
                        {
                            "code": "LEAD_NO_PROFILE",
                            "severity": SEV_P1,
                            "dept": dept,
                            "lead": lid,
                            "detail": "主官 %s 无档案%s"
                            % (
                                lid,
                                "（仅在本部门名单中）"
                                if in_list
                                else "（且不在本部门名单）",
                            ),
                        }
                    )
            # 跨部门引用与「有无档案」是两件事：主官有档案却不在本部门名单同样是跨部门，
            # 旧实现把它嵌在「无档案」分支里，导致这类情况整条漏报（S41 修）。
            if not in_list:
                ap = d.get("lead_appointed") or {}
                noted = bool(ap.get("cross_dept") and ap.get("cross_note"))
                findings.append(
                    {
                        "code": "LEAD_OUT_OF_DEPT",
                        "severity": SEV_P3 if noted else SEV_P2,
                        "dept": dept,
                        "lead": lid,
                        "detail": (
                            "主官 %s 跨部门兼任，已在真源显式登记（%s）"
                            % (lid, str(ap.get("cross_note"))[:40])
                        )
                        if noted
                        else (
                            "主官 %s 不在本部门名单，指挥链跨部门（须显式兼任说明，"
                            "用 org_enroll --set-lead --cross-note 登记）" % lid
                        ),
                    }
                )

        if extra:
            findings.append(
                {
                    "code": "PROFILE_NOT_LISTED",
                    "severity": SEV_P3,
                    "dept": dept,
                    "detail": "有档案但未列入部门名单",
                    "agents": extra,
                }
            )

        # 有档案却没说绑什么模型：取档方会回落默认档，等于**替这个席位假定有 LLM 能力**。
        # 规则执行体（桥接/回调/门面）本该是 none，回落 9b 就是虚构能力；
        # 真需要 LLM 的席位则缺了能力声明。两种都不该由工具猜——只报缺口。
        no_bind = [
            i for i in ids if (all_idx.get(i) or {}).get("llm_binding", "") == ""
        ]
        if no_bind:
            findings.append(
                {
                    "code": "BINDING_MISSING",
                    "severity": SEV_P2,
                    "dept": dept,
                    "agents": no_bind,
                    "detail": "%d 席有档案但未声明 llm_binding（%s）：取档方会回落默认档，"
                    "等于替它假定有 LLM 能力"
                    % (
                        len(no_bind),
                        "、".join(
                            sorted(
                                {
                                    (all_idx.get(i) or {}).get("source_detail", "")
                                    for i in no_bind
                                }
                            )
                        ),
                    ),
                }
            )

        rows.append(
            {
                "dept": dept,
                "name": d.get("name"),
                "lead": lead,
                "listed": len(ids),
                "profiled": len(profs),
                "ghost": len(ghosts),
                "ghost_list": ghosts,
                "matrix": len(matrix_ids),
                "profiled_elsewhere": elsewhere_seats,
                # 档案可能在本部门表、其他部门表、chief 或 team 文件——
                # 只要组织里任何一处有，就算「档案齐备」（位置问题由
                # LEAD_PROFILE_ELSEWHERE 单独提示，不重复算成不齐备）。
                "lead_ok": bool(lead) and (str(lead) in profs or str(lead) in all_idx),
            }
        )

    multi = {k: v for k, v in seat_depts.items() if len(v) > 1}
    if multi:
        findings.append(
            {
                "code": "SEAT_IN_MULTIPLE_DEPTS",
                "severity": SEV_P3,
                "agents": sorted(multi),
                "detail": "%d 席同时挂在多个部门名单（行营兼任：常设部门 + 项目行营）。"
                "名单条目 %d ≠ 独立席位 %d，任何「共多少席」的表述都必须说清用的是哪个口径"
                % (len(multi), listed_total, len(seat_depts)),
            }
        )

    # 中文名重复（历史 4 对，账面混淆）
    by_cn: dict[str, list[str]] = {}
    for aid, cn in names.items():
        by_cn.setdefault(cn, []).append(aid)
    dups = {cn: ids for cn, ids in by_cn.items() if len(ids) > 1}
    for cn, ids in sorted(dups.items()):
        findings.append(
            {
                "code": "DUPLICATE_CN_NAME",
                "severity": SEV_P2,
                "name": cn,
                "holders": ids,
                "detail": "中文名 %s 被 %d 个席位共用，账面易混" % (cn, len(ids)),
            }
        )

    # 未登记 vs 真编外：
    #   有 hr 档案、组织真源里没有席位——若此人正是某部门主官，属「主官未登记档案」，
    #   不能笼统称"编外"（把参谋长说成编外是错的）；其余才算真编外，待收编或注销。
    leads_all = {str(d.get("lead")) for d in depts if d.get("lead")}
    # 「未登记」只算真没有档案的：档案在 chief / team_* 的已在 LEAD_PROFILE_ELSEWHERE 单列，
    # 不能既说「有档案但位置不规范」又说「未登记档案」——两句话会互相打脸。
    unregistered_leads = sorted(
        a for a in names if a not in in_org and a in leads_all and a not in extra_prof
    )
    if unregistered_leads:
        findings.append(
            {
                "code": "LEAD_UNREGISTERED",
                "severity": SEV_P2,
                "agents": unregistered_leads,
                "detail": "%d 名主官持有 hr 档案但组织真源未登记席位档案"
                "（跨部门任职须在 org.json 显式登记，否则指挥链断在真源）"
                % len(unregistered_leads),
            }
        )
    orphans = sorted(
        a
        for a in names
        if a not in in_org and a not in leads_all and a not in extra_prof
    )
    if orphans:
        findings.append(
            {
                "code": "ORPHAN_STAFF",
                "severity": SEV_P3,
                "agents": orphans,
                "detail": "%d 员有 hr 档案但既未挂部门也非任何主官（收编或注销待裁定）"
                % len(orphans),
            }
        )

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
            "seats_unique": len(seat_depts),
            "seats_multi_dept": sum(1 for v in seat_depts.values() if len(v) > 1),
            "seats_profiled": profiled_total,
            "seats_matrix": matrix_total,
            "seats_ghost": ghost_total,
            "seats_profiled_elsewhere": elsewhere_total,
            "ghost_ratio": round(ghost_total / listed_total, 3)
            if listed_total
            else 0.0,
            "leads_total": leads_total,
            "leads_ok": leads_ok,
            "leads_not_ok": leads_total - leads_ok,
            "duplicate_names": len(dups),
            "orphan_staff": len(orphans),
            "leads_unregistered": len(unregistered_leads),
            "leads_profile_elsewhere": len(leads_elsewhere),
            "findings_p1": p1,
            "findings_p2": p2,
            "findings_p3": p3,
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
    # 三态必须分行写清楚：名单 = 部门表 + 别处 + 真无档，否则「65 有档 / 0 无档 / 共 66 席」
    # 读者自己都算不平（S42 实测被问过）。
    print(
        "部门 %d 个 · 名单条目 %d（含 %d 席跨部门兼任 → 独立席位 %d）"
        % (
            s["depts"],
            s["seats_listed"],
            s.get("seats_multi_dept", 0),
            s.get("seats_unique", s["seats_listed"]),
        )
    )
    print(
        "            = 部门档案表 %d + 档案在别处 %d + 真无档 %d（%.0f%%）"
        % (
            s["seats_profiled"],
            s.get("seats_profiled_elsewhere", 0),
            s["seats_ghost"],
            s["ghost_ratio"] * 100,
        )
    )
    print(
        "            + 项目行营矩阵档案 %d（已在主档 matrix_assignments，不重复计席位）"
        % s.get("seats_matrix", 0)
    )
    print(
        "主官 %d 个 · 档案齐备 %d · 异常 %d"
        % (s["leads_total"], s["leads_ok"], s["leads_not_ok"])
    )
    print(
        "重名 %d 对 · 编外 %d 员 · 主官未登记 %d · 缺口 P1=%d P2=%d P3=%d"
        % (
            s["duplicate_names"],
            s["orphan_staff"],
            s["leads_unregistered"],
            s["findings_p1"],
            s["findings_p2"],
            s["findings_p3"],
        )
    )
    print()
    order = {SEV_P1: 0, SEV_P2: 1, SEV_P3: 2}
    for f in sorted(
        rep["findings"], key=lambda x: (order.get(x["severity"], 9), x["code"])
    ):
        who = f.get("dept") or f.get("name") or ""
        extra = ""
        if f.get("agents"):
            extra = (
                " → "
                + ", ".join(f["agents"][:8])
                + ("…" if len(f["agents"]) > 8 else "")
            )
        print(
            "[%s] %-22s %-14s %s%s"
            % (f["severity"], f["code"], who, f["detail"], extra)
        )
    print()
    print(rep["note"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数字军团编制完整性审计（只读）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--json", action="store_true", help="输出 JSON（供端点/机器消费）")
    ap.add_argument(
        "--strict", action="store_true", help="存在 P1 时以非零码退出（CI 用）"
    )
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
