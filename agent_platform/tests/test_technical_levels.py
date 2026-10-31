# -*- coding: utf-8 -*-
"""D1 研究化水平测试（WO-D1-PLAN-20261031）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import technical_levels as tl  # noqa: E402


def _bars():
    # 60 根：缓慢上行，近 20 日支撑 10.0，压力 12.0
    out = []
    for i in range(60):
        c = 10.0 + i * 0.03
        out.append(["2026-09-%02d" % (i % 28 + 1), c - 0.1, c, c + 0.15, c - 0.2, 1000])
    out[-1] = ["2026-10-30", 11.7, 11.8, 12.0, 11.6, 1200]
    return out


def test_derive_watch_levels():
    bars = _bars()
    x = tl.derive_from_bars(bars, price=11.8, can_buy=False)
    assert x["source"] == "technical_atr_support_resistance"
    assert x["watch_trigger"]["pullback"] < 11.8 < x["watch_trigger"]["breakout"]
    assert x["stop_loss"] < 11.8 < x["target1"] < x["target2"]
    assert x["target1"] > x["target2"] * 0  # sanity


def test_derive_buy_zone():
    x = tl.derive_from_bars(_bars(), price=11.8, can_buy=True)
    assert x["buy_zone"][0] < x["buy_zone"][1] <= 11.8
    assert x["stop_loss"] < x["buy_zone"][0]
    assert x["target1"] > 11.8


def test_derive_empty():
    assert tl.derive_from_bars([], price=10) == {}
    assert tl.derive_from_bars([["d", 1, 1, 1, 1, 1]], price=1) == {}
