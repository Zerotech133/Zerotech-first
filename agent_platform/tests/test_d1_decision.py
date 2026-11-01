# -*- coding: utf-8 -*-
"""D1 final_decision 核心测试（WO-D1-DECISION-20261101）。离线、纯文件。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import d1_decision as D  # noqa: E402


def _payload(**kw):
    p = {
        "code": "002445.SZ",
        "name": "test",
        "market": "CN",
        "tier": "BUY",
        "confidence": 0.72,
        "rationale": "3买结构成立，回抽中枢上沿不破，paper-only 观察",
        "evidence": ["bridge:2026-11-01", "preflight:G1-G5 pass"],
        "dissent": ["T2: 量能不足"],
        "source": "d1_research",
        "paper_only": True,
    }
    p.update(kw)
    return p


def test_append_read_roundtrip(tmp_path):
    ledger = tmp_path / "d1.jsonl"
    row = D.append_decision(_payload(tier="STRONG_BUY"), ledger=ledger)
    assert row["tier"] == "STRONG_BUY"
    assert row["tier_label"] == "强烈买入"
    assert row["decided_by"] == "D1"
    assert row["paper_only"] is True
    assert row["decision_status"] == "done"
    assert row["decision_id"].startswith("d1-")
    rows = D.read_decisions(ledger)
    assert len(rows) == 1 and rows[0]["decision_id"] == row["decision_id"]


def test_five_tiers_accepted(tmp_path):
    ledger = tmp_path / "d1.jsonl"
    for i, tier in enumerate(D.TIERS):
        row = D.append_decision(_payload(tier=tier, code="C%d" % i), ledger=ledger)
        assert row["tier"] == tier
    assert D.summary(ledger)["total"] == len(D.TIERS)


def test_bad_tier_rejected(tmp_path):
    try:
        D.append_decision(_payload(tier="WATCH"), ledger=tmp_path / "x.jsonl")
    except D.D1DecisionError as e:
        assert e.code == "bad_tier"
    else:
        raise AssertionError("bad tier must fail")


def test_confidence_and_rationale_validation(tmp_path):
    for bad in (_payload(confidence=1.2), _payload(confidence=None),
                _payload(rationale="太短")):
        try:
            D.append_decision(bad, ledger=tmp_path / "x.jsonl")
        except D.D1DecisionError:
            pass
        else:
            raise AssertionError("invalid payload must fail: %r" % bad)


def test_not_paper_and_execution_fields_rejected(tmp_path):
    for bad in (_payload(paper_only=False), _payload(quantity=100),
                _payload(broker="x")):
        try:
            D.append_decision(bad, ledger=tmp_path / "x.jsonl")
        except D.D1DecisionError as e:
            assert e.code in ("not_paper", "execution_field_forbidden")
        else:
            raise AssertionError("must reject %r" % bad)


def test_duplicate_decision_id_rejected(tmp_path):
    ledger = tmp_path / "d1.jsonl"
    D.append_decision(_payload(decision_id="d1-20261101160000-abcdef"), ledger=ledger)
    try:
        D.append_decision(_payload(decision_id="d1-20261101160000-abcdef"), ledger=ledger)
    except D.D1DecisionError as e:
        assert e.code == "duplicate_decision"
    else:
        raise AssertionError("duplicate must fail")


def test_bad_lines_skipped(tmp_path):
    ledger = tmp_path / "d1.jsonl"
    ledger.write_text('{"bad"\n' + json.dumps(_payload()) + "\n", encoding="utf-8")
    rows = D.read_decisions(ledger)
    assert len(rows) == 1


def test_latest_for_and_summary(tmp_path):
    ledger = tmp_path / "d1.jsonl"
    D.append_decision(_payload(code="A", tier="BUY"), ledger=ledger)
    D.append_decision(_payload(code="B", tier="SELL"), ledger=ledger)
    assert D.latest_for("A", ledger)["tier"] == "BUY"
    assert D.latest_for("C", ledger) is None
    s = D.summary(ledger)
    assert s["by_tier"]["BUY"] == 1 and s["by_tier"]["SELL"] == 1
