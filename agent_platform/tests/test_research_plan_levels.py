# -*- coding: utf-8 -*-
"""research_plan levels override 测试（WO-D1-PLAN-20261031）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import research_plan as rp  # noqa: E402


def test_levels_override_watch():
    item = {"code": "600519", "name": "x"}
    rec = {"recommendation": "WATCH", "confidence": 0.5, "price": 100,
           "decision_status": "done"}
    levels = {"watch_trigger": {"pullback": 98.0, "breakout": 102.0},
              "stop_loss": 95.0, "target1": 110.0, "target2": 115.0,
              "source": "technical_atr_support_resistance", "atr": 1.2}
    out = rp.build_item(item, rec, levels=levels)
    assert out["watch_trigger"]["pullback"] == 98.0
    assert out["stop_loss"] == 95.0
    assert out["target1"] == 110.0
    assert out["level_source"] == "technical_atr_support_resistance"
    assert out["buy_zone"] is None


def test_levels_buy_zone_only_when_can_buy():
    rec = {"recommendation": "BUY", "confidence": 0.7, "price": 100,
           "decision_status": "done"}
    out = rp.build_item({"code": "600519"}, rec,
                        levels={"buy_zone": [98.0, 100.0], "stop_loss": 95.0,
                                "target1": 110.0, "target2": 115.0,
                                "source": "technical_atr_support_resistance"})
    assert out["can_buy"] is True
    assert out["buy_zone"] == [98.0, 100.0]
    assert out["stop_loss"] == 95.0
