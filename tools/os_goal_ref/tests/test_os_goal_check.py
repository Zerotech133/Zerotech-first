# -*- coding: utf-8 -*-
"""v1.1「事业目标—事项—成果」三级关系校验器回归测试（全内存）。"""
from __future__ import annotations

import os
import sys

import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import os_goal_check as GG  # noqa: E402
import os_samples as S  # noqa: E402

GOAL_ID = "GOL-20261024-0001"
DATA_CITE = {"source_surface": "research", "source_ref": "RES-001",
             "source_kind": "data", "claim": "回测窗口内信号胜率 55%",
             "confidence": "fact"}
DOC_CITE = {"source_surface": "content", "source_ref": "CNT-007",
            "source_kind": "document", "claim": "读者对成本结构最关心",
            "confidence": "hypothesis"}


def _goal_event(actor_kind="user"):
    return {"type": "goal.created",
            "actor": {"kind": actor_kind, "id": "laotian", "seat": "laotian"},
            "refs": {},
            "payload": {
                "goal_id": GOAL_ID,
                "statement": "把零界做成持续创业者的个人事业操作系统",
                "target_user": "有经验、连续创业的超级个体",
                "assumptions": [
                    {"id": "A1", "text": "用户愿为长期协作团队付订阅",
                     "status": "hypothesis"},
                    {"id": "A2", "text": "事项协议可跨四个工作面复用",
                     "status": "hypothesis"},
                ],
                "success_signals": ["老田每个工作面各验收一件真实成果"],
            }}


def _stream(kind="software", outcome_kind=None, citations=None,
            goal_ref=True, goal=True, actor_kind="user"):
    events = S.base_events("real")
    created = S.find(events, "item.created")
    if goal_ref:
        created["refs"]["goal_id"] = GOAL_ID
    created["payload"]["outcome_kind"] = kind
    art = S.find(events, "artifact.produced")
    art["payload"]["outcome_kind"] = kind if outcome_kind is None else outcome_kind
    art["payload"]["citations"] = [] if citations is None else citations
    head = [_goal_event(actor_kind)] if goal else []
    return GG.evaluate_stream(S.seal(head + events, schema_version="os-item/1.1"))


def _fail_ids(result):
    return [c["id"] for c in result["checks"] if c["status"] == "FAIL"]


@pytest.mark.parametrize("kind,cites", [
    ("software", []),
    ("business_plan", [DATA_CITE]),
    ("content", [DOC_CITE]),
    ("research", [DATA_CITE]),
])
def test_four_outcome_kinds_pass(kind, cites):
    r = _stream(kind=kind, citations=cites)
    assert r["verdict"] == "PASS", (r["verdict"], _fail_ids(r))
    assert r["exit_code"] == 0
    assert r["goal"]["status"] == "PASS"
    assert r["citations"]["status"] == "PASS"


def test_missing_goal_blocks():
    r = _stream(goal=False)
    assert r["verdict"] == "BLOCKED", _fail_ids(r)
    assert r["exit_code"] == 3
    ids = [c["id"] for c in r["checks"]]
    assert "GOAL_ANCHOR" in ids


def test_item_without_goal_ref_fails():
    r = _stream(goal_ref=False)
    assert r["verdict"] == "FAIL"
    assert "ITEM_ANCHOR" in _fail_ids(r)


def test_illegal_outcome_kind_fails():
    r = _stream(kind="media")
    assert r["verdict"] == "FAIL"
    assert "OUTCOME_KIND" in _fail_ids(r)


def test_outcome_mismatch_fails():
    r = _stream(kind="software", outcome_kind="content", citations=[DOC_CITE])
    assert r["verdict"] == "FAIL"
    assert "OUTCOME_MISMATCH" in _fail_ids(r)


def test_inference_as_fact_rejected():
    cite = {"source_surface": "content", "source_ref": "CNT-009",
            "source_kind": "inference", "claim": "用户会为订阅付费",
            "confidence": "fact"}
    r = _stream(kind="business_plan", citations=[cite])
    assert r["verdict"] == "FAIL"
    assert "INFERENCE_AS_FACT" in _fail_ids(r)


def test_citation_confidence_upgrade_rejected():
    cites = [
        {"source_surface": "content", "source_ref": "CNT-010",
         "source_kind": "conversation", "claim": "用户提到成本",
         "confidence": "hypothesis"},
        {"source_surface": "business_plan", "source_ref": "CNT-010",
         "source_kind": "conversation", "claim": "用户确认愿意付费",
         "confidence": "fact"},
    ]
    r = _stream(kind="business_plan", citations=cites)
    assert r["verdict"] == "FAIL"
    assert "CITATION_UPGRADE" in _fail_ids(r)


def test_business_plan_requires_citation():
    r = _stream(kind="business_plan", citations=[])
    assert r["verdict"] == "FAIL"
    assert "CITATION_REQUIRED" in _fail_ids(r)


def test_goal_must_be_user_created():
    r = _stream(actor_kind="agent")
    assert r["verdict"] == "FAIL"
    assert "GOAL_ACTOR" in _fail_ids(r)


def test_goal_chain_keeps_item_gates():
    r = _stream(kind="software")
    assert r["item_report"]["gates"]["G7"] == "PASS"
    assert r["item_report"]["honesty"]["user_verdict"] is True
