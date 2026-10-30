# -*- coding: utf-8 -*-
"""academy_ledger_check 单测（2026-10-30 P99）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import academy_ledger_check as m  # noqa: E402


def _topic(tid="T1", status="hypothesis", decision="", **kw):
    d = {"topic_id": tid, "title": tid, "status": status, "decision": decision,
         "next_action_owner": "DATA1", "next_deadline": "2026-11-03T18:00:00+08:00"}
    d.update(kw)
    return d


def test_decision_stage_without_decision_is_error():
    rep = m.check([_topic(status="decision", decision="")], [], [])
    assert not rep["ok"]
    assert any("decision" in e for e in rep["errors"])


def test_decision_value_and_review_stage_ok():
    rep = m.check([_topic(status="review", decision="No-Go")], [], [])
    assert rep["ok"], rep["errors"]


def test_closed_requires_decision_and_flag():
    rep = m.check([_topic(status="closed", decision="", closed=0)], [], [])
    assert not rep["ok"]
    rep2 = m.check([_topic(status="closed", decision="终止", closed=1)], [], [])
    assert rep2["ok"], rep2["errors"]


def test_queued_backtest_stale_warns():
    now = 1_800_000_000.0
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 9 * 86400))
    rep = m.check([_topic()], [{"topic_id": "T1", "status": "queued", "created": old}], [], now=now)
    assert rep["ok"]  # warning 不算 error
    assert any("queued" in w for w in rep["warnings"])


def test_freeze_request_unregistered_topic_is_error():
    rep = m.check([_topic()], [], [{"topic_id": "MISSING"}])
    assert not rep["ok"]
    assert any("MISSING" in e for e in rep["errors"])


def test_missing_owner_deadline_warns():
    rep = m.check([_topic(next_action_owner="", next_deadline="")], [], [])
    assert rep["ok"]
    assert len(rep["warnings"]) >= 2


import time  # noqa: E402  (放在末尾以便测试可读性，不参与导入期逻辑)
