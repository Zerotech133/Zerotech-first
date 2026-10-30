# -*- coding: utf-8 -*-
"""鲁肃军团（量化交易部）人员就位/模型/能力配置审计（2026-10-30）。

只读审计 8 席：
  org 编制 / HR 姓名 / IDE bundle 身份 / prompt 文件 / 模型绑定（org vs team）/
  工具与 memory_grants / 团队归属（alpha、strategy、standalone）/ 周派单 / QS1 探针。

用法：
  python -X utf8 tools/invest_staff_audit.py            # Markdown 报告
  python -X utf8 tools/invest_staff_audit.py --json     # JSON
  python -X utf8 tools/invest_staff_audit.py --strict   # 有 FAIL 时 rc=1
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORG = os.path.join(REPO, "agent_platform", "registry", "org.json")
TEAM_ALPHA = os.path.join(REPO, "agent_platform", "registry", "team_invest.json")
TEAM_STRATEGY = os.path.join(REPO, "agent_platform", "registry", "team_quant_strategy.json")
BUNDLE = os.path.join(REPO, "portal", "agent_ide_bundle.json")
# worktree 场景：任务板真源在主仓，回退读取
_PARENT = os.path.dirname(REPO)
MAIN_REPO = os.path.dirname(_PARENT) if os.path.basename(_PARENT) == ".worktrees" else REPO
TASKS = os.path.join(MAIN_REPO, ".worktrees", "tasks.json")
PROMPTS = os.path.join(REPO, "agent_platform", "prompts")
HR = os.path.join(REPO, "agent_platform", "hr")
HB = os.path.join(REPO, "agent_platform", "config", "seat_heartbeat_seats.json")
QS1_PROBE = os.path.join(REPO, "tools", "qs1_runtime_probe.py")

SEATS = ["D1", "T1", "T2", "N1", "F1", "ST1", "WA1", "QS1"]
ALPHA = {"D1", "T1", "T2", "N1", "F1"}
TOOL_MIN = {
    "D1": {"make_context", "quote_snapshot", "events"},
    "T1": {"kline_daily", "quote_snapshot", "events"},
    "T2": {"kline_daily", "quote_snapshot", "events"},
    "N1": {"quote_snapshot", "events_for_stock"},
    "F1": {"quote_snapshot", "make_context", "datamart_read"},
    "ST1": {"kline_daily", "quote_snapshot", "make_context", "events"},
    "WA1": {"quote_snapshot", "events"},
    "QS1": {"make_context", "events"},
}
WEEK_TASKS = ["WO-INV-WEEK-20261029-%s" % s for s in ("T2", "N1", "F1", "ST1", "WA1", "QS1")]


def _read(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _cn_from_resume(seat):
    p = os.path.join(HR, seat, "resume.yaml")
    try:
        text = io.open(p, encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r"^cn_name:\s*(\S+)\s*$", text, re.M)
    return m.group(1).strip("\"'") if m else None


def audit():
    org = _read(ORG, {}) or {}
    inv = next((d for d in org.get("departments") or [] if d.get("dept") == "invest"), {})
    profs = {p.get("agent_id"): p for p in inv.get("agent_profiles") or []}
    team = {a["agent_id"]: a for a in (_read(TEAM_ALPHA, {}) or {}).get("agents", [])}
    strat = {a["agent_id"]: a for a in (_read(TEAM_STRATEGY, {}) or {}).get("agents", [])}
    bundle = (_read(BUNDLE, {}) or {}).get("agents") or {}
    tasks = _read(TASKS, {}) or {}
    hb = _read(HB, {}) or {}

    checks = []

    def add(seat, name, ok, detail="", level="FAIL"):
        checks.append({"seat": seat, "check": name,
                       "status": "PASS" if ok else level, "detail": detail})

    org_seats = set(inv.get("agents") or [])
    base = set(SEATS)
    add("—", "编制基线 8 席", base <= org_seats,
        "org invest=%s" % sorted(org_seats))
    extra = sorted(org_seats - base)
    if extra:
        # 2026-10-30 鲁肃部门扩编（WO-INV-STAFF-20261030：TR1/TR2/TR3/WA2/WA3 等）
        # 后：审计基线仍为 8 席，新增席位单列 WARN（不阻断 test_audit_all_green），
        # 待扩编单补齐对应审计项（TOOL_MIN/团队归属）后再逐席纳入 FAIL 口径。
        add("—", "编制增员（待纳入审计）", True,
            "extra=%s" % extra, level="WARN")
    for s in SEATS:
        p = profs.get(s)
        add(s, "org profile 存在", bool(p))
        if not p:
            continue
        cn = p.get("cn_name")
        add(s, "cn_name", bool(cn), "org=%r hr=%r" % (cn, _cn_from_resume(s)))
        add(s, "HR 姓名一致", cn == _cn_from_resume(s),
            "hr=%r" % _cn_from_resume(s))
        add(s, "IDE bundle 身份", s in bundle and bundle[s].get("cn_name") == cn,
            "bundle=%r" % (bundle.get(s) or {}).get("cn_name"))
        rp = p.get("role_profile")
        add(s, "prompt 文件", bool(rp) and os.path.exists(os.path.join(PROMPTS, rp + ".md")),
            "role_profile=%r" % rp)
        add(s, "analysis_type", bool(p.get("analysis_type")), repr(p.get("analysis_type")))
        add(s, "模型绑定", bool(p.get("llm_binding")), repr(p.get("llm_binding")))
        tools = set(p.get("tools") or [])
        miss = TOOL_MIN[s] - tools
        add(s, "工具覆盖", not miss, "missing=%s" % sorted(miss))
        add(s, "memory_grants", bool(p.get("memory_grants")), repr(p.get("memory_grants"))[:80])
        if s in ALPHA:
            n = team.get(s)
            add(s, "alpha 团队归属", bool(n))
            if n:
                add(s, "org/team 模型一致", n.get("llm_binding") == p.get("llm_binding"),
                    "org=%s team=%s" % (p.get("llm_binding"), n.get("llm_binding")))
        elif s == "ST1":
            n = strat.get(s)
            add(s, "strategy 团队归属", bool(n))
            if n:
                add(s, "org/team 模型一致", n.get("llm_binding") == p.get("llm_binding"),
                    "org=%s team=%s" % (p.get("llm_binding"), n.get("llm_binding")))
        else:
            add(s, "团队归属", True, "standalone/runtime（WA1/QS1 独立链路）", level="WARN")
        if s == "QS1":
            add(s, "QS1 runtime 模式", str(p.get("llm_binding")).lower() == "none",
                repr(p.get("llm_binding")))
    for tid in WEEK_TASKS:
        t = tasks.get(tid) or {}
        add("—", "周派单 %s" % tid,
            t.get("status") == "doing" and t.get("approver") == "D1"
            and "正式派单" in str(t.get("note") or ""), "status=%s" % t.get("status"))
    hb_seats = set(hb.get("seats") or [])
    add("—", "心跳名单", set(SEATS) - {"QS1"} <= hb_seats, "missing=%s" % sorted((set(SEATS) - {"QS1"}) - hb_seats))
    add("—", "QS1 专用探针", os.path.exists(QS1_PROBE), QS1_PROBE)
    add("—", "QS1 runtime note", "qs1_runtime_probe.py" in str(hb.get("_runtime_note") or ""))

    fails = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    return {"ok": not fails, "checks": checks, "fails": fails, "warns": warns,
            "summary": {"total": len(checks), "pass": sum(1 for c in checks if c["status"] == "PASS"),
                        "warn": len(warns), "fail": len(fails)}}


def render_markdown(res):
    L = ["# 鲁肃军团人员就位与能力审计", "",
         "> total=%d pass=%d warn=%d fail=%d" % (
             res["summary"]["total"], res["summary"]["pass"],
             res["summary"]["warn"], res["summary"]["fail"]), ""]
    for seat in SEATS:
        rows = [c for c in res["checks"] if c["seat"] == seat]
        if not rows:
            continue
        L.append("## %s" % seat)
        L.append("")
        L.append("| 检查 | 结果 | 详情 |")
        L.append("|---|---|---|")
        for c in rows:
            L.append("| %s | %s | %s |" % (c["check"], c["status"], str(c["detail"])[:120]))
        L.append("")
    others = [c for c in res["checks"] if c["seat"] == "—"]
    if others:
        L.append("## 全部门检查")
        L.append("")
        L.append("| 检查 | 结果 | 详情 |")
        L.append("|---|---|---|")
        for c in others:
            L.append("| %s | %s | %s |" % (c["check"], c["status"], str(c["detail"])[:120]))
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="鲁肃军团人员就位/模型/能力审计")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    res = audit()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(res))
    return 1 if (args.strict and not res["ok"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
