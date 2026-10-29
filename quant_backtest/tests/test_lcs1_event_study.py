# -*- coding: utf-8 -*-
"""LCS-1 事件研究聚合单测。"""
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parent
sys.path.insert(0, str(TOOL))
import lcs1_event_study as E  # noqa: E402


def test_agg_handles_empty_and_values():
    out = E._agg([])
    assert out["runup"]["5"]["n"] == 0
    recs = [
        {"primary": "runup", "h": 5, "excess": 0.1},
        {"primary": "runup", "h": 5, "excess": -0.05},
        {"primary": "accumulate", "h": 5, "excess": 0.02},
    ]
    out = E._agg(recs)
    assert out["runup"]["5"]["n"] == 2
    assert out["runup"]["5"]["win_rate"] == 0.5
    assert out["accumulate"]["5"]["n"] == 1


def test_windows_match_freeze_receipt():
    assert E.WINDOWS["in"] == ("20230731", "20250630")
    assert E.WINDOWS["out"] == ("20250701", "20261016")
