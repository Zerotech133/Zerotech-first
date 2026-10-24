# -*- coding: utf-8 -*-
"""零界 v1.1「事业目标—事项—成果」三级关系校验器（WP-A1 参考实现）

在 os_gate_check（单事项 v1.0）之上，校验上层两级：
  1) 事业目标（Goal）：长期锚点；必须由本人建立；假设（assumption）必须带状态；
  2) 事项（Item）：必须挂在一个 Goal 下，且声明本轮成果类型（四工作面之一）；
  3) 成果（Outcome）：类型必须与事项声明一致；按类型要求引用/证据；
     引用必须带来源面与来源类型；**推断不得因重复转述升级为事实**。

事件流 = goal.created（可多条 goal.updated）+ v1.0 单事项事件链（schema_version=os-item/1.1）。

退出码沿用：0 PASS / 3 BLOCKED 或 ISOLATED_ONLY / 1 FAIL / 2 输入错误。

只读、零依赖；合成数据；不写文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import os_gate_check as G  # noqa: E402

SURFACES = ("software", "business_plan", "content", "research", "external")
OUTCOME_KINDS = ("software", "business_plan", "content", "research")
GOAL_ONLY_TYPES = ("goal.created", "goal.updated")
SOURCE_KINDS = ("data", "document", "commit", "conversation", "inference")
CONFIDENCES = ("fact", "hypothesis")
#: 这几类成果必须有外部引用（研究/BP/内容不能凭空产出）
CITATION_REQUIRED = ("business_plan", "content", "research")
REQUIRED_GOAL_FIELDS = ("goal_id", "statement", "target_user", "assumptions",
                        "success_signals")


def _fail(checks: List[Dict[str, Any]], cid: str, detail: str) -> None:
    checks.append({"id": cid, "status": "FAIL", "detail": detail})


def _ok(checks: List[Dict[str, Any]], cid: str, detail: str) -> None:
    checks.append({"id": cid, "status": "PASS", "detail": detail})


def _verify_chain(events: List[Dict[str, Any]],
                  checks: List[Dict[str, Any]]) -> None:
    """在原始事件流上校验哈希链（拆分前做，避免重建链掩盖篡改）。"""
    prev = None
    for ev in events:
        if prev is not None and ev.get("prev_hash") != prev:
            _fail(checks, "CHAIN_HASH",
                  "prev_hash 不连续（seq=%s）" % ev.get("seq"))
        if ev.get("hash") != G.event_hash(ev):
            _fail(checks, "CHAIN_HASH",
                  "事件内容与 hash 不符（seq=%s）" % ev.get("seq"))
        prev = ev.get("hash")


def _reseal(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为子链做本地连续编号（仅用于语义判定；原链完整性已单独校验）。"""
    out = []
    prev = None
    for i, ev in enumerate(events, 1):
        rec = dict(ev)
        rec["seq"] = i
        rec["event_id"] = "EVT-%s-%03d" % (rec.get("item_id"), i)
        rec["prev_hash"] = prev
        rec["hash"] = G.event_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    return out


