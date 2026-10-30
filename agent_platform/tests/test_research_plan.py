# -*- coding: utf-8 -*-
"""D1 投研工作台数据合同测试（WO-D1-RESEARCH）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import research_plan as rp  # noqa: E402


def test_watch_item_hands_to_watch():
    item = {"code": "001216", "name": "华瓷", "industry": "家居用品", "tier": "T2"}
    rec = {"recommendation": "WATCH", "confidence": 0.5, "price": 18.45,
           "decision_status": "done",
           "analyses": {"T1": "多头论据", "T2": "空头论据", "N1": "情绪", "F1": "基本面"}}
    plan = {"action": "watch", "price": 18.45,
            "watch_trigger": {"pullback": 17.9, "breakout": 18.82},
            "stop_loss": 16.97}
    x = rp.build_item(item, rec, plan, {"summary": {"rationale": "多空未收敛"}})
    assert x["can_buy"] is False
    assert x["action"] == "watch_trigger"
    assert x["handoff"]["to"].startswith("WA1")
    assert x["bull"] == "多头论据" and x["bear"] == "空头论据"
    assert x["stop_loss"] == 16.97 and x["target1"] > x["price"]
    assert x["strategy_owner"] == "ACAD1" and x["research_owner"] == "D1"


def test_buy_item_ready_and_summary():
    item = {"code": "600519", "name": "茅台"}
    rec = {"recommendation": "BUY", "confidence": 0.8, "price": 1500,
           "decision_status": "done"}
    x = rp.build_item(item, rec, {"action": "buy", "buy_zone": [1470, 1515],
                                  "stop_loss": 1380, "target1": 1620, "target2": 1725})
    assert x["can_buy"] is True and x["action"] == "buy_ready"
    assert x["handoff"]["to"] == "TR1"
    assert x["risk_reward"] and x["risk_reward"] > 0
    s = rp.summarize([x, {"action": "watch_trigger", "can_buy": False,
                          "decision_status": "done", "handoff": {"status": "armed"}}])
    assert s["total"] == 2 and s["can_buy"] == 1 and s["armed_watch"] == 1
