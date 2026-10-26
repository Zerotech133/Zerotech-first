# -*- coding: utf-8 -*-
"""派单巡视自动催办单测（2026-10-25）：去重 / 合并 / 历史工单收口。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ticket_loop as tl  # noqa: E402


class _CD:
    ESCALATE_AFTER_MIN = 15

    def __init__(self):
        self.stage = {}
        self.records = {}
        self.waits = {}

    def record_of(self, did):
        return self.records.get(did) or {}

    def check_receipt(self, did):
        if did in self.records:
            return {"ok": True, "state": "inbox"}
        return {"ok": False, "state": "not_found"}

    def waited_min(self, did):
        return self.waits.get(did, 0.0)

    def patrol_stage(self, did):
        return self.stage.get(did, 0)

    def set_patrol_stage(self, did, stage):
        self.stage[did] = int(stage)
        return {"ok": True}


def _setup(monkeypatch):
    cd = _CD()
    sent = []
    monkeypatch.setattr(tl, "comm_dispatch", cd)
    monkeypatch.setattr(tl, "owner_open_id", lambda: "ou_owner")
    monkeypatch.setattr(tl, "_send",
                        lambda oid, card, kind="comm_ticket": (
                            sent.append((oid, card)) or (True, "ok")))
    return cd, sent


def _rec(payload_oid):
    return {"payload": {"open_id": payload_oid},
            "submitted_at": "2026-10-25T16:00:00", "kind": "task_request"}


def test_auto_nudge_merged_single_card_and_dedupe(monkeypatch):
    cd, sent = _setup(monkeypatch)
    monkeypatch.setattr(tl, "owner_open_id", lambda: "ou_owner")
    cd.records["lg-a"] = _rec("ou_owner")   # 发起人 == owner → 只发一张合并卡
    cd.waits["lg-a"] = 6.0
    r1 = tl.auto_nudge("lg-a", 1)
    assert r1.get("merged") is True and len(sent) == 1
    assert "处理进度提醒" in str(sent[0][1])
    r2 = tl.auto_nudge("lg-a", 1)          # 同阶段不重发
    assert r2.get("skipped") is True and len(sent) == 1
    assert cd.patrol_stage("lg-a") == 1


def test_auto_nudge_stage2_after_stage1(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-b"] = _rec("ou_owner")
    cd.waits["lg-b"] = 20.0
    tl.auto_nudge("lg-b", 1)
    tl.auto_nudge("lg-b", 2)
    assert len(sent) == 2 and cd.patrol_stage("lg-b") == 2


def test_auto_nudge_history_only_once(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-old"] = _rec("ou_owner")
    cd.waits["lg-old"] = 2000.0
    r1 = tl.auto_nudge("lg-old", 2)
    assert r1.get("final") is True and len(sent) == 1
    assert "历史工单" in str(sent[0][1])
    r2 = tl.auto_nudge("lg-old", 2)
    assert r2.get("skipped") is True and len(sent) == 1


def test_auto_nudge_different_owner_two_cards(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-c"] = _rec("ou_customer")
    cd.waits["lg-c"] = 3.0
    tl.auto_nudge("lg-c", 1)
    assert len(sent) == 2
    assert {s[0] for s in sent} == {"ou_owner", "ou_customer"}


def test_auto_nudge_failed_send_keeps_stage_for_retry(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-fail"] = _rec("ou_owner")
    cd.waits["lg-fail"] = 8.0
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_ticket":
                        (False, "hub unavailable"))
    r = tl.auto_nudge("lg-fail", 1)
    assert r["ok"] is False and cd.patrol_stage("lg-fail") == 0
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_ticket":
                        (sent.append((oid, card)) or (True, "accepted")))
    r = tl.auto_nudge("lg-fail", 1)
    assert r["ok"] is True and len(sent) == 1
    assert cd.patrol_stage("lg-fail") == 1


def test_auto_nudge_history_failed_send_not_final(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-old-fail"] = _rec("ou_owner")
    cd.waits["lg-old-fail"] = 1500.0
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_patrol":
                        (False, "denied"))
    r = tl.auto_nudge("lg-old-fail", 2)
    assert r["ok"] is False and cd.patrol_stage("lg-old-fail") == 0
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_patrol":
                        (sent.append((oid, card)) or (True, "accepted")))
    r = tl.auto_nudge("lg-old-fail", 2)
    assert r["ok"] is True and r["final"] is True
    assert cd.patrol_stage("lg-old-fail") == 9 and len(sent) == 1


def test_auto_nudge_partial_send_requires_review_no_duplicate(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-part"] = _rec("ou_customer")
    cd.waits["lg-part"] = 20.0
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_ticket":
                        (sent.append((oid, card)) or (oid == "ou_owner", "accepted" if oid == "ou_owner" else "denied")))
    r = tl.auto_nudge("lg-part", 2)
    assert r["ok"] is False and r["error"] == "partial_delivery_needs_review"
    assert cd.patrol_stage("lg-part") == 2
    assert tl.auto_nudge("lg-part", 2)["skipped"] is True and len(sent) == 2
    assert "处理人已收到" not in str(sent[-1][1])


def test_nudge_requester_card_does_not_claim_owner_notified(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.nudge = lambda did, by, note: {"ok": True, "n": 1, "open_id": "ou_customer",
                                          "waited_min": 3.0, "state": "inbox"}
    monkeypatch.setattr(tl, "_send", lambda oid, card, kind="comm_ticket":
                        (sent.append((oid, card)) or (False, "denied")))
    result = tl.nudge("lg-f", notify_owner=True, notify_requester=True)
    assert not result["sent"]["owner"]["ok"]
    assert "已收到提醒" not in str(sent[-1][1])


def test_auto_nudge_done_skips(monkeypatch):
    cd, sent = _setup(monkeypatch)
    cd.records["lg-d"] = _rec("ou_owner")
    cd.check_receipt = lambda did: {"ok": True, "state": "processed"}
    r = tl.auto_nudge("lg-d", 1)
    assert r.get("skipped") is True and len(sent) == 0
