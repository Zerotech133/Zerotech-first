# -*- coding: utf-8 -*-
"""D1 data fallback 测试（WO-AUDIT-FIX-20261031）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "portal"))


def test_merge_record_does_not_keep_none():
    import d1_data
    item = {"code": "600519", "recommendation": "WATCH", "confidence": 0.6,
            "price": 1500, "decision_status": "done",
            "analyses": {"T1": "看多"}, "node_exec": [{"node_key": "t1"}]}
    rec = {"code": "600519", "recommendation": None, "confidence": None,
           "price": None, "decision_status": None, "analyses": {}, "node_exec": []}
    out = d1_data._merge_record(rec, item)
    assert out["recommendation"] == "WATCH"
    assert out["confidence"] == 0.6
    assert out["price"] == 1500
    assert out["analyses"]["T1"] == "看多"
    assert out["node_exec"][0]["node_key"] == "t1"
