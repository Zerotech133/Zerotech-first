# -*- coding: utf-8 -*-
"""D1 交棒回执测试（WO-D1-HANDOFF-20261101）。离线文件。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import d1_handoff as H  # noqa: E402


def _p(**kw):
    p = {
        "code": "002445.SZ",
        "name": "test",
        "target": "TR1",
        "action": "trade",
        "rationale": "3买回抽不破中枢，交模拟交易所观察",
        "buy_zone": [2.98, 3.05],
        "stop_loss": 2.67,
        "trigger": "回踩 3.00 附近",
        "evidence": ["bridge"],
        "decision_id": "d1-20261101230000-abcdef",
        "paper_only": True,
    }
    p.update(kw)
    return p


def test_append_read(tmp_path):
    ledger = tmp_path / "h.jsonl"
    row = H.append_handoff(_p(), ledger=ledger)
    assert row["target"] == "TR1" and row["action"] == "trade"
    assert row["status"] == "pending_ack" and row["paper_only"] is True
    rows = H.read_handoffs(ledger)
    assert len(rows) == 1 and rows[0]["receipt_id"].startswith("d1h-")


def test_target_action_validation(tmp_path):
    ledger = tmp_path / "h.jsonl"
    for bad in (_p(target="TR1", action="watch"), _p(target="WA1", action="trade"),
                _p(target="X", action="trade"), _p(action="buy")):
        try:
            H.append_handoff(bad, ledger=ledger)
        except H.D1HandoffError as e:
            assert e.code in ("bad_target_action", "bad_target", "bad_action")
        else:
            raise AssertionError("must reject %r" % bad)


def test_paper_and_execution_fields(tmp_path):
    for bad in (_p(paper_only=False), _p(quantity=100), _p(order={"x": 1})):
        try:
            H.append_handoff(bad, ledger=tmp_path / "h.jsonl")
        except H.D1HandoffError:
            pass
        else:
            raise AssertionError("must reject %r" % bad)


def test_buy_zone_validation(tmp_path):
    for bz in ([3.1, 3.0], [1], ["x", 2]):
        try:
            H.append_handoff(_p(buy_zone=bz), ledger=tmp_path / "h.jsonl")
        except H.D1HandoffError as e:
            assert e.code == "bad_buy_zone"
        else:
            raise AssertionError("bad buy_zone must fail")


def test_ack_and_summary(tmp_path):
    ledger = tmp_path / "h.jsonl"
    row = H.append_handoff(_p(), ledger=ledger)
    assert H.summary(ledger)["pending_ack"] == 1
    acked = H.ack_handoff(row["receipt_id"], "TR1", "received", "已收到", ledger=ledger)
    assert acked["status"] == "acked"
    assert acked["ack"]["by"] == "TR1"
    s = H.summary(ledger)
    assert s["acked"] == 1 and s["pending_ack"] == 0
    try:
        H.ack_handoff(row["receipt_id"], "TR1", ledger=ledger)
    except H.D1HandoffError as e:
        assert e.code == "already_acked"
    else:
        raise AssertionError("double ack must fail")


def test_latest_and_bad_lines(tmp_path):
    ledger = tmp_path / "h.jsonl"
    H.append_handoff(_p(code="A"), ledger=ledger)
    H.append_handoff(_p(code="B"), ledger=ledger)
    assert H.latest_for("A", ledger)["code"] == "A"
    assert H.latest_for("C", ledger) is None
    ledger.write_text('{bad\n' + ledger.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(H.read_handoffs(ledger)) == 2


def test_no_trade_target(tmp_path):
    ledger = tmp_path / "h.jsonl"
    row = H.append_handoff(_p(target="TR1", action="no_trade"), ledger=ledger)
    assert row["action"] == "no_trade"
