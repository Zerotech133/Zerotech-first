# -*- coding: utf-8 -*-
"""鲁肃部门扩编与盯盘/交易分组断言（WO-INV-STAFF-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ORG = REPO / "agent_platform" / "registry" / "org.json"
TT = REPO / "agent_platform" / "registry" / "team_trading.json"
WM = REPO / "agent_platform" / "registry" / "watch_methods.json"
MEET = REPO / "agent_platform" / "registry" / "d1_meeting.json"


def _j(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_department_staffing():
    org = _j(ORG)
    invest = next(d for d in org["departments"] if d["dept"] == "invest")
    for aid in ("T1", "T2", "N1", "F1", "D1", "ST1", "WA1", "QS1", "TR1",
                "WA2", "WA3", "TR2", "TR3"):
        assert aid in invest["agents"], aid
    prof = {p["agent_id"]: p for p in invest["agent_profiles"]}
    assert prof["WA2"]["reports_to"] == "WA1"
    assert prof["WA3"]["reports_to"] == "WA1"
    assert prof["TR2"]["reports_to"] == "TR1"
    assert prof["TR3"]["reports_to"] == "TR1"
    assert prof["D1"].get("meeting_chair") is True


def test_meeting_and_watch_methods():
    meet = _j(MEET)
    assert meet["chair"] == "D1"
    assert set(meet["voters"]) == {"T1", "T2", "N1", "F1"}
    assert meet["quorum"] == 5
    wm = _j(WM)
    seats = {m["seat"] for m in wm["methods"]}
    assert {"WA1", "WA2", "WA3"}.issubset(seats)
    assert len(wm["methods"]) >= 3  # 多种盯法，不是单一


def test_trading_group_team():
    team = _j(TT)
    ids = {a["agent_id"] for a in team["agents"]}
    assert ids == {"TR1", "TR2", "TR3"}
    assert team["agents"][0]["reports_to"] == "D1"
    assert team["agents"][1]["reports_to"] == "TR1"
