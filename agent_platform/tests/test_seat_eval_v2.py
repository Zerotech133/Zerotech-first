# -*- coding: utf-8 -*-
"""seat_eval schema v2 断言执行器 + 4 席 golden（2026-10-29）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "agent_platform" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import seat_eval  # noqa: E402

GOLDEN = REPO / "agent_platform" / "evals" / "seat_golden.json"
FOUR = ("COMM1", "INTEL1", "AI1", "PROD1")


def test_assertion_engine_pass_fail():
    text = "来源 https://a.example 与 https://b.example 事实 推断 未知"
    ok = seat_eval._assertion_failures(text, [
        {"kind": "contains_all", "values": ["事实", "推断"]},
        {"kind": "not_contains_any", "values": ["保证收益"]},
        {"kind": "regex", "pattern": "事实.*未知"},
        {"kind": "min_urls", "count": 2},
    ])
    assert ok == []
    fails = seat_eval._assertion_failures("无来源，保证收益", [
        {"kind": "contains_all", "values": ["事实"]},
        {"kind": "not_contains_any", "values": ["保证收益"]},
        {"kind": "min_urls", "count": 1},
        {"kind": "unknown_kind"},
    ])
    assert len(fails) == 4


def test_four_seats_golden_all_pass():
    report = seat_eval.run(REPO, GOLDEN)
    by = {s["seat"]: s for s in report["seats"]}
    for seat in FOUR:
        assert seat in by, seat
        assert by[seat]["rate"] == 1.0, (seat, by[seat])
        assert by[seat]["total"] >= 3


def test_v2_schema_marker():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert data.get("schema") == "seat_golden/v2"
    for seat in FOUR:
        cases = data["seats"][seat]["cases"]
        assert len(cases) == 3
        assert any(c.get("assertions") for c in cases)
