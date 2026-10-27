# -*- coding: utf-8 -*-
"""G0 合同判据单测（OS-CLOSE-G0）。

运行（portal 目录下）：
  python -B -m pytest tests/test_os_goal_contract.py -q -p no:cacheprovider
"""

import os
import sys

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import os_goal_contract as C  # noqa: E402

GOAL_ID = "GOL-20261024-0001"
ITEM_ID = "ITM-20261024-0001"
SHA = "sha256:" + "a" * 64


def seal(evs):
    out, prev = [], None
    for i, ev in enumerate(evs, 1):
        rec = dict(ev)
        rec["schema_version"] = C.SCHEMA_VERSION
        rec["seq"] = i
        rec["event_id"] = "EVT-OSG0-%03d" % i
        rec["at"] = "2026-10-24T09:30:00+08:00"
        rec["prev_hash"] = prev
        rec["hash"] = C.event_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    return out


def goal_event(actor_kind="user", goal_id=GOAL_ID):
    return {
        "type": "goal.created",
        "actor": {"kind": actor_kind, "id": "laotian"},
        "refs": {},
        "payload": {
            "goal_id": goal_id,
            "statement": "把零界做成持续创业者的个人事业OS",
            "target_user": "连续创业者",
            "assumptions": [{"id": "A1", "text": "用户愿付费", "status": "hypothesis"}],
            "success_signals": ["四类成果各验收一次"],
        },
    }


def item_event(kind="software", why_now="老田今天要试用"):
    return {
        "type": "item.created",
        "actor": {"kind": "user", "id": "laotian"},
        "refs": {"goal_id": GOAL_ID},
        "payload": {
            "item_id": ITEM_ID,
            "title": "做一页可试用原型",
            "why_now": why_now,
            "outcome_kind": kind,
            "attempt_no": 1,
        },
    }


def outcome_event(kind="software", citations=None):
    return {
        "type": "outcome.produced",
        "actor": {"kind": "user", "id": "laotian"},
        "refs": {"item_id": ITEM_ID},
        "payload": {
            "outcome_kind": kind,
            "version": "v1",
            "content_ref": "portal/data/os_goals/x/a1.html",
            "sha256": SHA,
            "citations": [] if citations is None else citations,
        },
    }


CITE = {
    "source_surface": "research",
    "source_ref": "RES-001",
    "source_kind": "data",
    "claim": "样本内胜率 55%",
    "confidence": "fact",
}


def codes(report):
    return [c["id"] for c in report["checks"] if c["status"] == "FAIL"]


def test_valid_chain_pass():
    rep = C.evaluate(seal([goal_event(), item_event(), outcome_event()]))
    assert rep["verdict"] == "PASS", codes(rep)
    assert rep["gates"]["GOAL"] == "PASS"
    assert rep["gates"]["OUTCOME"] == "PASS"


def test_tamper_fails_chain():
    evs = seal([goal_event(), item_event(), outcome_event()])
    evs[2]["payload"]["version"] = "v2"  # 不重算 hash
    rep = C.evaluate(evs)
    assert rep["verdict"] == "FAIL"
    assert "ENVELOPE" in codes(rep)


def test_seq_gap_fails():
    evs = seal([goal_event(), item_event(), outcome_event()])
    evs[1]["seq"] = 5
    rep = C.evaluate(evs)
    assert rep["verdict"] == "FAIL"
    assert "ENVELOPE" in codes(rep)


def test_goal_must_be_created_by_user():
    rep = C.evaluate(seal([goal_event("agent"), item_event(), outcome_event()]))
    assert rep["verdict"] == "FAIL"


def test_item_requires_existing_goal():
    evs = seal([goal_event(), item_event(), outcome_event()])
    evs[1]["refs"]["goal_id"] = "GOL-20261024-9999"
    rep = C.evaluate(evs)
    assert rep["verdict"] == "FAIL"


def test_outcome_kind_must_match_item():
    rep = C.evaluate(
        seal([goal_event(), item_event(), outcome_event("content", [CITE])])
    )
    assert rep["verdict"] == "FAIL"


def test_business_plan_requires_citation():
    rep = C.evaluate(
        seal(
            [
                goal_event(),
                item_event("business_plan"),
                outcome_event("business_plan", []),
            ]
        )
    )
    assert rep["verdict"] == "FAIL"


def test_inference_as_fact_rejected():
    bad = {
        "source_surface": "content",
        "source_ref": "C1",
        "source_kind": "inference",
        "claim": "用户会付费",
        "confidence": "fact",
    }
    rep = C.evaluate(
        seal(
            [
                goal_event(),
                item_event("business_plan"),
                outcome_event("business_plan", [bad]),
            ]
        )
    )
    assert rep["verdict"] == "FAIL"


def test_citation_upgrade_rejected():
    cites = [
        {
            "source_surface": "content",
            "source_ref": "C2",
            "source_kind": "conversation",
            "claim": "用户提到成本",
            "confidence": "hypothesis",
        },
        {
            "source_surface": "business_plan",
            "source_ref": "C2",
            "source_kind": "conversation",
            "claim": "用户确认付费",
            "confidence": "fact",
        },
    ]
    rep = C.evaluate(
        seal(
            [
                goal_event(),
                item_event("business_plan"),
                outcome_event("business_plan", cites),
            ]
        )
    )
    assert rep["verdict"] == "FAIL"


def test_goal_update_records_assumption_status():
    upd = {
        "type": "goal.updated",
        "actor": {"kind": "user", "id": "laotian"},
        "refs": {},
        "payload": {
            "goal_id": GOAL_ID,
            "assumption_id": "A1",
            "status": "validated",
            "evidence_refs": ["EV-1"],
            "reason": "研究结论支持",
        },
    }
    rep = C.evaluate(seal([goal_event(), upd, item_event(), outcome_event()]))
    assert rep["verdict"] == "PASS", codes(rep)


def test_duplicate_goal_id_rejected():
    rep = C.evaluate(seal([goal_event(), goal_event()]))
    assert rep["verdict"] == "FAIL"


def test_item_requires_why_now():
    item = item_event()
    item["payload"]["why_now"] = ""
    rep = C.evaluate(seal([goal_event(), item, outcome_event()]))
    assert rep["verdict"] == "FAIL"


def test_selftest_passes():
    assert C.selftest() == 0
