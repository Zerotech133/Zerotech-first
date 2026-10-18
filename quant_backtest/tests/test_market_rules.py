# -*- coding: utf-8 -*-
"""MarketRuleProvider 判定回归：停牌证据必须优先于 listed=True（fail-closed）。"""
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_rules import MarketRuleProvider


DT = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
BAR = None


def _decide(status_value, side="BUY", price=10.0):
    provider = MarketRuleProvider(
        status_provider=lambda *args: status_value,
        limit_provider=lambda *args: "TRADING",
        market="CN",
    )
    return provider.decide("000001.SZ", DT, BAR, side, price)


def test_halted_evidence_blocks_even_when_listed():
    for key in ("halted", "suspended", "blocked"):
        decision = _decide({"listed": True, key: True})
        assert decision.allowed is False
        assert decision.reason == "HALTED"


def test_listed_true_alone_still_allowed():
    decision = _decide({"listed": True})
    assert decision.allowed is True
    assert decision.reason == "ALLOWED"


def test_listing_semantics_do_not_regress():
    assert _decide({"delisted": True}).reason == "DELISTED"
    assert _decide({"listed": False}).reason == "UNLISTED"
    assert _decide({}).reason == "STATUS_UNKNOWN"