def evaluate_stream(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """返回 {verdict, exit_code, goal, citations, item_report, checks}。"""
    if not events:
        return {"verdict": "INPUT_ERROR", "exit_code": 2,
                "checks": [{"id": "INPUT", "status": "FAIL", "detail": "空事件流"}]}

    goal_events = [e for e in events if e.get("type") in GOAL_ONLY_TYPES]
    item_events = [e for e in events if e.get("type") not in GOAL_ONLY_TYPES]
    checks: List[Dict[str, Any]] = []
    goal_state = {"status": "BLOCKED", "goal_id": None}
    citation_state = {"status": "BLOCKED", "count": 0}
    _verify_chain(events, checks)

    # ---- 1. 目标层
    goals = [e for e in goal_events if e.get("type") == "goal.created"]
    if not goals:
        checks.append({"id": "GOAL_ANCHOR", "status": "WARN",
                       "detail": "没有 goal.created：事项缺少事业目标锚点（阻断，不是违规）"})
    else:
        goal = goals[0]
        payload = goal.get("payload") or {}
        actor = goal.get("actor") or {}
        missing = [k for k in REQUIRED_GOAL_FIELDS if not payload.get(k)]
        if missing:
            _fail(checks, "GOAL_FIELDS", "目标缺少字段：%s" % missing)
        if actor.get("kind") != "user":
            _fail(checks, "GOAL_ACTOR", "事业目标必须由本人建立，实际 actor.kind=%r"
                  % actor.get("kind"))
        bad_status = []
        for a in payload.get("assumptions") or []:
            if not isinstance(a, dict) or a.get("status") not in ("hypothesis", "validated", "falsified"):
                bad_status.append(a.get("id") if isinstance(a, dict) else a)
        if bad_status:
            _fail(checks, "GOAL_ASSUMPTIONS",
                  "假设缺少合法状态 hypothesis/validated/falsified：%s" % bad_status)
        goal_state = {"status": "PASS" if not missing and not bad_status and actor.get("kind") == "user" else "FAIL",
                      "goal_id": payload.get("goal_id")}

    # ---- 2. 事项挂靠
    created = [e for e in item_events if e.get("type") == "item.created"]
    item_goal = created[0].get("refs", {}).get("goal_id") if created else None
    outcome_kind = (created[0].get("payload") or {}).get("outcome_kind") if created else None
    if not created:
        _fail(checks, "ITEM_ANCHOR", "没有 item.created")
    else:
        if not item_goal:
            _fail(checks, "ITEM_ANCHOR", "item.created 未声明 refs.goal_id（无目标不开事项）")
        elif goal_state["goal_id"] and item_goal != goal_state["goal_id"]:
            _fail(checks, "ITEM_ANCHOR", "事项挂错目标：%r ≠ %r"
                  % (item_goal, goal_state["goal_id"]))
        if outcome_kind not in OUTCOME_KINDS:
            _fail(checks, "OUTCOME_KIND",
                  "事项成果类型非法：%r（允许 %s）" % (outcome_kind, list(OUTCOME_KINDS)))

    # ---- 3. 单事项 v1.0 链（复用判据器；子链需本地连续编号，原链完整性已校验）
    item_report = G.evaluate_events(_reseal(item_events))

    # ---- 4. 成果类型与引用
    artifacts = [e for e in item_events if e.get("type") == "artifact.produced"]
    seen_confidence: Dict[str, str] = {}
    for art in artifacts:
        payload = art.get("payload") or {}
        kind = payload.get("outcome_kind")
        if kind != outcome_kind:
            _fail(checks, "OUTCOME_MISMATCH",
                  "产物成果类型 %r 与事项声明 %r 不一致" % (kind, outcome_kind))
        citations = payload.get("citations")
        if citations is None:
            _fail(checks, "CITATION_DECLARATION", "产物未声明 citations（可空但必须显式声明）")
            continue
        if outcome_kind in CITATION_REQUIRED and not citations:
            _fail(checks, "CITATION_REQUIRED",
                  "%s 类成果必须至少 1 条外部引用" % outcome_kind)
        for c in citations:
            if not isinstance(c, dict):
                _fail(checks, "CITATION_SHAPE", "引用不是对象：%r" % (c,))
                continue
            missing = [k for k in ("source_surface", "source_ref", "source_kind",
                                   "claim", "confidence") if not c.get(k)]
            if missing:
                _fail(checks, "CITATION_SHAPE", "引用缺少字段 %s：%r" % (missing, c))
                continue
            if c["source_surface"] not in SURFACES:
                _fail(checks, "CITATION_SOURCE", "未知来源面：%r" % c["source_surface"])
            if c["source_kind"] not in SOURCE_KINDS:
                _fail(checks, "CITATION_SOURCE", "未知来源类型：%r" % c["source_kind"])
            if c["confidence"] not in CONFIDENCES:
                _fail(checks, "CITATION_CONFIDENCE", "未知置信度：%r" % c["confidence"])
            # 规则一：推断不得直接作为事实引用
            if c["source_kind"] == "inference" and c["confidence"] == "fact":
                _fail(checks, "INFERENCE_AS_FACT",
                      "推断被当作事实引用：%r（推断不得直接升格事实）" % c["source_ref"])
            # 规则二：同一来源被重复转述时，置信度不得升级
            prev = seen_confidence.get(c["source_ref"])
            if prev == "hypothesis" and c["confidence"] == "fact":
                _fail(checks, "CITATION_UPGRADE",
                      "同一来源 %r 由 hypothesis 被转述为 fact（重复转述不得升级）"
                      % c["source_ref"])
            seen_confidence.setdefault(c["source_ref"], c["confidence"])
        citation_state = {"status": "PASS", "count": len(citations)}
    if artifacts and citation_state["status"] != "PASS":
        citation_state["status"] = "FAIL" if any(
            c["id"].startswith(("CITATION", "INFERENCE", "OUTCOME")) and c["status"] == "FAIL"
            for c in checks) else "BLOCKED"

    # ---- 5. 汇总
    any_fail = any(c["status"] == "FAIL" for c in checks) or item_report.failed()
    if any_fail:
        verdict, code = "FAIL", 1
    elif goal_state["status"] != "PASS" or item_report.gates["G0"]["status"] != "PASS":
        verdict, code = "BLOCKED", 3
    elif item_report.verdict in ("BLOCKED", "ISOLATED_ONLY"):
        verdict, code = item_report.verdict, 3
    elif citation_state["status"] == "FAIL":
        verdict, code = "FAIL", 1
    elif citation_state["status"] != "PASS":
        verdict, code = "BLOCKED", 3
    else:
        verdict, code = "PASS", 0

    return {
        "verdict": verdict,
        "exit_code": code,
        "goal": goal_state,
        "citations": citation_state,
        "item_report": {
            "verdict": item_report.verdict,
            "state": item_report.state,
            "gates": {k: v["status"] for k, v in item_report.gates.items()},
            "honesty": item_report.honesty,
        },
        "checks": checks + [c for c in item_report.checks if c["status"] == "FAIL"],
    }


def load_stream(path: str):
    events, err = G.load_events(path)
    return events, err


def render(result: Dict[str, Any]) -> str:
    r = result
    lines = ["=" * 74,
             "零界 v1.1 三级关系校验 · 判定 %s (exit %d)" % (r["verdict"], r["exit_code"]),
             "目标层：%s  引用层：%s" % (r["goal"]["status"], r["citations"]["status"]),
             "单事项链：%s / 状态 %s" % (r["item_report"]["verdict"],
                                        r["item_report"]["state"]),
             "=" * 74]
    for c in r.get("checks", []):
        if c["status"] != "PASS":
            lines.append("[%s] %s: %s" % (c["status"], c["id"], c["detail"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="三级关系校验器（只读）")
    ap.add_argument("item_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    events, err = load_stream(args.item_dir)
    if err:
        print("INPUT_ERROR: %s" % err, file=sys.stderr)
        return 2
    result = evaluate_stream(events)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(render(result))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
